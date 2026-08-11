# GitHub 页面设置清单（JMComicReader）

README 已重写完成。本清单是**网页端手动操作项**——这些无法用代码提交完成，需要你在 GitHub 仓库页面点几下。按顺序做，15 分钟搞定一个专业感拉满的仓库主页。

---

## 1. 改名仓库：`JMComicReader-windows` → `JMComicReader`

> ⚠️ **改名前先确认占用**：仓库名在**同一账号下**全局唯一。请先打开 `https://github.com/chenxi101w/JMComicReader`：
> - 若显示 **404（Repository not found）** → 没被占用，可改名。
> - 若显示了一个仓库 → 已被占用，需另选（如 `jmcomic-reader-win`）。

步骤：
1. 打开仓库 → 右侧 **⚙ Settings** → **General**
2. 找到 **Repository name**，把 `JMComicReader-windows` 改为 `JMComicReader`
3. 点 **Rename**
4. 完成后，旧地址 `github.com/chenxi101w/JMComicReader-windows` 会**自动 302 重定向**到新地址——**Star、Release 下载、已分享的链接全部不丢**。

> 本仓库 README 内的所有 `JMComicReader-windows` 链接已同步改为 `JMComicReader`，改名后无需再改 README。

---

## 2. About 区块（仓库主页右侧）

在仓库主页右侧 **About** → 点 ⚙ 编辑，填写：

- **Description（一句话描述）**：
  ```
  Windows 桌面 JM 漫画阅读器 · 搜索 / 在线阅读 / 批量下载 / 书架管理 / 智能推荐 · 绿色免安装
  ```
- **Website**：留空即可（或填 Releases 页 `https://github.com/chenxi101w/JMComicReader/releases`）
- **Topics**（输入后回车添加，建议）：
  ```
  windows
  comic-reader
  manga
  pywebview
  flask
  jmcomic
  desktop-app
  python
  ```
- ✅ 勾选 **Releases**（在主页显示最新版徽标）
- ⬜ Packages / 其他无需勾选

---

## 3. 截图（预览区已预留）

README 顶部「📸 预览」小节已用 HTML 注释占位。你本地启动程序，截 3 张图放入 `assets/screenshots/`：

| 文件名 | 内容 | 建议 |
|--------|------|------|
| `home.png` | 主页（智能推荐） | 窗口最大化，截整体 |
| `search.png` | 搜索结果（多维度） | 搜一个词，截下拉三栏 |
| `reader.png` | 阅读器（滚轮缩放） | 打开一页漫画，最好放大过显出效果 |

放入后，打开 README.md，找到「📸 预览」小节里被 `<!-- -->` 包裹的 `<div align="center">…</div>`，**删除首尾的注释标记**即可显示。

> 图片建议宽度 760px 左右、PNG 格式，单张 < 500KB，加载快。

---

## 4. Social Preview（社交分享图，可选但推荐）

当链接被分享到微信 / Telegram / Twitter 等平台时，会显示这张图，是第一眼的门面。

1. 仓库 **⚙ Settings** → **General** → **Social preview**
2. 上传一张 **1280×640** 的封面图（可用主页截图裁剪，或加个标题文字）
3. 没图也能用，但有图明显更专业

---

## 5. 顺手提升的小项

- **Pin 仓库**：在个人 Profile 页把本仓库 Pin 到首页，增加曝光。
- **Releases 写清楚**：每个 Release 的 Notes 粘贴对应 `dist_out/JMComicReader_vX.Y.Z_更新说明.md` 内容，下载者一眼看到改了啥。
- **Issue 模板**（可选）：`.github/` 下加 `bug_report.md` / `feature_request.md`，引导用户规范反馈。
