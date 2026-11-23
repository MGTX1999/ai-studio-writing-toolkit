"""L0 确定性检查：零成本、可复现、只报告不修改正文。

分工原则（与 `docs/2026-11-22-优化研究.md` 的 4.3 一致）：
- 机器能判的（禁用词、字数区间、重复段落、待核事实、伏笔逾期、AI 味正则）
  全部在这里判，先跑、零 Token；
- 判不准的自然语言问题交给 LLM 终审，避免本地规则制造假阻断。

所有 finding 都必须带原文证据（evidence），否则视为不可验证。
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

import config_loader
import profile
from store import LongformError, MAX_CHAPTER_N, _as_int, _require_int

# ---------------------------------------------------------------- AI 味规则
# 六类黑名单。pattern 是编译好的正则（大小写无关），供 text_stats 与
# lint_chapter 直接复用；message 给作者看的整改方向。
AI_SMELL_RULES: List[dict] = [
    {
        "category": "宏大空洞",
        "pattern": re.compile(
            r"不可否认|毋庸置疑|画卷|交响乐|璀璨|日新月异|与时俱进|应运而生",
            re.IGNORECASE),
        "message": "宏大形容词替代了具体场景，建议改成可被看见/听见的细节",
    },
    {
        "category": "机械转折",
        "pattern": re.compile(
            r"综上所述|总而言之|值得注意的是|不仅.{0,20}而且|"
            r"一方面.{0,20}另一方面",
            re.IGNORECASE),
        "message": "模板化转折暴露了说明文腔调，建议用事件因果直接过渡",
    },
    {
        "category": "虚假比喻",
        "pattern": re.compile(
            r"宛如|恰似|犹如|如同.{0,20}一般", re.IGNORECASE),
        "message": "比喻过于顺滑，建议换成人物经验里真实见过的意象",
    },
    {
        "category": "结构毒药",
        "pattern": re.compile(
            r"不是.{0,20}而是|让我们|愿我们|这就是.{0,20}的意义",
            re.IGNORECASE),
        "message": "主题宣告式句式会把潜台词说破，建议留白",
    },
    {
        "category": "隐蔽 AI 味",
        "pattern": re.compile(
            r"引发了广泛关注|深刻地认识到|在某种意义上|事实上|显然",
            re.IGNORECASE),
        "message": "抽象概括替代了场景，建议落到具体人物与动作",
    },
    {
        "category": "翻译腔",
        "pattern": re.compile(
            r"对于.{0,20}的态度|被认为是一个|在某种程度上而言|"
            r"存在着.{0,20}的问题|做出了.{0,20}的举动|"
            r"基于.{0,20}的原因|通过.{0,20}的方式",
            re.IGNORECASE),
        "message": "欧化句式读起来发涩，建议改回中文常用语序",
    },
]

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_SENT_SPLIT_RE = re.compile(r"[。！？!?…]+")
_DIALOG_RE = re.compile(r"“([^”]*)”|「([^」]*)」|\"([^\"]*)\"")
_SENSORY_WORDS = ("看", "听", "闻", "摸", "尝", "冷", "热", "疼", "香", "腥",
                  "粗糙", "湿")
_BOUNDARY = "。！？!?…\n"

# 章节契约默认值；项目 config / defaults.yaml 均可覆盖。
DEFAULT_MIN_WORDS = 1500
DEFAULT_MAX_WORDS = 4000
# 短于这个值视为「空章 / 残章」，直接 block，避免残稿进入审校。
MIN_VIABLE_CHARS = 200
LONG_PARAGRAPH_CHARS = 500
REPEAT_MIN_CHARS = 15


def _split_paragraphs(text: str) -> List[str]:
    """按空行切段；整篇没有空行时退回按行切（中文网文常见一行为一段）。"""
    parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(parts) <= 1:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) > 1:
            return lines
    return parts


def text_stats(text: str) -> dict:
    """统计一段正文；字段与 P0-1 合同一致，供字数闸门与 AI 味扫描共用。"""
    if not isinstance(text, str):
        raise LongformError("text 必须是字符串")

    chars = len(text)
    chars_no_space = len(re.sub(r"\s+", "", text))
    cjk_chars = len(_CJK_RE.findall(text))
    paragraphs = _split_paragraphs(text)

    sentences = [s for s in _SENT_SPLIT_RE.split(text) if s.strip()]
    sentence_count = len(sentences)
    avg_sentence_len = (round(chars_no_space / sentence_count, 1)
                        if sentence_count else 0.0)

    dialogue_chars = 0
    for m in _DIALOG_RE.finditer(text):
        dialogue_chars += len(next((g for g in m.groups() if g is not None), ""))
    dialogue_ratio = (round(dialogue_chars / chars_no_space, 3)
                      if chars_no_space else 0.0)

    cliche_hits = 0
    for rule in AI_SMELL_RULES:
        cliche_hits += len(rule["pattern"].findall(text))

    return {
        "chars": chars,
        "chars_no_space": chars_no_space,
        "cjk_chars": cjk_chars,
        "paragraphs": len(paragraphs),
        "sentences": sentence_count,
        "avg_sentence_len": avg_sentence_len,
        "dialogue_chars": dialogue_chars,
        "dialogue_ratio": dialogue_ratio,
        "long_paragraphs": sum(1 for p in paragraphs
                              if len(p) > LONG_PARAGRAPH_CHARS),
        "cliche_hits": cliche_hits,
    }


def _locator(text: str, pos: int, n: int) -> str:
    """用「章号:行号」定位证据，方便作者回到原文。"""
    return f"ch{n}:p{text.count(chr(10), 0, pos) + 1}"


def _snippet(text: str, pos: int, length: int) -> str:
    lo = max(0, pos - 12)
    hi = min(len(text), pos + length + 12)
    return text[lo:hi].replace("\n", " ").strip()


def _sentence_at(text: str, pos: int) -> str:
    lo = 0
    for i in range(pos - 1, -1, -1):
        if text[i] in _BOUNDARY:
            lo = i + 1
            break
    hi = len(text)
    for i in range(pos, len(text)):
        if text[i] in _BOUNDARY:
            hi = i + 1
            break
    return text[lo:hi]


def _is_whitelisted(sentence: str) -> bool:
    """同句有对白或具体感官词时降级（仍 warn，不升级为 block）。"""
    if any(q in sentence for q in ("“", "”", "「", "」", '"')):
        return True
    return any(word in sentence for word in _SENSORY_WORDS)


def lint_chapter(s: object, pid: str, chapter_n: int, *,
                 run_id: Optional[str] = None,
                 persist: bool = True) -> dict:
    """对某章最新版跑 L0 检查；``persist=True`` 时先清后写检查结果。"""
    n = _require_int(chapter_n, "chapter_n", minimum=0, maximum=MAX_CHAPTER_N)
    chapter = s.chapter_get(pid, n)
    if chapter is None:
        raise LongformError(f"第 {n} 章还没有任何版本，无法检查")

    text = chapter.get("content") or ""
    stats = text_stats(text)

    defaults = config_loader.load_defaults()
    project_cfg = s.config_get(pid)
    if not isinstance(project_cfg, dict):
        project_cfg = {}
    merged = profile.deep_merge(defaults if isinstance(defaults, dict) else {},
                                project_cfg)
    chapter_cfg = merged.get("chapter") if isinstance(merged, dict) else None
    if not isinstance(chapter_cfg, dict):
        chapter_cfg = {}
    min_words = _as_int(chapter_cfg.get("min_words"), DEFAULT_MIN_WORDS,
                        minimum=0, maximum=MAX_CHAPTER_N)
    max_words = _as_int(chapter_cfg.get("max_words"), DEFAULT_MAX_WORDS,
                        minimum=1, maximum=MAX_CHAPTER_N)

    banned = s.style_get(pid, "banned_words")
    if not isinstance(banned, list):
        banned = []
    banned = [w.strip() for w in banned if isinstance(w, str) and w.strip()]

    findings: List[dict] = []

    # 1) 禁用词 → block
    for word in banned:
        start = text.find(word)
        while start != -1:
            findings.append({
                "rule": "banned_word",
                "level": "block",
                "evidence": _snippet(text, start, len(word)),
                "locator": _locator(text, start, n),
                "suggestion": f"删除或替换禁用词「{word}」",
            })
            start = text.find(word, start + len(word))

    # 2) 正文写入待核事实 → block
    for fact in s.fact_list(pid, "pending"):
        claim = (fact.get("claim") or "").strip()
        if not claim:
            continue
        start = text.find(claim)
        if start == -1:
            continue
        findings.append({
            "rule": "pending_fact",
            "level": "block",
            "evidence": _snippet(text, start, len(claim)),
            "locator": _locator(text, start, n),
            "suggestion": "该断言仍是 pending：先核实并 fact_set_status，"
                          "或从正文删除",
        })

    # 3) AI 味 → warn（有对白/感官细节时标记 whitelisted）
    for rule in AI_SMELL_RULES:
        for m in rule["pattern"].finditer(text):
            sentence = _sentence_at(text, m.start())
            findings.append({
                "rule": "ai_smell",
                "level": "warn",
                "evidence": _snippet(text, m.start(), len(m.group(0))),
                "locator": _locator(text, m.start(), n),
                "suggestion": f"[{rule['category']}] {rule['message']}",
                "whitelisted": _is_whitelisted(sentence),
            })

    # 4) 重复段落 → warn
    seen: Dict[str, int] = {}
    for idx, para in enumerate(_split_paragraphs(text), 1):
        norm = re.sub(r"\s+", "", para)
        if len(norm) < REPEAT_MIN_CHARS:
            continue
        if norm in seen:
            findings.append({
                "rule": "repeated_paragraph",
                "level": "warn",
                "evidence": para[:60],
                "locator": f"ch{n}:p{idx}",
                "suggestion": f"与第 {seen[norm]} 段重复，建议删除或改写",
            })
        else:
            seen[norm] = idx

    # 5) 字数闸门
    count = stats["chars_no_space"]
    if count < MIN_VIABLE_CHARS or count > max_words * 2:
        findings.append({
            "rule": "word_count",
            "level": "block",
            "evidence": f"去空白 {count} 字（目标 {min_words}–{max_words}）",
            "locator": f"ch{n}",
            "suggestion": "空章/残章或篇幅严重超标，先修到正常区间再送审",
        })
    elif not (min_words <= count <= max_words):
        findings.append({
            "rule": "word_count",
            "level": "warn",
            "evidence": f"去空白 {count} 字（目标 {min_words}–{max_words}）",
            "locator": f"ch{n}",
            "suggestion": "篇幅偏离目标区间，确认这是有意为之",
        })

    # 6) 对话占比
    ratio = stats["dialogue_ratio"]
    if ratio < 0.1 or ratio > 0.8:
        findings.append({
            "rule": "dialogue_ratio",
            "level": "warn",
            "evidence": f"对话占比 {ratio:.1%}",
            "locator": f"ch{n}",
            "suggestion": "对话过少或过多会让节奏失衡，建议调整",
        })

    # 7) 伏笔逾期
    for item in s.foreshadow_overdue(pid, n, 8):
        findings.append({
            "rule": "foreshadow_overdue",
            "level": "warn",
            "evidence": item.get("text") or "",
            "locator": f"ch{n}",
            "suggestion": "该伏笔已逾期未收，计划回收或显式放弃",
        })

    counts = {"block": 0, "warn": 0}
    for f in findings:
        counts[f["level"]] = counts.get(f["level"], 0) + 1

    if persist:
        # 先按章清空，避免重复累积；带 run_id 时再清一次该运行的旧结果。
        s.lint_findings_clear(pid, chapter_n=n)
        if run_id:
            s.lint_findings_clear(pid, run_id=run_id)
        for f in findings:
            s.lint_finding_add(
                pid, n, f["rule"], f["level"], run_id=run_id,
                evidence=f["evidence"], locator=f["locator"],
                suggestion=f["suggestion"])

    return {
        "ok": True,
        "project": pid,
        "chapter_n": n,
        "stats": stats,
        "blocked": counts["block"] > 0,
        "counts": counts,
        "findings": findings,
    }
