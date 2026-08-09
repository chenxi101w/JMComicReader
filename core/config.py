# -*- coding: utf-8 -*-
"""
统一配置模块 —— 所有路径和全局设置从这里取，不再各模块散落 os.environ.get。
"""

import os
import sys

# ─── 路径解析 ───────────────────────────────────────────────
IS_FROZEN: bool = getattr(sys, "frozen", False)

if IS_FROZEN:
    EXEC_DIR = os.path.dirname(sys.executable)
    # one-file 模式用 _MEIPASS；onedir 模式（本项目的打包方式）
    # 不同 PyInstaller 版本/ spec 把 datas+binaries 落到的子目录名不同
    # （如 _internal 或 spec 指定的 appdata）。直接定位包含 web/ 的目录，
    # 避免硬编码目录名导致运行时找不到模板/静态资源。
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass and os.path.isdir(meipass):
        BUNDLE_DIR = meipass
    else:
        candidates = [
            os.path.join(EXEC_DIR, "appdata"),
            os.path.join(EXEC_DIR, "_internal"),
            EXEC_DIR,
        ]
        BUNDLE_DIR = next(
            (c for c in candidates if os.path.isdir(os.path.join(c, "web"))),
            EXEC_DIR,
        )
else:
    EXEC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    BUNDLE_DIR = EXEC_DIR

PROJECT_ROOT = BUNDLE_DIR

# BASE_DIR 指向用户数据目录（运行时配置 + 下载 + 缓存）
BASE_DIR = os.environ.get("BASE_DIR", EXEC_DIR)
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", os.path.join(BASE_DIR, "DownloadedComics"))
TEMP_CACHE_DIR = os.environ.get("TEMP_CACHE_DIR", os.path.join(BASE_DIR, "TempCache"))

# 数据库与配置
BACKEND_DIR = os.path.join(BASE_DIR, "core")
DB_FILE = os.path.join(BACKEND_DIR, "comics.db")
OPTION_FILE = os.path.join(BACKEND_DIR, "jm_option.yml")
FRONTEND_DIR = os.path.join(PROJECT_ROOT, "web")
TEMPLATE_DIR = os.path.join(FRONTEND_DIR, "templates")
STATIC_DIR = os.path.join(FRONTEND_DIR, "static")

APP_VERSION: str = "0.0.0"


def load_app_version() -> str:
    global APP_VERSION
    version_file = os.path.join(PROJECT_ROOT, "VERSION")
    fallback = os.environ.get("APP_VERSION", "0.0.0")
    try:
        with open(version_file, "r", encoding="utf-8") as f:
            APP_VERSION = f.read().strip() or fallback
    except OSError:
        APP_VERSION = fallback
    return APP_VERSION


def ensure_dirs() -> None:
    """确保所有运行时目录存在。"""
    for d in (DOWNLOAD_DIR, TEMP_CACHE_DIR, BACKEND_DIR):
        os.makedirs(d, exist_ok=True)


# 启动时自动加载版本并创建目录
load_app_version()
ensure_dirs()
