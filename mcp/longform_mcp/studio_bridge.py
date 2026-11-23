"""AI Studio 桥接：把网页里设置的文风 / 写作要求 / 工具组导出为可粘贴的提示词。

适用场景：**不配置任何 LLM API**，由 AI Studio 对话框主控写作。
本项目只负责存数据、出提示词、收设置补丁：

    build_pack    → 生成结构化设置包（style / config / studio / tools）
    pack_prompt   → 把设置包渲染成 Markdown 提示词
    build_prompt  → 直接取项目数据 + 章纲 + 输入包生成写作提示
    export_bundle → 设置包 + JSON 文本 + 提示词一次给全
    import_pack   → 把 AI Studio 输出的设置补丁写回项目

安全约束：任何返回与提示词都**不含密钥字段**；导入时遇到密钥键
一律报错而非静默丢弃——静默丢弃会让调用方误以为密钥已生效。
"""

from __future__ import annotations

import datetime
import json
from typing import Any, Dict, List, Optional, Tuple

import tool_specs
from store import MAX_KEY_LEN, LongformError, Store, _require_str

PACK_FORMAT = "longform-studio-pack"
PACK_VERSION = 1

# 单次导入上限：设置补丁不该是一本小说，超过这个量级多半是传错了文件。
MAX_PACK_BYTES = 512 * 1024

# 组名 → 匹配规则。每个工具只归入**第一个**匹配到的组，
# 因此顺序即是优先级；未命中任何规则的归入「其他」。
TOOL_GROUPS: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "项目与设定": {"prefixes": ("project_", "entity_", "event_", "foreshadow_",
                             "fact_", "style_", "config_")},
    "大纲": {"prefixes": ("outline_",)},
    "章节": {"prefixes": ("chapter_", "section_")},
    "素材与知识库": {"prefixes": ("material_", "kb_", "ima_")},
    "审校与质量": {"prefixes": ("audit_", "lint_", "word_stats", "revision_",
                             "narrative_", "insights_", "prose_patch")},
    "自动化": {"prefixes": ("run_", "usage_")},
    "导出与提示词": {
        "prefixes": (),
        "names": ("export", "workspace_export", "appeal_pack", "role_prompt",
                  "trick_prompt", "review_chain_prompt"),
    },
    "设置与变体": {"prefixes": ("settings_", "profile_")},
}

SUGGESTED_WORKFLOW = [
    "config_get/style_get",
    "outline_next/outline_brief",
    "packet_assemble",
    "写正文",
    "chapter_save(draft)",
    "lint_check",
    "chapter_save(approved)",
    "summary_set/story_state_set",
    "usage_report",
]

TASKS = ("settings", "bootstrap", "outline", "chapter", "quality", "style",
         "review")

# 明确的密钥键名 + 后缀。刻意**不含** token：token_budget /
# max_output_tokens / tokens 都是正常配置，误删会让预算与账本失真。
_SECRET_KEYS = {"api_key", "apikey", "password", "secret", "authorization"}
_SECRET_SUFFIXES = ("_api_key", "_secret", "_password")


# ---------------------------------------------------------------- 密钥防护
def _is_secret_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    low = key.lower()
    return low in _SECRET_KEYS or low.endswith(_SECRET_SUFFIXES)


def _scrub(value: Any) -> Tuple[Any, int]:
    """递归剔除密钥字段，返回 (清洗后的值, 剔除数量)。"""
    removed = 0

    def walk(node: Any) -> Any:
        nonlocal removed
        if isinstance(node, dict):
            out: Dict[Any, Any] = {}
            for key, val in node.items():
                if _is_secret_key(key):
                    removed += 1
                    continue
                out[key] = walk(val)
            return out
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node

    return walk(value), removed


def _find_secret(value: Any, path: str = "") -> Optional[str]:
    """返回第一个密钥字段的路径；用于导入时拒绝而非静默丢弃。"""
    if isinstance(value, dict):
        for key, val in value.items():
            child = f"{path}.{key}" if path else str(key)
            if _is_secret_key(key):
                return child
            hit = _find_secret(val, child)
            if hit:
                return hit
    elif isinstance(value, list):
        for i, item in enumerate(value):
            hit = _find_secret(item, f"{path}[{i}]")
            if hit:
                return hit
    return None


# ---------------------------------------------------------------- 工具分组
def _classify(name: str) -> str:
    for group, rule in TOOL_GROUPS.items():
        if name in rule.get("names", ()):
            return group
        if any(name.startswith(p) for p in rule.get("prefixes", ())):
            return group
    return "其他"


