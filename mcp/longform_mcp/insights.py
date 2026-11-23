"""叙事工作台 v1.7：章节树、结构体检、串声检查、故事图谱、证据审计与申诉包。

设计原则与 v1.5 的叙事层一致：

- **纯标准库**：不引入 numpy / pandas / 前端构建链；
- **只读分析优先**：除预期账本与申诉包外，不在这个模块里改正文；
- **可解释**：每个结论都带证据（命中词、章号、分数、来源 id），
  让作者能反驳，而不是给一个黑箱评分。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import zipfile
from typing import Any, Dict, List, Optional, Tuple

import narrative
from store import (
    EXPECTATION_STATES,
    MAX_CHAPTER_N,
    MAX_SEARCH_LIMIT,
    MAX_TEXT_LEN,
    LongformError,
    _require_int,
    _require_str,
)

# 中文句子切分：保留句末标点，连续标点不单独成句。
_SENT_RE = re.compile(r"[^。！？!?…]+[。！？!?…]?")
_QUOTE_RE = re.compile(r"[“「『\"](.+?)[”」』\"]", re.S)
_SCENE_RE = re.compile(
    r"^\s*(?:[＊*×—\-]{3,}|第[一二三四五六七八九十百0-9]+节|"
    r"场景[：:]|【[^】]{1,40}】|地点[：:])\s*$")
_THEME_WORDS = ("命运", "自由", "真相", "救赎", "爱", "孤独", "尊严",
                "权力", "牺牲", "成长", "意义", "正义")
_EMOTION_WORDS = ("痛苦", "愤怒", "恐惧", "喜悦", "悲伤", "绝望",
                  "希望", "温柔", "恨", "笑", "哭", "颤")
_LINK_RE = re.compile(r"\[\[([^\[\]]{1,120})\]\]")


# ============================================================
# 基础工具
# ============================================================
def _clip(text: str, limit: int = 160) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def _norm_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _sentences(text: str) -> List[str]:
    return [s.strip() for s in _SENT_RE.findall(text or "") if s.strip()]


def _paragraphs(text: str) -> List[str]:
    parts = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    if parts:
        return parts
    return [p.strip() for p in (text or "").splitlines() if p.strip()]


def _dialogue_matches(text: str) -> List[str]:
    return [m.group(1).strip() for m in _QUOTE_RE.finditer(text or "")
            if m.group(1).strip()]


def _finding(layer: str, level: str, quote: str, why: str,
             alternatives: Optional[List[str]] = None,
             category: str = "") -> Dict[str, Any]:
    return {
        "layer": layer,
        "level": level,
        "quote": _clip(quote),
        "why": why,
        "alternatives": (alternatives or [])[:3],
        "category": category,
    }


def _dedupe_findings(findings: List[dict], limit: int = 40) -> List[dict]:
    order = {"red": 0, "yellow": 1, "gray": 2}
    seen = set()
    out = []
    for item in sorted(findings,
                       key=lambda x: (order.get(x.get("level"), 9),
                                      x.get("layer", ""), x.get("quote", ""))):
        key = (item.get("layer"), item.get("level"),
               item.get("quote"), item.get("why"), item.get("category"))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out[:limit]


def _counts(findings: List[dict]) -> Dict[str, int]:
    counts = {"red": 0, "yellow": 0, "gray": 0}
    for item in findings:
        level = item.get("level", "gray")
        counts[level] = counts.get(level, 0) + 1
    return counts


def _verdict(counts: Dict[str, int]) -> str:
    if counts.get("red"):
        return "red"
    if counts.get("yellow"):
        return "yellow"
    if counts.get("gray"):
        return "gray"
    return "green"


# ============================================================
# 章节树
# ============================================================
def chapter_tree(s, pid: str) -> dict:
    s._require_project(pid)
    chapters = s.chapter_list(pid)
    audits = s.audit_list(pid)
    foreshadow = s.foreshadow_list(pid)
    materials = s.material_list(pid, limit=MAX_SEARCH_LIMIT)
    material_counts: Dict[int, int] = {}
    for item in materials:
        if item.get("chapter") is not None:
            material_counts[int(item["chapter"])] = \
                material_counts.get(int(item["chapter"]), 0) + 1

    by_chapter_audit: Dict[int, List[dict]] = {}
    for audit in audits:
        by_chapter_audit.setdefault(int(audit.get("chapter_n") or 0), []).append(audit)

    rows = []
    for chapter in chapters:
        n = int(chapter["n"])
        versions = s.chapter_versions(pid, n)
        latest_version = versions[-1] if versions else {}
        latest_audit = None
        for audit in reversed(by_chapter_audit.get(n, [])):
            if audit.get("kind") == "compliance":
                latest_audit = audit
                break
        risk = (latest_audit or {}).get("verdict", "")
        blocked = bool((latest_audit or {}).get("export_blocked"))
        due = [f for f in foreshadow
               if f.get("status") == "open"
               and f.get("due_chapter") is not None
               and int(f["due_chapter"]) <= n]
        rows.append({
            "n": n,
            "title": chapter.get("title") or f"第{n}章",
            "status": chapter.get("status") or "draft",
            "version": int(chapter.get("latest_version") or 0),
            "words": int(latest_version.get("words") or 0),
            "risk": risk,
            "export_blocked": blocked,
            "due_foreshadow": len(due),
            "materials": material_counts.get(n, 0),
            "has_outline": bool(s.outline_by_chapter(pid, n)),
        })

    max_n = max([r["n"] for r in rows] + [0])
    return {
        "ok": True,
        "chapters": rows,
        "next_chapter": max_n + 1,
        "stats": {
            "chapters": len(rows),
            "words": sum(r["words"] for r in rows),
            "blocked": sum(1 for r in rows if r["export_blocked"]),
            "red": sum(1 for r in rows if r["risk"] == "red"),
            "yellow": sum(1 for r in rows if r["risk"] == "yellow"),
            "approved": sum(1 for r in rows if r["status"] == "approved"),
        },
    }


# ============================================================
# 章节结构体检
# ============================================================
def structure_check(text: str) -> dict:
    if not isinstance(text, str) or not text.strip():
        raise LongformError("text 不能为空")
    if len(text) > MAX_TEXT_LEN:
        raise LongformError(f"text 超过 {MAX_TEXT_LEN} 字符上限")

    paragraphs = _paragraphs(text)
    sentences = _sentences(text)
    chars = len(_norm_text(text))
    dialogue = _dialogue_matches(text)
    dialogue_chars = sum(len(_norm_text(x)) for x in dialogue)
    dialogue_ratio = dialogue_chars / chars if chars else 0.0
    avg_len = (sum(len(_norm_text(s)) for s in sentences) / len(sentences)
               if sentences else 0.0)
    long_sentences = [s for s in sentences if len(_norm_text(s)) > 40]
    scene_lines = [line for line in (text or "").splitlines()
                   if _SCENE_RE.match(line or "")]
    last = (text or "")[-300:]
    last_sentence = sentences[-1] if sentences else ""
    hook_signals = []
    if re.search(r"[？?！!…]", last):
        hook_signals.append("句末疑问/感叹/省略")
    if re.search(r"(可是|然而|直到|忽然|突然|却|偏偏|没想到|就在这时|下一秒)", last):
        hook_signals.append("转折词收尾")
    if len(last_sentence) <= 30 and last_sentence:
        hook_signals.append("短句收尾")
    hook_score = min(3, len(hook_signals))

    theme_hits = []
    for sentence in sentences:
        for word in _THEME_WORDS:
            if word in sentence and re.search(r"(是|就是|意味着|说到底|归根结底)", sentence):
                theme_hits.append({"word": word, "sentence": _clip(sentence, 80)})
                break

    openings: Dict[str, int] = {}
    for para in paragraphs:
        key = _norm_text(para)[:4]
        if key:
            openings[key] = openings.get(key, 0) + 1
    repeated_openings = [{"prefix": k, "count": v}
                         for k, v in openings.items() if v >= 3]

    # 情绪曲线按字符位置切 10 段；没有强烈情绪词时仍有对白/标点信号。
    segments = []
    total = max(1, len(text))
    for i in range(10):
        start = total * i // 10
        end = total * (i + 1) // 10 if i < 9 else total
        chunk = text[start:end]
        if not chunk.strip():
            continue
        exclaims = chunk.count("！") + chunk.count("!")
        questions = chunk.count("？") + chunk.count("?")
        strong = sum(chunk.count(w) for w in _EMOTION_WORDS)
        dlg = sum(len(_norm_text(x)) for x in _dialogue_matches(chunk))
        density = (exclaims * 2 + questions + strong * 1.5
                   + (dlg / max(1, len(_norm_text(chunk)))) * 10)
        score = min(100, int(round(density * 8)))
        segments.append({
            "index": i + 1,
            "start": start,
            "end": end,
            "score": score,
            "dialogue_chars": dlg,
            "exclaims": exclaims,
            "questions": questions,
        })

    findings: List[dict] = []
    if dialogue_ratio < 0.12 and len(text) > 500:
        findings.append(_finding("structure", "yellow", f"对白占比 {dialogue_ratio:.0%}",
                                 "整章几乎都是叙述，人物声音容易消失",
                                 ["挑一处冲突改成 3-5 轮短对白"]))
    if dialogue_ratio > 0.72 and len(text) > 500:
        findings.append(_finding("structure", "yellow", f"对白占比 {dialogue_ratio:.0%}",
                                 "对白过密，动作与场景信息会被挤掉",
                                 ["每 4-6 轮对白插一个动作或环境反应"]))
    if avg_len > 42:
        findings.append(_finding("structure", "yellow", f"平均句长 {avg_len:.1f} 字",
                                 "长句密度过高，阅读速度会掉",
                                 ["把信息点拆成两到三个短句"]))
    if not hook_score:
        findings.append(_finding("structure", "red", last_sentence or "章末",
                                 "章末没有钩子：既无悬念句，也无转折或短句收束",
                                 ["在最后 3 行补一个未解问题或动作打断"]))
    if len(scene_lines) == 0 and len(paragraphs) > 8:
        findings.append(_finding("structure", "gray", "场景切换",
                                 "没有识别到场景分隔标记，读者可能分不清时间/地点切换",
                                 ["用空行或「***」明确分隔场景"]))
    if repeated_openings:
        findings.append(_finding("structure", "gray",
                                 "、".join(x["prefix"] for x in repeated_openings[:3]),
                                 "多段开头重复，段落节奏会变得平",
                                 ["至少一半段落改用动作、声音或物件开头"]))
    if len(theme_hits) >= 3:
        findings.append(_finding("structure", "gray", f"主题句 {len(theme_hits)} 处",
                                 "主题词被反复点明，读者没有被留出自己体会的空间",
                                 ["保留一处最关键的，其余改成行动或物件"]))
    findings = _dedupe_findings(findings)
    counts = _counts(findings)
    return {
        "ok": True,
        "kind": "structure",
        "verdict": _verdict(counts),
        "counts": counts,
        "metrics": {
            "chars": chars,
            "paragraphs": len(paragraphs),
            "sentences": len(sentences),
            "dialogue_ratio": round(dialogue_ratio, 3),
            "dialogue_chars": dialogue_chars,
            "avg_sentence_len": round(avg_len, 1),
            "long_sentences": len(long_sentences),
            "scene_markers": len(scene_lines),
            "hook_score": hook_score,
            "hook_signals": hook_signals,
            "theme_hits": len(theme_hits),
            "repeated_openings": len(repeated_openings),
        },
        "segments": segments,
        "theme_hits": theme_hits[:5],
        "findings": findings,
    }


# ============================================================
# 多角色串声交叉检查
# ============================================================
def _voice_of(profile: dict) -> dict:
    voice = profile.get("voice")
    return voice if isinstance(voice, dict) else profile


def _as_list(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return []


def _speaker_label(text: str) -> str:
    m = re.match(r"^\s*([\u4e00-\u9fa5A-Za-z0-9_]{1,12})\s*[：:]", text or "")
    if m:
        return m.group(1)
    m = re.search(r"([\u4e00-\u9fa5]{2,4})(?:说|道|问|答|笑|低声|轻声|喊|叫)"
                  r"[，,:：]?\s*[“「]", text or "")
    return m.group(1) if m else ""


def _speech_segments(text: str) -> List[dict]:
    segments: List[dict] = []
    for idx, line in enumerate((text or "").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        quoted = _dialogue_matches(line)
        label = _speaker_label(line)
        content = " ".join(quoted) if quoted else ""
        if not content and label:
            content = re.split(r"[：:]", line, maxsplit=1)[-1].strip()
        if not content:
            continue
        segments.append({"line": idx, "text": _clip(content, 180),
                         "raw": content, "label": label})
    return segments


def _voice_score(profile: dict, text: str) -> Tuple[float, List[str]]:
    voice = _voice_of(profile)
    name = profile.get("name") or voice.get("name") or "角色"
    tics = _as_list(voice.get("verbal_tics"))
    banned = _as_list(voice.get("banned_words"))
    self_ref = _as_list(voice.get("self_ref"))
    sentence_max = voice.get("sentence_max")
    register = voice.get("register") or ""
    score = 0.0
    reasons: List[str] = []

    hits = sum(text.count(t) for t in tics)
    if hits:
        score += min(6, hits * 2)
        reasons.append(f"口癖命中 {hits} 次")
    for term in banned:
        if term in text:
            score -= 8
            reasons.append(f"禁用词「{term}」")
    if self_ref and any(ref in text for ref in self_ref):
        score += 2
        reasons.append("自称命中")
    elif self_ref and "我" in text and not any(ref in text for ref in self_ref):
        score -= 4
        reasons.append("自称不符")
    if isinstance(sentence_max, int) and sentence_max > 0:
        over = sum(1 for s in _sentences(text)
                   if len(_norm_text(s)) > sentence_max)
        if over:
            score -= min(3, over)
            reasons.append(f"超句长 {over} 句")
    avg = (sum(len(_norm_text(s)) for s in _sentences(text))
           / max(1, len(_sentences(text))))
    if register == "短句" and avg <= 18:
        score += 2
        reasons.append("短句语域")
    elif register == "松弛" and avg >= 14:
        score += 1
        reasons.append("松弛语域")
    if name and name in text:
        score += 1
        reasons.append("自名出现")
    return score, reasons


def voice_cross_check(s, pid: str, text: str) -> dict:
    if not isinstance(text, str) or not text.strip():
        raise LongformError("text 不能为空")
    profiles = s.bible_list(pid, "character")
    if not profiles:
        raise LongformError("还没有人物卡；先建人物卡并填写 data.voice，才能做串声检查")
    segments = _speech_segments(text)[:100]
    if not segments:
        raise LongformError("没有识别到对白；请用引号包住对白，或写成「角色：台词」")

    names = [p.get("name") or "" for p in profiles]
    out = []
    mismatch = 0
    uncertain = 0
    for seg in segments:
        scores = []
        for profile in profiles:
            raw, reasons = _voice_score(profile, seg["raw"])
            name = profile.get("name") or ""
            # 其他角色的口癖出现在这句话里，是串声的强信号。
            for other in profiles:
                if other.get("name") == name:
                    continue
                other_voice = _voice_of(other)
                for tic in _as_list(other_voice.get("verbal_tics")):
                    if tic and tic in seg["raw"]:
                        raw -= 2
                        reasons.append(f"含{other.get('name')}口癖「{tic}」")
            if seg["label"]:
                if seg["label"] == name:
                    raw += 3
                    reasons.append("说话人标注一致")
                elif seg["label"] in names:
                    raw -= 1
            scores.append({"name": name, "score": round(raw, 1),
                           "reasons": reasons[:5]})
        scores.sort(key=lambda x: x["score"], reverse=True)
        top = scores[0]
        second = scores[1] if len(scores) > 1 else {"name": "", "score": 0}
        margin = round(top["score"] - second["score"], 1)
        if top["score"] < 2 or margin < 2:
            confidence = "low"
            uncertain += 1
        elif margin < 4:
            confidence = "medium"
        else:
            confidence = "high"
        label = seg["label"]
        mismatch_flag = bool(label and label in names
                             and label != top["name"] and margin >= 2)
        if mismatch_flag:
            mismatch += 1
        issues = []
        if mismatch_flag:
            issues.append(f"标注为「{label}」，语言指纹更像「{top['name']}」")
        if confidence == "low":
            issues.append("分差太小，无法可靠归属；需要补口癖或改句长")
        if top["score"] < 0:
            issues.append(f"最像的角色得分仍为负：{top['name']} 的禁用词/自称可能被违反")
        out.append({
            "line": seg["line"],
            "text": seg["text"],
            "label": label,
            "predicted": top["name"],
            "score": top["score"],
            "runner_up": second["name"],
            "margin": margin,
            "confidence": confidence,
            "mismatch": mismatch_flag,
            "issues": issues,
            "scores": scores,
        })

    verdict = "red" if mismatch else ("yellow" if uncertain else "green")
    return {
        "ok": True,
        "kind": "voice_cross",
        "verdict": verdict,
        "characters": names,
        "segments": out,
        "summary": {
            "total": len(out),
            "mismatch": mismatch,
            "uncertain": uncertain,
            "high_confidence": sum(1 for x in out if x["confidence"] == "high"),
        },
        "note": "这是规则匹配，不是语义判断；低置信句请人工复核。",
    }


# ============================================================
# OOC：行为 / 关系 / 能力三类补全
# ============================================================
def _rule_to_dict(rule: Any, default_level: str = "yellow") -> dict:
    if isinstance(rule, str):
        return {"pattern": rule, "level": default_level,
                "why": f"命中行为红线「{rule}」", "fix": "改成符合人物动机的动作"}
    if isinstance(rule, dict):
        return {
            "pattern": str(rule.get("pattern") or rule.get("text") or "").strip(),
            "level": rule.get("level") or default_level,
            "why": str(rule.get("why") or rule.get("note") or ""),
            "fix": str(rule.get("fix") or rule.get("alternative") or ""),
        }
    return {}


def ooc_check(s, pid: str, character: str,
              text: str) -> dict:
    if not isinstance(text, str) or not text.strip():
        raise LongformError("text 不能为空")
    data = s.bible_get(pid, "character", character)
    if not isinstance(data, dict):
        raise LongformError(f"未找到人物卡「{character}」")
    profile = dict(data)
    profile.setdefault("name", character)
    others = [item for item in s.bible_list(pid, "character")
              if item.get("name") != character]
    try:
        base = narrative.voice_check(profile, text, others)
    except ValueError as e:
        raise LongformError(str(e)) from None

    findings = []
    for item in base.get("findings", []):
        item = dict(item)
        item["category"] = "voice"
        findings.append(item)

    ooc = data.get("ooc") if isinstance(data.get("ooc"), dict) else {}
    rules = ooc.get("behavior_rules", data.get("behavior_rules", []))
    for rule in (rules if isinstance(rules, list) else []):
        item = _rule_to_dict(rule)
        if item.get("pattern") and item["pattern"] in text:
            findings.append(_finding("ooc", item["level"], item["pattern"],
                                     item["why"] or "命中行为红线",
                                     [item["fix"]] if item["fix"] else [],
                                     category="behavior"))

    limits = ooc.get("ability_limits", data.get("ability_limits", []))
    for rule in (limits if isinstance(limits, list) else []):
        item = _rule_to_dict(rule, "red")
        if item.get("pattern") and item["pattern"] in text:
            findings.append(_finding("ooc", item["level"], item["pattern"],
                                     item["why"] or "出现人物卡未登记的能力",
                                     [item["fix"]] if item["fix"] else [],
                                     category="ability"))

    relations = data.get("relations") or data.get("relationships") or []
    for rel in relations if isinstance(relations, list) else []:
        if not isinstance(rel, dict):
            continue
        target = str(rel.get("target") or rel.get("name") or "").strip()
        if not target or target not in text:
            continue
        for forbidden in _as_list(rel.get("forbidden")):
            if forbidden and forbidden in text:
                findings.append(_finding(
                    "ooc", "yellow", forbidden,
                    f"对「{target}」出现关系红线「{forbidden}」；"
                    f"{rel.get('note') or '这层关系不该用这种动作/称呼'}",
                    [f"换一种符合「{rel.get('type') or '关系'}」的处理"],
                    category="relation"))
        for required in _as_list(rel.get("required")):
            if required and required not in text and rel.get("enforce"):
                findings.append(_finding(
                    "ooc", "gray", target,
                    f"与「{target}」互动时缺少关系约束「{required}」",
                    [f"补一个「{required}」的动作或称呼"],
                    category="relation"))

    state = s.story_state_get(pid, "character-state")
    flags = {}
    if isinstance(state, dict):
        candidate = state.get(character)
        if isinstance(candidate, dict):
            flags = candidate
    if flags.get("dead") and re.search(r"(走|跑|说|笑|站|推|拿|看|喊)", text):
        findings.append(_finding(
            "ooc", "red", character,
            "故事状态标记该角色已死亡，但本章仍有主动动作；"
            "如果是回忆/幻觉，请在正文里明确标记",
            ["加“回忆/幻觉/录音”框，或修正故事状态"],
            category="behavior"))
    if flags.get("injured") and re.search(r"(跑|跳|搏斗|飞奔)", text):
        findings.append(_finding(
            "ooc", "yellow", character,
            "故事状态标记该角色受伤，但出现高强度动作；确认伤势是否被忽略",
            ["补一句疼痛/限制，或降低动作强度"],
            category="behavior"))

    deduped = _dedupe_findings(findings)
    counts = _counts(deduped)
    categories = {"voice": 0, "behavior": 0, "relation": 0, "ability": 0}
    for item in deduped:
        cat = item.get("category") or "voice"
        categories[cat] = categories.get(cat, 0) + 1
    return {
        "ok": True,
        "kind": "ooc",
        "character": character,
        "verdict": _verdict(counts),
        "counts": counts,
        "categories": categories,
        "findings": deduped,
        "voice_fields": base.get("voice_fields", {}),
        "checked_chars": len(text),
        "note": "行为/关系/能力规则来自人物卡 data.ooc、data.relations、data.ability_limits；"
                "未配置的类别不会凭空推断。",
    }


# ============================================================
# 故事图谱：人物关系 / 伏笔链路 / 双轨时间线 / 素材网络
# ============================================================
def _normalize_relation(rel: Any) -> Optional[dict]:
    if isinstance(rel, str):
        text = rel.strip()
        if not text:
            return None
        parts = re.split(r"[:：\-—]", text, maxsplit=1)
        target = parts[0].strip()
        kind = parts[1].strip() if len(parts) > 1 else "关联"
        return {"target": target, "type": kind, "note": "", "strength": 1}
    if isinstance(rel, dict):
        target = str(rel.get("target") or rel.get("name") or "").strip()
        if not target:
            return None
        return {
            "target": target,
            "type": str(rel.get("type") or rel.get("relation") or "关联"),
            "note": str(rel.get("note") or rel.get("why") or ""),
            "strength": rel.get("strength") or 1,
        }
    return None


def _relations_graph(s, pid: str, character: Optional[str] = None) -> dict:
    entities = s.bible_list(pid, "character")
    nodes = []
    edges = []
    known = set()
    for item in entities:
        name = item.get("name") or ""
        known.add(name)
        nodes.append({
            "id": name, "name": name,
            "role": item.get("role") or item.get("identity") or "",
            "missing": False,
        })
        rels = item.get("relations") or item.get("relationships") or []
        for rel in rels if isinstance(rels, list) else []:
            normalized = _normalize_relation(rel)
            if not normalized:
                continue
            edges.append({
                "source": name,
                "target": normalized["target"],
                "type": normalized["type"],
                "note": normalized["note"],
                "strength": normalized["strength"],
            })
    for edge in edges:
        if edge["target"] not in known:
            known.add(edge["target"])
            nodes.append({"id": edge["target"], "name": edge["target"],
                          "role": "", "missing": True})

    focus = (character or "").strip()
    all_characters = [n["name"] for n in nodes if not n.get("missing")]
    if focus:
        neighbor_ids = {focus}
        for edge in edges:
            if edge["source"] == focus:
                neighbor_ids.add(edge["target"])
            if edge["target"] == focus:
                neighbor_ids.add(edge["source"])
        # 只保留一跳邻域，避免全图太密。
        nodes = [n for n in nodes if n["id"] in neighbor_ids]
        edges = [e for e in edges if e["source"] in neighbor_ids
                 and e["target"] in neighbor_ids]
    return {
        "ok": True,
        "focus": focus,
        "nodes": nodes,
        "characters": all_characters,
        "edges": edges,
        "missing_targets": [n["id"] for n in nodes if n.get("missing")],
        "hint": "人物卡 data.relations 支持字符串或对象："
                "{\"target\":\"周砚\",\"type\":\"旧友\",\"note\":\"互相欠一条命\"}",
    }


def _foreshadow_graph(s, pid: str) -> dict:
    rows = s.foreshadow_list(pid)
    chapters = s.chapter_list(pid)
    max_chapter = max([int(c["n"]) for c in chapters] + [0])
    out = []
    for item in rows:
        planted = int(item.get("planted_chapter") or 0)
        due = item.get("due_chapter")
        resolved = item.get("resolved_chapter")
        end = int(resolved or due or max_chapter or planted)
        overdue = bool(item.get("status") == "open" and due is not None
                       and int(due) <= max_chapter)
        out.append({
            "id": item.get("id"),
            "text": item.get("text") or "",
            "status": item.get("status") or "open",
            "planted": planted,
            "due": int(due) if due is not None else None,
            "resolved": int(resolved) if resolved is not None else None,
            "end": max(planted, end),
            "span": max(1, max(planted, end) - planted),
            "overdue": overdue,
        })
    return {
        "ok": True,
        "max_chapter": max(max_chapter, max([r["end"] for r in out] + [0])),
        "rows": out,
        "open": sum(1 for r in out if r["status"] == "open"),
        "overdue": sum(1 for r in out if r["overdue"]),
        "hint": "横条从 planted 到 due/resolved；红色表示已逾期未收。",
    }


def _timeline_graph(s, pid: str) -> dict:
    events = s.timeline_query(pid)
    out = []
    for event in events:
        story_time = (event.get("story_time") or event.get("storyTime")
                      or event.get("time") or "")
        narrative_time = (event.get("narrative_time")
                          or event.get("narrativeTime") or "")
        out.append({
            "id": event.get("id"),
            "title": event.get("title") or "",
            "chapter": int(event.get("chapter") or 0),
            "seq": int(event.get("seq") or 0),
            "story_time": str(story_time),
            "narrative_time": str(narrative_time),
            "place": str(event.get("place") or event.get("location") or ""),
        })
    story_order = sorted(out, key=lambda e: (e["story_time"] or "~",
                                             e["chapter"], e["seq"]))
    return {
        "ok": True,
        "events": out,
        "story_order": story_order,
        "narrative_order": sorted(out, key=lambda e: (e["chapter"], e["seq"])),
        "hint": "在 timeline_add 的 data 里填 story_time（故事内时间）与 "
                "narrative_time（叙述时间），两条轨道才会分开。",
    }


def _material_hash(content: str) -> str:
    return hashlib.sha1(_norm_text(content).encode("utf-8", "replace")).hexdigest()


def _material_evidence(item: dict) -> dict:
    """把素材按可核查程度分为 E0–E3；不把“有 URL”误当正文已核对。"""
    data = item.get("data_json") or {}
    if not isinstance(data, dict):
        data = {}
    source = str(item.get("source") or "")
    source_kind = str(item.get("source_kind") or "")
    content = str(item.get("content") or "")
    declared = str(data.get("evidence_level") or "").strip().lower()
    verification = str(data.get("verification_status") or "").strip().lower()
    content_source = str(data.get("content_source") or "").strip().lower()
    corroborated = data.get("corroborated_by") or []
    if isinstance(corroborated, str):
        corroborated = [corroborated] if corroborated.strip() else []
    has_second_source = isinstance(corroborated, list) and bool(corroborated)
    has_fulltext = bool(content.strip()) and (
        content_source in ("user_paste", "ima_export", "file_import")
        or source.startswith("ima-export://")
        or source_kind == "file"
        or verification in ("verified", "fulltext", "checked")
        or declared in ("e2", "fulltext", "primary")
    )
    if data.get("catalog_only") or declared in ("e0", "catalog"):
        level, label, reason = "E0", "题录线索", "只有题录或被明确标记为 catalog_only"
    elif has_second_source and (has_fulltext or declared in ("e3", "corroborated")):
        level, label, reason = "E3", "交叉核验", "正文可用且记录了独立交叉来源"
    elif has_fulltext:
        level, label, reason = "E2", "正文可用", "已取得正文或记录了原文核验状态"
    elif content.strip() or data.get("abstract") or declared in ("e1", "abstract"):
        level, label, reason = "E1", "摘要/未核材料", "有内容或摘要，但缺少可确认的正文核验标记"
    else:
        level, label, reason = "E0", "来源待补", "没有可审读正文；有链接也不等于已读原文"
    actions = {
        "E0": ("选题与待读目录", "补正文；若来自订阅库，由作者粘贴或客户端导出"),
        "E1": ("方法线索，不作关键事实引用", "回到原文核对，并记录 verification_status 与出处"),
        "E2": ("正文核对后的引用", "关键主张可再补 corroborated_by 独立来源"),
        "E3": ("关键事实引用", "保留正文与交叉来源，后续改结论时重新核验"),
    }
    usable, action = actions[level]
    return {"level": level, "label": label, "reason": reason,
            "usable_for": usable, "next_action": action}


def material_audit(s, pid: str, level: Optional[str] = None) -> dict:
    s._require_project(pid)
    items = s.material_list(pid, limit=MAX_SEARCH_LIMIT)
    wanted = (level or "").strip().upper()
    if wanted and wanted not in ("E0", "E1", "E2", "E3"):
        raise LongformError("level 必须是 E0 / E1 / E2 / E3 之一")
    rows = []
    counts = {key: 0 for key in ("E0", "E1", "E2", "E3")}
    for item in items:
        evidence = _material_evidence(item)
        counts[evidence["level"]] += 1
        if wanted and evidence["level"] != wanted:
            continue
        rows.append({
            "id": item.get("id"), "title": item.get("title") or "",
            "kind": item.get("kind"), "source": item.get("source") or "",
            "source_kind": item.get("source_kind") or "",
            "chapter": item.get("chapter"), "chars": item.get("chars") or 0,
            **evidence,
        })
    return {
        "ok": True, "items": rows, "total": len(items),
        "counts": counts,
        "e2_plus": counts["E2"] + counts["E3"],
        "truncated": len(items) >= MAX_SEARCH_LIMIT,
        "audit_scope": "E0–E3 只表示可核查程度，不是内容质量或可信度评分。",
        "levels": {"E0": "题录/来源待补", "E1": "摘要或未核材料",
                   "E2": "正文可用", "E3": "交叉核验"},
    }


def expectation_audit(s, pid: str, chapter_n: Optional[int] = None) -> dict:
    """检查预期承诺的记录完整性，不代替作者判断文学性兑现。"""
    s._require_project(pid)
    rows = s.expectation_list(pid, chapter_n)
    audited = []
    issue_counts: Dict[str, int] = {}
    for row in rows:
        state = row.get("state") or ""
        note = str(row.get("note") or "").strip()
        setup = str(row.get("setup") or "").strip() or note
        trigger = str(row.get("trigger") or "").strip()
        payoff = str(row.get("payoff") or "").strip()
        evidence = str(row.get("payoff_evidence") or "").strip()
        issues = []

        def add(code: str, message: str):
            issues.append({"code": code, "message": message})
            issue_counts[code] = issue_counts.get(code, 0) + 1

        if not setup:
            add("missing_setup", "缺少读者预期/叙事承诺；补 setup 或 note")
        if state == "meet":
            if not payoff:
                add("missing_payoff", "状态为满足，但没有记录真正兑现承诺的可观察事件")
            if not evidence:
                add("missing_payoff_evidence", "状态为满足，但缺少兑现章节、正文位置或作者确认依据")
        if state == "delay" and not trigger:
            add("waiting_without_trigger", "状态为延迟，但未记录以后允许兑现的触发条件")
        if state in ("dimension", "violate") and not (note or payoff):
            add("missing_state_reason", "换维/违背需要说明替代方案或作者决策")
        if setup and payoff and _norm_text(setup) == _norm_text(payoff):
            add("payoff_restates_setup", "兑现与承诺文字完全相同；需人工确认是否只是复述而非事件")
        audited.append({
            "id": row.get("id"), "chapter_n": row.get("chapter_n"),
            "state": state, "setup": setup, "trigger": trigger,
            "payoff": payoff, "payoff_evidence": evidence,
            "issues": issues, "complete": not issues,
        })
    return {
        "ok": True, "items": audited,
        "stats": {"total": len(audited),
                  "complete": sum(1 for r in audited if r["complete"]),
                  "needs_evidence": sum(1 for r in audited if not r["complete"]),
                  "issues": issue_counts},
        "audit_scope": "只检查记录完整性和显式矛盾；不自动判断语义相近是否等于兑现。",
    }


def material_network(s, pid: str) -> dict:
    items = s.material_list(pid, limit=MAX_SEARCH_LIMIT)
    by_title = {}
    by_id = {}
    for item in items:
        title = (item.get("title") or "").strip()
        if title:
            by_title[title.casefold()] = item
        by_id[item.get("id")] = item

    hashes: Dict[str, List[dict]] = {}
    for item in items:
        h = _material_hash(item.get("content") or "")
        if item.get("content"):
            hashes.setdefault(h, []).append(item)

    nodes = []
    backlinks: Dict[str, List[dict]] = {}
    unresolved: List[dict] = []
    link_count = 0
    for item in items:
        text = (item.get("title") or "") + "\n" + (item.get("content") or "")
        refs = []
        for match in _LINK_RE.finditer(text):
            target_title = match.group(1).strip()
            target = by_title.get(target_title.casefold())
            refs.append({"title": target_title,
                         "id": target.get("id") if target else "",
                         "resolved": bool(target)})
            link_count += 1
            if target:
                backlinks.setdefault(target["id"], []).append({
                    "id": item.get("id"), "title": item.get("title") or "",
                })
            else:
                unresolved.append({"from": item.get("id"),
                                   "from_title": item.get("title") or "",
                                   "target": target_title})
        nodes.append({
            "id": item.get("id"),
            "title": item.get("title") or "",
            "kind": item.get("kind"),
            "source": item.get("source") or "",
            "source_kind": item.get("source_kind") or "",
            "chapter": item.get("chapter"),
            "chars": item.get("chars") or 0,
            "outgoing": refs,
            "backlinks": backlinks.get(item.get("id"), []),
        })
    # 第二遍：把反链填回节点（第一遍时目标可能还没遍历到）。
    for node in nodes:
        node["backlinks"] = backlinks.get(node["id"], [])

    duplicates = []
    for h, group in hashes.items():
        if len(group) > 1:
            duplicates.append({
                "hash": h[:12],
                "count": len(group),
                "ids": [x.get("id") for x in group],
                "titles": [x.get("title") for x in group],
                "suggestion": "保留正文最完整的一条，其余改成引用或删除",
            })
    return {
        "ok": True,
        "nodes": nodes,
        "duplicates": duplicates,
        "unresolved": unresolved,
        "evidence": material_audit(s, pid)["counts"],
        "stats": {
            "materials": len(nodes),
            "links": link_count,
            "linked": sum(1 for n in nodes if n["outgoing"]),
            "backlinked": sum(1 for n in nodes if n["backlinks"]),
            "duplicate_groups": len(duplicates),
            "unresolved": len(unresolved),
            "ima": sum(1 for n in nodes if n["source_kind"] == "ima"),
        },
        "hint": "在素材正文里写 [[另一条素材标题]]，即可生成双向链接；"
                "内容 hash 相同（忽略空白）会进入重复组。",
    }


# ============================================================
# 写作流水线
# ============================================================
def pipeline_status(s, pid: str, chapter_n: Optional[int] = None) -> dict:
    s._require_project(pid)
    if chapter_n is None:
        tree = chapter_tree(s, pid)
        chapter_n = tree["next_chapter"]
    chapter_n = _require_int(chapter_n, "chapter_n",
                             minimum=0, maximum=MAX_CHAPTER_N)
    outline = s.outline_by_chapter(pid, chapter_n)
    chapter = s.chapter_get(pid, chapter_n)
    sections = s.section_list(pid, chapter_n)
    audits = [a for a in s.audit_list(pid, chapter_n)]
    compliance = next((a for a in reversed(audits)
                       if a.get("kind") == "compliance"), None)
    voice = next((a for a in reversed(audits)
                  if a.get("kind") in ("voice", "voice_cross", "ooc")), None)
    materials = [m for m in s.material_list(pid, limit=MAX_SEARCH_LIMIT)
                 if m.get("chapter") == chapter_n]
    chars = s.bible_list(pid, "character")

    def step(step_id, label, done, detail, section, status=""):
        return {"id": step_id, "label": label, "done": bool(done),
                "detail": detail, "section": section,
                "status": status or ("done" if done else "todo")}

    steps = [
        step("outline", "本章细纲", bool(outline),
             "已有章纲" if outline else "还没有第 %d 章章纲" % chapter_n, "outline"),
        step("packet", "组装输入包", bool(outline and (chars or materials)),
             f"人物 {len(chars)} · 本章素材 {len(materials)}", "workbench",
             "ready" if outline else "blocked"),
        step("chapter", "写正文", bool(chapter),
             f"{chapter.get('words', 0)} 字" if chapter else "还没有正文", "studio"),
        step("compliance", "合规预检", bool(compliance),
             (compliance or {}).get("verdict", "未检查"), "workbench",
             "blocked" if (compliance or {}).get("export_blocked") else ""),
        step("voice", "声线 / OOC", bool(voice),
             (voice or {}).get("verdict", "未检查"), "workbench"),
        step("export", "导出 / 申诉包", bool(chapter and compliance
                                             and not compliance.get("export_blocked")),
             "可导出" if chapter and compliance
             and not compliance.get("export_blocked") else "先过合规",
             "studio",
             "blocked" if (compliance or {}).get("export_blocked") else ""),
    ]
    next_step = next((x for x in steps if not x["done"]), None)
    return {
        "ok": True,
        "chapter_n": chapter_n,
        "steps": steps,
        "next_step": next_step["id"] if next_step else "",
        "percent": round(sum(1 for x in steps if x["done"]) / len(steps) * 100),
        "outline_title": (outline or {}).get("title", ""),
        "sections": len(sections),
        "audits": len(audits),
    }


def studio_view(s, pid: str, chapter_n: Optional[int] = None) -> dict:
    tree = chapter_tree(s, pid)
    if chapter_n is None:
        chapter_n = tree["chapters"][-1]["n"] if tree["chapters"] else 1
    chapter_n = _require_int(chapter_n, "chapter_n",
                             minimum=0, maximum=MAX_CHAPTER_N)
    chapter = s.chapter_get(pid, chapter_n)
    chapters = tree["chapters"]
    foreshadow = s.foreshadow_list(pid)
    due = [f for f in foreshadow
           if f.get("status") == "open"
           and f.get("due_chapter") is not None
           and int(f["due_chapter"]) <= chapter_n]
    chars = s.bible_list(pid, "character")
    audits = s.audit_list(pid, chapter_n)
    compliance = next((a for a in reversed(audits)
                       if a.get("kind") == "compliance"), None)
    return {
        "ok": True,
        "chapter_n": chapter_n,
        "chapter": chapter,
        "tree": tree,
        "context": {
            "characters": chars,
            "due_foreshadow": due,
            "compliance": compliance,
            "materials": [m for m in s.material_list(pid, limit=MAX_SEARCH_LIMIT)
                          if m.get("chapter") == chapter_n],
            "expectation": s.expectation_stats(pid),
        },
        "pipeline": pipeline_status(s, pid, chapter_n),
        "structure": structure_check(chapter.get("content") or "")
        if chapter and chapter.get("content") else None,
    }


# ============================================================
# 申诉包
# ============================================================
def _safe_name(name: str) -> str:
    safe = re.sub(r"[^\w\u4e00-\u9fa5-]+", "_", name or "project")
    return safe.strip("_")[:80] or "project"


def _audit_to_markdown(audit: dict) -> str:
    lines = [f"# {audit.get('kind', 'audit')} · {audit.get('verdict', '')}", ""]
    for item in audit.get("findings", []) or []:
        lines.append(f"- [{item.get('level')}] {item.get('quote', '')}")
        lines.append(f"  - {item.get('why', '')}")
        for alt in item.get("alternatives") or []:
            lines.append(f"  - 改法：{alt}")
    return "\n".join(lines)


def appeal_pack(s, pid: str, chapter_n: int) -> dict:
    chapter_n = _require_int(chapter_n, "chapter_n",
                             minimum=1, maximum=MAX_CHAPTER_N)
    project = s.project_get(pid) or {"id": pid, "name": pid}
    versions = s.chapter_versions(pid, chapter_n)
    if not versions:
        raise LongformError(f"第 {chapter_n} 章还没有版本，无法生成申诉包")

    audits = s.audit_list(pid, chapter_n)
    compliance = next((a for a in reversed(audits)
                       if a.get("kind") == "compliance"), None)
    voice = next((a for a in reversed(audits)
                  if a.get("kind") in ("voice", "voice_cross", "ooc")), None)
    declaration = narrative.ai_declaration(
        project.get("name") or pid, pid, s.ledger_stats(pid))

    safe = _safe_name(project.get("name") or pid)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    filename = f"{safe}_第{chapter_n}章_申诉包_{stamp}.zip"
    out_dir = os.path.join(os.path.dirname(os.path.abspath(s.db_path)), "appeals")
    os.makedirs(out_dir, exist_ok=True)
    zip_path = os.path.join(out_dir, filename)

    files: List[str] = []

    def add_text(zf: zipfile.ZipFile, name: str, text: str) -> None:
        zf.writestr(name, text)
        files.append(name)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        meta = {
            "project": project,
            "chapter_n": chapter_n,
            "versions": versions,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "compliance_verdict": (compliance or {}).get("verdict", ""),
            "export_blocked": bool((compliance or {}).get("export_blocked")),
            "note": "本包由叙事工作台本地生成，用于向平台说明创作过程；"
                    "不包含任何规避审核的内容。",
        }
        add_text(zf, "meta.json", json.dumps(meta, ensure_ascii=False, indent=2))

        for version in versions:
            v = int(version["version"])
            got = s.chapter_get(pid, chapter_n, v)
            if not got:
                continue
            add_text(zf, f"chapter-v{v}.txt",
                     f"{got.get('title', '')}\n\n{got.get('content', '')}")
        for a, b in zip(versions, versions[1:]):
            a_v, b_v = int(a["version"]), int(b["version"])
            try:
                diff = s.chapter_diff(pid, chapter_n, a_v, b_v)
            except LongformError:
                continue
            add_text(zf, f"diff-v{a_v}-v{b_v}.txt", diff.get("diff", ""))

        if compliance:
            add_text(zf, "compliance.json",
                     json.dumps(compliance, ensure_ascii=False, indent=2))
            add_text(zf, "compliance.md", _audit_to_markdown(compliance))
        if voice:
            add_text(zf, "voice.json",
                     json.dumps(voice, ensure_ascii=False, indent=2))
            add_text(zf, "voice.md", _audit_to_markdown(voice))
        if audits:
            add_text(zf, "audits.json",
                     json.dumps(audits, ensure_ascii=False, indent=2))
        add_text(zf, "ai-declaration.md", declaration.get("markdown", ""))

        readme = [
            f"# {project.get('name') or pid} · 第 {chapter_n} 章申诉包",
            "",
            "## 包含",
            "",
            "- `meta.json`：章节版本、生成时间、合规结论",
            "- `chapter-vN.txt`：每一次保存的正文（不覆盖旧稿）",
            "- `diff-vA-vB.txt`：相邻版本差异",
            "- `compliance.json` / `compliance.md`：三层合规预检证据",
            "- `voice.json` / `voice.md`：声线 / OOC 检查证据",
            "- `ai-declaration.md`：AI 使用声明草稿",
            "",
            "## 使用建议",
            "",
            "1. 先看 `compliance.md` 的红色项，确认是否还有需要人工改写的句子。",
            "2. 用 `diff-vA-vB.txt` 说明“哪些是人工修订、为什么改”。",
            "3. `ai-declaration.md` 只是草稿，发布前按目标平台最新规则复核。",
            "",
            "本包不构成平台申诉成功承诺；它只提供可核对的创作过程证据。",
        ]
        add_text(zf, "README.md", "\n".join(readme))

    size = os.path.getsize(zip_path)
    return {
        "ok": True,
        "chapter_n": chapter_n,
        "filename": filename,
        "zip_path": zip_path,
        "size": size,
        "files": files,
        "versions": len(versions),
        "compliance_verdict": (compliance or {}).get("verdict", ""),
    }


# ============================================================
# 汇总入口
# ============================================================
def story_graph(s, pid: str, view: str = "all",
                character: Optional[str] = None) -> dict:
    view = (view or "all").strip().lower()
    allowed = ("all", "relations", "foreshadow", "timeline", "materials")
    if view not in allowed:
        raise LongformError(f"view 必须是 {allowed} 之一")
    result = {"ok": True, "view": view}
    if view in ("all", "relations"):
        result["relations"] = _relations_graph(s, pid, character)
    if view in ("all", "foreshadow"):
        result["foreshadow"] = _foreshadow_graph(s, pid)
    if view in ("all", "timeline"):
        result["timeline"] = _timeline_graph(s, pid)
    if view in ("all", "materials"):
        result["materials"] = material_network(s, pid)
    return result
