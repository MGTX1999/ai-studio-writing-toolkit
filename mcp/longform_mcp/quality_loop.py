"""质量环（Quality Loop）：先 L0 确定性检查，再逐维评分与定向修复。

分工与 ``runner`` 一致：

1. **先 L0**：``lint.lint_chapter(persist=False)`` 一旦 block，立即建一条
   ``blocked`` 的质量环记录并返回，**不消耗任何模型调用**；
2. **再评分**：让模型只输出 JSON（7 个维度 0–10 分 + 违规清单 + 摘要），
   按权重加权得到 overall；
3. **定向修复**：把违规清单、文风档案与目标维度交给模型重写，只要求正文；
4. **可停可退**：达到阈值、连续无提升、轮数用尽都正常退出，
   并按分数保留最佳文本。

默认 ``apply=False``：只评分与给最佳文本，不写正文；``apply=True`` 才把
最佳文本写成新版本。每次模型调用都记入 ``llm_calls``（tier=qa），
每一轮每个维度都落一行 ``loop_metrics``，历史可回溯。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional

import lint
import llm_client
from store import (
    MAX_CHAPTER_N,
    MAX_CLAIM_LEN,
    MAX_KEY_LEN,
    MAX_TEXT_LEN,
    LongformError,
    _require_float,
    _require_int,
    _require_str,
)

# 七个维度，权重合计 10.0。权重不是平均分配：
# 留白 / 节奏 / 人物一致 / 情节推进是长篇小说最影响观感的四项。
DEFAULT_DIMENSIONS: List[dict] = [
    {"key": "blank_space", "name": "留白", "weight": 1.5},
    {"key": "rhythm", "name": "节奏", "weight": 1.5},
    {"key": "attitude", "name": "叙述态度", "weight": 1.4},
    {"key": "sensory", "name": "感官颗粒", "weight": 1.4},
    {"key": "character", "name": "人物一致", "weight": 1.5},
    {"key": "plot", "name": "情节推进", "weight": 1.5},
    {"key": "dialogue", "name": "对话潜台词", "weight": 1.2},
]

DEFAULT_THRESHOLD = 8.5
_MAX_ROUNDS = 5
_SCORE_MAX = 10.0
_MIN_REVISION_CHARS = 100
_PROVIDERS = ("auto", "api", "studio")
_CRITIQUE_SYSTEM = "你是严苛的文学编辑，只指出可核查的问题，不打感情分。"


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _int_token(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        value = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, value)


def _usage_tokens(usage: Any) -> Dict[str, int]:
    """兼容 OpenAI 与少量厂商的 usage 字段命名。"""
    if not isinstance(usage, dict):
        usage = {}
    cached = usage.get("cached_tokens")
    if cached is None:
        details = usage.get("prompt_tokens_details")
        if isinstance(details, dict):
            cached = details.get("cached_tokens")
    if cached is None:
        cached = usage.get("cache_read_input_tokens")
    prompt = usage.get("prompt_tokens")
    if prompt is None:
        prompt = usage.get("input_tokens")
    completion = usage.get("completion_tokens")
    if completion is None:
        completion = usage.get("output_tokens")
    return {
        "input": _int_token(prompt),
        "output": _int_token(completion),
        "cached": _int_token(cached),
    }


def _json_from_text(text: str) -> Any:
    """从模型输出里抠出 JSON；容忍 ```json 围栏与前后解释文字。"""
    raw = (text or "").strip()
    if not raw:
        raise LongformError("模型返回空文本，无法解析评分 JSON")
    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, re.IGNORECASE | re.DOTALL)
    if fence:
        raw = fence.group(1).strip()
    try:
        return json.loads(raw)
    except ValueError:
        pass
    lo, hi = raw.find("{"), raw.rfind("}")
    if lo != -1 and hi > lo:
        try:
            return json.loads(raw[lo:hi + 1])
        except ValueError:
            pass
    raise LongformError(f"模型输出不是合法 JSON：{raw[:200]}")


def _require_score(value: Any, field: str) -> float:
    v = _require_float(value, field, minimum=0.0)
    if v > _SCORE_MAX:
        raise LongformError(f"参数 `{field}` 不能大于 10，实际收到 {v}")
    return v


def _normalise_dimensions(dimensions: Optional[list]) -> List[dict]:
    """校验维度定义：key 唯一、权重为正、合计 10±0.01。"""
    dims = DEFAULT_DIMENSIONS if dimensions is None else dimensions
    if not isinstance(dims, list) or not dims:
        raise LongformError("dimensions 必须是非空数组")
    out: List[dict] = []
    seen = set()
    total = 0.0
    for i, item in enumerate(dims):
        if not isinstance(item, dict):
            raise LongformError(f"dimensions[{i}] 必须是对象")
        key = _require_str(item.get("key"), f"dimensions[{i}].key",
                           max_len=MAX_KEY_LEN)
        if key in seen:
            raise LongformError(f"维度 key 重复：{key}")
        name = _require_str(item.get("name") or key,
                            f"dimensions[{i}].name", max_len=MAX_KEY_LEN)
        weight = _require_float(item.get("weight"), f"dimensions[{i}].weight",
                                minimum=0.0001)
        seen.add(key)
        total += weight
        out.append({"key": key, "name": name, "weight": weight})
    if abs(total - 10.0) > 0.01:
        raise LongformError(f"维度权重合计必须为 10，实际为 {round(total, 4)}")
    return out


def _critique_prompt(text: str, dims: List[dict]) -> str:
    lines = "\n".join(
        f"- {d['key']}（{d['name']}，权重 {d['weight']}）" for d in dims)
    scores = ", ".join(f'"{d["key"]}":0' for d in dims)
    return (
        "请按以下维度给这一章打分（每项 0–10，10 为最佳）：\n"
        f"{lines}\n\n"
        "同时列出可核对的违规：每条必须包含 rule（规则名）、"
        "evidence（原文证据）、suggestion（改法）。\n"
        "只输出 JSON，不要解释，不要代码块：\n"
        '{"scores":{' + scores + '},'
        '"violations":[{"rule":"","evidence":"","suggestion":""}],'
        '"summary":""}\n\n'
        f"【正文】\n{text}"
    )


def _revise_prompt(text: str, violations: List[dict], style: Any,
                   dims: List[dict]) -> str:
    dims_text = "、".join(f"{d['name']}（{d['key']}）" for d in dims)
    return (
        "下面是编辑对本章的评分与问题清单。请在不改变情节事实的前提下"
        f"定向重写，重点改善：{dims_text}。\n\n"
        "【问题清单】\n"
        f"{json.dumps(violations, ensure_ascii=False)}\n\n"
        "【文风档案】\n"
        f"{json.dumps(style, ensure_ascii=False)}\n\n"
        "只输出修改后的完整正文，不要解释、不要代码块。\n\n"
        f"【当前正文】\n{text}"
    )


def _record_call(s: Any, project: str, loop_id: str, step: str, chapter_n: int,
                 prompt: str, result: Any) -> None:
    res = result if isinstance(result, dict) else {}
    tokens = _usage_tokens(res.get("usage"))
    s.llm_call_add(
        project, run_id=loop_id, step_name=step, chapter_n=chapter_n,
        provider=str(res.get("provider") or ""),
        model=str(res.get("model") or ""), tier="qa",
        prompt_hash=_sha256(prompt),
        input_tokens=tokens["input"], output_tokens=tokens["output"],
        cached_tokens=tokens["cached"])


def _generate(prompt: str, *, system: Optional[str],
              temperature: float, max_tokens: int) -> tuple:
    res = llm_client.generate(prompt, system=system,
                              temperature=temperature, max_tokens=max_tokens)
    text = res.get("text") if isinstance(res, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise LongformError("LLM 返回空文本，质量环无法继续")
    return text, res


def _parse_critique(text: str, dims: List[dict]) -> Dict[str, Any]:
    payload = _json_from_text(text)
    if not isinstance(payload, dict):
        raise LongformError("评分 JSON 顶层必须是对象")
    raw_scores = payload.get("scores")
    if not isinstance(raw_scores, dict):
        raise LongformError("评分 JSON 缺少 scores 对象")
    scores: Dict[str, float] = {}
    for d in dims:
        key = d["key"]
        if key not in raw_scores:
            raise LongformError(f"评分缺少维度 `{key}`")
        scores[key] = _require_score(raw_scores[key], f"scores.{key}")

    raw_violations = payload.get("violations")
    if raw_violations is None:
        raw_violations = []
    if not isinstance(raw_violations, list):
        raise LongformError("评分 JSON 的 violations 必须是数组")
    violations: List[dict] = []
    for i, item in enumerate(raw_violations):
        if not isinstance(item, dict):
            raise LongformError(f"violations[{i}] 必须是对象")
        violations.append({
            "rule": str(item.get("rule") or "").strip(),
            "evidence": str(item.get("evidence") or "").strip(),
            "suggestion": str(item.get("suggestion") or "").strip(),
        })
    summary = payload.get("summary")
    return {
        "scores": scores,
        "violations": violations,
        "summary": summary if isinstance(summary, str) else "",
    }


def _overall(scores: Dict[str, float], dims: List[dict]) -> float:
    total = sum(scores[d["key"]] * d["weight"] for d in dims)
    return round(total / 10.0, 4)


def _latest_chapter(s: Any, project: str) -> int:
    rows = s.chapter_list(project)
    if not rows:
        raise LongformError("项目还没有任何章节，无法启动质量环")
    return max(int(r["n"]) for r in rows)


def _resolve_provider(s: Any, pid: str, provider: Any) -> str:
    """把 auto 解析成 api / studio；显式值原样返回。"""
    provider = _require_str(provider, "provider", max_len=MAX_KEY_LEN)
    if provider not in _PROVIDERS:
        raise LongformError(
            "参数 `provider` 只允许 'auto' / 'api' / 'studio'，"
            f"实际收到 {provider!r}")
    if provider in ("api", "studio"):
        return provider
    if s.config_get(pid, "studio.mode") == "ai-studio":
        return "studio"
    try:
        if llm_client.resolve_llm().get("configured"):
            return "api"
    except Exception:       # noqa: BLE001 - 配置读取失败视为未配置
        pass
    return "studio"


def _critique_text(result: Any) -> str:
    """交接结果可以是 JSON 字符串，也可以是 ``{"text": ...}`` 或对象。"""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        if set(result.keys()) == {"text"} and isinstance(result.get("text"), str):
            return result["text"]
        return json.dumps(result, ensure_ascii=False)
    raise LongformError(
        f"评分结果必须是对象或 JSON 字符串，实际收到 {type(result).__name__}")


def _revision_text(result: Any) -> str:
    """修订结果：字符串，或 ``{"text"/"content"/"body": ...}``。"""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        for key in ("text", "content", "body"):
            value = result.get(key)
            if isinstance(value, str):
                return value
    raise LongformError("修订结果必须包含正文文本（字符串或 text 字段）")


def start(s: Any, pid: str, chapter_n: Optional[int] = None, *,
          max_rounds: int = 2, threshold: float = DEFAULT_THRESHOLD,
          apply: bool = False, dimensions: Optional[list] = None,
          min_delta: float = 0.1, provider: Any = "auto") -> dict:
    """启动一次质量环。

    ``apply=False``（默认）只评分并返回最佳文本；``apply=True`` 且最佳文本
    与原文不同时，才写成一个新版本。
    """
    s._require_project(pid)
    if chapter_n is None:
        chapter_n = _latest_chapter(s, pid)
    chapter_n = _require_int(chapter_n, "chapter_n",
                             minimum=0, maximum=MAX_CHAPTER_N)
    chapter = s.chapter_get(pid, chapter_n)
    if chapter is None:
        raise LongformError(f"第 {chapter_n} 章还没有任何版本，无法启动质量环")

    # 先 L0：block 级问题不消耗模型调用。
    lint_result = lint.lint_chapter(s, pid, chapter_n, persist=False)
    if lint_result.get("blocked"):
        loop = s.loop_create(pid, chapter_n, policy={
            "max_rounds": max_rounds, "threshold": threshold,
            "apply": bool(apply)})
        s.loop_update(loop["id"], status="blocked",
                      exit_reason="lint_blocked")
        return {"ok": True, "status": "blocked", "loop_id": loop["id"],
                "project": pid, "chapter_n": chapter_n, "lint": lint_result}

    dims = _normalise_dimensions(dimensions)
    max_rounds = _require_int(max_rounds, "max_rounds", minimum=1,
                              maximum=_MAX_ROUNDS)
    threshold = _require_float(threshold, "threshold", minimum=0.0)
    if threshold > _SCORE_MAX:
        raise LongformError(f"参数 `threshold` 不能大于 10，实际收到 {threshold}")
    min_delta = _require_float(min_delta, "min_delta", minimum=0.0)
    if not isinstance(apply, bool):
        raise LongformError("参数 `apply` 必须是布尔值")
    provider = _resolve_provider(s, pid, provider)

    original = chapter.get("content") or ""
    title = chapter.get("title") or f"第{chapter_n}章"

    if provider == "studio":
        studio_policy = {
            "provider": "studio", "max_rounds": max_rounds,
            "threshold": threshold, "dimensions": dims,
            "min_delta": min_delta, "apply": apply,
            "current_text": original, "round": 1, "prev_score": None,
            "best_text": original, "best_score": None,
            "original_hash": _sha256(original),
        }
        loop = s.loop_create(pid, chapter_n, policy=studio_policy)
        loop_id = loop["id"]
        s.loop_update(loop_id, status="awaiting_input")
        handoff = s.handoff_create(
            pid, "quality_critique", _critique_prompt(original, dims),
            ref_id=loop_id, chapter_n=chapter_n)
        return {"ok": True, "status": "awaiting_input", "loop_id": loop_id,
                "project": pid, "chapter_n": chapter_n, "handoff": handoff}

    policy = {"max_rounds": max_rounds, "threshold": threshold,
              "dimensions": dims, "min_delta": min_delta, "apply": apply}
    loop = s.loop_create(pid, chapter_n, policy=policy)
    loop_id = loop["id"]

    current = original
    best_text = original
    best_score: Optional[float] = None
    prev_overall: Optional[float] = None
    exit_reason = ""
    rounds: List[dict] = []

    try:
        for round_no in range(1, max_rounds + 1):
            prompt = _critique_prompt(current, dims)
            critique_text, critique_res = _generate(
                prompt, system=_CRITIQUE_SYSTEM,
                temperature=0.2, max_tokens=2000)
            _record_call(s, pid, loop_id, "critique", chapter_n,
                         _CRITIQUE_SYSTEM + "\n" + prompt, critique_res)
            critique = _parse_critique(critique_text, dims)
            overall = _overall(critique["scores"], dims)

            content_hash = _sha256(current)[:16]
            for d in dims:
                s.loop_metric_add(
                    pid, loop_id, round_no, d["key"],
                    critique["scores"][d["key"]], overall,
                    violations=critique["violations"],
                    content_hash=content_hash)
            rounds.append({"round": round_no, "overall": overall,
                           "scores": critique["scores"],
                           "violations": critique["violations"]})

            if best_score is None or overall > best_score:
                best_text, best_score = current, overall

            if overall >= threshold:
                best_text, best_score = current, overall
                exit_reason = "threshold"
                break
            if prev_overall is not None and overall - prev_overall < min_delta:
                exit_reason = "no_improvement"
                break
            if round_no == max_rounds:
                exit_reason = "max_rounds"
                break

            prev_overall = overall
            revise_prompt = _revise_prompt(
                current, critique["violations"], s.style_get(pid), dims)
            revised, revise_res = _generate(
                revise_prompt, system=_CRITIQUE_SYSTEM,
                temperature=0.7, max_tokens=8000)
            _record_call(s, pid, loop_id, "revise", chapter_n,
                         _CRITIQUE_SYSTEM + "\n" + revise_prompt, revise_res)
            current = revised.strip()
    except LongformError as e:
        s.loop_update(loop_id, status="failed",
                      exit_reason=_clip(str(e)))
        raise
    except Exception as e:  # noqa: BLE001 - 统一转成可读错误
        message = f"{type(e).__name__}: {e}"
        s.loop_update(loop_id, status="failed", exit_reason=_clip(message))
        raise LongformError(f"质量环执行失败：{message}") from None

    s.loop_update(loop_id, status="done", best_score=best_score,
                  exit_reason=exit_reason)

    applied = False
    out: Dict[str, Any] = {
        "ok": True, "loop_id": loop_id, "project": pid,
        "chapter_n": chapter_n, "status": "done", "rounds": rounds,
        "best_score": best_score, "exit_reason": exit_reason,
        "applied": applied,
    }
    if apply and best_text != original:
        saved = s.chapter_save(pid, chapter_n, title, best_text,
                               status="draft", data={"loop_id": loop_id})
        applied = True
        out["applied"] = True
        out["version"] = saved["version"]
    elif not apply:
        out["best_text"] = best_text
    return out


def _clip(text: str) -> str:
    return (text or "")[:MAX_CLAIM_LEN]


def _loop_pending_handoff(s: Any, pid: str, loop_id: str) -> Optional[dict]:
    for h in s.handoff_pending(pid):
        if h.get("ref_id") == loop_id and h.get("kind") in (
                "quality_critique", "quality_revise"):
            return h
    return None


def _apply_handoff(s: Any, pid: str, loop_id: str, kind: str,
                   result: Any) -> None:
    """把当前轮已消费的交接单推进到 applied。"""
    for h in s.handoff_pending(pid):
        if h.get("ref_id") == loop_id and h.get("kind") == kind:
            s.handoff_submit(h["id"], result)


def _finalize(s: Any, loop_id: str, policy: Dict[str, Any],
              exit_reason: str) -> dict:
    """结束质量环：按需写最佳文本，落 done 并收尾交接单。"""
    loop = s.loop_get(loop_id)
    pid = loop["project"]
    chapter_n = int(loop.get("chapter_n") or 0)
    best_text = policy.get("best_text") or ""
    best_score = policy.get("best_score")
    original_hash = policy.get("original_hash")

    applied = False
    version: Optional[int] = None
    if bool(policy.get("apply")) and _sha256(best_text) != original_hash:
        chapter = s.chapter_get(pid, chapter_n) or {}
        title = chapter.get("title") or f"第{chapter_n}章"
        saved = s.chapter_save(pid, chapter_n, title, best_text,
                               status="draft", data={"loop_id": loop_id})
        applied = True
        version = saved["version"]

    s.loop_update(loop_id, status="done", best_score=best_score,
                  exit_reason=exit_reason, policy=policy)

    pending = _loop_pending_handoff(s, pid, loop_id)
    if pending is not None:
        s.handoff_submit(pending["id"], {
            "loop_id": loop_id, "exit_reason": exit_reason,
            "best_score": best_score})

    out: Dict[str, Any] = {
        "ok": True, "loop_id": loop_id, "status": "done",
        "exit_reason": exit_reason, "best_score": best_score,
        "applied": applied,
    }
    if version is not None:
        out["version"] = version
    if not applied:
        out["best_text"] = best_text
    return out


def submit_critique(s: Any, loop_id: str, result: Any) -> dict:
    """studio 模式：提交一轮七维评分，记录 metrics 并决定修复/结束。"""
    loop = s.loop_get(loop_id)
    if loop is None:
        raise LongformError(f"质量环不存在：{loop_id}")
    if loop.get("status") != "awaiting_input":
        raise LongformError(
            f"质量环 {loop_id} 当前状态为 {loop.get('status')}，"
            "只有 awaiting_input 才能提交评分")
    pid = loop["project"]
    chapter_n = int(loop.get("chapter_n") or 0)
    policy = dict(loop.get("policy") or {})
    dims = _normalise_dimensions(policy.get("dimensions"))

    critique = _parse_critique(_critique_text(result), dims)
    overall = _overall(critique["scores"], dims)
    _apply_handoff(s, pid, loop_id, "quality_critique", result)
    round_no = int(policy.get("round") or 1)
    current = policy.get("current_text") or ""
    content_hash = _sha256(current)[:16]

    for d in dims:
        s.loop_metric_add(
            pid, loop_id, round_no, d["key"], critique["scores"][d["key"]],
            overall, violations=critique["violations"],
            content_hash=content_hash)

    best_text = policy.get("best_text", current)
    best_score = policy.get("best_score")
    if best_score is None or overall > float(best_score):
        best_text, best_score = current, overall
    policy["best_text"] = best_text
    policy["best_score"] = best_score

    threshold = float(policy.get("threshold") or DEFAULT_THRESHOLD)
    min_delta = float(policy.get("min_delta") or 0.0)
    max_rounds = int(policy.get("max_rounds") or 1)
    prev_score = policy.get("prev_score")

    exit_reason = None
    if overall >= threshold:
        exit_reason = "threshold"
    elif prev_score is not None and overall - float(prev_score) < min_delta:
        exit_reason = "no_improvement"
    elif round_no >= max_rounds:
        exit_reason = "max_rounds"
    if exit_reason is not None:
        return _finalize(s, loop_id, policy, exit_reason)

    policy["prev_score"] = overall
    s.loop_update(loop_id, status="awaiting_input", policy=policy)
    revise_prompt = _revise_prompt(
        current, critique["violations"], s.style_get(pid), dims)
    handoff = s.handoff_create(pid, "quality_revise", revise_prompt,
                               ref_id=loop_id, chapter_n=chapter_n)
    return {"ok": True, "status": "awaiting_input", "loop_id": loop_id,
            "round": round_no, "overall": overall, "handoff": handoff}


def submit_revision(s: Any, loop_id: str, text: Any) -> dict:
    """studio 模式：提交修订正文，进入下一轮评分。"""
    loop = s.loop_get(loop_id)
    if loop is None:
        raise LongformError(f"质量环不存在：{loop_id}")
    if loop.get("status") != "awaiting_input":
        raise LongformError(
            f"质量环 {loop_id} 当前状态为 {loop.get('status')}，"
            "只有 awaiting_input 才能提交修订")

    revision = _revision_text(text).strip()
    if len(revision) < _MIN_REVISION_CHARS:
        raise LongformError(
            f"修订正文过短：至少 {_MIN_REVISION_CHARS} 字，实际 {len(revision)} 字")
    if len(revision) > MAX_TEXT_LEN:
        raise LongformError(
            f"修订正文过长：上限 {MAX_TEXT_LEN} 字，实际 {len(revision)} 字")

    pid = loop["project"]
    chapter_n = int(loop.get("chapter_n") or 0)
    policy = dict(loop.get("policy") or {})
    dims = _normalise_dimensions(policy.get("dimensions"))
    policy["current_text"] = revision
    policy["round"] = int(policy.get("round") or 1) + 1
    s.loop_update(loop_id, status="awaiting_input", policy=policy)
    _apply_handoff(s, pid, loop_id, "quality_revise", {"text": revision})

    handoff = s.handoff_create(
        pid, "quality_critique", _critique_prompt(revision, dims),
        ref_id=loop_id, chapter_n=chapter_n)
    return {"ok": True, "status": "awaiting_input", "loop_id": loop_id,
            "round": policy["round"], "handoff": handoff}


def report(s: Any, pid: str, *, loop_id: Optional[str] = None,
           chapter_n: Optional[int] = None, limit: int = 20) -> dict:
    """读取质量环结果：指定 loop_id 给明细；否则给最近若干次运行汇总。"""
    s._require_project(pid)
    if loop_id is not None:
        loop = s.loop_get(loop_id)
        if loop is None or loop.get("project") != pid:
            raise LongformError(f"质量环不存在：{loop_id}（项目 {pid}）")
        out = {"ok": True, "loop": loop, "metrics": s.loop_metrics(loop_id)}
        handoff = _loop_pending_handoff(s, pid, loop_id)
        if handoff is not None:
            out["handoff"] = handoff
        return out

    pending = {h.get("ref_id"): h for h in s.handoff_pending(pid)}
    runs = s.loop_list(pid, chapter_n=chapter_n, limit=limit)
    out_runs = []
    for run in runs:
        metrics = s.loop_metrics(run["id"])
        overalls: Dict[int, float] = {}
        for m in metrics:
            if m.get("overall") is not None:
                overalls[int(m["round"])] = float(m["overall"])
        if run.get("best_score") is not None:
            avg = float(run["best_score"])
        elif overalls:
            avg = round(sum(overalls.values()) / len(overalls), 4)
        else:
            avg = None
        item = dict(run)
        item["avg_score"] = avg
        item["rounds"] = len(overalls)
        if run["id"] in pending:
            item["handoff"] = pending[run["id"]]
        out_runs.append(item)
    return {"ok": True, "project": pid, "runs": out_runs}


def meta(s: Any, pid: str, *, window: int = 10) -> dict:
    """质量环复盘：只出数据与建议，不写库、不改阈值。"""
    s._require_project(pid)
    window = _require_int(window, "window", minimum=1, maximum=100)

    all_runs = s.loop_list(pid, limit=100)
    runs = [r for r in all_runs if r.get("status") == "done"][:window]

    summaries: List[dict] = []
    scores: List[float] = []
    violation_counts: Dict[str, int] = {}
    dimension_scores: Dict[str, List[float]] = {}
    total_violations = 0
    character_low = 0
    passed = 0

    for run in runs:
        metrics = s.loop_metrics(run["id"])
        overalls: Dict[int, float] = {}
        char_scores: List[float] = []
        for m in metrics:
            if m.get("overall") is not None:
                overalls[int(m["round"])] = float(m["overall"])
            dim = m.get("dimension")
            if dim and m.get("score") is not None:
                dimension_scores.setdefault(dim, []).append(float(m["score"]))
            if m.get("dimension") == "character" and m.get("score") is not None:
                char_scores.append(float(m["score"]))
            for v in m.get("violations") or []:
                rule = (v.get("rule") or "").strip() or "unknown"
                violation_counts[rule] = violation_counts.get(rule, 0) + 1
                total_violations += 1

        if run.get("best_score") is not None:
            avg = float(run["best_score"])
        elif overalls:
            avg = round(sum(overalls.values()) / len(overalls), 4)
        else:
            avg = None

        policy = run.get("policy") if isinstance(run.get("policy"), dict) else {}
        threshold = policy.get("threshold")
        if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
            threshold = DEFAULT_THRESHOLD

        if avg is not None:
            scores.append(avg)
            if avg >= float(threshold):
                passed += 1
        if char_scores and min(char_scores) < 7.0:
            character_low += 1

        summaries.append({
            "id": run["id"], "chapter_n": run.get("chapter_n"),
            "status": run.get("status"), "best_score": run.get("best_score"),
            "exit_reason": run.get("exit_reason"), "avg_score": avg,
            "rounds": len(overalls),
        })

    avg_score = round(sum(scores) / len(scores), 4) if scores else None
    pass_rate = round(passed / len(runs), 4) if runs else 0.0
    top_violations = [
        {"rule": rule, "count": count}
        for rule, count in sorted(violation_counts.items(),
                                  key=lambda kv: (-kv[1], kv[0]))[:5]
    ]

    proposals: List[str] = []
    ai_count = violation_counts.get("ai_smell", 0)
    if total_violations and ai_count / total_violations >= 0.4:
        proposals.append("AI 味违规占比高，建议扩充黑名单（style.banned_words）。")
    if avg_score is not None and avg_score < DEFAULT_THRESHOLD:
        proposals.append("均分低于阈值，建议降低 threshold 或增加 max_rounds。")
    if character_low:
        proposals.append(
            f"人物一致低分集中（{character_low} 次），建议补充人物卡与行为红线。")

    dim_avgs = {k: round(sum(v) / len(v), 4)
                for k, v in dimension_scores.items() if v}
    try:
        changes = _suggest_changes(
            _current_quality(s, pid), avg_score, dim_avgs,
            total_violations, ai_count)
    except LongformError:
        changes = {"threshold": None, "weights": {}, "banned_add": []}

    return {"ok": True, "runs": summaries, "avg_score": avg_score,
            "pass_rate": pass_rate, "top_violations": top_violations,
            "proposals": proposals, "changes": changes}


# ============================================================
# 元循环调权：结构化建议 + 守卫式应用
# ============================================================
_META_THRESHOLD_MIN = 6.0
_META_THRESHOLD_MAX = 10.0
_META_WEIGHT_MIN = 1.0
_META_WEIGHT_MAX = 4.0
_META_WEIGHT_STEP = 0.3
_META_BANNED_MAX = 50
_META_BANNED_LEN = 64
_AI_SMELL_HINTS = ("宛如", "仿佛", "不由得", "莫名", "似乎")


def _current_quality(s: Any, pid: str) -> Dict[str, Any]:
    """读取当前质量配置；缺失或写坏时回落到内置阈值与默认权重。"""
    s._require_project(pid)
    threshold = s.config_get(pid, "quality.threshold")
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        threshold = DEFAULT_THRESHOLD
    else:
        threshold = float(threshold)

    weights = {d["key"]: float(d["weight"]) for d in DEFAULT_DIMENSIONS}
    stored = s.config_get(pid, "quality.weights")
    if isinstance(stored, dict):
        for key, value in stored.items():
            if key in weights and not isinstance(value, bool) \
                    and isinstance(value, (int, float)):
                weights[key] = float(value)

    banned = s.style_get(pid, "banned_words")
    if not isinstance(banned, list):
        banned = []
    banned = [b for b in banned if isinstance(b, str) and b.strip()]
    return {"threshold": threshold, "weights": weights,
            "banned_words": banned}


def _suggest_changes(current: Dict[str, Any], avg_score: Optional[float],
                     dim_avgs: Dict[str, float], total_violations: int,
                     ai_count: int) -> Dict[str, Any]:
    """只给出落在守卫范围内的建议；不安全就留空。"""
    changes: Dict[str, Any] = {"threshold": None, "weights": {},
                               "banned_add": []}

    if avg_score is not None:
        target = round(float(avg_score), 1)
        if (_META_THRESHOLD_MIN <= target <= _META_THRESHOLD_MAX
                and abs(target - current["threshold"]) >= 0.05):
            changes["threshold"] = target

    weights = dict(current["weights"])
    ups = [k for k, v in dim_avgs.items()
           if k in weights and v < 7.5 and weights[k] < _META_WEIGHT_MAX]
    downs = [k for k, v in dim_avgs.items()
             if k in weights and v >= 9.0 and weights[k] > _META_WEIGHT_MIN]
    ups.sort(key=lambda k: dim_avgs[k])
    downs.sort(key=lambda k: -dim_avgs[k])
    for up, down in zip(ups, downs):
        delta = round(min(0.2, _META_WEIGHT_MAX - weights[up],
                          weights[down] - _META_WEIGHT_MIN), 4)
        if delta <= 0:
            continue
        weights[up] = round(weights[up] + delta, 4)
        weights[down] = round(weights[down] - delta, 4)
    if (abs(sum(weights.values()) - 10.0) <= 0.01
            and any(abs(weights[k] - current["weights"][k]) > 1e-9
                    for k in weights)):
        changes["weights"] = {k: round(v, 2) for k, v in weights.items()}

    if total_violations and ai_count / total_violations >= 0.4:
        existing = {b.strip() for b in current["banned_words"]}
        add = []
        for word in _AI_SMELL_HINTS:
            if word not in existing and word not in add:
                add.append(word)
        changes["banned_add"] = add
    return changes


def meta_apply(s: Any, pid: str, changes: Any, *,
               confirm: bool = False) -> dict:
    """校验并（在 confirm 时）应用调权建议。

    ``confirm=False`` 只回显规范化后的 changes 与当前配置，不写库；
    任何越界都会在写入前抛 ``LongformError``，避免部分写入。
    """
    s._require_project(pid)
    if not isinstance(changes, dict):
        raise LongformError(
            f"参数 `changes` 必须是对象，实际收到 {type(changes).__name__}")
    current = _current_quality(s, pid)
    clean: Dict[str, Any] = {"threshold": None, "weights": {},
                             "banned_add": []}

    raw_threshold = changes.get("threshold")
    if raw_threshold is not None:
        if isinstance(raw_threshold, bool) \
                or not isinstance(raw_threshold, (int, float)):
            raise LongformError("threshold 必须是数字")
        value = float(raw_threshold)
        if not (_META_THRESHOLD_MIN <= value <= _META_THRESHOLD_MAX):
            raise LongformError(
                f"threshold 必须在 {_META_THRESHOLD_MIN}–{_META_THRESHOLD_MAX}，"
                f"实际收到 {value}")
        clean["threshold"] = round(value, 2)

    raw_weights = changes.get("weights")
    if raw_weights is not None:
        if not isinstance(raw_weights, dict) or not raw_weights:
            raise LongformError("weights 必须是非空对象")
        unknown = [k for k in raw_weights if k not in current["weights"]]
        if unknown:
            raise LongformError(f"未知维度：{', '.join(sorted(unknown))}")
        merged = dict(current["weights"])
        for key, value in raw_weights.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise LongformError(f"权重 {key} 必须是数字")
            v = float(value)
            if not (_META_WEIGHT_MIN <= v <= _META_WEIGHT_MAX):
                raise LongformError(
                    f"权重 {key} 必须在 {_META_WEIGHT_MIN}–{_META_WEIGHT_MAX}，"
                    f"实际收到 {v}")
            if abs(v - merged[key]) > _META_WEIGHT_STEP + 1e-9:
                raise LongformError(
                    f"权重 {key} 单次变化不能超过 {_META_WEIGHT_STEP}，"
                    f"当前 {merged[key]} → {v}")
            merged[key] = v
        total = sum(merged.values())
        if abs(total - 10.0) > 0.01:
            raise LongformError(f"权重合计必须为 10，实际为 {round(total, 4)}")
        clean["weights"] = {k: round(v, 2) for k, v in merged.items()}

    raw_banned = changes.get("banned_add")
    if raw_banned is not None:
        if not isinstance(raw_banned, list):
            raise LongformError("banned_add 必须是数组")
        if len(raw_banned) > _META_BANNED_MAX:
            raise LongformError(
                f"banned_add 最多 {_META_BANNED_MAX} 条，实际 {len(raw_banned)} 条")
        kept: List[str] = []
        for i, word in enumerate(raw_banned):
            if not isinstance(word, str):
                raise LongformError(f"banned_add[{i}] 必须是字符串")
            text = word.strip()
            if not text:
                raise LongformError(f"banned_add[{i}] 不能为空")
            if len(text) > _META_BANNED_LEN:
                raise LongformError(
                    f"banned_add[{i}] 过长：{len(text)} 字符，上限 {_META_BANNED_LEN}")
            kept.append(text)
        clean["banned_add"] = kept

    if not (clean["threshold"] is not None or clean["weights"]
            or clean["banned_add"]):
        return {"ok": True, "preview": not confirm, "applied": False,
                "changes": clean, "current": current, "note": "没有可应用的变更"}

    if not confirm:
        return {"ok": True, "preview": True, "applied": False,
                "changes": clean, "current": current}

    before = {"threshold": current["threshold"],
              "weights": dict(current["weights"]),
              "banned_words": list(current["banned_words"])}
    if clean["threshold"] is not None:
        s.config_set(pid, "quality.threshold", clean["threshold"])
    if clean["weights"]:
        s.config_set(pid, "quality.weights", clean["weights"])

    merged_banned = list(current["banned_words"])
    if clean["banned_add"]:
        seen = {b.strip() for b in merged_banned}
        for word in clean["banned_add"]:
            if word not in seen:
                merged_banned.append(word)
                seen.add(word)
        s.style_set(pid, "banned_words", merged_banned)

    after = {"threshold": clean["threshold"] if clean["threshold"] is not None
             else before["threshold"],
             "weights": clean["weights"] or before["weights"],
             "banned_words": merged_banned}
    audit = s.audit_add(pid, {"kind": "quality_meta", "before": before,
                              "after": after})
    return {"ok": True, "preview": False, "applied": True,
            "changes": clean, "current": before, "after": after,
            "audit_id": audit["id"]}
