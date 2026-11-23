"""章节正文的精确局部改稿。

设计目标只有一个：把「原文里某一段」换成「新文字」，其余正文一个字符都
不动，并把结果作为新版本落库。它不调用模型，也不做模糊匹配，因此同一套
逻辑既能给 MCP 工具用，也能给 Web 编辑器的人手操作使用。

关键约束：

- ``find`` 必须在当前正文中**逐字出现**，否则直接报错并返回命中数 0；
- 默认只改第 ``occurrence`` 处，``all_matches=true`` 时改全部非重叠命中；
- ``preview=true`` 只计算不写库，返回改前/改后摘录，方便人确认；
- 写入前可用 ``expected_version`` 做乐观并发保护，防止覆盖别人刚保存的版本；
- 写入复用 ``Store.chapter_save``，因此版本号、标题、状态、data 与导出行为
  都沿用既有章节保存路径，不另造一套存储。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from store import (
    CHAPTER_STATUSES,
    MAX_CHAPTER_N,
    MAX_TEXT_LEN,
    LongformError,
    Store,
    _require_int,
    _require_str,
)


def _require_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise LongformError(f"参数 `{field}` 必须是布尔值 true/false")
    return value


def _positions(text: str, find: str) -> List[int]:
    """返回所有非重叠命中的起始位置，与 ``str.count`` 口径一致。"""
    out: List[int] = []
    start = 0
    while True:
        idx = text.find(find, start)
        if idx < 0:
            return out
        out.append(idx)
        start = idx + len(find)


def _excerpt(text: str, start: int, end: int, pad: int = 90) -> str:
    left = max(0, start - pad)
    right = min(len(text), end + pad)
    prefix = "…" if left > 0 else ""
    suffix = "…" if right < len(text) else ""
    return f"{prefix}{text[left:right]}{suffix}"


def patch_text(text: str, find: str, replace: str, *, occurrence: int = 1,
               all_matches: bool = False) -> Dict[str, Any]:
    """纯函数：计算替换结果，不碰数据库。

    返回 ``matches``（总命中数）、``replaced``（本次实际替换数）、
    ``content``（新正文）以及 ``before``/``after`` 两段上下文摘录。
    """
    if not find:
        raise LongformError("参数 `find` 不能为空")
    if find == replace:
        raise LongformError("`find` 与 `replace` 相同，正文不会有任何变化")
    if not isinstance(occurrence, int) or isinstance(occurrence, bool):
        raise LongformError("参数 `occurrence` 必须是正整数")
    if occurrence < 1:
        raise LongformError("参数 `occurrence` 必须从 1 开始")

    hits = _positions(text, find)
    if not hits:
        raise LongformError(
            "正文中找不到 `find` 指定的原文。请确认标点、空格与换行是否一致；"
            "可先用 `chapter_get` 读取当前版本再复制原文。")

    if all_matches:
        count = len(hits)
        new_text = text.replace(find, replace)
    else:
        if occurrence > len(hits):
            raise LongformError(
                f"正文只有 {len(hits)} 处命中，`occurrence={occurrence}` 超出范围")
        idx = hits[occurrence - 1]
        count = 1
        new_text = text[:idx] + replace + text[idx + len(find):]

    if new_text == text:
        raise LongformError("替换后正文没有变化，未生成新版本")

    if all_matches:
        first = hits[0]
        old_end = hits[-1] + len(find)
        delta = len(replace) - len(find)
        new_end = old_end + delta * len(hits)
    else:
        first = hits[occurrence - 1]
        old_end = first + len(find)
        new_end = first + len(replace)

    return {
        "content": new_text,
        "matches": len(hits),
        "replaced": count,
        "occurrence": occurrence,
        "all_matches": bool(all_matches),
        "before_excerpt": _excerpt(text, first, old_end),
        "after_excerpt": _excerpt(new_text, first, new_end),
        "chars_before": len(text),
        "chars_after": len(new_text),
        "delta_chars": len(new_text) - len(text),
    }


def chapter_patch(store: Store, project: str, n: Any, find: Any,
                  replace: Any, *, occurrence: Any = 1,
                  all_matches: Any = False, preview: Any = False,
                  expected_version: Any = None,
                  status: Optional[str] = None) -> Dict[str, Any]:
    """对第 ``n`` 章当前版本做一次局部替换。

    ``preview=true`` 时只返回差异摘要；否则调用 ``chapter_save`` 生成
    ``version+1``，旧版本保持可查、可 diff。
    """
    _require_str(project, "project")
    _require_int(n, "n", minimum=0, maximum=MAX_CHAPTER_N)
    _require_str(find, "find", allow_empty=True, max_len=MAX_TEXT_LEN)
    _require_str(replace, "replace", allow_empty=True, max_len=MAX_TEXT_LEN)
    _require_int(occurrence, "occurrence", minimum=1)
    _require_bool(all_matches, "all_matches")
    _require_bool(preview, "preview")
    if expected_version is not None:
        _require_int(expected_version, "expected_version", minimum=1)
    if status is not None and status not in CHAPTER_STATUSES:
        raise LongformError(
            f"参数 `status` 必须是 {CHAPTER_STATUSES} 之一，实际收到 {status!r}")

    chapter = store.chapter_get(project, n)
    if not chapter:
        raise LongformError(f"第 {n} 章还没有任何版本，无法局部改稿")
    base_version = chapter["version"]
    if expected_version is not None and expected_version != base_version:
        raise LongformError(
            f"版本已变化：期望 v{expected_version}，当前最新为 v{base_version}。"
            "请重新读取本章、确认改动后再试。")

    result = patch_text(
        chapter["content"], find, replace,
        occurrence=occurrence, all_matches=bool(all_matches))

    summary = {
        "ok": True,
        "preview": bool(preview),
        "n": n,
        "from_version": base_version,
        "matches": result["matches"],
        "replaced": result["replaced"],
        "occurrence": result["occurrence"],
        "all_matches": result["all_matches"],
        "chars_before": result["chars_before"],
        "chars_after": result["chars_after"],
        "delta_chars": result["delta_chars"],
        "before_excerpt": result["before_excerpt"],
        "after_excerpt": result["after_excerpt"],
    }

    if preview:
        summary["title"] = chapter["title"]
        summary["status"] = status or chapter["status"]
        return summary

    saved = store.chapter_save(
        project, n,
        chapter["title"],
        result["content"],
        status or chapter["status"],
        chapter.get("data_json"),
    )
    summary["to_version"] = saved["version"]
    summary["title"] = chapter["title"]
    summary["status"] = saved["status"]
    return summary
