# -*- coding: utf-8 -*-
"""
JMComicReader — 后端主应用。
"""

import os
import json
import shutil
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import List, Dict, Optional, Any, Tuple

from flask import Flask, render_template, jsonify, request, send_file, redirect
from flask_cors import CORS

from core.config import (
    TEMPLATE_DIR, STATIC_DIR, APP_VERSION,
    TEMP_CACHE_DIR, DOWNLOAD_DIR, BASE_DIR, DATA_DIR, DB_FILE,
)
__APP_VERSION__ = APP_VERSION
from core.models.database import (
    init_database, get_system_config,
    get_all_categories, get_category_tree,
    create_category, update_category, delete_category,
    delete_category_with_reassign, get_category_comic_count,
    get_category_descendant_ids,
    get_comic_categories, get_comic_category, get_uncategorized_comic_ids,
    set_comic_categories, set_comic_category, set_category_parent,
    add_search_history, add_download_history, get_download_history,
    delete_download_history, clear_download_history,
    add_block, remove_block, remove_block_by_value, get_blocks, get_blocked_sets, get_local_block_hits,
    add_alias, remove_alias, get_aliases, normalize_author,
    get_search_history_aggregated, cleanup_old_records,
    # v2.5 数据层新增（对齐 Android）
    BIT_FAVORITE, BIT_FINISHED, BIT_LOCAL, BIT_BLOCKED, BIT_DOWNLOADED,
    get_comic_status, set_status_bits, clear_status_bits,
    bump_status_view_count, touch_status_read,
    upsert_comic_status, delete_comic_status, clear_all_comic_status,
    toggle_comic_favorite, set_comic_favorite, get_favorite_ids,
    toggle_comic_finished, set_comic_finished,
    has_bit,
    # 回收站 / 宽限期（对齐 Android v7.1.3 RecycleManager）
    RECYCLE_RETENTION_DAYS,
    get_recycle_marks, mark_finished_recycle, renew_recycle,
    set_trashed, clear_recycle_marks, list_recycle_items, get_expired_recycle_ids,
    # 订阅
    get_subscriptions, find_subscription_by_author,
    upsert_subscription, delete_subscription,
    note_subscription_checked, note_subscription_update, clear_subscription_new_count,
    get_sub_updates, get_sub_updates_by_author,
    insert_sub_update, mark_sub_update_seen, mark_all_sub_updates_seen,
    delete_sub_updates_by_author, clear_all_sub_updates,
    # 标签库
    get_tag_library, get_tag_library_count, clear_tag_library,
    bump_tag,
    # 标签库 P2：池 / 三态 / CSV / 批量 / 管理查询 / 重置样本 / 汇总统计
    get_manual_tags, save_manual_tags, get_hated_tags, save_hated_tags,
    get_user_ignored_tags, save_user_ignored_tags, add_user_ignored_tags,
    get_ignored_tags_merged, is_irrelevant_tag,
    parse_csv_tags, serialize_csv_tags,
    toggle_ignored_tag, cycle_tag_opinion, reset_tag_preferences,
    add_tag_manually, bulk_tag_opinions,
    query_tag_library_paginated, reset_interest_to_default_samples,
    load_tag_stats,
)
from core.services.filter_service import (
    postprocess_results, expand_tags, expand_author, suggest_aliases,
)

# ─── Flask 初始化 ──────────────────────────────────────────

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)
# 桌面应用仅通过 pywebview 同源访问，收紧 CORS 到 localhost，避免任意网页跨域调用本地 API
CORS(app, resources={r"/*": {"origins": r"https?://(127\.0\.0\.1|localhost)(:\d+)?$"}})


@app.after_request
def _no_store_dynamic_pages(resp):
    """动态页面/接口一律禁止缓存。

    桌面端用 pywebview(持久化 profile) + 固定端口加载页面，WebView2 会对
    无缓存头的响应做启发式缓存——升级后旧 HTML（含旧静态资源版本号）会被命中，
    造成「文件已更新但界面仍是老 UI」。这里对 text/html 与 JSON API 统一发
    Cache-Control: no-store，确保每次启动、每次版本升级都能立即拿到最新页面。
    静态资源（/static/...）仍走 ?v= 版本号控制，不受影响。
    """
    resp.headers.setdefault("Cache-Control", "no-store, no-cache, must-revalidate")
    resp.headers.setdefault("Pragma", "no-cache")
    resp.headers.setdefault("Expires", "0")
    return resp


@app.context_processor
def inject_app_version():
    return {"app_version": APP_VERSION}


# ─── 服务初始化 ────────────────────────────────────────────

from core.services.jm_crawler import JMCrawler
from core.services.download_manager import DownloadManager
from core.services.comic_manager import ComicManager

init_database()
jm_crawler = JMCrawler()
download_manager = DownloadManager()
comic_manager = ComicManager()

# 历史记录（搜索/阅读）每日清理一次，避免无限增长
def _schedule_history_cleanup():
    """每 24h 清理一次超过 30 天的搜索/阅读历史。后台线程（daemon），不阻塞启动、不阻止进程退出。"""
    def _run():
        try:
            cleanup_old_records(30)
        except Exception as e:
            print(f"历史记录清理失败: {e}")
        finally:
            t = threading.Timer(86400, _run)
            t.daemon = True
            t.start()
    t0 = threading.Timer(86400, _run)
    t0.daemon = True
    t0.start()

_schedule_history_cleanup()


# ─── 订阅自动巡检（每小时一次，启动后先延迟 3 分钟）──────────
SUB_POLL_INTERVAL_SECONDS = 3600   # 正式间隔 1 小时
SUB_POLL_START_DELAY   = 180      # 启动后 3 分钟首次巡检（等用户打开应用、网络/代理就绪）
SUB_POLL_AUTHOR_LIMIT  = 30       # 每次巡检单作者最多拉取条数

def _run_subscription_poll_once() -> Dict:
    """按作者巡检一次订阅，返回汇总信息 {checked, added_new, authors_with_new}。
    单作者新作判定：取 search_by_author() 前 N 条，过滤掉 sub_updates 中已存在 book_id 的条目，
    即任何新 id（不管时间）都算"首次发现的新作"并入库，避免 JM 时间字段不稳定导致的漏报。"""
    import time
    try:
        subs = get_subscriptions()
    except Exception as e:
        print(f"[SubPoll] 读订阅列表失败: {e}")
        return {"checked": 0, "added_new": 0, "authors_with_new": 0, "error": str(e)}
    checked = 0
    added_new = 0
    authors_with_new = 0
    now_ms = int(time.time() * 1000)
    for s in subs:
        author = (s.get("author") or "").strip()
        if not author:
            continue
        try:
            # 同义作者展开，避免别名作者漏更
            try:
                expanded = expand_author(author) or [author]
            except Exception:
                expanded = [author]
            collected = []
            seen_ids = set()
            for a in expanded:
                try:
                    res = jm_crawler.search_by_author(a, max_total=SUB_POLL_AUTHOR_LIMIT) or []
                except Exception as e2:
                    print(f"[SubPoll] 作者搜索异常 '{a}': {e2}")
                    continue
                for c in res:
                    cid = int(c.get("id") or 0)
                    if not cid or cid in seen_ids:
                        continue
                    seen_ids.add(cid)
                    collected.append(c)
            if not collected:
                note_subscription_checked(author, now_ms)
                checked += 1
                continue
            # 已有 sub_updates 中的 book_id 去重
            try:
                existing_updates = get_sub_updates_by_author(author) or []
            except Exception:
                existing_updates = []
            known_ids = {int(u.get("book_id") or 0) for u in existing_updates if u.get("book_id")}
            new_items = [c for c in collected if int(c.get("id") or 0) not in known_ids]
            # 按 id 升序插入（更像"先发表→后入库"），最近一部用于写 last_title
            new_items.sort(key=lambda c: int(c.get("id") or 0))
            delta = 0
            last_title = ""
            for c in new_items:
                cid = int(c.get("id") or 0)
                title = (c.get("title") or "").strip() or f"作品 {cid}"
                cover_url = (c.get("cover") or "").strip()
                try:
                    insert_sub_update(author=author, book_id=cid, title=title, cover_url=cover_url, found_at=now_ms, ignore_duplicate=True)
                except Exception as ie:
                    print(f"[SubPoll] 插入新作记录失败 '{author}' id={cid}: {ie}")
                    continue
                delta += 1
                last_title = title
                added_new += 1
            if delta > 0:
                note_subscription_update(author, now_ms, delta, last_title)
                authors_with_new += 1
            else:
                note_subscription_checked(author, now_ms)
            checked += 1
        except Exception as e:
            print(f"[SubPoll] 巡检作者 '{author}' 失败: {e}")
            continue
    summary = {"checked": checked, "added_new": added_new, "authors_with_new": authors_with_new}
    if added_new:
        print(f"[SubPoll] 巡检完成：检查 {checked} 位作者，新增 {added_new} 条新作（{authors_with_new} 位作者有更新）")
    else:
        print(f"[SubPoll] 巡检完成：检查 {checked} 位作者，无新增。")
    return summary


def _schedule_subscription_poll():
    """递归：启动延迟 3 分钟 → 每 60 分钟巡检一次订阅作者。全部走 daemon Timer，进程退出即停。"""
    def _run():
        try:
            _run_subscription_poll_once()
        except Exception as e:
            print(f"[SubPoll] 例行巡检异常: {e}")
        finally:
            t = threading.Timer(SUB_POLL_INTERVAL_SECONDS, _run)
            t.daemon = True
            t.start()
    t0 = threading.Timer(SUB_POLL_START_DELAY, _run)
    t0.daemon = True
    t0.start()


_schedule_subscription_poll()


@app.route("/api/subscriptions/poll", methods=["POST"])
def trigger_sub_poll_route():
    """手动触发一次订阅巡检（调试/用户主动用）。"""
    try:
        summary = _run_subscription_poll_once()
        return jsonify({"success": True, **summary})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

# 线程安全的下载进度
_dl_lock = threading.Lock()
_dl_progress: dict = {}


def _update_progress(download_id: str, progress: int, status: str, message: str) -> None:
    with _dl_lock:
        if download_id in _dl_progress:
            _dl_progress[download_id].update(
                {"progress": progress, "status": status, "message": message}
            )


