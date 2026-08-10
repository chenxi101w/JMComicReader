# -*- coding: utf-8 -*-
"""
JM 漫画爬虫服务。
"""

import concurrent.futures
import threading
import io
import copy
import json
import os
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

import jmcomic
from jmcomic.jm_toolkit import JmcomicText
from jmcomic.jm_downloader import DownloadCallback
import requests
import yaml
from PIL import Image

from core.config import BASE_DIR, TEMP_CACHE_DIR, OPTION_FILE
from core.models.database import get_system_config, get_image_quality_params


class _JmProgressCallback(DownloadCallback):
    """把 jmcomic 的逐章节下载进度映射到上层 progress_callback。"""

    def __init__(self, progress_callback):
        self._pc = progress_callback
        self._total = 0
        self._done = 0

    def before_album(self, album):
        try:
            self._total = int(getattr(album, "photo_count", 0) or 0)
            if self._total == 0:
                try:
                    self._total = len(list(album))
                except Exception:
                    self._total = 0
        except Exception:
            self._total = 0

    def after_photo(self, photo):
        self._done += 1
        if self._total > 0:
            pct = 20 + int(self._done / self._total * 60)  # 映射到 20~80%
        else:
            pct = 60
        try:
            self._pc(pct, "downloading", f"下载章节 {self._done}/{self._total}")
        except Exception:
            pass

    def after_album(self, album):
        try:
            self._pc(85, "processing", "章节下载完成，正在整理…")
        except Exception:
            pass


