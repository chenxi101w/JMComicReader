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

import json
import re
import sqlite3
import threading
import time
from typing import Optional, List, Dict, Any, Tuple, Iterable, Set, Union

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

# 漫画关系状态登记表（v2.5.0 从 Android 版复刻，位掩码方案）。
# 主键 = jm 号；本表是「用户↔漫画关系状态」的持久真相源，不随 downloaded_comics 级联删除：
# 删除本地漫画只清 LOCAL 位、行仍保留，故「已删但封面在/可重下」与「曾下载」都能稳定成立。
# status 位掩码（bit0-6 当前用，bit7-31 预留恒为 0）：
#   bit0 LOCAL      在本地（downloaded_comics 有行）
#   bit1 FINISHED   已读完
#   bit2 DOWNLOADED 已下载过（含淘汰/删除；不变量：bit0=1 则 bit2 必=1）
#   bit3 FAVORITE   收藏
#   bit4 BLOCKED    拉黑（作品级）
#   bit5 TAGS_RECORDED 标签已录入标签库
#   bit6 TAGS_CHECKED  实验抓取已检查过
DDL_COMIC_STATUS = """
CREATE TABLE IF NOT EXISTS comic_status (
    book_id      INTEGER PRIMARY KEY,
    status       INTEGER NOT NULL DEFAULT 0,
    title        TEXT    NOT NULL DEFAULT '',
    author       TEXT    NOT NULL DEFAULT '',
    cover_url    TEXT    NOT NULL DEFAULT '',
    tags         TEXT    NOT NULL DEFAULT '',
    view_count   INTEGER NOT NULL DEFAULT 0,
    last_read_at INTEGER NOT NULL DEFAULT 0,
    ext          TEXT    NOT NULL DEFAULT ''
)
"""

# ─── 订阅（subscriptions / sub_updates，从 Android 复刻）────
# subscriptions    作者订阅：收藏作者、检查更新、新作计数与最近新作标题。
# sub_updates      订阅新作记录：检查订阅时发现的新作逐部落库，书架「订阅更新」区块据此展示。

DDL_SUBSCRIPTIONS = """
CREATE TABLE IF NOT EXISTS subscriptions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    author         TEXT    NOT NULL DEFAULT '',
    created_at     INTEGER NOT NULL DEFAULT 0,
    last_checked_at INTEGER NOT NULL DEFAULT 0,
    last_update_at INTEGER NOT NULL DEFAULT 0,
    new_count      INTEGER NOT NULL DEFAULT 0,
    last_title     TEXT    NOT NULL DEFAULT ''
)
"""

DDL_SUB_UPDATES = """
CREATE TABLE IF NOT EXISTS sub_updates (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    author     TEXT    NOT NULL DEFAULT '',
    book_id    INTEGER NOT NULL DEFAULT 0,
    title      TEXT    NOT NULL DEFAULT '',
    cover_url  TEXT    NOT NULL DEFAULT '',
    found_at   INTEGER NOT NULL DEFAULT 0,
    seen       INTEGER NOT NULL DEFAULT 0
)
"""

# ─── 标签库（tag，从 Android TagLibrary 复刻）────────────────
# tag_library       兴趣标签库：搜索结果命中的标签累积（供算法/设置页浏览）
# tag_library_exp   实验标签库：批量抓取 JM 号写入的全量标签字典（独立，可随时清空重建）
# 两表结构一致：主键 tag（小写归一化），count 累计次数，last_seen_at 最近命中时间(ms)。

DDL_TAG_LIBRARY = """
CREATE TABLE IF NOT EXISTS tag_library (
    tag          TEXT    PRIMARY KEY,
    count        INTEGER NOT NULL DEFAULT 0,
    last_seen_at INTEGER NOT NULL DEFAULT 0
)
"""

DDL_TAG_LIBRARY_EXP = """
CREATE TABLE IF NOT EXISTS tag_library_exp (
    tag          TEXT    PRIMARY KEY,
    count        INTEGER NOT NULL DEFAULT 0,
    last_seen_at INTEGER NOT NULL DEFAULT 0
)
"""

# ─── comic_status 位常量（与 Android ComicStatus 语义一致）───
BIT_LOCAL         = 1 << 0   # 1  在本地
BIT_FINISHED      = 1 << 1   # 2  已读完
BIT_DOWNLOADED    = 1 << 2   # 4  已下载过
BIT_FAVORITE      = 1 << 3   # 8  收藏
BIT_BLOCKED       = 1 << 4   # 16 拉黑（作品级）
BIT_TAGS_RECORDED = 1 << 5   # 32 标签已录入标签库
BIT_TAGS_CHECKED  = 1 << 6   # 64 实验抓取已检查过


def has_bit(status: int, bit: int) -> bool:
    return (status & bit) != 0


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
    DDL_COMIC_STATUS,
    DDL_SUBSCRIPTIONS,
    DDL_SUB_UPDATES,
    DDL_TAG_LIBRARY,
    DDL_TAG_LIBRARY_EXP,
]

DEFAULT_CONFIGS = [
    ("max_concurrent_downloads", "3", "最大并发下载数"),
    ("auto_cleanup_cache", "true", "自动清理缓存开关（已内置生效，无需 UI；默认开启）"),
    ("cache_size_limit", "104857600", "缓存大小限制(字节)"),
    ("image_quality", "original", "图片质量"),
    ("enable_pdf_generation", "false", "启用PDF生成（已弃用）"),
    ("theme", "system", "主题：system/light/dark/auto"),
    ("theme_night_start", "22:00", "定时夜间起始时间（仅 theme=auto 生效）"),
    ("theme_night_end", "06:00", "定时夜间结束时间（仅 theme=auto 生效）"),
    ("show_hints", "1", "是否显示所有辅助提示说明小字"),
    ("app_icon", "default", "应用图标：default/alt1/alt2/doubao2/doubao3/doubao4（对齐 Android 外观→应用图标）"),
    ("proxy_enabled", "0", "启用代理下载 (0/1)"),
    ("proxy_url", "", "代理地址，如 http://127.0.0.1:7890 或 socks5://127.0.0.1:1080"),
    ("search_result_limit", "80", "搜索结果上限（标签/联合搜索）"),
    ("search_tag_limit", "0", "搜索结果卡片最多显示几个标签(0=全部)"),
    ("default_sort", "mr", "搜索默认排序：mr=最新/mv=浏览量/mp=收藏量"),
    ("search_bar_behavior", "hide", "搜索后搜索栏：stay=留在顶部/hide=自动隐藏"),
    ("hide_downloaded_in_search", "hide", "搜索里已下载的漫画：off=不处理/hide=隐藏/gray=灰显"),
    ("hide_finished_in_search", "hide", "搜索里已看完的漫画：off=不处理/hide=隐藏/gray=灰显"),
    ("search_apply_blocks", "1", "搜索结果是否应用屏蔽规则（0/1）"),
    ("reader_chapter_toast", "1", "阅读器到达章节边界时是否显示提醒"),
    ("shelf_sort", "download_desc", "书架默认排序：download_desc / download_asc / read_desc / read_asc / favorite_desc / favorite_asc"),
    ("recommend_enabled", "1", "是否启用兴趣推荐（0/1）"),
    ("recommend_count", "20", "主页兴趣推荐最多返回条数"),
    ("recommend_basis", '["keyword","author","tag"]', "兴趣推荐依据集合：常搜索/作者/标签（JSON 数组）"),
    ("recommend_custom", "[]", "兴趣推荐自定义内容：[{type,value}] 数组（JSON）"),
    # ── P2：智能关联 / 喜欢·讨厌·无关 / 标签管理 / 策略算法 ──
    ("smart_association",   "1", "智能关联拓展总开关（0/1）：关闭则退化为纯排序"),
    ("pref_min_hits",       "4", "喜欢红线：标签/名称命中≥N 次才计入画像（≥2）"),
    ("pref_max_tags",      "25", "主喜欢标签上限 N（前 N 个记主喜欢，其余为微喜欢）"),
    ("active_tag_lib", "interest", "标签库管理页激活的数据源：interest=兴趣库 / experimental=实验库"),
    ("hide_irrelevant",     "1", "标签库管理是否隐藏无关标签（0/1）"),
    ("manual_tags",          "", "手动喜欢标签集合（英文逗号分隔的小写归一化字符串）"),
    ("hated_tags",           "", "手动讨厌标签集合（英文逗号分隔的小写归一化字符串）"),
    ("ignored_tags",         "", "用户标记的无关标签（英文逗号分隔，小写，手动增删）"),
    ("search_strategy", "hybrid", "搜索排序算法：classic / hybrid / hybrid_default"),
    ("recommend_strategy", "hybrid", "兴趣推荐策略：hybrid / weighted_only / tags / author"),
    ("tag_count",            "0", "展示用：兴趣库当前 tag 条数（与 tag_library 查询一致）"),
    ("exp_tag_count",        "0", "展示用：实验库当前 tag 条数（与 tag_library exp 查询一致）"),
    ("user_ignored_csv",     "", "展示用：用户手动标记无关 tags 的 CSV（与 ignored_tags 同源，读时同步写入 ignored_tags）"),
]


