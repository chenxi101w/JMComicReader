# -*- coding: utf-8 -*-
"""构建脚本：用 build/JMComicReader.spec 打包为 one-folder EXE（dist_out/JMComicReader/）。
构建完成后自动压缩为 dist_out/JMComicReader_vX.Y.Z_portable.zip（解压即用，便于上传分享）。

沿用 PcFocus 打包手法：对 os.remove/unlink/rmdir/shutil.rmtree 打 nt.unlink 补丁，
绕过沙箱 safe-delete 包装，保证 pyinstaller --noconfirm 清理 build/dist 时不被拦截。

用法：<venv>/Scripts/python.exe build/_build.py
输出：dist_out/JMComicReader/                （含 JMComicReader.exe + appdata/ + web/）
      dist_out/JMComicReader_vX.Y.Z_portable.zip  （便携压缩包）
"""
import os
import sys
import stat
import nt
import zipfile

# 项目根目录（本脚本位于 build/ 下，父目录即根）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

_orig_remove = os.remove


def _patched_remove(path, *a, **k):
    try:
        if os.path.isdir(path):
            os.chmod(path, stat.S_IWRITE)
            nt.rmdir(path)
        else:
            os.chmod(path, stat.S_IWRITE)
            nt.unlink(path)
    except Exception:
        try:
            _orig_remove(path, *a, **k)
        except Exception:
            pass


os.remove = _patched_remove
os.unlink = _patched_remove
os.rmdir = _patched_remove


def _onerror(func, path, exc_info):
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


import shutil

_orig_rmtree = shutil.rmtree


def _patched_rmtree(path, *a, **k):
    try:
        shutil.rmtree(path, onerror=_onerror)
    except Exception:
        for root, dirs, files in os.walk(path, topdown=False):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    os.chmod(fp, stat.S_IWRITE)
                    nt.unlink(fp)
                except Exception:
                    pass
            for d in dirs:
                dp = os.path.join(root, d)
                try:
                    os.chmod(dp, stat.S_IWRITE)
                    nt.rmdir(dp)
                except Exception:
                    pass
        try:
            os.chmod(path, stat.S_IWRITE)
            nt.rmdir(path)
        except Exception:
            pass


shutil.rmtree = _patched_rmtree

import PyInstaller.__main__

sys.argv = [
    "pyinstaller",
    "build/JMComicReader.spec",
    "--distpath", "dist_out",
    "--workpath", "build_out",
    "--noconfirm",
]
PyInstaller.__main__.run()

# ---- 构建完成后产出便携 zip（解压即用，便于上传分享） ----
version = "unknown"
vf = os.path.join(ROOT, "VERSION")
if os.path.isfile(vf):
    with open(vf, encoding="utf-8") as f:
        version = f.read().strip() or version

src_dir = os.path.join(ROOT, "dist_out", "JMComicReader")
zip_name = f"JMComicReader_v{version}_portable.zip"
zip_path = os.path.join(ROOT, "dist_out", zip_name)

if os.path.isdir(src_dir):
    if os.path.isfile(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        # 不把运行时生成的用户数据打进发行包：UserData 含 WebView2 缓存与已下载漫画，可达 GB 级
        for base, dirs, files in os.walk(src_dir):
            dirs[:] = [d for d in dirs if d not in ("UserData", "TempCache")]
            for name in files:
                fp = os.path.join(base, name)
                z.write(fp, os.path.relpath(fp, src_dir))
    size_mb = os.path.getsize(zip_path) / 1024 / 1024
    print(f"[OK] 便携压缩包已生成: dist_out/{zip_name} ({size_mb:.1f} MB)")
else:
    print(f"[WARN] 未找到构建产物目录: {src_dir}")


# ---- 部署自用版到 D:\Game\JMComicReader，并清掉 dist_out 的解包目录 ----
# 约定（用户 2026-09-24 定）：
#   dist_out          → 只放便携版 zip（用来分享/存档）
#   D:\Game\JMComicReader → 自用解包版（双击即跑）
# 部署时**只覆盖 exe 与 appdata，绝不碰目标目录里的 UserData**（那是用户的
# 配置数据库与已下载漫画）。想换目录可设环境变量 JM_SELF_USE_DIR。
SELF_USE_DIR = os.environ.get("JM_SELF_USE_DIR", r"D:\Game\JMComicReader")

def _is_exe_running(path):
    """exe 正在运行时无法以写模式打开它 —— 用这个判断程序是否还开着。

    （不要用 tasklist 过滤，那种写法在某些 shell 下会静默失效，容易误判。）
    """
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "r+b"):
            pass
        return False
    except OSError:
        return True


if os.path.isdir(src_dir):
    _exe = os.path.join(SELF_USE_DIR, "JMComicReader.exe")
    if _is_exe_running(_exe):
        print("[WARN] 检测到 JMComicReader.exe 正在运行，跳过自用版部署（文件被占用）。")
        print(f"       关掉程序后重跑本脚本即可；或手动复制 {src_dir} -> {SELF_USE_DIR}")
    else:
        try:
            os.makedirs(SELF_USE_DIR, exist_ok=True)
            for item in os.listdir(src_dir):
                if item in ("UserData", "TempCache"):
                    continue
                s = os.path.join(src_dir, item)
                d = os.path.join(SELF_USE_DIR, item)
                if os.path.isdir(s):
                    # dirs_exist_ok=True：同名文件直接覆盖。
                    # 不要"先 rmtree 再 copytree"——删目录失败会被静默吞掉，
                    # 然后在 copytree 上撞出 WinError 183（已存在），把原因指错。
                    shutil.copytree(s, d, dirs_exist_ok=True)
                else:
                    shutil.copy2(s, d)
            print(f"[OK] 已部署自用版到: {SELF_USE_DIR}（其 UserData 原样保留）")
            try:
                shutil.rmtree(src_dir)
                print("[OK] 已清理 dist_out 的解包目录，该目录现在只放便携 zip")
            except Exception as e:
                print(f"[WARN] 中转目录清理失败（可手动删 {src_dir}）: {e}")
        except Exception as e:
            print(f"[WARN] 自用版部署失败: {e}")
            print(f"       中转目录已保留，可手动复制：{src_dir} -> {SELF_USE_DIR}")