class JMCrawler:
    """JM 漫画爬虫服务。"""

    def __init__(self):
        self.base_dir = BASE_DIR
        self.temp_cache = TEMP_CACHE_DIR
        self.option_file = OPTION_FILE

        os.makedirs(self.temp_cache, exist_ok=True)
        self._ensure_option_file()

        self.jm_option = jmcomic.JmOption.from_file(self.option_file)
        self.cover_cache_file = os.path.join(self.temp_cache, "cover_cache.json")
        self.cover_cache = self._load_cover_cache()
        self.detail_cache_file = os.path.join(self.temp_cache, "detail_cache.json")
        self.detail_cache = self._load_detail_cache()
        # 详情缓存异步落盘用的锁 / 状态标记，避免每次搜索同步写大盘 JSON 卡住响应
        self._detail_save_lock = threading.Lock()
        self._detail_save_in_progress = False
        # 内存详情缓存的读写锁，避免并发搜索请求下字典迭代/重建竞争
        self._detail_cache_lock = threading.Lock()
        # 客户端复用：保留 HTTP keep-alive 连接池，重复搜索/翻页/封面域名解析显著变快
        self._client = None
        self._client_sig = None
        self._client_lock = threading.Lock()
        # 搜索结果内存缓存：相同查询 / 翻回页即时返回（JM 限流或抽风时也更稳）
        self._search_cache = {}
        self._search_cache_lock = threading.Lock()
        self._search_cache_ttl = 120  # 秒

    def _build_default_option_content(self) -> Dict:
        return {
            "client": {
                "retry_times": 5,
                "domain": [],
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
                "threading": {"batch_count": 30, "max_workers": 5},
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
        """Ensure jm_option.yml exists without overwriting user settings."""
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

        # 强制修正：下载目录必须跟随当前 BASE_DIR（避免旧路径/移动后失效）
        merged_content.setdefault("dir_rule", {})
        merged_content["dir_rule"]["base_dir"] = os.path.join(
            self.base_dir, "TempCache", "downloads"
        )

        # 强制移除 img2pdf（历史遗留：会污染 exe 目录且阅读器不用 PDF）
        plugins = merged_content.get("plugins")
        if isinstance(plugins, dict):
            after_photo = plugins.get("after_photo") or []
            if isinstance(after_photo, list):
                after_photo = [
                    p for p in after_photo
                    if (p.get("plugin") if isinstance(p, dict) else p) != "img2pdf"
                ]
                if after_photo:
                    plugins["after_photo"] = after_photo
                else:
                    plugins.pop("after_photo", None)
            if not plugins:
                merged_content.pop("plugins", None)

        if merged_content != current_content:
            self._write_option_file(merged_content)

    def _build_option(self):
        option = jmcomic.create_option_by_file(self.option_file)
        self._apply_proxy(option)
        return option

    def _apply_proxy(self, option) -> None:
        """根据设置中的代理配置，给 jmcomic option 注入代理。
        所有走 _build_option 的客户端（搜索 / 详情 / 下载）都会自动生效。"""
        try:
            enabled = get_system_config("proxy_enabled")
            url = (get_system_config("proxy_url") or "").strip()
            if enabled not in (None, "0", "false", "False", "") and url:
                if "://" not in url:
                    url = "http://" + url
                proxies = {"http": url, "https": url}
                option.client["postman"]["meta_data"]["proxies"] = proxies
        except Exception as e:
            print(f"应用代理设置失败: {e}")

    def _build_client(self):
        return self._build_option().build_jm_client()

    def _client_signature(self):
        """客户端可复用的签名：option 文件 mtime + 代理配置。任一变化即重建。"""
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
            # 紧凑写入（去掉 indent），大幅减小体积与写入耗时
            with open(self.detail_cache_file, "w", encoding="utf-8") as f:
                json.dump(self.detail_cache, f, ensure_ascii=False, separators=(",", ":"))
        except Exception as e:
            print(f"保存详情缓存失败: {e}")

    def _save_detail_cache_async(self):
        """后台线程落盘，避免在大体积缓存时阻塞搜索响应（造成"忽快忽慢"的卡顿感）。"""
        if self._detail_save_in_progress:
            return
        self._detail_save_in_progress = True

        def _worker():
            try:
                with self._detail_save_lock:
                    self._save_detail_cache()
            finally:
                self._detail_save_in_progress = False

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

    def get_comic_info(self, album_id: int) -> Optional[Dict]:
        """获取漫画详细信息。"""
        try:
            client = self._get_client()
            album = client.get_album_detail(album_id)
            if not album:
                return None

            comic_info = {
                "id": album_id,
                "title": getattr(album, "title", "Unknown"),
                "author": getattr(album, "author", "Unknown"),
                "cover": "",
                "tags": list(getattr(album, "tags", []) or []),
                "description": getattr(album, "description", ""),
                "favorites": self._parse_count(getattr(album, "likes", 0)),
                "pages": getattr(album, "page_count", 0),
                "scramble_id": getattr(album, "scramble_id", ""),
                "works": list(getattr(album, "works", []) or []),
                "actors": list(getattr(album, "actors", []) or []),
                "keywords": list(getattr(album, "keywords", []) or []),
            }

            cover_url = self.get_cover_url(album_id)
            if cover_url:
                comic_info["cover"] = cover_url
                cover_path = self._download_cover(cover_url, album_id)
                if cover_path:
                    comic_info["cover_local"] = cover_path

            return comic_info

        except Exception as e:
            print(f"获取漫画信息失败 {album_id}: {e}")
            return None

    def get_cover_url(self, album_id: int) -> str:
        """获取封面 URL。"""
        cache_key = str(album_id)
        if cache_key in self.cover_cache:
            print(f"从缓存获取封面 {album_id}")
            return self.cover_cache[cache_key]

        try:
            # 使用 jmcomic 维护的图片 CDN 域名列表生成封面地址，
            # 避免使用失效的固定域名（如旧版 cdnhth.club）。
            cover_url = JmcomicText.get_album_cover_url(album_id)
            self.cover_cache[cache_key] = cover_url
            self._save_cover_cache()
            return cover_url
        except Exception as e:
            print(f"获取封面 URL 失败 {album_id}: {e}")
            return ""

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
                for key in (
                    "favorites",
                    "likes",
                    "like",
                    "favourites",
                    "favorite",
                    "fav",
                ):
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
                "title": getattr(album, "title", None)
                or getattr(album, "name", "未知标题"),
                "author": getattr(album, "author", "未知作者"),
                "cover": "",
                "favorites": self._parse_count(favorites_raw),
                "tags": list(getattr(album, "tags", []) or []),
                "description": (getattr(album, "description", None) or "")[:100],
            }
        elif isinstance(album, dict):
            favorites_raw = 0
            for key in (
                "favorites",
                "likes",
                "like",
                "favourites",
                "favorite",
                "fav",
            ):
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
        # 命中本地详情缓存的先直接取，避免重复网络请求（重复搜同一批标签≈秒出）
        missing = []
        for album_id in cleaned_ids:
            with self._detail_cache_lock:
                cached = self.detail_cache.get(str(album_id))
            if cached:
                details[str(album_id)] = cached
            else:
                missing.append(album_id)

        # 一次性批量补全缺失的详情。
        # 注意：绝不能把下面整块放进上面的 for 循环里——否则每发现一个未缓存
        # id 都会重跑整个线程池，造成 O(N^2) 重复请求（30 个结果 ≈ 465 次网络
        # 请求），这是此前"开了代理能搜但极慢"的根因。
        if missing:
            client = self._get_client()
            max_workers = min(16, len(missing))

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(self._fetch_search_detail, client, album_id): album_id
                    for album_id in missing
                }

                # 整体超时保护：JM 服务器偶发抽风/限流时，已完成的先采用，
                # 未完成的本次放弃（不阻塞搜索响应，下次再补），避免整批卡死。
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

            # 控制缓存体积（dict 保序，保留最近 3000 条，文件更小落盘更快）
            with self._detail_cache_lock:
                if len(self.detail_cache) > 3500:
                    self.detail_cache = dict(list(self.detail_cache.items())[-3000:])
            self._save_detail_cache_async()

        return details

    def _search_site(
        self,
        client,
        keyword: str,
        page: int,
        order_by: str,
        time: str,
        category: str,
        sub_category,
    ):
        """
        兼容 jmcomic 新旧版本的站内搜索接口。
        新版把 main_tag 提升为了必填参数，并提供了 search_site 包装方法。
        """
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
            return client.search(
                keyword,
                page,
                0,
                order_by,
                time,
                category,
                sub_category,
            )

    def search_by_keyword(
        self, keyword: str, sort_order: str = "desc",
        page: int = 1, order_by: str = "mr", time_range: str = "a",
    ) -> List[Dict]:
        """轻量搜索，详情按需补充。

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

            # 搜索结果内存缓存：相同查询 / 翻回页直接返回，跳过 JM 网络请求
            cache_key = (keyword, sort_order, order_by, time_range, page)
            with self._search_cache_lock:
                entry = self._search_cache.get(cache_key)
                if entry and (time.time() - entry[0]) < self._search_cache_ttl:
                    print(f"搜索命中缓存: {keyword!r} p{page}")
                    return copy.deepcopy(entry[1])

            client = self._get_client()

            try:
                search_results = self._search_site(
                    client,
                    keyword,
                    page=page,
                    order_by=order_by,
                    time=time_range,
                    category="0",
                    sub_category=None,
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
                            # 封面 URL 是纯本地构造（无网络），直接在结果里带上，
                            # 前端即可跳过逐个 /api/cover 往返，浏览器直连 JM CDN 拉图。
                            try:
                                comic_info["cover"] = JmcomicText.get_album_cover_url(aid)
                            except Exception:
                                pass
                        comics.append(comic_info)
                except Exception as e:
                    print(f"处理专辑信息失败: {e}, album: {album}")

            # 关键词搜索不再批量补全详情：之前为显示标签对每个结果都并发抓
            # album_detail，导致 3s 变 5s+。标签/完整信息可在详情页单独获取，
            # 或前端对可见卡片调用 /api/search/enrich 按需补充。

            comics.sort(
                key=lambda x: self._parse_count(x.get("favorites", 0)),
                reverse=(sort_order == "desc"),
            )
            # 写入搜索结果缓存（深拷贝隔离，避免调用方 postprocess 原地归一化污染缓存）
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
        """展开单个标签的全部同义词（含自身）。惰性导入 filter_service 避免循环依赖。"""
        try:
            from core.services.filter_service import expand_tags
            return expand_tags([tag])
        except Exception:
            return [tag]

    def search_by_tags(
        self, tags: List[str], max_total: int = 80, mode: str = "or",
        input_tags: Optional[List[str]] = None
    ):
        """多标签搜索。调用 jmcomic 的 search_tag，再批量补全详情，保证结果有完整标签/作者信息。
        mode='or'：包含任一标签；mode='and'：必须包含所有标签。
        input_tags：用户实际输入的标签（展开同义词前的原文）。提供时，结果中命中用户输入名的
        作品优先排在前面（搜索优先级=input 的默认行为）。
        返回 (results_list, stats_dict)。"""
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
                # 每个「原始输入标签」至少命中其任一展开同义词即算命中；
                # 用 tag_list（展开后的全部变体）做 OR 判定等价，但要求每个输入标签都命中。
                # 这里直接基于用户实际输入 input_tags 判定，避免同义词交叉导致「且」失效。
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
                    # 无 input_tags 时退化为：所有展开变体都需命中（保留原语义）
                    if not all(str(t).strip().lower() in detail_tags for t in tag_list):
                        continue
            else:
                # or 模式下所有候选本来就是按标签搜出来的，直接保留
                pass
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

        # 4) 排序并截断
        if input_tags:
            lowered = {str(t).strip().lower() for t in input_tags if t}
            for r in results:
                raw = {str(t).strip().lower() for t in (r.pop("_raw_tags", []) or [])}
                # 命中用户实际输入的名字 -> 优先排在前面
                r["_input_match"] = any(inp in raw for inp in lowered)
            # 先按是否命中输入名，再按收藏数降序
            results.sort(key=lambda x: (not x["_input_match"],
                                         -self._parse_count(x.get("favorites", 0))))
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
            # 先尝试精确匹配
            exact = [c for c in candidates if (c.get("author") or "").strip() == author]
            if exact:
                return exact[:max_total]
            # 退而求其次：作者包含关键词
            fuzzy = [c for c in candidates if author in (c.get("author") or "")]
            return fuzzy[:max_total] or candidates[:max_total]
        except Exception as e:
            print(f"作者搜索失败 '{author}': {e}")
            return []

    def download_comic(self, album_id: int, progress_callback=None) -> bool:
        """下载漫画。"""
        try:
            if progress_callback:
                progress_callback(0, "starting", "开始下载...")

            download_dir = os.path.join(
                self.base_dir, "TempCache", "downloads", str(album_id)
            )
            os.makedirs(download_dir, exist_ok=True)

            if progress_callback:
                progress_callback(10, "preparing", "准备下载环境...")

            option = self._build_option()

            if progress_callback:
                progress_callback(30, "downloading", "正在获取漫画信息...")

            try:
                jmcomic.download_album(
                    album_id,
                    option=option,
                    callback=_JmProgressCallback(progress_callback),
                    check_exception=False,
                )

                if progress_callback:
                    progress_callback(80, "downloading", "漫画下载中...")

                time.sleep(2)
            except Exception as e:
                print(f"JMComic 下载失败: {e}")
                return self._simple_download(album_id, progress_callback)

            if progress_callback:
                progress_callback(95, "processing", "下载完成，正在验证文件...")

            if not os.path.exists(download_dir):
                if progress_callback:
                    progress_callback(0, "error", "下载失败：目录未创建")
                return False

            files = os.listdir(download_dir)
            if not files:
                if progress_callback:
                    progress_callback(0, "error", "下载失败：目录为空")
                return False

            print(f"漫画 {album_id} 下载成功，文件数: {len(files)}")
            if progress_callback:
                progress_callback(100, "completed", f"下载完成，共 {len(files)} 个文件")
            return True

        except Exception as e:
            print(f"下载漫画失败 {album_id}: {e}")
            import traceback

            traceback.print_exc()
            if progress_callback:
                progress_callback(0, "error", f"下载失败: {str(e)}")
            return False

    def _simple_download(self, album_id: int, progress_callback=None) -> bool:
        """备用下载流程。"""
        try:
            if progress_callback:
                progress_callback(40, "downloading", "使用备用方式下载...")

            option = self._build_option()
            client = option.build_jm_client()

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

            downloader = jmcomic.JmDownloader(option)
            downloader.download_album(album_id)

            if progress_callback:
                progress_callback(95, "processing", "下载完成")

            return True

        except Exception as e:
            print(f"备用下载失败 {album_id}: {e}")
            return False

    def _download_cover(self, cover_url: str, album_id: int) -> Optional[str]:
        """下载封面图片。"""
        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36"
                )
            }
            response = requests.get(cover_url, headers=headers, timeout=30)
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
