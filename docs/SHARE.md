# JMComicReader 使用与分享说明（v1.8.5）

## 一、运行要求
- **操作系统**：Windows 10 / Windows 11（64 位）。
- **WebView2 运行时**：Windows 11 已自带；Windows 10 若没有，首次打开会自动回退到「系统默认浏览器」模式，功能不受影响（只是窗口变成浏览器标签页）。想要原生窗口体验，去微软官网装一下「WebView2 Runtime」即可（可选，非必须）。
- **无需安装 Python**，也无需联网安装任何依赖。

## 二、怎么用
1. 把 `JMComicReader` 这个**整个文件夹**解压到任意位置（桌面、D 盘都行）。
2. 双击里面的 `JMComicReader.exe`。
3. 首次启动会在 `JMComicReader.exe` **同级的 `UserData/` 文件夹**下自动创建数据文件夹（`core`、`DownloadedComics`、`TempCache`、`webview_data`），不用手动建。
4. 关闭就是正常退出，数据都会保留。

## 三、数据在哪 / 怎么备份
所有用户数据都放在 `JMComicReader.exe` **同级的 `UserData/` 文件夹**里，与程序本体 `App/` 完全分离（`App/` 只放 exe 和运行时，`UserData/` 只放你的个人数据）：
- `UserData/core/comics.db` —— 下载历史、设置、收藏等数据库
- `UserData/core/jm_option.yml` —— 漫画源配置
- `UserData/DownloadedComics/` —— 下载下来的漫画
- `UserData/webview_data/` —— 浏览器本地存储（收藏标签、搜索历史、常用标签）

**备份 / 迁移**：直接把整个 `JMComicReader` 文件夹拷贝走即可（`App/` 程序 + `UserData/` 数据都跟着走），换电脑也不丢。需要单独备份个人数据时，只拷 `UserData/` 即可。

## 四、怎么分享给别人
程序本体在 `App/`，数据在 `UserData/`，**分享 = 把整个 `JMComicReader` 文件夹打包发过去**：
- 微信 / QQ 传文件夹，或先压缩成 `JMComicReader.zip` 再发。
- 对方解压后双击 `JMComicReader.exe` 就能用，零配置。

> 注意：如果你不想把**自己已下载的漫画**（`UserData/DownloadedComics/`）和**个人记录**一起发出去，分享前把整个 `UserData/` 删掉即可（或只删其中的 `DownloadedComics/`、`webview_data/`、`core/comics.db`、`core/jm_option.yml`、`.app_url`、`TempCache/`），对方首次打开会自动生成空白的一套。

## 五、已知说明
- 首次启动会尝试占用本机 `28888` 端口作为内部服务端口（已写入 `.app_url` 固定下来，避免端口变化导致本地数据被当新站点清空）。如该端口被占用会自动换一个。
- 本程序仅用于个人本地漫画阅读与管理。