def _cleanup_progress(download_id: str) -> None:
    """下载完成后从"进行中"内存列表移除（历史已写入）。"""
    with _dl_lock:
        _dl_progress.pop(download_id, None)


def _comic_title(jm_id: int) -> str:
    """已下载漫画的标题（取不到时退化为 JM-<id>）。直接单条查询，不 materialize 全列表。"""
    meta = comic_manager.get_comic_meta(jm_id) or {}
    return meta.get("title") or f"JM-{jm_id}"


# ─── 下载并发控制 ────────────────────────────────────────────
# 全局线程池：限制同时进行的下载任务数（对应设置项 max_concurrent_downloads）。
# 单本与批量下载都提交到这里，天然保证总并发不超过上限，避免无限制起线程同时打 JM。
_dl_executor = None


def _get_dl_executor():
    global _dl_executor
    if _dl_executor is None:
        try:
            limit = int(get_system_config("max_concurrent_downloads") or "3")
        except (ValueError, TypeError):
            limit = 3
        limit = max(1, min(16, limit))
        _dl_executor = ThreadPoolExecutor(max_workers=limit, thread_name_prefix="jm-dl")
    return _dl_executor


def _run_download_task(jm_id, comic_info, download_id, category_ids):
    """下载任务实体（被线程池调用）。成功 / 失败都负责进度清理。"""
    try:
        download_manager.download_comic(
            jm_id, comic_info,
            lambda p, s, m: _update_progress(download_id, p, s, m),
        )
        if category_ids:
            set_comic_categories(jm_id, category_ids)
        add_download_history(jm_id, comic_info.get("title", ""), "completed")
        # 短暂保留以便前端提示，随后移出"进行中"列表
        threading.Timer(2.0, _cleanup_progress, args=(download_id,)).start()
    except Exception as e:
        _update_progress(download_id, 0, "error", str(e))
        add_download_history(jm_id, comic_info.get("title", ""), "failed", str(e))
        # 失败任务同样延时清理，避免无限残留（保留 30s 供前端提示错误）
        threading.Timer(30.0, _cleanup_progress, args=(download_id,)).start()


# ─── 页面路由 ──────────────────────────────────────────────

@app.route("/")
def index():
    # 启动页 = 书架（对齐手机版 Route 默认页）
    return redirect("/library")

@app.route("/home")
def home_page():
    # v2.11.0：主页与搜索合并为「发现」页；v2.12.0 起只保留重定向兜底旧链接
    return redirect("/search")

@app.route("/search")
def search_page():
    return render_template("search.html")

@app.route("/library")
def library_page():
    return render_template("library.html")

@app.route("/reader/<int:jm_id>")
def reader_page(jm_id):
    return render_template("reader.html", jm_id=jm_id)

@app.route("/settings")
def settings_page():
    return render_template("settings.html")


SUB_PAGES = {
    "appearance": ("外观", "settings_sub_appearance.html"),
    "reader":     ("阅读", "settings_sub_reader.html"),
    "search":     ("搜索", "settings_sub_search.html"),
    "tags":       ("标签库", "settings_sub_tags.html"),
    "block":      ("屏蔽管理", "settings_sub_block.html"),
    "download":   ("下载与备份", "settings_sub_download.html"),
    "recommend":  ("推荐", "settings_sub_recommend.html"),
    "about":      ("关于", "settings_sub_about.html"),
}

# v2.12.0：标签库 / 标签库管理 / 同义词 三页合并为「标签库」一页。
# 旧子页保留 302，避免历史链接与旧书签失效。
SUB_PAGE_ALIASES = {
    "tags-manage": "/settings/tags",
    "synonyms":    "/settings/tags",
}


@app.route("/settings/<sub>")
def settings_sub_page(sub):
    if sub in SUB_PAGE_ALIASES:
        return redirect(SUB_PAGE_ALIASES[sub])
    if sub not in SUB_PAGES:
        return redirect("/settings")
    title, tmpl = SUB_PAGES[sub]
    return render_template(tmpl, sub_title=title, sub_key=sub)


@app.route("/api/about")
def about_info_route():
    """关于页统计：版本号、用户数据、漫画占用、标签库统计。"""
    import os
    import platform
    from core.config import BASE_DIR, DATA_DIR, DB_FILE
    try:
        # 用户数据（data 目录 + 数据库 + 缓存）：粗略全量 walk
        user_bytes = 0
        for root, _, fns in os.walk(DATA_DIR):
            for fn in fns:
                try: user_bytes += os.path.getsize(os.path.join(root, fn))
                except OSError: pass
    except Exception:
        user_bytes = 0
    try:
        conn = _get_conn()
        rows = conn.execute("SELECT COALESCE(SUM(file_size),0) FROM downloaded_comics").fetchone()
        comic_bytes = int(rows[0] or 0)
    except Exception:
        comic_bytes = 0
    try:
        tag_count = get_tag_library_count(exp=False)
    except Exception:
        tag_count = 0
    try:
        exp_tag_count = get_tag_library_count(exp=True)
    except Exception:
        exp_tag_count = 0
    try:
        comics_count = int(_get_conn().execute("SELECT COUNT(*) FROM downloaded_comics").fetchone()[0] or 0)
    except Exception:
        comics_count = 0
    def fmt_bytes(b):
        b = int(b or 0)
        if b >= 1024**3: return f"{b/1024**3:.2f} GB"
        if b >= 1024**2: return f"{b/1024**2:.2f} MB"
        if b >= 1024: return f"{b/1024:.2f} KB"
        return f"{b} B"
    return jsonify({
        "success": True,
        "data": {
            "version": __APP_VERSION__,
            "platform": f"Windows · {platform.python_implementation()} {platform.python_version()}",
            "comics_count": comics_count,
            "tag_count": tag_count,
            "exp_tag_count": exp_tag_count,
            "user_data_bytes": user_bytes,
            "user_data": fmt_bytes(user_bytes),
            "comic_bytes": comic_bytes,
            "comic_size": fmt_bytes(comic_bytes),
            "db_file": fmt_bytes(os.path.getsize(DB_FILE) if os.path.isfile(DB_FILE) else 0),
        }
    })


@app.route("/downloads")
def downloads_page():
    return render_template("downloads.html")


# ─── 搜索 API ──────────────────────────────────────────────

@app.route("/api/search/jm/<int:jm_id>")
def search_by_jm_id(jm_id):
    try:
        info = jm_crawler.get_comic_info(jm_id)
        if info:
            blocked = get_blocked_sets()
            cid = info.get("id")
            is_blocked = (cid is not None and cid in blocked["works"]) or (
                info.get("author") and normalize_author(info.get("author")) in blocked["authors"]
            )
            if is_blocked:
                return jsonify({"success": False, "message": "该作品已被屏蔽"})
            add_search_history("jm_id", str(jm_id), 1)
            return jsonify({"success": True, "data": info})
        return jsonify({"success": False, "message": "未找到对应的漫画"})
    except Exception as e:
        return jsonify({"success": False, "message": f"搜索失败: {str(e)}"})


def _filter_blocked(results):
    """从搜索结果中剔除已被拉黑的作品 / 作者 / 标签（与书架一致）。

    搜索路由此前未应用黑名单，导致在搜索页屏蔽作品后，翻页或切回搜索页时
    被屏蔽的作品会重新出现。这里统一过滤，保证屏蔽在所有搜索入口都生效。
    """
    if not results:
        return results
    blocked = get_blocked_sets()
    if not (blocked["works"] or blocked["authors"] or blocked["tags"]):
        return results
    out = []
    for c in results:
        cid = c.get("id")
        if cid is not None and cid in blocked["works"]:
            continue
        author = c.get("author")
        if author and normalize_author(author) in blocked["authors"]:
            continue
        tags = c.get("tags") or []
        if any(str(t) in blocked["tags"] for t in tags):
            continue
        out.append(c)
    return out


def _enrich_and_filter_search(results: List[Dict]) -> List[Dict]:
    """P1 搜索页新行为：
    - 在每条结果上附加 downloaded / finished 状态（由本地 downloaded_comics 决定）
    - 按 hide_downloaded_in_search / hide_finished_in_search 隐藏或标记灰显
    - 若 search_apply_blocks=0 跳过黑名单过滤（默认 1）
    返回最终列表，过滤后带 downloaded/finished 字段。
    """
    if not results:
        return results or []

    # 1) 屏蔽应用开关
    apply_blocks = (get_system_config("search_apply_blocks") or "1").strip().lower() not in ("0", "false", "off", "no")
    if apply_blocks:
        results = _filter_blocked(results)

    # 2) 本地已下载 / 已读完映射（单条 SQL 更高效）
    try:
        ids = []
        for c in results:
            try: ids.append(int(c.get("id") or 0))
            except Exception: pass
        if ids:
            qs = ",".join("?" * len(ids))
            rows = _get_conn().execute(
                f"SELECT jm_id, favorite, finished FROM downloaded_comics WHERE jm_id IN ({qs})",
                ids,
            ).fetchall()
            local_map = {int(r[0]): {"favorite": int(r[1] or 0), "finished": int(r[2] or 0), "downloaded": 1} for r in rows}
        else:
            local_map = {}
    except Exception:
        local_map = {}

    # 3) 附加字段 & 过滤
    mode_dl = (get_system_config("hide_downloaded_in_search") or "hide").strip().lower()
    mode_fin = (get_system_config("hide_finished_in_search") or "hide").strip().lower()

    out = []
    for c in results:
        try: jid = int(c.get("id") or 0)
        except Exception: jid = 0
        info = local_map.get(jid)
        c["downloaded"] = 1 if info else 0
        c["favorite"] = (info or {}).get("favorite") or 0
        c["finished"] = (info or {}).get("finished") or 0
        if mode_dl == "hide" and info:
            continue
        if mode_fin == "hide" and info and info.get("finished"):
            continue
        c["__gray_dl"] = 1 if (mode_dl == "gray" and info) else 0
        c["__gray_fin"] = 1 if (mode_fin == "gray" and info and info.get("finished")) else 0
        out.append(c)
    return out


def _default_order_by() -> str:
    v = (get_system_config("default_sort") or "mr").strip().lower()
    if v in ("mr", "mv", "mp"):
        return v
    return "mr"


