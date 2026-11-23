"""AI Studio 自驾协议：下一步决策、交接单、一键设计。

不配置 LLM API 时，所有需要模型生成的环节都改为"交接"：
系统把完整指令落成 ``handoffs`` 行，AI Studio 对话返回结果后由 ``submit``
校验、落库并推进状态机。本模块只负责编排，不直接暴露 MCP 工具。

公开 API：

- ``next_task(s, pid)``：按固定优先级返回 ``{action: tool|handoff|human|done}``；
- ``start_design(s, pid, ...)``：创建 ``project_design`` 交接单；
- ``submit(s, handoff_id, result)``：按 kind 校验并应用结果；
- ``cancel(s, handoff_id)``：取消未应用的交接单；
- ``handoffs(s, pid, status)``：列出交接单。

设计/大纲一律"先全量校验，再落库"：校验失败不写任何数据；重复提交靠
``result_hash`` 与实体/大纲的按名覆盖保证幂等。
"""

from __future__ import annotations

import importlib
import inspect
import json
import logging
import os
from typing import Any, Dict, List, Optional

import pipeline
from store import (
    LongformError,
    MAX_BRIEF_LEN,
    MAX_KEY_LEN,
    MAX_NAME_LEN,
    MAX_OUTLINE_BODY_LEN,
    MAX_TITLE_LEN,
    Store,
    _require_int,
    _require_str,
)

# 交给 AI Studio 的设计 JSON 结构。字段名即契约，改这里等于改协议。
DESIGN_SCHEMA: Dict[str, Any] = {
    "project": {"genre": "", "audience": "", "tone": "", "premise": "",
                "target_chapters": 0, "target_words": 0},
    "style": {"叙事视角": "第三人称", "时态": "过去时", "语感": "", "禁用词": []},
    "characters": [{"name": "", "role": "", "want": "", "fear": "", "secret": ""}],
    "world": [{"name": "", "type": "地点/规则/组织", "rules": ""}],
    "master_outline": {"title": "", "body": ""},
    "volumes": [{"title": "", "start_chapter": 1, "end_chapter": 20, "body": ""}],
    "first_chapters": [{"n": 1, "title": "", "brief": ""},
                       {"n": 2, "title": "", "brief": ""},
                       {"n": 3, "title": "", "brief": ""}],
}

# 总纲/卷纲/章纲的交接 JSON 结构（用于缺总纲或章纲时的 outline 单）。
OUTLINE_SCHEMA: Dict[str, Any] = {
    "master_outline": {"title": "", "body": ""},
    "volumes": [{"title": "", "start_chapter": 1, "end_chapter": 20, "body": ""}],
    "first_chapters": [{"n": 1, "title": "", "brief": ""}],
}

_MAX_CHARACTERS = 300
_MAX_WORLD = 300
_MAX_VOLUMES = 200
_MAX_CHAPTER_OUTLINES = 500

# 题材模板目录：仓库根下的 templates/genres/，由 index.json 给出 id 顺序。
REPO_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
GENRES_DIR = os.path.join(REPO_ROOT, "templates", "genres")
GENRE_REQUIRED = ("id", "name", "premise_hint", "default_genre",
                  "contract_hints", "style_seed", "outline_seed")
_GENRE_STR_FIELDS = ("id", "name", "premise_hint", "default_genre")
_LOG = logging.getLogger(__name__)


def _genre_index() -> List[str]:
    """读取 index.json 里的题材 id 顺序；缺失或损坏一律返回空列表。"""
    path = os.path.join(GENRES_DIR, "index.json")
    if not os.path.isfile(path):
        _LOG.warning("题材模板索引不存在：%s；已跳过", path)
        return []
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as e:
        _LOG.warning("题材模板索引不可读：%s（%s）；已跳过", path, e)
        return []
    if isinstance(raw, dict):
        raw = raw.get("genres", raw.get("ids", []))
    if not isinstance(raw, list):
        _LOG.warning("题材模板索引必须是 id 列表：%s；已跳过", path)
        return []
    return [item.strip() for item in raw
            if isinstance(item, str) and item.strip()]


