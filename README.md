# JMComicReader · JM 漫画阅读器（Windows）

> 基于 **pywebview + Flask** 的 Windows 桌面漫画阅读 / 下载 / 管理工具。移动优先的响应式界面，支持搜索、在线阅读、本地下载、书架管理与智能推荐。

[![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D4?logo=windows&logoColor=white)](https://www.microsoft.com/windows)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](docs/LICENSE)
[![Release](https://img.shields.io/github/v/release/chenxi101w/JMComicReader-windows?label=latest%20release&color=blue)](https://github.com/chenxi101w/JMComicReader-windows/releases)
[![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)](https://www.python.org)

---

## ✨ 功能特性

- **多维度搜索**：关键词 / 标签 / 作者三种维度，搜索下拉三栏一目了然
- **搜索偏好库**：历史、标签、作者可收藏、可编辑、可恢复（误删进回收站），一键回填搜索框
- **在线阅读**：章节切换、图片预加载、缩放、拖拽、返回顶部
- **本地下载与书架**：单本 / 批量下载，书架统一管理
- **智能推荐主页**：基于你的搜索偏好（关键字 / 标签 / 作者）生成「为你推荐」
- **丰富设置**：主题（亮 / 暗 / 跟随系统）、代理、推荐源、下载路径等
- **数据可携带**：程序本体与个人数据分离，整个文件夹拷贝即完成迁移 / 备份

---

## 📥 下载与运行

### 方式一：绿色版（推荐，零配置）

1. 获取 `JMComicReader_vX.Y.Z_portable.zip`（**单一完整包**，约 35MB，已含程序本体 + 前端 + 运行说明，不含个人数据）
   - 仓库 **[Releases](https://github.com/chenxi101w/JMComicReader-windows/releases)** 页面，或从仓库文件中获取
2. 用 **7-Zip** / 系统自带解压工具解压
3. 双击 `JMComicReader.exe` 即可使用

> 说明：本仓库采用**完整包直传**（GitHub 仓库本身不限大小），无需合并分卷。若日后改走 GitHub Release 附件发布且单文件 >25MB，再拆成 `.z01` + `.zip` 两卷（下载到同一文件夹后用 7-Zip 右键 `.z01` →「解压到」即可自动合并）。

**运行要求**：Windows 10 / 11（64 位）；Edge **WebView2 运行时**（Win11 自带，缺则自动回退到系统浏览器模式，功能不受影响）。无需安装 Python，无需联网装依赖。

### 方式二：从源码构建

见下方「🛠 从源码构建」。

---

## 🚀 快速开始

| 入口 | 说明 |
|------|------|
| 主页 | 基于偏好的智能推荐，点作者 / 标签直达搜索 |
| 搜索 | 关键词 / 标签 / 作者三维度，支持收藏偏好回填 |
| 阅读 | 在线阅读，支持预加载、缩放、拖拽 |
| 设置 | 主题、代理、推荐源、下载管理等 |

> 首次启动会在 `JMComicReader.exe` **同级的 `UserData/`** 下自动生成数据目录，无需手动配置。

---

## 🛠 从源码构建

```bash
# 1. 创建虚拟环境并安装依赖
python -m venv build_venv
build_venv\Scripts\pip install -r docs/requirements.txt

# 2. 构建单目录版 exe（产物放到 D:\Game\JMComicReader）
build_venv\Scripts\python -m PyInstaller JMComicReader.spec ^
  --noconfirm --clean ^
  --distpath D:/Game/JMComicReader/dist ^
  --workpath D:/Game/JMComicReader/build

# 3. 部署：将 dist/JMComicReader 下的 exe + appdata 合并进运行目录，
#    保留已有的 UserData/（个人数据）不动
```

> ⚠️ 分享 / 分发时请删除个人数据：`UserData/DownloadedComics/`、`UserData/webview_data/`、`UserData/core/comics.db`、`UserData/core/jm_option.yml`、`.app_url`、`UserData/TempCache/`。

---

## 📂 项目结构

```
jmcomicreader-windows/
├─ desktop_app.py          # 桌面入口（pywebview 启动 + Flask 线程）
├─ core/
│  ├─ app.py               # Flask 路由与接口
│  └─ services/
│     └─ jm_crawler.py     # JM 源站爬虫 / 搜索 / 详情 / 下载
├─ web/                    # 前端（Jinja2 模板 + 原生 JS/CSS）
│  ├─ templates/
│  ├─ static/js/app.js
│  └─ static/css/style.css
├─ docs/                   # 完整文档（API / 架构 / 分享说明）
└─ JMComicReader.spec      # PyInstaller 配置
```

---

## 🧱 技术栈

- **后端**：Python 3.13 · Flask · jmcomic
- **前端**：原生 HTML / CSS / JavaScript（Jinja2 模板）
- **桌面壳**：pywebview + Edge WebView2
- **打包**：PyInstaller（onedir）

---

## 📚 文档

| 文档 | 说明 |
|------|------|
| [docs/README.md](docs/README.md) | 项目完整说明（功能、数据、构建） |
| [docs/API.md](docs/API.md) | 后端 HTTP 接口契约 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 系统架构与 Android 复刻准备 |
| [docs/SHARE.md](docs/SHARE.md) | 分享包使用与分发说明 |
| [docs/LICENSE](docs/LICENSE) | MIT 许可证 |
| [docs/requirements.txt](docs/requirements.txt) | Python 依赖清单 |

---

## 🗺 近期更新

- **v2.4.3** — 修复搜索详情补全的 O(N²) 重复请求，搜索速度大幅提升
- **v2.4.2** — 推荐独立成「主页」、设置页返回栈、搜索框视觉去线、推荐自定义内容改为关键字 / 标签 / 作者三区块

---

## ⚠️ 免责声明

本项目仅用于**个人本地漫画阅读与管理**，不提供任何漫画内容的存储与分发。使用过程中请遵守所在地区法律法规及相关网站的使用条款。

---

## 📄 License

[MIT](docs/LICENSE) © JMComicReader