@app.route("/api/search/keyword")
def search_by_keyword():
    keyword = request.args.get("keyword", "").strip()
    sort_order = request.args.get("sort", "desc")
    order_by = request.args.get("order_by", "") or _default_order_by()
    time_range = request.args.get("time", "a")
    page = request.args.get("page", "1").strip()
    if not keyword:
        return jsonify({"success": False, "message": "关键词不能为空"})
    try:
        page_num = max(1, int(page))
        results = jm_crawler.search_by_keyword(
            keyword, sort_order, page=page_num,
            order_by=order_by, time_range=time_range,
        )
        results = postprocess_results(results)
        results = _enrich_and_filter_search(results)
        add_search_history("keyword", keyword, len(results))
        return jsonify({"success": True, "data": results, "order_by": order_by})
    except Exception as e:
        return jsonify({"success": False, "message": f"搜索失败: {str(e)}"})


@app.route("/api/search/tags")
def search_by_tags():
    tags = request.args.get("tags", "").strip()
    if not tags:
        return jsonify({"success": False, "message": "请选择至少一个标签"})
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    if not tag_list:
        return jsonify({"success": False, "message": "请选择至少一个标签"})
    try:
        try:
            limit = int(get_system_config("search_result_limit") or "80")
        except (ValueError, TypeError):
            limit = 80
        mode = request.args.get("mode", "or").strip().lower()
        if mode not in ("or", "and"):
            mode = "or"
        # 别名展开：每个标签一并检索其同义词（同义词保留）；默认把用户实际输入的名字排前面
        expanded = expand_tags(tag_list)
        input_tags = tag_list
        results, stats = jm_crawler.search_by_tags(expanded, mode=mode, max_total=limit, input_tags=input_tags)
        results = postprocess_results(results)
        results = _enrich_and_filter_search(results)
        add_search_history("tag", ",".join(tag_list), len(results))
        return jsonify({"success": True, "data": results, "stats": stats})
    except Exception as e:
        return jsonify({"success": False, "message": f"搜索失败: {str(e)}"})


@app.route("/api/search/author")
def search_by_author():
    """按作者搜索。"""
    author = request.args.get("author", "").strip()
    if not author:
        return jsonify({"success": False, "message": "作者不能为空"})
    try:
        try:
            limit = int(get_system_config("search_result_limit") or "80")
        except (ValueError, TypeError):
            limit = 80
        # 别名展开：作者名一并检索其同义署名
        results = []
        seen = set()
        for a in expand_author(author):
            for c in jm_crawler.search_by_author(a, max_total=limit):
                cid = c.get("id")
                if cid not in seen:
                    seen.add(cid)
                    results.append(c)
        results = postprocess_results(results)
        results = _enrich_and_filter_search(results)
        add_search_history("author", author, len(results))
        return jsonify({"success": True, "data": results, "author": author})
    except Exception as e:
        return jsonify({"success": False, "message": f"搜索失败: {str(e)}"})


@app.route("/api/search/combined")
def search_combined():
    """联合搜索：关键词 + 标签 + 作者（且/或）过滤。"""
    import re
    keyword = request.args.get("keyword", "").strip()
    author = request.args.get("author", "").strip()
    tags = request.args.get("tags", "").strip()
    mode = request.args.get("mode", "or").strip().lower()
    if mode not in ("or", "and"):
        mode = "or"
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    try:
        try:
            limit = int(get_system_config("search_result_limit") or "80")
        except (ValueError, TypeError):
            limit = 80

        # 第一步：关键词/作者/JM号 搜索
        kw_candidates = None
        if keyword and re.match(r'^\d+$', keyword):
            kw_candidates = [jm_crawler.get_comic_info(int(keyword))]
            kw_candidates = [c for c in kw_candidates if c]
        elif keyword:
            kw_candidates = jm_crawler.search_by_keyword(keyword, page=1)
        elif author:
            kw_candidates = jm_crawler.search_by_author(author, max_total=limit)

        # 第二步：标签搜索
        tag_results = []
        tag_ids = None
        if tag_list:
            expanded_tags = expand_tags(tag_list)
            input_tags = tag_list
            tag_results, _ = jm_crawler.search_by_tags(expanded_tags, mode=mode, max_total=limit * 2, input_tags=input_tags)
            tag_ids = {c["id"] for c in tag_results if c.get("id")}

        # 第三步：取交集
        if kw_candidates is not None and tag_ids is not None:
            candidates = [c for c in kw_candidates if c.get("id") in tag_ids]
        elif kw_candidates is not None:
            candidates = kw_candidates
        elif tag_ids is not None:
            candidates = tag_results
        else:
            candidates = []

        candidates = candidates[:limit]
        candidates = postprocess_results(candidates)
        candidates = _filter_blocked(candidates)

        # 记录联合搜索历史（关键词 / 作者 / 标签分别记一条，供推荐算法提取兴趣）
        if keyword:
            add_search_history("keyword", keyword, len(candidates))
        if author:
            add_search_history("author", author, len(candidates))
        if tag_list:
            add_search_history("tag", ",".join(tag_list), len(candidates))

        return jsonify({
            "success": True, "data": candidates,
            "keyword": keyword, "author": author, "tags": tag_list, "mode": mode
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"搜索失败: {str(e)}"})


@app.route("/api/search/enrich")
def enrich_search_results():
    ids_param = request.args.get("ids", "").strip()
    if not ids_param:
        return jsonify({"success": False, "message": "缺少 ids 参数"})
    album_ids = []
    for raw in ids_param.split(","):
        raw = raw.strip()
        if raw and raw.isdigit():
            album_ids.append(int(raw))
    album_ids = album_ids[:12]
    if not album_ids:
        return jsonify({"success": False, "message": "没有可用的 ids"})
    try:
        details = jm_crawler.get_search_result_details(album_ids)
        return jsonify({"success": True, "data": details})
    except Exception as e:
        return jsonify({"success": False, "message": f"补充详情失败: {str(e)}"})


# 推荐结果短期缓存（避免每次切回搜索页都重新打 JM 服务器）
_recommend_cache = {"sig": "", "time": 0, "payload": None}
_RECOMMEND_TTL = 300  # 秒


def _offline_recommend(seeds, count):
    """离线推荐：实时搜索不可用时，用本地已下载漫画按标签/作者相似度推荐。

    这是「推荐功能从没出过东西」的主因修复——此前一旦对 jmcomic 源站
    的实时搜索失败（无代理 / 站点不可达），run_seed 全抛异常返回空，
    最终 data 为空，前端永远显示空。这里降级到本地数据。
    """
    try:
        dl = comic_manager.get_downloaded_comics() or []
    except Exception:
        return [], "本地暂无已下载漫画，无法离线推荐"
    if not dl:
        return [], "本地暂无已下载漫画，无法离线推荐"
    seed_tags = {s["value"].strip().lower() for s in seeds if s["type"] == "tag"}
    seed_authors = {normalize_author(s["value"]) for s in seeds if s["type"] == "author"}
    scored = []
    for c in dl:
        tags = [str(t).lower() for t in (c.get("tags") or [])]
        author = c.get("author") or ""
        score = 0
        if seed_tags & set(tags):
            score += 2
        if normalize_author(author) in seed_authors:
            score += 3
        if score > 0:
            scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    out = [c for _, c in scored]
    seen = {c.get("id") for c in out}
    for c in dl:  # 相似度不足时用最近下载补齐
        if c.get("id") not in seen:
            out.append(c)
        if len(out) >= count:
            break
    data = [{
        "id": c.get("id"), "title": c.get("title"), "author": c.get("author"),
        "tags": c.get("tags") or [], "pages": c.get("pages", 0),
        "favorites": c.get("favorites", 0), "cover": "",
    } for c in out[:count]]
    note = "网络推荐不可用，已用本地相似漫画推荐" if scored else "网络不可用，已用最近下载的漫画推荐"
    return data, note


