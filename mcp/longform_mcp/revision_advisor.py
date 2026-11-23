"""根据已有章节给出可执行的修订建议。

输出分两类：
- **建议**：证据 + 原因 + 改法，供作者判断；
- **补丁**：只在能用原文精确匹配、且替换结果确定时给出（目前是超长段落
  按句拆段），可直接交给 ``chapter_patch`` 预览/应用。

这是规则体检，不是文学评分；不调用模型，也不自动改正文。
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

import insights
from store import MAX_CHAPTER_N, LongformError, Store, _require_int

_SEVERITY = {"red": "high", "yellow": "medium", "gray": "low"}
_SENT_RE = re.compile(r"[^。！？!?…]+[。！？!?…]?")
_OPENING_WORDS = ("他", "她", "沈", "我", "你", "这", "那")


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _clip(text: str, limit: int = 180) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def _paragraphs(text: str) -> List[str]:
    parts = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    if parts:
        return parts
    return [p.strip() for p in (text or "").splitlines() if p.strip()]


def _sentences(text: str) -> List[str]:
    return [s.strip() for s in _SENT_RE.findall(text or "") if s.strip()]


def _split_paragraph(paragraph: str, target_chars: int = 200) -> str:
    """按句末标点把超长段落切成 2–4 段；不改变句子内部文字。"""
    sentences = _sentences(paragraph)
    if len(sentences) <= 1:
        chars = _norm(paragraph)
        if len(chars) <= target_chars:
            return paragraph
        chunks = [chars[i:i + target_chars] for i in range(0, len(chars), target_chars)]
        return "\n\n".join(chunks)
    chunks: List[str] = []
    current = ""
    for sentence in sentences:
        if current and len(_norm(current + sentence)) > target_chars:
            chunks.append(current.strip())
            current = sentence
        else:
            current += sentence
    if current.strip():
        chunks.append(current.strip())
    return "\n\n".join(chunks)


def _issue(issue_id: str, severity: str, category: str, title: str,
           evidence: str, why: str, suggestion: str,
           patch: Optional[dict] = None) -> dict:
    return {
        "id": issue_id,
        "severity": severity,
        "category": category,
        "title": title,
        "evidence": evidence,
        "why": why,
        "suggestion": suggestion,
        "patch": patch,
        "patchable": bool(patch),
    }


def revision_plan(s: Store, pid: str, n: int, max_items: int = 8) -> dict:
    s._require_project(pid)
    n = _require_int(n, "n", minimum=0, maximum=MAX_CHAPTER_N)
    max_items = _require_int(max_items, "max_items", minimum=3, maximum=30)
    chapter = s.chapter_get(pid, n)
    if not chapter:
        raise LongformError(f"第 {n} 章还没有任何版本")

    text = chapter.get("content") or ""
    report = insights.structure_check(text)
    metrics = report.get("metrics") or {}
    issues: List[dict] = []
    seen = set()

    for idx, finding in enumerate(report.get("findings") or []):
        quote = finding.get("quote") or ""
        why = finding.get("why") or ""
        alternatives = finding.get("alternatives") or []
        key = (finding.get("layer"), quote, why)
        if key in seen:
            continue
        seen.add(key)
        issues.append(_issue(
            "structure-" + str(idx + 1),
            _SEVERITY.get(finding.get("level"), "low"),
            finding.get("category") or finding.get("layer") or "structure",
            finding.get("layer", "结构") + " · " + _clip(quote, 50),
            quote,
            why,
            "；".join(alternatives) if alternatives else "按证据回读原段，做最小改动",
        ))

    # 超长段落：能唯一定位时给出精确补丁，拆段后其余文字逐字不变。
    for idx, paragraph in enumerate(_paragraphs(text)):
        chars = len(_norm(paragraph))
        if chars <= 280:
            continue
        replacement = _split_paragraph(paragraph)
        if replacement == paragraph:
            continue
        if text.count(paragraph) != 1:
            patch = None
            why = "这一段在正文中出现多次，自动定位不唯一，建议手动选中后拆段"
        else:
            patch = {"find": paragraph, "replace": replacement, "occurrence": 1}
            why = "单段过长会压掉阅读节奏；按句末标点拆成 2–4 段，句子本身不动"
        issues.append(_issue(
            "paragraph-" + str(idx + 1),
            "medium",
            "paragraph",
            f"第 {idx + 1} 段过长（{chars} 字）",
            _clip(paragraph),
            why,
            "点「填入局部改稿」预览拆段结果；确认后再应用生成新版本。",
            patch,
        ))

    # 长句与重复句：只给建议，不硬造替换文本。
    sentences = _sentences(text)
    long_sentences = [s for s in sentences if len(_norm(s)) > 80]
    if long_sentences:
        issues.append(_issue(
            "long-sentence",
            "low",
            "sentence",
            f"有 {len(long_sentences)} 句超过 80 字",
            _clip(long_sentences[0], 100),
            "长句连续会失去换气点，读者容易在句中迷路",
            "挑最长的一到两句，在转折或动作处断句；不要为了短而机械切碎",
        ))

    repeated = []
    counts: Dict[str, int] = {}
    for sentence in sentences:
        norm = _norm(sentence)
        if len(norm) < 12:
            continue
        counts[norm] = counts.get(norm, 0) + 1
    for norm, count in counts.items():
        if count >= 2:
            repeated.append((norm, count))
    if repeated:
        repeated.sort(key=lambda x: (-x[1], -len(x[0])))
        issues.append(_issue(
            "repeated-sentence",
            "medium",
            "repetition",
            f"有 {len(repeated)} 句原样重复",
            _clip(repeated[0][0], 100),
            "原样重复会让读者觉得是复制，而不是强调",
            "保留第一次；第二次改成动作、反应或留白；若是有意复沓，请在上下文里做出差异",
        ))

    # 段落开头重复（structure_check 只看前 4 个非空字符，这里补一个更直观的例子）。
    openings: Dict[str, int] = {}
    for para in _paragraphs(text):
        prefix = _norm(para)[:2]
        if prefix:
            openings[prefix] = openings.get(prefix, 0) + 1
    duplicates = sorted(((k, v) for k, v in openings.items() if v >= 3),
                        key=lambda x: -x[1])
    if duplicates:
        issues.append(_issue(
            "repeated-opening",
            "low",
            "paragraph",
            "段落开头重复",
            "、".join(k for k, _ in duplicates[:3]),
            "多段用同一种方式起笔，画面会变得平",
            "至少一半段落改用动作、声音、物件或对白开头；保留一处最强的",
        ))

    # 篇幅只做提示，不判定“好/坏”。
    chars = metrics.get("chars") or len(_norm(text))
    if chars < 1500:
        issues.append(_issue(
            "short-chapter",
            "low",
            "pace",
            f"本章 {chars} 字，篇幅偏短",
            f"{chars} 字",
            "短章不是错；但如果这是推进章，冲突可能还没展开就被收掉",
            "检查是否缺少一次阻碍、一次选择或一个具体场景；不要为了凑字加无关描写",
        ))
    elif chars > 8000:
        issues.append(_issue(
            "long-chapter",
            "low",
            "pace",
            f"本章 {chars} 字，篇幅偏长",
            f"{chars} 字",
            "长章容易让中段失去张力，读者也可能找不到停点",
            "按场景或目标变化检查能否拆章；不能拆时，至少在中段加一次反转或代价",
        ))

    order = {"high": 0, "medium": 1, "low": 2}
    issues.sort(key=lambda x: (order.get(x["severity"], 9), not x["patchable"]))
    issues = issues[:max_items]
    stats = {"issues": len(issues),
             "high": sum(1 for x in issues if x["severity"] == "high"),
             "medium": sum(1 for x in issues if x["severity"] == "medium"),
             "low": sum(1 for x in issues if x["severity"] == "low"),
             "chars": metrics.get("chars") or len(_norm(text)),
             "paragraphs": metrics.get("paragraphs") or len(_paragraphs(text)),
             "sentences": metrics.get("sentences") or len(sentences),
             "long_paragraphs": sum(1 for p in _paragraphs(text)
                                    if len(_norm(p)) > 280),
             "patchable": sum(1 for x in issues if x["patchable"])}

    return {
        "ok": True,
        "project": pid,
        "chapter_n": n,
        "title": chapter.get("title") or f"第{n}章",
        "version": chapter.get("version"),
        "verdict": report.get("verdict"),
        "metrics": metrics,
        "issues": issues,
        "stats": stats,
        "note": "规则体检，不是文学评分；补丁只覆盖能精确匹配的段落拆合，"
                "应用前必须在「局部改稿」里预览。",
    }
