"""叙事工作台：合规预检、OOC 语言指纹、角色会诊、妙招与 AI 使用声明。

设计原则：

- **零第三方依赖**：只用标准库，规则文件走 ``config_loader`` 的极简 YAML 解析。
- **确定性**：同样的文本与规则永远得到同样的报告，方便测试与复盘。
- **只提示、不代替审核**：合规层输出红/黄/灰三色风险与改写方向，
  不提供规避平台审核的方法，也不自动改写正文。
- **可配置**：词表、动作词、规模词都在 ``config/compliance_rules.yaml``，
  作者可按目标平台替换，不需要改代码。
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import config_loader

_HERE = os.path.dirname(os.path.abspath(__file__))
RULES_PATH = os.environ.get(
    "LONGFORM_COMPLIANCE_FILE",
    os.path.join(config_loader.CONFIG_DIR, "compliance_rules.yaml"),
)

_MAX_TEXT = 200_000
_SENTENCE_RE = re.compile(r"[。！？!?；;\n]+")
_LEVEL_ORDER = {"red": 0, "yellow": 1, "gray": 2}

_DEFAULT_RULES: Dict[str, Any] = {
    "version": 0,
    "name": "内置兜底规则",
    "word_layer": {"red_words": [], "yellow_words": [], "platform_words": []},
    "semantic_layer": {
        "generalization_terms": [], "dehumanizing_terms": [],
        "polarity_terms": [], "real_anchor_patterns": [],
    },
    "event_layer": {
        "group_subjects": [], "collective_actions": [],
        "scale_markers": [], "organization_markers": [], "min_hits": 2,
    },
    "alternatives": {
        "general": ["把冲突收回到一个具体人物的处境与选择"],
        "dehumanizing": ["改为具体行为描述，不用物种化比喻"],
        "event": ["改为单个角色的遭遇，不写群体行动"],
    },
    "ai_phrases": [],
}


# ============================================================
# 规则读取
# ============================================================
def load_rules(path: Optional[str] = None) -> Dict[str, Any]:
    """读取规则文件；文件缺失或解析失败时退回内置空规则。"""
    raw = config_loader.load_defaults(path or RULES_PATH)
    if not isinstance(raw, dict) or not raw:
        return dict(_DEFAULT_RULES)
    out = dict(_DEFAULT_RULES)
    for key in ("version", "name", "disclaimer"):
        if key in raw:
            out[key] = raw[key]
    for section in ("word_layer", "semantic_layer", "event_layer", "alternatives"):
        value = raw.get(section)
        if isinstance(value, dict):
            out[section] = {**out.get(section, {}), **value}
    if isinstance(raw.get("ai_phrases"), list):
        out["ai_phrases"] = raw["ai_phrases"]
    return out


def _as_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def rules_summary(rules: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    r = rules or load_rules()
    word = r.get("word_layer") or {}
    event = r.get("event_layer") or {}
    return {
        "version": r.get("version", 0),
        "name": r.get("name", ""),
        "disclaimer": r.get("disclaimer", ""),
        "word_counts": {k: len(_as_list(word.get(k))) for k in
                        ("red_words", "yellow_words", "platform_words")},
        "event_terms": {k: len(_as_list(event.get(k))) for k in
                        ("group_subjects", "collective_actions",
                         "scale_markers", "organization_markers")},
        "path": RULES_PATH,
    }


# ============================================================
# 合规预检
# ============================================================
def _split_sentences(text: str) -> List[str]:
    return [part.strip() for part in _SENTENCE_RE.split(text) if part.strip()]


def _clip(text: str, limit: int = 160) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit] + "…"


def _finding(layer: str, level: str, quote: str, why: str,
             alternatives: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "layer": layer,
        "level": level,
        "quote": _clip(quote),
        "why": why,
        "alternatives": (alternatives or [])[:3],
    }


def _dedupe(findings: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for item in sorted(findings, key=lambda x: (_LEVEL_ORDER.get(x["level"], 9),
                                                x["layer"], x["quote"])):
        key = (item["layer"], item["level"], item["quote"], item["why"])
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def compliance_check(text: str, rules: Optional[Dict[str, Any]] = None,
                     max_findings: int = 50) -> Dict[str, Any]:
    """三层合规预检：词层 → 语义层 → 事件三元组。"""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text 不能为空")
    if len(text) > _MAX_TEXT:
        raise ValueError(f"text 超过 {_MAX_TEXT} 字符上限")
    r = rules or load_rules()
    findings: List[Dict[str, Any]] = []
    alts = r.get("alternatives") or {}
    general_alts = _as_list(alts.get("general"))
    dehuman_alts = _as_list(alts.get("dehumanizing"))
    event_alts = _as_list(alts.get("event"))

    word = r.get("word_layer") or {}
    for term in _as_list(word.get("red_words")):
        count = text.count(term)
        if count:
            findings.append(_finding("word", "red", term,
                                     f"命中硬红词「{term}」，共 {count} 处", general_alts))
    for term in _as_list(word.get("yellow_words")):
        count = text.count(term)
        if count:
            findings.append(_finding("word", "yellow", term,
                                     f"命中高风险词「{term}」，共 {count} 处", general_alts))
    for term in _as_list(word.get("platform_words")):
        count = text.count(term)
        if count:
            findings.append(_finding("word", "gray", term,
                                     f"出现平台/站外相关词「{term}」，请确认是否适配目标平台",
                                     general_alts))

    semantic = r.get("semantic_layer") or {}
    for term in _as_list(semantic.get("dehumanizing_terms")):
        if term in text:
            findings.append(_finding("semantic", "red", term,
                                     f"物种化/非人化表达「{term}」，容易触发歧视与暴力风险",
                                     dehuman_alts))
    for term in _as_list(semantic.get("polarity_terms")):
        if term in text:
            findings.append(_finding("semantic", "yellow", term,
                                     f"绝对化处置表达「{term}」，建议弱化为具体行为与后果",
                                     general_alts))
    for term in _as_list(semantic.get("generalization_terms")):
        if term in text:
            findings.append(_finding("semantic", "gray", term,
                                     f"群体概括词「{term}」，注意不要用个体替代整体，也不要贴标签",
                                     general_alts))
    for pattern in _as_list(semantic.get("real_anchor_patterns")):
        try:
            match = re.search(pattern, text)
        except re.error:
            continue
        if match:
            findings.append(_finding("semantic", "gray", match.group(0),
                                     "出现现实时间/地点锚点，建议确认是否需要在现实指向与架空设定之间做选择",
                                     general_alts))

    event = r.get("event_layer") or {}
    subjects = _as_list(event.get("group_subjects"))
    actions = _as_list(event.get("collective_actions"))
    scales = _as_list(event.get("scale_markers"))
    orgs = _as_list(event.get("organization_markers"))
    for sentence in _split_sentences(text):
        subj = [t for t in subjects if t in sentence]
        acts = [t for t in actions if t in sentence]
        sc = [t for t in scales if t in sentence]
        og = [t for t in orgs if t in sentence]
        if acts and subj and (sc or og):
            findings.append(_finding(
                "event", "red", sentence,
                "事件层同时出现群体主体、集体动作与规模/组织线索，构成高风险三元组；"
                "建议改为个体视角，删去组织方式与规模描写",
                event_alts))
        elif acts and subj:
            findings.append(_finding(
                "event", "yellow", sentence,
                "事件层出现群体主体与集体动作，但规模/组织线索不完整；建议复核是否会被读成群体行动",
                event_alts))
        elif subj and sc and not acts:
            findings.append(_finding(
                "event", "gray", sentence,
                "出现群体主体与规模词，建议避免把个体行为写成群体行为",
                event_alts))

    findings = _dedupe(findings, max_findings)
    counts = {"red": 0, "yellow": 0, "gray": 0}
    for item in findings:
        counts[item["level"]] = counts.get(item["level"], 0) + 1
    verdict = "red" if counts["red"] else ("yellow" if counts["yellow"]
                                           else ("gray" if counts["gray"] else "green"))
    return {
        "ok": True,
        "kind": "compliance",
        "verdict": verdict,
        "export_blocked": counts["red"] > 0,
        "counts": counts,
        "findings": findings,
        "checked_chars": len(text),
        "rules_version": r.get("version", 0),
        "rules_name": r.get("name", ""),
        "disclaimer": r.get("disclaimer", ""),
    }


# ============================================================
# OOC / 语言指纹
# ============================================================
_AI_PHRASES = ["不由得", "不由自主", "仿佛", "空气仿佛凝固", "心中一凛",
               "嘴角勾起", "眼底闪过一丝", "不易察觉", "意味深长"]


def _voice_of(profile: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(profile, dict):
        return {}
    voice = profile.get("voice")
    return voice if isinstance(voice, dict) else profile


def voice_check(profile: Dict[str, Any], text: str,
                other_profiles: Optional[List[Dict[str, Any]]] = None,
                max_findings: int = 30) -> Dict[str, Any]:
    """按角色语言指纹检查一段对白/独白是否 OOC。"""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text 不能为空")
    if len(text) > _MAX_TEXT:
        raise ValueError(f"text 超过 {_MAX_TEXT} 字符上限")
    voice = _voice_of(profile or {})
    name = (profile or {}).get("name") or voice.get("name") or "角色"
    banned = _as_list(voice.get("banned_words"))
    tics = _as_list(voice.get("verbal_tics"))
    self_ref = _as_list(voice.get("self_ref"))
    sentence_max = voice.get("sentence_max")
    require_tic = bool(voice.get("require_tic", False))
    findings: List[Dict[str, Any]] = []

    for term in banned:
        if term in text:
            findings.append(_finding("voice", "red", term,
                                     f"{name} 的禁用词「{term}」出现，容易直接崩人设",
                                     [f"删掉「{term}」或换成{name}会用的说法"]))
    if isinstance(sentence_max, int) and sentence_max > 0:
        for sentence in _split_sentences(text):
            if len(sentence) > sentence_max:
                findings.append(_finding("voice", "yellow", sentence,
                                         f"句长 {len(sentence)} 字，超过{name}的句长上限 {sentence_max} 字；"
                                         "长句会稀释口癖与节奏", ["拆成两到三个短句，保留一个信息点"]))
                if len(findings) >= 8:
                    break
    if tics:
        hits = sum(text.count(t) for t in tics)
        if hits == 0 and require_tic:
            findings.append(_finding("voice", "gray", text,
                                     f"没有出现{name}的口癖（{'、'.join(tics[:4])}），声线可能偏平",
                                     ["在情绪转折处补一处口癖，不要每句都加"]))
    for other in other_profiles or []:
        other_voice = _voice_of(other)
        other_name = other.get("name") or other_voice.get("name") or "另一角色"
        for term in _as_list(other_voice.get("verbal_tics")):
            if term and term in text:
                findings.append(_finding("voice", "yellow", term,
                                         f"出现{other_name}的口癖「{term}」，疑似串声",
                                         [f"确认这句话是谁说的；若是{name}，换成{name}的口癖"]))
    for phrase in _AI_PHRASES:
        if phrase in text:
            findings.append(_finding("voice", "gray", phrase,
                                     f"通用 AI 腔短语「{phrase}」，建议换成具体动作或感官细节",
                                     ["用角色自己的动作/感官替代套话"]))
    if self_ref and "我" in text and not any(ref in text for ref in self_ref):
        findings.append(_finding("voice", "gray", "我",
                                 f"{name} 的自称设定为「{'、'.join(self_ref[:3])}」，但文本出现「我」",
                                 ["确认叙述视角；若是对白，换回角色自称"]))

    findings = _dedupe(findings, max_findings)
    counts = {"red": 0, "yellow": 0, "gray": 0}
    for item in findings:
        counts[item["level"]] = counts.get(item["level"], 0) + 1
    verdict = "red" if counts["red"] else ("yellow" if counts["yellow"]
                                           else ("gray" if counts["gray"] else "green"))
    return {
        "ok": True,
        "kind": "voice",
        "character": name,
        "verdict": verdict,
        "counts": counts,
        "findings": findings,
        "checked_chars": len(text),
        "voice_fields": {
            "self_ref": self_ref, "verbal_tics": tics, "banned_words": banned,
            "sentence_max": sentence_max, "register": voice.get("register", ""),
        },
    }


# ============================================================
# 角色会诊（8 角色）
# ============================================================
ROLE_DECK: List[Dict[str, Any]] = [
    {
        "id": "market-editor", "name": "商业编辑", "tagline": "先问读者凭什么点进来",
        "focus": "开篇吸引力、卖点、章节钩子、追读率",
        "questions": ["前 300 字给了读者什么不可替代的信息？",
                      "这一章的章末钩子能不能让人点下一章？",
                      "如果把这段发到平台，标题和首句会不会被划走？"],
        "output": "3 条追读风险 + 1 个改法 + 1 个不要动的卖点",
    },
    {
        "id": "structure-editor", "name": "结构编辑", "tagline": "先看骨架，再改句子",
        "focus": "主线、冲突升级、节奏、信息释放顺序",
        "questions": ["这一章推进了哪条主线？删掉它主线会不会断？",
                      "冲突是在升级还是在重复？",
                      "信息是在正确的顺序里释放的吗？"],
        "output": "1 张节拍表 + 2 处结构问题 + 1 个重排方案",
    },
    {
        "id": "character-editor", "name": "人物编辑", "tagline": "人物要能为难自己",
        "focus": "动机、欲望、缺陷、弧光、关系变化",
        "questions": ["主角在这一章想要什么？代价是什么？",
                      "他/她做了什么只有这个人会做的事？",
                      "这一章结束时，人物关系发生了什么不可逆的变化？"],
        "output": "人物动机一句话 + 2 处扁平化风险 + 1 个加戏点",
    },
    {
        "id": "dialogue-editor", "name": "台词编辑", "tagline": "让每个人说话像自己",
        "focus": "口癖、自称、句长、潜台词、信息差",
        "questions": ["遮住名字，能不能靠说话认出是谁？",
                      "这句台词是在说信息，还是在说关系？",
                      "哪一句可以删掉，让读者自己补？"],
        "output": "3 处串声/直白台词 + 1 段改写示范",
    },
    {
        "id": "continuity-auditor", "name": "连续性审校", "tagline": "先查账，再谈灵感",
        "focus": "设定、时间线、伏笔、道具、伤势、称呼",
        "questions": ["这一章和前面的时间线对得上吗？",
                      "埋下的伏笔有没有登记？该收的收了吗？",
                      "人物的称呼、伤势、位置有没有漂移？"],
        "output": "冲突清单（附出处）+ 回收建议 + 需要补登记的条目",
    },
    {
        "id": "platform-reviewer", "name": "平台审稿", "tagline": "先过线，再谈爆款",
        "focus": "平台规则、群体事件、现实锚点、AI 腔、格式",
        "questions": ["有没有群体标签、集体动作、规模/组织描写？",
                      "有没有现实时间地点锚点，容易被读成影射？",
                      "有没有平台明确禁止的低质、引战、水文特征？"],
        "output": "红/黄/灰风险清单 + 每条的改写方向（不提供规避方法）",
    },
    {
        "id": "sharp-reader", "name": "毒舌读者", "tagline": "读者只会用脚投票",
        "focus": "阅读体验、尴尬点、信息密度、情绪回报",
        "questions": ["哪一段我会快进？",
                      "哪一句让我出戏？",
                      "读完这一章，我得到了什么情绪回报？"],
        "output": "3 个快进点 + 1 个出戏点 + 1 个情绪回报增强点",
    },
    {
        "id": "genre-writer", "name": "类型写手", "tagline": "套路是壳，预期是骨",
        "focus": "类型预期、反套路、爽点/虐点、章末钩子",
        "questions": ["这个类型的老读者在期待什么？",
                      "我是在满足预期、延迟预期，还是反预期？",
                      "这一章的爽点/虐点够不够具体？"],
        "output": "预期账本 3 行 + 1 个反套路方案 + 1 个章末钩子备选",
    },
]


def role_deck() -> Dict[str, Any]:
    return {"ok": True, "count": len(ROLE_DECK), "roles": ROLE_DECK}


def _find_role(role_id: str) -> Dict[str, Any]:
    for role in ROLE_DECK:
        if role["id"] == role_id or role["name"] == role_id:
            return role
    raise ValueError(f"未知角色「{role_id}」；可用：{', '.join(r['id'] for r in ROLE_DECK)}")


PROMPT_CONTRACT: Dict[str, Any] = {
    "evidence": "每个判断都引用待审文本的短语或位置；区分原文事实、项目设定与审稿推断。",
    "missing_evidence": "上下文不足时写“需作者确认”，列出所缺材料，不补编人物、世界观或来源。",
    "stop_when": "没有可定位证据，或建议会越出当前任务时，停止在“待补材料/待作者决策”，不硬给结论。",
    "self_check": "交付前逐项检查：问题是否全覆盖、证据是否可定位、是否新增设定、是否越权重写、是否保留未决项。",
}
PROMPT_CONTRACT_TEXT = (
    "【证据契约】\n"
    f"1. {PROMPT_CONTRACT['evidence']}\n"
    f"2. {PROMPT_CONTRACT['missing_evidence']}\n"
    f"3. {PROMPT_CONTRACT['stop_when']}\n"
    f"【完成前自检】{PROMPT_CONTRACT['self_check']}\n"
)


def build_role_prompt(role_id: str, text: str, context: str = "") -> Dict[str, Any]:
    """把角色会诊变成一段可直接喂给任意模型的结构化提示词。"""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text 不能为空")
    role = _find_role(role_id)
    system = (f"你是叙事工作台的「{role['name']}」。{role['tagline']}。"
              f"你的审查重点是：{role['focus']}。"
              "只做审稿，不重写整章；先给判断，再给证据，最后给最小改法。"
              "不要编造设定；不确定的地方标注“需作者确认”。")
    prompt = (
        f"【角色】{role['name']}\n"
        f"【审查重点】{role['focus']}\n"
        f"【请按顺序回答】\n"
        + "\n".join(f"{i + 1}. {q}" for i, q in enumerate(role["questions"]))
        + f"\n\n【输出格式】{role['output']}\n"
        + "逐条使用：证据位置｜判断｜最小改法｜置信/待确认。\n"
        + PROMPT_CONTRACT_TEXT
        + (f"\n【项目上下文】\n{context.strip()}\n" if context.strip() else "")
        + f"\n【待审文本】\n{text.strip()}\n"
    )
    return {"ok": True, "role": role, "system": system, "prompt": prompt,
            "contract": PROMPT_CONTRACT}


# ============================================================
# 妙招工具箱（12 招）
# ============================================================
TRICKS: List[Dict[str, Any]] = [
    {"id": "three-question-open", "name": "三问开局", "use": "开篇没有抓手时",
     "steps": ["这一章谁最想要什么？", "谁/什么在阻止？", "失败的最坏后果是什么？"],
     "output": "用三句话写出开篇，第一句只写动作或异常"},
    {"id": "hook-grid", "name": "钩子九宫格", "use": "章末留不住人时",
     "steps": ["信息钩：抛一个未解事实", "危险钩：让角色立刻面临损失", "关系钩：让关系发生不可逆变化"],
     "output": "同一章末写 3 个钩子备选，选一个最不像套路的"},
    {"id": "conflict-ladder", "name": "冲突升温阶梯", "use": "剧情原地打转时",
     "steps": ["把冲突从“意见不合”升到“利益受损”", "再升到“身份/关系受威胁”", "最后升到“不可逆代价”"],
     "output": "画 4 级阶梯，标出当前章停在第几级"},
    {"id": "foreshadow-triptych", "name": "伏笔三连", "use": "伏笔埋得生硬时",
     "steps": ["第一次只给异常，不给解释", "第二次让角色误判异常", "第三次让异常成为代价"],
     "output": "为一条伏笔写三个落点，标出回收章"},
    {"id": "twist-three-proof", "name": "反转三证", "use": "反转被读者提前猜到时",
     "steps": ["证据一：读者看得见但没在意", "证据二：角色看见但理解错", "证据三：只有重读才发现"],
     "output": "列出三条证据在正文中的位置"},
    {"id": "subtext-dialogue", "name": "对话潜台词", "use": "对话太直白时",
     "steps": ["让角色不回答被问的问题", "让角色说一件不相关的小事", "让动作替台词说话"],
     "output": "改一段 6 句以内的对话，删掉所有解释性台词"},
    {"id": "five-senses-anchor", "name": "场景五感锚点", "use": "场景像说明文时",
     "steps": ["选一个感官做主锚", "再选一个反常态的细节", "删掉与冲突无关的布景"],
     "output": "写 3 句场景，至少一句让读者产生身体感"},
    {"id": "arc-four-beats", "name": "角色弧光四拍", "use": "人物没有变化时",
     "steps": ["旧信念", "被事实打脸", "做出代价选择", "新信念但留下裂缝"],
     "output": "用四拍写人物小传，标出哪一拍在本章发生"},
    {"id": "expectation-ledger", "name": "类型预期账本", "use": "写类型文怕套路时",
     "steps": ["列出读者预期 3 条", "标出满足/延迟/反转", "每 3 章至少反一次预期"],
     "output": "做一张 3 行账本，写清本章策略"},
    {"id": "de-ai-three-cuts", "name": "去 AI 腔三删", "use": "文本像模型写的时",
     "steps": ["删掉解释情绪的副词", "删掉“仿佛/不由得”类套话", "把抽象判断换成一个动作"],
     "output": "标出 3 处可删，给 1 段改写"},
    {"id": "chapter-end-three", "name": "章末三选一", "use": "每章结尾都一个样时",
     "steps": ["危机型：立刻要付出代价", "信息型：抛出新事实", "关系型：关系不可逆"],
     "output": "写 3 个不同类型结尾，选一个"},
    {"id": "emotion-curve", "name": "读者情绪曲线", "use": "整章平的时候",
     "steps": ["标出读者情绪高点/低点", "高点前必须有压缩", "低点后 300 字内给一个小回报"],
     "output": "画一条 5 段情绪曲线，标出本章位置"},
]


def trick_deck() -> Dict[str, Any]:
    return {"ok": True, "count": len(TRICKS), "tricks": TRICKS}


def _find_trick(trick_id: str) -> Dict[str, Any]:
    for trick in TRICKS:
        if trick["id"] == trick_id or trick["name"] == trick_id:
            return trick
    raise ValueError(f"未知妙招「{trick_id}」；可用：{', '.join(t['id'] for t in TRICKS)}")


def build_trick_prompt(trick_id: str, text: str, context: str = "") -> Dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text 不能为空")
    trick = _find_trick(trick_id)
    system = ("你是叙事工作台的“妙招教练”。你的任务不是替作者写完，"
              "而是把一招拆成可执行的步骤，指出在这段文本里怎么用、用在哪、不要用在哪。")
    prompt = (
        f"【妙招】{trick['name']}（适用：{trick['use']}）\n"
        f"【步骤】\n" + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(trick["steps"]))
        + f"\n\n【产出要求】{trick['output']}\n"
        + "【约束】只改与这一招相关的部分；不要重写整章；不要新增设定。\n"
        + PROMPT_CONTRACT_TEXT
        + (f"\n【项目上下文】\n{context.strip()}\n" if context.strip() else "")
        + f"\n【待处理文本】\n{text.strip()}\n"
    )
    return {"ok": True, "trick": trick, "system": system, "prompt": prompt,
            "contract": PROMPT_CONTRACT}


def build_review_chain_prompt(text: str, context: str = "",
                              role: str = "structure-editor") -> Dict[str, Any]:
    """生成可逐段复制、可检查交接物的四步审稿链；不调用任何外部模型。"""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text 不能为空")
    selected = _find_role(role)
    source = text.strip()
    project_context = context.strip() or "（未提供；缺失处标“需作者确认”）"
    questions = "\n".join(f"- {q}" for q in selected["questions"])
    definitions = [
        {
            "id": "evidence", "name": "1. 提取证据",
            "goal": "只提取原文和项目上下文中可定位的事实，不做审稿结论。",
            "output": "evidence[]（quote/location/observation）+ unknowns[]；没有证据就返回空数组。",
            "stop": "无法定位原文或上下文时停止，把缺口写入 unknowns，不猜测。",
            "handoff": "step1.evidence、step1.unknowns",
            "body": f"【项目上下文】\n{project_context}\n【原文】\n{source}",
        },
        {
            "id": "review", "name": f"2. {selected['name']}审稿",
            "goal": f"只完成{selected['name']}判断：{selected['focus']}。",
            "output": "findings[]（evidence_refs/judgment/severity/minimal_fix/confidence）；无依据的问题不列。",
            "stop": "findings 没有 evidence_refs 时不得给确定性结论，转入 remaining_questions。",
            "handoff": "step2.findings、step2.remaining_questions",
            "body": f"【角色问题】\n{questions}\n【上一步交接】\n{{{{step1.evidence}}}}\n{{{{step1.unknowns}}}}",
        },
        {
            "id": "refine", "name": "3. 最小修订建议",
            "goal": "把已确认 findings 转成局部补丁建议，不重写整章。",
            "output": "patches[]（finding_ref/target_quote/replacement/reason）+ deferred[]；无依据的补丁放 deferred。",
            "stop": "target_quote 不在 evidence 中，或修改需要新设定时停止该补丁并请求作者确认。",
            "handoff": "step3.patches、step3.deferred",
            "body": "【上一步交接】\n{{step2.findings}}\n{{step2.remaining_questions}}\n"
                    "只处理 findings；保留作者措辞与叙事视角，不新增人物、能力、关系或世界观。",
        },
        {
            "id": "selfcheck", "name": "4. 完成前自检",
            "goal": "检查覆盖、证据和越权修改，决定是否交给作者终审。",
            "output": "covered_findings[] + unsupported_changes[] + remaining_questions[] + ready_for_author(bool)。",
            "stop": "仍有 unsupported_changes 或缺证据判断时 ready_for_author=false，不宣称已完成。",
            "handoff": "作者终审清单",
            "body": "【待核交接】\n{{step2.findings}}\n{{step3.patches}}\n{{step3.deferred}}\n"
                    f"逐项核对：{PROMPT_CONTRACT['self_check']}",
        },
    ]
    steps = []
    for item in definitions:
        step = dict(item)
        step["prompt"] = (
            f"【本步唯一目标】{item['goal']}\n"
            f"{item['body']}\n"
            f"【输出契约】{item['output']}\n"
            f"【缺证据与停止条件】{item['stop']}\n"
            f"【交接字段】{item['handoff']}\n"
            "不要执行后续步骤，不要调用未提供的资料。"
        )
        steps.append(step)
    combined = "\n\n".join(
        f"## {step['name']}\n{step['prompt']}" for step in steps)
    return {"ok": True, "role": selected, "steps": steps,
            "prompt": combined, "contract": PROMPT_CONTRACT,
            "external_model_called": False,
            "note": "这是提示词链，不是自动审稿结果；每步输出需经作者或下一链节检查。"}


# ============================================================
# AI 使用声明
# ============================================================
def ai_declaration(project_name: str, project_id: str, stats: Optional[Dict[str, Any]] = None,
                   generated_at: Optional[str] = None) -> Dict[str, Any]:
    """生成一份可放进作品简介/投稿邮件的 AI 使用声明草稿。"""
    stats = stats or {}
    now = generated_at or datetime.now().strftime("%Y-%m-%d")
    chapters = stats.get("chapters", 0)
    words = stats.get("total_words", 0)
    materials = stats.get("materials", 0)
    audits = stats.get("audits", 0)
    body = f"""# AI 使用声明

