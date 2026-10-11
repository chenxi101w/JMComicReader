# -*- coding: utf-8 -*-
"""v2.13.0 书架分类树重构 —— 静态自检五件套

（文件名保留 v2123 是历史沿革，本文件已随版本持续扩展：
 v2.12.3 书架布局 → v2.12.4 小字清理 → v2.13.0 分类树合一）
"""
import ast, io, os, re, sys, tempfile, threading, json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
FAIL = []


def check(name, ok, detail=""):
    print(("  [PASS] " if ok else "  [FAIL] ") + name + (("  -> " + detail) if detail and not ok else ""))
    if not ok:
        FAIL.append(name)


print("=" * 62)
print("v2.13.0 书架分类树重构 · 静态自检")
print("=" * 62)

# ── 1. app.js 符号一致性（语法本身由 node --check 把关） ──
print("\n[1] app.js 符号一致性")
js = io.open("web/static/js/app.js", encoding="utf-8").read()
# v2.13.0 新增
check("VIRTUAL_ALL 已定义", "VIRTUAL_ALL" in js)
check("VIRTUAL_FAV 已定义", "VIRTUAL_FAV" in js)
check("VIRTUAL_UNCAT 已定义", "VIRTUAL_UNCAT" in js)
check("specialRow 已定义", "function specialRow(" in js)
check("_collectSubtreeIds 已定义", "function _collectSubtreeIds(" in js)
check("_isDescendant 已定义", "function _isDescendant(" in js)
check("renameCategory 已定义", "async function renameCategory(" in js)
check("deleteCategoryFlow 已定义", "async function deleteCategoryFlow(" in js)
check("deleteCategoryFlow 走 reassign_to", "reassign_to" in js)
check("loadComics 支持 uncategorized", "/api/downloaded?uncategorized=1" in js)
check("树内含 tree-sep 分隔", 'class="tree-sep"' in js)
check("拖到子孙用 drop-invalid", "drop-invalid" in js)
check("收藏徽标用 tree-item__badge", "tree-item__badge" in js)
# v2.13.0 必须清干净的旧符号
for dead in ("renderCatNav", "_applyCatSelection", "_catPath", "openCategoryMovePicker",
             "collectDescendantIds", "Q_UNUSED_GUARD"):
    check("无 %s 残留" % dead, dead not in js)
check("moveComicToCategory 仅定义一次", js.count("async function moveComicToCategory(") == 1)
check("refreshQuickCounts 仍定义", "async function refreshQuickCounts()" in js)
check("refreshQuickCounts 写 favCountBadge", "getElementById('favCountBadge')" in js)
# 历史清理项（防止回退）
check("无 bindQuickViews 残留", "bindQuickViews" not in js)
check("无 [data-view] 选择器", "querySelectorAll('[data-view]')" not in js)
check("无 [data-count= 选择器", '[data-count="' not in js)

tpl = io.open("web/templates/library.html", encoding="utf-8").read()
need_ids = [
    "comicGrid", "catTree", "currentCatName", "currentCatCount", "emptyState",
    "treeToggleAll", "newSubBtn", "newCatBtn", "recycleEntry", "recycleBadge",
]
missing = [i for i in need_ids if ('id="%s"' % i) not in tpl]
check("library.html 关键 id 齐全", not missing, str(missing))
for cls in ("lib-layout", "lib-side", "lib-main", "lib-side__head"):
    check("library.html 含 %s" % cls, cls in tpl)
for dead in ("catNav", "catNavBlocks", "catTreeWrap", "toggleCatTree",
             "continueReadingCard", "d-cc"):
    check("library.html 无 %s" % dead, dead not in tpl)
check("app.js 无空态 p 写入", "querySelector('p').textContent" not in js)
# app.js 里引用的 DOM id 必须在模板里存在（catTree 树为主容器）
check("initLibrary 取 catTree", "getElementById('catTree')" in js)

# ── 2. CSS 一致性 ──
print("\n[2] style.css")
css = io.open("web/static/css/style.css", encoding="utf-8").read()
for alive in (".lib-layout", ".lib-side", ".lib-main", ".lib-head", ".lib-iconbtn",
              ".tree-item__acts", ".tree-item__actbtn", ".tree-item__badge",
              ".tree-sep", ".drop-invalid", ".tree-group"):
    check("%s 已定义" % alive, re.search(re.escape(alive) + r"[\s,{:]", css) is not None)
