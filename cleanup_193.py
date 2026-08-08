import os, shutil

ROOT = r"D:\code\CODE\AI project\jmcomicreader-windows"
B_DB = r"D:\Game\JMComicReader\dist\JMComicReader\core\comics.db"

# 安全前置检查：确认真实数据库在 B 运行目录里，再去删源码里的 dev 残留 db
real_db_ok = os.path.exists(B_DB)
print("REAL_DB_IN_B_EXISTS:", real_db_ok)
if not real_db_ok:
    print("ABORT: 真实数据库不在 B 目录，跳过删除源码 db")
else:
    # 1) 构建产物 / 缓存 / 空残留目录
    dirs = [
        os.path.join(ROOT, "dist"),
        os.path.join(ROOT, "build"),
        os.path.join(ROOT, "__pycache__"),
        os.path.join(ROOT, "DownloadedComics"),
        os.path.join(ROOT, "TempCache"),
        os.path.join(ROOT, "core", "__pycache__"),
    ]
    for d in dirs:
        if os.path.isdir(d):
            shutil.rmtree(d); print("rmdir:", d)

    # 2) 任意 __pycache__（递归，排除 .workbuddy / .git）
    for dp, dn, fn in list(os.walk(ROOT)):
        if ".workbuddy" in dp.replace("\\", "/").split("/") or dp.endswith(".git"):
            continue
        if "__pycache__" in dn:
            p = os.path.join(dp, "__pycache__")
            shutil.rmtree(p); print("rmdir:", p)

    # 3) 源码里的 dev-mode 数据库（真实库在 B）
    for f in ("comics.db", "comics.db-shm", "comics.db-wal"):
        p = os.path.join(ROOT, "core", f)
        if os.path.exists(p):
            os.remove(p); print("rm:", p)

    # 4)  stray 文档
    for f in (os.path.join(ROOT, "overview.md"),
              os.path.join(ROOT, "docs", "BUILD_REPORT.md")):
        if os.path.exists(f):
            os.remove(f); print("rm:", f)

    print("CLEANUP_DONE")
