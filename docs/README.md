# JMComicReader

一个现代化、轻量级的本地 JM 漫画阅读器与下载管理器。桌面端采用 Flask + 原生 WebView 架构，前端为响应式 Web UI；手机端（Android）另见 `jmcomic-android` 工程。

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.13-green.svg)

---

## 功能特性

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
- 拉黑作品/作者（本地灰显，文件保留）
- 标签/作者同义词别名，搜索结果自动归一化

---

## 快速开始

### 源码运行（Windows）

**环境要求：** Python 3.13+

```bash
cd "jmcomicreader-windows"
pip install -r docs/requirements.txt
python desktop_app.py
```

启动后自动打开桌面窗口，或浏览器访问 `http://127.0.0.1:<port>`（端口写入 `.app_url`）。

### 构建桌面版

```bash
python -m PyInstaller JMComicReader.spec --distpath D:/Game/JMComicReader/dist_tmp --workpath D:/Game/JMComicReader/build_tmp
```

产物为 onedir 目录，用户数据（数据库/下载/缓存）与 `JMComicReader.exe` 同级，便于携带。

---

## 项目结构

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

## 手机端

Android 项目：`D:\code\Android\AI CODE\jmcomic-android`（Kotlin + Jetpack Compose）。

手机端与桌面端共享：
- 本地目录结构（`DownloadedComics/JM-{id}/...`）
- 数据库表结构（分类/下载历史/搜索历史/拉黑/别名/配置）
- JM 网络协议、scramble 解码、同义词种子

详见 `docs/ARCHITECTURE.md`。

---

## 免责声明

本项目仅供学习交流使用，请勿用于非法用途。所有漫画资源均来自网络，本项目不存储任何漫画内容。
