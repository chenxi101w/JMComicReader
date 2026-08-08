# JMComicReader · GitHub 发布文本包（v2.0）

> 本文件汇总发布到 GitHub 所需的全部文本：仓库基本信息、完整 README、Release 发布说明。
> 直接复制对应小节即可。源码仓（`jmcomicreader-windows`）已确认 `.gitignore` 干净，可安全 `git push`。

---

## 一、仓库基本信息（创建仓库时填写）

**仓库名（建议）**：`JMComicReader`
**描述（Description，≤100 字符）**：
```
现代化本地 JM 漫画阅读器与下载管理器 · Windows 桌面版（Flask + WebView）
```

**网站（Homepage）**：留空 或 填你的 GitHub Pages / 仓库地址

**Topics / 标签**（在仓库 Settings → Topics 添加）：
```
manga-reader
comic-downloader
jmcomic
flask
pywebview
webview2
windows
python
desktop-app
manga
```

**License**：MIT（仓库已含 `docs/LICENSE`）

---

## 二、README.md（完整，直接粘到 GitHub 仓库的 README）

```markdown
# JMComicReader

一个现代化、轻量级的本地 JM 漫画阅读器与下载管理器。桌面端采用 **Flask + 原生 WebView** 架构，前端为响应式 Web UI；手机端（Android）正在开发中（见 `jmcomic-android` 工程）。

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.13-green.svg)
![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-lightgrey.svg)

---

## ✨ 功能特性

**搜索与发现**
- 关键词模糊搜索 + JM 号精准直达
- 标签搜索（支持多标签 OR/AND、同义词展开）
- 作者搜索（支持同义署名展开）
- 联合搜索：关键词 + 标签 + 作者
- 漫画详细信息、标签、章节列表
- 封面智能缓存

**下载管理**
- 多线程异步下载
- 实时下载进度监控
- 批量下载
- 本地存储，离线阅读

**阅读体验**
- 响应式 Web 阅读器
- 章节切换、页码跳转
- 本地书架 + 分类管理（树形文件夹）

**屏蔽与别名**
- 拉黑作品 / 作者（本地灰显，文件保留）
- 标签 / 作者同义词别名，搜索结果自动归一化

**v2.0 更新亮点**
- 🆕 新增「搜索结果标签显示数量」设置：默认显示全部标签，可自定义最多显示几个（超出显示 `+N`）
- 🎨 设置页重构为「分类钻取」布局：下载 / 搜索 / 外观 / 网络代理 / 缓存 / 关于 六大类卡片，点击进入具体设置
- 🧩 屏蔽列表升级为紧凑 chip：作者 / 作品名成块展示，内置 `×` 一键删除，大幅节省空间
- 🐛 修复搜索框多余分隔线（原两条线，已去掉下方那条）

---

## 🚀 快速开始

### 源码运行（Windows）

**环境要求：** Python 3.13+

```bash
cd jmcomicreader-windows
pip install -r docs/requirements.txt
python desktop_app.py
```

启动后自动打开桌面窗口，或浏览器访问 `http://127.0.0.1:<port>`（端口写入 `.app_url`）。

### 构建桌面版（onedir）

```bash
python -m PyInstaller JMComicReader.spec --distpath dist --workpath build
```

产物为 onedir 目录，用户数据（数据库 / 下载 / 缓存）与 `JMComicReader.exe` 同级，便于携带。

### 直接使用（免安装）

下载 Release 中的压缩包，解压后双击 `JMComicReader.exe` 即可。
- 首次启动会在 `JMComicReader.exe` 同级的 `UserData/` 下自动创建数据目录，无需手动配置。
- 需要 Windows 10/11；WebView2 运行时 Win11 自带，Win10 缺失时自动回退浏览器模式（功能不受影响）。
- 无需安装 Python，也无需联网安装依赖。

---

## 📁 项目结构

