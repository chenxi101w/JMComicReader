# -*- coding: utf-8 -*-
"""
漫画管理器 — 已下载漫画的管理、阅读、分类。
"""

import os
import re
import json
import shutil
import sqlite3
from typing import List, Dict, Optional

import jmcomic

from core.config import BASE_DIR, DOWNLOAD_DIR, DB_FILE
from core.models.database import (
    _get_conn,
    get_comic_categories,
    set_comic_categories,
)


class ComicManager:
    """本地漫画生命周期管理。"""

    def __init__(self):
        self.base_dir = BASE_DIR
        self.downloaded_dir = DOWNLOAD_DIR
        os.makedirs(self.downloaded_dir, exist_ok=True)

    # ─── 路径/图片辅助 ─────────────────────────────────────
    def _find_downloaded_dir(self, jm_id: int) -> Optional[str]:
        for name in os.listdir(self.downloaded_dir):
            if name.startswith(f"{jm_id}_"):
                return os.path.join(self.downloaded_dir, name)
        return None

    def _comic_already_finalized(self, jm_id: int) -> bool:
        return self._find_downloaded_dir(jm_id) is not None

    def _collect_images_recursive(self, root: str) -> List[str]:
        """递归收集目录下所有图片（排除封面），按自然顺序返回完整路径列表。"""
        exts = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}
        files = []
        for dp, _, fns in os.walk(root):
            for fn in fns:
                if os.path.splitext(fn)[1].lower() in exts and not fn.lower().startswith("cover"):
                    files.append(os.path.join(dp, fn))

        def _nat(p: str):
            return [int(s) if s.isdigit() else s for s in re.split(r'(\d+)', p)]

        files.sort(key=_nat)
        return files

    def _scan_chapters_in_dir(self, root: str) -> List[Dict]:
        """扫描单个漫画根目录（平铺页面或分章子目录），返回章节列表。"""
        if not root or not os.path.isdir(root):
            return []
        exts = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}
        chapters: List[Dict] = []
        subdirs = [d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))]
        if subdirs:
            def _sk(d):
                try:
                    return int(d)
                except ValueError:
                    return d
            ordered = sorted(subdirs, key=_sk)
            idx = 0
            for sub in ordered:
                sp = os.path.join(root, sub)
                pages = sum(
                    1 for f in os.listdir(sp)
                    if os.path.splitext(f)[1].lower() in exts and not f.lower().startswith("cover")
                )
                if pages > 0:
                    idx += 1
                    chapters.append({
                        "id": sub, "name": f"第{idx}章",
                        "pages": list(range(1, pages + 1)), "path": sp, "index": idx - 1,
                    })
        else:
            page_count = sum(
                1 for f in os.listdir(root)
                if os.path.splitext(f)[1].lower() in exts and not f.lower().startswith("cover")
            )
            if page_count > 0:
                chapters.append({
                    "id": "1", "name": "第1章",
                    "pages": list(range(1, page_count + 1)), "path": root, "index": 0,
                })
        return chapters

    # ─── 数据库操作 ────────────────────────────────────────

    def is_comic_downloaded(self, jm_id: int) -> bool:
        conn = _get_conn()
        row = conn.execute("SELECT id FROM downloaded_comics WHERE jm_id = ?", (jm_id,)).fetchone()
        if row:
            for name in os.listdir(self.downloaded_dir):
                if name.startswith(f"{jm_id}_"):
                    return True
        return False

    def add_downloaded_comic(self, jm_id: int, comic_info: dict) -> bool:
        conn = _get_conn()
        try:
            comic_dir = None
            for name in os.listdir(self.downloaded_dir):
                if name.startswith(f"{jm_id}_"):
                    comic_dir = os.path.join(self.downloaded_dir, name)
                    break
            if not comic_dir:
                return False

            pdf_path = None
            for fname in os.listdir(comic_dir):
                if fname.endswith(".pdf"):
                    pdf_path = os.path.join(comic_dir, fname)
                    break

            cover_path = None
            if os.path.exists(os.path.join(comic_dir, "cover.jpg")):
                cover_path = os.path.join(comic_dir, "cover.jpg")

            file_size = 0
            try:
                for root, _, files in os.walk(comic_dir):
                    for f in files:
                        fp = os.path.join(root, f)
                        if os.path.isfile(fp):
                            file_size += os.path.getsize(fp)
            except Exception:
                pass

            chapter_count = len(self.get_comic_chapters(jm_id))

            # 页数从本地实际图片文件统计（下载信息里没有 pages 字段，必须自算）
            page_count = len(self._collect_images_recursive(comic_dir))

            conn.execute(
                """INSERT OR REPLACE INTO downloaded_comics
                   (jm_id, title, author, tags, description, favorites, pages, chapter_count, cover_path, comic_path, file_size)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    jm_id,
                    comic_info.get("title", ""),
                    comic_info.get("author", ""),
                    ",".join(comic_info.get("tags", [])),
                    comic_info.get("description", ""),
                    comic_info.get("favorites", 0),
                    page_count,
                    chapter_count,
                    cover_path,
                    pdf_path,
                    file_size,
                ),
            )
            conn.commit()
            return True
        except Exception as e:
            print(f"添加已下载漫画失败 {jm_id}: {e}")
            return False

    def get_downloaded_comics(self, category_id: Optional[int] = None) -> List[Dict]:
        """获取已下载漫画列表。可选按分类筛选。"""
        conn = _get_conn()
        if category_id is not None:
            query = """SELECT d.jm_id, d.title, d.author, d.tags, d.favorites, d.pages, d.chapter_count,
                              d.download_time, d.last_read_time, d.read_progress, d.file_size
                       FROM downloaded_comics d
                       INNER JOIN comic_categories cc ON d.jm_id = cc.comic_jm_id
                       WHERE cc.category_id = ?
                       ORDER BY d.download_time DESC"""
            rows = conn.execute(query, (category_id,)).fetchall()
        else:
            query = """SELECT jm_id, title, author, tags, favorites, pages, chapter_count,
                              download_time, last_read_time, read_progress, file_size
                       FROM downloaded_comics ORDER BY download_time DESC"""
            rows = conn.execute(query).fetchall()

        comics = []
        for row in rows:
            jm_id = row["jm_id"]
            comic_dir = None
            for name in os.listdir(self.downloaded_dir):
                if name.startswith(f"{jm_id}_"):
                    comic_dir = os.path.join(self.downloaded_dir, name)
                    break
            if not comic_dir:
                continue

            cover_path = None
            if os.path.exists(os.path.join(comic_dir, "cover.jpg")):
                cover_path = os.path.join(comic_dir, "cover.jpg")

            # 历史数据可能 pages=0（旧版未统计），按本地实际图片文件修复一次并回写 DB
            pages = row["pages"]
            if not pages or pages <= 0:
                try:
                    pages = len(self._collect_images_recursive(comic_dir))
                    if pages > 0:
                        conn.execute(
                            "UPDATE downloaded_comics SET pages = ? WHERE jm_id = ?",
                            (pages, jm_id),
                        )
                        conn.commit()
                except Exception:
                    pages = row["pages"] or 0

            # 附加分类 ID 列表
            cat_ids = get_comic_categories(jm_id)

            comics.append({
            "id": jm_id,
            "title": row["title"],
            "author": row["author"],
            "tags": row["tags"].split(",") if row["tags"] else [],
            "favorites": row["favorites"],
            "pages": pages,
            "chapter_count": row["chapter_count"] or 0,
            "cover_path": cover_path,
            "download_time": row["download_time"],
            "last_read_time": row["last_read_time"],
            "read_progress": row["read_progress"],
            "file_size": row["file_size"],
            "category_ids": cat_ids,
        })
        return comics

    def get_comic_path(self, jm_id: int) -> Optional[str]:
        for name in os.listdir(self.downloaded_dir):
            if name.startswith(f"{jm_id}_"):
                comic_dir = os.path.join(self.downloaded_dir, name)
                for fname in os.listdir(comic_dir):
                    if fname.endswith(".pdf"):
                        return os.path.join(comic_dir, fname)
                subdir = os.path.join(comic_dir, str(jm_id))
                if os.path.isdir(subdir):
                    return subdir
                return comic_dir
        return None

    def get_comic_pages(self, jm_id: int) -> int:
        conn = _get_conn()
        row = conn.execute("SELECT pages FROM downloaded_comics WHERE jm_id = ?", (jm_id,)).fetchone()
        if row and row["pages"] and row["pages"] > 0:
            return row["pages"]

        comic_dir = None
        for name in os.listdir(self.downloaded_dir):
            if name.startswith(f"{jm_id}_"):
                comic_dir = os.path.join(self.downloaded_dir, name)
                break
        if not comic_dir:
            return 0

        exts = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}
        return sum(1 for f in os.listdir(comic_dir) if os.path.splitext(f)[1].lower() in exts)

    # ─── 章节 / 页面 ──────────────────────────────────────

    def _get_chapter_order_from_jm(self, jm_id: int) -> Optional[List[str]]:
        try:
            client = jmcomic.JmOption.default().new_jm_client()
            album = client.get_album_detail(jm_id)
            return [str(p.photo_id) for p in album]
        except Exception as e:
            print(f"从JM获取章节顺序失败 {jm_id}: {e}")
            return None

    def get_comic_chapters(self, jm_id: int) -> List[Dict]:
        comic_dir = self._find_downloaded_dir(jm_id)
        if comic_dir:
            chapters = self._scan_chapters_in_dir(comic_dir)
            if chapters and len(chapters) > 1:
                # 分章结构：尝试用 JM 顺序校正（失败则保留本地顺序，离线可用）
                chapter_order = self._get_chapter_order_from_jm(jm_id)
                if chapter_order:
                    by_id = {c["id"]: c for c in chapters}
                    ordered = []
                    for oid in chapter_order:
                        c = by_id.pop(oid, None)
                        if c:
                            ordered.append(c)
                    ordered += list(by_id.values())
                    for i, c in enumerate(ordered):
                        c["index"] = i
                        c["name"] = f"第{i + 1}章"
                    chapters = ordered
            if chapters:
                return chapters

        # 兜底：临时下载目录（下载未完成最终搬运、页面滞留 TempCache 时也能阅读）
        temp = os.path.join(self.base_dir, "TempCache", "downloads", str(jm_id))
        if os.path.isdir(temp):
            imgs = self._collect_images_recursive(temp)
            if imgs:
                return [{
                    "id": "1", "name": "第1章",
                    "pages": list(range(1, len(imgs) + 1)), "path": temp, "index": 0,
                }]
        return []

    def get_comic_page_path(self, jm_id: int, page_num: int, chapter_id: Optional[str] = None) -> Optional[str]:
        comic_dir = self._find_downloaded_dir(jm_id)
        if not comic_dir:
            temp = os.path.join(self.base_dir, "TempCache", "downloads", str(jm_id))
            if os.path.isdir(temp):
                comic_dir = temp
        if not comic_dir:
            return None

        if chapter_id:
            chapter_path = os.path.join(comic_dir, chapter_id)
            if os.path.isdir(chapter_path):
                comic_dir = chapter_path

        candidates = [
            f"{page_num:05d}.jpg", f"{page_num:05d}.png",
            f"{page_num:04d}.jpg", f"{page_num:04d}.png",
            f"{page_num:03d}.jpg", f"{page_num:03d}.png",
            f"{page_num:02d}.jpg", f"{page_num:02d}.png",
            f"{page_num}.jpg", f"{page_num}.png",
        ]
        for fname in candidates:
            path = os.path.join(comic_dir, fname)
            if os.path.exists(path):
                return path

        exts = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}
        indexed = []
        for fname in os.listdir(comic_dir):
            ext = os.path.splitext(fname)[1].lower()
            if ext in exts:
                digits = "".join(filter(str.isdigit, os.path.splitext(fname)[0]))
                if digits:
                    indexed.append((int(digits), fname))
        indexed.sort()
        for num, fname in indexed:
            if num == page_num:
                return os.path.join(comic_dir, fname)

        # 递归兜底：兼容 TempCache 中 {Aid}/{Pid}/图片 的嵌套结构
        imgs = self._collect_images_recursive(comic_dir)
        if 1 <= page_num <= len(imgs):
            return imgs[page_num - 1]
        # 找不到精确匹配则返回 None，避免错误返回邻页导致 UI 显示错位
        return None

    # ─── 删除 ──────────────────────────────────────────────

    def delete_comic(self, jm_id: int) -> bool:
        try:
            conn = _get_conn()
            conn.execute("DELETE FROM downloaded_comics WHERE jm_id = ?", (jm_id,))
            conn.execute("DELETE FROM comic_categories WHERE comic_jm_id = ?", (jm_id,))
            conn.commit()
            for name in os.listdir(self.downloaded_dir):
                if name.startswith(f"{jm_id}_"):
                    shutil.rmtree(os.path.join(self.downloaded_dir, name))
                    break
            return True
        except Exception as e:
            print(f"删除漫画失败 {jm_id}: {e}")
            return False

    def update_read_progress(self, jm_id: int, page_num: int) -> None:
        conn = _get_conn()
        conn.execute(
            "UPDATE downloaded_comics SET last_read_time = CURRENT_TIMESTAMP, read_progress = ? WHERE jm_id = ?",
            (page_num, jm_id),
        )
        conn.commit()

    def get_cache_size(self) -> int:
        total = 0
        try:
            for dirpath, _, filenames in os.walk(self.downloaded_dir):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    if os.path.isfile(fp):
                        total += os.path.getsize(fp)
        except Exception:
            pass
        return total
