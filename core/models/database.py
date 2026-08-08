# -*- coding: utf-8 -*-
"""
数据库模型 — 所有表的集中定义与初始化。

表清单:
  downloaded_comics — 已下载漫画主表
  categories         — 分类（支持无限层级：parent_id 自引用）
  comic_categories   — 漫画↔分类 多对多关联
  search_history     — 搜索历史
  download_history   — 下载历史（状态追踪）
  reading_history    — 阅读历史
  system_config      — 全局键值配置
"""

import re
import sqlite3
import threading
from typing import Optional, List, Dict

from core.config import DB_FILE

# 每个线程持有一个独立连接，避免 check_same_thread=False 的隐患
_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


# ─── 建表 SQL ──────────────────────────────────────────────

DDL_DOWNLOADED_COMICS = """
CREATE TABLE IF NOT EXISTS downloaded_comics (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    jm_id        INTEGER UNIQUE NOT NULL,
    title        TEXT    NOT NULL,
    author       TEXT,
    tags         TEXT,
    description  TEXT,
    favorites     INTEGER DEFAULT 0,
    pages         INTEGER DEFAULT 0,
    chapter_count INTEGER DEFAULT 0,
    cover_path    TEXT,
    comic_path   TEXT,
    download_time DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_read_time DATETIME,
    read_progress INTEGER DEFAULT 0,
    file_size    INTEGER DEFAULT 0
)
"""

DDL_CATEGORIES = """
CREATE TABLE IF NOT EXISTS categories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    parent_id  INTEGER DEFAULT NULL,
    sort_order INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (parent_id) REFERENCES categories(id) ON DELETE CASCADE
)
"""

DDL_COMIC_CATEGORIES = """
CREATE TABLE IF NOT EXISTS comic_categories (
    comic_jm_id INTEGER NOT NULL,
    category_id INTEGER NOT NULL,
    PRIMARY KEY (comic_jm_id, category_id),
    FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE
)
"""

DDL_SEARCH_HISTORY = """
CREATE TABLE IF NOT EXISTS search_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    search_type    TEXT NOT NULL,
    search_content TEXT NOT NULL,
    results_count  INTEGER DEFAULT 0,
    search_time    DATETIME DEFAULT CURRENT_TIMESTAMP
)
"""

DDL_DOWNLOAD_HISTORY = """
CREATE TABLE IF NOT EXISTS download_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    jm_id           INTEGER NOT NULL,
    title           TEXT NOT NULL,
    download_status TEXT NOT NULL,
    download_time   DATETIME DEFAULT CURRENT_TIMESTAMP,
    complete_time   DATETIME,
    error_message   TEXT
)
"""

DDL_READING_HISTORY = """
CREATE TABLE IF NOT EXISTS reading_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    jm_id       INTEGER NOT NULL,
    page_number INTEGER NOT NULL,
    read_time   DATETIME DEFAULT CURRENT_TIMESTAMP
)
"""

DDL_SYSTEM_CONFIG = """
CREATE TABLE IF NOT EXISTS system_config (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    description TEXT,
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP
)
"""

DDL_BLOCKLIST = """
CREATE TABLE IF NOT EXISTS blocklist (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    block_type TEXT NOT NULL,
    value      TEXT NOT NULL,
    note       TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
"""

DDL_ALIASES = """
CREATE TABLE IF NOT EXISTS aliases (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    type      TEXT NOT NULL,
    alias     TEXT NOT NULL UNIQUE,
    canonical TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
"""

ALL_DDL = [
    DDL_CATEGORIES,          # 先建 categories，因为有外键
    DDL_DOWNLOADED_COMICS,
    DDL_COMIC_CATEGORIES,
    DDL_SEARCH_HISTORY,
    DDL_DOWNLOAD_HISTORY,
    DDL_READING_HISTORY,
    DDL_SYSTEM_CONFIG,
    DDL_BLOCKLIST,
    DDL_ALIASES,
]

DEFAULT_CONFIGS = [
    ("max_concurrent_downloads", "3", "最大并发下载数"),
    ("auto_cleanup_cache", "false", "自动清理缓存"),
    ("cache_size_limit", "104857600", "缓存大小限制(字节)"),
    ("image_quality", "original", "图片质量"),
    ("enable_pdf_generation", "false", "启用PDF生成（已弃用）"),
    ("theme", "light", "界面主题"),
    ("proxy_enabled", "0", "启用代理下载 (0/1)"),
    ("proxy_url", "", "代理地址，如 http://127.0.0.1:7890 或 socks5://127.0.0.1:1080"),
    ("search_result_limit", "80", "搜索结果上限（标签/联合搜索）"),
    ("search_priority", "input", "搜索优先级：input=优先输入的名字 / equal=同义词等同"),
    ("hide_page_chapter", "false", "隐藏漫画卡片上的页数/章数"),
    ("search_tag_limit", "0", "搜索结果卡片最多显示几个标签(0=全部)"),
    ("reader_chapter_toast", "1", "阅读器到达章节边界时是否显示提醒"),
]