# ─── 初始化 ─────────────────────────────────────────────────

def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """幂等加列：已存在则静默跳过，绝不 drop 重建。用于增量 schema 迁移。"""
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


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

    # 增量迁移：为 downloaded_comics 增加扩展字段（对齐 Android Book 语义，幂等）
    _ensure_column(conn, "downloaded_comics", "chapter_count", "INTEGER DEFAULT 0")
    _ensure_column(conn, "downloaded_comics", "favorite", "INTEGER DEFAULT 0")
    _ensure_column(conn, "downloaded_comics", "finished", "INTEGER DEFAULT 0")
    _ensure_column(conn, "downloaded_comics", "cached", "INTEGER DEFAULT 0")
    _ensure_column(conn, "downloaded_comics", "view_count", "INTEGER DEFAULT 0")
    _ensure_column(conn, "downloaded_comics", "added_at", "DATETIME")

    conn.commit()

    # v2.13.0 迁移：分类归属由多选收敛为单选（每本漫画保留 sort_order 最小的分类）
    _dropped = migrate_categories_to_single()
    if _dropped:
        print(f"[migrate] 分类归属单选化：清理 {_dropped} 条多余分类关联")


# ─── 分类操作 ───────────────────────────────────────────────

def get_all_categories() -> List[Dict]:
    """返回所有分类（平铺列表）。"""
    rows = _get_conn().execute(
        "SELECT id, name, parent_id, sort_order FROM categories ORDER BY sort_order, id"
    ).fetchall()
    return [dict(r) for r in rows]


def get_category_tree() -> List[Dict]:
    """返回递归分类树（children 嵌套），每个节点附带漫画数量（含所有子孙分类）。"""
    from collections import defaultdict

    rows = get_all_categories()
    node_map: Dict[int, Dict] = {}
    roots: List[Dict] = []
    children_map: Dict[Optional[int], List[int]] = defaultdict(list)

    # 一次性查每个分类的漫画数
    conn = _get_conn()
    count_rows = conn.execute(
        "SELECT category_id, COUNT(*) as cnt FROM comic_categories GROUP BY category_id"
    ).fetchall()
    counts = {r["category_id"]: r["cnt"] for r in count_rows}

    for r in rows:
        node = {**r, "children": [], "count": counts.get(r["id"], 0)}
        node_map[r["id"]] = node
        children_map[r["parent_id"]].append(r["id"])

    # 自底向上递归求和：父节点 count = 直接归属数 + 各子节点累加 count（与 id 顺序无关）
    def _total(cid: int) -> int:
        s = node_map[cid]["count"]
        for ch in children_map.get(cid, []):
            s += _total(ch)
        node_map[cid]["count"] = s
        return s

    for cid in list(node_map.keys()):
        pid = node_map[cid]["parent_id"]
        if pid not in node_map:
            _total(cid)
            roots.append(node_map[cid])

    # 把子节点 dict 挂载到父节点的 children，构建完整树形结构
    for pid, children in children_map.items():
        if pid is not None and pid in node_map:
            for ch in children:
                node_map[pid]["children"].append(node_map[ch])

    # 按原 sort_order / id 排序根节点与子节点，保持 UI 稳定
    def _sort(nodes: List[Dict]) -> None:
        nodes.sort(key=lambda n: (n.get("sort_order", 0), n["id"]))
        for n in nodes:
            _sort(n["children"])

    _sort(roots)
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
    """删除分类。

    v2.13.0 修复：旧实现只把子分类提升到父级，**没有处理 comic_categories 里
    属于该分类的漫画关联**。由于 comic_categories.category_id 带
    ON DELETE CASCADE 且连接开启了 PRAGMA foreign_keys=ON，删分类会把该分类下
    所有漫画的分类归属一并级联清空 —— 且没有任何提示，属于静默丢数据。

    现在语义明确：本函数**仅用于该分类下没有漫画的情况**；若分类非空，应改调
    delete_category_with_reassign() 指定漫画去向。调用方（API 层）负责把关。
    为防误用，这里再兜底一次：如果发现该分类下还有漫画，直接返回 False 拒绝删除。
    """
    conn = _get_conn()
    if get_category_comic_count(cat_id) > 0:
        return False
    return _delete_category_row(conn, cat_id)


def _delete_category_row(conn: sqlite3.Connection, cat_id: int) -> bool:
    """真正执行删除：子分类提升到被删节点的父级，清掉残留关联，再删自身。"""
    row = conn.execute("SELECT parent_id FROM categories WHERE id = ?", (cat_id,)).fetchone()
    parent = row["parent_id"] if row else None
    # 子分类提升为被删节点的父级（避免整棵子树被 CASCADE 带走）
    conn.execute("UPDATE categories SET parent_id = ? WHERE parent_id = ?", (parent, cat_id))
    # 兜底清理残留关联（正常情况下调用方已处理完，这里保证不留孤儿）
    conn.execute("DELETE FROM comic_categories WHERE category_id = ?", (cat_id,))
    conn.execute("DELETE FROM categories WHERE id = ?", (cat_id,))
    conn.commit()
    return True


def get_category_comic_count(cat_id: int) -> int:
    """该分类**直接归属**的漫画数量（不含子分类，子分类是独立分类）。"""
    row = _get_conn().execute(
        "SELECT COUNT(*) AS cnt FROM comic_categories WHERE category_id = ?", (cat_id,)
    ).fetchone()
    return int(row["cnt"]) if row else 0