def list_genres() -> List[dict]:
    """列出可用的题材模板。

    读操作保持宽容：索引或某个模板缺失/损坏时跳过并记 warning，不抛异常。
    重复 id 只保留第一次出现的那个。
    """
    out: List[dict] = []
    seen = set()
    for gid in _genre_index():
        path = os.path.join(GENRES_DIR, f"{gid}.json")
        if not os.path.isfile(path):
            _LOG.warning("题材模板缺失：%s；已跳过", path)
            continue
        try:
            with open(path, encoding="utf-8") as f:
                item = json.load(f)
        except (OSError, ValueError) as e:
            _LOG.warning("题材模板不可读：%s（%s）；已跳过", path, e)
            continue
        if not isinstance(item, dict):
            _LOG.warning("题材模板顶层必须是对象：%s；已跳过", path)
            continue
        missing = [key for key in GENRE_REQUIRED if key not in item]
        if missing:
            _LOG.warning("题材模板 %s 缺少字段：%s；已跳过", gid, "、".join(missing))
            continue
        if not all(isinstance(item[key], str) and item[key].strip()
                   for key in _GENRE_STR_FIELDS):
            _LOG.warning("题材模板 %s 的文本字段为空或类型错误；已跳过", gid)
            continue
        if (not isinstance(item["contract_hints"], list)
                or not isinstance(item["style_seed"], dict)
                or not isinstance(item["outline_seed"], dict)):
            _LOG.warning("题材模板 %s 的契约/风格/大纲字段类型错误；已跳过", gid)
            continue
        ident = item["id"].strip()
        if ident in seen:
            _LOG.warning("题材模板 id 重复：%s；已跳过后出现的那份", ident)
            continue
        seen.add(ident)
        out.append(item)
    return out



# ---------------------------------------------------------------- 结果解析
def _strip_fence(text: str) -> str:
    """去掉 AI Studio 常带的 ```json ... ``` 围栏，宽容但不多做猜测。"""
    t = text.strip()
    if not t.startswith("```"):
        return t
    lines = t.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _as_json(result: Any) -> Any:
    """把提交结果解析为 JSON 值；``{"text": ...}`` 包裹会先解包。"""
    if isinstance(result, dict):
        if set(result.keys()) == {"text"} and isinstance(result.get("text"), str):
            return _as_json(result["text"])
        return result
    if isinstance(result, str):
        raw = _strip_fence(result)
        try:
            return json.loads(raw)
        except (TypeError, ValueError) as e:
            raise LongformError(f"提交结果不是合法 JSON：{e}") from None
    raise LongformError(
        f"提交结果必须是 JSON 对象或 JSON 字符串，实际收到 {type(result).__name__}")


def _as_text(result: Any) -> str:
    """取纯文本结果：直接字符串，或 ``{"text"/"content"/"body": ...}``。"""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        for key in ("text", "content", "body"):
            value = result.get(key)
            if isinstance(value, str):
                return value
    raise LongformError("提交结果必须包含正文文本（字符串或 text 字段）")


def _opt_str(data: Dict[str, Any], key: str, field: str,
             max_len: int) -> str:
    value = data.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise LongformError(
            f"设计字段 `{field}` 必须是字符串，实际收到 {type(value).__name__}")
    if len(value) > max_len:
        raise LongformError(f"设计字段 `{field}` 过长：{len(value)} 字符，上限 {max_len}")
    return value.strip()


def _opt_int(data: Dict[str, Any], key: str, field: str, *,
             minimum: int, maximum: int, allow_zero: bool = True) -> int:
    value = data.get(key)
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int):
        raise LongformError(
            f"设计字段 `{field}` 必须是整数，实际收到 {type(value).__name__}")
    if allow_zero and value == 0:
        return 0
    if not (minimum <= value <= maximum):
        raise LongformError(
            f"设计字段 `{field}` 必须在 {minimum}–{maximum}，实际收到 {value}")
    return value


def _opt_list(data: Dict[str, Any], key: str, field: str, limit: int) -> list:
    value = data.get(key)
    if not isinstance(value, list):
        raise LongformError(
            f"设计字段 `{field}` 必须是列表，实际收到 {type(value).__name__}")
    if len(value) > limit:
        raise LongformError(f"设计字段 `{field}` 条目过多：{len(value)}，上限 {limit}")
    return value