@app.route("/api/recommend")
def recommend():
    """自动推荐：基于搜索历史（常搜关键词/作者/标签）+ 用户自定义内容生成兴趣推荐。

    返回 data（漫画列表，结构同搜索结果）、basis / seeds / is_default 元信息。
    无历史且无自定义时，回退为「最近下载的漫画」作为默认推荐（离线可用）。
    """
    try:
        enabled = (get_system_config("recommend_enabled") or "1").strip().lower() in ("1", "true", "on", "yes")
        if not enabled:
            return jsonify({"success": True, "data": [], "enabled": False, "basis": [], "seeds": [], "is_default": False})

        try:
            count = int(get_system_config("recommend_count") or "20")
        except (ValueError, TypeError):
            count = 20
        count = max(4, min(60, count))

        basis = []
        raw_basis = get_system_config("recommend_basis")
        if raw_basis:
            try:
                basis = [b for b in json.loads(raw_basis) if b in ("keyword", "author", "tag")]
            except Exception:
                basis = []
        if not basis:
            basis = ["keyword", "author", "tag"]

        custom = []
        raw_custom = get_system_config("recommend_custom")
        if raw_custom:
            try:
                custom = json.loads(raw_custom) or []
                if not isinstance(custom, list):
                    custom = []
            except Exception:
                custom = []
        custom = [c for c in custom
                  if isinstance(c, dict) and c.get("value") and c.get("type") in ("name", "tag", "author")]

        # ── 构造兴趣种子 ──
        seeds = []
        seen = set()

        def add_seed(stype, value):
            value = (value or "").strip()
            if not value:
                return
            key = (stype, value.lower())
            if key in seen:
                return
            seen.add(key)
            seeds.append({"type": stype, "value": value})

        type_map_custom = {"name": "keyword", "tag": "tag", "author": "author"}
        for c in custom:
            add_seed(type_map_custom[c["type"]], c["value"])

        hist = get_search_history_aggregated(limit=200)
        for h in hist:
            st = h["search_type"]
            if st in ("keyword", "jm_id") and "keyword" in basis:
                add_seed("keyword", h["search_content"])
            elif st == "author" and "author" in basis:
                add_seed("author", h["search_content"])
            elif st == "tag" and "tag" in basis:
                add_seed("tag", h["search_content"])

        per_type_limit = 3
        limited, cnt = [], {"keyword": 0, "author": 0, "tag": 0}
        for s in seeds:
            if cnt[s["type"]] < per_type_limit:
                cnt[s["type"]] += 1
                limited.append(s)
        seeds = limited[:10]

        # 无种子 → 默认推荐（最近下载，离线）
        if not seeds:
            dl = comic_manager.get_downloaded_comics()[:count]
            data = [{
                "id": c["id"], "title": c["title"], "author": c["author"],
                "tags": c.get("tags") or [], "pages": c.get("pages", 0),
                "favorites": c.get("favorites", 0), "cover": "",
            } for c in dl]
            return jsonify({"success": True, "data": data, "enabled": True,
                            "basis": basis, "seeds": [], "is_default": True,
                            "note": "暂无搜索历史，已用最近下载的漫画作为默认推荐"})

        # ── 短期缓存：签名一致且未过期直接返回 ──
        sig = f"{count}|{','.join(sorted(basis))}|{json.dumps(custom, ensure_ascii=False)}"
        now = time.time()
        if _recommend_cache["sig"] == sig and _recommend_cache["payload"] \
                and (now - _recommend_cache["time"]) < _RECOMMEND_TTL:
            cached = dict(_recommend_cache["payload"])
            cached["cached"] = True
            return jsonify(cached)

        # ── 并发执行各种子搜索 ──
        def run_seed(seed):
            st, val = seed["type"], seed["value"]
            try:
                if st == "keyword":
                    res = jm_crawler.search_by_keyword(val, page=1)
                elif st == "author":
                    res = jm_crawler.search_by_author(val, max_total=12)
                else:
                    res, _ = jm_crawler.search_by_tags([val], mode="or", max_total=12)
                return postprocess_results(res[:8]) if res else []
            except Exception as e:
                print(f"推荐种子搜索失败 {st}={val}: {e}")
                return []

        merged = {}
        with ThreadPoolExecutor(max_workers=min(4, len(seeds))) as ex:
            for res in ex.map(run_seed, seeds):
                for c in (res or []):
                    cid = c.get("id")
                    if cid and cid not in merged:
                        merged[cid] = c

        data = list(merged.values())
        def _fav_score(c):
            try:
                return jm_crawler._parse_count(c.get("favorites", 0))
            except Exception:
                return 0
        data.sort(key=_fav_score, reverse=True)
        data = data[:count]

        # 实时搜索无结果（多半是网络不通）→ 离线降级，用本地已下载漫画推荐
        if not data:
            offline, note = _offline_recommend(seeds, count)
            if offline:
                return jsonify({"success": True, "data": offline, "enabled": True,
                                "basis": basis, "seeds": [f"{s['type']}:{s['value']}" for s in seeds],
                                "is_default": True, "cached": False, "note": note})

        payload = {"success": True, "data": data, "enabled": True,
                   "basis": basis, "seeds": [f"{s['type']}:{s['value']}" for s in seeds],
                   "is_default": False, "cached": False}
        _recommend_cache.update({"sig": sig, "time": now, "payload": payload})
        return jsonify(payload)
    except Exception as e:
        return jsonify({"success": False, "message": f"推荐失败: {str(e)}"})


# ─── 封面 API ──────────────────────────────────────────────

@app.route("/api/cover/<int:jm_id>")
def get_comic_cover(jm_id):
    try:
        meta = comic_manager.get_comic_meta(jm_id)
        if meta and meta.get("cover_path") and os.path.exists(meta["cover_path"]):
            return send_file(meta["cover_path"])
        cover_url = jm_crawler.get_cover_url(jm_id)
        if cover_url:
            return jsonify({"success": True, "data": {
                "cover": cover_url,
                "cover_local": os.path.join(TEMP_CACHE_DIR, f"cover_{jm_id}.jpg"),
            }})
        return jsonify({"success": False, "message": "未找到封面"})
    except Exception as e:
        return jsonify({"success": False, "message": f"获取封面失败: {str(e)}"})


@app.route("/api/cover/downloaded/<int:jm_id>")
def get_downloaded_comic_cover(jm_id):
    try:
        meta = comic_manager.get_comic_meta(jm_id)
        if meta and meta.get("cover_path") and os.path.exists(meta["cover_path"]):
            return send_file(meta["cover_path"])
        return jsonify({"success": False, "message": "封面不存在"})
    except Exception as e:
        return jsonify({"success": False, "message": f"获取封面失败: {str(e)}"})


# ─── 下载 API ──────────────────────────────────────────────

@app.route("/api/download/<int:jm_id>", methods=["POST"])
def download_comic(jm_id):
    """下载单本漫画（可选分类）。"""
    try:
        if comic_manager.is_comic_downloaded(jm_id):
            return jsonify({"success": False, "message": "该漫画已下载", "downloaded": True})

        comic_info = jm_crawler.get_comic_info(jm_id)
        if not comic_info:
            return jsonify({"success": False, "message": "未找到对应的漫画"})

        category_ids = _parse_category_ids(request)

        download_id = f"{jm_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        with _dl_lock:
            _dl_progress[download_id] = {
                "progress": 0, "status": "starting", "message": "开始下载...",
                "jm_id": jm_id, "title": comic_info.get("title", ""),
            }

        _get_dl_executor().submit(_run_download_task, jm_id, comic_info, download_id, list(category_ids))
        return jsonify({"success": True, "download_id": download_id, "message": "下载任务已启动"})
    except Exception as e:
        return jsonify({"success": False, "message": f"下载失败: {str(e)}"})


