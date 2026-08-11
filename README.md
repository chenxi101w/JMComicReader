# 📚 JMComicReader · Windows 桌面漫画阅读器

> **一个绿色、免安装的 JM 漫画本地阅读与管理工具** —— 搜索、在线阅读、批量下载、书架管理、智能推荐，全部在桌面搞定。基于 pywebview + Flask，移动优先的响应式界面。

[![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D4?logo=windows&logoColor=white)](https://www.microsoft.com/windows)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](docs/LICENSE)
[![Release](https://img.shields.io/github/v/release/chenxi101w/JMComicReader?label=latest%20release&color=blue)](https://github.com/chenxi101w/JMComicReader/releases)
[![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![Downloads](https://img.shields.io/github/downloads/chenxi101w/JMComicReader/total?label=downloads&color=orange)](https://github.com/chenxi101w/JMComicReader/releases)

---

## 📑 目录

- [📸 预览](#preview)
- [✨ 功能一览](#features)
- [📥 下载与运行](#download)
- [🚀 功能详解](#usage)
- [⚠️ 注意事项](#notes)
- [🛠 从源码构建](#build)
- [📂 项目结构](#structure)
- [🧱 技术栈](#tech-stack)
- [📚 文档](#docs)
- [🗺 更新日志](#changelog)
- [⚠️ 免责声明](#disclaimer)
- [📄 License](#license)

---

<a id="preview"></a>
## 📸 预览

> 📌 **截图待补充**：将以下三张图放入 `assets/screenshots/` 后，取消下方 HTML 注释包裹即可显示（详见 [GITHUB_SETUP.md](GITHUB_SETUP.md)）。
>
> - `home.png` —— 主页 · 智能推荐
> - `search.png` —— 搜索 · 多维度（关键词 / 标签 / 作者）
> - `reader.png` —— 阅读器 · 滚轮以鼠标为中心缩放

<!-- 截图到位后取消此注释块
<div align="center">
  <img src="assets/screenshots/home.png" width="760" alt="主页 · 智能推荐"/>
  <p><em>主页 · 基于搜索偏好的「为你推荐」</em></p>
  <img src="assets/screenshots/search.png" width="760" alt="搜索 · 多维度"/>
  <p><em>搜索 · 关键词 / 标签 / 作者 三维度</em></p>
  <img src="assets/screenshots/reader.png" width="760" alt="阅读器 · 滚轮缩放"/>
  <p><em>阅读器 · 滚轮以鼠标位置为中心缩放、方向键平移</em></p>
</div>
-->

---

<a id="features"></a>
## ✨ 功能一览

| 功能 | 说明 |
|------|------|
| 🔍 多维度搜索 | 关键词 / 标签 / 作者三种方式，结果一目了然 |
| ⭐ 搜索偏好库 | 历史、标签、作者可收藏、可编辑、可恢复（误删进回收站），一键回填搜索框 |
| 📖 在线阅读 | 章节切换、图片预加载、以鼠标为中心的滚轮缩放、拖拽平移、返回顶部 |
| 💾 下载与书架 | 单本 / 批量下载（真正限并发），书架统一管理 |
| 🧭 智能推荐 | 基于你的搜索偏好（关键字 / 标签 / 作者）生成「为你推荐」主页 |
| 🎨 丰富设置 | 主题（亮 / 暗 / 跟随系统）、代理、推荐源、下载路径等 |
| 📦 数据可携带 | 程序与个人数据分离，整个文件夹拷贝即完成迁移 / 备份 |

---

<a id="download"></a>
## 📥 下载与运行

> 绿色版，推荐。无需安装，解压即用。

1. 获取 `JMComicReader_v2.4.10_portable.zip`（单一完整包，约 28MB，已含程序本体 + 前端，**不含个人数据**）
   - 仓库 **[Releases](https://github.com/chenxi101w/JMComicReader/releases)** 页面获取
2. 用 **7-Zip** / 系统自带解压工具解压
3. 双击 `JMComicReader.exe` 即可使用

**运行要求**：Windows 10 / 11（64 位）；需 Edge **WebView2 运行时**（Windows 11 自带，Windows 10 若缺失请先安装 [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)）。无需安装 Python，无需联网装依赖。

> 首次启动会在 `JMComicReader.exe` **同级的 `UserData/`** 目录下自动生成数据目录，无需手动配置。

---

<a id="usage"></a>
## 🚀 功能详解

### 1. 搜索漫画
- 顶部搜索框支持 **三种维度**：直接输关键词、切到「标签」或「作者」标签页搜索。
- 搜索结果下拉分三栏（关键词 / 标签 / 作者），对应关系清晰。
- 想搜的标签或作者，点一下即可跳转搜索。

### 2. 搜索偏好库（收藏常用搜索）
- 搜过的关键词、标签、作者会进入「搜索偏好库」，可**收藏**常用的。
- 收藏项可**编辑**、可**删除**；误删会进回收站，可恢复。
- 在偏好库里点任意一项，会**一键回填**到搜索框，省得重复输入。

### 3. 在线阅读
- 点开漫画 → 进入章节列表 → 点章节开始阅读。
- 阅读页支持：**章节切换**、**图片预加载**（翻页不转圈）、**以鼠标为中心的滚轮缩放**、**拖拽**平移、**返回顶部**。
- 阅读进度自动记录，下次打开接着看。

### 4. 下载与书架
- 漫画详情页可**单本下载**，书架页支持**批量下载**（并发受 `max_concurrent_downloads` 控制）。
- 下载的内容在「书架」统一管理，可离线阅读、可删除。
- 下载路径在「设置」里自定义。

### 5. 智能推荐主页
- 打开软件默认进入「主页」，基于你的搜索偏好（关键字 / 标签 / 作者）生成「为你推荐」。
- 点推荐里的作者 / 标签，直达对应搜索。

### 6. 设置
- **主题**：亮色 / 暗色 / 跟随系统。
- **代理**：网络环境需要时填写。
- **推荐源 / 下载管理**：调整推荐内容与下载行为、下载路径。

### 7. 数据管理（迁移 / 备份 / 重置）
- **迁移 / 备份**：整个程序文件夹拷到别的电脑即可，`UserData/` 会跟着走，数据不丢。
- **重置**：删掉 `UserData/` 文件夹，所有本地配置与下载记录清空，下次启动恢复初始状态。
- **分享给他人前**：请删除个人数据，见下方「分享注意」。

---

<a id="notes"></a>
## ⚠️ 注意事项

- **杀软拦截**：解压后个别杀软可能拦 `JMComicReader.exe`，放行即可。这是纯本地程序，**不会上传任何数据**。
- **WebView2**：没有 WebView2 运行时无法启动窗口，请先安装（见下载小节链接）。
- **网络**：搜索 / 下载需要能访问漫画源站的网络环境；代理在「设置」里配。

---

<a id="build"></a>
## 🛠 从源码构建

```bash
# 1. 创建虚拟环境并安装依赖
python -m venv build_venv
build_venv\Scripts\pip install -r build/requirements.txt

# 2. 构建单目录版 exe
build_venv\Scripts\python -m PyInstaller build/JMComicReader.spec ^
  --noconfirm --clean ^
  --distpath dist_out ^
  --workpath build_out

# 3. 构建完成后会在 dist_out/ 生成 JMComicReader/ 与 JMComicReader_vX.Y.Z_portable.zip
```

> ⚠️ 分享 / 分发时请删除个人数据：`UserData/DownloadedComics/`、`UserData/webview_data/`、`UserData/core/comics.db`、`UserData/core/jm_option.yml`、`.app_url`、`UserData/TempCache/`。

---

<a id="structure"></a>
## 📂 项目结构

```
jmcomicreader-windows/
├─ desktop_app.py          # 桌面入口（pywebview 启动 + Flask 线程）
├─ core/                   # 后端（app/config/models/services）
├─ web/                    # 前端（Jinja2 模板 + 原生 JS/CSS）
├─ docs/                   # 文档（API / 架构 / 分享 / 审查 / 更新日志）
├─ build/                  # 打包类（spec / 构建脚本 / 依赖清单）
├─ assets/                 # 仓库页面素材（截图等）
├─ dist_out/               # 构建产物（exe + appdata + 便携 zip，gitignore）
└─ VERSION                 # 版本号（运行时读取 + 便携包命名）
```

---

<a id="tech-stack"></a>
## 🧱 技术栈

- **后端**：Python 3.13 · Flask · jmcomic
- **前端**：原生 HTML / CSS / JavaScript（Jinja2 模板）
- **桌面壳**：pywebview + Edge WebView2
- **打包**：PyInstaller（onedir）

---

<a id="docs"></a>
## 📚 文档

| 文档 | 说明 |
|------|------|
| [docs/API.md](docs/API.md) | 后端 HTTP 接口契约 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 系统架构与 Android 复刻准备 |
| [docs/SHARE.md](docs/SHARE.md) | 分享包使用与分发说明 |
| [docs/CODE_REVIEW.md](docs/CODE_REVIEW.md) | 代码审查与修复记录 |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | 版本更新日志（含每版更新说明） |
| [docs/LICENSE](docs/LICENSE) | MIT 许可证 |

---

<a id="changelog"></a>
## 🗺 更新日志

- **v2.4.10** — 阅读器新增滚轮缩放：以鼠标位置为中心放大/缩小当前页；缩放复用同一张图不再重加载；放大后方向键 ↑/↓ 竖向平移。详见 [docs/CHANGELOG.md](docs/CHANGELOG.md)
- **v2.4.9** — 代码优化：封面/阅读接口去全量扫描、下载任务去重、批量下载真正限并发（设置项 `max_concurrent_downloads` 生效）。详见 [docs/CHANGELOG.md](docs/CHANGELOG.md)
- **v2.4.8** — 修复推荐页标签消失（标签按需补全扩展到推荐页）。详见 [docs/CHANGELOG.md](docs/CHANGELOG.md)
- **v2.4.7** — 修复搜索结果标签消失（前端接上 `/api/search/enrich` 按需补标签）。详见 [docs/CHANGELOG.md](docs/CHANGELOG.md)
- **v2.4.6** — 搜索优化：客户端连接复用 / 120s 结果缓存 / 封面直连 CDN。详见 [docs/CHANGELOG.md](docs/CHANGELOG.md)
- **v2.4.5** — 搜索提速、下载完立即可见、本地翻页即时、章节后台预取。详见 [docs/CHANGELOG.md](docs/CHANGELOG.md)
- **v2.4.4** — 重新发布（区分已发布的 2.4.3）；内置路径遍历防护、跨域收紧、设置白名单、历史清理守护线程、阅读接口 JSON 化等质量修复。详见 [docs/CHANGELOG.md](docs/CHANGELOG.md)
- **v2.4.3** — 修复搜索详情补全的 O(N²) 重复请求，搜索速度大幅提升
- **v2.4.2** — 推荐独立成「主页」、设置页返回栈、搜索框视觉去线、推荐自定义内容改为关键字 / 标签 / 作者三区块

---

<a id="disclaimer"></a>
## ⚠️ 免责声明

本项目仅用于**个人本地漫画阅读与管理**，不提供任何漫画内容的存储与分发。使用过程中请遵守所在地区法律法规及相关网站的使用条款。

---

<a id="license"></a>
## 📄 License

[MIT](docs/LICENSE) © JMComicReader
