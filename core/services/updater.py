# -*- coding: utf-8 -*-
"""检查更新：只走 GitHub Releases（api.github.com），不依赖任何自建服务器。

设计要点
- 匿名 GET，不需要 token。GitHub 未认证限流 60 次/小时/IP，本地 10 分钟缓存足够。
- 版本比较用「数字分段元组」，2.15.0 > 2.9.9；字符串比较会翻车。
- 任何网络 / 解析异常都吞掉并返回 success=False —— 检查更新绝不能拖垮主流程。
- 不记录、不上报任何用户数据（无设备号、无路径、无使用记录），请求里只有 UA。
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.request

GITHUB_REPO = "chenxi101w/JMComicReader"
APP_NAME = "JMComicReader"
_API = "https://api.github.com/repos/{repo}/releases/latest"
_TTL = 600.0  # 秒；防止用户连点把 GitHub 限流额度吃光

_lock = threading.Lock()
_cache = {"ts": 0.0, "data": None}


def norm_version(v: str) -> tuple:
    """'v2.15.0' → (2, 15, 0, 0)；缺位补 0，保证元组等长可比。"""
    nums = re.findall(r"\d+", str(v or ""))
    out = tuple(int(n) for n in nums[:4])
    return out + (0,) * (4 - len(out))


def _get_json(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": f"{APP_NAME}-updater",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def fetch_latest(timeout: float = 8.0, force: bool = False) -> dict:
    """取最新 release 元信息（带缓存）。异常直接抛，由 check_update 兜底。"""
    with _lock:
        if not force and _cache["data"] and time.time() - _cache["ts"] < _TTL:
            return _cache["data"]
    raw = _get_json(_API.format(repo=GITHUB_REPO), timeout)
    data = {
        "repo": GITHUB_REPO,
        "latest": str(raw.get("tag_name") or "").lstrip("vV"),
        "url": raw.get("html_url") or "",
        "notes": (raw.get("body") or "").strip()[:2000],
        "published_at": raw.get("published_at") or "",
        "assets": [
            {
                "name": a.get("name") or "",
                "size": int(a.get("size") or 0),
                "url": a.get("browser_download_url") or "",
            }
            for a in (raw.get("assets") or [])
        ],
    }
    with _lock:
        _cache["ts"] = time.time()
        _cache["data"] = data
    return data


def check_update(current: str, timeout: float = 8.0, force: bool = False) -> dict:
    """对比本地版本与最新 release。**永不抛异常**。"""
    base = {"success": False, "current": current, "latest": "", "has_update": False,
            "url": "", "notes": "", "published_at": "", "repo": GITHUB_REPO}
    try:
        latest = fetch_latest(timeout=timeout, force=force)
    except Exception as e:  # 网络不通 / 404 / 限流
        base["error"] = f"取不到版本信息：{type(e).__name__}: {str(e)[:160]}"
        return base
    base.update({k: latest[k] for k in ("latest", "url", "notes", "published_at")})
    base["success"] = True
    if not latest["latest"]:
        base["error"] = "release 没有 tag_name"
        return base
    base["has_update"] = norm_version(latest["latest"]) > norm_version(current)
    return base
