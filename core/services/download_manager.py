# -*- coding: utf-8 -*-
"""下载管理器（轻量版）。

从旧版搬改：异步下载 → TempCache 中转 → 扁平化搬运进 DownloadedComics →
重压缩（按画质设置）→ 落库；含启动时临时下载恢复。
"""

import asyncio
import io
import json
import os
import shutil
from datetime import datetime
from typing import Callable

from PIL import Image

from core.config import BASE_DIR, DOWNLOAD_DIR, TEMP_CACHE_DIR
from core.models.database import get_image_quality_params
from core.services.jm_crawler import JMCrawler, DownloadCancelled, get_jm_crawler


class DownloadManager:
    """负责漫画的异步下载和落库。"""

    def __init__(self):
        self.base_dir = BASE_DIR
        self.downloaded_dir = DOWNLOAD_DIR
        self.temp_dir = TEMP_CACHE_DIR

        os.makedirs(self.downloaded_dir, exist_ok=True)
        os.makedirs(self.temp_dir, exist_ok=True)

        # 不在此构造 JMCrawler：复用全局惰性单例（首次下载/恢复时才创建），
        # 避免 import 阶段多建一个实例、重复加载大缓存拖慢启动。
        self.jm_crawler = None

    def _clean_filename(self, filename: str) -> str:
        illegal_chars = ["<", ">", ":", '"', "|", "?", "*", "\\", "/", "[", "]", "(", ")"]
        for char in illegal_chars:
            filename = filename.replace(char, "_")
        filename = "".join(char for char in filename if ord(char) >= 32)
        if len(filename) > 100:
            filename = filename[:100]
        filename = filename.strip(". ")
        return filename or "comic"

    def _get_current_time(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def download_comic(self, jm_id: int, comic_info: dict,
                       progress_callback: Callable, cancel_check=None) -> bool:
        """同步包装异步下载。支持 cancel_check 取消信号。"""
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(
                self.download_comic_async(jm_id, comic_info, progress_callback, cancel_check)
            )
        except DownloadCancelled:
            raise
        except Exception as e:
            progress_callback(0, "error", f"下载失败: {str(e)}")
            return False
        finally:
            loop.close()

    async def download_comic_async(self, jm_id: int, comic_info: dict,
                                   progress_callback: Callable,
                                   cancel_check=None) -> bool:
        try:
            safe_title = self._clean_filename(comic_info["title"])
            comic_dir = os.path.join(self.downloaded_dir, f"{jm_id}_{safe_title}")
            os.makedirs(comic_dir, exist_ok=True)

            progress_callback(5, "preparing", "准备下载环境...")

            if comic_info.get("cover"):
                await self._download_image_async(
                    comic_info["cover"], os.path.join(comic_dir, "cover.jpg"),
                )

            progress_callback(15, "downloading", "正在下载漫画图片...")

            success = await self._real_comic_download(
                jm_id, comic_dir, progress_callback, cancel_check
            )
            if not success:
                raise RuntimeError("下载失败，未生成可用文件")

            await self._save_comic_info(comic_dir, comic_info)
            await self._ensure_files_ready(comic_dir, jm_id)

            progress_callback(100, "completed", "下载完成")
            return True

        except DownloadCancelled:
            raise
        except Exception as e:
            progress_callback(0, "error", f"下载失败: {str(e)}")
            return False

    async def _real_comic_download(self, jm_id: int, comic_dir: str,
                                   progress_callback: Callable,
                                   cancel_check=None) -> bool:
        """执行真实下载流程（jmcomic → TempCache → 搬运 → 重压缩）。"""

        def update_progress(progress, status, message, extra=None):
            if status == "starting":
                progress_callback(15, "downloading", "开始下载...")
            elif status == "downloading":
                mapped_progress = 15 + (progress / 100) * 70
                progress_callback(int(mapped_progress), "downloading", message, extra)
            elif status == "processing":
                progress_callback(85, "processing", message)
            elif status == "completed":
                progress_callback(95, "processing", message)
            elif status == "error":
                progress_callback(0, "error", message)

        try:
            if self.jm_crawler is None:
                self.jm_crawler = get_jm_crawler()
            success = self.jm_crawler.download_comic(
                jm_id, update_progress, cancel_check=cancel_check
            )
            if not success:
                return False

            temp_download_dir = os.path.join(self.base_dir, "TempCache", "downloads", str(jm_id))
            if not os.path.exists(temp_download_dir):
                return False

            subdirs = [
                name for name in os.listdir(temp_download_dir)
                if os.path.isdir(os.path.join(temp_download_dir, name))
            ]

            if len(subdirs) == 1 and subdirs[0] == str(jm_id):
                chapter_dir = os.path.join(temp_download_dir, subdirs[0])
                for filename in os.listdir(chapter_dir):
                    shutil.move(
                        os.path.join(chapter_dir, filename),
                        os.path.join(comic_dir, filename),
                    )
            elif len(subdirs) > 1:
                for subdir in subdirs:
                    shutil.move(
                        os.path.join(temp_download_dir, subdir),
                        os.path.join(comic_dir, subdir),
                    )
            else:
                for filename in os.listdir(temp_download_dir):
                    shutil.move(
                        os.path.join(temp_download_dir, filename),
                        os.path.join(comic_dir, filename),
                    )

            # 按图像质量设置重压缩（original 不调用，保持最高保真）
            quality, subsampling = get_image_quality_params()
            if quality < 100 or subsampling != 0:
                self._recompress_images(comic_dir, quality, subsampling)

            try:
                shutil.rmtree(temp_download_dir)
            except Exception:
                pass
            return True

        except DownloadCancelled:
            raise
        except Exception as e:
            print(f"真实下载失败 {jm_id}: {e}")
            raise RuntimeError(f"真实下载失败: {e}") from e

    async def recover_temp_downloads(self):
        """启动时把 TempCache/downloads 中已下载完成但未最终搬运的漫画补完进 DownloadedComics。"""
        from core.services.comic_manager import ComicManager
        try:
            temp_root = os.path.join(self.base_dir, "TempCache", "downloads")
            if not os.path.isdir(temp_root):
                return
            comic_manager = ComicManager()
            for name in list(os.listdir(temp_root)):
                if not name.isdigit():
                    continue
                jm_id = int(name)
                temp_dir = os.path.join(temp_root, name)
                if not os.path.isdir(temp_dir):
                    continue
                if comic_manager._comic_already_finalized(jm_id):
                    continue
                imgs = comic_manager._collect_images_recursive(temp_dir)
                if not imgs:
                    continue
                print(f"恢复临时下载: {jm_id}（{len(imgs)} 张图片）")
                await self._finalize_recovered(jm_id, temp_dir, imgs, comic_manager)
        except Exception as e:
            print(f"恢复临时下载失败: {e}")

    async def _finalize_recovered(self, jm_id: int, temp_dir: str, imgs: list,
                                  comic_manager) -> None:
        """把 TempCache 中滞留的页面补完进 DownloadedComics（扁平化、顺序命名）。"""
        try:
            title = str(jm_id)
            author = ""
            tags = []
            try:
                if self.jm_crawler is None:
                    self.jm_crawler = get_jm_crawler()
                details = self.jm_crawler.get_search_result_details([jm_id])
                d = details.get(str(jm_id)) or {}
                if d.get("title"):
                    title = d["title"]
                author = d.get("author", "") or ""
                tags = d.get("tags", []) or []
            except Exception:
                pass

            safe_title = self._clean_filename(title)
            comic_dir = os.path.join(self.downloaded_dir, f"{jm_id}_{safe_title}")
            os.makedirs(comic_dir, exist_ok=True)

            # 扁平化：按自然顺序命名为 00001.jpg ...
            for i, src in enumerate(imgs, start=1):
                dst = os.path.join(comic_dir, f"{i:05d}.jpg")
                if os.path.abspath(src) != os.path.abspath(dst):
                    shutil.move(src, dst)

            info = {
                "id": jm_id, "title": title, "author": author, "tags": tags,
                "description": "", "pages": len(imgs),
                "download_time": self._get_current_time(),
            }
            with open(os.path.join(comic_dir, "info.json"), "w", encoding="utf-8") as f:
                json.dump(info, f, ensure_ascii=False, indent=2)

            comic_manager.add_downloaded_comic(jm_id, info)

            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass
            print(f"已恢复漫画 {jm_id} -> {comic_dir}")
        except Exception as e:
            print(f"补完漫画 {jm_id} 失败: {e}")

    def _recompress_images(self, comic_dir: str, quality: int, subsampling) -> None:
        """按图像质量设置重压缩已落库的漫画图片（封面不处理）。"""
        for root, _dirs, files in os.walk(comic_dir):
            for filename in files:
                low = filename.lower()
                if not low.endswith((".jpg", ".jpeg")):
                    continue
                if low.startswith("cover"):
                    continue
                path = os.path.join(root, filename)
                try:
                    with Image.open(path) as img:
                        if img.mode in ("RGBA", "P", "LA"):
                            img = img.convert("RGB")
                        img.save(path, "JPEG", quality=quality, subsampling=subsampling)
                except Exception:
                    pass

    async def _download_image_async(self, url: str, save_path: str):
        """下载封面图（curl_cffi 同步请求；保持 async 签名与调用方兼容）。"""
        try:
            from curl_cffi import requests as curl_requests
            response = curl_requests.get(url, timeout=30)
            if response.status_code != 200:
                return
            content = response.content
            image = Image.open(io.BytesIO(content))
            if image.mode == "RGBA":
                rgb_image = Image.new("RGB", image.size, (255, 255, 255))
                rgb_image.paste(image, mask=image.split()[3])
                image = rgb_image
            quality, subsampling = get_image_quality_params()
            image.save(save_path, "JPEG", quality=quality, subsampling=subsampling)
        except Exception as e:
            print(f"异步下载图片失败 {url}: {e}")

    async def _save_comic_info(self, comic_dir: str, comic_info: dict):
        """保存漫画元信息到 info.json。"""
        try:
            save_info = {
                "id": comic_info.get("id"),
                "title": comic_info.get("title"),
                "author": comic_info.get("author"),
                "tags": comic_info.get("tags", []),
                "description": comic_info.get("description"),
                "pages": comic_info.get("pages", 0),
                "download_time": self._get_current_time(),
            }
            with open(os.path.join(comic_dir, "info.json"), "w", encoding="utf-8") as f:
                json.dump(save_info, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存漫画信息失败: {e}")

    async def _ensure_files_ready(self, comic_dir: str, jm_id: int):
        """等待文件落盘后写入数据库。"""
        try:
            await asyncio.sleep(1)

            required_files = []
            subdirs = [
                name for name in os.listdir(comic_dir)
                if os.path.isdir(os.path.join(comic_dir, name))
            ]
            exts = (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp")

            if subdirs:
                for subdir in subdirs:
                    chapter_path = os.path.join(comic_dir, subdir)
                    images = [f for f in os.listdir(chapter_path) if f.lower().endswith(exts)]
                    required_files.extend(
                        os.path.join(chapter_path, f) for f in images
                    )
            else:
                images = [
                    f for f in os.listdir(comic_dir)
                    if f.lower().endswith(exts) and not f.startswith("cover")
                ]
                required_files.extend(os.path.join(comic_dir, f) for f in images)

            wait_time = 0
            while wait_time < 10:
                all_ready = True
                for file_path in required_files:
                    if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
                        all_ready = False
                        break
                if all_ready:
                    break
                await asyncio.sleep(1)
                wait_time += 1

            print(f"漫画 {jm_id} 文件准备就绪，共 {len(required_files)} 个文件")

            from core.services.comic_manager import ComicManager
            comic_manager = ComicManager()
            info_path = os.path.join(comic_dir, "info.json")
            comic_info = {}
            if os.path.exists(info_path):
                with open(info_path, "r", encoding="utf-8") as f:
                    comic_info = json.load(f)

            comic_info["id"] = jm_id
            success = comic_manager.add_downloaded_comic(jm_id, comic_info)
            if success:
                print(f"漫画 {jm_id} 已添加到数据库")
            else:
                print(f"漫画 {jm_id} 添加到数据库失败")

        except Exception as e:
            print(f"确保文件准备就绪失败 {jm_id}: {e}")