# ---------------------------------------------------------------- design 校验
def _validate_project(project: Any) -> Dict[str, Any]:
    if not isinstance(project, dict):
        raise LongformError("design.project 必须是对象")
    out = {
        "genre": _opt_str(project, "genre", "project.genre", MAX_NAME_LEN),
        "audience": _opt_str(project, "audience", "project.audience", MAX_NAME_LEN),
        "tone": _opt_str(project, "tone", "project.tone", MAX_NAME_LEN),
        "premise": _opt_str(project, "premise", "project.premise", MAX_BRIEF_LEN),
        "target_chapters": _opt_int(project, "target_chapters",
                                    "project.target_chapters",
                                    minimum=1, maximum=5000),
        "target_words": _opt_int(project, "target_words",
                                 "project.target_words",
                                 minimum=100, maximum=100_000),
    }
    return out


def _validate_style(style: Any) -> Dict[str, Any]:
    if not isinstance(style, dict):
        raise LongformError("design.style 必须是对象")
    out: Dict[str, Any] = {}
    for key, value in style.items():
        if not isinstance(key, str) or not key.strip():
            raise LongformError("design.style 的键必须是非空字符串")
        if len(key) > MAX_KEY_LEN:
            raise LongformError(f"design.style 键过长：{key[:20]}…")
        if isinstance(value, (dict, list)):
            out[key] = value
        elif isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
        else:
            raise LongformError(
                f"design.style[{key!r}] 类型不支持：{type(value).__name__}")
    return out


def _validate_characters(items: Any) -> List[Dict[str, Any]]:
    rows = _opt_list({"characters": items}, "characters", "characters",
                     _MAX_CHARACTERS)
    out: List[Dict[str, Any]] = []
    for i, item in enumerate(rows):
        if not isinstance(item, dict):
            raise LongformError(f"design.characters[{i}] 必须是对象")
        name = _opt_str(item, "name", f"characters[{i}].name", MAX_NAME_LEN)
        if not name:
            raise LongformError(f"design.characters[{i}] 缺少 name")
        row: Dict[str, Any] = {"name": name}
        for key, limit in (("role", MAX_NAME_LEN), ("want", MAX_BRIEF_LEN),
                           ("fear", MAX_BRIEF_LEN), ("secret", MAX_BRIEF_LEN)):
            text = _opt_str(item, key, f"characters[{i}].{key}", limit)
            if text:
                row[key] = text
        out.append(row)
    return out


def _validate_world(items: Any) -> List[Dict[str, Any]]:
    rows = _opt_list({"world": items}, "world", "world", _MAX_WORLD)
    out: List[Dict[str, Any]] = []
    for i, item in enumerate(rows):
        if not isinstance(item, dict):
            raise LongformError(f"design.world[{i}] 必须是对象")
        name = _opt_str(item, "name", f"world[{i}].name", MAX_NAME_LEN)
        if not name:
            raise LongformError(f"design.world[{i}] 缺少 name")
        row: Dict[str, Any] = {"name": name}
        for key, limit in (("type", MAX_NAME_LEN), ("rules", MAX_BRIEF_LEN)):
            text = _opt_str(item, key, f"world[{i}].{key}", limit)
            if text:
                row[key] = text
        out.append(row)
    return out


def _validate_master(data: Any) -> Dict[str, str]:
    if data is None:
        return {"title": "", "body": ""}
    if not isinstance(data, dict):
        raise LongformError("master_outline 必须是对象")
    return {
        "title": _opt_str(data, "title", "master_outline.title", MAX_TITLE_LEN),
        "body": _opt_str(data, "body", "master_outline.body", MAX_OUTLINE_BODY_LEN),
    }


def _validate_volumes(items: Any) -> List[Dict[str, Any]]:
    rows = _opt_list({"volumes": items}, "volumes", "volumes", _MAX_VOLUMES)
    out: List[Dict[str, Any]] = []
    for i, item in enumerate(rows):
        if not isinstance(item, dict):
            raise LongformError(f"design.volumes[{i}] 必须是对象")
        start = _opt_int(item, "start_chapter", f"volumes[{i}].start_chapter",
                         minimum=1, maximum=5000, allow_zero=False)
        end = _opt_int(item, "end_chapter", f"volumes[{i}].end_chapter",
                       minimum=1, maximum=5000, allow_zero=False)
        if start > end:
            raise LongformError(
                f"design.volumes[{i}] 的 start_chapter({start}) 不能大于 "
                f"end_chapter({end})")
        out.append({
            "title": _opt_str(item, "title", f"volumes[{i}].title", MAX_TITLE_LEN),
            "start_chapter": start,
            "end_chapter": end,
            "body": _opt_str(item, "body", f"volumes[{i}].body",
                             MAX_OUTLINE_BODY_LEN),
        })
    return out


