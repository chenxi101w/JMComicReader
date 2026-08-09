# JMComicReader（Windows 桌面版）代码审查报告

> 审查对象：`jmcomicreader-windows/`（Flask 后端 + pywebview 桌面壳 + 原生 JS 前端）
> 审查范围：`desktop_app.py`、`core/app.py`、`core/config.py`、`core/models/database.py`、`core/services/*.py`、`web/static/js/app.js`、Jinja2 模板
> 审查维度：逻辑错误、边界情况、异常处理、安全性、性能、可读性

---

## 0. 总览

| 等级 | 数量 | 关键项 |
|------|------|--------|
| 🔴 Critical（安全/严重） | 3 | 路径遍历任意文件读取、开发入口开调试器全网卡监听、CORS 全开放 |
| 🟠 High（功能缺陷） | 3 | 多标签「且」搜索失效、删除分类级联丢失子项、失败任务不清理 |
| 🟡 Medium（性能/健壮/并发） | 5 | O(n²) 书架扫描、分类计数顺序依赖、历史无限增长、缓存并发、配置无白名单 |
| 🟢 Low（可读性/小问题） | 5 | 裸 dict 返回、逗号标签错位、散装 import、重复实例化、缓存并发赋值 |

**结论**：代码整体结构清晰、异常处理覆盖较全、XSS 防护（前端 `escapeHtml`）做得不错。但存在 **1 个可被恶意网页利用的本地文件读取漏洞**（配合全开放 CORS）和 **1 个核心搜索功能逻辑错误**，建议优先修复。

---

## 1. 🔴 Critical — 安全问题

### C1. 路径遍历：任意文件读取（`core/app.py` + `comic_manager.py`）

**位置**
- `core/app.py:884` 路由 `/api/comic/<int:jm_id>/page/<int:page_num>`
- `core/services/comic_manager.py:306` `get_comic_page_path`

**问题**
```python
chapter_id = request.args.get("chapter", None)          # 用户可控字符串
page_path = comic_manager.get_comic_page_path(jm_id, page_num, chapter_id)
```
而 `get_comic_page_path` 中：
```python
if chapter_id:
    chapter_path = os.path.join(comic_dir, chapter_id)  # 未校验！
    if os.path.isdir(chapter_path):
        comic_dir = chapter_path                         # 目录可被跳出
```
`chapter_id` 未经任何校验直接拼进路径。攻击者可传 `?chapter=../../../../Users/xxx`，使 `comic_dir` 逃逸到任意目录；后续 `_collect_images_recursive` / `os.listdir` / `send_file` 会读取并返回该目录下的图片文件。

**影响**：本地服务若被恶意网页（见 C3）跨域访问，可读取本机任意文件（如配置文件、隐私图片）。

**修复**（双保险）
1) API 层：仅接受真实存在的章节 id：
```python
@app.route("/api/comic/<int:jm_id>/page/<int:page_num>")
def get_comic_page(jm_id, page_num):
    try:
        chapter_id = request.args.get("chapter", None)
        if chapter_id is not None:
            valid = {str(c["id"]) for c in comic_manager.get_comic_chapters(jm_id)}
            if chapter_id not in valid:
                return jsonify({"success": False, "message": "章节不存在"}), 400
        page_path = comic_manager.get_comic_page_path(jm_id, page_num, chapter_id)
        ...
```
2) `get_comic_page_path` 内增加纵深防御：
```python
if chapter_id and (".." in chapter_id or "/" in chapter_id or os.path.sep in chapter_id):
    return None
# 落盘前再确认不越界
resolved = os.path.realpath(page_path)
base = os.path.realpath(comic_dir)
if not (resolved == base or resolved.startswith(base + os.sep)):
    return None
```

---

### C2. 开发入口开启 Werkzeug 调试器并全网卡监听（`core/app.py:1171`）

**问题**
```python
def main():
    app.run(debug=True, host="0.0.0.0", port=5000)
```
`debug=True` 会暴露 Werkzeug 交互式调试器（可被用于远程代码执行），`host="0.0.0.0"` 监听所有网卡。一旦开发者用 `python core/app.py` 直接启动测试，本机暴露在局域网内即存在 RCE 风险。