# ─── 初始化 ─────────────────────────────────────────────────

def init_database() -> None:
    """创建所有表并写入默认配置。幂等。"""
    conn = _get_conn()
    for ddl in ALL_DDL:
        conn.execute(ddl)

    for key, value, desc in DEFAULT_CONFIGS:
        conn.execute(
            "INSERT OR IGNORE INTO system_config (key, value, description) VALUES (?, ?, ?)",
            (key, value, desc),
        )

    # 修正历史遗留的非法 image_quality 值（早期默认 "85" 不匹配任何选项）
    _valid_quality = ("original", "high", "medium", "low")
    _cur = conn.execute("SELECT value FROM system_config WHERE key='image_quality'").fetchone()
    if _cur and _cur["value"] not in _valid_quality:
        conn.execute(
            "UPDATE system_config SET value='original' WHERE key='image_quality'"
        )

    # 增量迁移：为 downloaded_comics 增加 chapter_count 字段
    try:
        conn.execute("ALTER TABLE downloaded_comics ADD COLUMN chapter_count INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # 已存在

    conn.commit()


# ─── 分类操作 ───────────────────────────────────────────────

def get_all_categories() -> List[Dict]:
    """返回所有分类（平铺列表）。"""
    rows = _get_conn().execute(
        "SELECT id, name, parent_id, sort_order FROM categories ORDER BY sort_order, id"
    ).fetchall()
    return [dict(r) for r in rows]


def get_category_tree() -> List[Dict]:
    """返回递归分类树（children 嵌套），每个节点附带漫画数量。"""
    rows = get_all_categories()
    node_map: Dict[int, Dict] = {}
    roots: List[Dict] = []

    # 一次性查每个分类的漫画数
    conn = _get_conn()
    count_rows = conn.execute(
        "SELECT category_id, COUNT(*) as cnt FROM comic_categories GROUP BY category_id"
    ).fetchall()
    counts = {r["category_id"]: r["cnt"] for r in count_rows}

    for r in rows:
        node = {**r, "children": [], "count": counts.get(r["id"], 0)}
        node_map[r["id"]] = node

    for node in node_map.values():
        pid = node.get("parent_id")
        if pid and pid in node_map:
            node_map[pid]["children"].append(node)
            # 父节点计数 = 自己的 + 所有子节点之和
            node_map[pid]["count"] += node["count"]
        else:
            roots.append(node)
    return roots


def create_category(name: str, parent_id: Optional[int] = None) -> int:
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO categories (name, parent_id) VALUES (?, ?)",
        (name, parent_id),
    )
    conn.commit()
    return cur.lastrowid


def update_category(cat_id: int, name: Optional[str] = None, parent_id: Optional[int] = None) -> bool:
    fields = []
    params = []
    if name is not None:
        fields.append("name = ?")
        params.append(name)
    if parent_id is not None:
        fields.append("parent_id = ?")
        params.append(parent_id)
    if not fields:
        return False
    params.append(cat_id)
    conn = _get_conn()
    conn.execute(f"UPDATE categories SET {', '.join(fields)} WHERE id = ?", params)
    conn.commit()
    return True


def set_category_parent(cat_id: int, parent_id: Optional[int]) -> bool:
    """重定分类父级；parent_id 为 None 表示移到根目录（置 NULL）。"""
    conn = _get_conn()
    conn.execute("UPDATE categories SET parent_id = ? WHERE id = ?", (parent_id, cat_id))
    conn.commit()
    return True


def delete_category(cat_id: int) -> bool:
    conn = _get_conn()
    conn.execute("DELETE FROM categories WHERE id = ?", (cat_id,))
    conn.commit()
    return True


def get_category_descendant_ids(cat_id: int) -> List[int]:
    """返回 cat_id 的所有后代分类 id（不含自身），用于移动时的环检测。"""
    rows = get_all_categories()
    by_parent: Dict[int, List[int]] = {}
    for r in rows:
        pid = r["parent_id"]
        by_parent.setdefault(pid, []).append(r["id"])

    result: List[int] = []
    stack = [cat_id]
    while stack:
        cur = stack.pop()
        for child in by_parent.get(cur, []):
            result.append(child)
            stack.append(child)
    return result


def get_comic_categories(jm_id: int) -> List[int]:
    """返回漫画所属分类的 id 列表。"""
    rows = _get_conn().execute(
        "SELECT category_id FROM comic_categories WHERE comic_jm_id = ?", (jm_id,)
    ).fetchall()
    return [r["category_id"] for r in rows]