def delete_category_with_reassign(cat_id: int, target_cat_id: Optional[int] = None) -> bool:
    """删除分类并把它的漫画转移到目标分类。

    target_cat_id:
      - 一个真实分类 id → 漫画改归该分类
      - None           → 漫画移入「未归类」（即删除其分类关联）

    子分类一律提升为被删分类的父级（与 delete_category 一致），不随删除消失。
    与「先删关联再删分类」的旧写法不同，这里**转移在前、删除在后**，因此不会
    留下孤儿记录，也不会静默丢失归属。

    v2.13.0：归属已改为单选，所以是直接改写 category_id，不涉及「保留其他分类」。
    """
    if target_cat_id is not None and target_cat_id == cat_id:
        return False
    conn = _get_conn()
    if not conn.execute("SELECT 1 FROM categories WHERE id = ?", (cat_id,)).fetchone():
        return False

    if target_cat_id is not None:
        # 目标分类必须存在，否则拒绝（避免把漫画指向一个不存在的 id）
        if not conn.execute("SELECT 1 FROM categories WHERE id = ?", (target_cat_id,)).fetchone():
            return False
        # 转移归属。注意 PRIMARY KEY (comic_jm_id, category_id) 下可能出现冲突：
        # 直接 UPDATE 会把该漫画原有的目标分类关联撞成重复键，故先删目标关联再改。
        conn.execute(
            "DELETE FROM comic_categories WHERE category_id = ? AND comic_jm_id IN "
            "(SELECT comic_jm_id FROM comic_categories WHERE category_id = ?)",
            (target_cat_id, cat_id),
        )
        conn.execute(
            "UPDATE comic_categories SET category_id = ? WHERE category_id = ?",
            (target_cat_id, cat_id),
        )
    else:
        # 移入「未归类」= 删除关联
        conn.execute("DELETE FROM comic_categories WHERE category_id = ?", (cat_id,))

    return _delete_category_row(conn, cat_id)


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


def is_category_descendant(cat_id: int, maybe_descendant_id: int) -> bool:
    """判断 maybe_descendant_id 是否为 cat_id 的子孙（前端拖拽预判用）。"""
    return maybe_descendant_id in get_category_descendant_ids(cat_id)


def get_comic_categories(jm_id: int) -> List[int]:
    """返回漫画所属分类的 id 列表。

    v2.13.0 起归属为单选，返回值长度恒为 0 或 1；保留列表形态以兼容既有调用方。
    """
    rows = _get_conn().execute(
        "SELECT category_id FROM comic_categories WHERE comic_jm_id = ?", (jm_id,)
    ).fetchall()
    return [r["category_id"] for r in rows]


def get_comic_category(jm_id: int) -> Optional[int]:
    """返回漫画所属的单个分类 id；未归类返回 None。"""
    row = _get_conn().execute(
        "SELECT category_id FROM comic_categories WHERE comic_jm_id = ? LIMIT 1", (jm_id,)
    ).fetchone()
    return row["category_id"] if row else None


def get_uncategorized_comic_ids() -> Set[int]:
    """已下载但未归入任何分类的漫画 id 集合（「未归类」收件箱视图）。

    对应 Android 的 getDefaultCategoryBookIds()。
    """
    rows = _get_conn().execute(
        "SELECT jm_id FROM downloaded_comics WHERE jm_id NOT IN "
        "(SELECT DISTINCT comic_jm_id FROM comic_categories)"
    ).fetchall()
    return {r["jm_id"] for r in rows}


def get_all_comic_categories() -> Dict[int, List[int]]:
    """一次性返回 {jm_id: [category_id,...]}，供书架批量取分类，避免 N 次查询。"""
    rows = _get_conn().execute(
        "SELECT comic_jm_id, category_id FROM comic_categories"
    ).fetchall()
    out: Dict[int, List[int]] = {}
    for r in rows:
        out.setdefault(r["comic_jm_id"], []).append(r["category_id"])
    return out


def set_comic_categories(jm_id: int, category_ids: List[int]) -> None:
    """全量替换漫画的分类（先删后插）。

    v2.13.0 起归属为单选：只取列表首个有效 id 写入，传空列表即「未归类」。
    保留列表入参是为了不破坏既有调用点。
    """
    conn = _get_conn()
    conn.execute("DELETE FROM comic_categories WHERE comic_jm_id = ?", (jm_id,))
    cids = [c for c in category_ids if c is not None]
    if cids:
        conn.execute(
            "INSERT INTO comic_categories (comic_jm_id, category_id) VALUES (?, ?)",
            (jm_id, cids[0]),
        )
    conn.commit()


def set_comic_category(jm_id: int, category_id: Optional[int]) -> None:
    """设置漫画的单个分类；category_id 为 None 表示移入「未归类」。"""
    set_comic_categories(jm_id, [] if category_id is None else [category_id])


def migrate_categories_to_single() -> int:
    """v2.13.0 数据迁移：把多选归属收敛为单选。

    规则：每本漫画保留一个分类（按 categories.sort_order, id 排序取第一个），
    其余关联删除。未关联的漫画保持未归类。

    幂等：已经是单选的漫画不受影响；重复执行无副作用。
    返回被清理掉的多余关联条数。
    """
    conn = _get_conn()
    rows = conn.execute(
        "SELECT cc.comic_jm_id AS jm, cc.category_id AS cid "
        "FROM comic_categories cc "
        "LEFT JOIN categories c ON c.id = cc.category_id "
        "ORDER BY cc.comic_jm_id, c.sort_order, cc.category_id"
    ).fetchall()

    keep: Dict[int, int] = {}
    drop: List[Tuple[int, int]] = []
    for r in rows:
        jm, cid = r["jm"], r["cid"]
        if jm not in keep:
            keep[jm] = cid
        else:
            drop.append((jm, cid))

    if drop:
        conn.executemany(
            "DELETE FROM comic_categories WHERE comic_jm_id = ? AND category_id = ?", drop
        )
        conn.commit()
    return len(drop)



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


# ─── 漫画关系状态（comic_status 位掩码，从 Android 版复刻）────

def _status_row(row: sqlite3.Row) -> Dict:
    return {
        "book_id": row["book_id"], "status": row["status"],
        "title": row["title"], "author": row["author"],
        "cover_url": row["cover_url"], "tags": row["tags"],
        "view_count": row["view_count"], "last_read_at": row["last_read_at"],
        "ext": row["ext"],
    }


def get_comic_status(book_id: int) -> Optional[Dict]:
    """读取单本关系状态行；不存在返回 None。"""
    row = _get_conn().execute(
        "SELECT * FROM comic_status WHERE book_id = ?", (book_id,)
    ).fetchone()
    return _status_row(row) if row else None


def get_all_comic_statuses() -> List[Dict]:
    rows = _get_conn().execute("SELECT * FROM comic_status").fetchall()
    return [_status_row(r) for r in rows]


def ensure_comic_status(book_id: int) -> None:
    """确保状态行存在；缺失则 upsert 空行。"""
    conn = _get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO comic_status (book_id) VALUES (?)", (book_id,)
    )
    conn.commit()


