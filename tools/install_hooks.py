# -*- coding: utf-8 -*-
"""安装 git pre-commit 隐私扫描钩子（幂等）。

    python tools/install_hooks.py

钩子文件位于 .git/hooks/pre-commit（不随仓库分发），本脚本克隆/换机后跑一次即可。
钩子内容是一小段 sh：优先用项目 .venv-build 的 python 调 tools/privacy_scan.py，
扫描暂存区，发现高危隐私内容则中止提交。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HOOK = r'''#!/bin/sh
# pre-commit：隐私误传拦截（由 tools/install_hooks.py 生成，勿手改）
PY=""
for cand in ".venv-build/Scripts/python.exe" "py" "python3" "python"; do
  if [ "$cand" = "py" ] || [ "$cand" = "python3" ] || [ "$cand" = "python" ]; then
    if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
  elif [ -x "$cand" ]; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then
  echo "[privacy-scan] 未找到 python，跳过检查（建议安装后重跑 tools/install_hooks.py）"
  exit 0
fi
"$PY" tools/privacy_scan.py || {
  echo "[privacy-scan] 提交已阻断。确认无误传后：真隐私移入 private/；个别误报在行尾加 privacy-scan: ignore。"
  exit 1
}
'''


def _git(*args):
    """优先 PATH 上的 git，找不到回退 GitHub Desktop 自带 git。"""
    cands = ["git",
             r"D:\code\app\GitHubDesktop\app-3.6.3\resources\app\git\cmd\git.exe"]
    base = r"D:\code\app\GitHubDesktop"
    if os.path.isdir(base):
        for d in sorted(os.listdir(base), reverse=True):
            g = os.path.join(base, d, "resources", "app", "git", "cmd", "git.exe")
            if os.path.isfile(g):
                cands.append(g)
    for g in cands:
        try:
            r = subprocess.run([g, *args], cwd=ROOT, capture_output=True, text=True)
            if r.returncode == 0 or r.stdout:
                return r
        except OSError:
            continue
    return None


def find_git_dir():
    r = _git("rev-parse", "--git-dir")
    gd = r.stdout.strip() if r else ""
    return os.path.join(ROOT, gd) if gd else None


def main():
    gd = find_git_dir()
    if not gd or not os.path.isdir(gd):
        print("未找到 .git 目录，请在仓库根目录执行")
        sys.exit(1)
    hooks = os.path.join(gd, "hooks")
    os.makedirs(hooks, exist_ok=True)
    p = os.path.join(hooks, "pre-commit")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(HOOK)
    print("已安装 pre-commit 隐私扫描钩子:", p)


if __name__ == "__main__":
    main()