def _validate_chapters(items: Any) -> List[Dict[str, Any]]:
    rows = _opt_list({"first_chapters": items}, "first_chapters",
                     "first_chapters", _MAX_CHAPTER_OUTLINES)
    out: List[Dict[str, Any]] = []
    seen = set()
    for i, item in enumerate(rows):
        if not isinstance(item, dict):
            raise LongformError(f"design.first_chapters[{i}] 必须是对象")
        n = _opt_int(item, "n", f"first_chapters[{i}].n",
                     minimum=1, maximum=5000, allow_zero=False)
        if n in seen:
            raise LongformError(f"design.first_chapters 章号重复：{n}")
        seen.add(n)
        out.append({
            "n": n,
            "title": _opt_str(item, "title", f"first_chapters[{i}].title",
                              MAX_TITLE_LEN),
            "brief": _opt_str(item, "brief", f"first_chapters[{i}].brief",
                              MAX_BRIEF_LEN),
        })
    return out


def _validate_design(data: Any) -> Dict[str, Any]:
    """设计结果全量校验；任何一项不合法都抛错，调用方保证不落库。"""
    data = _as_json(data)
    if not isinstance(data, dict):
        raise LongformError("设计结果必须是 JSON 对象")
    for key in ("project", "style", "characters", "world", "volumes",
                "first_chapters"):
        if key not in data:
            raise LongformError(f"设计结果缺少 `{key}`")
    return {
        "project": _validate_project(data.get("project")),
        "style": _validate_style(data.get("style")),
        "characters": _validate_characters(data.get("characters")),
        "world": _validate_world(data.get("world")),
        "master_outline": _validate_master(data.get("master_outline")),
        "volumes": _validate_volumes(data.get("volumes")),
        "first_chapters": _validate_chapters(data.get("first_chapters")),
    }


def _validate_outline(data: Any) -> Dict[str, Any]:
    """outline 交接单：支持 master/volumes/first_chapters，或单章草稿。"""
    data = _as_json(data)
    if not isinstance(data, dict):
        raise LongformError("大纲结果必须是 JSON 对象")

    out: Dict[str, Any] = {}
    if "master_outline" in data or "master" in data:
        out["master_outline"] = _validate_master(
            data.get("master_outline", data.get("master")))
    if "volumes" in data:
        out["volumes"] = _validate_volumes(data.get("volumes"))
    if "first_chapters" in data:
        out["first_chapters"] = _validate_chapters(data.get("first_chapters"))

    chapter = data.get("chapter")
    if isinstance(chapter, dict):
        out["first_chapters"] = _validate_chapters([chapter])
    elif "chapter_n" in data or ("n" in data and "brief" in data):
        single = {"n": data.get("chapter_n", data.get("n")),
                  "title": data.get("title", ""),
                  "brief": data.get("brief", data.get("body", ""))}
        out["first_chapters"] = _validate_chapters([single])

    if not out:
        raise LongformError("大纲结果没有可应用的内容（需要 master/volumes/"
                            "first_chapters 或单章 chapter）")
    return out


# ---------------------------------------------------------------- 写库
def _apply_project(s: Store, pid: str, project: Dict[str, Any]) -> Dict[str, Any]:
    """文档约定：项目配置以扁平键写入，另存 ``studio.design_applied`` 标记。"""
    applied = []
    s.config_set(pid, "studio.design_applied", True)
    for key in ("genre", "audience", "tone", "premise"):
        value = project.get(key)
        if value:
            s.config_set(pid, key, value)
            applied.append(key)
    for key in ("target_chapters", "target_words"):
        value = project.get(key)
        if isinstance(value, int) and value > 0:
            s.config_set(pid, key, value)
            applied.append(key)
    return {"config": applied}


def _apply_style(s: Store, pid: str, style: Dict[str, Any]) -> Dict[str, Any]:
    keys = []
    for key, value in style.items():
        s.style_set(pid, key, value)
        keys.append(key)
    return {"style": keys}


