# -*- coding: utf-8 -*-
"""
JM 漫画爬虫服务（轻量版）。

从旧版搬改：保留搜索 / 详情 / 下载全部核心链路，含客户端复用（keep-alive）、
搜索结果内存缓存、详情缓存加锁与异步落盘、同义词展开接入（_expand_single）。
"""

import concurrent.futures
import copy
import io
import json
import os
import re
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

# 启动性能：jmcomic / requests 均改为「惰性 import」（在各使用点函数内按需加载，
# 启动省 ~370ms）。desktop_app 启动后立即用后台线程预热，用户点搜索/下载时零等待。
import yaml
from PIL import Image

from core.config import BASE_DIR, OPTION_FILE, TEMP_CACHE_DIR
from core.models.database import get_image_quality_params, get_system_config


# ── 下载并发上限 ──────────────────────────────────────────
# 实测 jmcomic 的真实并发开关是 download.threading.image / .photo：
#   decide_image_batch_count → threading.image
#   decide_photo_batch_count → threading.photo
# 且 execute_on_condition 里「batch >= 对象数」时走 multi_thread_launcher，
# 即**每个对象一个线程、完全不设上限**。所以 image/photo 偏大会嵌套爆炸
# （16 章 × 30 图 = 480 线程同时 HTTP + Pillow decode），表现为下载卡死、
# 进度不动、速度掉 0。
# 另注：max_workers 是幽灵键（threading 为宽松 AdvancedDict，未知键静默忽略），
# batch_count 是旧版别名（会被改写成 image），两者都不是有效开关。
DOWNLOAD_IMAGE_CONCURRENCY = 16  # 单章内图片并发（基准实测 16×4 为最优组合）
DOWNLOAD_PHOTO_CONCURRENCY = 4   # 同时下载的章节数（16×4=64 峰值，PC 端安全）
DOWNLOAD_HTTP_TIMEOUT = 30       # 单请求超时秒（jmcomic 默认无超时，会被死连接挂死）


def _clamp_concurrency(value, ceiling: int) -> int:
    """把并发配置夹到安全区间 [1, ceiling]；缺失/非法值回落到 ceiling。

    这里刻意「封顶」而不是「保底」：并发一旦超过 ceiling，jmcomic 会在
    batch >= 对象数时退化成每对象一线程，嵌套后线程数失控反而更慢。
    """
    try:
        v = int(value)
    except (TypeError, ValueError):
        return ceiling
    if v < 1:
        return ceiling
    return min(v, ceiling)


# ── 下载日志（JSONL，一行一本，供后续分析下载速度惯性） ──────
# 字段刻意精简：jm_id / 时间 / 状态 / 耗时秒 / 章节数 / 总页数 /
# 实际下载图数 / 大小MB / 平均MB每秒 / 每秒MB采样数组。
DOWNLOAD_LOG_PATH = os.path.join(BASE_DIR, "logs", "download_log.jsonl")
_dl_log_lock = threading.Lock()


class DownloadCancelled(Exception):
    """下载被用户取消：在 jmcomic 回调中抛出以中断同步下载循环。"""
    pass


