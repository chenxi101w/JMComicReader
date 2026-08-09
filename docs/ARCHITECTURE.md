# JMComicReader 架构与手机端准备

## 当前目录结构

```
jmcomicreader-windows/
├── desktop_app.py              # 桌面启动入口：环境初始化 + Flask 服务线程 + pywebview
├── JMComicReader.spec          # PyInstaller onedir 打包配置
├── VERSION                     # 版本号
├── core/
│   ├── app.py                  # Flask 应用：页面路由 + API 路由（~880 行）
│   ├── config.py               # 统一配置：路径、版本、目录确保
│   ├── models/
│   │   └── database.py         # SQLite 数据层：表定义/DDL + CRUD +系统配置
│   └── services/
│       ├── jm_crawler.py       # JM 客户端构建、搜索、详情、封面、下载抓取
│       ├── download_manager.py # 本地文件下载、保存、目录结构
│       ├── comic_manager.py    # 已下载漫画枚举、章节、页面路径
│       └── filter_service.py   # 拉黑过滤 + 标签/作者别名展开 + 同义词建议
├── web/
│   ├── templates/              # Jinja2 页面（base/search/detail/...）
│   └── static/
│       ├── css/style.css       # 单文件样式（~880 行）
│       ├── js/app.js           # 单文件前端逻辑（~1670 行）
│       └── img/                # 图标（app_icon.ico / app_icon.png）
└── docs/                       # 文档与需求
```

## 模块职责

| 模块 | 职责 | 手机端复用度 |
|------|------|-------------|
| `desktop_app.py` | 仅桌面：目录迁移、端口固定、pywebview 窗口 | 不可复用 |
| `core/config.py` | 路径与版本解析，启动时确保目录存在 | 逻辑可参考，路径需适配 Android |
| `core/models/database.py` | 数据持久化：分类/下载历史/搜索历史/拉黑/别名/配置 | **模型与 SQL 模式可直接复用** |
| `core/services/jm_crawler.py` | 网络层：JM 域名、scramble 解码、搜索、详情、封面、下载 | **协议与解码逻辑需复用到 Kotlin** |
| `core/services/download_manager.py` | 文件 I/O：按规则保存图片、生成 PDF（已弃用） | 保存路径逻辑可参考 |
| `core/services/comic_manager.py` | 本地漫画目录扫描、章节识别、页面读取 | **扫描逻辑需复用到 Kotlin** |
| `core/services/filter_service.py` | 业务规则：拉黑过滤、别名展开、同义词建议 | **规则与 SEED_SYNONYMS 可直接复用** |
| `core/app.py` | HTTP API 与页面路由粘合层 | 手机端不需要 Flask，但需要等效 API 实现 |
| `web/` | PC Web UI | 手机端用 Jetpack Compose 重写 |

## 关键数据流

```
搜索：前端 -> /api/search/* -> app.py -> filter_service.expand_* -> jm_crawler.search_* -> filter_service.postprocess -> DB（搜索历史） -> JSON
下载：前端 -> /api/download/* -> app.py -> download_manager.download_comic -> DownloadedComics/JM-{id}/ -> DB（下载历史）
阅读：前端 -> /api/read/* /api/comic/*/page/* -> app.py -> comic_manager 扫描本地目录 -> 返回章节/图片
书架：前端 -> /api/downloaded -> app.py -> comic_manager + filter_service 拉黑判断 -> JSON
```

## 手机端（Android）准备建议

### 1. 共享「业务规则」
以下数据结构/规则建议作为事实来源，Android 直接对照实现或导出为 JSON/YAML 配置：
- `filter_service.SEED_SYNONYMS`：内置同义词种子。
- `DEFAULT_CONFIGS` 的 key 与默认值：保证两端设置语义一致。
- 数据库表结构（`database.py` 中的 DDL）：Android Room 可复用同一张表设计。
- 本地目录结构：`DownloadedComics/JM-{id}/` 与图片命名规则，保证 PC/手机数据可互迁。

### 2. 共享「网络协议」
- `jm_crawler.py` 中 JM 域名切换、scramble 解码、搜索/详情/图片 URL 构造规则。
- 建议把协议层抽象为纯函数/配置，后续可抽成 Kotlin `data/JmComicClient.kt`（Android 项目已存在）。
- 下载保存规则（每话子目录、图片重命名）保持一致。

### 3. 不建议共享的层
- Flask HTTP API：手机端是本地 App，不需要 REST；直接用 Repository 模式调用本地服务。
- `web/` 前端：Android 用 Compose 重写，只复用页面/交互概念。
- `desktop_app.py`：仅 Windows 桌面打包入口。

### 4. 推荐的重构方向（为双端铺路）
1. **把 `jm_crawler.py` 拆成三块**：
   - `jm_client.py`：HTTP 客户端构建、domain 切换、重试。
   - `jm_search.py`：search_keyword / search_tag / search_author / combined。
   - `jm_download.py`：图片下载、scramble 解码。
   这样 Android 抄协议时只看 `jm_client` + `jm_download`。
2. **把 `app.py` 按蓝图拆分**：
   - `api/search.py`, `api/download.py`, `api/categories.py`, `api/settings.py`...
   降低单文件维护成本，也便于和 Android 的 ViewModel 层一一对应。
3. **把 `app.js` 按页面拆模块**：
   - `search.js`, `downloads.js`, `library.js`, `reader.js`, `settings.js`, `api.js`。
   目前单文件 1670 行，新增功能容易互相影响。
4. **统一数据模型文件**：
   在 `core/models/` 下新增 `schemas.py`（或用 dataclass）定义 ComicInfo/Progress/Category/History 等，作为前后端与 Android 的共同契约。

## 当前可做的安全整理（不破坏功能）
- 删除源码目录下的运行时产物：`__pycache__`、`core/__pycache__`、`core/comics.db*`、`TempCache/`、`core/jm_option.yml`（若由程序自动生成）。
- 补充 `.gitignore`（如果缺失）：排除 `__pycache__`, `build/`, `dist/`, `*.db*`, `TempCache/`, `webview_data/`。
- 给大文件加模块级 docstring：`jm_crawler.py`, `download_manager.py`, `comic_manager.py`, `app.js`。

## 与现有 Android 工程的关系
- Android 工程路径：`D:\code\Android\AI CODE\jmcomic-android`
- 已存在 `data/AppRepository.kt`、`data/JmComicClient.kt`、`data/LocalDb.kt`、`data/ScrambleDecoder.kt`。
- 建议把本项目的 `jm_crawler.py` + `filter_service.py` 与 Android 的 `JmComicClient` / `ScrambleDecoder` 对齐规则，避免两端行为不一致。