def _tool_section(requested: List[str], warnings: List[str]) -> dict:
    known = list(TOOL_GROUPS)
    if requested:
        enabled: List[str] = []
        for group in requested:
            if group in TOOL_GROUPS:
                if group not in enabled:
                    enabled.append(group)
            else:
                warnings.append(f"未知工具组：{group}（已忽略）")
    else:
        # 空列表 = 全部（含未分组的「其他」），保证不遗漏任何工具。
        enabled = known + ["其他"]

    buckets: Dict[str, List[dict]] = {group: [] for group in enabled}
    for tool in tool_specs.TOOLS:
        group = _classify(tool["name"])
        if group in buckets:
            buckets[group].append({"name": tool["name"],
                                   "description": tool.get("description", "")})
    return {
        "groups": {group: buckets[group] for group in enabled},
        "enabled_groups": enabled,
        "workflow": list(SUGGESTED_WORKFLOW),
    }


# ---------------------------------------------------------------- 设置包
def _text(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _studio_section(cfg: dict) -> dict:
    raw = cfg.get("studio") if isinstance(cfg, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    groups_raw = raw.get("tool_groups")
    groups: List[str] = []
    if isinstance(groups_raw, list):
        for item in groups_raw:
            if isinstance(item, str) and item.strip():
                groups.append(item.strip())
    mode = _text(raw.get("mode")).strip() or "ai-studio"
    return {
        "mode": mode,
        "requirements": _text(raw.get("requirements")),
        "tool_groups": groups,
        "notes": _text(raw.get("notes")),
    }


def build_pack(s: Store, pid: str, *, include_tools: bool = True) -> dict:
    """生成 AI Studio 设置包。project 只带 id/name/kind，绝不泄露 config。"""
    project = s.project_get(pid)
    if project is None:
        raise LongformError(f"项目不存在：{pid}")

    warnings: List[str] = []
    style, removed_style = _scrub(s.style_get(pid) or {})
    config, removed_config = _scrub(s.config_get(pid) or {})
    removed = removed_style + removed_config
    if removed:
        warnings.append(f"已排除 {removed} 个密钥字段")

    studio = _studio_section(config)
    pack = {
        "format": PACK_FORMAT,
        "version": PACK_VERSION,
        "generated_at": datetime.datetime.now().astimezone().isoformat(
            timespec="seconds"),
        "mode": "ai-studio",
        "project": {"id": project.get("id"), "name": project.get("name"),
                    "kind": project.get("kind")},
        "style": style,
        "config": config,
        "studio": studio,
        "warnings": warnings,
    }
    if include_tools:
        pack["tools"] = _tool_section(studio["tool_groups"], warnings)
    return pack


# ---------------------------------------------------------------- 提示词
def _require_task(task: Any) -> str:
    if not isinstance(task, str) or task not in TASKS:
        raise LongformError(
            f"参数 `task` 取值非法：{task!r}，允许的值为 {list(TASKS)}")
    return task


def _json_block(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(value)


def _design_schema() -> dict:
    """立项 JSON 结构与自驾协议共用同一份契约，避免两处漂移。"""
    import studio_autopilot
    return studio_autopilot.DESIGN_SCHEMA


def _outline_schema() -> dict:
    import studio_autopilot
    return studio_autopilot.OUTLINE_SCHEMA


def _quality_schema() -> dict:
    import quality_loop
    return {
        "scores": {d["key"]: 0 for d in quality_loop.DEFAULT_DIMENSIONS},
        "violations": [{"rule": "", "evidence": "", "suggestion": ""}],
        "summary": "",
    }


def _style_schema() -> dict:
    import style_distill
    return {
        "dimensions": {key: "" for key in style_distill.NINE_DIMENSIONS},
        "writing_genes": [{"name": "", "description": "", "example": ""}],
        "thinking_genes": [{"name": "", "description": "", "example": ""}],
        "banned": [""],
        "style_prompt": "",
        "limitations": "",
    }


def _common_header(pack: dict, task_label: str) -> List[str]:
    return [
        f"# AI Studio 主控 · 长篇小说写作包（{task_label}）",
        "",
        "本项目**不配置直接模型 API**，由 AI Studio 对话主控。",
        "项目数据（文风 / 设定 / 大纲 / 章节）一律通过 MCP/CLI 工具读写；"
        "以下信息均来自工具真实返回。",
        "不得编造工具返回结果；需要数据时请调用对应工具。",
        "设置来源：项目 style / config；工具清单仅包含启用的工具组。",
        "",
    ]


def _tools_lines(pack: dict) -> List[str]:
    tools = pack.get("tools") or {}
    groups = tools.get("groups") or {}
    enabled = tools.get("enabled_groups") or []
    lines = [
        "## 启用工具组与工作流",
        "",
        "- 启用工具组：" + ("、".join(enabled) if enabled else "（未提供）"),
        "- 建议工作流：" + " → ".join(tools.get("workflow") or SUGGESTED_WORKFLOW),
        "",
    ]
    for group, items in groups.items():
        names = ", ".join(item.get("name", "") for item in items)
        lines.append(f"- {group}（{len(items)}）：{names}")
    lines.append("")
    return lines


def pack_prompt(pack: dict, task: str = "settings", *,
                context: str = "") -> str:
    """把设置包渲染成可粘贴到 AI Studio 的 Markdown 提示词。"""
    task = _require_task(task)
    project = pack.get("project") or {}
    studio = pack.get("studio") or {}
    config = pack.get("config") or {}
    lines: List[str] = []

    if task == "settings":
        lines.extend(_common_header(pack, "设置"))
        lines.extend([
            "## 项目",
            "",
            f"- id：{project.get('id', '-')}",
            f"- 名称：{project.get('name', '-')}",
            f"- 类型：{project.get('kind', '-')}",
            f"- 当前模式：{studio.get('mode', 'ai-studio')}",
            "",
            "## 文风档案",
            "",
            "```json",
            _json_block(pack.get("style") or {}),
            "```",
            "",
            "## 写作要求",
            "",
            studio.get("requirements") or "（未填写）",
            "",
            "## 项目配置摘要",
            "",
            "```json",
            _json_block(config),
            "```",
            "",
        ])
        if studio.get("notes"):
            lines.extend(["## 补充说明", "", studio["notes"], ""])
        lines.extend(_tools_lines(pack))
        lines.extend([
            "## 设置修改输出格式",
            "",
            "如需修改设置，只输出一个 JSON 补丁，格式为：",
            "",
            '```json',
            '{"style": {...}, "config": {...}, "studio": {...}}',
            '```',
            "",
            "不要输出正文。",
        ])
        return "\n".join(lines)

    if task == "chapter":
        n = pack.get("chapter_n")
        target = None
        chapter_cfg = config.get("chapter") if isinstance(config, dict) else None
        if isinstance(chapter_cfg, dict):
            target = chapter_cfg.get("target_words")
        lines.extend(_common_header(pack, "写正文"))
        lines.extend([
            "## 写作任务",
            "",
            f"请撰写第 {n if n is not None else 'N'} 章的正文。",
            f"目标字数：{target if target else '按章纲与文风自然长度'}。",
            "",
            "- 只输出正文，不要输出解释、大纲复述或元信息。",
            "- 写完后调用/建议调用 chapter_save(status=\"draft\") 保存草稿，"
            "再调用 lint_check 做确定性检查。",
            "- 定稿前若 lint_check 有 block 级问题，先修订而不是直接 approved。",
            "",
            "## 写作上下文",
            "",
            context or "（未提供上下文；请先调用 packet_assemble 获取输入包）",
        ])
        return "\n".join(lines)

    if task == "bootstrap":
        lines.extend(_common_header(pack, "立项"))
        lines.extend([
            "## 立项任务",
            "",
            "请为这本书产出可落库的立项设计。只输出一个 JSON 对象，"
            "不要解释，不要 Markdown 代码块。",
            "",
            "```json",
            _json_block(_design_schema()),
            "```",
            "",
            "要求：人物至少 1 个；世界条目至少 1 个；卷纲区间不得重叠；"
            "first_chapters 给出前 3 章章纲；不确定的数字可写 0。",
            "",
            "## 项目",
            "",
            f"- id：{project.get('id', '-')}",
            f"- 名称：{project.get('name', '-')}",
            f"- 类型：{project.get('kind', '-')}",
            "",
            "## 现有文风档案",
            "",
            "```json",
            _json_block(pack.get("style") or {}),
            "```",
            "",
            "## 写作要求",
            "",
            studio.get("requirements") or "（未填写）",
            "",
        ])
        if studio.get("notes"):
            lines.extend(["## 补充说明", "", studio["notes"], ""])
        return "\n".join(lines)

    if task == "outline":
        lines.extend(_common_header(pack, "大纲"))
        lines.extend([
            "## 大纲任务",
            "",
            "请补齐总纲 / 卷纲 / 章纲。只输出一个 JSON 对象，"
            "不要解释，不要 Markdown 代码块。",
            "",
            "```json",
            _json_block(_outline_schema()),
            "```",
            "",
            "要求：卷纲区间不得重叠；每卷给出起止章与卷内推进；"
            "如能确定前几章，请一并给出 first_chapters。",
            "",
            "## 项目",
            "",
            f"- id：{project.get('id', '-')}",
            f"- 名称：{project.get('name', '-')}",
            f"- 类型：{project.get('kind', '-')}",
            "",
            "## 写作要求",
            "",
            studio.get("requirements") or "（未填写）",
            "",
        ])
        lines.extend(_tools_lines(pack))
        return "\n".join(lines)

    if task == "quality":
        lines.extend(_common_header(pack, "质量评分"))
        lines.extend([
            "## 质量评分任务",
            "",
            "请只做可核查的评分与问题清单。只输出一个 JSON 对象，"
            "不要解释，不要 Markdown 代码块。",
            "",
            "```json",
            _json_block(_quality_schema()),
            "```",
            "",
            "- 每个维度 0–10 分，10 为最佳，不要打感情分。",
            "- violations 每条必须含 rule / evidence / suggestion，"
            "evidence 引用原文到具体句子。",
            "",
            "## 文风档案",
            "",
            "```json",
            _json_block(pack.get("style") or {}),
            "```",
            "",
            "## 待评正文",
            "",
            context or "（未提供正文；请先传入章节文本或 packet）",
        ])
        return "\n".join(lines)

    if task == "style":
        lines.extend(_common_header(pack, "文风画像"))
        lines.extend([
            "## 文风蒸馏任务",
            "",
            "请按九个维度提炼文体特征。只输出一个 JSON 对象，"
            "不要解释，不要 Markdown 代码块。",
            "",
            "```json",
            _json_block(_style_schema()),
            "```",
            "",
            "- 九个维度缺一不可，值必须是非空字符串。",
            "- writing_genes / thinking_genes 各给可复用条目；"
            "banned 列出需要避开的表达。",
            "",
            "## 现有文风档案",
            "",
            "```json",
            _json_block(pack.get("style") or {}),
            "```",
            "",
            "## 样本",
            "",
            context or "（未提供样本；请先传入至少 3 篇正文）",
        ])
        return "\n".join(lines)

    # review
    lines.extend(_common_header(pack, "审校"))
    lines.extend([
        "## 审校任务",
        "",
        "请基于 lint_check 的 findings 与项目文风 / 事实 / 伏笔账做审校。",
        "输出问题清单，每条包含三个字段：",
        "",
        "- rule：规则或维度（如 banned_word / fact / foreshadow / style）",
        "- evidence：原文证据（引用到具体句子，不要概括）",
        "- suggestion：可执行的修改建议",
        "",
        "**不要改写正文**，不要输出全文；只列问题。",
        "",
        "## 审校依据",
        "",
        "### 文风档案",
        "```json",
        _json_block(pack.get("style") or {}),
        "```",
        "",
        "### 写作要求",
        studio.get("requirements") or "（未填写）",
        "",
        context or "（未提供正文上下文；请先传入待审章节文本或 packet）",
    ])
    return "\n".join(lines)


def build_prompt(s: Store, pid: str, task: str = "settings", *,
                 chapter_n: Optional[int] = None) -> dict:
    """按任务生成提示词；chapter 任务必须给 chapter_n。"""
    task = _require_task(task)
    pack = build_pack(s, pid)
    context = ""
    if task == "chapter":
        if chapter_n is None:
            raise LongformError("task='chapter' 必须提供 chapter_n")
        import packet as packet_mod
        import pipeline

        brief_info = pipeline.outline_brief(s, pid, chapter_n)
        brief = brief_info.get("brief") or ""
        text, _included = packet_mod.packet_text(s, pid, chapter_n, brief)
        context = (f"## 第 {chapter_n} 章章纲\n\n{brief}\n\n"
                   f"## 第 {chapter_n} 章输入包\n\n{text}")
        pack = dict(pack, chapter_n=chapter_n)
    prompt = pack_prompt(pack, task, context=context)
    return {"ok": True, "project": pid, "task": task, "prompt": prompt}


def export_bundle(s: Store, pid: str, *, include_tools: bool = True) -> dict:
    """一次返回 pack / JSON 文本 / 设置提示词。"""
    pack = build_pack(s, pid, include_tools=include_tools)
    return {
        "ok": True,
        "pack": pack,
        "json": json.dumps(pack, ensure_ascii=False, indent=2),
        "prompt": pack_prompt(pack, "settings"),
    }


# ---------------------------------------------------------------- 导入
def _load_pack(pack: Any) -> dict:
    if isinstance(pack, str):
        if len(pack.encode("utf-8")) > MAX_PACK_BYTES:
            raise LongformError("导入内容过大：上限 512KB")
        try:
            data = json.loads(pack)
        except ValueError as e:
            raise LongformError(f"pack 不是合法 JSON：{e}") from None
    elif isinstance(pack, dict):
        try:
            raw = json.dumps(pack, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            raise LongformError(f"pack 无法序列化为 JSON：{e}") from None
        if len(raw.encode("utf-8")) > MAX_PACK_BYTES:
            raise LongformError("导入内容过大：上限 512KB")
        data = pack
    else:
        raise LongformError(
            f"pack 必须是 JSON 对象或 JSON 字符串，实际收到 {type(pack).__name__}")
    if not isinstance(data, dict):
        raise LongformError("pack 顶层必须是 JSON 对象")
    return data


def _flatten(value: Any, prefix: str = "") -> List[Tuple[str, Any]]:
    """把嵌套对象摊平成点号叶子；dict/list 作为叶子整体写入。"""
    if isinstance(value, dict) and value:
        out: List[Tuple[str, Any]] = []
        for key, val in value.items():
            key = _require_str(str(key), "key", max_len=MAX_KEY_LEN)
            child = f"{prefix}.{key}" if prefix else key
            out.extend(_flatten(val, child))
        return out
    return [(prefix, value)]


def _check_dot_key(key: str) -> None:
    _require_str(key, "key", max_len=MAX_KEY_LEN)
    if any(not part.strip() for part in key.split(".")):
        raise LongformError(f"参数 `key` 不能含空段：{key!r}")


def _ensure_serializable(value: Any, where: str) -> None:
    try:
        json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        raise LongformError(f"{where} 的值无法序列化为 JSON：{e}") from None


def import_pack(s: Store, pid: str, pack: Any, *,
                dry_run: bool = False) -> dict:
    """把设置补丁写回项目。只接受 style / config / studio 三段。"""
    data = _load_pack(pack)
    if data.get("format") != PACK_FORMAT:
        raise LongformError(
            f"pack 格式不匹配：期望 {PACK_FORMAT}，实际 {data.get('format')!r}")
    if data.get("version") != PACK_VERSION:
        raise LongformError(
            f"pack 版本不支持：期望 {PACK_VERSION}，实际 {data.get('version')!r}")

    sections: Dict[str, dict] = {}
    for scope in ("style", "config", "studio"):
        if scope not in data:
            continue
        value = data[scope]
        if not isinstance(value, dict):
            raise LongformError(
                f"pack.{scope} 必须是对象，实际收到 {type(value).__name__}")
        hit = _find_secret(value, scope)
        if hit:
            raise LongformError(f"拒绝导入密钥字段：{hit}")
        sections[scope] = value

    # 密钥也会藏在 style 的嵌套值里，上面的递归已覆盖。
    style = sections.get("style") or {}
    config = sections.get("config") or {}
    studio = sections.get("studio") or {}

    style_plan: List[Tuple[str, Any]] = []
    for key, value in style.items():
        _check_dot_key(key)
        _ensure_serializable(value, f"style.{key}")
        style_plan.append((key, value))

    config_plan: List[Tuple[str, Any]] = []
    if config:
        for key, value in _flatten(config):
            _check_dot_key(key)
            _ensure_serializable(value, f"config.{key}")
            config_plan.append((key, value))

    studio_plan: List[Tuple[str, Any]] = []
    if studio:
        for key, value in _flatten(studio, "studio"):
            _check_dot_key(key)
            _ensure_serializable(value, f"studio.{key}")
            studio_plan.append((key, value))

    counts = {"style": len(style_plan), "config": len(config_plan),
              "studio": len(studio_plan)}

    if dry_run:
        changes = ([{"scope": "style", "key": k} for k, _ in style_plan]
                   + [{"scope": "config", "key": k} for k, _ in config_plan]
                   + [{"scope": "studio", "key": k} for k, _ in studio_plan])
        return {"ok": True, "dry_run": True, "changes": changes,
                "counts": counts}

    for key, value in style_plan:
        s.style_set(pid, key, value)
    for key, value in config_plan:
        s.config_set(pid, key, value)
    # studio 最后写：即使 config 里也带了 studio 段，显式补丁优先。
    for key, value in studio_plan:
        s.config_set(pid, key, value)

    skipped = [name for name in ("project", "tools") if name in data]
    return {"ok": True, "dry_run": False, "counts": counts, "skipped": skipped}