def set_comic_categories(jm_id: int, category_ids: List[int]) -> None:
    """全量替换漫画的分类（先删后插）。"""
    conn = _get_conn()
    conn.execute("DELETE FROM comic_categories WHERE comic_jm_id = ?", (jm_id,))
    conn.executemany(
        "INSERT INTO comic_categories (comic_jm_id, category_id) VALUES (?, ?)",
        [(jm_id, cid) for cid in category_ids],
    )
    conn.commit()


# ─── 系统配置 ───────────────────────────────────────────────

def get_system_config(key: str) -> Optional[str]:
    row = _get_conn().execute("SELECT value FROM system_config WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_system_config(key: str, value: str) -> None:
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO system_config (key, value, update_time) VALUES (?, ?, CURRENT_TIMESTAMP)",
        (key, value),
    )
    conn.commit()


def get_image_quality_params() -> tuple:
    """根据 image_quality 设置返回 (quality, subsampling) 供 JPEG 保存使用。

    original -> (100, 0)   最高保真，几乎无损（4:4:4）
    high     -> (92, 'keep')
    medium   -> (82, 'keep')
    low      -> (65, 'keep')
    """
    q = (get_system_config("image_quality") or "original").strip().lower()
    mapping = {
        "original": (100, 0),
        "high": (92, "keep"),
        "medium": (82, "keep"),
        "low": (65, "keep"),
    }
    return mapping.get(q, (100, 0))


# ─── 拉黑 / 别名 ─────────────────────────────────────────────

def normalize_author(author: str) -> str:
    """作者名归一化：去空格、转小写、压缩连续空白，用于精确匹配。"""
    return re.sub(r"\s+", "", (author or "").strip().lower())


# ── 拉黑 ──

def add_block(block_type: str, value: str, note: Optional[str] = None) -> int:
    """新增拉黑项。block_type='author'|'work'|'tag'；作者 value 自动归一化，标签 trim。返回新记录 id。"""
    if block_type not in ("author", "work", "tag"):
        raise ValueError("block_type 必须是 'author'、'work' 或 'tag'")
    if not value or not value.strip():
        raise ValueError("value 不能为空")
    if block_type == "author":
        val = normalize_author(value)
    elif block_type == "tag":
        val = value.strip()
    else:
        val = value.strip()
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO blocklist (block_type, value, note) VALUES (?, ?, ?)",
        (block_type, val, note),
    )
    conn.commit()
    return cur.lastrowid


def remove_block(block_id: int) -> bool:
    conn = _get_conn()
    cur = conn.execute("DELETE FROM blocklist WHERE id = ?", (block_id,))
    conn.commit()
    return cur.rowcount > 0


def remove_block_by_value(block_type: str, value: str) -> bool:
    """按类型+值删除拉黑项（前端书架一键取消用）。"""
    if block_type not in ("author", "work", "tag"):
        return False
    if block_type == "author":
        val = normalize_author(value)
    else:
        val = value.strip()
    conn = _get_conn()
    cur = conn.execute(
        "DELETE FROM blocklist WHERE block_type = ? AND value = ?", (block_type, val)
    )
    conn.commit()
    return cur.rowcount > 0


