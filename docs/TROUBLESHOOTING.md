# 故障排查

> 本文只记录**用户运行时会遇到的故障**（环境 / 运行时问题）。代码层面的 bug 与修复请看 [docs/CODE_REVIEW.md](CODE_REVIEW.md)。

## 通用故障（与版本无关，最常见）

### 界面错乱 / "UI 变样"了

- **现象**：页面铺满整个窗口宽度（没有居中的卡片列）、底部导航栏被压得很矮、毛玻璃/渐变背景消失、控件错位。
- **原因**：系统**缺少或损坏 Microsoft Edge WebView2 运行时**。程序用 pywebview 承载界面，没装 WebView2 时会自动退回老旧的 IE 内核；而本界面依赖 `var()` / `color-mix()` / `flex`，IE 全都不支持 → 整页错乱。这是运行环境问题，**不是程序本身的 bug**。
- **确认**：程序已内置引擎自检——若检测到退回 IE，会直接弹出「⚠ 界面渲染引擎不可用，需要 Microsoft Edge WebView2」浮层。看到它就 100% 是这原因。
- **解决**：
  1. 装 WebView2 Runtime（Evergreen Bootstrapper，**选 x64**）：https://developer.microsoft.com/zh-cn/microsoft-edge/webview2/
     或管理员 PowerShell：`winget install Microsoft.EdgeWebView2.Runtime`
  2. 装完后**彻底退出程序，重新打开**。
- **装了还不行**：确认装的是 x64；到「控制面板 → 程序」修复/重装 Runtime；企业环境可能被组策略或杀软拦截。

---

## 版本故障（按版本，最新在前）

> 记录**某个版本特有**的、用户运行时会遇到的故障 / 回归。通用问题见上方。遇到后按下面格式补一条即可（只填运行时现象，不写代码细节）。

### vX.Y.Z — 一句话问题描述
- **现象**：
- **原因**：
- **解决 / 状态**：

（暂无记录，遇到后补充）