check("精致侧栏树：active 圆角药丸用 --d-grad-soft",
      bool(re.search(r"\.tree-item\.active\s*\{[^}]*--d-grad-soft", css, re.S))
      and "border-radius: 999px" in css)
check("精致侧栏树：分组小标题用 tree-group", 'class="tree-group"' in js)
for dead in (".cat-nav", ".cat-crumb", ".cat-block", ".cat-blocks", ".cat-move-btn",
             ".cat-nav-tip", ".lib-hint", ".cat-card__desc", ".d-cc"):
    check("无 %s 孤儿规则" % dead, re.search(r"^\s*" + re.escape(dead) + r"[\s,{:.]", css, re.M) is None)
check("CSS 大括号配对", css.count("{") == css.count("}"),
      "open=%d close=%d" % (css.count("{"), css.count("}")))
for var in ("--d-cat-fant", "--d-cat-mys", "--d-grad-primary", "--d-card-cover-h"):
    check("变量保留 %s" % var, var in css)

# ── 3. Jinja2 全量编译 ──
print("\n[3] Jinja2 模板编译")
try:
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("web/templates"))
    tpls = [t for t in sorted(os.listdir("web/templates")) if t.endswith(".html")]
    bad = []
    for t in tpls:
        try:
            env.get_template(t)
        except Exception as e:
            bad.append("%s: %s" % (t, e))
    check("%d 个模板全部编译通过" % len(tpls), not bad, "; ".join(bad))
except Exception as e:
    check("Jinja2 可用", False, str(e))

# ── 4. HTML 标签配对 ──
print("\n[4] library.html 标签配对")
VOID = {"br", "hr", "img", "input", "meta", "link", "source", "use", "path", "circle"}
stack, errs = [], []
for m in re.finditer(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)\b[^>]*?(/?)>", tpl):
    closing, tag, selfclose = m.group(1), m.group(2).lower(), m.group(3)
    if tag in VOID or selfclose:
        continue
    if closing:
        if not stack:
            errs.append("多余闭合 </%s>" % tag)
        elif stack[-1] != tag:
            errs.append("错配 </%s> 期望 </%s>" % (tag, stack[-1]))
        else:
            stack.pop()
    else:
        stack.append(tag)
if stack:
    errs.append("未闭合: %s" % stack)
check("标签配对无误", not errs, "; ".join(errs))
check("library.html 含 lib-head", "lib-head" in tpl)
check("library.html 未再引用 cat-manage", "cat-manage" not in tpl)