@app.route("/api/download/batch", methods=["POST"])
def download_batch():
    """批量下载。body: {"ids": [123, 456, ...], "category_ids": [1, 2]}"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        raw_ids = data.get("ids", [])
        if not raw_ids:
            return jsonify({"success": False, "message": "请提供漫画 ID 列表"})

        jm_ids = []
        for rid in raw_ids:
            try:
                jm_ids.append(int(rid))
            except (ValueError, TypeError):
                continue
        if not jm_ids:
            return jsonify({"success": False, "message": "没有有效的漫画 ID"})

        category_ids = _parse_category_ids(request)
        ex = _get_dl_executor()

        results = []
        for jm_id in jm_ids:
            if comic_manager.is_comic_downloaded(jm_id):
                results.append({"jm_id": jm_id, "status": "skipped", "message": "已下载"})
                continue

            comic_info = jm_crawler.get_comic_info(jm_id)
            if not comic_info:
                results.append({"jm_id": jm_id, "status": "error", "message": "未找到漫画"})
                continue

            download_id = f"{jm_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            with _dl_lock:
                _dl_progress[download_id] = {
                    "progress": 0, "status": "starting", "message": "开始下载...",
                    "jm_id": jm_id, "title": comic_info.get("title", ""),
                }

            ex.submit(_run_download_task, jm_id, comic_info, download_id, list(category_ids))
            results.append({"jm_id": jm_id, "status": "downloading", "download_id": download_id})

        return jsonify({"success": True, "data": results})
    except Exception as e:
        return jsonify({"success": False, "message": f"批量下载失败: {str(e)}"})


def _parse_category_ids(req) -> list:
    """从请求中解析 category_ids（支持 JSON body 和 query string）。"""
    cids = []
    if req.is_json:
        data = req.get_json(silent=True) or {}
        raw = data.get("category_ids", [])
    else:
        raw = req.args.get("category_ids", "").split(",") if req.args.get("category_ids") else []
    for c in raw:
        try:
            cids.append(int(c))
        except (ValueError, TypeError):
            pass
    return cids


@app.route("/api/download/progress/<download_id>")
def get_download_progress(download_id):
    with _dl_lock:
        if download_id in _dl_progress:
            return jsonify({"success": True, "data": _dl_progress[download_id]})
    return jsonify({"success": False, "message": "下载任务不存在"})


@app.route("/api/download/progress/<download_id>", methods=["DELETE"])
def delete_download_progress(download_id):
    """从内存中的进行中列表移除一个任务（失败/取消/卡住时清理用）。"""
    with _dl_lock:
        if download_id in _dl_progress:
            del _dl_progress[download_id]
            return jsonify({"success": True, "message": "已移除"})
    return jsonify({"success": False, "message": "任务不存在"})


@app.route("/api/download/progress")
def get_all_progress():
    with _dl_lock:
        return jsonify({"success": True, "data": dict(_dl_progress)})


@app.route("/api/download/history")
def get_download_history_route():
    try:
        history = get_download_history(50)
        # 标记每条记录对应的本地文件是否还存在
        for item in history:
            jm_id = item.get("jm_id")
            item["files_exist"] = comic_manager.is_comic_downloaded(jm_id) if jm_id else False
        return jsonify({"success": True, "data": history})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/download/history/<int:record_id>", methods=["DELETE"])
def delete_download_history_route(record_id):
    """删除单条下载历史记录。"""
    try:
        ok = delete_download_history(record_id)
        return jsonify({"success": ok, "message": "已删除" if ok else "记录不存在"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/download/history", methods=["DELETE"])
def clear_download_history_route():
    """清空全部下载历史记录。"""
    try:
        n = clear_download_history()
        return jsonify({"success": True, "data": {"deleted": n}, "message": f"已清空 {n} 条记录"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ─── 分类 API ──────────────────────────────────────────────

@app.route("/api/categories")
def list_categories():
    """获取分类列表。?tree=1 返回树形结构。"""
    tree = request.args.get("tree", "0") == "1"
    data = get_category_tree() if tree else get_all_categories()
    return jsonify({"success": True, "data": data})


@app.route("/api/categories", methods=["POST"])
def add_category():
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "message": "分类名称不能为空"})
    parent_id = data.get("parent_id")
    try:
        cid = create_category(name, parent_id)
        return jsonify({"success": True, "data": {"id": cid, "name": name}})
    except Exception as e:
        return jsonify({"success": False, "message": f"创建失败: {str(e)}"})


@app.route("/api/categories/<int:cat_id>", methods=["PUT"])
def edit_category(cat_id):
    data = request.get_json(force=True, silent=True) or {}
    name = data.get("name")
    parent_id = data.get("parent_id")
    if update_category(cat_id, name=name, parent_id=parent_id):
        return jsonify({"success": True})
    return jsonify({"success": False, "message": "更新失败"})


@app.route("/api/categories/<int:cat_id>", methods=["DELETE", "GET"])
def remove_category(cat_id):
    """删除分类；GET ?count_only=1 用于删除前预检。

    v2.13.0：分类非空时必须指定漫画去向，否则拒绝删除（防止静默丢归属）。
    请求体可选 {"reassign_to": <分类 id>}：
      - 省略 / null → 漫画移入「未归类」
      - 具体 id     → 漫画改归该分类
    """
    if request.args.get("count_only") == "1":
        return jsonify({
            "success": True,
            "count": get_category_comic_count(cat_id),
        })
    if request.method == "GET":
        return jsonify({"success": False, "message": "仅支持 DELETE 或 ?count_only=1"})

    data = request.get_json(force=True, silent=True) or {}
    raw = data.get("reassign_to", "__absent__")
    target = None
    if raw not in ("__absent__", None, "", "null"):
        try:
            target = int(raw)
        except (ValueError, TypeError):
            return jsonify({"success": False, "message": "reassign_to 无效"})

    cnt = get_category_comic_count(cat_id)
    if cnt > 0:
        # 必须显式给出去向（前端会传 reassign_to，哪怕是 null 也会带上键）
        if "reassign_to" not in data:
            return jsonify({
                "success": False,
                "need_reassign": True,
                "count": cnt,
                "message": f"该分类下有 {cnt} 部漫画，请先选择将它们移动到哪个分类",
            })
        ok = delete_category_with_reassign(cat_id, target)
    else:
        ok = delete_category(cat_id)

    if ok:
        return jsonify({"success": True, "count": cnt})
    return jsonify({"success": False, "message": "删除失败（分类不存在或目标分类无效）"})


@app.route("/api/categories/<int:cat_id>/move", methods=["POST"])
def move_category(cat_id):
    """移动分类（重定父级）。parent_id 为 null 表示移到根目录。
    阻止把分类移到自身或其子孙分类下，避免产生环。"""
    data = request.get_json(force=True, silent=True) or {}
    raw_parent = data.get("parent_id")
    parent_id = None
    if raw_parent is not None and str(raw_parent) != "" and str(raw_parent).lower() != "null":
        try:
            parent_id = int(raw_parent)
        except (ValueError, TypeError):
            return jsonify({"success": False, "message": "parent_id 无效"})

    if parent_id is not None:
        if parent_id == cat_id:
            return jsonify({"success": False, "message": "不能移动到自身"})
        if parent_id in get_category_descendant_ids(cat_id):
            return jsonify({"success": False, "message": "不能移动到其子分类下"})

    if set_category_parent(cat_id, parent_id):
        return jsonify({"success": True})
    return jsonify({"success": False, "message": "移动失败"})


@app.route("/api/comic/<int:jm_id>/categories", methods=["GET"])
def comic_categories(jm_id):
    return jsonify({"success": True, "data": get_comic_categories(jm_id)})


@app.route("/api/comic/<int:jm_id>/categories", methods=["PUT"])
def set_comic_cats(jm_id):
    data = request.get_json(force=True, silent=True) or {}
    cids = [int(c) for c in data.get("category_ids", []) if str(c).isdigit()]
    set_comic_categories(jm_id, cids)
    return jsonify({"success": True})


# ─── 已下载 / 阅读 API ─────────────────────────────────────

@app.route("/api/downloaded")
def get_downloaded_comics():
    try:
        category_id = request.args.get("category_id", type=int)
        # v2.13.0：「未归类」收件箱视图（已下载但未归入任何分类）
        uncategorized = request.args.get("uncategorized") == "1"
        comics = comic_manager.get_downloaded_comics(
            category_id=category_id, uncategorized=uncategorized
        )
        # 标注是否被拉黑（作者/作品），供前端书架灰显
        blocked = get_blocked_sets()
        for c in comics:
            is_blocked = (c.get("id") in blocked["works"]) or (
                c.get("author") and normalize_author(c.get("author")) in blocked["authors"]
            )
            c["blocked"] = bool(is_blocked)
        return jsonify({"success": True, "data": comics})
    except Exception as e:
        return jsonify({"success": False, "message": f"获取列表失败: {str(e)}"})


@app.route("/api/read/<int:jm_id>")
def read_comic(jm_id):
    try:
        if not comic_manager.is_comic_downloaded(jm_id):
            return jsonify({"success": False, "message": "该漫画尚未下载"})
        chapters = comic_manager.get_comic_chapters(jm_id)
        if not chapters:
            return jsonify({"success": False, "message": "没有可用的章节"})
        # 回收站：宽限期内重新阅读（该书仍是已读完）→ 续期 7 天，对齐 Android renewRecycle
        _st = get_comic_status(jm_id)
        if _st and has_bit(_st["status"], BIT_FINISHED):
            renew_recycle(jm_id)
        title = _comic_title(jm_id)
        return jsonify({
            "success": True,
            "data": {
                "title": title, "chapters": chapters,
                "current_chapter": chapters[0]["id"],
                "current_chapter_pages": chapters[0]["pages"],
                "total_chapters": len(chapters),
                "comic_path": chapters[0]["path"],
            },
        }), 200
    except Exception as e:
        return jsonify({"success": False, "message": f"获取阅读数据失败: {str(e)}"})


@app.route("/api/read/<int:jm_id>/chapter/<chapter_id>")
def read_comic_chapter(jm_id, chapter_id):
    try:
        if not comic_manager.is_comic_downloaded(jm_id):
            return jsonify({"success": False, "message": "该漫画尚未下载"})
        chapters = comic_manager.get_comic_chapters(jm_id)
        target = next((ch for ch in chapters if ch["id"] == chapter_id), None)
        if not target:
            return jsonify({"success": False, "message": "章节不存在"})
        title = _comic_title(jm_id)
        return jsonify({
            "success": True,
            "data": {
                "title": title, "chapters": chapters,
                "current_chapter": target["id"],
                "current_chapter_pages": target["pages"],
                "total_chapters": len(chapters),
                "comic_path": target["path"],
            },
        }), 200
    except Exception as e:
        return jsonify({"success": False, "message": f"获取章节数据失败: {str(e)}"})


@app.route("/api/comic/<int:jm_id>/page/<int:page_num>")
def get_comic_page(jm_id, page_num):
    try:
        chapter_id = request.args.get("chapter", None)
        # 轻量本地校验章节 id（不联网）：原逻辑每翻一页都调 get_comic_chapters，
        # 而多章漫画的 get_comic_chapters 会联网抓 JM 章节顺序 → 每翻一页卡好几秒。
        # 这里改成只做本地目录校验，路径越界由 get_comic_page_path 的 realpath 兜底。
        if not comic_manager.is_local_chapter(jm_id, chapter_id):
            return jsonify({"success": False, "message": "章节不存在"}), 400
        page_path = comic_manager.get_comic_page_path(jm_id, page_num, chapter_id)
        if page_path and os.path.exists(page_path):
            return send_file(page_path)
        return jsonify({"success": False, "message": "页面不存在"})
    except Exception as e:
        return jsonify({"success": False, "message": f"获取页面失败: {str(e)}"})


@app.route("/api/delete/<int:jm_id>", methods=["DELETE"])
def delete_comic(jm_id):
    """移入回收站（软删）：只删本地图片内容，**保留**记录与封面，宽限期内仍可直接读。

    对齐 Android 书架卡片删除 → trashBook。彻底删除走 DELETE /api/recycle/<jm_id>。
    """
    try:
        ok = comic_manager.delete_local_keep_record(jm_id)
        if ok:
            clear_status_bits(jm_id, BIT_LOCAL)
            set_trashed(jm_id, True)
        return jsonify({"success": ok, "message": "已移入回收站" if ok else "移入回收站失败"})
    except Exception as e:
        return jsonify({"success": False, "message": f"移入回收站失败: {str(e)}"})


# ─── 漫画详情页（对齐 Android DetailScreen）──────────────────
# 本地元数据优先，缺失字段回落到在线详情；两种来源都没有才报错。
# 本地已删（回收站）时 get_comic_meta 返回 None → has_local=False，
# 但状态行与封面仍可展示，正好对应「已删本地·需重新下载」。


@app.route("/detail/<int:jm_id>")
def detail_page(jm_id):
    return render_template("detail.html", jm_id=jm_id)


@app.route("/api/detail/<int:jm_id>")
def get_comic_detail(jm_id):
    try:
        local = comic_manager.get_comic_meta(jm_id)
        st = get_comic_status(jm_id) or {}
        status = int(st.get("status") or 0)
        marks = get_recycle_marks(jm_id)

        online = None
        if not local:
            try:
                online = jm_crawler.get_comic_info(jm_id)
            except Exception:
                online = None

        src = local or online
        if not src:
            return jsonify({"success": False,
                            "message": "本地没有这本，在线也取不到详情（检查网络或 JM 号）"})

        tags = src.get("tags") or []
        if isinstance(tags, str):
            tags = [t for t in tags.split(",") if t]

        chapters = []
        if local:
            for ch in (comic_manager.get_comic_chapters(jm_id) or []):
                chapters.append({"id": ch.get("id"), "name": ch.get("name"),
                                 "pages": ch.get("pages")})

        cats = []
        try:
            for c in (get_comic_categories(jm_id) or []):
                cats.append({"id": c.get("id"), "name": c.get("name")})
        except Exception:
            cats = []

        pages = int(src.get("pages") or 0)
        read_page = comic_manager.get_read_progress(jm_id) if local else 0

        return jsonify({"success": True, "data": {
            "jm_id": jm_id,
            "title": src.get("title") or f"JM-{jm_id}",
            "author": src.get("author") or "",
            "tags": tags,
            "description": src.get("description") or "",
            "pages": pages,
            "chapter_count": int(src.get("chapter_count") or len(chapters) or 0),
            "favorites": src.get("favorites") or 0,
            "cover_api": (f"/api/cover/downloaded/{jm_id}" if local else f"/api/cover/{jm_id}"),
            "has_local": bool(local),
            "file_size": int((local or {}).get("file_size") or 0),
            "status": {
                "downloaded": bool(local) or has_bit(status, BIT_DOWNLOADED),
                "finished": has_bit(status, BIT_FINISHED),
                "favorite": has_bit(status, BIT_FAVORITE),
                "blocked": has_bit(status, BIT_BLOCKED),
            },
            "recycle": marks,
            "progress": {
                "page": read_page,
                "pages": pages,
                "percent": round(read_page / pages * 100) if pages else 0,
            },
            "chapters": chapters,
            "categories": cats,
        }})
    except Exception as e:
        return jsonify({"success": False, "message": f"获取详情失败: {str(e)}"})


# ─── 缓存管理 ──────────────────────────────────────────────

def _get_dir_size(directory: str) -> int:
    total = 0
    try:
        for dirpath, _, filenames in os.walk(directory):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                if os.path.exists(fp):
                    total += os.path.getsize(fp)
    except Exception:
        pass
    return total


@app.route("/api/cache/status")
def get_cache_status():
    try:
        size = _get_dir_size(TEMP_CACHE_DIR)
        return jsonify({"success": True, "data": {
            "cache_size": size,
            "cache_size_mb": round(size / 1048576, 2),
            "need_cleanup": size > 104857600,
        }})
    except Exception as e:
        return jsonify({"success": False, "message": f"获取缓存状态失败: {str(e)}"})


@app.route("/api/cache/clear", methods=["POST"])
def clear_cache():
    try:
        cache_dir = TEMP_CACHE_DIR
        if not os.path.exists(cache_dir):
            return jsonify({"success": True, "data": {"message": "缓存目录不存在"}})

        # 保护已下载漫画封面
        protected = {"cover_cache.json"}
        try:
            for c in comic_manager.get_downloaded_comics():
                if c.get("cover_path"):
                    protected.add(os.path.basename(c["cover_path"]))
        except Exception:
            pass

        original_size = _get_dir_size(cache_dir)
        deleted = 0
        for item in os.listdir(cache_dir):
            if item in protected:
                continue
            item_path = os.path.join(cache_dir, item)
            try:
                if os.path.isfile(item_path):
                    os.remove(item_path)
                    deleted += 1
                elif os.path.isdir(item_path):
                    shutil.rmtree(item_path)
            except Exception:
                continue

        final_size = _get_dir_size(cache_dir)
        cleared = original_size - final_size
        return jsonify({"success": True, "data": {
            "cleared_size": cleared,
            "cleared_size_mb": round(cleared / 1048576, 2),
            "remaining_size_mb": round(final_size / 1048576, 2),
            "message": f"成功清理了 {round(cleared / 1048576, 2)} MB 的缓存",
        }})
    except Exception as e:
        return jsonify({"success": False, "message": f"清理缓存失败: {str(e)}"})


# ─── 设置 API ──────────────────────────────────────────────

@app.route("/api/settings")
def get_settings():
    try:
        from core.models.database import get_system_config, set_system_config
        keys = ["max_concurrent_downloads", "auto_cleanup_cache", "cache_size_limit", "image_quality",
                "theme", "theme_night_start", "theme_night_end", "show_hints",
                "app_icon",
                "proxy_enabled", "proxy_url", "search_result_limit",
                "search_tag_limit", "default_sort", "search_bar_behavior",
                "hide_downloaded_in_search", "hide_finished_in_search", "search_apply_blocks",
                "reader_chapter_toast", "shelf_sort",
                "recommend_enabled", "recommend_count", "recommend_basis", "recommend_custom",
                # P2：智能关联 + 标签池 + 策略
                "smart_association", "pref_min_hits", "pref_max_tags",
                "active_tag_lib", "hide_irrelevant",
                "manual_tags", "hated_tags", "ignored_tags",
                "search_strategy", "recommend_strategy",
                # P2：展示用计数
                "tag_count", "exp_tag_count", "user_ignored_csv",
                ]
        configs = {}
        for k in keys:
            configs[k] = get_system_config(k) or ""
        # 把 ignored_tags 同步到 user_ignored_csv（配置页和 JS 都直接读 user_ignored_csv），保持双向一致
        try:
            if not configs.get("user_ignored_csv") and configs.get("ignored_tags"):
                configs["user_ignored_csv"] = configs["ignored_tags"]
                set_system_config("user_ignored_csv", configs["ignored_tags"])
        except Exception:
            pass
        # tag_count / exp_tag_count：根据库的实时条数覆盖，保证设置页/关于页看到最新（因为外部操作不会都更新到 pref）
        try:
            configs["tag_count"] = str(get_tag_library_count(exp=False))
            configs["exp_tag_count"] = str(get_tag_library_count(exp=True))
            set_system_config("tag_count", configs["tag_count"])
            set_system_config("exp_tag_count", configs["exp_tag_count"])
        except Exception:
            pass
        return jsonify({"success": True, "data": configs})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

@app.route("/api/settings", methods=["POST"])
def save_settings():
    try:
        from core.models.database import set_system_config
        data = request.get_json(force=True, silent=True) or {}
        # 仅允许已知配置键写入，防止客户端污染 system_config 表或写入非预期键
        ALLOWED = {
            "max_concurrent_downloads", "auto_cleanup_cache", "cache_size_limit", "image_quality",
            "theme", "theme_night_start", "theme_night_end", "show_hints",
            "app_icon",
            "proxy_enabled", "proxy_url", "search_result_limit",
            "search_tag_limit", "default_sort", "search_bar_behavior",
            "hide_downloaded_in_search", "hide_finished_in_search", "search_apply_blocks",
            "reader_chapter_toast", "shelf_sort",
            "recommend_enabled", "recommend_count", "recommend_basis", "recommend_custom",
            # P2：智能关联 + 标签池 + 策略
            "smart_association", "pref_min_hits", "pref_max_tags",
            "active_tag_lib", "hide_irrelevant",
            "manual_tags", "hated_tags", "ignored_tags",
            "search_strategy", "recommend_strategy",
            # P2：展示用计数（可读写，写入时只做白名单接受不强制校验值）
            "tag_count", "exp_tag_count", "user_ignored_csv",
        }
        for k, v in data.items():
            if k not in ALLOWED:
                continue
            # 列表/字典类配置（如推荐依据、自定义推荐内容）以 JSON 存储，便于前端解析
            if isinstance(v, (list, dict)):
                set_system_config(k, json.dumps(v, ensure_ascii=False))
            else:
                set_system_config(k, str(v))
        # 推荐相关配置变更 → 失效推荐缓存，下次打开搜索页即生效
        if any(k.startswith("recommend_") for k in data.keys()):
            _recommend_cache["payload"] = None
            _recommend_cache["sig"] = ""
        # 下载并发数变更 → 重建下载线程池（下次提交即按新上限；在飞任务不受影响）
        if "max_concurrent_downloads" in data:
            global _dl_executor
            old = _dl_executor
            _dl_executor = None
            if old is not None:
                old.shutdown(wait=False)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ─── 拉黑 / 别名 API ──────────────────────────────────────

@app.route("/api/blocklist", methods=["GET"])
def list_blocklist():
    try:
        return jsonify({"success": True, "data": get_blocks()})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/blocklist", methods=["POST"])
def create_block():
    try:
        data = request.get_json(force=True, silent=True) or {}
        bt = data.get("type")
        val = (data.get("value") or "").strip()
        if bt not in ("author", "work", "tag"):
            return jsonify({"success": False, "message": "type 无效（应为 author/work/tag）"})
        if not val:
            return jsonify({"success": False, "message": "value 不能为空"})
        bid = add_block(bt, val, data.get("note"))
        hits = get_local_block_hits(bt, val)
        return jsonify({"success": True, "data": {"id": bid}, "local_hits": hits})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/blocklist/<int:bid>", methods=["DELETE"])
def delete_block(bid):
    try:
        return jsonify({"success": remove_block(bid)})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/blocklist/by-value", methods=["DELETE"])
def delete_block_by_value():
    try:
        data = request.get_json(force=True, silent=True) or {}
        bt = data.get("type")
        val = (data.get("value") or "").strip()
        if bt not in ("author", "work", "tag"):
            return jsonify({"success": False, "message": "type 无效（应为 author/work/tag）"})
        return jsonify({"success": remove_block_by_value(bt, val)})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/blocklist/preview")
def preview_block():
    """点击屏蔽项时预览：work 返回作品详情，author 返回该作者作品列表，tag 返回含该标签的作品列表。"""
    try:
        bt = request.args.get("type")
        val = (request.args.get("value") or "").strip()
        if bt not in ("author", "work", "tag") or not val:
            return jsonify({"success": False, "message": "参数无效"})
        limit = request.args.get("limit", "8").strip()
        try:
            limit = max(1, min(24, int(limit)))
        except (ValueError, TypeError):
            limit = 8

        if bt == "work":
            try:
                jm_id = int(val)
            except (ValueError, TypeError):
                return jsonify({"success": False, "message": "作品 ID 必须是数字"})
            info = jm_crawler.get_comic_info(jm_id)
            if not info:
                return jsonify({"success": False, "message": "未找到该作品"})
            return jsonify({"success": True, "type": bt, "data": [info]})

        if bt == "author":
            results = []
            seen = set()
            for a in expand_author(val):
                for c in jm_crawler.search_by_author(a, max_total=limit):
                    cid = c.get("id")
                    if cid and cid not in seen:
                        seen.add(cid)
                        results.append(c)
            results = postprocess_results(results)[:limit]
            return jsonify({"success": True, "type": bt, "data": results})

        # tag
        expanded = expand_tags([val])
        results, _ = jm_crawler.search_by_tags(expanded, mode="or", max_total=limit, input_tags=[val])
        results = postprocess_results(results)[:limit]
        return jsonify({"success": True, "type": bt, "data": results})
    except Exception as e:
        return jsonify({"success": False, "message": f"预览失败: {str(e)}"})


@app.route("/api/blocklist/affects")
def blocklist_affects():
    """返回影响某本地漫画（按其 jm_id 与 author）的拉黑记录 id 列表，供一键取消。"""
    try:
        jm_id = request.args.get("jm_id")
        author = (request.args.get("author") or "").strip()
        try:
            jm_id = int(jm_id)
        except (ValueError, TypeError):
            return jsonify({"success": False, "message": "jm_id 无效"})
        ids = []
        for r in get_blocks():
            if r["block_type"] == "work" and r["value"].isdigit() and int(r["value"]) == jm_id:
                ids.append(r["id"])
            elif r["block_type"] == "author" and author and normalize_author(author) == r["value"]:
                ids.append(r["id"])
        return jsonify({"success": True, "data": ids})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/aliases", methods=["GET"])
def list_aliases():
    try:
        t = request.args.get("type")
        return jsonify({"success": True, "data": get_aliases(t)})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/aliases", methods=["POST"])
def create_alias():
    try:
        data = request.get_json(force=True, silent=True) or {}
        t = data.get("type")
        alias = (data.get("alias") or "").strip()
        canon = (data.get("canonical") or "").strip()
        if t not in ("tag", "author"):
            return jsonify({"success": False, "message": "type 无效（应为 tag/author）"})
        if not alias or not canon:
            return jsonify({"success": False, "message": "alias 与 canonical 均不能为空"})
        aid = add_alias(t, alias, canon)
        return jsonify({"success": True, "data": {"id": aid}})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/aliases/<int:aid>", methods=["DELETE"])
def delete_alias_route(aid):
    try:
        return jsonify({"success": remove_alias(aid)})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/aliases/suggestions", methods=["GET"])
def alias_suggestions():
    try:
        return jsonify({"success": True, "data": suggest_aliases()})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ─── 漫画关系状态 / 收藏 / 读完 API（对齐 Android v2.5） ────

@app.route("/api/status/<int:jm_id>")
def comic_status_route(jm_id):
    """读单本关系状态（位掩码 + 快照字段）。"""
    try:
        st = get_comic_status(jm_id)
        if st is None:
            return jsonify({"success": True, "data": None})
        return jsonify({"success": True, "data": st})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/status/favorites")
def favorite_ids_route():
    """收藏的 jm 号列表。"""
    try:
        return jsonify({"success": True, "data": get_favorite_ids()})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/status/<int:jm_id>/favorite", methods=["POST"])
def toggle_favorite_route(jm_id):
    """翻转收藏（返回新态 favorite: true/false）。body 可带可选 fav 显式设值。"""
    try:
        req = request.get_json(force=True, silent=True) or {}
        if "fav" in req:
            set_comic_favorite(jm_id, bool(req["fav"]))
            now = bool(req["fav"])
        else:
            now = toggle_comic_favorite(jm_id)
        return jsonify({"success": True, "favorite": now})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/status/<int:jm_id>/finished", methods=["POST"])
def toggle_finished_route(jm_id):
    """翻转读完（返回新态 finished）。body 可带可选 finished 显式设值。"""
    try:
        req = request.get_json(force=True, silent=True) or {}
        if "finished" in req:
            set_comic_finished(jm_id, bool(req["finished"]))
            now = bool(req["finished"])
        else:
            now = toggle_comic_finished(jm_id)
        # 回收站：标已看完 → 写 7 天宽限期（不删本地，书架「看完」区仍可直接读）；
        # 取消已看完 → 连带清掉宽限标记，避免残留被回收站误收。
        if now:
            mark_finished_recycle(jm_id)
        else:
            clear_recycle_marks(jm_id)
        return jsonify({"success": True, "finished": now})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/status/<int:jm_id>/view", methods=["POST"])
def bump_status_view_route(jm_id):
    """查看次数 +1（智能缓存/排序用）。"""
    try:
        bump_status_view_count(jm_id)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/status/<int:jm_id>/read", methods=["POST"])
def touch_status_read_route(jm_id):
    """刷新最近阅读时间（body: last_read_at 可选，默认当前毫秒）。"""
    try:
        req = request.get_json(force=True, silent=True) or {}
        ts = req.get("last_read_at") or int(time.time() * 1000)
        touch_status_read(jm_id, int(ts))
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/status", methods=["DELETE"])
def clear_comic_status_route():
    """清空全部关系状态（隐私「清空阅读画像」）。"""
    try:
        n = clear_all_comic_status()
        return jsonify({"success": True, "deleted": n})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ─── 回收站 / 宽限期（对齐 Android RecycleScreen + RecycleManager）───
# 两态条目：
#   宽限中（trashed=0、recycle_at>0）→ 本地还在，点进去能直接读，剩余 N 天
#   已删本地（trashed=1）→ 只剩记录与封面，重新下载即可拾回

def _recycle_item_view(st):
    """状态行 → 前端回收站条目。"""
    jm_id = st["book_id"]
    cover_url = st.get("cover_url") or ""
    return {
        "jm_id": jm_id,
        "title": st.get("title") or f"JM-{jm_id}",
        "author": st.get("author") or "",
        "cover_url": cover_url,
        "cover_api": f"/api/cover/downloaded/{jm_id}" if cover_url else "",
        "trashed": bool(st.get("trashed")),
        "recycle_at": int(st.get("recycle_at") or 0),
        "remain_days": int(st.get("remain_days") or 0),
        "has_local": comic_manager.is_comic_downloaded(jm_id),
    }


@app.route("/recycle")
def recycle_page():
    return render_template("recycle.html")


@app.route("/api/recycle")
def get_recycle_list():
    """回收站列表：宽限中 + 已删本地。"""
    try:
        items = [_recycle_item_view(st) for st in list_recycle_items()]
        return jsonify({"success": True, "data": {
            "items": items,
            "grace_days": RECYCLE_RETENTION_DAYS,
            "pending": sum(1 for i in items if not i["trashed"]),
            "trashed": sum(1 for i in items if i["trashed"]),
        }})
    except Exception as e:
        return jsonify({"success": False, "message": f"获取回收站失败: {str(e)}"})


@app.route("/api/recycle/scan", methods=["POST"])
def scan_recycle_expiry():
    """扫描过期项：宽限期已到的 → 删本地图片（保留记录与封面）+ 标已删本地。

    App 启动与进入回收站页时各调一次（对齐 Android checkRecycleExpiry 的调用时机）。
    """
    try:
        expired = get_expired_recycle_ids()
        done = []
        for jm_id in expired:
            if comic_manager.delete_local_keep_record(jm_id):
                clear_status_bits(jm_id, BIT_LOCAL)
                set_trashed(jm_id, True)
                done.append(jm_id)
        return jsonify({"success": True, "expired": done, "count": len(done)})
    except Exception as e:
        return jsonify({"success": False, "message": f"扫描失败: {str(e)}"})


@app.route("/api/recycle/<int:jm_id>/restore", methods=["POST"])
def restore_from_recycle(jm_id):
    """恢复：宽限中 → 取消宽限标记回到书架；已删本地 → 告知需重新下载拾回。"""
    try:
        marks = get_recycle_marks(jm_id)
        if marks["trashed"]:
            return jsonify({"success": True, "need_download": True,
                            "message": "本地内容已删除，重新下载即可拾回"})
        clear_recycle_marks(jm_id)
        return jsonify({"success": True, "need_download": False, "message": "已恢复到书架"})
    except Exception as e:
        return jsonify({"success": False, "message": f"恢复失败: {str(e)}"})


@app.route("/api/recycle/<int:jm_id>", methods=["DELETE"])
def delete_from_recycle(jm_id):
    """彻底删除：记录 + 分类关联 + 本地内容全清（不可恢复）。"""
    try:
        ok = comic_manager.delete_completely(jm_id)
        if ok:
            delete_comic_status(jm_id)
        return jsonify({"success": ok, "message": "已彻底删除" if ok else "删除失败"})
    except Exception as e:
        return jsonify({"success": False, "message": f"彻底删除失败: {str(e)}"})


@app.route("/api/recycle/clear", methods=["POST"])
def clear_recycle():
    """清空回收站：把所有「已删本地」的条目彻底删除（宽限中的不动）。"""
    try:
        n = 0
        for st in list_recycle_items():
            if st.get("trashed"):
                if comic_manager.delete_completely(st["book_id"]):
                    delete_comic_status(st["book_id"])
                    n += 1
        return jsonify({"success": True, "deleted": n})
    except Exception as e:
        return jsonify({"success": False, "message": f"清空回收站失败: {str(e)}"})


# ─── 订阅 API（对齐 Android v2.5） ─────────────────────────

@app.route("/api/subscriptions", methods=["GET"])
def list_subscriptions_route():
    try:
        return jsonify({"success": True, "data": get_subscriptions()})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/subscriptions", methods=["POST"])
def add_subscription_route():
    """订阅作者。body: {author}。"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        author = (data.get("author") or "").strip()
        if not author:
            return jsonify({"success": False, "message": "author 不能为空"})
        existing = find_subscription_by_author(author)
        if existing:
            return jsonify({"success": True, "id": existing["id"], "exists": True})
        sid = upsert_subscription(author)
        return jsonify({"success": True, "id": sid})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/subscriptions/<int:sid>", methods=["DELETE"])
