# -*- coding: utf-8 -*-
"""提交前隐私扫描器（开源防泄露）。

用法：
    python tools/privacy_scan.py                 # 扫 git 暂存区（pre-commit 默认）
    python tools/privacy_scan.py --head          # 扫整个仓库已跟踪文件
    python tools/privacy_scan.py --files a b c   # 扫指定文件
    python tools/privacy_scan.py --zip 包.zip     # 扫待发布便携包（禁带文件 + 文本内容）
    加 --strict：提示级（本机路径等）也按高危处理

退出码：发现高危内容 = 1，否则 0。
行尾写 `privacy-scan: ignore` 可豁免该行（仅限无法改写的第三方文本）。
"""
import argparse
import os
import re
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── 高危模式（命中即阻断提交）─────────────────────────────────
# 邮箱白名单后缀：开源许可证/公共规范里合理出现的域
EMAIL_WHITELIST = re.compile(
    r"(example\.(com|org)|noreply|w3\.org|w3c|microsoft|python\.org|github|sentry|"
    r"mozilla|webkit|chromium|adobe|apache\.org|opensource\.org|gnu\.org|kernel\.org|"
    r"freedesktop|sqlite\.org|curl\.se|haxx\.se|mit-license|creativecommons)", re.I)
# 许可证/第三方文件里的联系方式不算用户隐私
LICENSE_PATH = re.compile(r"(LICENSE|COPYING|COPYRIGHT|NOTICE|THIRD_PARTY|dist-info[/\\])", re.I)
# 密钥赋值右侧"像真密钥"：引号内 6+ 位；排除占位符/模板/环境变量引用
SECRET_ASSIGN = re.compile(
    r"(?i)(api[_-]?key|secret|token|passwd|password|pwd|access[_-]?key|app[_-]?secret)"
    r"\s*[:=]\s*['\"]([^'\"\n]{6,})['\"]")
SECRET_VALUE_OK = re.compile(
    r"^(xxx+|your[_-]|changeme|example|<|\$\(|%|\*+|n/a|none|null|true|false|\d+$)", re.I)
HEX_WITH_CTX = re.compile(
    r"(?i)(token|secret|api[_-]?key|password)\b.{0,40}?\b[0-9a-f]{32,64}\b")
PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
IDCARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
CRED_URL = re.compile(r"https?://[^/\s:@]{2,}:[^/@\s]{2,}@")
# 公网 IP 只在服务器/代理/URL 上下文里报，裸程序集版本号不拦截  # privacy-scan: ignore
IP_CTX = re.compile(
    r"(https?://|host|server|proxy|addr|url|endpoint|sync_server|服务器|代理)", re.I)
PUBLIC_IP = re.compile(
    r"(?<![\d.])(?!(?:127\.|10\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.|0\.0\.0\.0))"
    r"(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")

# ── 提示级（只报告，默认不阻断）──────────────────────────────
HINT_ADMIN = re.compile(r"C:\\Users\\(Administrator|.+?)\\", re.I)

BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".zip", ".exe", ".dll",
              ".pyd", ".so", ".dylib", ".pdf", ".db", ".woff", ".woff2", ".ttf",
              ".webp", ".jar", ".bin", ".pyc"}
FORBIDDEN_NAMES = re.compile(
    r"(^|[/\\])(comics\.db|jm_option\.yml|\.app_url|download_log\.jsonl|\.env(\.\w+)?|"
    r".*\.key|.*\.p12|.*\.pfx|id_rsa(\.pub)?|credentials?|"
    r".*(private|privkey|secret).*\.pem)$", re.I)

high_hits = []
hint_hits = []


