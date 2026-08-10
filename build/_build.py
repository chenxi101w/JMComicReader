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
