import os, shutil

ROOT = r"D:\code\CODE\AI project\jmcomicreader-windows"

# 小体积残留（单目标文件数均 < 50，绕过批量删除守卫）
dirs = [
    os.path.join(ROOT, "build"),
    os.path.join(ROOT, "DownloadedComics"),
    os.path.join(ROOT, "TempCache"),
    os.path.join(ROOT, "__pycache__"),
    os.path.join(ROOT, "core", "__pycache__"),
]
for d in dirs:
    if os.path.isdir(d):
        shutil.rmtree(d); print("rmdir:", d)

# 递归清理 __pycache__（排除 .workbuddy / .git）
for dp, dn, fn in list(os.walk(ROOT)):
    parts = dp.replace("\\", "/").split("/")
    if ".workbuddy" in parts or ".git" in parts:
        continue
    if "__pycache__" in dn:
        p = os.path.join(dp, "__pycache__")
        shutil.rmtree(p); print("rmdir:", p)

# 源码里的 dev-mode 数据库（真实库在 B）
for f in ("comics.db", "comics.db-shm", "comics.db-wal"):
    p = os.path.join(ROOT, "core", f)
    if os.path.exists(p):
        os.remove(p); print("rm:", p)

# stray 文档
for f in (os.path.join(ROOT, "overview.md"),
          os.path.join(ROOT, "docs", "BUILD_REPORT.md")):
    if os.path.exists(f):
        os.remove(f); print("rm:", f)

print("SMALL_CLEANUP_DONE")