def _apply_entities(s: Store, pid: str, design: Dict[str, Any]) -> Dict[str, Any]:
    characters = 0
    for row in design.get("characters") or []:
        data = {k: v for k, v in row.items() if k != "name"}
        s.bible_upsert(pid, "character", row["name"], data)
        characters += 1
    world = 0
    for row in design.get("world") or []:
        data = {k: v for k, v in row.items() if k != "name"}
        s.bible_upsert(pid, "world", row["name"], data)
        world += 1
    return {"characters": characters, "world": world}


def _apply_outline(s: Store, pid: str, design: Dict[str, Any]) -> Dict[str, Any]:
    master = design.get("master_outline")
    if master is not None:
        data = None
        total = (design.get("project") or {}).get("target_chapters") or 0
        if total:
            data = {"total_chapters": total}
        s.outline_set(pid, "master", title=master.get("title") or None,
                      body=master.get("body") or "", data=data)
    volumes = 0
    for i, row in enumerate(design.get("volumes") or []):
        s.outline_set(pid, "volume", seq=i + 1,
                      title=row.get("title") or None,
                      body=row.get("body") or "",
                      start_chapter=row["start_chapter"],
                      end_chapter=row["end_chapter"])
        volumes += 1
    chapters = 0
    for row in design.get("first_chapters") or []:
        s.outline_set(pid, "chapter", chapter_n=row["n"],
                      title=row.get("title") or f"第{row['n']}章",
                      body=row.get("brief") or "")
        chapters += 1
    return {"volumes": volumes, "chapters": chapters,
            "master": master is not None}


# ---------------------------------------------------------------- 公开 API
def start_design(s: Store, pid: str, *, premise: str = "", genre: str = "",
                 chapters: int = 0, target_words: int = 0) -> dict:
    """创建 `project_design` 交接单，要求 AI Studio 只输出设计 JSON。"""
    s._require_project(pid)
    premise = _require_str(premise, "premise", allow_empty=True,
                           max_len=MAX_BRIEF_LEN)
    genre = _require_str(genre, "genre", allow_empty=True, max_len=MAX_NAME_LEN)
    chapters = _require_int(chapters, "chapters", minimum=0, maximum=5000)
    target_words = _require_int(target_words, "target_words",
                                minimum=0, maximum=100_000)

    prompt = (
        "你是本书的策划编辑。请只输出一个 JSON 对象，不要解释，不要 Markdown 代码块。\n"
        f"项目：{pid}\n"
        "已知信息：\n"
        f"- 题材：{genre or '由 AI Studio 先向作者确认'}\n"
        f"- 核心设定：{premise or '由 AI Studio 先向作者确认'}\n"
        f"- 计划章数：{chapters or '由 AI Studio 先向作者确认'}\n"
        f"- 目标总字数：{target_words or '由 AI Studio 先向作者确认'}\n"
        "输出结构必须严格为：\n"
        f"{json.dumps(DESIGN_SCHEMA, ensure_ascii=False, indent=2)}\n"
        "要求：人物至少 1 个；世界条目至少 1 个；卷纲区间不得重叠；"
        "first_chapters 给出前 3 章章纲；target_chapters 在 1–5000、"
        "target_words 在 100–100000（不确定时可写 0，由作者随后确认）。"
    )
    handoff = s.handoff_create(pid, "project_design", prompt,
                               schema=DESIGN_SCHEMA)
    return {"ok": True, "handoff_id": handoff["id"], "kind": "project_design",
            "prompt": prompt, "schema": DESIGN_SCHEMA}


def handoffs(s: Store, pid: str, status: Optional[str] = None) -> dict:
    """列出项目交接单；status 可选。"""
    s._require_project(pid)
    rows = s.handoff_list(pid, status=status)
    return {"ok": True, "handoffs": rows}


def cancel(s: Store, handoff_id: str) -> dict:
    """取消未应用的交接单。"""
    return s.handoff_cancel(handoff_id)