def remove_subscription_route(sid):
    """按 id 删除订阅（不含其新作记录，如需一并清可另调 /api/subscriptions/<sid>/updates DELETE）。"""
    try:
        delete_subscription(sid)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/subscriptions/<int:sid>/updates", methods=["DELETE"])
def remove_subscription_updates_route(sid):
    """删除某订阅已登记的新作记录。body: {author}。"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        author = data.get("author")
        if not author:
            return jsonify({"success": False, "message": "author 不能为空"})
        delete_sub_updates_by_author(author)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/subscriptions/<int:sid>/clear-new", methods=["POST"])
def clear_sub_new_route(sid):
    """新作计数清零（用户已查看订阅更新）。"""
    try:
        clear_subscription_new_count(sid)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/sub_updates", methods=["GET"])
def list_sub_updates_route():
    """订阅新作记录。query: unseen=1 仅取未查看。"""
    try:
        unseen = request.args.get("unseen") == "1"
        data = get_sub_updates(unseen_only=unseen)
        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/sub_updates/seen", methods=["POST"])
def mark_sub_update_seen_route():
    """标记新作已看。body: {book_id} 或 {all:true}。"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        if data.get("all"):
            mark_all_sub_updates_seen()
        else:
            book_id = data.get("book_id")
            if not book_id:
                return jsonify({"success": False, "message": "book_id 或 all 必填"})
            mark_sub_update_seen(int(book_id))
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/sub_updates", methods=["DELETE"])
def clear_sub_updates_route():
    """清空全部订阅新作记录。"""
    try:
        n = clear_all_sub_updates()
        return jsonify({"success": True, "deleted": n})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ─── 标签库 API（对齐 Android v2.5） ──────────────────────

