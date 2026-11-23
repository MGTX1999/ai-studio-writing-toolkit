"""字数统计：作者看稿、模型估 token 共用同一套口径。

口径固定为：
- ``chars``：去掉所有空白后的字符数，含标点（与编辑器里“多少字”一致）。
- ``raw_chars``：原始字符串长度，含空白与换行，用于估算上下文体积。
- ``cjk_chars``：CJK 统一表意文字个数，不含标点与拉丁字母。
- ``approx_tokens``：按 4 字符 ≈ 1 token 估算，与 ``packet`` 的口径一致。

这里只做只读统计，不写数据库；章节版本由 ``Store`` 负责。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Optional

from store import MAX_CHAPTER_N, MAX_TEXT_LEN, LongformError, Store, _require_int

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_LATIN_RE = re.compile(r"[A-Za-z]+")
_SENT_RE = re.compile(r"[^。！？!?…\n]+[。！？!?…]?")


def count_text(text: str) -> Dict[str, int]:
    """统计一段文本，返回稳定字段；调用方不需要自己写正则。"""
    if not isinstance(text, str):
        raise LongformError("text 必须是字符串")
    if len(text) > MAX_TEXT_LEN:
        raise LongformError(f"text 超过 {MAX_TEXT_LEN} 字符上限")

    raw_chars = len(text)
    chars = len(re.sub(r"\s+", "", text))
    cjk_chars = len(_CJK_RE.findall(text))
    latin_words = len(_LATIN_RE.findall(text))
    punctuation = sum(1 for ch in text
                      if not ch.isspace()
                      and unicodedata.category(ch).startswith("P"))
    digits = sum(1 for ch in text if ch.isdigit())
    paragraphs = len([p for p in re.split(r"\n\s*\n", text) if p.strip()])
    if not paragraphs:
        paragraphs = len([line for line in text.splitlines() if line.strip()])
    sentences = len([s for s in _SENT_RE.findall(text) if s.strip()])
    lines = len([line for line in text.splitlines() if line.strip()])

    return {
        "chars": chars,
        "raw_chars": raw_chars,
        "cjk_chars": cjk_chars,
        "latin_words": latin_words,
        "punctuation": punctuation,
        "digits": digits,
        "paragraphs": paragraphs,
        "sentences": sentences,
        "lines": lines,
        "approx_tokens": raw_chars // 4,
        "reading_minutes": round(chars / 400, 2) if chars else 0.0,
    }


def _summary_chars(s: Store, pid: str, n: int) -> int:
    row = s.summary_get(pid, n)
    return len((row or {}).get("summary") or "")


def chapter_word_stats(s: Store, pid: str, n: int,
                       version: Optional[int] = None) -> dict:
    s._require_project(pid)
    n = _require_int(n, "n", minimum=0, maximum=MAX_CHAPTER_N)
    chapter = s.chapter_get(pid, n, version)
    if not chapter:
        raise LongformError(f"第 {n} 章还没有任何版本")
    stats = count_text(chapter.get("content") or "")
    stats.update({
        "ok": True,
        "project": pid,
        "n": n,
        "title": chapter.get("title") or f"第{n}章",
        "status": chapter.get("status") or "draft",
        "version": chapter.get("version"),
        "summary_chars": _summary_chars(s, pid, n),
    })
    return stats


def project_word_stats(s: Store, pid: str) -> dict:
    """统计项目全部章节最新版；旧版本不计入总字数，避免版本叠加虚高。"""
    s._require_project(pid)
    chapters = s.chapter_list(pid)
    rows: List[dict] = []
    total = {
        "chars": 0,
        "raw_chars": 0,
        "cjk_chars": 0,
        "latin_words": 0,
        "punctuation": 0,
        "digits": 0,
        "paragraphs": 0,
        "sentences": 0,
        "lines": 0,
        "approx_tokens": 0,
    }
    summaries = 0
    for item in chapters:
        chapter = s.chapter_get(pid, int(item["n"]))
        if not chapter:
            continue
        stats = count_text(chapter.get("content") or "")
        summary_chars = _summary_chars(s, pid, int(item["n"]))
        if summary_chars:
            summaries += 1
        for key in total:
            total[key] += stats[key]
        rows.append({
            "n": int(item["n"]),
            "title": chapter.get("title") or f"第{item['n']}章",
            "status": chapter.get("status") or "draft",
            "version": chapter.get("version"),
            "chars": stats["chars"],
            "raw_chars": stats["raw_chars"],
            "cjk_chars": stats["cjk_chars"],
            "paragraphs": stats["paragraphs"],
            "sentences": stats["sentences"],
            "approx_tokens": stats["approx_tokens"],
            "summary_chars": summary_chars,
        })

    count = len(rows)
    total["chapters"] = count
    total["approx_tokens"] = total["raw_chars"] // 4
    total["reading_minutes"] = round(total["chars"] / 400, 2) if total["chars"] else 0.0
    total["avg_chars"] = round(total["chars"] / count, 1) if count else 0.0
    total["summary_coverage"] = round(summaries / count, 3) if count else 0.0
    longest = max(rows, key=lambda x: x["chars"]) if rows else None
    shortest = min(rows, key=lambda x: x["chars"]) if rows else None

    return {
        "ok": True,
        "project": pid,
        "totals": total,
        "longest": longest,
        "shortest": shortest,
        "chapters": rows,
        "note": "字数口径=去除空白后的字符数（含标点）；旧版本不重复计入。",
    }