class _DownloadSession:
    """一次下载的记录器：每秒速度采样 + 结束落 JSONL 日志。

    download_comic 只管「下载」，本类管「记录」——采样线程纯内存读回调的
    字节计数（零 IO），无论 completed / cancelled / failed，finally 里
    finish() 都落一条日志。
    """

    def __init__(self, album_id: int, cb: "_JmProgressCallback"):
        self.album_id = album_id
        self.cb = cb
        self.status = "failed"
        self.samples: List[float] = []
        self._t0 = time.time()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        # 每秒采一次「该秒下载的 MB」（即瞬时 MB/s）
        last = 0
        while not self._stop.wait(1.0):
            cur = self.cb._bytes
            self.samples.append(round(max(0.0, cur - last) / 1048576, 3))
            last = cur

    def finish(self):
        """结束采样并落日志（线程安全、失败静默，绝不影响下载本体）。"""
        self._stop.set()
        try:
            self._thread.join(timeout=1.5)
        except Exception:
            pass
        self._write_log()

    def _write_log(self):
        try:
            cb = self.cb
            dur = max(0.1, time.time() - self._t0)
            rec = {
                "jm_id": self.album_id,
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "status": self.status,
                "duration_s": round(time.time() - self._t0, 1),
                "chapters": cb._total,
                "pages": cb._pages_total,
                "imgs": cb._images,
                "size_mb": round((cb._bytes + cb._skip_bytes) / 1048576, 2),
                "avg_mb_s": round(cb._bytes / dur / 1048576, 3),
                "samples_mb": self.samples,
            }
            os.makedirs(os.path.dirname(DOWNLOAD_LOG_PATH), exist_ok=True)
            with _dl_log_lock:
                with open(DOWNLOAD_LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
        except Exception as e:
            print(f"写下载日志失败: {e}")



class _JmProgressCallback:
    """下载进度收集器（由 _build_hooked_downloader 生成的子类转发钩子调用）。

    进度模型：全局「已完成图数 / 全本总页数」单一计数（锁保护）——
    imgs_done 只增不减 → pct 天然单调，章节并发下也无共享状态竞争。
    """

    def __init__(self, progress_callback, cancel_check=None):
        self._pc = progress_callback
        self._cancel_check = cancel_check
        self._lock = threading.Lock()
        self._total = 0         # 总章数
        self._done = 0          # 已完成章数
        self._pages_total = 0   # 已开始章节的总页数（before_photo 逐章累加）
        self._done_pages = 0    # 已完成章节的总页数（after_photo 累加，用于批进度）
        self._imgs_done = 0     # 全局已完成图数（skip 的图也算，见 before_image）
        self._bytes = 0         # 新下载字节（after_image 累加）
        self._skip_bytes = 0    # 已存在被跳过文件的大小（计入日志 size_mb）
        self._images = 0        # 真实下载图数

    def _maybe_cancel(self):
        if self._cancel_check and self._cancel_check():
            raise DownloadCancelled()

    def before_photo(self, photo):
        """逐章累计全本总页数（len(photo) = 该章图片数）。"""
        try:
            n = len(photo)
        except Exception:
            n = 0
        with self._lock:
            self._pages_total += n
        self._emit()

    def _emit(self):
        """推送进度快照。pct 映射 20~80%，**批间连续、全程单调**：

        frac = (已完成章数 + 当前进行中章的页数完成比) / 总章数

        推导（关键不变量）：章完成瞬间 done+1 且该章移出「进行中」批，
        (done + 1) / total ≡ (done + 该章页/该章页) / total —— 恒等连续。
        由此**无需全本总页数**（禁漫不提供）也能做到：
        - 章数做分母（全程恒定）→ 换批（2 章一批）不再回跳；
        - 章内页数比做分子微调 → 进度条在章内持续走动。
        """
        if not self._pc:
            return
        with self._lock:
            total, done = self._total, self._done
            pages, imgs = self._pages_total, self._imgs_done
            done_pages = self._done_pages
            new_bytes, skip_bytes = self._bytes, self._skip_bytes
            images = self._images
        if total > 0:
            batch_pages = pages - done_pages          # 进行中章的页数
            batch_imgs = max(0, imgs - done_pages)    # 进行中章已完成图数
            if batch_pages > 0:
                frac = min(1.0, (done + batch_imgs / batch_pages) / total)
            else:
                frac = min(1.0, done / total)
        elif pages > 0:                               # 兜底：总章数未知
            frac = min(1.0, imgs / pages)
        else:
            frac = 0.0
        pct = 20 + int(frac * 60)  # 映射到 20~80%
        if total:
            msg = f"下载 {done}/{total} 章 · 已下 {imgs}/{pages} 页"
        else:
            msg = f"已下 {imgs} 页" if imgs else "下载中"
        extra = {"done": done, "total": total,
                 "bytes": new_bytes, "images": images}
        try:
            self._pc(pct, "downloading", msg, extra)
        except Exception:
            pass

    def before_album(self, album):
        # len(album) = episode_list 长度（纯本地，零网络）；jmcomic 的
        # JmAlbumDetail 没有 photo_count 属性，page_count 对多章本恒为 0。
        try:
            total = len(album)
        except Exception:
            total = 0
        with self._lock:
            self._total = total
        self._emit()

    def before_image(self, image, img_save_path):
        """jmcomic 在「skip 判断之前」调用本钩子——两件事都在这里做：

        1. 取消检查点：重下同一本时，TempCache 里上次残留的已下完文件会被
           大批 skip，skip 路径不会触发 after_image → 没有这里的检查点，
           这一大段完全无响应（取消点不了一、进度也不走）。
        2. skip 图也算完成（_imgs_done +1），进度条照常走动。
        """
        self._maybe_cancel()
        with self._lock:
            self._imgs_done += 1
        try:
            sz = os.path.getsize(img_save_path)
        except OSError:
            sz = 0
        with self._lock:
            self._skip_bytes += sz
        self._emit()

    def after_image(self, image, img_save_path):
        """逐图落盘后累加真实下载字节（速度的数据源），并立即推进度。"""
        self._maybe_cancel()
        try:
            sz = os.path.getsize(img_save_path)
        except OSError:
            sz = 0
        with self._lock:
            self._images += 1
            self._bytes += sz
        self._emit()

    def after_photo(self, photo):
        self._maybe_cancel()
        try:
            n = len(photo)
        except Exception:
            n = 0
        with self._lock:
            self._done += 1
            self._done_pages += n
        self._emit()

    def after_album(self, album):
        try:
            self._pc(85, "processing", "章节下载完成，正在整理…")
        except Exception:
            pass


def _build_hooked_downloader(cb: "_JmProgressCallback"):
    """生成把 jmcomic 下载钩子转发给进度收集器的 JmDownloader 子类。

    硬约束：download_album 的 `callback` 参数只是**返回值回调**（下载结束后
    才被调用一次），不会注册进下载器钩子链——进度监听必须子类化
    JmDownloader 覆写钩子并经 `downloader=` 传入，且 super() 要保留
    （jmcomic 自身的 feature/统计机制挂在那里）。
    """
    from jmcomic.jm_downloader import JmDownloader

    class _HookedDownloader(JmDownloader):
        def before_album(self, album):
            cb.before_album(album)
            super().before_album(album)

        def before_photo(self, photo):
            cb.before_photo(photo)
            super().before_photo(photo)

        def before_image(self, image, img_save_path):
            cb.before_image(image, img_save_path)
            super().before_image(image, img_save_path)

        def after_image(self, image, img_save_path):
            cb.after_image(image, img_save_path)
            super().after_image(image, img_save_path)

        def after_photo(self, photo):
            cb.after_photo(photo)
            super().after_photo(photo)

        def after_album(self, album):
            cb.after_album(album)
            super().after_album(album)

    return _HookedDownloader


def _detect_system_proxy() -> Optional[str]:
    """读取 Windows 系统代理（IE 设置）。返回 http://host:port 或 None。"""
    if sys.platform != "win32":
        return None
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        enable, _t = winreg.QueryValueEx(key, "ProxyEnable")
        if not enable:
            return None
        server, _t = winreg.QueryValueEx(key, "ProxyServer")
        winreg.CloseKey(key)
        if not server:
            return None
        if "=" in server or ";" in server:
            # 形如 "http=127.0.0.1:7890;https=127.0.0.1:7890;ftp=..."
            chosen = ""
            for part in server.split(";"):
                if part.startswith(("https=", "http=")):
                    chosen = part.split("=", 1)[1]
                    break
            server = chosen
        if not server:
            return None
        if not server.startswith(("http://", "https://")):
            server = "http://" + server
        return server
    except Exception:
        return None


class JMCrawler:
    """JM 漫画爬虫服务。"""

    def __init__(self):
        self.base_dir = BASE_DIR
        self.temp_cache = TEMP_CACHE_DIR
        self.option_file = OPTION_FILE

        os.makedirs(self.temp_cache, exist_ok=True)
        self._ensure_option_file()

        import jmcomic
        self.jm_option = jmcomic.JmOption.from_file(self.option_file)
        self.cover_cache_file = os.path.join(self.temp_cache, "cover_cache.json")
        self.cover_cache = self._load_cover_cache()
        self.detail_cache_file = os.path.join(self.temp_cache, "detail_cache.json")
        self.detail_cache = self._load_detail_cache()
        # 详情缓存异步落盘用的锁 / 状态标记，避免同步写大盘 JSON 卡住响应
        self._detail_save_lock = threading.Lock()
        self._detail_save_in_progress = False
        # 内存详情缓存的读写锁，避免并发请求下字典迭代/重建竞争
        self._detail_cache_lock = threading.Lock()
        # 客户端复用：保留 HTTP keep-alive 连接池，重复搜索/翻页显著变快
        self._client = None
        self._client_sig = None
        self._client_lock = threading.Lock()
        self._cover_ok_prefix = None   # 上次成功的封面域前缀（域记忆，加速批量封面）
        # 搜索结果内存缓存：相同查询 / 翻回页即时返回
        self._search_cache = {}
        self._search_cache_lock = threading.Lock()
        self._search_cache_ttl = 120  # 秒

    # ─── jmcomic 选项与客户端 ───────────────────────────────

    def _build_default_option_content(self) -> Dict:
        return {
            "client": {
                "retry_times": 5,
                "domain": [],
                # 关键性能修复：curl_cffi 默认（非 session）= 每张图重新 TLS 握手，
                # session 模式复用连接池，基准实测 +54% 速度（0.50→0.77MB/s）
                "postman": {"type": "curl_cffi_session"},
                "headers": {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Accept": (
                        "text/html,application/xhtml+xml,application/xml;q=0.9,"
                        "image/webp,*/*;q=0.8"
                    ),
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Connection": "keep-alive",
                },
            },
            "download": {
                "cache": True,
                "image": {"decode": True, "suffix": ".jpg"},
                # 真实并发开关（image=单章内图片并发，photo=同时下载章节数）
                "threading": {
                    "image": DOWNLOAD_IMAGE_CONCURRENCY,
                    "photo": DOWNLOAD_PHOTO_CONCURRENCY,
                },
            },
            "dir_rule": {
                "rule": "{Aid}/{Pid}",
                "base_dir": os.path.join(self.base_dir, "TempCache", "downloads"),
            },
        }

    def _merge_option_content(self, current: Dict, defaults: Dict) -> Dict:
        merged = dict(current)
        for key, default_value in defaults.items():
            current_value = merged.get(key)
            if key not in merged:
                merged[key] = default_value
            elif isinstance(current_value, dict) and isinstance(default_value, dict):
                merged[key] = self._merge_option_content(current_value, default_value)
        return merged

    def _write_option_file(self, option_content: Dict):
        os.makedirs(os.path.dirname(self.option_file), exist_ok=True)
        with open(self.option_file, "w", encoding="utf-8") as f:
            yaml.safe_dump(
                option_content,
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )

    def _ensure_option_file(self):
        """确保 jm_option.yml 存在且不覆盖用户设置。"""
        default_content = self._build_default_option_content()

        if not os.path.exists(self.option_file):
            self._write_option_file(default_content)
            return

        try:
            with open(self.option_file, "r", encoding="utf-8") as f:
                current_content = yaml.safe_load(f) or {}
            if not isinstance(current_content, dict):
                raise ValueError("jm_option.yml must contain a YAML mapping")
        except Exception as exc:
            backup_file = f"{self.option_file}.bak"
            try:
                shutil.copy2(self.option_file, backup_file)
                print(f"jm_option.yml 无法解析，已备份到 {backup_file}")
            except Exception as backup_error:
                print(f"备份 jm_option.yml 失败: {backup_error}")
            print(f"重建默认 jm_option.yml: {exc}")
            self._write_option_file(default_content)
            return

        merged_content = self._merge_option_content(current_content, default_content)

        # 强制修正：下载目录必须跟随当前 BASE_DIR（避免移动目录后失效）
        merged_content.setdefault("dir_rule", {})
        merged_content["dir_rule"]["base_dir"] = os.path.join(
            self.base_dir, "TempCache", "downloads"
        )

        # 并发治理：清掉无效键，并把真实开关统一为实测最优默认（16×4）
        # 注意：并发无用户配置入口，老 yml 里的 8/2 全是历史默认值——必须
        # 主动提升（而非只封顶），否则新默认白定；封顶防手改超限线程爆炸。
        threading_cfg = merged_content.setdefault("download", {}).setdefault("threading", {})
        # batch_count 是旧版别名（jmcomic 会改写成 image，留着会覆盖我们的设置）
        # max_workers 从未被 jmcomic 读取，纯误导，一并清掉
        threading_cfg.pop("batch_count", None)
        threading_cfg.pop("max_workers", None)
        threading_cfg["image"] = _clamp_concurrency(DOWNLOAD_IMAGE_CONCURRENCY, 24)
        threading_cfg["photo"] = _clamp_concurrency(DOWNLOAD_PHOTO_CONCURRENCY, 8)

        # 连接复用：老配置缺 postman.type 时 jmcomic 默认非 session（每图重握手，
        # 基准实测慢 54%）——强制写为 curl_cffi_session（幂等修正）
        postman_cfg = merged_content.setdefault("client", {}).setdefault("postman", {})
        if postman_cfg.get("type") != "curl_cffi_session":
            postman_cfg["type"] = "curl_cffi_session"

        if merged_content != current_content:
            self._write_option_file(merged_content)

    def _build_option(self):
        import jmcomic
        option = jmcomic.create_option_by_file(self.option_file)
        self._apply_proxy(option)
        return option

    def _apply_proxy(self, option) -> None:
        """接管代理与超时（必须在 jmcomic 注入默认值之后运行）。

        硬约束：jmcomic 在 option 无 proxies 时会注入 DEFAULT_PROXIES
        =127.0.0.1:5788，所有请求打向本机死端口。因此这里**必须显式接管**：
        代理开 → 用户代理；代理关 → 跟随系统代理（探测不到则显式直连）。
        timeout 无条件注入（postman 会把 meta_data 合并进每个请求 kwargs）。
        """
        try:
            meta = option.client["postman"]["meta_data"]
            meta["timeout"] = DOWNLOAD_HTTP_TIMEOUT
            enabled = get_system_config("proxy_enabled")
            url = (get_system_config("proxy_url") or "").strip()
            if enabled not in (None, "0", "false", "False", "") and url:
                if "://" not in url:
                    url = "http://" + url
                meta["proxies"] = {"http": url, "https": url}
            else:
                sys_proxy = _detect_system_proxy()
                if sys_proxy:
                    meta["proxies"] = {"http": sys_proxy, "https": sys_proxy}
                    print(f"[jmcomic] 下载跟随系统代理: {sys_proxy}")
                else:
                    # 显式直连：不能用 proxies=None（会被 postman.merge_kwargs
                    # 跳过、漏回 jmcomic 默认代理），必须用「值为 None 的键」
                    meta["proxies"] = {"http": None, "https": None}
        except Exception as e:
            print(f"应用代理设置失败: {e}")

    def _build_client(self):
        return self._build_option().build_jm_client()

    def _client_signature(self):
        """客户端复用签名：option 文件 mtime + 代理配置。任一变化即重建。"""
        try:
            mtime = os.path.getmtime(self.option_file) if os.path.exists(self.option_file) else 0
        except Exception:
            mtime = 0
        try:
            proxy_enabled = get_system_config("proxy_enabled")
            proxy_url = (get_system_config("proxy_url") or "").strip()
        except Exception:
            proxy_enabled = None
            proxy_url = ""
        return (mtime, proxy_enabled, proxy_url)

    def _get_client(self):
        """复用 jmcomic 客户端以保留 keep-alive 连接；代理 / option 变化时自动重建。"""
        sig = self._client_signature()
        with self._client_lock:
            if self._client is not None and self._client_sig == sig:
                return self._client
            client = self._build_client()
            self._client = client
            self._client_sig = sig
            return client

    # ─── 工具 ──────────────────────────────────────────────

    def _parse_count(self, value) -> int:
        if value is None:
            return 0
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return int(value)
        text = str(value).strip()
        if not text:
            return 0
        text = text.replace(",", "").replace(" ", "").lower()
        match = re.search(r"(\d+(?:\.\d+)?)(亿|万|k|m|b|w)?", text)
        if not match:
            return 0
        number = float(match.group(1))
        unit = match.group(2) or ""
        multiplier = {
            "w": 10_000,
            "k": 1_000,
            "m": 1_000_000,
            "b": 1_000_000_000,
            "万": 10_000,
            "亿": 100_000_000,
        }.get(unit, 1)
        return int(number * multiplier)

    # ─── 本地缓存 ──────────────────────────────────────────

    def _load_cover_cache(self) -> Dict[str, str]:
        try:
            if os.path.exists(self.cover_cache_file):
                with open(self.cover_cache_file, "r", encoding="utf-8") as f:
                    cache = json.load(f)
                print(f"加载封面缓存，数量: {len(cache)}")
                return cache
        except Exception as e:
            print(f"加载封面缓存失败: {e}")
        return {}

    def _save_cover_cache(self):
        try:
            with open(self.cover_cache_file, "w", encoding="utf-8") as f:
                json.dump(self.cover_cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存封面缓存失败: {e}")

    def _load_detail_cache(self) -> Dict[str, Dict]:
        try:
            if os.path.exists(self.detail_cache_file):
                with open(self.detail_cache_file, "r", encoding="utf-8") as f:
                    cache = json.load(f)
                if isinstance(cache, dict):
                    return cache
        except Exception as e:
            print(f"加载详情缓存失败: {e}")
        return {}

    def _save_detail_cache(self):
        try:
            with open(self.detail_cache_file, "w", encoding="utf-8") as f:
                json.dump(self.detail_cache, f, ensure_ascii=False, separators=(",", ":"))
        except Exception as e:
            print(f"保存详情缓存失败: {e}")

    def _save_detail_cache_async(self):
        """后台线程落盘，避免大缓存时阻塞搜索响应。"""
        if self._detail_save_in_progress:
            return
        self._detail_save_in_progress = True

        def _worker():
            try:
                with self._detail_save_lock:
                    self._save_detail_cache()
            finally:
                self._detail_save_in_progress = False

        threading.Thread(target=_worker, daemon=True).start()

    # ─── 详情 / 封面 ───────────────────────────────────────

    def get_comic_info(self, album_id: int) -> Optional[Dict]:
        """获取漫画详细信息（详情模态 / 纯数字 ID 搜索用）。"""
        try:
            client = self._get_client()
            album = client.get_album_detail(album_id)
            if not album:
                return None

            # 章节数：album 迭代零额外请求（详情响应本身包含章节列表）。
            # 注意：jmcomic 的 Album 没有现成「全站页数」字段（page_count 通常为 0，
            # 单章相册才可能有值），逐章统计图片数需要 N 次请求，详情页不做。
            chapter_count = 0
            try:
                chapter_count = len(list(album))
            except Exception:
                pass

            comic_info = {
                "id": album_id,
                "title": getattr(album, "title", "Unknown"),
                "author": getattr(album, "author", "Unknown"),
                "cover": "",
                "tags": list(getattr(album, "tags", []) or []),
                "description": getattr(album, "description", ""),
                "favorites": self._parse_count(getattr(album, "likes", 0)),
                "pages": int(getattr(album, "page_count", 0) or 0),
                "chapters": chapter_count,
                "scramble_id": getattr(album, "scramble_id", ""),
                "works": list(getattr(album, "works", []) or []),
                "actors": list(getattr(album, "actors", []) or []),
                "keywords": list(getattr(album, "keywords", []) or []),
            }

            cover_url = self.get_cover_url(album_id)
            if cover_url:
                comic_info["cover"] = cover_url

            return comic_info

        except Exception as e:
            print(f"获取漫画信息失败 {album_id}: {e}")
            return None

    def get_cover_url(self, album_id: int) -> str:
        """获取封面 URL（本地构造 + 缓存）。"""
        cache_key = str(album_id)
        if cache_key in self.cover_cache:
            return self.cover_cache[cache_key]
        try:
            # 使用 jmcomic 维护的图片 CDN 域名列表生成封面地址，避免失效的固定域名
            from jmcomic.jm_toolkit import JmcomicText
            cover_url = JmcomicText.get_album_cover_url(album_id)
            self.cover_cache[cache_key] = cover_url
            self._save_cover_cache()
            return cover_url
        except Exception as e:
            print(f"获取封面 URL 失败 {album_id}: {e}")
            return ""

    def fetch_cover_bytes(self, jm_id: int, url: str):
        """拉取封面字节（复用带代理/session 的 client；多域名轮换重试）。

        背景：封面 img 标签由 WebView 直连，部分禁漫 CDN 域可能被网络环境
        阻断（用户偶发「封面没返回」）——服务端拉取走配置好的代理/session，
        并轮换 jmcomic 图片域列表，成功率远高于直连。失败返回 None。
        """
        candidates = []
        # 域记忆优先：上次成功的域排最前（搜索页批量封面时后续都走热域）
        if self._cover_ok_prefix:
            candidates.append(f"{self._cover_ok_prefix}/media/albums/{jm_id}.jpg")
        if url:
            candidates.append(url)
        try:
            import jmcomic
            for dom in jmcomic.JmModuleConfig.DOMAIN_IMAGE_LIST:
                u = f"https://{dom}/media/albums/{jm_id}.jpg"
                if u not in candidates:
                    candidates.append(u)
        except Exception:
            pass
        try:
            client = self._get_client()
        except Exception as e:
            print(f"封面拉取无可用 client: {e}")
            return None
        for u in candidates:
            try:
                # 显式短超时：封面上限 6 个候选域，逐个 30s 会拖到 3 分钟
                resp = client.postman.get(u, timeout=6)
                if getattr(resp, "status_code", 0) == 200 and getattr(resp, "content", None):
                    self._cover_ok_prefix = u.rsplit("/media/", 1)[0]
                    return resp.content
            except Exception:
                continue
        return None

    def _extract_search_result(self, album) -> Optional[Dict]:
        comic_info = {}

        if isinstance(album, tuple) and len(album) >= 2:
            album_id = album[0]
            info_dict = album[1]
            # jmcomic search_tag 返回的是 (album_id, title) 二元组
            if isinstance(info_dict, str):
                comic_info = {
                    "id": int(album_id) if album_id and str(album_id).isdigit() else 0,
                    "title": info_dict or "未知标题",
                    "author": "",
                    "cover": "",
                    "favorites": 0,
                    "tags": [],
                    "description": "",
                }
            elif isinstance(info_dict, dict):
                favorites_raw = 0
                for key in ("favorites", "likes", "like", "favourites", "favorite", "fav"):
                    if key in info_dict and info_dict[key] is not None:
                        favorites_raw = info_dict[key]
                        break
                comic_info = {
                    "id": int(album_id) if album_id and str(album_id).isdigit() else 0,
                    "title": info_dict.get("name", "未知标题"),
                    "author": info_dict.get("author", "未知作者"),
                    "cover": "",
                    "favorites": self._parse_count(favorites_raw),
                    "tags": list(info_dict.get("tags", []) or []),
                    "description": (info_dict.get("description") or "")[:100],
                }
            else:
                return None
        elif hasattr(album, "__dict__"):
            favorites_raw = (
                getattr(album, "favorites", None)
                if getattr(album, "favorites", None) is not None
                else getattr(album, "likes", 0)
            )
            comic_info = {
                "id": getattr(album, "id", 0),
                "title": getattr(album, "title", None) or getattr(album, "name", "未知标题"),
                "author": getattr(album, "author", "未知作者"),
                "cover": "",
                "favorites": self._parse_count(favorites_raw),
                "tags": list(getattr(album, "tags", []) or []),
                "description": (getattr(album, "description", None) or "")[:100],
            }
        elif isinstance(album, dict):
            favorites_raw = 0
            for key in ("favorites", "likes", "like", "favourites", "favorite", "fav"):
                if key in album and album[key] is not None:
                    favorites_raw = album[key]
                    break
            comic_info = {
                "id": album.get("id", 0),
                "title": album.get("name", album.get("title", "未知标题")),
                "author": album.get("author", "未知作者"),
                "cover": "",
                "favorites": self._parse_count(favorites_raw),
                "tags": list(album.get("tags", []) or []),
                "description": (album.get("description") or "")[:100],
            }

        if not comic_info or not comic_info.get("id"):
            return None

        comic_info["needs_cover"] = True
        comic_info["needs_detail"] = True
        return comic_info

    def _fetch_search_detail(self, client, album_id: int) -> Optional[Dict]:
        try:
            detail = client.get_album_detail(album_id)
            if not detail:
                return None
            return {
                "id": album_id,
                "title": str(getattr(detail, "title", "") or getattr(detail, "name", "未知标题")),
                "favorites": self._parse_count(getattr(detail, "likes", 0)),
                "author": str(getattr(detail, "author", "未知作者")),
                "tags": list(getattr(detail, "tags", []) or []),
                "description": (getattr(detail, "description", None) or "")[:100],
                "pages": getattr(detail, "page_count", 0),
            }
        except Exception as e:
            print(f"获取搜索详情失败 {album_id}: {e}")
            return None

    def get_search_result_details(self, album_ids: List[int]) -> Dict[str, Dict]:
        cleaned_ids = []
        seen_ids = set()
        for album_id in album_ids:
            try:
                parsed_id = int(album_id)
            except (TypeError, ValueError):
                continue
            if parsed_id <= 0 or parsed_id in seen_ids:
                continue
            seen_ids.add(parsed_id)
            cleaned_ids.append(parsed_id)
        if not cleaned_ids:
            return {}

        details = {}
        # 命中本地详情缓存的先直接取，避免重复网络请求
        missing = []
        for album_id in cleaned_ids:
            with self._detail_cache_lock:
                cached = self.detail_cache.get(str(album_id))
            if cached:
                details[str(album_id)] = cached
            else:
                missing.append(album_id)

        # 一次性批量补全缺失的详情（绝不能放进上面的循环里，否则 O(N^2) 重复请求）
        if missing:
            client = self._get_client()
            max_workers = min(16, len(missing))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(self._fetch_search_detail, client, album_id): album_id
                    for album_id in missing
                }
                # 整体超时保护：JM 抽风/限流时已完成的先采用，未完成的本次放弃
                done, not_done = concurrent.futures.wait(futures, timeout=25)
                for future in done:
                    album_id = futures[future]
                    try:
                        detail = future.result()
                    except Exception as e:
                        print(f"处理搜索详情失败 {album_id}: {e}")
                        continue
                    if detail:
                        details[str(album_id)] = detail
                        with self._detail_cache_lock:
                            self.detail_cache[str(album_id)] = detail
                if not_done:
                    print(f"搜索详情有 {len(not_done)} 个在超时内未完成，本次跳过（下次再补）")

            # 控制缓存体积（保留最近 3000 条）
            with self._detail_cache_lock:
                if len(self.detail_cache) > 3500:
                    self.detail_cache = dict(list(self.detail_cache.items())[-3000:])
            self._save_detail_cache_async()

        return details

    # ─── 搜索 ──────────────────────────────────────────────

    def _search_site(self, client, keyword: str, page: int, order_by: str,
                     time: str, category: str, sub_category):
        """兼容 jmcomic 新旧版本的站内搜索接口。"""
        search_kwargs = {
            "page": page,
            "order_by": order_by,
            "time": time,
            "category": category,
            "sub_category": sub_category,
        }
        if hasattr(client, "search_site"):
            return client.search_site(keyword, **search_kwargs)
        try:
            return client.search(keyword, **search_kwargs)
        except TypeError as e:
            if "main_tag" not in str(e):
                raise
            return client.search(keyword, page, 0, order_by, time, category, sub_category)

    def search_by_keyword(
        self, keyword: str, sort_order: str = "desc",
        page: int = 1, order_by: str = "mr", time_range: str = "a",
    ) -> List[Dict]:
        """关键词搜索，详情按需补充。

        order_by: mr=最新, mv=浏览量, mp=收藏量, tr=标题
        time_range: a=全部, t=今天, w=本周, m=本月
        """
        try:
            sort_order = (sort_order or "desc").strip().lower()
            if sort_order not in ("asc", "desc"):
                sort_order = "desc"
            page = max(1, int(page or 1))
            if order_by not in ("mr", "mv", "mp", "tr"):
                order_by = "mr"
            if time_range not in ("a", "t", "w", "m"):
                time_range = "a"

            # 搜索结果内存缓存：相同查询 / 翻回页直接返回
            cache_key = (keyword, sort_order, order_by, time_range, page)
            with self._search_cache_lock:
                entry = self._search_cache.get(cache_key)
                if entry and (time.time() - entry[0]) < self._search_cache_ttl:
                    print(f"搜索命中缓存: {keyword!r} p{page}")
                    return copy.deepcopy(entry[1])

            client = self._get_client()
            try:
                search_results = self._search_site(
                    client, keyword, page=page, order_by=order_by,
                    time=time_range, category="0", sub_category=None,
                )
                print(f"搜索成功，结果类型: {type(search_results)}")
            except Exception as e:
                print(f"搜索失败: {e}")
                search_results = None

            if not search_results:
                return []

            try:
                if hasattr(search_results, "album_info_list"):
                    albums = getattr(search_results, "album_info_list", [])
                elif hasattr(search_results, "content"):
                    albums = list(getattr(search_results, "content", []))
                elif hasattr(search_results, "__iter__") and not isinstance(
                    search_results, (str, bytes)
                ):
                    albums = list(search_results)
                else:
                    albums = [search_results] if search_results else []
            except Exception as e:
                print(f"获取专辑列表失败: {e}")
                albums = []

            if not albums:
                try:
                    tag_results = client.search_tag(keyword, page)
                    if (
                        tag_results
                        and hasattr(tag_results, "__iter__")
                        and not isinstance(tag_results, (str, bytes))
                    ):
                        albums = list(tag_results)
                except Exception as e:
                    print(f"标签搜索失败: {e}")

            comics = []
            for album in albums:
                try:
                    comic_info = self._extract_search_result(album)
                    if comic_info:
                        aid = comic_info.get("id")
                        if aid:
                            # 封面 URL 纯本地构造（无网络），直接带上，前端可跳过逐个往返
                            try:
                                from jmcomic.jm_toolkit import JmcomicText
                                comic_info["cover"] = JmcomicText.get_album_cover_url(aid)
                            except Exception:
                                pass
                        comics.append(comic_info)
                except Exception as e:
                    print(f"处理专辑信息失败: {e}, album: {album}")

            comics.sort(
                key=lambda x: self._parse_count(x.get("favorites", 0)),
                reverse=(sort_order == "desc"),
            )
            with self._search_cache_lock:
                self._search_cache[cache_key] = (time.time(), comics)
                if len(self._search_cache) > 200:
                    items = sorted(
                        self._search_cache.items(), key=lambda kv: kv[1][0], reverse=True
                    )
                    self._search_cache = dict(items[:150])
            return comics

        except Exception as e:
            print(f"关键词搜索失败 '{keyword}': {str(e)}")
            import traceback
            traceback.print_exc()
            return []

    def _expand_single(self, tag: str) -> List[str]:
        """展开单个标签的全部同义词（含自身）。惰性导入避免循环依赖。"""
        try:
            from core.services.filter_service import expand_tags
            return expand_tags([tag])
        except Exception:
            return [tag]

    def search_by_tags(self, tags: List[str], max_total: int = 80, mode: str = "or",
                       input_tags: Optional[List[str]] = None):
        """多标签搜索。返回 (results_list, stats_dict)。

        mode='or'：包含任一标签；mode='and'：必须包含所有标签。
        input_tags：用户实际输入的标签（同义词展开前）。
        """
        if not tags:
            return [], {}
        tag_list = [str(t).strip() for t in tags if str(t).strip()]
        if not tag_list:
            return [], {}
        client = self._get_client()
        tag_counts = {tag: 0 for tag in tag_list}

        # 1) 并发抓取每个标签的前 2 页，收集候选 id（去重）
        candidate_ids = []
        seen_ids = set()
        tag_page_counts = {tag: 0 for tag in tag_list}

        def _fetch_tag_page(args):
            tag, page = args
            try:
                page_res = client.search_tag(tag, page)
            except Exception as e:
                print(f"标签搜索失败 '{tag}' page={page}: {e}")
                return tag, []
            if not page_res:
                return tag, []
            ids = []
            for item in page_res:
                info = self._extract_search_result(item)
                if info and info.get("id"):
                    ids.append(info["id"])
            return tag, ids

        tasks = [(tag, page) for tag in tag_list for page in (1, 2)]
        max_workers = min(16, len(tasks)) if tasks else 1
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for tag, ids in executor.map(_fetch_tag_page, tasks):
                tag_page_counts[tag] += len(ids)
                for aid in ids:
                    if aid not in seen_ids:
                        seen_ids.add(aid)
                        candidate_ids.append(aid)

        tag_counts = tag_page_counts
        if not candidate_ids:
            tag_counts["_total"] = 0
            return [], tag_counts

        # 2) 批量补全详情（限制数量，避免过慢）
        pool_size = min(max_total * 2, 80)
        details = self.get_search_result_details(candidate_ids[:pool_size])

        # 3) 组装结果并过滤
        results = []
        for raw_id, detail in details.items():
            if not detail:
                continue
            detail_tags = {str(t).lower() for t in (detail.get("tags") or [])}
            if mode == "and":
                # 每个「原始输入标签」至少命中其任一展开同义词即算命中
                if input_tags:
                    hit = True
                    for ot in input_tags:
                        variants = {str(t).strip().lower() for t in self._expand_single(ot)}
                        if not (variants & detail_tags):
                            hit = False
                            break
                    if not hit:
                        continue
                else:
                    if not all(str(t).strip().lower() in detail_tags for t in tag_list):
                        continue
            results.append({
                "id": int(raw_id),
                "title": detail.get("title", "未知标题"),
                "author": detail.get("author", ""),
                "cover": "",
                "favorites": detail.get("favorites", 0),
                "tags": list(detail_tags),
                "_raw_tags": list(detail_tags),
                "pages": detail.get("pages", 0),
                "description": (detail.get("description") or "")[:100],
            })

        # 4) 排序并截断：命中用户实际输入名的作品优先
        if input_tags:
            lowered = {str(t).strip().lower() for t in input_tags if t}
            for r in results:
                raw = {str(t).strip().lower() for t in (r.pop("_raw_tags", []) or [])}
                r["_input_match"] = any(inp in raw for inp in lowered)
            results.sort(key=lambda x: (not x["_input_match"], -self._parse_count(x.get("favorites", 0))))
            for r in results:
                r.pop("_input_match", None)
        else:
            for r in results:
                r.pop("_raw_tags", None)
            results.sort(key=lambda x: self._parse_count(x.get("favorites", 0)), reverse=True)
        results = results[:max_total]
        tag_counts["_total"] = len(results)
        return results, tag_counts

    def search_by_author(self, author: str, max_total: int = 80) -> List[Dict]:
        """按作者搜索：站内关键词搜索后过滤作者字段（精确/包含匹配）。"""
        if not author or not author.strip():
            return []
        author = author.strip()
        try:
            candidates = self.search_by_keyword(author, page=1, order_by="mr", time_range="a")
            if not candidates:
                return []
            exact = [c for c in candidates if (c.get("author") or "").strip() == author]
            if exact:
                return exact[:max_total]
            fuzzy = [c for c in candidates if author in (c.get("author") or "")]
            return fuzzy[:max_total] or candidates[:max_total]
        except Exception as e:
            print(f"作者搜索失败 '{author}': {e}")
            return []

    # ─── 下载 ──────────────────────────────────────────────

    def download_comic(self, album_id: int, progress_callback=None,
                       cancel_check=None) -> bool:
        """下载漫画到 TempCache/downloads/<id>（下载管理器负责后续搬运）。

        cancel_check: 可调用对象，返回 True 时中断下载（抛 DownloadCancelled）。
        结束时（无论成败/取消）把速度采样写入 JSONL 下载日志，供分析下载惯性。
        """
        cb = _JmProgressCallback(progress_callback, cancel_check)
        session = _DownloadSession(album_id, cb)
        try:
            if progress_callback:
                progress_callback(0, "starting", "开始下载...")

            download_dir = os.path.join(self.base_dir, "TempCache", "downloads", str(album_id))
            os.makedirs(download_dir, exist_ok=True)
            # 续下检测：TempCache 有残留 → 已下好的图会被 jmcomic 的 skip 自动跳过
            _exist_files = 0
            for _r, _d, fs in os.walk(download_dir):
                _exist_files += len(fs)

            if progress_callback:
                if _exist_files:
                    progress_callback(10, "preparing",
                                      f"续传：检测到已下 {_exist_files} 个文件，自动跳过已下部分...")
                else:
                    progress_callback(10, "preparing", "准备下载环境...")

            option = self._build_option()

            if progress_callback:
                progress_callback(30, "downloading", "正在获取漫画信息...")

            try:
                import jmcomic
                jmcomic.download_album(
                    album_id,
                    option=option,
                    downloader=_build_hooked_downloader(cb),  # 钩子子类：进度/取消/字节统计的唯一正道
                    check_exception=False,
                )
                if progress_callback:
                    progress_callback(80, "downloading", "漫画下载中...")
                time.sleep(2)
            except DownloadCancelled:
                raise
            except Exception as e:
                print(f"JMComic 下载失败: {e}")
                ok = self._simple_download(album_id, progress_callback, cancel_check, cb)
                session.status = "completed" if ok else "failed"
                return ok

            if progress_callback:
                progress_callback(95, "processing", "下载完成，正在验证文件...")

            if not os.path.exists(download_dir):
                if progress_callback:
                    progress_callback(0, "error", "下载失败：目录未创建")
                return False

            # 只数真实文件：jmcomic 会先建 Pid 子目录，图片全失败时
            # os.listdir 也能看到子目录名 → 曾造成大量「假 completed」
            file_count = 0
            for _root, _dirs, fs in os.walk(download_dir):
                file_count += len(fs)
            if file_count == 0:
                if progress_callback:
                    progress_callback(0, "error", "下载失败：未产出任何图片文件")
                return False

            print(f"漫画 {album_id} 下载成功，文件数: {file_count}")
            if progress_callback:
                progress_callback(100, "completed", f"下载完成，共 {file_count} 个文件")
            session.status = "completed"
            return True

        except DownloadCancelled:
            # 取消必须继续向上抛：download_manager 三层都 re-raise，
            # 最终由 app._run_download_task 触发「取消并清理」；否则会被当成
            # 普通失败（历史记 failed、TempCache 不清理）。
            session.status = "cancelled"
            raise
        except Exception as e:
            print(f"下载漫画失败 {album_id}: {e}")
            import traceback
            traceback.print_exc()
            if progress_callback:
                progress_callback(0, "error", f"下载失败: {str(e)}")
            return False
        finally:
            session.finish()

    def _simple_download(self, album_id: int, progress_callback=None,
                         cancel_check=None, cb=None) -> bool:
        """备用下载流程（同样挂进度钩子）。"""
        try:
            if cancel_check and cancel_check():
                raise DownloadCancelled()
            if progress_callback:
                progress_callback(40, "downloading", "使用备用方式下载...")

            option = self._build_option()
            client = option.build_jm_client()

            if cancel_check and cancel_check():
                raise DownloadCancelled()
            album = client.get_album_detail(album_id)
            if not album:
                print(f"无法获取专辑 {album_id} 详情")
                return False

            if progress_callback:
                progress_callback(60, "downloading", "获取图片列表...")

            photo_detail = client.get_photo_detail(album_id)
            if not photo_detail:
                print(f"无法获取照片详情 {album_id}")
                return False

            if progress_callback:
                progress_callback(80, "downloading", "下载漫画内容...")

            if cb is not None:
                dler = _build_hooked_downloader(cb)(option)
            else:
                import jmcomic
                dler = jmcomic.JmDownloader(option)
            dler.download_album(album_id)

            if progress_callback:
                progress_callback(95, "processing", "下载完成")
            return True

        except Exception as e:
            print(f"备用下载失败 {album_id}: {e}")
            return False

    def _download_cover(self, cover_url: str, album_id: int) -> Optional[str]:
        """下载封面到本地缓存（带画质设置）。"""
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            from curl_cffi import requests as curl_requests
            response = curl_requests.get(cover_url, headers=headers, timeout=30)
            response.raise_for_status()

            cover_filename = f"cover_{album_id}.jpg"
            cover_path = os.path.join(self.temp_cache, cover_filename)

            image = Image.open(io.BytesIO(response.content))
            if image.mode == "RGBA":
                rgb_image = Image.new("RGB", image.size, (255, 255, 255))
                rgb_image.paste(image, mask=image.split()[3])
                image = rgb_image

            quality, subsampling = get_image_quality_params()
            image.save(cover_path, "JPEG", quality=quality, subsampling=subsampling)
            return cover_path

        except Exception as e:
            print(f"下载封面失败 {album_id}: {e}")
            return None


# ── 全局惰性单例 ──────────────────────────────────────────
# 进程内只保留一个 JMCrawler：构造时会同步加载 cover/detail 缓存等大 JSON，
# 放在 import 阶段会拖慢启动、窗口晚出现。改为首次使用时才构造。
_jm_crawler_instance = None
_jm_crawler_lock = threading.Lock()


def get_jm_crawler() -> "JMCrawler":
    """惰性单例：首次调用才构造 JMCrawler（提速应用启动）。"""
    global _jm_crawler_instance
    if _jm_crawler_instance is None:
        with _jm_crawler_lock:
            if _jm_crawler_instance is None:
                _jm_crawler_instance = JMCrawler()
    return _jm_crawler_instance