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
from datetime import datetime

from flask import Flask, render_template, jsonify, request, send_file, redirect
from flask_cors import CORS

from core.config import (
    TEMPLATE_DIR, STATIC_DIR, APP_VERSION,
    TEMP_CACHE_DIR, DOWNLOAD_DIR, BASE_DIR,
)
from core.models.database import (
    init_database, get_system_config,
    get_all_categories, get_category_tree,
    create_category, update_category, delete_category,
    get_category_descendant_ids,
    get_comic_categories, set_comic_categories, set_category_parent,
    add_search_history, add_download_history, get_download_history,
    delete_download_history, clear_download_history,
    add_block, remove_block, remove_block_by_value, get_blocks, get_blocked_sets, get_local_block_hits,
    add_alias, remove_alias, get_aliases, normalize_author,
    get_search_history_aggregated,
)
from core.services.filter_service import (
    postprocess_results, expand_tags, expand_author, suggest_aliases,
)

# ─── Flask 初始化 ──────────────────────────────────────────

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)
CORS(app)


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


# ─── 页面路由 ──────────────────────────────────────────────

@app.route("/")
def index():
    return redirect("/home")

@app.route("/home")
def home_page():
    return render_template("home.html")

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


@app.route("/downloads")
def downloads_page():
    return render_template("downloads.html")


# ─── 搜索 API ──────────────────────────────────────────────

@app.route("/api/search/jm/<int:jm_id>")
def search_by_jm_id(jm_id):
    try:
        info = jm_crawler.get_comic_info(jm_id)
        if info:
            add_search_history("jm_id", str(jm_id), 1)
            return jsonify({"success": True, "data": info})
        return jsonify({"success": False, "message": "未找到对应的漫画"})
    except Exception as e:
        return jsonify({"success": False, "message": f"搜索失败: {str(e)}"})


@app.route("/api/search/keyword")
def search_by_keyword():
    keyword = request.args.get("keyword", "").strip()
    sort_order = request.args.get("sort", "desc")
    order_by = request.args.get("order_by", "mr")
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
        add_search_history("keyword", keyword, len(results))
        return jsonify({"success": True, "data": results})
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
        # 别名展开：每个标签一并检索其同义词
        expanded = expand_tags(tag_list)
        # 搜索优先级：input=优先把用户实际输入的名字排前面（默认）
        search_priority = (get_system_config("search_priority") or "input").strip().lower()
        input_tags = tag_list if search_priority == "input" else None
        results, stats = jm_crawler.search_by_tags(expanded, mode=mode, max_total=limit, input_tags=input_tags)
        results = postprocess_results(results)
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
            # 搜索优先级：input=优先把用户实际输入的名字排前面（默认）
            search_priority = (get_system_config("search_priority") or "input").strip().lower()
            input_tags = tag_list if search_priority == "input" else None
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
        data.sort(key=lambda x: jm_crawler._parse_count(x.get("favorites", 0)), reverse=True)
        data = data[:count]

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
        if comic_manager.is_comic_downloaded(jm_id):
            for c in comic_manager.get_downloaded_comics():
                if c["id"] == jm_id and c.get("cover_path"):
                    if os.path.exists(c["cover_path"]):
                        return send_file(c["cover_path"])
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
        for c in comic_manager.get_downloaded_comics():
            if c["id"] == jm_id and c.get("cover_path"):
                if os.path.exists(c["cover_path"]):
                    return send_file(c["cover_path"])
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

        def _task():
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

        t = threading.Thread(target=_task, daemon=True)
        t.start()
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

            def _task(_id=jm_id, _info=comic_info, _did=download_id, _cids=list(category_ids)):
                try:
                    download_manager.download_comic(
                        _id, _info,
                        lambda p, s, m: _update_progress(_did, p, s, m),
                    )
                    if _cids:
                        set_comic_categories(_id, _cids)
                    add_download_history(_id, _info.get("title", ""), "completed")
                    threading.Timer(2.0, _cleanup_progress, args=(_did,)).start()
                except Exception as e:
                    _update_progress(_did, 0, "error", str(e))
                    add_download_history(_id, _info.get("title", ""), "failed", str(e))

            t = threading.Thread(target=_task, daemon=True)
            t.start()
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


@app.route("/api/categories/<int:cat_id>", methods=["DELETE"])
def remove_category(cat_id):
    delete_category(cat_id)
    return jsonify({"success": True})


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
        comics = comic_manager.get_downloaded_comics(category_id=category_id)
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
        title = f"JM-{jm_id}"
        for c in comic_manager.get_downloaded_comics():
            if c["id"] == jm_id:
                title = c["title"]
                break
        return {
            "success": True,
            "data": {
                "title": title, "chapters": chapters,
                "current_chapter": chapters[0]["id"],
                "current_chapter_pages": chapters[0]["pages"],
                "total_chapters": len(chapters),
                "comic_path": chapters[0]["path"],
            },
        }
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
        title = f"JM-{jm_id}"
        for c in comic_manager.get_downloaded_comics():
            if c["id"] == jm_id:
                title = c["title"]
                break
        return {
            "success": True,
            "data": {
                "title": title, "chapters": chapters,
                "current_chapter": target["id"],
                "current_chapter_pages": target["pages"],
                "total_chapters": len(chapters),
                "comic_path": target["path"],
            },
        }
    except Exception as e:
        return jsonify({"success": False, "message": f"获取章节数据失败: {str(e)}"})


@app.route("/api/comic/<int:jm_id>/page/<int:page_num>")
def get_comic_page(jm_id, page_num):
    try:
        chapter_id = request.args.get("chapter", None)
        page_path = comic_manager.get_comic_page_path(jm_id, page_num, chapter_id)
        if page_path and os.path.exists(page_path):
            return send_file(page_path)
        return jsonify({"success": False, "message": "页面不存在"})
    except Exception as e:
        return jsonify({"success": False, "message": f"获取页面失败: {str(e)}"})


@app.route("/api/delete/<int:jm_id>", methods=["DELETE"])
def delete_comic(jm_id):
    try:
        ok = comic_manager.delete_comic(jm_id)
        return jsonify({"success": ok, "message": "删除成功" if ok else "删除失败"})
    except Exception as e:
        return jsonify({"success": False, "message": f"删除失败: {str(e)}"})


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
                "theme", "proxy_enabled", "proxy_url", "search_result_limit", "search_priority",
                "hide_page_chapter", "show_block_hits", "search_tag_limit", "reader_chapter_toast",
                "recommend_enabled", "recommend_count", "recommend_basis", "recommend_custom"]
        configs = {}
        for k in keys:
            configs[k] = get_system_config(k) or ""
        return jsonify({"success": True, "data": configs})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

@app.route("/api/settings", methods=["POST"])
def save_settings():
    try:
        from core.models.database import set_system_config
        data = request.get_json(force=True, silent=True) or {}
        for k, v in data.items():
            # 列表/字典类配置（如推荐依据、自定义推荐内容）以 JSON 存储，便于前端解析
            if isinstance(v, (list, dict)):
                set_system_config(k, json.dumps(v, ensure_ascii=False))
            else:
                set_system_config(k, str(v))
        # 推荐相关配置变更 → 失效推荐缓存，下次打开搜索页即生效
        if any(k.startswith("recommend_") for k in data.keys()):
            _recommend_cache["payload"] = None
            _recommend_cache["sig"] = ""
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


# ─── 入口（源码直接运行时用） ──────────────────────────────

def main():
    app.run(debug=True, host="0.0.0.0", port=5000)


if __name__ == "__main__":
    main()
