# -*- coding: utf-8 -*-
"""
过滤与别名服务 —— 拉黑过滤 + 标签/作者别名展开与归一化。

- postprocess_results(): 搜索结果后处理（剔除拉黑项 + 标签/作者归一化到 canonical）。
- expand_tags() / expand_author(): 把用户输入的标签/作者展开为其同义词组，供搜索时一并检索。
- suggest_aliases(): 自动建议同义词（内置种子 + 本地已下载库标签共现挖掘）。
"""

import re
from collections import Counter, defaultdict
from typing import Dict, List, Optional

from core.models.database import (
    get_blocked_sets,
    get_alias_map,
    normalize_author,
    _get_conn,
)

# 内置种子同义词（canonical 在前，其余为别名）。用户可在设置页一键采纳或自行增删。
SEED_SYNONYMS: Dict[str, List] = {
    "tag": [
        ("巨乳", ["爆乳", "巨乳", "大奶"]),
        ("JK", ["女子高生", "女学生", "JK"]),
        ("单篇", ["单话", "短篇", "单篇"]),
        ("中文", ["汉化", "中文翻译", "中文"]),
        ("无修正", ["无修", "无修正版", "无修正"]),
        ("性转", ["性轉", "性转"]),
        ("调教", ["调教", "凌辱", "调教系"]),
    ],
    "author": [],
}


def postprocess_results(results: Optional[List[dict]]) -> List[dict]:
    """过滤拉黑项 + 标签/作者归一化到 canonical。

    results: list[dict]，每个含 id / author / tags。原地修改并返回同一列表。
    """
    if not results:
        return results or []
    blocked = get_blocked_sets()
    tag_map = get_alias_map("tag")
    author_map = get_alias_map("author")
    blocked_tags = blocked.get("tags", set())
    out: List[dict] = []
    for c in results:
        if not isinstance(c, dict):
            continue
        aid = c.get("id")
        author = c.get("author") or ""
        # ── 拉黑过滤 ──
        if aid is not None and aid in blocked["works"]:
            continue
        if author and normalize_author(author) in blocked["authors"]:
            continue
        # 标签拉黑：命中任一被屏蔽标签即过滤
        tags = c.get("tags") or []
        if isinstance(tags, str):
            tags = [t for t in tags.split(",") if t.strip()]
        if blocked_tags and any((t or "").strip() in blocked_tags for t in tags):
            continue
        # ── 标签归一化 ──
        tags = c.get("tags") or []
        if isinstance(tags, str):
            tags = [t for t in tags.split(",") if t.strip()]
        new_tags: List[str] = []
        for t in tags:
            tt = (t or "").strip()
            if not tt:
                continue
            canon = tag_map.get(tt.lower(), tt)
            if canon not in new_tags:
                new_tags.append(canon)
        c["tags"] = new_tags
        # ── 作者归一化 ──
        if author and author_map.get(author.lower()):
            c["author"] = author_map[author.lower()]
        out.append(c)
    return out


def _reverse_alias_map(alias_map: Dict[str, str]) -> Dict[str, List[str]]:
    """canonical -> [aliases]。"""
    rev: Dict[str, List[str]] = {}
    for alias, canon in alias_map.items():
        rev.setdefault(canon, [])
        if alias != canon.lower():
            rev[canon].append(alias)
    return rev


def expand_tags(tags: List[str]) -> List[str]:
    """把每个标签展开为 canonical + 其全部别名。去重，canonical 在前。"""
    tag_map = get_alias_map("tag")
    rev = _reverse_alias_map(tag_map)
    out: List[str] = []
    seen = set()
    for t in tags:
        tt = (t or "").strip()
        if not tt:
            continue
        canon = tag_map.get(tt.lower(), tt)
        for variant in [canon] + rev.get(canon, []):
            if variant and variant not in seen:
                seen.add(variant)
                out.append(variant)
    return out


def expand_author(author: str) -> List[str]:
    """把作者展开为 canonical + 其全部别名。去重。"""
    if not author or not author.strip():
        return []
    author_map = get_alias_map("author")
    canon = author_map.get(author.strip().lower(), author.strip())
    aliases = [a for a, c in author_map.items() if c == canon]
    out: List[str] = []
    seen = set()
    for v in [canon] + aliases:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def suggest_aliases(limit: int = 12) -> List[Dict]:
    """返回同义词建议列表（内置种子 + 本地共现挖掘）。"""
    suggestions: List[Dict] = []

    # 1) 种子同义词
    for type_, seeds in SEED_SYNONYMS.items():
        for canon, aliases in seeds:
            distinct = [a for a in dict.fromkeys(aliases) if a and a != canon]
            if distinct:
                suggestions.append({
                    "type": type_, "canonical": canon, "aliases": distinct, "source": "seed",
                })

    # 2) 本地共现挖掘（仅 tag）
    conn = _get_conn()
    rows = conn.execute(
        "SELECT tags FROM downloaded_comics WHERE tags IS NOT NULL AND tags <> ''"
    ).fetchall()
    doc_count: Counter = Counter()
    co: Dict[frozenset, int] = defaultdict(int)
    for r in rows:
        tags = [t.strip() for t in (r["tags"] or "").split(",") if t.strip()]
        tags = list(dict.fromkeys(tags))
        for t in tags:
            doc_count[t] += 1
        for i in range(len(tags)):
            for j in range(i + 1, len(tags)):
                co[frozenset((tags[i], tags[j]))] += 1

    alias_map = get_alias_map("tag")
    known = set(alias_map.values()) | set(alias_map.keys())
    pairs: List[tuple] = []
    for pair, cnt in co.items():
        a, b = sorted(pair)
        if doc_count[a] < 3 or doc_count[b] < 3:
            continue
        union = doc_count[a] + doc_count[b] - cnt
        if union <= 0:
            continue
        ratio = cnt / union
        if ratio >= 0.6 and cnt >= 3 and a not in known and b not in known:
            pairs.append((ratio, cnt, a, b))
    pairs.sort(reverse=True)
    for ratio, cnt, a, b in pairs[:limit]:
        suggestions.append({
            "type": "tag", "canonical": a, "aliases": [b],
            "source": "cooccur", "score": round(ratio, 2),
        })

    # 去重
    seen = set()
    uniq: List[Dict] = []
    for s in suggestions:
        key = (s["type"], s["canonical"], tuple(s["aliases"]))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(s)
    return uniq