def scan_text(path, text):
    for i, line in enumerate(text.splitlines(), 1):
        if "privacy-scan: ignore" in line:
            continue
        def add(kind, frag):
            high_hits.append((path, i, kind, frag.strip()[:90]))
        def hint(kind, frag):
            hint_hits.append((path, i, kind, frag.strip()[:90]))
        if PHONE.search(line):
            add("手机号", PHONE.search(line).group(0))
        if IDCARD.search(line):
            add("身份证号", IDCARD.search(line).group(0))
        if PRIVATE_KEY.search(line):
            add("私钥", PRIVATE_KEY.search(line).group(0))
        if CRED_URL.search(line):
            add("带账号密码的URL", CRED_URL.search(line).group(0)[:60])
        m = SECRET_ASSIGN.search(line)
        if m and not SECRET_VALUE_OK.match(m.group(2).strip()):
            add("疑似硬编码密钥", m.group(0)[:70])
        m2 = HEX_WITH_CTX.search(line)
        if m2 and not LICENSE_PATH.search(path):
            add("长hex密钥(带密钥上下文)", m2.group(0)[:70])
        for em in re.finditer(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", line):
            if not EMAIL_WHITELIST.search(em.group(0)) and not LICENSE_PATH.search(path):
                add("私人邮箱", em.group(0))
        if IP_CTX.search(line):
            for ip in PUBLIC_IP.finditer(line):
                add("公网IP(服务器上下文)", ip.group(0))
        if HINT_ADMIN.search(line):
            hint("Windows 本机用户路径", HINT_ADMIN.search(line).group(0))


def scan_file(path, text=None, forbid_name=True):
    if forbid_name and FORBIDDEN_NAMES.search(path.replace("\\", "/")):
        high_hits.append((path, 0, "禁带文件名", path))
        return
    if os.path.splitext(path)[1].lower() in BINARY_EXT:
        return
    if text is None:
        try:
            text = open(path, encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            return
    scan_text(path, text)


def _find_git():
    """git 不在 PATH 时回退到 GitHub Desktop 自带 git。"""
    for cand in ("git",
                 r"D:\code\app\GitHubDesktop\app-3.6.3\resources\app\git\cmd\git.exe"):
        try:
            r = subprocess.run([cand, "--version"], capture_output=True, timeout=5)
            if r.returncode == 0:
                return cand
        except OSError:
            continue
    # 通配：GitHubDesktop 多版本并存时取最新
    base = r"D:\code\app\GitHubDesktop"
    if os.path.isdir(base):
        for d in sorted(os.listdir(base), reverse=True):
            g = os.path.join(base, d, "resources", "app", "git", "cmd", "git.exe")
            if os.path.isfile(g):
                return g
    return "git"


GIT_BIN = _find_git()


def git(*args):
    return subprocess.run([GIT_BIN, *args], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def scan_staged():
    r = git("diff", "--cached", "--name-only", "--diff-filter=ACM")
    files = [f for f in r.stdout.splitlines() if f.strip()]
    for f in files:
        if FORBIDDEN_NAMES.search(f.replace("\\", "/")):
            high_hits.append((f, 0, "禁带文件名", f))
        show = git("show", ":" + f)
        if show.returncode == 0 and os.path.splitext(f)[1].lower() not in BINARY_EXT:
            scan_text(f, show.stdout)


def scan_head():
    r = git("ls-files")
    for f in r.stdout.splitlines():
        scan_file(os.path.join(ROOT, f))


def scan_zip(zp):
    with zipfile.ZipFile(zp) as z:
        for n in z.namelist():
            if FORBIDDEN_NAMES.search("/" + n.replace("\\", "/")) or \
                    re.search(r"(/UserData/|webview_data|TempCache|DownloadedComics)", n, re.I):
                high_hits.append((n, 0, "包内禁带路径/文件", n))
                continue
            if os.path.splitext(n)[1].lower() in BINARY_EXT or "dist-info" in n:
                continue
            if n.lower().endswith((".py", ".json", ".yml", ".yaml", ".md", ".txt",
                                   ".html", ".js", ".css", ".toml", ".svg")):
                try:
                    scan_text(n, z.read(n).decode("utf-8", "ignore"))
                except Exception:
                    pass


def report(strict=False):
    for p, ln, kind, frag in high_hits:
        print(f"[阻断] {p}:{ln}  <{kind}>  {frag}")
    for p, ln, kind, frag in hint_hits:
        print(f"[提示] {p}:{ln}  <{kind}>  {frag}")
    blocked = bool(high_hits) or (strict and bool(hint_hits))
    total = len(high_hits) + len(hint_hits)
    if total == 0:
        print("[privacy-scan] 未发现隐私内容")
    else:
        print(f"[privacy-scan] 高危 {len(high_hits)} 条，提示 {len(hint_hits)} 条。"
              f"误报可在行尾加 `privacy-scan: ignore`；真隐私请移入 private/（已被 gitignore）。")
    return 1 if blocked else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--head", action="store_true")
    ap.add_argument("--files", nargs="*")
    ap.add_argument("--zip")
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()
    if a.zip:
        scan_zip(a.zip)
    elif a.files:
        for f in a.files:
            scan_file(f)
    elif a.head:
        scan_head()
    else:
        scan_staged()
    sys.exit(report(a.strict))


if __name__ == "__main__":
    main()