@app.route("/api/tag-library")
def get_tag_library_route():
    """兴趣标签库快照（按 count 降序）。query: exp=1 查实验库。"""
    try:
        exp = request.args.get("exp") == "1"
        return jsonify({"success": True, "data": get_tag_library(exp=exp)})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/tag-library/count")
def get_tag_library_count_route():
    """标签库计数。query: exp=1 查实验库。"""
    try:
        exp = request.args.get("exp") == "1"
        return jsonify({"success": True, "data": get_tag_library_count(exp=exp)})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/tag-library", methods=["DELETE"])
def clear_tag_library_route():
    """清空标签库。body/query: exp=1 清实验库。"""
    try:
        exp = request.args.get("exp") == "1"
        n = clear_tag_library(exp=exp)
        return jsonify({"success": True, "deleted": n})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/tag-library/bump", methods=["POST"])
def bump_tag_route():
    """标签命中 +1。body: {tag, exp?}。"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        tag = (data.get("tag") or "").strip()
        if not tag:
            return jsonify({"success": False, "message": "tag 不能为空"})
        bump_tag(tag, exp=bool(data.get("exp")))
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ─── P2：标签意见（tag-opinions / tag-library / tag-stats）7+ 个 API ───

@app.route("/api/tag-opinions", methods=["GET"])
def get_tag_opinions_route():
    """一次性返回 manual/hated/ignored 三池 CSV + 三池各自 size。返回结构与前端 parseCsvSet 对齐。"""
    try:
        from core.models.database import DEFAULT_IRRELEVANT_TAGS as DEFAULT_IRRELEVANT_TAGS

        manual_list = get_manual_tags()
        hated_list = get_hated_tags()
        ignored_user_list = list(get_user_ignored_tags())

        def _to_csv(arr):
            return serialize_csv_tags(arr) if arr else ""

        def _merged_irrel_csv():
            merged = set(DEFAULT_IRRELEVANT_TAGS)
            merged.update(get_user_ignored_tags())
            return ",".join(sorted(merged))

        return jsonify({
            "success": True,
            "data": {
                "manual_csv": _to_csv(manual_list),
                "manual_size": len(manual_list),
                "hated_csv": _to_csv(hated_list),
                "hated_size": len(hated_list),
                "ignored_csv": _merged_irrel_csv(),  # 种子 + 用户
                "ignored_size": len(DEFAULT_IRRELEVANT_TAGS) + len(ignored_user_list),
                "user_ignored_csv": _to_csv(ignored_user_list),
                "user_ignored_size": len(ignored_user_list),
            },
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/tag-opinions/cycle", methods=["POST"])
def cycle_tag_opinion_route():
    """三态循环切换：normal → like → hate → normal（对立互剔）。body: {tag}。"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        tag = (data.get("tag") or "").strip()
        if not tag:
            return jsonify({"success": False, "message": "tag 不能为空"})
        new_state = cycle_tag_opinion(tag)
        return jsonify({"success": True, "new_state": new_state, "tag": tag.lower()})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/tag-opinions/bulk", methods=["POST"])
