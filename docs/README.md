# JMComicReader / JM漫画阅读器（Windows）

一个基于 [pywebview](https://pywebview.flowrl.com/) + Flask 的 Windows 桌面漫画阅读/下载器，界面采用类 App 的移动优先设计，支持搜索、阅读、下载、书架管理与推荐。

> 当前版本：**v2.4.3**

---

## 功能一览

- 搜索漫画（关键词 / 作者 / JM 号）
- 标签与作者收藏、快捷填入搜索栏
- 漫画详情页、在线阅读、章节切换
- 本地下载、书架管理、批量下载
- 阅读器预加载、缩放、拖拽、返回顶部
- 可恢复删除的「搜索偏好库」（历史 / 标签 / 作者）
- 设置面板：主题、推荐源、下载路径等

---

## 下载与运行

### 方式一：直接下载绿色版

1. 访问本仓库右侧的 [Releases](https://github.com/chenxi101w/JMComicReader-windows/releases)。
2. 下载 `JMComicReader_vX.Y.Z_portable.zip`。
3. 解压到任意位置，双击 `JMComicReader.exe` 即可使用。

### 运行要求

- Windows 10 / Windows 11（64 位）
- 已自带或已安装 Microsoft Edge WebView2 运行时（Windows 11 通常自带；未安装时会自动回退到系统浏览器标签页）
- 无需安装 Python，无需联网安装依赖

---

## 数据在哪 / 怎么备份

所有用户数据都在 `JMComicReader.exe` **同级**自动生成的文件夹中：

| 路径 | 说明 |
|------|------|
| `core/comics.db` | 下载历史、设置、收藏等数据库 |
| `core/jm_option.yml` | 漫画源配置 |
| `DownloadedComics/` | 已下载的漫画 |
| `TempCache/` | 运行时临时缓存 |
| `webview_data/` | 浏览器本地存储（搜索历史、收藏标签等） |
| `.app_url` | 内部服务端口记录 |

**备份**：直接拷贝整个程序文件夹即可；若只想备份个人数据，拷贝上述文件/文件夹。

---

## 自行构建

如果你要从源码构建：

```bash
# 1. 创建虚拟环境并安装依赖
python -m venv build_venv
build_venv\Scripts\pip install -r requirements.txt

# 2. 构建单目录版 exe（以 v2.4.1 为例，把产物放到 D:\Game\JMComicReader）
build_venv\Scripts\python -m PyInstaller JMComicReader.spec \
  --noconfirm --clean \
  --distpath D:/Game/JMComicReader/dist \
  --workpath D:/Game/JMComicReader/build
```

> 构建产物中**不要包含**用户数据目录；分享给别人时请删除 `DownloadedComics/`、`webview_data/`、`core/comics.db`、`.app_url`、`TempCache/` 等个人数据。

---

## 技术栈

- 后端：Python 3.13 + Flask + jmcomic
- 前端：原生 HTML / CSS / JavaScript（Jinja2 模板）
- 桌面壳：pywebview + Edge WebView2
- 打包：PyInstaller（onedir）

---

## 免责声明

本项目仅用于个人本地漫画阅读与管理，不提供漫画内容存储与分发。使用过程中请遵守所在地区法律法规及相关网站使用条款。

---

## License

[MIT](LICENSE)