# ── 5. Flask test_client 路由 + 分类接口冒烟 ──
print("\n[5] Flask 路由 / 分类接口冒烟")
sys.path.insert(0, ROOT)
try:
    import core.models.database as db
    tmpdir = tempfile.mkdtemp(prefix="jmsmoke_")
    db.DB_FILE = os.path.join(tmpdir, "t.db")
    db._local = threading.local()
    db.database = db
    db.init_database()

    from core import app as appmod
    flask_app = appmod.app
    flask_app.config["TESTING"] = True
    c = flask_app.test_client()
    routes = [
        ("/", 302), ("/library", 200), ("/search", 200), ("/settings", 200),
        ("/recycle", 200), ("/downloads", 200),
        ("/settings/appearance", 200), ("/settings/reader", 200),
        ("/settings/search", 200), ("/settings/block", 200),
        ("/settings/tags", 200), ("/settings/download", 200),
        ("/settings/recommend", 200), ("/settings/about", 200),
        ("/settings/tags-manage", 302), ("/settings/synonyms", 302),
        ("/static/css/style.css", 200), ("/static/js/app.js", 200),
    ]
    bad = []
    for path, want in routes:
        r = c.get(path)
        if r.status_code != want:
            bad.append("%s got %s want %s" % (path, r.status_code, want))
    check("%d 条路由冒烟" % len(routes), not bad, "; ".join(bad))

    html = c.get("/library").get_data(as_text=True)
    check("/library 含 lib-layout", "lib-layout" in html)
    check("/library 含 lib-side", "lib-side" in html)
    check("/library 含 catTree", "catTree" in html)
    check("/library 含 recycleEntry", "recycleEntry" in html)
    for dead in ("catNavBlocks", "catTreeWrap", "toggleCatTree", "continueReadingCard"):
        check("/library 不含 %s" % dead, dead not in html)
    check("base 引用 style.css?v=66", "style.css?v=66" in html)
    check("base 引用 reader-logic.js?v=66", "reader-logic.js?v=66" in html)
    check("base 引用 app.js?v=66", "app.js?v=66" in html)

    # ── v2.14 翻新增补断言 ──
    js = open(os.path.join(ROOT, "web", "static", "js", "app.js"), encoding="utf-8").read()
    check("阅读器默认连排（scroll）", "Settings.get('reader_wheel_mode', 'scroll')" in js)
    check("阅读器有连排建列（buildColumn）", "function buildColumn()" in js)
    check("阅读器有滑轨（readerRail/updateRail）", "readerRail" in js and "function updateRail()" in js)
    check("阅读器有览图（ReaderLogic.buildOverviewHtml）", "ReaderLogic.buildOverviewHtml" in js)
    check("阅读器进度防抖落库", "saveProgressDebounced" in js and "/progress" in js)
    check("WebView2 全局拖拽补丁（preventDefault + 合成 drop）",
          "document.addEventListener('dragstart', function (e) {\n    e.preventDefault();\n  }, true)" in js
          and "makeDragEvent('drop'" in js)
    check("下载引擎：Registry 取代旧 _dl_progress", "_registry = _DownloadRegistry()" in js or
          ("_DownloadRegistry" in open(os.path.join(ROOT, "core", "app.py"), encoding="utf-8").read()
           and "_dl_progress" not in open(os.path.join(ROOT, "core", "app.py"), encoding="utf-8").read()))
    py_app = open(os.path.join(ROOT, "core", "app.py"), encoding="utf-8").read()
    check("下载可取消（/api/download/cancel）", "/api/download/cancel/" in py_app)
    check("下载历史带 resumable（续下）", "resumable" in py_app)
    check("封面代理（/api/cover/proxy + 负缓存）", "/api/cover/proxy/" in py_app and "_COVER_FAIL_TTL" in py_app)
    check("收藏导入导出（/api/favorites/export|import）",
          "/api/favorites/export" in py_app and "/api/favorites/import" in py_app)
    check("设置白名单含 library_columns/search_columns",
          "library_columns" in py_app and "search_columns" in py_app)
    check("桌面入口：NativeSplash + bind 端口探测",
          "class NativeSplash" in open(os.path.join(ROOT, "desktop_app.py"), encoding="utf-8").read()
          and "def _is_port_free" in open(os.path.join(ROOT, "desktop_app.py"), encoding="utf-8").read())
    r = c.post("/api/read/1/progress", json={"page": 3})
    check("POST /api/read/<id>/progress 已注册", r.status_code == 200 and r.get_json().get("success") is True)
    r = c.get("/api/favorites/export")
    check("GET /api/favorites/export 返回备份 JSON",
          r.status_code == 200 and b"favoriteBooks" in r.data)

    # 分类树 UI 依赖的三个后端契约
    r = c.get("/api/categories?tree=1")
    check("GET /api/categories?tree=1", r.status_code == 200 and r.get_json().get("success") is True)
    r = c.get("/api/downloaded?uncategorized=1")
    ok = r.status_code == 200 and isinstance(r.get_json().get("data"), list)
    check("GET /api/downloaded?uncategorized=1", ok, str(r.status_code))
    created = c.post("/api/categories", json={"name": "__selftest__"})
    cid = (created.get_json().get("data") or {}).get("id")
    check("POST /api/categories", created.status_code == 200 and cid is not None)
    if cid is not None:
        r = c.get("/api/categories/%s?count_only=1" % cid)
        check("GET /api/categories/<id>?count_only=1",
              r.status_code == 200 and "count" in (r.get_json() or {}))
        r = c.put("/api/categories/%s" % cid, json={"name": "__selftest2__"})
        check("PUT /api/categories/<id> 重命名", r.status_code == 200 and r.get_json().get("success") is True)
        r = c.delete("/api/categories/%s" % cid, json={"reassign_to": None})
        check("DELETE /api/categories/<id> 带 reassign_to",
              r.status_code == 200 and r.get_json().get("success") is True)

    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)
except Exception as e:
    import traceback
    traceback.print_exc()
    check("Flask 冒烟可运行", False, str(e))

print("\n" + "=" * 62)
if FAIL:
    print("结果：%d 项失败" % len(FAIL))
    for f in FAIL:
        print("  - " + f)
    sys.exit(1)
else:
    print("结果：全部通过 ✓")