**修复**
```python
def main():
    # 仅本地回环；调试器必须显式开启且永不对外
    debug = os.environ.get("FLASK_DEBUG") == "1"
    app.run(debug=debug, host="127.0.0.1", port=5000)
```
> 注：正式桌面入口 `desktop_app.py` 用 `make_server(threaded=True)` 无 debug，是安全的；此问题仅限开发入口，但危害大，应尽早收敛。

---

### C3. CORS 完全放开（任意来源）— 与 C1 组合成可利用链（`core/app.py:41`）

**问题**
```python
CORS(app)   # 默认 origins="*"，允许任何网站跨域调用本机 API
```
桌面应用仅通过 pywebview 以同源方式访问，本不需要放开 CORS。放开后，用户浏览器中打开的任意恶意网页只要知道端口即可调用 `/api/comic/.../page?chapter=..`（C1）读取本机文件。

**修复**（限制为 localhost 来源）
```python
from flask_cors import CORS
CORS(app, resources={r"/*": {"origins": r"https?://(127\.0\.0\.1|localhost)(:\d+)?$"}})
```

---

## 2. 🟠 High — 功能/逻辑缺陷

### H1. 多标签「且（and）」搜索几乎永远返回空（`jm_crawler.py:737`）

**问题**
```python
results = []
for raw_id, detail in details.items():
    detail_tags = set(detail.get("tags") or [])
    if mode == "and":
        if not all(t in detail_tags for t in tag_list):   # tag_list 是“展开后的同义词列表”
            continue
```
`tag_list` 传入的是 `expand_tags(...)` 的结果（canonical + 全部别名，例如搜「爆乳」会展开成 `["巨乳","爆乳","大奶"]`）。而 `detail_tags` 是 JM 原始标签（通常只有一个，如 `"爆乳"`）。`all(...)` 要求作品同时拥有「巨乳」「爆乳」「大奶」所有变体 → 几乎不可能命中，**「且」搜索实质失效**。

**修复**：按「每个原始输入标签，至少命中其任一同义词」来判定：
```python
from core.services.filter_service import expand_tags  # 函数内惰性导入，避免模块级循环依赖

if mode == "and":
    orig = input_tags if input_tags else tag_list
    hit = True
    for ot in orig:
        variants = {t.lower() for t in expand_tags([ot])}
        if not (variants & {str(t).lower() for t in detail_tags}):
            hit = False
            break
    if not hit:
        continue
```
（`search_by_tags` 已接收 `input_tags` 参数，可直接使用。）

---

### H2. 删除分类级联删除子分类，意外丢失数据（`database.py:273`）

**问题**
```python
def delete_category(cat_id):
    conn.execute("DELETE FROM categories WHERE id = ?", (cat_id,))
```
`categories.parent_id` 外键带 `ON DELETE CASCADE` → 删除父分类会**连其子分类、以及这些子分类的漫画关联一并删除**，而非把子分类提升一级。用户删除一个父目录时，会无预警地丢失整棵子树。

**修复**：删除前把子分类提升为被删节点的父级：
```python
def delete_category(cat_id):
    conn = _get_conn()
    row = conn.execute("SELECT parent_id FROM categories WHERE id=?", (cat_id,)).fetchone()
    parent = row["parent_id"] if row else None
    conn.execute("UPDATE categories SET parent_id=? WHERE parent_id=?", (parent, cat_id))
    conn.execute("DELETE FROM categories WHERE id=?", (cat_id,))
    conn.commit()
    return True
```
（子分类的漫画关联因 `parent_id` 已改指向祖父，不再级联删除，得以保留。）

---

### H3. 下载失败的任务永久残留在内存（`core/app.py:588` / `:647`）

**问题**：成功路径会 `threading.Timer(2.0, _cleanup_progress, ...)` 清理 `_dl_progress`，但**异常路径只调用 `_update_progress(..., "error", ...)` 并写入历史，不清理**。失败的 `download_id` 会一直留在内存中，直到前端主动 DELETE。若前端未调用，状态无限堆积。

**修复**：异常分支同样延时清理（保留短暂展示错误）：
```python
except Exception as e:
    _update_progress(_did, 0, "error", str(e))
    add_download_history(_id, _info.get("title", ""), "failed", str(e))
    threading.Timer(30.0, _cleanup_progress, args=(_did,)).start()  # 30s 后清理
```

---

## 3. 🟡 Medium — 性能 / 健壮性 / 并发

### M1. 书架加载 O(n²)：每次都 `os.listdir(downloaded_dir)`（`comic_manager.py`）