```
jmcomicreader-windows/
├── desktop_app.py              # 桌面启动入口（pywebview + Flask 服务线程）
├── JMComicReader.spec          # PyInstaller 配置
├── VERSION                     # 版本号
├── core/                       # 后端
│   ├── app.py                  # Flask 路由（页面 + API）
│   ├── config.py               # 统一配置与路径
│   ├── models/
│   │   └── database.py         # SQLite 数据层
│   └── services/
│       ├── jm_crawler.py       # JM 网络协议：搜索/详情/封面/下载
│       ├── download_manager.py # 文件下载与保存
│       ├── comic_manager.py    # 本地漫画扫描与读取
│       └── filter_service.py   # 拉黑过滤 + 别名/同义词
├── web/                        # 前端
│   ├── templates/              # Jinja2 HTML 页面
│   └── static/
│       ├── css/style.css
│       └── js/app.js
├── assets/                     # 应用图标
└── docs/                       # 文档
    ├── API.md                  # HTTP API 参考（手机端可用）
    ├── ARCHITECTURE.md         # 架构说明与手机端准备建议
    ├── requirements.txt        # Python 依赖
    └── SHARE.md                # 分享包制作说明
```

---

## 💾 数据与备份

所有用户数据放在 `JMComicReader.exe` 同级的 `UserData/` 文件夹，与程序本体 `App/` 完全分离：

- `UserData/core/comics.db` —— 下载历史、设置、收藏等数据库
- `UserData/core/jm_option.yml` —— 漫画源配置
- `UserData/DownloadedComics/` —— 下载的漫画
- `UserData/webview_data/` —— 浏览器本地存储（收藏标签、搜索历史）

**备份 / 迁移**：直接把整个程序文件夹拷贝走即可（`App/` 程序 + `UserData/` 数据都跟着走），换电脑也不丢。

---

## 📱 手机端（Android，开发中）

Android 项目：`jmcomic-android`（Kotlin + Jetpack Compose），与桌面端共享本地目录结构、数据库表结构、JM 网络协议与同义词种子。详见 `docs/ARCHITECTURE.md`。

---

## ⚠️ 免责声明

本项目仅供学习交流使用，请勿用于非法用途。所有漫画资源均来自网络，本项目不存储任何漫画内容。
```

---

## 三、Release v2.0 发布说明（在 GitHub 创建 Release 时填写）

**Tag / 版本号**：`v2.0`
**Release 标题**：`JMComicReader v2.0`
**Target**：`main`（或你的默认分支）

**发布说明正文**：

```markdown
## JMComicReader v2.0

桌面端体验大版本更新，重点打磨搜索与设置体验。

### 🆕 新增
- **搜索结果标签显示数量设置**：默认显示全部标签；可自定义最多显示几个，超出部分折叠为 `+N`，长标签列表不再刷屏。

### 🎨 优化
- **设置页重构为「分类钻取」布局**：下载 / 搜索 / 外观 / 网络代理 / 缓存 / 关于 六大类以卡片呈现，点击进入具体设置，信息层级更清晰。
- **屏蔽列表升级为紧凑 chip**：作者 / 作品名以小块呈现，内置 `×` 一键删除，相比旧版整行布局大幅节省空间。

### 🐛 修复
- 搜索框多余分隔线（原本上下两条线，已去掉下方那条）。

### 📦 其他
- 版本号升级至 2.0。

### 使用说明
下载 `JMComicReader.zip`，解压后双击 `JMComicReader.exe` 即可，无需安装 Python 或任何依赖。
用户数据位于程序同级的 `UserData/` 目录，备份 / 迁移直接拷贝整个文件夹即可。
```

---

## 四、发布前检查清单（给开发者的备注）

- [x] 源码 `VERSION` = `2.0`
- [x] `py_compile` 后端 + `node --check` 前端 通过
- [x] `settings.html` Flask 渲染冒烟通过
- [x] `.gitignore` 已排除 `build/ dist/ *.exe *.db* TempCache/ DownloadedComics/ webview_data/ .app_url .workbuddy/`
- [x] **已完成**：v2.0 已重建并部署为扁平布局 —— `JMComicReader/JMComicReader.exe` + `appdata/`（原 `_internal` 改名，PyInstaller `contents_directory='appdata'`）+ `UserData/`。`desktop_app.py` 数据路径改为 `exe 同级/UserData`。无头启动验证通过：`/search` `/api/settings` 200、静态资源 `?v=16`、无 `startup_error.log`、后端返回 `search_tag_limit`。
- [ ] **待办**：把旧的 `App/`（v1.9.3 残留，被 Defender 锁）与临时构建目录（`out_v200`/`bld_v200`/`_verify_v200_TO_DELETE`）清理掉（用户稍后手动或本机命令删除）。
- [ ] 上传 Release 附件（`JMComicReader.zip`）