作品：{project_name}（{project_id}）
生成日期：{now}

## 一、AI 参与的环节

本项目在以下环节使用 AI 辅助：大纲头脑风暴、资料整理与摘要、章节草稿扩写、一致性自检、合规风险提示。
AI 输出均经过作者人工筛选、改写与终审，不直接作为最终稿件发布。

## 二、人工控制

- 作者负责主线、人物、世界观与最终定稿。
- 每一章保存独立版本，修改过程可追溯；AI 生成的段落不覆盖人工稿。
- 对 AI 给出的设定、事实与引文，作者按素材来源逐条核对后再写入正文。

## 三、数据与素材

- 项目当前登记：{chapters} 章、约 {words} 字、{materials} 条素材、{audits} 条审校记录。
- 素材来源在素材库中保留出处；未核实的引文不进入正文。
- 不将未授权的第三方作品、隐私数据或付费内容作为 AI 输入。

## 四、标识与发布

- 发布前按目标平台要求填写 AI 辅助创作说明；如平台要求显式标识，按平台规则执行。
- 本声明为项目内模板，不构成法律意见；正式发布前请按平台最新规则复核。

## 五、作者声明

本人对作品内容负责，AI 仅作为辅助工具使用。
"""
    return {"ok": True, "kind": "ai_declaration", "markdown": body,
            "generated_at": now, "stats": stats}
