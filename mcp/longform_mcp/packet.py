"""章节输入包：取数、预算降级、组装与上下文账。

``packet_assemble`` 与 ``context_estimate`` 必须看到**完全同一份数据**，
因此两者共用 ``_packet_prepare``：取数一次、组装多次。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import config_loader
import kb
import profile as profiles
from exporting import project_config
from store import (
    ENTITY_META_KEYS,
    MAX_BRIEF_LEN,
    MAX_CHAPTER_N,
    MAX_TEXT_LEN,
    MAX_TOKEN_BUDGET,
    MIN_TOKEN_BUDGET,
    LongformError,
    Store,
    _as_int,
    _dump,
    _require_int,
    _require_str,
)

# 小节顺序对外是契约（见 schemas/chapter_packet.schema.json），不可调整；
# 但超预算时的**丢弃优先级**与之不同：越靠后的越先被舍弃。
_PACKET_ORDER = ("meta", "brief", "style", "entities",
                 "timeline", "foreshadow", "facts", "materials", "prev",
                 "summaries", "story_state")
_PACKET_LABELS = {
    "meta": "标题与项目元信息",
    "brief": "本章细纲",
    "style": "文风档案",
    "entities": "设定档",
    "timeline": "时间线",
    "foreshadow": "伏笔台账",
    "facts": "待核事实",
    "materials": "本章素材与参考",
    "prev": "上一章结尾摘录",
    "summaries": "前情摘要",
    "story_state": "故事状态",
}
# 可被预算降级丢弃的小节，按先后依次舍弃；素材包最后才丢
_DROPPABLE = ("summaries", "story_state", "facts",
              "timeline", "foreshadow", "prev", "materials")

# 六类状态文件里最影响下一章连贯性的三份，未配置时默认携带
_DEFAULT_STORY_STATE_KEYS = ("character-state", "next-hook", "arc-status")

# 设定条目选择：优先级只影响排序与常驻，不影响渲染格式。
_ENTITY_PRIORITY_RANK = {"always": 0, "high": 1, "normal": 2}
_ENTITY_TRACE_TOKEN_BUDGET = 12_000
_ENTITY_RECENT_WINDOW = 5


def _entity_priority(e: dict) -> str:
    p = e.get("priority")
    return p if p in _ENTITY_PRIORITY_RANK else "normal"


def _entity_body(e: dict) -> dict:
    """条目正文：去掉身份字段与元数据保留键，避免哈希/优先级污染提示词。"""
    return {k: v for k, v in e.items()
            if k not in ("id", "kind", "name")
            and k not in ENTITY_META_KEYS}


def _entity_line(e: dict) -> str:
    """一条设定在输入包里的渲染文本；选择器用它计算 chars。"""
    return (f"- [{e.get('kind', '?')}] {e.get('name')}: "
            f"{_dump(_entity_body(e), 'entity')}")


def _entity_key(e: dict) -> Tuple[Any, Any]:
    return (e.get("kind"), e.get("name"))


def _entity_trace_entry(e: dict, reason: str) -> dict:
    return {
        "id": e.get("id") or f"{e.get('kind')}:{e.get('name')}",
        "kind": e.get("kind"),
        "name": e.get("name"),
        "reason": reason,
        "priority": _entity_priority(e),
        "chars": len(_entity_line(e)),
    }


def _trace_budget(trace: dict, token_budget: Optional[int]) -> dict:
    """按真实预算重算 trace 的 budget 小节（选择器内部只知道默认预算）。"""
    out = dict(trace)
    out["activated"] = list(trace.get("activated") or [])
    out["dropped"] = list(trace.get("dropped") or [])
    out["warnings"] = list(trace.get("warnings") or [])
    tokens = int(token_budget or _ENTITY_TRACE_TOKEN_BUDGET)
    used = sum(int(item.get("chars") or 0) for item in out["activated"])
    ratio = round(min(1.0, used / (tokens * 4)), 4) if tokens else 0.0
    out["budget"] = {"tokens": tokens, "used": used, "ratio": ratio}
    return out


def _select_entities(ents: List[dict], query_text: str, mode: str,
                     chapter_n: int = 0) -> Tuple[List[dict], Optional[str], dict]:
    """按「常驻 → 锚点 → 名称 → 近章 → 全量回退」挑选本章设定。

    返回 ``(picked, note, trace)``；``trace`` 是激活轨迹，供输入包/运行详情
    解释"为什么带了这几条、为什么没带那几条"。排序固定为
    ``(priority_rank, kind, name)``，同一章重复调用结果逐项一致。
    """
    items: List[dict] = []
    seen = set()
    for e in ents or []:
        key = _entity_key(e)
        if key in seen:
            continue
        seen.add(key)
        items.append(e)

    query = (query_text or "").strip()
    norm_mode = str(mode or "keyword").lower()
    warnings: List[str] = []
    if norm_mode == "embedding":
        warnings.append(
            "entity_match=embedding 尚未接入嵌入模型，本次已退化为 keyword 匹配")
        norm_mode = "keyword"

    picked: Dict[Tuple[Any, Any], str] = {}

    if norm_mode == "all":
        for e in items:
            picked[_entity_key(e)] = "all"
    else:
        for e in items:
            if _entity_priority(e) == "always":
                picked[_entity_key(e)] = "always"

        if query:
            for e in items:
                if _entity_key(e) in picked:
                    continue
                anchors = e.get("anchors")
                if not isinstance(anchors, list):
                    continue
                hit = next((a for a in anchors
                            if isinstance(a, str) and a and a in query), None)
                if hit:
                    picked[_entity_key(e)] = f"anchor:{hit}"

        if query:
            for e in items:
                if _entity_key(e) in picked:
                    continue
                name = e.get("name")
                if isinstance(name, str) and name and name in query:
                    picked[_entity_key(e)] = "name"

        if chapter_n and chapter_n > 0:
            threshold = chapter_n - _ENTITY_RECENT_WINDOW
            for e in items:
                if _entity_key(e) in picked:
                    continue
                last = e.get("last_seen_chapter")
                if (isinstance(last, int) and not isinstance(last, bool)
                        and last >= threshold):
                    picked[_entity_key(e)] = "recent"

        if not picked and items:
            for e in items:
                picked[_entity_key(e)] = "fallback_all"
            warnings.append(
                f"细纲未命中任何设定条目，已回退为全量设定（共 {len(items)} 条）")

    entities = sorted(
        (e for e in items if _entity_key(e) in picked),
        key=lambda e: (_ENTITY_PRIORITY_RANK.get(_entity_priority(e), 2),
                       e.get("kind") or "", e.get("name") or ""))

    dropped = [{"id": e.get("id") or f"{e.get('kind')}:{e.get('name')}",
                "kind": e.get("kind"), "name": e.get("name"),
                "reason": "no_match"}
               for e in items if _entity_key(e) not in picked]

    activated = [_entity_trace_entry(e, picked[_entity_key(e)])
                 for e in entities]
    used = sum(item["chars"] for item in activated)
    trace = _trace_budget({
        "activated": activated,
        "dropped": dropped,
        "warnings": warnings,
    }, _ENTITY_TRACE_TOKEN_BUDGET)
    note = warnings[0] if warnings else None
    return entities, note, trace


def _match_entities(ents: List[dict], query_text: str,
                    mode: str) -> Tuple[List[dict], Optional[str]]:
    """兼容旧调用：只返回 ``(picked, note)``，选择逻辑全部交给
    ``_select_entities``。"""
    picked, note, _ = _select_entities(ents, query_text, mode, 0)
    return picked, note


def _truncate_at_line_boundary(text: str, max_chars: int) -> str:
    """兜底裁剪：只在换行处切断，避免把一条设定截成半句。"""
    if len(text) <= max_chars:
        return text
    head = text[:max_chars]
    cut = head.rfind("\n")
    if cut > 0:
        head = head[:cut]
    return head + "\n\n...[因 token_budget 限制已截断，完整内容请用 bible_get / chapter_get 单独查询]..."


def _clip_lines(lines: List[str], max_chars: int) -> str:
    """按行边界裁剪到 ``max_chars`` 以内（摘要专用，不带 token_budget 提示）。"""
    out: List[str] = []
    used = 0
    for line in lines:
        add = len(line) + (1 if out else 0)
        if used + add > max_chars:
            if not out:
                out.append(line[:max_chars])
            else:
                out.append(f"……（另有 {len(lines) - len(out)} 章摘要因 summary_max_chars 省略）")
            break
        out.append(line)
        used += add
    return "\n".join(out)


def _append_notes(text: str, notes: list) -> str:
    notes = [n for n in notes if n]
    if not notes:
        return text
    return text + "\n\n## 组装说明\n- " + "\n- ".join(notes)


def _packet_prepare(s: Store, pid: str, chapter_n: int, brief: str = "",
                    token_budget: Optional[int] = None,
                    include_materials: Optional[bool] = None):
    """取数 + 解析配置，返回 ``(build(lb, tw, drop) -> 各小节文本, ctx)``。

    降级阶梯要在**同一份数据**上反复试算（先压缩长度可变的小节，再丢弃可选
    小节），所以把"取数"与"组装"拆开：取数一次、组装多次，避免为了试算
    把设定档和时间线重复读七八遍。

    ``context_estimate`` 也复用这里——它与 ``packet_assemble`` 必须看到完全
    同一份数据，否则估算出来的账对不上实际喂进去的内容。
    """
    chapter_n = _require_int(chapter_n, "chapter_n", minimum=0, maximum=MAX_CHAPTER_N)
    brief = _require_str(brief, "brief", allow_empty=True, max_len=MAX_BRIEF_LEN)

    proj = s.project_get(pid)
    if proj is None:
        raise LongformError(f"项目不存在：{pid}（请先调用 project_init）")

    # 配置优先级（高 → 低）：
    # 变体 overrides > 项目 config > config/defaults.yaml > 代码内默认值。
    # 变体存在的意义就是"针对某个写作方向的刻意微调"，所以它必须能盖住项目默认值。
    # 反过来，变体文件夹被删掉时不该让开写流程整个断掉——读操作保持宽容，
    # 回退为项目自身配置，并在包尾明确告知调用方。
    raw_cfg = project_config(s, pid)
    pname = s.project_get_profile(pid)
    base_cfg = config_loader.load_defaults()
    cfg = profiles.deep_merge(base_cfg, raw_cfg)
    ov: Dict[str, Any] = {}
    profile_note: Optional[str] = None
    if pname:
        try:
            pcfg = profiles.read(pname)
        except LongformError as e:
            profile_note = f"项目绑定的变体「{pname}」不可读（{e}），本次已回退为项目自身配置"
        else:
            ov = pcfg.get("overrides") or {}
            if ov:
                cfg = profiles.deep_merge(cfg, ov)
                desc = pcfg.get("description") or ""
                profile_note = (f"已应用变体「{pname}」的覆盖配置"
                                + (f"（{desc}）" if desc else ""))

    def _sub(key: str) -> Dict[str, Any]:
        v = cfg.get(key)
        return v if isinstance(v, dict) else {}

    packet_cfg = _sub("packet")
    audit_cfg = _sub("audit")
    chapter_cfg = _sub("chapter")

    lookback = _as_int(packet_cfg.get("timeline_lookback_chapters"), 5,
                       minimum=0, maximum=MAX_CHAPTER_N)
    overdue_after = _as_int(audit_cfg.get("foreshadow_overdue_after"), 8,
                            minimum=0, maximum=MAX_CHAPTER_N)
    tail_words = _as_int(chapter_cfg.get("tail_words"), 300,
                         minimum=0, maximum=MAX_TEXT_LEN)
    include_style = bool(packet_cfg.get("include_style_profile", True))
    include_timeline = bool(packet_cfg.get("include_timeline", True))
    include_fs = bool(packet_cfg.get("include_open_foreshadow", True))
    include_facts = bool(packet_cfg.get("include_facts", True))
    include_summaries = bool(packet_cfg.get("include_summaries", True))
    include_story_state = bool(packet_cfg.get("include_story_state", True))
    summary_lookback = _as_int(packet_cfg.get("summary_lookback_chapters"), 10,
                               minimum=0, maximum=MAX_CHAPTER_N)
    summary_max_chars = _as_int(packet_cfg.get("summary_max_chars"), 1200,
                                minimum=0, maximum=MAX_TEXT_LEN)
    entity_mode = str(packet_cfg.get("entity_match", "keyword")).lower()
    include_material_section = bool(packet_cfg.get("include_materials", True))
    if include_materials is not None:
        include_material_section = bool(include_materials)
    material_limit = _as_int(packet_cfg.get("material_limit"), 4,
                             minimum=0, maximum=20)
    material_clue_limit = _as_int(packet_cfg.get("material_clue_limit"), 2,
                                  minimum=0, maximum=10)
    material_budget_ratio = packet_cfg.get("material_budget_ratio", 0.12)
    try:
        material_budget_ratio = max(0.0, min(0.5, float(material_budget_ratio)))
    except (TypeError, ValueError):
        material_budget_ratio = 0.12
    material_max_chars = _as_int(packet_cfg.get("material_max_chars"), 0,
                                 minimum=0, maximum=20_000)
    material_min_evidence = str(
        packet_cfg.get("material_min_evidence", "E2")).upper()
    if material_min_evidence not in ("E0", "E1", "E2", "E3"):
        material_min_evidence = "E2"
    material_max_queries = _as_int(packet_cfg.get("material_max_queries"), 12,
                                   minimum=1, maximum=20)

    # 故事状态白名单：配置必须是字符串列表且非空，否则回落到三份核心状态。
    # 去重与上限都是为了不让配置把输入包塞爆。
    raw_keys = packet_cfg.get("story_state_keys")
    story_state_keys: List[str] = []
    if isinstance(raw_keys, list) and raw_keys:
        for k in raw_keys:
            if not isinstance(k, str):
                continue
            k = k.strip()
            if k and k not in story_state_keys:
                story_state_keys.append(k)
    if not story_state_keys:
        story_state_keys = list(_DEFAULT_STORY_STATE_KEYS)
    story_state_keys = story_state_keys[:10]

    if token_budget is None:
        token_budget = _as_int(packet_cfg.get("token_budget"), 12_000,
                               minimum=MIN_TOKEN_BUDGET, maximum=MAX_TOKEN_BUDGET)
    else:
        # 显式传参优先于项目配置（旧实现无条件被配置覆盖，参数形同虚设）
        token_budget = max(MIN_TOKEN_BUDGET, min(
            MAX_TOKEN_BUDGET, _require_int(token_budget, "token_budget")))

    max_chars = max(token_budget * 4, 200)

    style = s.style_get(pid) or {}
    # 文风三层：defaults.yaml（基础层）< style 表（项目权威）< 项目/变体显式配置。
    # 基础层不参与「← 覆盖 / ← 新增」标记，避免把默认值说成用户覆盖。
    base_style = base_cfg.get("style") if isinstance(base_cfg.get("style"), dict) else {}
    explicit_style: Dict[str, Any] = {}
    for src in (raw_cfg, ov):
        st = src.get("style") if isinstance(src, dict) else None
        if isinstance(st, dict):
            explicit_style.update(st)
    merged_style = dict(base_style)
    merged_style.update(style)
    merged_style.update(explicit_style)
    style_overridden = sorted(set(explicit_style) & set(style))
    style_added = sorted(set(explicit_style) - set(style))

    all_ents = s.bible_list(pid)
    events = s.timeline_query(pid, 0, max(chapter_n - 1, 0))
    open_fs = s.foreshadow_list(pid, "open")
    pending = s.fact_list(pid, "pending")
    prev = s.chapter_get(pid, chapter_n - 1) if chapter_n >= 1 else None
    if chapter_n >= 2:
        summaries = s.summary_list(
            pid, max(1, chapter_n - summary_lookback), max(0, chapter_n - 1))
    else:
        summaries = []
    states: List[Tuple[str, Any]] = []
    for k in story_state_keys:
        v = s.story_state_get(pid, k)
        if v is None or v == "" or v == {} or v == []:
            continue
        states.append((k, v))

    ents, match_note, entity_trace = _select_entities(
        all_ents, brief, entity_mode, chapter_n)

    material_result: Optional[dict] = None
    material_md = ""
    material_budget_chars = 0
    if include_material_section and material_limit > 0:
        material_budget_chars = max(200, int(max_chars * material_budget_ratio))
        if material_max_chars > 0:
            material_budget_chars = min(material_budget_chars, material_max_chars)
        material_result = kb.material_packet(
            s, pid, chapter_n, brief, max_items=material_limit,
            max_chars=material_budget_chars,
            min_evidence=material_min_evidence,
            clue_limit=material_clue_limit,
            max_queries=material_max_queries)
        material_md = material_result.get("markdown") or ""

    def build(lb: int, tail_w: int, drop: Tuple[str, ...]) -> Dict[str, str]:
        sec: Dict[str, str] = {}

        meta_lines = [
            f"# 章节输入包 · 第 {chapter_n} 章",
            f"项目：{proj.get('name', pid)}　类型：{proj.get('kind', '-')}",
        ]
        if pname:
            meta_lines.append(f"变体：{pname}")
        sec["meta"] = "\n".join(meta_lines)

        if brief:
            sec["brief"] = f"\n## 本章细纲\n{brief}"

        if include_style and merged_style:
            lines = ["\n## 文风档案（必须遵守）"]
            for k in sorted(merged_style):
                v = merged_style[k]
                shown = _dump(v, f"style.{k}") if isinstance(v, (dict, list)) else v
                tag = ""
                if k in explicit_style:
                    tag = "　← 覆盖" if k in style else "　← 新增"
                lines.append(f"- {k}: {shown}{tag}")
            sec["style"] = "\n".join(lines)

        if ents and "entities" not in drop:
            lines = ["\n## 设定档（唯一事实来源，禁止另起炉灶）"]
            for e in ents:
                lines.append(_entity_line(e))
            sec["entities"] = "\n".join(lines)

        if include_timeline and "timeline" not in drop:
            window = [e for e in events if e["chapter"] >= chapter_n - lb]
            if window:
                lines = [f"\n## 时间线（近 {lb} 章）"]
                for e in window:
                    lines.append(f"- 第{e['chapter']}章: {e['title']}")
                sec["timeline"] = "\n".join(lines)

        if include_fs and open_fs and "foreshadow" not in drop:
            lines = ["\n## 伏笔台账"]
            for f in open_fs:
                age = chapter_n - (f["planted_chapter"] or 0)
                overdue = age >= overdue_after or (
                    f.get("due_chapter") is not None and chapter_n > f["due_chapter"])
                flag = " ⚠逾期" if overdue else ""
                due = f"，应于第{f['due_chapter']}章回收" if f.get("due_chapter") else ""
                lines.append(f"- [open{flag}] 埋于第{f['planted_chapter']}章"
                             f"（已 {age} 章）{due}: {f['text']}")
            sec["foreshadow"] = "\n".join(lines)

        if (include_facts and pending and "facts" not in drop):
            lines = ["\n## 待核事实（未核实前不得写入正文）"]
            for f in pending:
                src = f"（来源：{f['source']}）" if f.get("source") else "（无来源）"
                lines.append(f"- {f['claim']}{src}")
            sec["facts"] = "\n".join(lines)

        if (include_material_section and material_md
                and "materials" not in drop):
            sec["materials"] = "\n" + material_md

        if (prev and prev.get("content") and tail_w > 0
                and "prev" not in drop):
            sec["prev"] = (f"\n## 上一章（第{chapter_n - 1}章）结尾摘录"
                           f"（保证衔接）\n...{prev['content'][-tail_w:]}")

        if (include_summaries and summaries and summary_max_chars > 0
                and "summaries" not in drop):
            body = _clip_lines(
                [f"- 第{r['chapter_n']}章：{r['summary']}" for r in summaries],
                summary_max_chars)
            if body:
                first = summaries[0]["chapter_n"]
                last = summaries[-1]["chapter_n"]
                sec["summaries"] = f"\n## 前情摘要（第 {first}–{last} 章）\n{body}"

        if include_story_state and states and "story_state" not in drop:
            lines = ["\n## 故事状态（必须遵守）"]
            for k, v in states:
                shown = v if isinstance(v, str) else _dump(v, f"story_state.{k}")
                lines.append(f"- {k}: {shown}")
            sec["story_state"] = "\n".join(lines)

        return sec

    ctx: Dict[str, Any] = {
        "chapter_n": chapter_n,
        "token_budget": token_budget,
        "max_chars": max_chars,
        "lookback": lookback,
        "tail_words": tail_words,
        "summary_lookback": summary_lookback,
        "summary_max_chars": summary_max_chars,
        "story_state_keys": story_state_keys,
        "profile": pname,
        "notes": ([match_note, profile_note]
                  + list((material_result or {}).get("notes") or [])),
        "entity_count": len(ents),
        "entity_trace": _trace_budget(entity_trace, token_budget),
        "style_overridden": style_overridden,
        "style_added": style_added,
        "include_materials": include_material_section,
        "material_limit": material_limit,
        "material_clue_limit": material_clue_limit,
        "material_min_evidence": material_min_evidence,
        "material_budget_chars": material_budget_chars,
        "material_chars": len(material_md),
        "material_usable_count": (material_result or {}).get("usable_count", 0),
        "material_clue_count": (material_result or {}).get("clue_count", 0),
        "material_queries": (material_result or {}).get("queries", []),
    }
    return build, ctx


def _ladder(lookback: int, tail_words: int) -> List[Tuple[int, int, Tuple[str, ...]]]:
    """预算降级阶梯：时间线回溯只减不增，尾部引用逐步缩短，最后丢可选小节。

    旧实现写死 3→1→0；当配置的回溯窗口小于 3 时，第一步反而会**扩大**内容。
    这里先用 ``min(lookback, 3)`` 保证每一步都不比当前更大，再去掉重复档位。
    """
    rungs: List[Tuple[int, int, Tuple[str, ...]]] = []
    seen = set()

    def add(lb: int, tw: int, drop: Tuple[str, ...]) -> None:
        key = (lb, tw, drop)
        if key not in seen:
            seen.add(key)
            rungs.append(key)

    add(min(lookback, 3), tail_words, ())
    add(min(lookback, 1), tail_words, ())
    add(0, tail_words, ())
    add(0, tail_words // 2, ())
    add(0, 0, ())
    for drop in (("summaries",),
                 ("summaries", "story_state"),
                 ("summaries", "story_state", "facts"),
                 ("summaries", "story_state", "facts", "timeline"),
                 ("summaries", "story_state", "facts", "timeline", "foreshadow"),
                 ("summaries", "story_state", "facts", "timeline",
                  "foreshadow", "prev"),
                 ("summaries", "story_state", "facts", "timeline",
                  "foreshadow", "prev", "materials")):
        add(0, 0, drop)
    return rungs


def packet_text(s: Store, pid: str, chapter_n: int, brief: str = "",
                token_budget: Optional[int] = None,
                include_materials: Optional[bool] = None) -> Tuple[str, list]:
    """组装章节输入包：写第 N 章前必须喂给模型的全部上下文。

    返回 ``(packet 文本, 已包含的小节名列表)``。

    预算控制采用分级降级而非"掐头去尾砍中段"——旧实现会从设定档条目
    正中间切断，恰好毁掉最不该毁的"唯一事实来源"。
    """
    build, ctx = _packet_prepare(s, pid, chapter_n, brief, token_budget,
                                 include_materials)

    def join(sec: Dict[str, str]) -> Tuple[str, list]:
        included = [k for k in _PACKET_ORDER if k in sec]
        return "\n".join(sec[k] for k in included), included

    lookback, tail_words = ctx["lookback"], ctx["tail_words"]
    max_chars = ctx["max_chars"]

    text, included = join(build(lookback, tail_words, ()))

    for lb, tw, drop in _ladder(lookback, tail_words):
        if len(text) <= max_chars:
            break
        text, included = join(build(lb, tw, drop))

    if len(text) > max_chars:
        text = _truncate_at_line_boundary(text, max_chars)

    notes: List[str] = list(ctx["notes"])
    if ctx["style_overridden"] or ctx["style_added"]:
        notes.append(
            f"文风档案有 {len(ctx['style_overridden'])} 项被覆盖、"
            f"{len(ctx['style_added'])} 项为新增"
            f"（行尾标 ← 覆盖 / ← 新增）；数据库里的原始文风未被改动")
    return _append_notes(text, notes), included


def prompt_parts(s: Store, pid: str, chapter_n: int, brief: str = "",
                 token_budget: Optional[int] = None) -> dict:
    """把输入包拆成「稳定前缀」与「动态上下文」两段，供 Run Engine 使用。

    稳定前缀（meta / style / entities）在同一章内基本不变，放在 system 里
    有利于前缀缓存；其余小节随章号与细纲变化，放在 user prompt 里。
    与 ``packet_text`` 共用 ``_packet_prepare``，取的是同一份数据；
    本函数不改写任何既有输出，也不做预算降级。
    """
    build, ctx = _packet_prepare(s, pid, chapter_n, brief, token_budget)
    sec = build(ctx["lookback"], ctx["tail_words"], ())
    stable_keys = ("meta", "style", "entities")
    included = [k for k in _PACKET_ORDER if k in sec]
    stable = "\n".join(sec[k] for k in _PACKET_ORDER
                       if k in stable_keys and k in sec)
    dynamic = "\n".join(sec[k] for k in _PACKET_ORDER
                        if k not in stable_keys and k in sec)
    return {
        "stable": stable,
        "dynamic": dynamic,
        "sections": included,
        "chars": len(stable) + len(dynamic),
        "trace": ctx["entity_trace"],
    }


def _entity_mode_and_budget(s: Store, pid: str) -> Tuple[str, int]:
    """只解析选择策略与预算，供 ``packet_trace`` 独立算轨迹用。"""
    cfg = profiles.deep_merge(config_loader.load_defaults(),
                              project_config(s, pid))
    pname = s.project_get_profile(pid)
    if pname:
        try:
            pcfg = profiles.read(pname)
        except LongformError:
            pass
        else:
            cfg = profiles.deep_merge(cfg, pcfg.get("overrides") or {})
    packet_cfg = cfg.get("packet") if isinstance(cfg.get("packet"), dict) else {}
    mode = str(packet_cfg.get("entity_match", "keyword")).lower()
    budget = _as_int(packet_cfg.get("token_budget"), _ENTITY_TRACE_TOKEN_BUDGET,
                     minimum=MIN_TOKEN_BUDGET, maximum=MAX_TOKEN_BUDGET)
    return mode, budget


def packet_trace(s: Store, pid: str, n: int, brief: str = "") -> dict:
    """独立计算第 N 章的设定激活轨迹，不组装整份输入包。

    返回结构与 ``prompt_parts()["trace"]`` 完全一致：
    ``{activated, dropped, budget, warnings}``。
    """
    chapter_n = _require_int(n, "chapter_n", minimum=0, maximum=MAX_CHAPTER_N)
    brief = _require_str(brief, "brief", allow_empty=True, max_len=MAX_BRIEF_LEN)
    if s.project_get(pid) is None:
        raise LongformError(f"项目不存在：{pid}（请先调用 project_init）")
    mode, budget = _entity_mode_and_budget(s, pid)
    _, _note, trace = _select_entities(s.bible_list(pid), brief, mode, chapter_n)
    return _trace_budget(trace, budget)


def _shrink_suggestion(key: str, chars: int) -> str:
    """给出一条**可直接执行**的省 token 手段（键名与 config_set 完全一致）。"""
    if key == "prev":
        return (f"config_set(key='chapter.tail_words', value=0) "
                f"可省约 {chars // 4} tokens")
    if key == "summaries":
        return (f"config_set(key='packet.include_summaries', value=false) "
                f"可省约 {chars // 4} tokens")
    if key == "story_state":
        return (f"config_set(key='packet.include_story_state', value=false) "
                f"可省约 {chars // 4} tokens")
    if key == "materials":
        return (f"config_set(key='packet.include_materials', value=false) "
                f"可省约 {chars // 4} tokens；也可调小 "
                f"packet.material_limit / packet.material_budget_ratio")
    cfg_key = "packet.include_open_foreshadow" if key == "foreshadow" else f"packet.include_{key}"
    return (f"config_set(key='{cfg_key}', value=false) "
            f"可省约 {chars // 4} tokens")


def context_estimate(s: Store, pid: str, chapter_n: int, brief: str = "",
                     token_budget: Optional[int] = None,
                     include_materials: Optional[bool] = None) -> dict:
    """预估第 N 章输入包的上下文账：先算账，再生成。

    与 ``packet_assemble`` 共用 ``_packet_prepare``，因此这里看到的每一节
    就是待会儿真正会被喂进去的那一节——不存在"估算一套、实跑另一套"。

    只做只读统计，不写任何数据；返回的 ``suggestions`` 给出可执行的省 token
    手段（降预算 / 关小节 / 缩引用），由调用方决定用哪个。
    """
    build, ctx = _packet_prepare(s, pid, chapter_n, brief, token_budget,
                                 include_materials)
    sec = build(ctx["lookback"], ctx["tail_words"], ())
    max_chars = ctx["max_chars"]

    # 约 4 字符 ≈ 1 token，与 packet_assemble 的估算口径保持一致。
    rows = []
    total = 0
    for key in _PACKET_ORDER:
        if key not in sec:
            continue
        chars = len(sec[key])
        total += chars
        rows.append({"section": key, "label": _PACKET_LABELS.get(key, key),
                     "chars": chars, "approx_tokens": chars // 4,
                     "share": round(chars / max_chars, 4) if max_chars else 0.0,
                     "optional": key in _DROPPABLE})
    rows.sort(key=lambda r: -r["chars"])

    over = max(0, total - max_chars)
    suggestions: List[str] = []
    if over:
        lookback = ctx["lookback"]
        shrink = "→".join(str(x) for x in
                          sorted({lookback, min(lookback, 3), min(lookback, 1), 0},
                                 reverse=True))
        suggestions.append(
            f"超出预算约 {over // 4} tokens：packet_assemble 会自动走降级阶梯"
            f"（时间线回溯 {shrink} 章，再依次丢弃 "
            f"{'、'.join(_PACKET_LABELS[k] for k in _DROPPABLE)}）")
        for k in _DROPPABLE:
            if k in sec:
                suggestions.append(_shrink_suggestion(k, len(sec[k])))
        if "summaries" in sec:
            suggestions.append(
                f"已用摘要替代整章：可将 packet.summary_lookback_chapters "
                f"从 {ctx['summary_lookback']} 调小以进一步省 token")
        suggestions.append(
            f"显式传 packet_assemble(token_budget={max(total * 5 // 4 // 4, MIN_TOKEN_BUDGET)}) "
            f"可保留全部小节但放宽预算")
    else:
        suggestions.append(
            f"预算余量约 {(max_chars - total) // 4} tokens，当前无需裁剪")

    return {
        "project": pid, "chapter_n": chapter_n,
        "token_budget": ctx["token_budget"],
        "approx_tokens": total // 4,
        "approx_chars": total,
        "budget_chars": max_chars,
        "over_chars": over,
        "over_tokens": over // 4,
        "fits": over == 0,
        "sections": rows,
        "entity_count": ctx["entity_count"],
        "profile": ctx["profile"],
        "materials": {
            "included": "materials" in sec,
            "usable_count": ctx["material_usable_count"],
            "clue_count": ctx["material_clue_count"],
            "chars": ctx["material_chars"],
            "budget_chars": ctx["material_budget_chars"],
            "min_evidence": ctx["material_min_evidence"],
            "queries": ctx["material_queries"],
        },
        "notes": [n for n in ctx["notes"] if n],
        "suggestions": suggestions,
    }