def submit(s: Store, handoff_id: str, result: Any) -> dict:
    """校验并应用交接结果；成功推进到 applied，失败可读报错。"""
    h = s.handoff_get(handoff_id)
    if h is None:
        raise LongformError(f"交接单不存在：{handoff_id}")
    pid = h["project"]
    kind = h["kind"]

    if h["status"] == "applied":
        return {"ok": True, "handoff_id": handoff_id, "kind": kind,
                "already_applied": True}
    if h["status"] in ("cancelled", "rejected"):
        raise LongformError(
            f"交接单 {handoff_id} 处于 {h['status']}，不能再提交")

    try:
        if kind == "project_design":
            design = _validate_design(result)
            try:
                summary = _apply_design(s, pid, design)
            except Exception as e:
                s.handoff_fail(handoff_id, f"{type(e).__name__}: {e}")
                return {"ok": False, "handoff_id": handoff_id, "kind": kind,
                        "error": f"{type(e).__name__}: {e}", "partial": True}
            s.handoff_submit(handoff_id, result)
            return {"ok": True, "handoff_id": handoff_id, "kind": kind,
                    "applied": True, "result": summary}

        if kind == "outline":
            outline = _validate_outline(result)
            try:
                summary = _apply_outline(s, pid, outline)
            except Exception as e:
                s.handoff_fail(handoff_id, f"{type(e).__name__}: {e}")
                return {"ok": False, "handoff_id": handoff_id, "kind": kind,
                        "error": f"{type(e).__name__}: {e}", "partial": True}
            s.handoff_submit(handoff_id, result)
            return {"ok": True, "handoff_id": handoff_id, "kind": kind,
                    "applied": True, "result": summary}

        if kind == "llm_generate":
            text = _as_text(result)
            s.handoff_submit(handoff_id, result)
            return {"ok": True, "handoff_id": handoff_id, "kind": kind,
                    "applied": True, "text": text}

        if kind == "review":
            s.handoff_submit(handoff_id, result)
            return {"ok": True, "handoff_id": handoff_id, "kind": kind,
                    "applied": True, "result": result}

        if kind == "chapter_draft":
            func = _dispatcher("runner", "submit_draft", kind)
            text = _as_text(result)
            if not h.get("ref_id"):
                raise LongformError("chapter_draft 交接单缺少 ref_id（run_id）")
            res = func(s, h["ref_id"], text)
            s.handoff_submit(handoff_id, result)
            return {"ok": True, "handoff_id": handoff_id, "kind": kind,
                    "applied": True, "result": res}

        if kind in ("quality_critique", "quality_revise"):
            name = ("submit_critique" if kind == "quality_critique"
                    else "submit_revision")
            func = _dispatcher("quality_loop", name, kind)
            payload = _as_json(result)
            res = func(s, h.get("ref_id") or h.get("chapter_n"), payload)
            s.handoff_submit(handoff_id, result)
            return {"ok": True, "handoff_id": handoff_id, "kind": kind,
                    "applied": True, "result": res}

        if kind == "style_distill":
            func = _dispatcher("style_distill", "submit_profile", kind)
            payload = _as_json(result)
            res = func(s, h["id"], payload)
            s.handoff_submit(handoff_id, result)
            return {"ok": True, "handoff_id": handoff_id, "kind": kind,
                    "applied": True, "result": res}

        raise LongformError(f"该交接类型尚未启用：{kind}")
    except LongformError:
        raise
    except Exception as e:  # 分发器内部异常：标记失败但不吞掉原因
        s.handoff_fail(handoff_id, f"{type(e).__name__}: {e}")
        raise LongformError(f"应用交接结果失败：{type(e).__name__}: {e}") from None


def _apply_design(s: Store, pid: str, design: Dict[str, Any]) -> Dict[str, Any]:
    """按固定顺序应用设计：配置 → 文风 → 实体 → 大纲。"""
    summary: Dict[str, Any] = {}
    summary.update(_apply_project(s, pid, design["project"]))
    summary.update(_apply_style(s, pid, design["style"]))
    summary.update(_apply_entities(s, pid, design))
    summary.update(_apply_outline(s, pid, design))
    return summary


def _dispatcher(module_name: str, func_name: str, kind: str):
    """延迟导入分发器；模块或函数缺失时给出可读错误（Task 3/4 补齐）。"""
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        raise LongformError(f"该交接类型尚未启用：{kind}（缺少模块 {module_name}）")
    func = getattr(module, func_name, None)
    if not callable(func):
        raise LongformError(
            f"该交接类型尚未启用：{kind}（{module_name}.{func_name} 尚未实现）")
    return func