def bulk_tag_opinions_route():
    """
    批量写入三池/追加入库。body:
      { tags: CSV 字符串或数组, op: like|hate|ignore|add_to_library }
    返回新增/跳过统计。
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        tags = data.get("tags") or data.get("tag") or ""
        op = (data.get("op") or "").lower().strip()
        if op in {"add_to_library"} and tags:
            library = (data.get("library") or "interest").lower()
            if library == "experimental":
                # 对实验库用 bump_tag(exp=True)
                items = parse_csv_tags(tags if isinstance(tags, str) else ",".join(tags))
                added = 0; skipped = 0
                for t in items:
                    try: bump_tag(t, exp=True); added += 1
                    except Exception: skipped += 1
                return jsonify({"success": True, "added": added, "skipped": skipped, "pool": "experimental"})
        result = bulk_tag_opinions(tags, op)
        return jsonify({"success": True, **result})
    except ValueError as ve:
        return jsonify({"success": False, "message": str(ve)})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/tag-opinions/toggle-ignored", methods=["POST"])
def toggle_ignored_tag_route():
    """切换标签的"用户手动无关"态。body: {tag} → 返回 added 布尔。"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        tag = (data.get("tag") or "").strip()
        if not tag:
            return jsonify({"success": False, "message": "tag 不能为空"})
        added = toggle_ignored_tag(tag)
        return jsonify({"success": True, "added": added, "tag": tag.lower()})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/tag-opinions/add", methods=["POST"])
def add_tag_manually_route():
    """手动添加标签：bump 进兴趣库 + 按 opinion(normal/like/hate/ignored) 写入。"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        tag = (data.get("tag") or "").strip()
        if not tag:
            return jsonify({"success": False, "message": "tag 不能为空"})
        opinion = (data.get("opinion") or "normal").lower()
        if opinion not in {"normal", "like", "hate", "ignored"}:
            opinion = "normal"
        result = add_tag_manually(tag, opinion=opinion)
        return jsonify({"success": True, **result})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/tag-opinions", methods=["DELETE"])
def reset_tag_preferences_route():
    """清空 manual_tags + hated_tags（≠清空兴趣库）。返回删除的两个 count。"""
    try:
        m, h = reset_tag_preferences()
        return jsonify({"success": True, "deleted": {"manual": m, "hated": h}})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/tag-library/manage", methods=["GET", "POST"])
def tag_library_manage_route():
    """
    管理页聚合：POST（推荐）body:
        { library: interest|experimental, mode: all|like|hate|irrelevant,
          search: str, hide_irrelevant: bool, page: int, page_size: int }
    也兼容 GET（?library=&mode=&search=&hide_irrelevant=&page=&page_size=）。
    返回：items/total/total_by_mode/tag_count/exp_tag_count 与 JS initTagsManagePage 一致。
    """
    try:
        if request.method == "POST":
            data = request.get_json(force=True, silent=True) or {}
            library = (data.get("library") or "interest").lower()
            mode = (data.get("mode") or "all").lower()
            search = (data.get("search") or "")
            hide_irrelevant = bool(data.get("hide_irrelevant", True))
            page = data.get("page") or 1
            page_size = data.get("page_size") or 200
        else:
            library = (request.args.get("library") or "interest").lower()
            mode = (request.args.get("mode") or "all").lower()
            search = (request.args.get("search") or request.args.get("q") or "")
            hide_irrelevant = (request.args.get("hide_irrelevant") or request.args.get("hide_irrel") or "") in {"1", "true", "True", "yes"}
            page = request.args.get("page") or 1
            page_size = request.args.get("page_size") or request.args.get("limit") or 200
        payload = query_tag_library_paginated(
            library=library, mode=mode, search=search,
            hide_irrelevant=hide_irrelevant, page=page, page_size=page_size,
        )
        return jsonify({"success": True, "data": payload})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/tag-library/reset-interest", methods=["POST"])
def tag_library_reset_interest_route():
    """清空兴趣库，并以 DEFAULT_SAMPLE_TAGS 重建。返回插入条数 + tag_count。"""
    try:
        inserted = reset_interest_to_default_samples()
        tag_count = get_tag_library_count(exp=False)
        exp_tag_count = get_tag_library_count(exp=True)
        return jsonify({
            "success": True,
            "inserted": inserted,
            "tag_count": tag_count,
            "exp_tag_count": exp_tag_count,
            "message": f"已重置到默认样本（写入 {inserted} 条，兴趣库共 {tag_count} 条）",
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/tag-library/clear", methods=["POST"])
def tag_library_clear_route():
    """清空指定库。body: { library: interest|experimental }"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        library = (data.get("library") or "").lower()
        if library not in {"interest", "experimental"}:
            return jsonify({"success": False, "message": "library 必须是 interest 或 experimental"})
        exp = (library == "experimental")
        deleted_rows = clear_tag_library(exp=exp)
        tag_count = get_tag_library_count(exp=False)
        exp_tag_count = get_tag_library_count(exp=True)
        # 同步保存到 pref，确保设置页马上读到正确数字
        try:
            from core.models.database import set_system_config as _ssc
            _ssc("tag_count", str(tag_count)); _ssc("exp_tag_count", str(exp_tag_count))
        except Exception:
            pass
        which = "实验库" if exp else "兴趣库"
        return jsonify({
            "success": True,
            "library": library,
            "deleted": deleted_rows,
            "tag_count": tag_count,
            "exp_tag_count": exp_tag_count,
            "message": f"{which}已清空（删除 {deleted_rows} 条）",
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/tag-stats", methods=["GET"])
def get_tag_stats_route():
    """
    汇总统计：收藏×喜欢/讨厌、拉黑×喜欢/讨厌 四组 Top 标签。
    返回 shape：
      { success, data: {
          favorite_total, blocked_total,
          favorite_liked: [{tag,count}], favorite_hated: [...],
          blocked_liked: [...], blocked_hated: [...] } }
    """
    try:
        try:
            limit = max(5, min(200, int(request.args.get("limit") or 80)))
        except ValueError:
            limit = 80
        fav_all = [{"tag": t, "count": c} for t, c in load_tag_stats("favorite", limit=limit * 4)]
        blk_all = [{"tag": t, "count": c} for t, c in load_tag_stats("blacklist", limit=limit * 4)]

        manual = set(get_manual_tags())
        hated = set(get_hated_tags())

        fav_tag_set = {x["tag"] for x in fav_all}
        blk_tag_set = {x["tag"] for x in blk_all}
        fav_like = [x for x in fav_all if x["tag"] in manual][:limit]
        fav_hate = [x for x in fav_all if x["tag"] in hated][:limit]
        blk_like = [x for x in blk_all if x["tag"] in manual][:limit]
        blk_hate = [x for x in blk_all if x["tag"] in hated][:limit]

        # 合计多少本收藏/拉黑（走 comic_status 位掩码计数）
        try:
            from core.models.database import _get_conn as _conn
            c = _conn()
            fav_total = c.execute("SELECT COUNT(*) n FROM comic_status WHERE (status & ?) != 0", (1 << 3,)).fetchone()["n"]
            blk_total = c.execute("SELECT COUNT(*) n FROM comic_status WHERE (status & ?) != 0", (1 << 4,)).fetchone()["n"]
        except Exception:
            fav_total = 0; blk_total = 0

        return jsonify({
            "success": True,
            "data": {
                "favorite_total": fav_total,
                "blocked_total": blk_total,
                "favorite_liked": fav_like,
                "favorite_hated": fav_hate,
                "blocked_liked": blk_like,
                "blocked_hated": blk_hate,
                # 保留原来的 flat favorite/blacklist 给未来可能的兼容
                "favorite": fav_all[:limit],
                "blacklist": blk_all[:limit],
            },
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ─── 入口（源码直接运行时用） ──────────────────────────────

def main():
    # 仅本地回环监听；调试器必须显式通过 FLASK_DEBUG=1 开启，永不对外暴露（避免 Werkzeug 调试器 RCE）
    debug = os.environ.get("FLASK_DEBUG") == "1"
    app.run(debug=debug, host="127.0.0.1", port=5000)


if __name__ == "__main__":
    main()