def upsert_comic_status(book_id: int, *, status: int = 0, title: str = "",
                        author: str = "", cover_url: str = "", tags: str = "",
                        view_count: int = 0, last_read_at: int = 0,
                        ext: str = "", preserve_ext: bool = True) -> None:
    """整行写状态（快照 + 位掩码）。

    preserve_ext=True 时保留已有 ext（仅当未显式传 ext 时），避免覆盖 JSON 兜底字段。
    """
    conn = _get_conn()
    if not ext and preserve_ext:
        existing = conn.execute(
            "SELECT ext FROM comic_status WHERE book_id = ?", (book_id,)
        ).fetchone()
        if existing:
            ext = existing["ext"] if existing["ext"] is not None else ""
    conn.execute(
        """INSERT OR REPLACE INTO comic_status
           (book_id, status, title, author, cover_url, tags, view_count, last_read_at, ext)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (book_id, status, title, author, cover_url, tags, view_count, last_read_at, ext),
    )
    conn.commit()


def set_status_bits(book_id: int, bits: int) -> None:
    """置位（OR）。"""
    conn = _get_conn()
    ensure_comic_status(book_id)
    conn.execute(
        "UPDATE comic_status SET status = status | ? WHERE book_id = ?", (bits, book_id)
    )
    conn.commit()


def clear_status_bits(book_id: int, bits: int) -> None:
    """清位（AND NOT）。"""
    conn = _get_conn()
    conn.execute(
        "UPDATE comic_status SET status = status & ~? WHERE book_id = ?", (bits, book_id)
    )
    conn.commit()


def bump_status_view_count(book_id: int) -> None:
    """查看次数 +1（智能缓存判常看漫画 / 排序用）。"""
    conn = _get_conn()
    ensure_comic_status(book_id)
    conn.execute(
        "UPDATE comic_status SET view_count = view_count + 1 WHERE book_id = ?", (book_id,)
    )
    conn.commit()


def touch_status_read(book_id: int, last_read_at_ms: int) -> None:
    """刷新最近阅读时间（ms 时间戳）。"""
    conn = _get_conn()
    ensure_comic_status(book_id)
    conn.execute(
        "UPDATE comic_status SET last_read_at = ? WHERE book_id = ?",
        (last_read_at_ms, book_id),
    )
    conn.commit()


def delete_comic_status(book_id: int) -> None:
    conn = _get_conn()
    conn.execute("DELETE FROM comic_status WHERE book_id = ?", (book_id,))
    conn.commit()


def clear_all_comic_status() -> int:
    """清空全部关系状态行，返回删除条数（隐私「清空阅读画像」用）。"""
    conn = _get_conn()
    cur = conn.execute("DELETE FROM comic_status")
    conn.commit()
    return cur.rowcount


# ─── 收藏 / 读完 toggle（位掩码 + downloaded_comics 列联动）───

def _sync_downloaded_flag(book_id: int, column: str, value: int) -> None:
    """把收藏/读完列同步到 downloaded_comics（行不存在则静默跳过）。"""
    conn = _get_conn()
    conn.execute(
        f"UPDATE downloaded_comics SET {column} = ? WHERE jm_id = ?", (value, book_id)
    )
    conn.commit()


def toggle_comic_favorite(book_id: int) -> bool:
    """翻转收藏位（bit3 FAVORITE），同步 downloaded_comics.favorite；返回翻转后的收藏态。"""
    st = get_comic_status(book_id)
    now = has_bit(st["status"], BIT_FAVORITE) if st else False
    target = not now
    if target:
        set_status_bits(book_id, BIT_FAVORITE)
    else:
        clear_status_bits(book_id, BIT_FAVORITE)
    _sync_downloaded_flag(book_id, "favorite", 1 if target else 0)
    return target


def set_comic_favorite(book_id: int, fav: bool) -> None:
    if fav:
        set_status_bits(book_id, BIT_FAVORITE)
    else:
        clear_status_bits(book_id, BIT_FAVORITE)
    _sync_downloaded_flag(book_id, "favorite", 1 if fav else 0)


def get_favorite_ids() -> List[int]:
    return [r["book_id"] for r in get_all_comic_statuses() if has_bit(r["status"], BIT_FAVORITE)]


def toggle_comic_finished(book_id: int) -> bool:
    """翻转读完位（bit1 FINISHED），同步 downloaded_comics.finished；返回翻转后的读完态。"""
    st = get_comic_status(book_id)
    now = has_bit(st["status"], BIT_FINISHED) if st else False
    target = not now
    if target:
        set_status_bits(book_id, BIT_FINISHED)
    else:
        clear_status_bits(book_id, BIT_FINISHED)
    _sync_downloaded_flag(book_id, "finished", 1 if target else 0)
    return target


def set_comic_finished(book_id: int, finished: bool) -> None:
    if finished:
        set_status_bits(book_id, BIT_FINISHED)
    else:
        clear_status_bits(book_id, BIT_FINISHED)
    _sync_downloaded_flag(book_id, "finished", 1 if finished else 0)


# ─── 回收站 / 宽限期（从 Android v7.1.3 RecycleManager 复刻）────
# 语义：看完 / 手动删除都**不立即抹掉本地内容**，先给 7 天宽限期，到期才删本地图片
# （保留记录与封面，以后想再看重新下载即可）。
# 标记全部落在 comic_status.ext（JSON），**不改 DDL**（发版铁律：绝不 drop 重建）：
#   recycle_at  宽限期到期时间戳(ms)：看完时写 now+7天；宽限内重读续期
#   trashed     本地内容已删（1）：保留记录/封面，重新下载完成后清除

RECYCLE_RETENTION_DAYS = 7
RECYCLE_DAY_MS = 86_400_000


def _now_ms() -> int:
    return int(time.time() * 1000)


def _ext_read(book_id: int) -> Dict[str, Any]:
    """读 comic_status.ext 的 JSON；空值或坏值一律当空字典（容错）。"""
    row = _get_conn().execute(
        "SELECT ext FROM comic_status WHERE book_id = ?", (book_id,)
    ).fetchone()
    if not row or not row["ext"]:
        return {}
    try:
        d = json.loads(row["ext"])
    except (ValueError, TypeError):
        return {}
    return d if isinstance(d, dict) else {}


def _ext_write(book_id: int, ext: Dict[str, Any]) -> None:
    ensure_comic_status(book_id)
    conn = _get_conn()
    conn.execute(
        "UPDATE comic_status SET ext = ? WHERE book_id = ?",
        (json.dumps(ext, ensure_ascii=False), book_id),
    )
    conn.commit()


def _ext_update(book_id: int, mutate) -> Dict[str, Any]:
    ext = _ext_read(book_id)
    mutate(ext)
    _ext_write(book_id, ext)
    return ext


def remain_days_of(recycle_at: int, trashed: bool) -> int:
    """剩余天数：宽限中 = 距到期还剩几天（**向上取整**，最少 1 天）；已删本地 = 0。

    向上取整是有意的：刚标完看完时差值是 6.9999 天，向下取整会立刻显示「还剩 6 天」，
    看起来像少了一天。向上取整后刚写完显示 7 天，符合直觉。
    """
    if trashed or recycle_at <= 0:
        return 0
    delta = recycle_at - _now_ms()
    if delta <= 0:
        return 0
    return max(1, int((delta + RECYCLE_DAY_MS - 1) // RECYCLE_DAY_MS))


def get_recycle_marks(book_id: int) -> Dict[str, Any]:
    """取回收站标记：{recycle_at, trashed, remain_days}。"""
    ext = _ext_read(book_id)
    at = int(ext.get("recycle_at") or 0)
    trashed = bool(ext.get("trashed"))
    return {"recycle_at": at, "trashed": trashed,
            "remain_days": remain_days_of(at, trashed)}


def mark_finished_recycle(book_id: int) -> None:
    """看完整本：写宽限期 recycle_at = now + 7 天，本地内容保留；顺手清掉旧的 trashed。"""
    def _m(ext: Dict[str, Any]) -> None:
        ext["recycle_at"] = _now_ms() + RECYCLE_RETENTION_DAYS * RECYCLE_DAY_MS
        ext.pop("trashed", None)
    _ext_update(book_id, _m)


def renew_recycle(book_id: int) -> None:
    """宽限期内重新阅读：续期 recycle_at = now + 7 天。

    已删本地（trashed）的不续期 —— 那种情况要等重新下载拾回。
    """
    def _m(ext: Dict[str, Any]) -> None:
        if ext.get("trashed"):
            return
        ext["recycle_at"] = _now_ms() + RECYCLE_RETENTION_DAYS * RECYCLE_DAY_MS
    _ext_update(book_id, _m)


def set_trashed(book_id: int, trashed: bool = True) -> None:
    """标记本地内容已删（保留记录/封面，可重新下载拾回）；传 False 清标记。"""
    def _m(ext: Dict[str, Any]) -> None:
        if trashed:
            ext.pop("recycle_at", None)
            ext["trashed"] = 1
        else:
            ext.pop("trashed", None)
    _ext_update(book_id, _m)


def clear_recycle_marks(book_id: int) -> None:
    """拾回：清 trashed / recycle_at（重新下载完成后调用）。"""
    def _m(ext: Dict[str, Any]) -> None:
        ext.pop("trashed", None)
        ext.pop("recycle_at", None)
    _ext_update(book_id, _m)


def list_recycle_items() -> List[Dict[str, Any]]:
    """回收站列表：宽限中 + 已删本地两类；宽限中优先，同态按到期时间倒序。"""
    items: List[Dict[str, Any]] = []
    for st in get_all_comic_statuses():
        marks = get_recycle_marks(st["book_id"])
        if marks["recycle_at"] <= 0 and not marks["trashed"]:
            continue
        row = dict(st)
        row.update(marks)
        items.append(row)
    items.sort(key=lambda x: (1 if x["trashed"] else 0, -x["recycle_at"]))
    return items


def get_expired_recycle_ids(now_ms: Optional[int] = None) -> List[int]:
    """宽限期已过期且尚未删本地的 book_id（供扫描时删本地内容）。"""
    now = now_ms if now_ms is not None else _now_ms()
    out: List[int] = []
    for st in get_all_comic_statuses():
        marks = get_recycle_marks(st["book_id"])
        if marks["recycle_at"] > 0 and not marks["trashed"] and marks["recycle_at"] < now:
            out.append(st["book_id"])
    return out


# ─── 标签库（tag_library / tag_library_exp，从 Android 复刻）───

def bump_tag(tag: str, ts_ms: Optional[int] = None, *, exp: bool = False) -> None:
    """标签命中 +1：首次插入 count=1，已有则 count+1 并刷新 last_seen_at。

    与 Android DAO 语义一致：搜索命中某标签即调用一次（每本漫画每个标签各 +1）。
    exp=True 写入实验库 tag_library_exp，否则写入兴趣库 tag_library。
    """
    table = "tag_library_exp" if exp else "tag_library"
    if ts_ms is None:
        import time
        ts_ms = int(time.time() * 1000)
    conn = _get_conn()
    conn.execute(
        f"""INSERT INTO {table} (tag, count, last_seen_at) VALUES (?, 1, ?)
            ON CONFLICT(tag) DO UPDATE SET
              count = count + 1,
              last_seen_at = excluded.last_seen_at""",
        (tag, ts_ms),
    )
    conn.commit()


def get_tag_library(*, exp: bool = False) -> List[Dict]:
    """标签库快照，按 count 降序、次级按最近命中时间降序。"""
    table = "tag_library_exp" if exp else "tag_library"
    rows = _get_conn().execute(
        f"SELECT tag, count, last_seen_at FROM {table} "
        "ORDER BY count DESC, last_seen_at DESC"
    ).fetchall()
    return [{"tag": r["tag"], "count": r["count"], "last_seen_at": r["last_seen_at"]}
            for r in rows]


def get_tag_library_count(*, exp: bool = False) -> int:
    """标签库标签总数（管理页展示用）。"""
    table = "tag_library_exp" if exp else "tag_library"
    row = _get_conn().execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
    return row["n"] if row else 0


def clear_tag_library(*, exp: bool = False) -> int:
    """清空标签库，返回删除条数（设置页「清空标签库」/ 实验库重建用）。"""
    table = "tag_library_exp" if exp else "tag_library"
    conn = _get_conn()
    cur = conn.execute(f"DELETE FROM {table}")
    conn.commit()
    return cur.rowcount


# ─── P2：喜欢·讨厌·无关 标签池 + 智能关联 + 汇总统计 ──────

# 结构性无关标签正则（与 Android BookLibrary.kt 语义一致，忽略大小写）
IRRELEVANT_TAG_REGEX = re.compile(r"^[cv]\d+$", re.IGNORECASE)

# 默认无关标签固定种子（来自旧硬编码清单，常见"噪声"标签：语言/色深/尺寸类）
DEFAULT_IRRELEVANT_TAGS: frozenset = frozenset({
    "中文", "chinese", "全彩", "color", "colour", "colored",
    "单行本", "single-page", "单页", "短篇", "合集", "anthology",
    "汉化", "漢化", "chinese-translated", "无修正", "uncensored",
    "decensored",
})

# 默认样本标签（兴趣库重置为默认 / 新用户初始化种子用）
DEFAULT_SAMPLE_TAGS: Tuple[str, ...] = (
    # 风格
    "纯爱", "ntr", "ahegao", "tsundere", "yandere", "stockings",
    "丝袜", "巨乳", "贫乳", "御姐", "萝莉", "人妻", "教师", "学生",
    "女仆", "护士", "巫女", "兔女郎", "制服", "运动服",
    # 场景/题材
    "催眠", "调教", "后宫", "奇幻", "异世界", "校园", "职场",
    "奇幻漫画", "汉化版", "同人", "原创",
    # 语言/区域类(噪声)故意不加入——已被 DEFAULT_IRRELEVANT_TAGS 剔除
)


def parse_csv_tags(raw: Optional[str]) -> List[str]:
    """CSV → list（小写归一化 / 去空 / 保序去重）；公开导出别名。"""
    return _split_csv_list(raw)


def serialize_csv_tags(tags: Iterable[str]) -> str:
    """list/set/CSV 字符串 → 干净 CSV；公开导出别名。"""
    return _join_csv_list(tags)


def save_user_ignored_tags(tags: Iterable[str]) -> int:
    """写回用户手动无关集合，返回实际写入条数（与 get_user_ignored_tags 配对）。"""
    clean = _split_csv_list(",".join(list(tags)))
    set_system_config("ignored_tags", ",".join(clean))
    set_system_config("user_ignored_csv", ",".join(clean))
    return len(clean)


def add_user_ignored_tags(tags: Iterable[str]) -> int:
    """合并一批新标签到用户无关集，返回新增条数。"""
    cur = get_user_ignored_tags()
    orig_size = len(cur)
    for raw in tags:
        t = (raw or "").strip().lower()
        if t:
            cur.add(t)
    save_user_ignored_tags(sorted(cur))
    return len(cur) - orig_size


def _split_csv_list(raw: Optional[str]) -> List[str]:
    """CSV 小写归一化 + 去空 + 保序去重（manual/hated/ignored 读取用）。"""
    if not raw:
        return []
    seen = set()
    out = []
    for t in raw.split(","):
        tag = t.strip().lower()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
    return out


def _join_csv_list(tags: Iterable[str]) -> str:
    """写回 CSV：先过一遍 _split_csv_list 同语义过滤，再拼接。"""
    return ",".join(_split_csv_list(",".join(tags) if isinstance(tags, (list, tuple, set)) else str(tags)))


# ── 手动喜欢 / 手动讨厌 / 手动无关 读写 ──────────────────────

def get_manual_tags() -> List[str]:
    """返回用户手动喜欢标签（按 config 顺序）。"""
    return _split_csv_list(get_system_config("manual_tags"))


def save_manual_tags(tags: Iterable[str]) -> int:
    """写回手动喜欢标签（小写去重，返回实际写入条数）。"""
    clean = _split_csv_list(",".join(tags))
    set_system_config("manual_tags", ",".join(clean))
    return len(clean)


def get_hated_tags() -> List[str]:
    """返回用户手动讨厌标签（推荐/搜索降权或排除对象）。"""
    return _split_csv_list(get_system_config("hated_tags"))


def save_hated_tags(tags: Iterable[str]) -> int:
    """写回手动讨厌标签，返回实际写入条数。"""
    clean = _split_csv_list(",".join(tags))
    set_system_config("hated_tags", ",".join(clean))
    return len(clean)


def get_user_ignored_tags() -> Set[str]:
    """仅用户自定义的 ignored_tags（不含结构正则/默认种子）。"""
    return set(_split_csv_list(get_system_config("ignored_tags")))


def get_ignored_tags_merged() -> Set[str]:
    """合并三源：结构正则（当前无实体，匹配时动态算）+ 默认种子 + 用户 ignored_tags。"""
    merged = set(DEFAULT_IRRELEVANT_TAGS)
    merged.update(get_user_ignored_tags())
    return merged


def is_irrelevant_tag(tag: str) -> bool:
    r"""无关标签判定：结构性正则 ^[cv]\d+$ 或 默认种子 或 用户手动 ignored_tags。"""
    if tag is None:
        return False
    t = tag.strip().lower()
    if not t:
        return False
    if IRRELEVANT_TAG_REGEX.match(t):
        return True
    return t in DEFAULT_IRRELEVANT_TAGS or t in get_user_ignored_tags()


def toggle_ignored_tag(tag: str) -> bool:
    """切换某标签的「用户手动无关」状态；返回 True 表示被加入，False 表示被移除。"""
    t = tag.strip().lower()
    if not t:
        return False
    user = get_user_ignored_tags()
    added = True
    if t in user:
        user.discard(t)
        added = False
    else:
        user.add(t)
    set_system_config("ignored_tags", ",".join(sorted(user)))
    return added


# ── 三态 cycle（normal → like → hate → normal）+ 对立互剔 ──────

def cycle_tag_opinion(tag: str) -> str:
    """三态循环切换，对立互剔；返回切换后的新状态（'like'/'hate'/'normal'）。"""
    t = tag.strip().lower()
    if not t:
        return "normal"
    manual = set(get_manual_tags())
    hated = set(get_hated_tags())
    if t in manual:
        # like → hate（对立互剔：从 manual 移除，加入 hated）
        manual.discard(t)
        hated.add(t)
        save_manual_tags(list(manual))
        save_hated_tags(list(hated))
        return "hate"
    if t in hated:
        # hate → normal（从 hated 移除，两边都不再持有）
        hated.discard(t)
        save_hated_tags(list(hated))
        return "normal"
    # normal → like（加入 manual，同时确保不在 hated）
    hated.discard(t)
    manual.add(t)
    save_manual_tags(list(manual))
    save_hated_tags(list(hated))
    return "like"


# cycle_tag_opinion 外部别名为 cycle_tag（与 VM API 同名）
def cycle_tag(tag: str) -> str:
    return cycle_tag_opinion(tag)


def reset_tag_preferences() -> Tuple[int, int]:
    """清空手动喜欢与手动讨厌（≠清空兴趣库），返回被删除的 manual/hated 条数。"""
    manual = get_manual_tags()
    hated = get_hated_tags()
    n_manual = len(manual)
    n_hated = len(hated)
    set_system_config("manual_tags", "")
    set_system_config("hated_tags", "")
    return n_manual, n_hated


def add_tag_manually(tag: str, opinion: str = "normal") -> Dict[str, Any]:
    """手动添加标签：bump 进兴趣库；再按 opinion 写状态。返回字典。"""
    t = tag.strip().lower()
    if not t:
        return {"bumped": False, "opinion_applied": False, "tag": None}
    try:
        bump_tag(t, exp=False)
    except Exception:
        pass  # 兴趣库写失败不阻塞观点写入
    bumped = True
    opinion_applied = False
    if opinion == "like":
        manual = set(get_manual_tags())
        hated = set(get_hated_tags())
        hated.discard(t)
        manual.add(t)
        save_manual_tags(list(manual))
        save_hated_tags(list(hated))
        opinion_applied = True
    elif opinion == "hate":
        manual = set(get_manual_tags())
        hated = set(get_hated_tags())
        manual.discard(t)
        hated.add(t)
        save_hated_tags(list(hated))
        save_manual_tags(list(manual))
        opinion_applied = True
    elif opinion == "ignored":
        toggle_ignored_tag(t) if not is_irrelevant_tag(t) else None
        # 若原本不在用户集里，toggle_ignored_tag 会加入 → applied=True；若已在 → 仍 True
        opinion_applied = True
    return {"bumped": bumped, "opinion_applied": opinion_applied, "tag": t}


# ── 汇总统计：收藏/拉黑漫画的标签出现次数 ──────────────────────

_TAG_SPLIT_RE = re.compile(r"[\s,，、;；/\\|]+")


def _split_tag_field(raw: Optional[str]) -> List[str]:
    """tags 文本列分词，支持中英文逗号、斜杠、分号等常见分隔。"""
    if not raw:
        return []
    return [t.strip().lower() for t in _TAG_SPLIT_RE.split(str(raw)) if t.strip()]


def load_tag_stats(kind: str = "favorite", limit: int = 80) -> List[Tuple[str, int]]:
    """
    统计本地书架中 FAVORITE/BLOCKED 位掩码漫画的标签频次，
    自动剔除无关标签；按 count 降序返回 top N。
    """
    from collections import Counter
    conn = _get_conn()
    # 位掩码常量（与 comic_status 区 BIT_FAVORITE=bit3 / BIT_BLOCKED=bit4 对齐）
    if kind == "favorite":
        BIT = 1 << 3
    elif kind == "blacklist":
        BIT = 1 << 4
    else:
        return []
    try:
        rows = conn.execute(
            f"""
            SELECT dc.tags
            FROM comic_status cs
            LEFT JOIN downloaded_comics dc ON cs.book_id = dc.jm_id
            WHERE (cs.status & ?) != 0
              AND dc.tags IS NOT NULL
              AND dc.tags != ''
            """,
            (BIT,),
        ).fetchall()
    except Exception:
        # downloaded_comics 没 tags 列或表变更 → 空降级
        return []
    counter: Counter = Counter()
    for r in rows:
        for t in _split_tag_field(r["tags"]):
            if is_irrelevant_tag(t):
                continue
            counter[t] += 1
    return counter.most_common(limit)


# ── 批量写入 opinions / 追加入库 ────────────────────────────────

def bulk_tag_opinions(tags: Union[str, Iterable[str]], op: str) -> Dict[str, Any]:
    """
    批量写入标签到指定池。

    Args:
        tags: 英文逗号分隔的 CSV 字符串，或 list/set/tuple；每项空值跳过。
        op: 操作枚举（全部小写）
            - 'like'   → 写入手动喜欢（对立互剔）
            - 'hate'   → 写入手动讨厌（对立互剔）
            - 'ignore' → 写入用户手动无关（已存在=幂等跳过）
            - 'add_to_library' → 仅 bump 进标签库（不写入三池）
            - 'clear_like' / 'clear_hate' / 'clear_ignore' → 清空指定池
    Returns:
        {'added': int, 'skipped': int, 'pool': str, 'tags': list[str]}
    """
    if isinstance(tags, str):
        items = _split_csv_list(tags)
    else:
        items = _split_csv_list(",".join([str(t) for t in tags]) if not isinstance(tags, str) else tags)

    op = (op or "").lower().strip()

    if op in {"clear_like", "clear_hate", "clear_ignore"}:
        if op == "clear_like":
            old = get_manual_tags(); set_system_config("manual_tags", "")
            return {"added": 0, "skipped": 0, "cleared": len(old), "pool": "like"}
        if op == "clear_hate":
            old = get_hated_tags(); set_system_config("hated_tags", "")
            return {"added": 0, "skipped": 0, "cleared": len(old), "pool": "hate"}
        old = list(get_user_ignored_tags()); save_user_ignored_tags([])
        return {"added": 0, "skipped": 0, "cleared": len(old), "pool": "ignore"}

    if op not in {"like", "hate", "ignore", "add_to_library"}:
        raise ValueError(f"不支持的批量操作 op: {op}")

    added = 0; skipped = 0
    if op == "like":
        manual = set(get_manual_tags()); hated = set(get_hated_tags())
        for t in items:
            if t in manual: skipped += 1; continue
            hated.discard(t); manual.add(t); added += 1
        save_manual_tags(list(manual)); save_hated_tags(list(hated))
    elif op == "hate":
        manual = set(get_manual_tags()); hated = set(get_hated_tags())
        for t in items:
            if t in hated: skipped += 1; continue
            manual.discard(t); hated.add(t); added += 1
        save_manual_tags(list(manual)); save_hated_tags(list(hated))
    elif op == "ignore":
        before = get_user_ignored_tags(); cur = set(before)
        for t in items:
            if t in cur: skipped += 1; continue
            cur.add(t); added += 1
        save_user_ignored_tags(sorted(cur))
    elif op == "add_to_library":
        # 默认只追加入兴趣库（count=1 保证存在即可），返回新增条数
        existing = set()
        try:
            conn = _get_conn()
            rows = conn.execute("SELECT tag FROM tag_library").fetchall()
            existing = {r["tag"] for r in rows}
        except Exception:
            pass
        for t in items:
            if t in existing: skipped += 1; continue
            try: bump_tag(t, exp=False); existing.add(t); added += 1
            except Exception: skipped += 1

    return {"added": added, "skipped": skipped, "pool": op, "tags": items}


# ── 标签库管理：带过滤/搜索/分页的查询 ─────────────────────────

def query_tag_library_paginated(
    library: str = "interest",
    mode: str = "all",
    search: str = "",
    hide_irrelevant: bool = True,
    page: int = 1,
    page_size: int = 200,
) -> Dict[str, Any]:
    """
    标签库管理页聚合查询，返回结构与 JS initTagsManagePage 对齐。

    Returns:
        {
          items: list[{tag, count}],   # 本页数据
          total: int,                  # 过滤后总条数（mode/search/hide_irrel 之后）
          total_by_mode: {all, like, hate, irrelevant},  # 四态在整个 library 的分布
          tag_count: int,              # 整个 interest 库条数
          exp_tag_count: int,          # 整个 experimental 库条数
        }
    """
    library = library.lower() if library else "interest"
    exp = (library == "experimental")
    table = "tag_library_exp" if exp else "tag_library"
    try:
        page = max(1, int(page))
    except Exception:
        page = 1
    try:
        page_size = max(1, min(1000, int(page_size)))
    except Exception:
        page_size = 200
    mode = (mode or "all").lower()
    if mode not in {"all", "like", "hate", "irrelevant"}:
        mode = "all"
    q = (search or "").strip().lower()

    conn = _get_conn()

    # 全局库计数（永远用实际库，不是过滤后结果）
    try:
        interest_total = conn.execute("SELECT COUNT(*) n FROM tag_library").fetchone()["n"]
    except Exception:
        interest_total = 0
    try:
        exp_total = conn.execute("SELECT COUNT(*) n FROM tag_library_exp").fetchone()["n"]
    except Exception:
        exp_total = 0

    # 全量读库（2~3 万条以内性能足够；表大可后续加 SQL WHERE 优化）
    try:
        rows = conn.execute(f"SELECT tag, count FROM {table} ORDER BY count DESC, tag ASC").fetchall()
    except Exception:
        rows = []

    manual = set(get_manual_tags())
    hated = set(get_hated_tags())

    def _state(tag: str) -> str:
        t = tag.lower()
        if t in manual: return "like"
        if t in hated: return "hate"
        if is_irrelevant_tag(t): return "irrelevant"
        return "normal"

    # total_by_mode（全量库在四态上的分布，用来渲染 tabs badge）
    by_mode = {"all": len(rows), "like": 0, "hate": 0, "irrelevant": 0}
    for r in rows:
        s = _state(r["tag"])
        if s == "like": by_mode["like"] += 1
        elif s == "hate": by_mode["hate"] += 1
        elif s == "irrelevant": by_mode["irrelevant"] += 1

    # 应用过滤：mode → hide_irrelevant → search
    filtered = []
    for r in rows:
        st = _state(r["tag"])
        if mode == "like" and st != "like": continue
        if mode == "hate" and st != "hate": continue
        if mode == "irrelevant" and st != "irrelevant": continue
        if hide_irrelevant and st == "irrelevant": continue
        if q and q not in r["tag"].lower(): continue
        filtered.append((r["tag"], int(r["count"]) if isinstance(r, sqlite3.Row) else int(r[1] if isinstance(r, tuple) else r["count"])))

    total = len(filtered)
    start = (page - 1) * page_size
    end = start + page_size
    page_items = [{"tag": t, "count": c} for (t, c) in filtered[start:end]]

    return {
        "items": page_items,
        "total": total,
        "total_by_mode": by_mode,
        "tag_count": interest_total,
        "exp_tag_count": exp_total,
    }


# ── 兴趣库重置为默认样本（返回：写入的条目数 + 计数） ──────────

def reset_interest_to_default_samples() -> int:
    """
    清空 interest 库，并把 DEFAULT_SAMPLE_TAGS 以 count=1 逐条插入 tag_library。
    再写入 tag_count/exp_tag_count 到 system_config（保证设置页即时展示）。
    返回实际写入条数。
    """
    clear_tag_library(exp=False)
    conn = _get_conn()
    inserted = 0
    for raw in DEFAULT_SAMPLE_TAGS:
        t = (raw or "").strip().lower()
        if not t: continue
        try:
            conn.execute(
                "INSERT INTO tag_library (tag, count, last_seen_at) VALUES (?, 1, CURRENT_TIMESTAMP) "
                "ON CONFLICT(tag) DO NOTHING",
                (t,),
            )
            inserted += 1
        except sqlite3.IntegrityError:
            continue
        except Exception:
            # 若表上无唯一索引或没有 last_seen_at 列 → 降级普通 INSERT IGNORE
            try:
                conn.execute("INSERT OR IGNORE INTO tag_library (tag, count) VALUES (?, 1)", (t,))
                inserted += 1
            except Exception:
                continue
    conn.commit()
    # 同步计数到 system_config 便于展示
    try:
        tcnt = conn.execute("SELECT COUNT(*) n FROM tag_library").fetchone()["n"]
        ecnt = conn.execute("SELECT COUNT(*) n FROM tag_library_exp").fetchone()["n"]
    except Exception:
        tcnt = inserted; ecnt = 0
    set_system_config("tag_count", str(tcnt))
    set_system_config("exp_tag_count", str(ecnt))
    return inserted


# ─── 订阅（subscriptions / sub_updates，从 Android 复刻）───

def _sub_row(row: sqlite3.Row) -> Dict:
    return {
        "id": row["id"], "author": row["author"],
        "created_at": row["created_at"], "last_checked_at": row["last_checked_at"],
        "last_update_at": row["last_update_at"],
        "new_count": row["new_count"], "last_title": row["last_title"],
    }


def get_subscriptions() -> List[Dict]:
    """全部作者订阅，按订阅时间倒序。"""
    rows = _get_conn().execute(
        "SELECT * FROM subscriptions ORDER BY created_at DESC"
    ).fetchall()
    return [_sub_row(r) for r in rows]


def find_subscription_by_author(author: str) -> Optional[Dict]:
    """按作者名精确查找订阅；不存在返回 None。"""
    row = _get_conn().execute(
        "SELECT * FROM subscriptions WHERE author = ? LIMIT 1", (author,)
    ).fetchone()
    return _sub_row(row) if row else None


def upsert_subscription(author: str, created_at: Optional[int] = None) -> int:
    """订阅作者（REPLACE，幂等）。返回 id。"""
    conn = _get_conn()
    if created_at is None:
        import time
        created_at = int(time.time() * 1000)
    conn.execute(
        """INSERT OR REPLACE INTO subscriptions
           (author, created_at) VALUES (?, ?)""",
        (author, created_at),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM subscriptions WHERE author = ?", (author,)
    ).fetchone()
    return row["id"] if row else 0


def delete_subscription(sub_id: int) -> None:
    """按 id 删除订阅。"""
    conn = _get_conn()
    conn.execute("DELETE FROM subscriptions WHERE id = ?", (sub_id,))
    conn.commit()


def note_subscription_checked(author: str, ts_ms: int) -> None:
    """订阅检查完毕：仅刷新 last_checked_at。"""
    conn = _get_conn()
    conn.execute(
        "UPDATE subscriptions SET last_checked_at = ? WHERE author = ?",
        (ts_ms, author),
    )
    conn.commit()


def note_subscription_update(author: str, ts_ms: int, delta: int, title: str) -> None:
    """订阅发现新作：刷新检查/更新时间，并累加新作计数、记录最近新作标题。"""
    conn = _get_conn()
    conn.execute(
        "UPDATE subscriptions SET last_checked_at = ?, last_update_at = ?, "
        "new_count = new_count + ?, last_title = ? WHERE author = ?",
        (ts_ms, ts_ms, delta, title, author),
    )
    conn.commit()


def clear_subscription_new_count(sub_id: int) -> None:
    """将某订阅的新作计数清零（用户已查看）。"""
    conn = _get_conn()
    conn.execute("UPDATE subscriptions SET new_count = 0 WHERE id = ?", (sub_id,))
    conn.commit()


def clear_all_subscriptions() -> int:
    conn = _get_conn()
    cur = conn.execute("DELETE FROM subscriptions")
    conn.commit()
    return cur.rowcount


# ── 订阅新作记录（sub_updates）──────────────────────────────

def _sub_update_row(row: sqlite3.Row) -> Dict:
    return {
        "id": row["id"], "author": row["author"], "book_id": row["book_id"],
        "title": row["title"], "cover_url": row["cover_url"],
        "found_at": row["found_at"], "seen": row["seen"],
    }


def get_sub_updates(*, unseen_only: bool = False) -> List[Dict]:
    """订阅新作记录，按发现时间倒序。unseen_only=True 仅取未查看。"""
    sql = "SELECT * FROM sub_updates"
    if unseen_only:
        sql += " WHERE seen = 0"
    sql += " ORDER BY found_at DESC"
    rows = _get_conn().execute(sql).fetchall()
    return [_sub_update_row(r) for r in rows]


def get_sub_updates_by_author(author: str) -> List[Dict]:
    rows = _get_conn().execute(
        "SELECT * FROM sub_updates WHERE author = ? ORDER BY found_at DESC", (author,)
    ).fetchall()
    return [_sub_update_row(r) for r in rows]


def insert_sub_update(author: str, book_id: int = 0, title: str = "",
                      cover_url: str = "", found_at: Optional[int] = None, *,
                      ignore_duplicate: bool = True) -> None:
    """记录一条新作（同 book_id 已存在则忽略，防重复）。"""
    conn = _get_conn()
    if found_at is None:
        import time
        found_at = int(time.time() * 1000)
    if ignore_duplicate:
        conn.execute(
            "INSERT OR IGNORE INTO sub_updates "
            "(author, book_id, title, cover_url, found_at) VALUES (?, ?, ?, ?, ?)",
            (author, book_id, title, cover_url, found_at),
        )
    else:
        conn.execute(
            "INSERT INTO sub_updates "
            "(author, book_id, title, cover_url, found_at) VALUES (?, ?, ?, ?, ?)",
            (author, book_id, title, cover_url, found_at),
        )
    conn.commit()


def mark_sub_update_seen(book_id: int) -> None:
    """按 book_id 标记为已查看。"""
    conn = _get_conn()
    conn.execute("UPDATE sub_updates SET seen = 1 WHERE book_id = ?", (book_id,))
    conn.commit()


def mark_all_sub_updates_seen() -> None:
    conn = _get_conn()
    conn.execute("UPDATE sub_updates SET seen = 1")
    conn.commit()


def delete_sub_updates_by_author(author: str) -> None:
    conn = _get_conn()
    conn.execute("DELETE FROM sub_updates WHERE author = ?", (author,))
    conn.commit()


def clear_all_sub_updates() -> int:
    conn = _get_conn()
    cur = conn.execute("DELETE FROM sub_updates")
    conn.commit()
    return cur.rowcount
