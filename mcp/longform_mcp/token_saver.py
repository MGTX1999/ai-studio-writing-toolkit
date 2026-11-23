"""省 token 工具：抽取式章节摘要、摘要批量生成与项目 token 账。

设计边界：
- 摘要由**规则抽取**（位置 + 关键词 + 实体名 + 句式信号）产生，不调用模型。
- ``save=false`` 时只预览，不覆盖任何摘要；``save=true`` 且已有摘要时必须
  显式 ``overwrite=true``，防止自动摘要冲掉作者手写的摘要。
- 只写 ``summaries`` 表（复用 ``summary_set``），不改正文、不改版本。
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

import packet
import word_stats
from store import (
    MAX_CHAPTER_N,
    MAX_SUMMARY_LEN,
    MAX_TEXT_LEN,
    LongformError,
    Store,
    _require_int,
    _require_str,
)

_SENT_RE = re.compile(r"[^。！？!?…\n]+[。！？!?…]?")
_HOOK_WORDS = (
    "但是", "却", "忽然", "突然", "发现", "决定", "必须", "不能",
    "原来", "秘密", "离开", "回来", "答应", "拒绝", "死", "活",
    "信", "钥匙", "门", "电话", "照片", "名字",
)
_CLIP_MARK = "……"


def _paragraphs(text: str) -> List[str]:
    parts = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    if parts:
        return parts
    return [p.strip() for p in (text or "").splitlines() if p.strip()]


def _sentences(paragraph: str) -> List[str]:
    return [s.strip() for s in _SENT_RE.findall(paragraph or "") if s.strip()]


def _sentence_units(text: str) -> List[Tuple[int, int, str]]:
    """返回 (段号, 句号, 句子)；段号与句号都从 0 开始。"""
    units: List[Tuple[int, int, str]] = []
    for pi, para in enumerate(_paragraphs(text)):
        for si, sentence in enumerate(_sentences(para)):
            units.append((pi, si, sentence))
    return units


def _score_sentence(sentence: str, pi: int, si: int,
                    paragraph_count: int, title: str,
                    entity_names: List[str]) -> float:
    length = len(re.sub(r"\s+", "", sentence))
    score = 0.0
    if pi == 0:
        score += 3.0
    if pi == paragraph_count - 1:
        score += 3.0
    if si == 0:
        score += 1.0
    if 12 <= length <= 110:
        score += 2.0
    elif length < 8:
        score -= 3.0
    elif length > 140:
        score -= 1.5
    if title and title in sentence:
        score += 2.0
    if any(name and name in sentence for name in entity_names):
        score += 2.0
    if any(word in sentence for word in _HOOK_WORDS):
        score += 1.2
    if re.search(r"[？?！!…]", sentence):
        score += 0.8
    if re.search(r"[“「『\"]", sentence):
        score += 0.5
    return score


def digest_text(text: str, max_chars: int = 600, title: str = "",
                entity_names: Optional[List[str]] = None) -> str:
    """抽取式摘要：保留首尾句与高信号句，按原顺序拼接。"""
    if not isinstance(text, str) or not text.strip():
        raise LongformError("正文不能为空，无法生成摘要")
    if len(text) > MAX_TEXT_LEN:
        raise LongformError(f"正文超过 {MAX_TEXT_LEN} 字符上限")
    max_chars = _require_int(max_chars, "max_chars", minimum=100,
                             maximum=MAX_SUMMARY_LEN)
    max_chars = min(max_chars, MAX_SUMMARY_LEN)
    if len(re.sub(r"\s+", "", text)) <= max_chars:
        return text.strip()

    units = _sentence_units(text)
    if not units:
        return text.strip()[:max_chars]
    paragraphs = len(_paragraphs(text))
    scored = []
    for pi, si, sentence in units:
        scored.append((_score_sentence(sentence, pi, si, paragraphs,
                                       title, entity_names or []), pi, si, sentence))

    # 首句与末句必留，保证摘要不丢开场和收束；其余按分数贪心取。
    # 同一句原样重复时只保留一次，否则“摘要”几乎等于复制正文。
    mandatory = {0, len(units) - 1}
    chosen = set(mandatory)
    seen_norm = set()
    for idx in mandatory:
        seen_norm.add(re.sub(r"\s+", "", units[idx][2]))
    used = sum(len(re.sub(r"\s+", "", units[i][2])) for i in mandatory)
    for score, pi, si, sentence in sorted(scored, key=lambda x: -x[0]):
        idx = next(i for i, u in enumerate(units)
                   if u[0] == pi and u[1] == si)
        if idx in chosen:
            continue
        norm = re.sub(r"\s+", "", sentence)
        if norm in seen_norm:
            continue
        compact = len(norm)
        if used + compact > max_chars and len(chosen) >= 2:
            continue
        chosen.add(idx)
        seen_norm.add(norm)
        used += compact
        if used >= max_chars:
            break

    parts: List[str] = []
    last_index: Optional[int] = None
    seen_parts = set()
    for idx in sorted(chosen):
        norm = re.sub(r"\s+", "", units[idx][2])
        if norm in seen_parts:
            continue
        if last_index is not None and idx > last_index + 1:
            parts.append(_CLIP_MARK)
        parts.append(units[idx][2].strip())
        seen_parts.add(norm)
        last_index = idx
    summary = "".join(parts).strip()
    if len(re.sub(r"\s+", "", summary)) > max_chars:
        summary = summary[:max_chars].rstrip("，、；：,;:") + "…"
    return summary


def _entity_names(s: Store, pid: str) -> List[str]:
    names: List[str] = []
    for item in s.bible_list(pid)[:40]:
        name = (item.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def _digest_one(s: Store, pid: str, n: int, max_chars: int,
                save: bool, overwrite: bool) -> dict:
    chapter = s.chapter_get(pid, n)
    if not chapter:
        raise LongformError(f"第 {n} 章还没有任何版本")
    text = chapter.get("content") or ""
    existing = s.summary_get(pid, n)
    summary = digest_text(text, max_chars=max_chars,
                          title=chapter.get("title") or "",
                          entity_names=_entity_names(s, pid))
    before = word_stats.count_text(text)
    after = word_stats.count_text(summary)
    saved_chars = max(0, before["chars"] - after["chars"])
    result = {
        "ok": True,
        "n": n,
        "title": chapter.get("title") or f"第{n}章",
        "from_version": chapter.get("version"),
        "summary": summary,
        "chars_before": before["chars"],
        "chars_after": after["chars"],
        "saved_chars": saved_chars,
        "saved_tokens": saved_chars // 4,
        "compression_ratio": round(after["chars"] / before["chars"], 3)
        if before["chars"] else 0.0,
        "already_had_summary": bool(existing),
        "existing_summary": (existing or {}).get("summary"),
        "saved": False,
        "method": "extractive",
        "note": "规则抽取，不是语义重写；保存前请人工复核。",
    }
    if save:
        if existing and not overwrite:
            raise LongformError(
                f"第 {n} 章已有摘要；确认覆盖请传 overwrite=true，"
                "或先用 save=false 预览")
        s.summary_set(pid, n, summary)
        result["saved"] = True
    return result


def chapter_digest(s: Store, pid: str, n: Optional[int] = None,
                   all_missing: bool = False, max_chars: int = 600,
                   save: bool = False, overwrite: bool = False) -> dict:
    """生成一章摘要；``all_missing=true`` 时批量补齐所有缺摘要章节。"""
    s._require_project(pid)
    max_chars = _require_int(max_chars, "max_chars", minimum=100,
                             maximum=MAX_SUMMARY_LEN)
    if all_missing:
        rows = []
        for chapter in s.chapter_list(pid):
            n_value = int(chapter["n"])
            if s.summary_get(pid, n_value):
                continue
            rows.append(_digest_one(s, pid, n_value, max_chars,
                                    save=save, overwrite=False))
        total_saved_tokens = sum(row["saved_tokens"] for row in rows)
        return {
            "ok": True,
            "project": pid,
            "mode": "all_missing",
            "items": rows,
            "generated": len(rows),
            "saved": sum(1 for row in rows if row["saved"]),
            "saved_tokens": total_saved_tokens,
            "note": "只补齐原本没有摘要的章节；已有手写摘要不会被覆盖。",
        }

    n = _require_int(n, "n", minimum=0, maximum=MAX_CHAPTER_N)
    return _digest_one(s, pid, n, max_chars, save=bool(save),
                       overwrite=bool(overwrite))


def token_audit(s: Store, pid: str, chapter_n: Optional[int] = None,
                lookback: int = 10) -> dict:
    """项目级 token 账：摘要覆盖率、最近 N 章全量 vs 摘要的差额。"""
    s._require_project(pid)
    lookback = _require_int(lookback, "lookback", minimum=1, maximum=50)
    chapters = s.chapter_list(pid)
    all_counts = []
    for item in chapters:
        chapter = s.chapter_get(pid, int(item["n"]))
        if not chapter:
            continue
        stats = word_stats.count_text(chapter.get("content") or "")
        summary = s.summary_get(pid, int(item["n"]))
        summary_text = (summary or {}).get("summary") or ""
        projected_chars = len(summary_text)
        if not projected_chars:
            # 缺摘要时用同一套抽取式规则实算，而不是拿固定 600 字估：
            # 短章抽不出更短的摘要时会得到与正文相当的长度，避免虚报节省。
            projected_chars = len(digest_text(chapter.get("content") or "",
                                              max_chars=600,
                                              title=chapter.get("title") or ""))
        all_counts.append({
            "n": int(item["n"]),
            "title": chapter.get("title") or f"第{item['n']}章",
            "chars": stats["chars"],
            "raw_chars": stats["raw_chars"],
            "approx_tokens": stats["approx_tokens"],
            "summary": summary_text,
            "summary_chars": len(summary_text),
            "projected_chars": projected_chars,
            "projected_tokens": projected_chars // 4,
            "covered": bool(summary_text),
        })

    if chapter_n is None:
        selected = all_counts[-lookback:]
    else:
        chapter_n = _require_int(chapter_n, "chapter_n",
                                 minimum=0, maximum=MAX_CHAPTER_N)
        selected = [row for row in all_counts if row["n"] < chapter_n][-lookback:]
    covered = [row for row in selected if row["covered"]]
    missing = [row["n"] for row in selected if not row["covered"]]
    full_tokens = sum(row["approx_tokens"] for row in selected)
    # 缺摘要时优先用同一套抽取式规则实算长度；短章抽不出更短摘要，
    # 会如实显示“可省 0”，而不是先承诺一个假的节省。
    projected_summary_chars = sum(row["projected_chars"] for row in selected)
    summary_tokens = projected_summary_chars // 4
    savings_tokens = max(0, full_tokens - summary_tokens)

    suggestions: List[str] = []
    if missing and savings_tokens > 0:
        suggestions.append(
            f"最近 {len(selected)} 章里有 {len(missing)} 章缺摘要："
            f"chapter_digest(all_missing=true, save=true) 可批量补齐，"
            f"预计把全量约 {full_tokens} tokens 压到约 {summary_tokens} tokens，"
            f"省约 {savings_tokens} tokens")
    elif missing:
        suggestions.append(
            f"最近 {len(selected)} 章里有 {len(missing)} 章缺摘要；"
            f"但这些章正文合计只有约 {full_tokens} tokens，按现有规则抽摘要不会更短，"
            "补摘要也省不下 token——先把章节写长，再考虑摘要替代。")
    else:
        suggestions.append(
            f"最近 {len(selected)} 章摘要已全覆盖；全量约 {full_tokens} tokens，"
            f"摘要约 {summary_tokens} tokens，理论上可省 {savings_tokens} tokens")
    if not selected:
        suggestions.append("还没有章节正文；先写第一章，再来看 token 账")
    if covered and sum(row["summary_chars"] for row in covered) > 0:
        avg = sum(row["summary_chars"] for row in covered) // len(covered)
        suggestions.append(
            f"摘要平均 {avg} 字；若仍超预算，可在生成摘要时把 max_chars 调到 300–500")

    materials = []
    for item in s.material_list(pid, limit=100):
        chars = int(item.get("chars") or 0)
        if chars:
            materials.append({"id": item.get("id"), "title": item.get("title"),
                              "chars": chars, "approx_tokens": chars // 4})
    materials.sort(key=lambda x: -x["chars"])
    if materials[:1] and materials[0]["chars"] > 1200:
        suggestions.append(
            f"最大素材《{materials[0]['title']}》约 {materials[0]['chars']} 字"
            f"（{materials[0]['approx_tokens']} tokens）；"
            "本章不需要全文时可 material_update 精简，或把章节关联清空")

    packet_view = None
    if chapter_n is not None:
        packet_view = packet.context_estimate(s, pid, chapter_n)
        suggestions.append(
            f"第 {chapter_n} 章输入包预算 {packet_view['token_budget']} tokens，"
            f"估算 {packet_view['approx_tokens']} tokens；"
            + ("已超预算，按 context_estimate 的降级建议处理。"
               if not packet_view["fits"] else "当前在预算内。"))

    return {
        "ok": True,
        "project": pid,
        "chapter_n": chapter_n,
        "lookback": lookback,
        "totals": {
            "chapters": len(all_counts),
            "covered": sum(1 for row in all_counts if row["covered"]),
            "missing": sum(1 for row in all_counts if not row["covered"]),
            "chars": sum(row["chars"] for row in all_counts),
            "approx_tokens": sum(row["approx_tokens"] for row in all_counts),
        },
        "window": {
            "chapters": selected,
            "full_tokens": full_tokens,
            "summary_tokens": summary_tokens,
            "savings_tokens": savings_tokens,
            "coverage": round(len(covered) / len(selected), 3) if selected else 0.0,
            "missing": missing,
        },
        "largest_materials": materials[:5],
        "packet": packet_view,
        "suggestions": suggestions,
        "assumptions": ("summary_tokens 对缺摘要章节用同一套抽取式规则实算预览，"
                        "不按固定字数估算；实际以生成结果为准。"),
    }