# ---------------------------------------------------------------- next_task
def _handoff_public(h: dict) -> dict:
    return {
        "handoff_id": h["id"], "kind": h["kind"], "status": h["status"],
        "chapter_n": h.get("chapter_n"), "ref_id": h.get("ref_id"),
        "prompt": h.get("prompt"), "schema": h.get("schema"),
    }


def _tool(tool: str, args: dict, why: str) -> dict:
    return {"ok": True, "action": "tool", "tool": tool, "args": args, "why": why}


def _human(gate: str, why: str, **extra) -> dict:
    return {"ok": True, "action": "human", "gate": gate, "why": why, **extra}


def _handoff_action(h: dict, why: str) -> dict:
    return {"ok": True, "action": "handoff", "why": why,
            "handoff": _handoff_public(h)}


def _outline_handoff(s: Store, pid: str, level: str,
                     chapter_n: Optional[int] = None) -> dict:
    """创建缺总纲/卷纲/章纲的 outline 交接单。"""
    if level == "chapter":
        template = {"chapter": {"n": chapter_n, "title": "", "brief": ""}}
        prompt = (
            f"请为项目「{pid}」第 {chapter_n} 章写章纲。只输出一个 JSON 对象，"
            "不要解释，不要 Markdown 代码块：\n"
            f"{json.dumps(template, ensure_ascii=False, indent=2)}\n"
            "要求：brief 是可执行的本章细纲（目标 200–800 字），"
            "必须给出场景、冲突、转折与结尾钩子。"
        )
        handoff = s.handoff_create(pid, "outline", prompt, chapter_n=chapter_n,
                                   schema=template)
        return _handoff_action(
            handoff, f"第 {chapter_n} 章还没有章纲，先让 AI Studio 补章纲")

    template = OUTLINE_SCHEMA if level == "master" else {
        "volumes": OUTLINE_SCHEMA["volumes"]}
    prompt = (
        f"请为项目「{pid}」补齐全书大纲。只输出一个 JSON 对象，"
        "不要解释，不要 Markdown 代码块：\n"
        f"{json.dumps(template, ensure_ascii=False, indent=2)}\n"
        "要求：卷纲区间不得重叠；每卷给出起止章与卷内推进；"
        "如能确定前几章，请一并给出 first_chapters。"
    )
    handoff = s.handoff_create(pid, "outline", prompt, schema=template)
    return _handoff_action(handoff, "还缺总纲/卷纲，先让 AI Studio 补大纲")


def _pending_quality_handoff(s: Store, pid: str, n: int) -> Optional[dict]:
    for h in s.handoff_pending(pid):
        if h["kind"] == "quality_critique" and h["chapter_n"] == n:
            return h
    return None


def _start_quality(s: Store, pid: str, n: int) -> Optional[dict]:
    """尝试以 provider=studio 启动质量环；未实现时返回 None。"""
    try:
        module = importlib.import_module("quality_loop")
    except ImportError:
        return None
    func = getattr(module, "start", None)
    if not callable(func):
        return None
    try:
        params = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return None
    if "provider" not in params:
        return None
    try:
        func(s, pid, n, provider="studio")
    except (LongformError, TypeError):
        return None
    return _pending_quality_handoff(s, pid, n)


def _chapter_action(s: Store, pid: str, n: int) -> Optional[dict]:
    """返回该章需要的下一步；None 表示该章已完成（approved + 有摘要）。"""
    chapter = s.chapter_get(pid, n)
    if chapter is None:
        return _tool(
            "run_start",
            {"project": pid,
             "policy": {"provider": "studio", "require_human_gate": False}},
            f"第 {n} 章章纲已就绪，启动 studio 模式单章流水线")

    findings = s.lint_findings_list(pid, chapter_n=n)
    if not findings:
        return _tool("lint_check", {"project": pid, "chapter_n": n},
                     "正文已存在但还没有 L0 报告，先跑 L0")
    blocks = [f for f in findings if f.get("level") == "block"]
    if blocks:
        return _human("fix_lint",
                      f"第 {n} 章 L0 有 {len(blocks)} 条阻断项，必须先修复",
                      chapter_n=n, findings=blocks)

    loops = s.loop_list(pid, chapter_n=n)
    if not any(l.get("status") == "done" for l in loops):
        if any(l.get("status") == "running" for l in loops):
            return _human("quality", f"第 {n} 章质量环正在运行，等待其结束",
                          chapter_n=n)
        handoff = _start_quality(s, pid, n)
        if handoff is not None:
            return _handoff_action(
                handoff, f"第 {n} 章 L0 通过，交给 AI Studio 做质量评分")
        return _tool(
            "loop_start",
            {"project": pid, "chapter_n": n, "provider": "studio"},
            f"第 {n} 章 L0 通过，启动 studio 模式质量环")

    if chapter.get("status") == "approved":
        if not s.summary_get(pid, n):
            return _tool("summary_set", {"project": pid, "chapter_n": n},
                         f"第 {n} 章已定稿但还没有摘要，补齐摘要")
        return None

    return _human("approve", f"第 {n} 章质量环已结束，等待人工批准",
                  chapter_n=n)


