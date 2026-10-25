#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
JMComicReader 桌面启动入口（v2.14.0 翻新版）。

启动本地 Flask 服务并用原生桌面窗口承载页面；所有用户数据（数据库 / 下载 /
缓存）统一放到 exe 同级 UserData/ 目录，更新程序时只覆盖 exe 与 appdata/，
用户数据不受影响。启动链对齐轻量版：原生预启动窗（秒弹）→ 内嵌 splash →
后端并行预热 → 无缝切主界面；端口探测用 bind（免疫杀软回环拦截拖慢）。
"""

import io
import os
import shutil
import socket
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import webbrowser
import ctypes
from pathlib import Path

from werkzeug.serving import make_server


APP_NAME = "JMComicReader"
WINDOWS_APP_ID = "JMComicReader.Desktop"
WINDOW_TITLE = "JMComicReader"
WINDOW_WIDTH = 1360
WINDOW_HEIGHT = 900
WINDOW_MIN_SIZE = (980, 680)

# 固定端口保证 localStorage / WebView2 存储不因端口变化而丢失。
DEFAULT_PORT = 28888

# 启动加载动画（内嵌 HTML，零外部依赖 + 深浅色自适应）——窗口立即出现，
# 后端在后台线程加载，就绪后同窗口无缝切到主界面。
_SPLASH_HTML = """<!doctype html><html><head><meta charset="utf-8">
<style>
  :root { color-scheme: light dark; }
  * { margin:0; padding:0; box-sizing:border-box; }
  body { height:100vh; display:flex; flex-direction:column; align-items:center;
         justify-content:center; background:#f5f6fa; color:#5a6478;
         font-family: system-ui,-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;
         -webkit-user-select:none; user-select:none; }
  @media (prefers-color-scheme: dark) { body { background:#14161c; color:#aab2c5; } }
  .logo { width:88px; height:88px; border-radius:24px; display:flex; align-items:center;
          justify-content:center; background:linear-gradient(135deg,#7B6FE0,#9B8CF0);
          box-shadow:0 12px 34px rgba(123,111,224,.35); animation:breathe 2.4s ease-in-out infinite; }
  .logo svg { width:46px; height:46px; stroke:#fff; }
  @keyframes breathe { 0%,100% { transform:scale(1); } 50% { transform:scale(1.06); } }
  .title { margin-top:24px; font-size:17px; font-weight:700; letter-spacing:.6px; }
  .spinner { width:26px; height:26px; margin-top:28px; border-radius:50%;
             border:3px solid rgba(123,111,224,.22); border-top-color:#7B6FE0;
             animation:spin .85s linear infinite; }
  @keyframes spin { to { transform:rotate(360deg); } }
  .hint { margin-top:14px; font-size:12.5px; opacity:.72; }
</style></head><body>
  <div class="logo"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
    stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12"/><path d="M7 10l5 5 5-5"/>
    <path d="M5 21h14"/></svg></div>
  <div class="title">JMComicReader</div>
  <div class="spinner"></div>
  <div class="hint">正在启动…</div>
</body></html>"""

# 加载动画最短展示时长（秒）——定稿「自适应」= 0：
#   启动快（热启动 ~0.6s）→ 一闪而过、直接进界面（不为动画付一秒）；
#   启动慢（冷启动/杀软扫描）→ 动画自然播放、起「有反馈」掩护作用。
# 如需强制保底展示（快速启动也看完整动画），把此值改为如 1.5 即可。
_SPLASH_MIN_SECONDS = 0

_BOOT_ERROR_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
  body { height:100vh; display:flex; flex-direction:column; align-items:center;
         justify-content:center; gap:12px; background:#f5f6fa; color:#5a6478;
         font-family:system-ui,'Microsoft YaHei',sans-serif; }
  @media (prefers-color-scheme: dark) { body { background:#14161c; color:#aab2c5; } }
  b { font-size:16px; } p { font-size:13px; opacity:.75; }
</style></head><body>
  <b>启动失败</b><p>后端服务未能就绪。请查看 UserData/startup_error.log 后重试。</p>
</body></html>"""


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def get_exec_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def get_default_user_base_dir() -> Path:
    env_base_dir = os.environ.get("BASE_DIR")
    if env_base_dir:
        return Path(env_base_dir).expanduser().resolve()
    if is_frozen() and os.name == "nt":
        # 打包后：数据目录 = exe 同级的 UserData（与运行时 appdata/ 分离，
        # 便于单独备份；更新只覆盖 exe + appdata 不丢数据）。
        return get_exec_dir() / "UserData"
    return get_exec_dir()


def _move_if_missing(source: Path, target: Path):
    if not source.exists():
        return
    if target.exists():
        if source.is_dir() and target.is_dir():
            try:
                if any(target.iterdir()):
                    return
            except OSError:
                return
            for child in source.iterdir():
                shutil.move(str(child), str(target / child.name))
            try:
                source.rmdir()
            except OSError:
                pass
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))


def migrate_legacy_data(base_dir: Path):
    """把旧版本散落各处的用户数据迁移到当前数据目录（exe 同目录）。

    只在打包环境中执行，且目标不存在时才迁移，避免覆盖新数据。
    """
    if not is_frozen() or os.environ.get("BASE_DIR"):
        return
    exec_dir = get_exec_dir()
    # 旧数据可能来源：① exe 同目录 ② 项目根目录 ③ %LOCALAPPDATA%/JMComicReader/
    legacy_roots = [exec_dir, exec_dir.parent.parent]
    try:
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        if local_appdata:
            legacy_roots.append(Path(local_appdata) / APP_NAME)
    except Exception:
        pass
    relative_items = [
        "DownloadedComics",
        "TempCache",
        "webview_data",
        "core/comics.db",
        "jm_option.yml",
        "core/jm_option.yml",
        ".app_url",
    ]
    for legacy_root in legacy_roots:
        if legacy_root == base_dir or not legacy_root.exists():
            continue
        for rel in relative_items:
            source = legacy_root / rel
            target = base_dir / rel
            try:
                _move_if_missing(source, target)
            except Exception as exc:
                print(f"迁移数据失败: {source} -> {target}: {exc}")


def configure_runtime_env() -> Path:
    """创建运行时目录并把路径写入环境变量，供 core.config 读取。"""
    base_dir = get_default_user_base_dir()
    migrate_legacy_data(base_dir)
    download_dir = base_dir / "DownloadedComics"
    temp_cache_dir = base_dir / "TempCache"
    backend_dir = base_dir / "core"

    download_dir.mkdir(parents=True, exist_ok=True)
    temp_cache_dir.mkdir(parents=True, exist_ok=True)
    backend_dir.mkdir(parents=True, exist_ok=True)

    os.environ["BASE_DIR"] = str(base_dir)
    os.environ["DOWNLOAD_DIR"] = str(download_dir)
    os.environ["TEMP_CACHE_DIR"] = str(temp_cache_dir)
    return base_dir


def _is_port_free(port: int) -> bool:
    """用 bind 探测端口是否空闲（比 connect_ex 更稳：不产生出站流量，
    不会被火绒/卡巴等杀软对本地回环连接的拦截拖慢 ~1s/次）。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def get_or_create_port(base_dir: Path) -> int:
    """复用上次使用的端口；被占用或首次运行时使用默认端口并写回 .app_url。"""
    port_file = base_dir / ".app_url"
    saved = None
    try:
        if port_file.exists():
            saved = int(port_file.read_text().strip())
    except Exception:
        pass
    for candidate in (saved, DEFAULT_PORT):
        if candidate and candidate > 0 and _is_port_free(candidate):
            port_file.write_text(str(candidate))
            return candidate
    # 都不可用：随机端口兜底
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    port_file.write_text(str(port))
    return port


def configure_windows_app_id():
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_ID)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 原生预启动窗（Win32 ctypes，零额外依赖）
#
# 启动链路：run_desktop → 先弹原生窗（秒级可见，消除点开到 splash 弹出的空白期）
#           → import webview → 内嵌 splash → webview.start 初始化 WebView2
#           → load_url 主界面。
#
# 内嵌 splash 出现在 import webview *之后*，因此点开到 splash 弹出之间仍有
# 一段纯黑屏 / 无响应期。这里在 import webview *之前* 用 Win32 直接弹一个原生窗，
# 把那段空白期变成「正在启动…」，体感从「点了没反应」变成「秒弹窗」。
#
# 任何异常都静默降级为「无原生 splash」，绝不阻塞主流程。
# ---------------------------------------------------------------------------
class NativeSplash:
    def __init__(self, title="JMComicReader", hint="正在启动…"):
        self.hwnd = None
        self._thread = None
        self._wndproc_cb = None
        self._close_requested = False
        self._title = title
        self._hint = hint
        if os.name != "nt":
            return
        try:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        except Exception:
            self.hwnd = None

    # ---- Win32 常量 ----
    _CS_HREDRAW = 0x2
    _CS_VREDRAW = 0x1
    _WS_POPUP = 0x80000000
    _WS_VISIBLE = 0x10000000
    _WS_EX_TOPMOST = 0x8
    _WM_DESTROY = 0x2
    _WM_PAINT = 0xF
    _WM_CLOSE = 0x10
    _IDC_ARROW = 32512
    _COLOR_WINDOW = 5
    _DT_CENTER = 0x1
    _DT_SINGLELINE = 0x20
    _TRANSPARENT = 1
    _NULL_PEN = 8
    _SM_CXSCREEN = 0
    _SM_CYSCREEN = 1

    def _run(self):
        try:
            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32
            kernel32 = ctypes.windll.kernel32
            WNDPROC = ctypes.WINFUNCTYPE(
                ctypes.c_long, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p
            )

            class _WNDCLASSEXW(ctypes.Structure):
                _fields_ = [
                    ("cbSize", ctypes.c_uint),
                    ("style", ctypes.c_uint),
                    ("lpfnWndProc", ctypes.c_void_p),
                    ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int),
                    ("hInstance", ctypes.c_void_p),
                    ("hIcon", ctypes.c_void_p),
                    ("hCursor", ctypes.c_void_p),
                    ("hbrBackground", ctypes.c_void_p),
                    ("lpszMenuName", ctypes.c_wchar_p),
                    ("lpszClassName", ctypes.c_wchar_p),
                    ("hIconSm", ctypes.c_void_p),
                ]

            class _RECT(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long),
                ]

            class _PAINTSTRUCT(ctypes.Structure):
                _fields_ = [
                    ("hdc", ctypes.c_void_p),
                    ("fErase", ctypes.c_int),
                    ("rcPaint", _RECT),
                    ("fRestore", ctypes.c_int),
                    ("fIncUpdate", ctypes.c_int),
                    ("rgbReserved", ctypes.c_byte * 32),
                ]

            self._wndproc_cb = WNDPROC(self._wndproc)

            hinst = kernel32.GetModuleHandleW(None)
            cls = _WNDCLASSEXW()
            cls.cbSize = ctypes.sizeof(cls)
            cls.style = self._CS_HREDRAW | self._CS_VREDRAW
            cls.lpfnWndProc = ctypes.cast(self._wndproc_cb, ctypes.c_void_p)
            cls.hInstance = hinst
            cls.hCursor = user32.LoadCursorW(0, self._IDC_ARROW)
            cls.hbrBackground = ctypes.c_void_p(self._COLOR_WINDOW + 1)
            cls.lpszClassName = "JMComicReaderSplash"
            if not user32.RegisterClassExW(ctypes.byref(cls)):
                return

            sw = user32.GetSystemMetrics(self._SM_CXSCREEN)
            sh = user32.GetSystemMetrics(self._SM_CYSCREEN)
            w, h = 360, 300
            x, y = (sw - w) // 2, (sh - h) // 2
            self.hwnd = user32.CreateWindowExW(
                self._WS_EX_TOPMOST,
                "JMComicReaderSplash",
                self._title,
                self._WS_POPUP | self._WS_VISIBLE,
                x, y, w, h,
                0, 0, hinst, 0,
            )
            if not self.hwnd:
                self.hwnd = None
                return
            # 若 close() 已先被调用（极端竞态），立即销毁
            if self._close_requested:
                user32.DestroyWindow(self.hwnd)
                return

            msg = ctypes.wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            self.hwnd = None

    def _wndproc(self, hwnd, msg, wparam, lparam):
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        if msg == self._WM_PAINT:
            self._on_paint(hwnd, user32, gdi32)
            return 0
        if msg == self._WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _on_paint(self, hwnd, user32, gdi32):
        ps = self._PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        rect = self._RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rect))
        try:
            # 背景 #f5f6fa -> COLORREF 0x00BBGGRR = 0x00FAF6F5
            bg = gdi32.CreateSolidBrush(0x00FAF6F5)
            gdi32.FillRect(hdc, ctypes.byref(rect), bg)
            gdi32.DeleteObject(bg)

            cx = (rect.left + rect.right) // 2
            logo_top = rect.top + 44
            # 无边框笔（避免 logo 黑边）
            null_pen = gdi32.GetStockObject(self._NULL_PEN)
            gdi32.SelectObject(hdc, null_pen)
            # 紫色 logo 块 #7B6FE0 -> 0x00E06F7B
            purple = gdi32.CreateSolidBrush(0x00E06F7B)
            gdi32.SelectObject(hdc, purple)
            gdi32.RoundRect(hdc, cx - 44, logo_top, cx + 44, logo_top + 88, 24, 24)
            gdi32.DeleteObject(purple)

            # 文本：标题 + 提示（#5a6478 -> 0x0078645A）
            gdi32.SetBkMode(hdc, self._TRANSPARENT)
            gdi32.SetTextColor(hdc, 0x0078645A)
            tr1 = self._RECT(rect.left, logo_top + 112, rect.right, logo_top + 142)
            gdi32.DrawTextW(
                hdc, self._title, -1, ctypes.byref(tr1),
                self._DT_CENTER | self._DT_SINGLELINE,
            )
            tr2 = self._RECT(rect.left, logo_top + 152, rect.right, logo_top + 178)
            gdi32.DrawTextW(
                hdc, self._hint, -1, ctypes.byref(tr2),
                self._DT_CENTER | self._DT_SINGLELINE,
            )
        finally:
            user32.EndPaint(hwnd, ctypes.byref(ps))

    def close(self):
        """关闭原生预启动窗（WebView2 splash 已就绪后调用，视觉无缝衔接）。"""
        self._close_requested = True
        hwnd = self.hwnd
        if hwnd:
            try:
                ctypes.windll.user32.PostMessageW(hwnd, self._WM_CLOSE, 0, 0)
            except Exception:
                pass
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2)


WM_SETICON = 0x0080
ICON_SMALL = 0
ICON_BIG = 1
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
GCLP_HICON = -14
GCLP_HICONSM = -34
GA_ROOT = 2


def _icon_dbg(msg):
    """图标切换调试日志：写入用户数据目录 icon_dbg.log，便于定位运行时问题。"""
    try:
        base = get_default_user_base_dir()
        base.mkdir(parents=True, exist_ok=True)
        with open(base / "icon_dbg.log", "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def _find_main_hwnd():
    """获取 app 主窗口句柄（当前进程的可见顶层窗口）。

    优先枚举标题精确匹配 WINDOW_TITLE 的窗口；找不到则回退到当前进程
    面积最大的可见顶层窗口。不依赖 window.native——打包后 native 在
    webview.start() 创建窗口之前为 None，且各 pywebview 后端内部结构不同。
    """
    import ctypes
    import ctypes.wintypes as wt

    user32 = ctypes.windll.user32
    cur_pid = os.getpid()
    matches = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def _enum_cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != cur_pid:
            return True
        try:
            buf = ctypes.create_unicode_buffer(512)
            length = user32.GetWindowTextW(hwnd, buf, 512)
            title = buf.value[:length] if length else ""
        except Exception:
            title = ""
        try:
            rect = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            area = (rect.right - rect.left) * (rect.bottom - rect.top)
        except Exception:
            area = 0
        matches.append((title, area, hwnd))
        return True

    try:
        user32.EnumWindows(_enum_cb, 0)
    except Exception as exc:
        _icon_dbg("[icon] EnumWindows 失败: " + repr(exc))
        return None

    if not matches:
        return None
    for title, _, hwnd in matches:
        if title == WINDOW_TITLE:
            return hwnd
    matches.sort(key=lambda m: m[1], reverse=True)
    return matches[0][2]


def _apply_window_icon(window, bundle_root, key):
    """把窗口/任务栏图标设置为指定 key 的 .ico（对齐 Android「应用图标」设置）。

    实现：EnumWindows 定位主窗口（取其根窗口）后：
      1) SetClassLongPtrW 修改窗口类图标（GCLP_HICON/HICONSM）——任务栏、Alt+Tab
         及后续新建窗口均使用该类图标；
      2) SendMessageW 发送 WM_SETICON（ICON_SMALL 标题栏小图标 + ICON_BIG 大图标）。
    不依赖 window.native（打包后 native 在窗口创建前为 None），不依赖 System.Drawing
    （规避 PyInstaller 打包后 pythonnet 程序集缺失）。图标句柄保留不销毁，避免类图标
    句柄失效导致图标不显示。成功返回 True；窗口尚未创建或失败返回 False，调用方重试。
    """
    try:
        import os
        import ctypes
        import ctypes.wintypes as wt
        key = str(key or "").strip().lower() or "default"
        ico = os.path.join(bundle_root, "web", "static", "img", "icons", key + ".ico")
        if not os.path.exists(ico):
            ico = os.path.join(bundle_root, "web", "static", "img", "icons", "default.ico")
        if not os.path.exists(ico):
            _icon_dbg(f"[icon] ico 缺失: bundle={bundle_root}")
            return False
        _icon_dbg(f"[icon] 目标={key} ico={os.path.basename(ico)}")

        user32 = ctypes.windll.user32
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.LoadImageW.argtypes = [
            ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint,
            ctypes.c_int, ctypes.c_int, ctypes.c_uint,
        ]
        hicon = user32.LoadImageW(None, ico, IMAGE_ICON, 0, 0, LR_LOADFROMFILE)
        _icon_dbg(f"[icon] LoadImageW hicon={hicon}")
        if not hicon:
            return False
        self_ptr = ctypes.c_void_p(hicon)

        try:
            hwnd = _find_main_hwnd()
            _icon_dbg(f"[icon] 主窗口句柄 hwnd={int(hwnd) if hwnd else None}")
            if not hwnd:
                return False
            # 取根窗口（若枚举到的是 owned/popup 窗口则回到最顶层根窗口）
            root = ctypes.c_void_p(user32.GetAncestor(hwnd, GA_ROOT))
            if not root.value:
                root = hwnd
            _icon_dbg(f"[icon] 根窗口句柄 root={int(root.value)}")

            # 窗口类图标：任务栏 / Alt+Tab / 后续新建窗口生效
            user32.SetClassLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_void_p]
            user32.SetClassLongPtrW.restype = ctypes.c_void_p
            user32.SetClassLongPtrW(root, GCLP_HICON, self_ptr)
            user32.SetClassLongPtrW(root, GCLP_HICONSM, self_ptr)

            # 窗口图标：标题栏小图标（ICON_SMALL）+ 任务栏大图标（ICON_BIG）
            user32.SendMessageW.argtypes = [
                wt.HWND, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
            ]
            user32.SendMessageW(root, WM_SETICON, ctypes.c_void_p(ICON_SMALL), self_ptr)
            user32.SendMessageW(root, WM_SETICON, ctypes.c_void_p(ICON_BIG), self_ptr)
            _icon_dbg("[icon] SetClass + WM_SETICON OK（句柄保留）")
            return True
        except Exception as exc:
            _icon_dbg("[icon] 设置窗口图标失败: " + repr(exc))
            return False
    except Exception as exc:
        _icon_dbg("[icon] apply 失败: " + repr(exc))
        return False


def _window_icon_watcher(window, bundle_root, stop_event):
    """轮询后端 app_icon 配置，变化时及时更新窗口/任务栏图标（3s 间隔）。

    窗口在 webview.start() 之后才创建，因此设置失败（窗口未就绪）时持续重试，
    直到首次生效；用户切换图标后再次触发重设。
    """
    import time
    last = None
    _applied = False
    while not stop_event.is_set():
        try:
            from core.models.database import get_system_config
            cur = (get_system_config("app_icon") or "default").strip().lower() or "default"
            if cur != last:
                _icon_dbg(f"[icon] watcher 读到 app_icon={cur}")
                last = cur
                _applied = False
            if not _applied and _apply_window_icon(window, bundle_root, cur):
                _applied = True
        except Exception as exc:
            _icon_dbg("[icon] watcher 读取失败: " + repr(exc))
        time.sleep(3)

def wait_for_server(url: str, timeout: float = 10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1):
                return
        except Exception:
            time.sleep(0.1)
    raise TimeoutError(f"服务启动超时: {url}")


class FlaskServerThread(threading.Thread):
    def __init__(self, app, host: str, port: int):
        super().__init__(daemon=True)
        self._server = make_server(host, port, app, threaded=True)

    def run(self):
        self._server.serve_forever()

    def shutdown(self):
        self._server.shutdown()


def _log_error(msg: str):
    """启动错误写入用户目录日志，窗口打不开时也能留痕排查。"""
    try:
        base = get_default_user_base_dir()
        base.mkdir(parents=True, exist_ok=True)
        with open(base / "startup_error.log", "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def configure_utf8_output():
    """强制 stdout/stderr 使用 UTF-8。

    Windows 中文系统默认 GBK，jmcomic 打印含特殊字符（如 ♡）的漫画标题 /
    进度信息时会抛 'gbk' codec can't encode character，导致下载流程中断。
    需在 import 业务模块之前调用，确保全局生效。
    """
    if sys.platform != "win32":
        return
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "buffer"):
            continue
        if getattr(stream, "_utf8_forced", False):
            continue
        try:
            new_stream = io.TextIOWrapper(
                stream.buffer,
                encoding="utf-8",
                errors="replace",  # 极少数无法编码的字符用  替代而非崩溃
                newline=None,
                line_buffering=getattr(stream, "line_buffering", False),
            )
            new_stream._utf8_forced = True
            setattr(sys, stream_name, new_stream)
        except Exception:
            pass


def _open_window(url: str, base_dir: Path, boot_backend=None, native_splash=None):
    """splash 先行：窗口立即显示加载动画，后端就绪后同窗口无缝切到主界面。

    回退链：无 pywebview → 同步起后端 + 系统浏览器；后端启动失败 → 窗口显示
    错误提示（并写 startup_error.log，重启不丢线索）。
    """
    try:
        import webview
    except ImportError:
        print("未安装 pywebview，回退到浏览器模式。")
        print(f"当前数据目录: {base_dir}")
        if boot_backend:
            boot_backend()
        if native_splash is not None:
            try:
                native_splash.close()
            except Exception:
                pass
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return

    window = webview.create_window(
        WINDOW_TITLE,
        html=_SPLASH_HTML,          # 先挂加载动画（内嵌，无外部依赖）
        width=WINDOW_WIDTH,
        height=WINDOW_HEIGHT,
        min_size=WINDOW_MIN_SIZE,
    )

    # 后端启动与 WebView2 引擎初始化并行：窗口已挂 splash，引擎在 webview.start
    # 内初始化；这段时间同步跑后端 import + 起服务，省掉串行等待。
    _boot_result = {}
    def _run_boot():
        try:
            _boot_result["ok"] = boot_backend() if boot_backend else True
        except Exception as exc:
            _log_error(f"[启动] 后端启动异常: {exc}\n{traceback.format_exc()}")
            _boot_result["ok"] = False
    _boot_thread = threading.Thread(target=_run_boot, daemon=True)
    _boot_thread.start()

    def _on_gui_ready():
        """GUI 就绪后（WebView2 引擎已初始化）：等后端就绪即可切主界面。"""
        t0 = time.time()
        _boot_thread.join()                    # 通常引擎初始化期间已完成
        # 关闭原生预启动窗：WebView2 内嵌 splash 已就绪，视觉无缝衔接
        if native_splash is not None:
            try:
                native_splash.close()
            except Exception:
                pass
        ok = _boot_result.get("ok", False)
        # 最短展示时长：动画不能被「太快」吃掉（渲染在 GUI 线程，此处 sleep 不影响动画播放）
        remain = _SPLASH_MIN_SECONDS - (time.time() - t0)
        if remain > 0:
            time.sleep(remain)
        try:
            if ok:
                window.load_url(url)          # 无缝切主界面
            else:
                window.load_url("data:text/html;charset=utf-8," + urllib.parse.quote(_BOOT_ERROR_HTML))
        except Exception as exc:
            _log_error(f"[启动] 切换主界面失败: {exc}\n{traceback.format_exc()}")

    try:
        # private_mode=False 才能持久化 localStorage；storage_path 指定
        # WebView2 用户数据目录，配合固定端口实现数据持久化。
        webview.start(
            _on_gui_ready,
            debug=False,
            private_mode=False,
            storage_path=str(base_dir / "webview_data"),
        )
    except Exception as exc:
        tb = traceback.format_exc()
        _log_error(f"[回退浏览器] pywebview 启动失败: {exc}\n{tb}")
        print(f"pywebview 启动失败，已回退到浏览器模式: {exc}")
        if native_splash is not None:
            try:
                native_splash.close()
            except Exception:
                pass
        if boot_backend:
            boot_backend()                        # 幂等，确保服务在跑
        print(f"请用浏览器打开: {url}")
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    finally:
        try:
            window.destroy()
        except Exception:
            pass


def _register_startup_workers():
    """启动后的后台工作：临时下载恢复（下载完成但滞留 TempCache 的补搬运）。"""
    try:
        import asyncio
        from core.services.download_manager import DownloadManager

        def _startup_recover():
            try:
                asyncio.run(DownloadManager().recover_temp_downloads())
            except Exception as e:
                print(f"启动恢复临时下载失败: {e}")

        threading.Thread(target=_startup_recover, daemon=True).start()
    except Exception as e:
        print(f"注册启动恢复失败: {e}")


def _preheat_backend():
    """后台预热：重模块已惰性化（启动提速），这里提前加载 + 预构造 crawler/client
    （含域名更新），用户点搜索/下载时零等待。失败静默。"""
    try:
        import jmcomic  # noqa: F401  预热重模块
        from core.services.jm_crawler import get_jm_crawler
        crawler = get_jm_crawler()
        if hasattr(crawler, "_get_client"):
            crawler._get_client()
    except Exception as e:
        print(f"启动预热失败: {e}")


def _preheat_backend():
    """后台预热：重模块已惰性化（启动提速），这里提前加载 + 预构造 crawler/client
    （含域名更新），用户点搜索/下载时零等待。失败静默。"""
    try:
        import jmcomic  # noqa: F401  预热重模块
        from core.services.jm_crawler import get_jm_crawler
        get_jm_crawler()._get_client()
    except Exception as e:
        print(f"启动预热失败: {e}")


def run_desktop():
    configure_utf8_output()
    configure_windows_app_id()
    base_dir = configure_runtime_env()

    port = get_or_create_port(base_dir)
    url = f"http://127.0.0.1:{port}"
    state = {"server": None}

    # 原生预启动窗：在 import webview 之前弹出，消除「点开到 splash 出现」的空白期
    splash = NativeSplash()

    # 窗口/任务栏图标跟随「设置→外观→应用图标」（轮询后端 app_icon，切图标后 ≤3s 生效）
    _icon_stop = threading.Event()
    try:
        from core.config import PROJECT_ROOT as _bundle_root
        threading.Thread(
            target=_window_icon_watcher,
            args=(None, str(_bundle_root), _icon_stop),
            daemon=True,
        ).start()
    except Exception:
        pass

    def _boot_backend():
        """（幂等）加载后端 + 起服务 + 预热；供 splash 切换与浏览器回退共用。"""
        if state.get("server") is not None:
            return True
        from core.app import app  # 重导入后移到此处：窗口先出现
        _register_startup_workers()
        server = FlaskServerThread(app, "127.0.0.1", port)
        state["server"] = server
        server.start()
        wait_for_server(url)
        threading.Thread(target=_preheat_backend, daemon=True).start()
        return True

    try:
        _open_window(url, base_dir, _boot_backend, splash)
    finally:
        try:
            _icon_stop.set()
        except Exception:
            pass
        srv = state.get("server")
        if srv is not None:
            try:
                srv.shutdown()
            except Exception:
                pass


def _enable_hidpi() -> None:
    """高分屏 / 高刷环境适配：per-monitor DPI v2。

    Android 那边要手动要最高刷新率（国产 ROM 会把第三方 App 锁 60Hz），
    Windows 的刷新率由 DWM 直出、无需干预；真正会让高分屏用户掉体验的是
    DPI 缩放未声明 —— 125%/150% 下窗口与 WebView2 会被位图拉伸，字发虚。
    这里在创建窗口前声明 per-monitor v2（shcore 不可用时退回系统级）。
    """
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

if __name__ == "__main__":
    _enable_hidpi()
    try:
        run_desktop()
    except Exception:
        tb = traceback.format_exc()
        try:
            _log_error("[致命错误] 启动失败:\n" + tb)
        except Exception:
            pass
        print(tb)
