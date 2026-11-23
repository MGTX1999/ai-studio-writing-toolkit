"""引证验真：把正文里的断言回溯到素材 / 章节 / 原始来源文本。

设计取舍：
1. 只用标准库 ``difflib`` 做模糊匹配，不引入向量模型；
2. 先规范化（去空白与中英文标点、转小写）再比较，避免"多一个逗号就判失败"；
3. 精确子串直接给满分；否则用最长公共子串长度 / 断言长度作为得分；
4. 验真只写 ``citations`` 表，绝不改动章节正文——验真是旁证，不是改写。
"""

from __future__ import annotations

import difflib
from typing import Any, Dict, List, Tuple

from store import LongformError

DEFAULT_THRESHOLD = 0.86
_EVIDENCE_RADIUS = 80
_FALLBACK_EVIDENCE_CHARS = 120

# 需要忽略的标点；空白另行用 isspace() 判定。
_PUNCT = "，。！？；：、“”‘’（）()《》〈〉…—-,.!?;:'\""


def _strip_char(ch: str) -> bool:
    return ch.isspace() or ch in _PUNCT


def _normalize_with_map(text: str) -> Tuple[str, List[int]]:
    """规范化并记录每个规范化字符对应的原文下标，供证据片段定位。"""
    chars: List[str] = []
    positions: List[int] = []
    for i, ch in enumerate(text):
        if _strip_char(ch):
            continue
        low = ch.lower()
        for piece in low:
            chars.append(piece)
            positions.append(i)
    return "".join(chars), positions


def normalize(text: str) -> str:
    text = "" if text is None else str(text)
    return _normalize_with_map(text)[0]


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise LongformError(
            f"参数 `{field}` 必须是字符串，实际收到 {type(value).__name__}：{value!r}")
    if not value.strip():
        raise LongformError(f"参数 `{field}` 不能为空")
    return value


def _require_threshold(value: Any) -> float:
    try:
        threshold = float(value)
    except (TypeError, ValueError):
        raise LongformError(
            f"参数 `threshold` 必须是数字，实际收到 {value!r}") from None
    if not 0.0 < threshold <= 1.0:
        raise LongformError(f"参数 `threshold` 必须在 (0, 1] 之间，实际 {threshold}")
    return threshold


def _excerpt(text: str, start: int, end: int) -> str:
    lo = max(0, start - _EVIDENCE_RADIUS)
    hi = min(len(text), end + _EVIDENCE_RADIUS)
    return text[lo:hi]


def verify_text(claim: str, source_text: str, *,
                threshold: float = DEFAULT_THRESHOLD) -> dict:
    """判断 ``claim`` 是否能在 ``source_text`` 中找到。

    返回 ``{verified, score, evidence, match}``；``match`` 为
    ``exact`` / ``fuzzy`` / ``none``。失败时 evidence 取来源前 120 字，
    便于调用方判断"是来源不对，还是断言被改写过头"。
    """
    claim = _require_text(claim, "claim")
    source_text = _require_text(source_text, "source")
    threshold = _require_threshold(threshold)

    norm_claim, _ = _normalize_with_map(claim)
    norm_source, src_map = _normalize_with_map(source_text)
    if not norm_claim:
        raise LongformError("参数 `claim` 去掉标点/空白后为空")
    if not norm_source:
        raise LongformError("参数 `source` 去掉标点/空白后为空")

    pos = norm_source.find(norm_claim)
    if pos >= 0:
        start = src_map[pos]
        end = src_map[pos + len(norm_claim) - 1] + 1
        return {"verified": True, "score": 1.0,
                "evidence": _excerpt(source_text, start, end),
                "match": "exact"}

    matcher = difflib.SequenceMatcher(None, norm_claim, norm_source)
    match = matcher.find_longest_match(0, len(norm_claim), 0, len(norm_source))
    score = (match.size / len(norm_claim)) if norm_claim else 0.0

    if match.size and score >= threshold:
        start = src_map[match.b]
        end = src_map[match.b + match.size - 1] + 1
        return {"verified": True, "score": round(score, 4),
                "evidence": _excerpt(source_text, start, end),
                "match": "fuzzy"}

    return {"verified": False, "score": round(score, 4),
            "evidence": source_text[:_FALLBACK_EVIDENCE_CHARS],
            "match": "none"}


def _resolve_source(s, pid: str, source: str) -> Tuple[str, str]:
    """把 source 解析成（正文, 种类）；种类用于对外说明来源类型。"""
    if source.startswith("mt_"):
        material = s.material_get(pid, source)
        if not material:
            raise LongformError(f"引证来源素材不存在：{source}")
        text = material.get("content") or ""
        if not text.strip():
            raise LongformError(f"引证来源素材没有正文：{source}")
        return text, "material"

    raw_n = source[3:].strip() if source.startswith("ch:") else (
        source if source.isdigit() else "")
    if raw_n:
        try:
            n = int(raw_n)
        except ValueError:
            raise LongformError(f"引证来源章节号非法：{source}") from None
        chapter = s.chapter_get(pid, n)
        if not chapter:
            raise LongformError(f"引证来源章节不存在：第 {n} 章")
        text = chapter.get("content") or ""
        if not text.strip():
            raise LongformError(f"引证来源章节没有正文：第 {n} 章")
        return text, "chapter"

    return source, "text"


def verify(s, pid: str, citation_id: str, *,
           threshold: float = DEFAULT_THRESHOLD) -> dict:
    """验真一条已登记的引证，并把 verified/evidence 写回 citations。"""
    citation = s.citation_get(pid, citation_id)
    if not citation:
        raise LongformError(f"引证不存在：{citation_id}（项目 {pid}）")
    source = (citation.get("source") or "").strip()
    if not source:
        raise LongformError("引证缺少来源：请先补 source 再验真")

    text, kind = _resolve_source(s, pid, source)
    result = verify_text(citation["claim"], text, threshold=threshold)
    s.citation_update(pid, citation_id, verified=result["verified"],
                      evidence=result["evidence"])
    return {"ok": True, "id": citation_id, "verified": result["verified"],
            "score": result["score"], "evidence": result["evidence"],
            "source_kind": kind}


def add_and_verify(s, pid: str, chapter_n, claim: str, source: str = "",
                   locator: str = "", *,
                   threshold: float = DEFAULT_THRESHOLD) -> dict:
    """登记后立即验真；工具层仍以 citation_add + citation_verify 两步为主。"""
    added = s.citation_add(pid, chapter_n, claim, source=source,
                           locator=locator)
    return {"ok": True, "citation": added,
            "verification": verify(s, pid, added["id"], threshold=threshold)}


def format_citation(row: Dict[str, Any]) -> str:
    """把一条引证渲染成单行文本（CLI / 报告共用）。"""
    status = "已验真" if row.get("verified") else "未验真"
    bits = [status, str(row.get("claim") or "")]
    if row.get("source"):
        bits.append(f"来源：{row['source']}")
    if row.get("locator"):
        bits.append(f"定位：{row['locator']}")
    return "｜".join(bits)