**问题**：`_find_downloaded_dir` 对每个漫画都做一次全目录 `listdir`；`get_downloaded_comics` 在循环里对每个漫画又调用它，且逐条 `get_comic_categories` 查库。漫画数量大时（几百本）书架页极慢。

**修复**：一次性建立索引并批量取分类。
```python
def _build_index(self):
    """jm_id -> 漫画目录，单次 listdir 完成。"""
    idx = {}
    try:
        for name in os.listdir(self.downloaded_dir):
            if "_" not in name:
                continue
            try:
                jid = int(name.split("_", 1)[0])
            except ValueError:
                continue
            idx[jid] = os.path.join(self.downloaded_dir, name)
    except OSError:
        pass
    return idx

# is_comic_downloaded / get_downloaded_comics 等改用 self._build_index()[jm_id]
```
并在 `database.py` 增加批量查询：
```python
def get_all_comic_categories() -> Dict[int, List[int]]:
    rows = _get_conn().execute(
        "SELECT comic_jm_id, category_id FROM comic_categories"
    ).fetchall()
    out: Dict[int, List[int]] = {}
    for r in rows:
        out.setdefault(r["comic_jm_id"], []).append(r["category_id"])
    return out
```
`get_downloaded_comics` 用该字典一次取出，避免 N 次查询。

### M2. 分类计数依赖 id 插入顺序（`database.py:226`）

**问题**：`get_category_tree` 通过 `for node in node_map.values()` 把子节点 count 累加给父节点，结果依赖字典遍历顺序（= id 顺序）。若父 id > 子 id（手动改父级、合并库等），父节点会被重复累加，计数翻倍。

**修复**：自底向上递归求和（与顺序无关）：
```python
children_map = defaultdict(list)
for r in rows:
    children_map[r["parent_id"]].append(r["id"])
def total(cid):
    s = node_map[cid]["count"]            # 直接归属数
    for ch in children_map.get(cid, []):
        s += total(ch)
    node_map[cid]["count"] = s
    return s
for cid in node_map:
    if node_map[cid]["parent_id"] not in node_map:
        total(cid)
```

### M3. 搜索/阅读历史无限增长（`database.py:584`）

**问题**：`cleanup_old_records(days=30)` 已实现但**从未被调用**，搜索历史与阅读历史表会无限膨胀。

**修复**：在 `desktop_app.run_desktop()` 启动或 `init_database()` 后定时清理（如每天一次的后台线程 / Timer）。

### M4. `jm_crawler.detail_cache` 跨请求并发读写无锁（`jm_crawler.py`）

**问题**：`JMCrawler` 是单例，其 `detail_cache` 字典被并发搜索请求读写；`get_search_result_details` 中的 `self.detail_cache = dict(list(self.detail_cache.items())[-3000:])` 重建赋值与 `_save_detail_cache_async` 的读取存在竞争，高并发可能触发 `RuntimeError: dictionary changed size during iteration`。

**修复**：对缓存的读写加锁，或迭代前先 `snapshot = list(self.detail_cache.items())` 在本地快照上操作：
```python
self._detail_lock = threading.Lock()
# 读取/写入 detail_cache 时：
with self._detail_lock:
    cached = self.detail_cache.get(str(album_id))
...
with self._detail_lock:
    self.detail_cache[str(album_id)] = detail
    if len(self.detail_cache) > 3500:
        self.detail_cache = dict(list(self.detail_cache.items())[-3000:])
```

### M5. `/api/settings` POST 无 key 白名单（`core/app.py:993`）

**问题**：`save_settings` 遍历请求体的所有 key 直接 `set_system_config`，客户端可写入任意配置键（污染 `system_config` 表、甚至构造非预期键）。

**修复**：仅允许已知键：
```python
ALLOWED = {"max_concurrent_downloads","auto_cleanup_cache","cache_size_limit","image_quality",
           "theme","proxy_enabled","proxy_url","search_result_limit","search_priority",
           "hide_page_chapter","show_block_hits","search_tag_limit","reader_chapter_toast",
           "recommend_enabled","recommend_count","recommend_basis","recommend_custom"}
for k, v in data.items():
    if k not in ALLOWED:
        continue
    ...
```

---

## 4. 🟢 Low — 可读性与小问题