def get_blocks() -> List[Dict]:
    rows = _get_conn().execute(
        "SELECT id, block_type, value, note, created_at FROM blocklist ORDER BY id DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_blocked_sets() -> Dict[str, set]:
    """返回 {authors: set(normalized), works: set(int), tags: set(str)} 供搜索过滤使用。"""
    authors, works, tags = set(), set(), set()
    for r in get_blocks():
        if r["block_type"] == "author":
            authors.add(r["value"])
        elif r["block_type"] == "work":
            try:
                works.add(int(r["value"]))
            except (ValueError, TypeError):
                pass
        elif r["block_type"] == "tag":
            tags.add(r["value"])
    return {"authors": authors, "works": works, "tags": tags}


def get_local_block_hits(block_type: str, value: str) -> List[Dict]:
    """返回本地已下载库中命中该拉黑条件的作品列表。"""
    conn = _get_conn()
    if block_type == "author":
        val = normalize_author(value)
        rows = conn.execute(
            "SELECT jm_id, title FROM downloaded_comics WHERE lower(replace(author, ' ', '')) = ?",
            (val,),
        ).fetchall()
    elif block_type == "tag":
        val = (value or "").strip()
        rows = conn.execute(
            "SELECT jm_id, title FROM downloaded_comics WHERE tags LIKE ?",
            (f"%{val}%",),
        ).fetchall()
    else:
        try:
            jm_id = int(value.strip())
        except (ValueError, TypeError):
            return []
        rows = conn.execute(
            "SELECT jm_id, title FROM downloaded_comics WHERE jm_id = ?", (jm_id,)
        ).fetchall()
    return [{"jm_id": r["jm_id"], "title": r["title"]} for r in rows]


# ── 别名 / 同义词 ──

def add_alias(type_: str, alias: str, canonical: str) -> int:
    """新增/更新别名映射。alias 唯一；已存在则覆盖 canonical。返回记录 id。"""
    if type_ not in ("tag", "author"):
        raise ValueError("type 必须是 'tag' 或 'author'")
    alias = (alias or "").strip()
    canonical = (canonical or "").strip()
    if not alias or not canonical:
        raise ValueError("alias 和 canonical 均不能为空")
    conn = _get_conn()
    cur = conn.execute(
        "INSERT OR REPLACE INTO aliases (type, alias, canonical) VALUES (?, ?, ?)",
        (type_, alias, canonical),
    )
    conn.commit()
    return cur.lastrowid


def remove_alias(alias_id: int) -> bool:
    conn = _get_conn()
    cur = conn.execute("DELETE FROM aliases WHERE id = ?", (alias_id,))
    conn.commit()
    return cur.rowcount > 0


def get_aliases(type_: Optional[str] = None) -> List[Dict]:
    conn = _get_conn()
    if type_:
        rows = conn.execute(
            "SELECT id, type, alias, canonical FROM aliases WHERE type = ? ORDER BY canonical, alias",
            (type_,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, type, alias, canonical FROM aliases ORDER BY type, canonical, alias"
        ).fetchall()
    return [dict(r) for r in rows]


def get_alias_map(type_: str) -> Dict[str, str]:
    """返回 {alias.lower(): canonical} 供搜索展开 / 展示归一化。"""
    m = {}
    for r in get_aliases(type_):
        m[r["alias"].lower()] = r["canonical"]
    return m


# ─── 历史记录 ───────────────────────────────────────────────

def add_search_history(search_type: str, content: str, count: int = 0) -> None:
    conn = _get_conn()
    conn.execute(
        "INSERT INTO search_history (search_type, search_content, results_count) VALUES (?, ?, ?)",
        (search_type, content, count),
    )
    conn.commit()


def get_search_history_aggregated(limit: int = 200) -> List[Dict]:
    """聚合搜索历史：按 (type, content) 去重，统计频次与最近搜索时间，按频次降序。

    供「自动推荐」提取兴趣种子（常搜关键词 / 作者 / 标签）。
    """
    conn = _get_conn()
    rows = conn.execute(
        """SELECT search_type, search_content,
                  COUNT(*) AS freq,
                  MAX(search_time) AS last_time
           FROM search_history
           GROUP BY search_type, search_content
           ORDER BY freq DESC, last_time DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    return [{
        "search_type": r["search_type"],
        "search_content": r["search_content"],
        "freq": r["freq"],
        "last_time": r["last_time"],
    } for r in rows]


def add_download_history(jm_id: int, title: str, status: str, error_msg: Optional[str] = None) -> None:
    conn = _get_conn()
    if status == "completed":
        conn.execute(
            "INSERT INTO download_history (jm_id, title, download_status, complete_time) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            (jm_id, title, status),
        )
    else:
        conn.execute(
            "INSERT INTO download_history (jm_id, title, download_status, error_message) VALUES (?, ?, ?, ?)",
            (jm_id, title, status, error_msg),
        )
    conn.commit()


def get_download_history(limit: int = 50) -> List[Dict]:
    """返回最近的下载历史记录。"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, jm_id, title, download_status, download_time, complete_time, error_message "
        "FROM download_history ORDER BY download_time DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def delete_download_history(record_id: int) -> bool:
    """按 id 删除单条下载历史记录。"""
    conn = _get_conn()
    cur = conn.execute("DELETE FROM download_history WHERE id = ?", (record_id,))
    conn.commit()
    return cur.rowcount > 0


def clear_download_history() -> int:
    """清空全部下载历史记录，返回删除条数。"""
    conn = _get_conn()
    cur = conn.execute("DELETE FROM download_history")
    conn.commit()
    return cur.rowcount


def add_reading_history(jm_id: int, page_number: int) -> None:
    conn = _get_conn()
    conn.execute("INSERT INTO reading_history (jm_id, page_number) VALUES (?, ?)", (jm_id, page_number))
    conn.commit()


def cleanup_old_records(days: int = 30) -> None:
    conn = _get_conn()
    conn.execute("DELETE FROM search_history WHERE search_time < datetime('now', '-' || ? || ' days')", (days,))
    conn.execute("DELETE FROM reading_history WHERE read_time < datetime('now', '-' || ? || ' days')", (days,))
    conn.commit()