def next_task(s: Store, pid: str) -> dict:
    """按固定优先级给出可执行的下一步。

    顺序：待处理交接单 → 最近运行（awaiting_input / awaiting_human /
    blocked / failed）→ 立项设计 → 总纲/卷纲 → 章纲 → 正文 → L0 →
    质量环 → 摘要 → 下一章 → done。
    """
    s._require_project(pid)

    pending = s.handoff_pending(pid)
    if pending:
        return _handoff_action(
            pending[0],
            f"有 {len(pending)} 张待处理交接单（{pending[0]['kind']}），先提交结果")

    runs = s.run_list(pid, limit=20)
    # 批次父运行只做编排，不能抢单章运行的下一步；优先看最近的章节运行。
    chapter_runs = [r for r in runs if r.get("kind") != "batch"]
    if chapter_runs:
        run = chapter_runs[0]
        status = run.get("status")
        if status == "awaiting_input":
            return _human("submit",
                          "运行在等待 AI Studio 提交正文，但当前没有待处理交接单",
                          run_id=run["id"])
        if status == "awaiting_human":
            return _human("approve", "流水线已到人工闸门，等待批准",
                          run_id=run["id"])
        if status in ("blocked", "failed"):
            return _human("fix", "最近一次运行被阻断/失败，需要先修复",
                          run_id=run["id"], error=run.get("error"))
    else:
        batch = next((r for r in runs if r.get("kind") == "batch"), None)
        if batch is not None:
            status = batch.get("status")
            if status == "awaiting_human":
                return _human("approve", "整本批跑停在人工闸门，等待批准",
                              run_id=batch["id"], batch=True)
            if status in ("blocked", "failed"):
                return _human("fix", "整本批跑被阻断/失败，需要先修复",
                              run_id=batch["id"], error=batch.get("error"),
                              batch=True)

    if not s.outline_list(pid, level="master"):
        applied = s.config_get(pid, "studio.design_applied") is True
        if not applied:
            design = start_design(s, pid)
            return _handoff_action(
                {"id": design["handoff_id"], "kind": design["kind"],
                 "status": "pending", "prompt": design["prompt"],
                 "schema": design["schema"], "chapter_n": None, "ref_id": None},
                "项目还没有立项设计，先让 AI Studio 完成立项/文风/人物/总纲/前 3 章")
        return _outline_handoff(s, pid, "master")

    advice = pipeline.outline_next(s, pid)
    stage = advice.get("stage")
    next_n = int(advice.get("next_chapter") or 1)

    if stage == "master":
        return _outline_handoff(s, pid, "master")
    if stage == "volume":
        return _outline_handoff(s, pid, "volume")
    if stage == "chapter":
        return _outline_handoff(s, pid, "chapter", chapter_n=next_n)

    outlined = sorted(
        c["chapter_n"] for c in s.outline_list(pid, level="chapter")
        if c.get("chapter_n") is not None)
    if not outlined:
        return _outline_handoff(s, pid, "chapter", chapter_n=next_n)

    for n in outlined:
        action = _chapter_action(s, pid, n)
        if action is not None:
            return action

    total = int((advice.get("outline") or {}).get("total_chapters") or 0)
    if total and len(outlined) < total:
        return _outline_handoff(s, pid, "chapter", chapter_n=max(outlined) + 1)

    stats = s.ledger_stats(pid)
    return {
        "ok": True, "action": "done",
        "why": "所有已规划章节均已完成（定稿 + 摘要）",
        "summary": {"project": pid, "chapters": stats["chapters"],
                    "words": stats["total_words"],
                    "outline_chapters": stats["outline_chapters"]},
    }