- **L1（一致性）**：`/api/read/*`（`core/app.py:842/870`）返回裸 `dict` 依赖 Flask 自动 JSON 序列化，与其余 `jsonify()` 混用。建议统一 `jsonify(...)`（并显式 `return ..., 200`）。
- **L2（边界）**：标签以 `",".join` / `split(",")` 存储，若标签本身含逗号会错位。建议存储为 JSON 数组（`json.dumps`/`json.loads`），或至少对标签做逗号清洗。
- **L3（风格）**：`import json` / `import traceback` 散落在多个函数内（`jm_crawler.py:668,851`；`download_manager.py:243,313,380`），可统一提到模块顶部。
- **L4（重复实例化）**：`DownloadManager`、启动恢复线程、`_ensure_files_ready` 各自 `new JMCrawler()` 并重读 `jm_option.yml` 与缓存，开销可避免（共享单例）。
- **L5（并发）**：`recommend` 与 `_recommend_cache` 在并发请求下赋值无锁，最坏只是缓存短暂失效/重复计算，影响小，可加 `threading.Lock()` 兜底。

---

## 5. 修复优先级建议

1. **立刻修**：C1（路径遍历）、C2（调试器）、C3（CORS 收紧）—— 三者共同构成可被网页利用的本地文件泄露链。
2. **尽快修**：H1（and 搜索失效）、H2（删除分类丢数据）、H3（失败任务清理）。
3. **排期优化**：M1/M2（性能与计数正确性）、M3（历史清理）、M5（配置白名单）。
4. **顺手改**：L1–L5 可读性项。

> 前端 `app.js` 对用户输入/后端数据均做了 `escapeHtml` 转义（已核对 `renderCards`、`renderTree`、`renderBlockList`、`renderAliasList` 等关键渲染点），未发现 XSS 注入点；Jinja2 模板无 `|safe` / `autoescape off` 风险点。安全短板集中在后端 C1–C3。

---

## 6. 修复执行状态（2026-08-08 已 apply）

### 已落地 & 验证

| 项 | 文件 | 验证 |
|----|------|------|
| ✅ C3 CORS 收紧 | `core/app.py:41` | py_compile 通过 |
| ✅ C2 调试器/监听收敛 | `core/app.py:main()` | py_compile 通过 |
| ✅ C1 路径遍历（API+纵深） | `core/app.py:get_comic_page` + `comic_manager.get_comic_page_path` | 隔离测试 7/7 通过（`..`/`/`/越界均拒） |
| ✅ H3 失败任务清理 | `core/app.py` 两处 except | py_compile 通过 |
| ✅ H1 且搜索修复 | `jm_crawler.search_by_tags` + `_expand_single` | py_compile 通过（逻辑复核） |
| ✅ H2 删除分类提升子级 | `database.delete_category` | 冒烟测试通过（C 提升为 A，漫画关联保留） |
| ✅ M1 书架去 O(n²) | `comic_manager` 索引 + `database.get_all_comic_categories` | 索引解析测试通过；批量取分类测试通过 |
| ✅ M2 分类计数 | `database.get_category_tree` | 冒烟测试通过（A=3/B=2/C=1，与 id 顺序无关） |
| ✅ M3 历史清理调度 | `core/app.py:_schedule_history_cleanup` | py_compile 通过 |
| ✅ M4 detail_cache 加锁 | `jm_crawler` `_detail_cache_lock` | py_compile 通过 |
| ✅ M5 设置白名单 | `core/app.py:save_settings` | py_compile 通过 |
| ✅ L1 read 路由 jsonify | `core/app.py:read_comic/read_comic_chapter` | py_compile 通过 |

### 验证方式
- 全部改动文件 `python -m py_compile` 通过。
- `database` 层（H2/M2/M1）用临时 SQLite 跑通：删除提升、计数递归、批量取分类均正确。
- C1 路径遍历防护与 M1 索引解析提取为隔离逻辑，对临时目录跑 7 条用例全绿。
- `jm_crawler` / `comic_manager` 因依赖 `jmcomic` 包（本机沙箱未装），仅做 py_compile + 逻辑复核，未在运行时导入；需在装好 `jmcomic` 的环境（即你的开发机）实际启动验证。

### 未改动（维持原状）
- 前端 `app.js` 无 XSS，未动。
- Low 项 L2（标签逗号错位）、L3（散装 import）、L4（重复实例化）、L5（recommend 锁）属可读性/边际优化，未自动改，按需可后续处理。
