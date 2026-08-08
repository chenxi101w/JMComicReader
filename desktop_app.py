#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
桌面版启动入口。

启动本地 Flask 服务并用原生桌面窗口承载页面，而不是交给外部浏览器。
所有用户数据（下载漫画、数据库、缓存）统一放到 exe 所在项目的根目录。
"""

import os
import shutil
import socket
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
import io
from pathlib import Path

from werkzeug.serving import make_server


APP_NAME = "JMComicReader"
WINDOWS_APP_ID = "JMComicReader.Desktop"
WINDOW_TITLE = "JMComicReader"
WINDOW_WIDTH = 1360
WINDOW_HEIGHT = 900
WINDOW_MIN_SIZE = (980, 680)


def is_frozen():
    return getattr(sys, "frozen", False)


def get_exec_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def get_default_user_base_dir() -> Path:
    env_base_dir = os.environ.get("BASE_DIR")
    if env_base_dir:
        return Path(env_base_dir).expanduser().resolve()

    if not is_frozen():
        return get_exec_dir()

    if os.name == "nt":
        # 打包后：数据目录 = exe 所在目录同级的 UserData
        # （如 JMComicReader/JMComicReader.exe → JMComicReader/UserData），
        # 与程序运行时目录（appdata/）分离，便于单独备份、更新只覆盖 exe+appdata/ 不丢数据。
        return get_exec_dir() / "UserData"

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME

    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME


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
    """
    把旧版本散落各处的用户数据迁移到当前数据目录（exe 同目录）。
    只在打包环境中执行，且目标不存在时才迁移，避免覆盖新数据。
    """
    if not is_frozen() or os.environ.get("BASE_DIR"):
        return

    exec_dir = get_exec_dir()
    # 旧数据可能来源：① exe 同目录 ② 项目根目录（dist 上两级）③ %LOCALAPPDATA%/JMComicReader/
    legacy_roots = [exec_dir, exec_dir.parent.parent]
    try:
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        if local_appdata:
            legacy_roots.append(Path(local_appdata) / APP_NAME)
    except Exception:
        pass

    # 需要迁移的相对路径条目（含浏览器存储，避免再次丢失收藏标签/搜索历史）
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


def get_or_create_port(base_dir: Path) -> int:
    """读取上次使用的端口；首次运行或用已占则用默认端口，写入文件供下次复用。
    固定端口保证 localStorage/IndexedDB 等浏览器存储不因端口变化而丢失。"""
    default_port = 28888
    port_file = base_dir / ".app_url"
    # 尝试复用上次端口
    saved = None
    try:
        if port_file.exists():
            saved = int(port_file.read_text().strip())
    except Exception:
        pass
    for candidate in (saved, default_port):
        if candidate and candidate > 0:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(("127.0.0.1", candidate)) != 0:
                    port_file.write_text(str(candidate))
                    return candidate
    # 都不行，随机找一个并写入
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    port_file.write_text(str(port))
    return port


def configure_windows_app_id():
    if os.name != "nt":
        return

    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            WINDOWS_APP_ID
        )
    except Exception:
        pass


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
        self.host = host
        self.port = port
        self._server = make_server(host, port, app, threaded=True)

    def run(self):
        self._server.serve_forever()

    def shutdown(self):
        self._server.shutdown()


def _log_error(msg: str):
    """把启动错误写到用户目录的日志，便于排查（窗口打不开/静默崩溃时也能留痕）。"""
    try:
        base = get_default_user_base_dir()
        base.mkdir(parents=True, exist_ok=True)
        log_path = base / "startup_error.log"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def configure_utf8_output():
    """
    强制 stdout/stderr 使用 UTF-8 编码。

    Windows 中文系统默认 GBK，jmcomic 打印含特殊字符（如 ♡\\u2661）的
    漫画标题/进度信息时会抛 'gbk' codec can't encode character 错误，
    导致下载流程中断。此函数在所有 import 之前调用，确保全局生效。
    """
    if sys.platform != "win32":
        return

    # 环境变量让子进程也继承
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        # 跳过已被替换或无 buffer 的流
        if not hasattr(stream, "buffer") or getattr(stream, "_utf8_forced", False):
            continue
        try:
            new_stream = io.TextIOWrapper(
                stream.buffer,
                encoding="utf-8",
                errors="replace",  # 仍有极少数字符无法编码时用 � 替代而非崩溃
                newline=None,
                line_buffering=getattr(stream, "line_buffering", False),
            )
            new_stream._utf8_forced = True
            setattr(sys, stream_name, new_stream)
        except Exception:
            pass


def run_desktop():
    configure_utf8_output()
    configure_windows_app_id()
    base_dir = configure_runtime_env()

    from core.app import app

    # 启动后后台恢复临时下载（不阻塞主线程）：把下载完成但未最终搬运、页面滞留
    # TempCache 的漫画补完进 DownloadedComics，避免「下载完打不开」。
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

    port = get_or_create_port(base_dir)
    url = f"http://127.0.0.1:{port}"
    server = FlaskServerThread(app, "127.0.0.1", port)
    server.start()
    wait_for_server(url)

    try:
        import webview
    except ImportError:
        print("未安装 pywebview，回退到浏览器模式。")
        print(f"当前数据目录: {base_dir}")
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    else:
        window = webview.create_window(
            WINDOW_TITLE,
            url,
            width=WINDOW_WIDTH,
            height=WINDOW_HEIGHT,
            min_size=WINDOW_MIN_SIZE,
        )
        try:
            # private_mode=False 才能持久化 localStorage（收藏标签/搜索历史/常用标签）；
            # storage_path 指定 WebView2 用户数据目录，配合固定端口(.app_url)实现数据持久化。
            webview.start(
                debug=False,
                private_mode=False,
                storage_path=str(base_dir / "webview_data"),
            )
        except Exception as exc:
            # 桌面窗口创建/渲染失败（常见于 WebView2 运行时异常）时，
            # 回退到系统默认浏览器，保证 app 始终可用。
            tb = traceback.format_exc()
            _log_error(f"[回退浏览器] pywebview 启动失败: {exc}\n{tb}")
            print(f"pywebview 启动失败，已回退到浏览器模式: {exc}")
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
    finally:
        server.shutdown()


if __name__ == "__main__":
    try:
        run_desktop()
    except Exception:
        tb = traceback.format_exc()
        try:
            _log_error("[致命错误] 启动失败:\n" + tb)
        except Exception:
            pass
        print(tb)
