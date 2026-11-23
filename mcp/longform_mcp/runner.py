"""单章自动流水线（Run Engine）：可恢复、可拒绝、可计量。

流程固定为六步：

    plan → assemble → draft → lint → summary → commit

每一步都写 ``run_steps`` 检查点；恢复时只重跑非 ``done`` 的步骤。
两类停止条件：

- **L0 阻断**：``lint`` 发现 block 级问题，step/run 置 ``blocked``，不自动放行；
- **预算耗尽**：``draft`` 前按 ``usage_summary`` 核对本次运行已花 token。

默认启用人闸门（``require_human_gate=True``）：正文与检查都完成后停在
``awaiting_human``，必须由 ``resume(decision="approve"|"reject")`` 推进。

本模块不直接暴露 MCP 工具，也不改 ``tool_specs`` / ``handlers``；调用方
（CLI / Web / 未来的工具层）自行决定何时 start / resume / cancel。
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

import lint
import llm_client
import notify
import packet
import pipeline
import token_saver
import word_stats
from store import (
    LongformError,
    MAX_CHAPTER_N,
    MAX_TEXT_LEN,
    Store,
    _require_int,
)

DEFAULT_POLICY: Dict[str, Any] = {
    "require_human_gate": True,
    "budget_tokens": 200000,
    "target_words": 2500,
    "temperature": 0.8,
    "max_output_tokens": 8000,
    "write_summary": True,
    "summary_max_chars": 600,
    # auto：有 LLM 配置走 API，否则走 studio 交接；显式 api / studio 优先。
    "provider": "auto",
}
PROVIDERS = ("auto", "api", "studio")
_MIN_DRAFT_CHARS = 100

# 步骤序是持久化契约（run_steps.seq），不可调整。
STEPS = (
    (1, "plan"),
    (2, "assemble"),
    (3, "draft"),
    (4, "lint"),
    (5, "summary"),
    (6, "commit"),
)
_TERMINAL_RUN_STATUSES = ("done", "cancelled")
_SYSTEM_PREFIX = "你是长篇小说的写作专家。严格遵守以下项目设定、文风与硬规则。\n\n"
# 整本批跑：单次最多排多少章，防止误传一个天文数字。
_MAX_BATCH_CHAPTERS = 200


class _StepBlocked(Exception):
    """步骤级的确定性阻断（预算 / L0），与普通异常区分。"""


# ---------------------------------------------------------------- 工具
def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _message(exc: Exception) -> str:
    """统一错误格式；不额外拼接任何配置值，避免密钥进入错误信息。"""
    return f"{type(exc).__name__}: {exc}"


def _notify(event: str, text: str) -> None:
    """发送外部通知；通知只是旁路能力，任何失败都不影响主流程。"""
    try:
        notify.send(event, text)
    except Exception:           # noqa: BLE001 - 通知失败必须静默
        pass


def _int_token(value: Any) -> int:
    """把 usage 里的 token 字段收敛成非负整数，脏数据不落库。"""
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


def _finding_line(finding: dict) -> str:
    rule = finding.get("rule") or "?"
    locator = finding.get("locator") or ""
    evidence = (finding.get("evidence") or "").strip()
    suggestion = (finding.get("suggestion") or "").strip()
    head = f"[{rule}]" + (f"@{locator}" if locator else "")
    bits = [head]
    if evidence:
        bits.append(f"证据「{evidence[:40]}」")
    if suggestion:
        bits.append(suggestion)
    return " ".join(bits)


def _current_version(s: Store, pid: str, n: int,
                     fallback: Optional[int] = None) -> Optional[int]:
    chapter = s.chapter_get(pid, n)
    return chapter["version"] if chapter else fallback


# ---------------------------------------------------------------- 策略
def _merge_policy(policy: Optional[dict]) -> Dict[str, Any]:
    if policy is None:
        policy = {}
    if not isinstance(policy, dict):
        raise LongformError(
            f"参数 `policy` 必须是对象，实际收到 {type(policy).__name__}：{policy!r}")
    merged = dict(DEFAULT_POLICY)
    merged.update(policy)

    gate = merged.get("require_human_gate")
    if not isinstance(gate, bool):
        raise LongformError(
            f"参数 `require_human_gate` 必须是布尔值，实际收到 {type(gate).__name__}：{gate!r}")

    budget = merged.get("budget_tokens")
    if isinstance(budget, bool) or not isinstance(budget, int) \
            or not (500 <= budget <= 2_000_000):
        raise LongformError(
            "参数 `budget_tokens`（运行预算）必须是 500–2000000 的整数，"
            f"实际收到 {budget!r}")

    target = merged.get("target_words")
    if isinstance(target, bool) or not isinstance(target, int) \
            or not (100 <= target <= 100_000):
        raise LongformError(
            f"参数 `target_words` 必须是 100–100000 的整数，实际收到 {target!r}")

    temperature = merged.get("temperature")
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) \
            or not (0 <= float(temperature) <= 2):
        raise LongformError(
            f"参数 `temperature` 必须是 0–2 的数字，实际收到 {temperature!r}")
    merged["temperature"] = float(temperature)

    max_output = merged.get("max_output_tokens")
    if isinstance(max_output, bool) or not isinstance(max_output, int) \
            or not (64 <= max_output <= 200_000):
        raise LongformError(
            f"参数 `max_output_tokens` 必须是 64–200000 的整数，实际收到 {max_output!r}")

    write_summary = merged.get("write_summary")
    if not isinstance(write_summary, bool):
        raise LongformError(
            f"参数 `write_summary` 必须是布尔值，实际收到 {type(write_summary).__name__}：{write_summary!r}")

    summary_chars = merged.get("summary_max_chars")
    if isinstance(summary_chars, bool) or not isinstance(summary_chars, int) \
            or not (100 <= summary_chars <= 4_000):
        raise LongformError(
            f"参数 `summary_max_chars` 必须是 100–4000 的整数，实际收到 {summary_chars!r}")

    provider = merged.get("provider")
    if provider not in PROVIDERS:
        raise LongformError(
            "参数 `provider` 只允许 'auto' / 'api' / 'studio'，"
            f"实际收到 {provider!r}")
    return merged


def _studio_mode(s: Store, pid: str) -> bool:
    try:
        return s.config_get(pid, "studio.mode") == "ai-studio"
    except LongformError:
        return False


def _llm_configured() -> bool:
    """LLM 是否已配置；测试可通过 patch ``llm_client.resolve_llm`` 切换。"""
    try:
        return bool(llm_client.resolve_llm().get("configured"))
    except Exception:       # noqa: BLE001 - 配置读取失败视为未配置
        return False


def _resolve_provider(s: Store, pid: str, policy: dict) -> str:
    """把 auto 解析成 api / studio；显式值原样返回。"""
    provider = policy.get("provider") or "auto"
    if provider in ("api", "studio"):
        return provider
    if _studio_mode(s, pid):
        return "studio"
    return "api" if _llm_configured() else "studio"


def _require_outline(s: Store, pid: str, n: int) -> dict:
    """取章纲；没有纲就不允许启动——自动写正文必须站在章纲上。"""
    try:
        info = pipeline.outline_brief(s, pid, n)
    except LongformError:
        raise LongformError(
            f"第 {n} 章还没有章纲：请先 outline_set(level='chapter')") from None
    if not str(info.get("brief") or "").strip():
        raise LongformError(
            f"第 {n} 章还没有章纲：请先 outline_set(level='chapter')")
    return info


def _draft_prompt(s: Store, pid: str, n: int, brief: str, title: str,
                  policy: dict) -> tuple:
    """构造正文生成的 (system, prompt)；API 与 studio 共用同一套指令。"""
    parts = packet.prompt_parts(s, pid, n, brief)
    system = _SYSTEM_PREFIX + parts["stable"]
    prompt = (
        parts["dynamic"]
        + f"\n\n## 写作任务\n写第 {n} 章《{title}》的正文，"
          f"目标约 {int(policy.get('target_words') or 2500)} 字。"
          "只输出正文，不要解释，不要 markdown 代码块。")
    return system, prompt


# ---------------------------------------------------------------- 公开 API
def start(s: Store, project: str, policy: Optional[dict] = None, *,
          auto_run: bool = True) -> dict:
    """为「下一章」新建一次运行；默认立即执行到人闸门 / 阻断 / 完成。"""
    s._require_project(project)
    merged = _merge_policy(policy)
    merged["provider"] = _resolve_provider(s, project, merged)

    advice = pipeline.outline_next(s, project)
    n = int(advice.get("next_chapter") or 1)
    info = _require_outline(s, project, n)
    title = info.get("title") or f"第{n}章"

    run = s.run_create(
        project, kind="chapter", policy=merged,
        budget={"tokens": merged["budget_tokens"]},
        target={"chapter_n": n, "title": title})
    run_id = run["id"]

    if auto_run:
        _execute(s, run_id)
    return status(s, run_id)


def status(s: Store, run_id: str) -> dict:
    """返回运行、步骤与本次 token 账。"""
    run = s.run_get(run_id)
    if run is None:
        raise LongformError(f"运行不存在：{run_id}")
    steps = [
        {
            "seq": row.get("seq"),
            "name": row.get("name"),
            "status": row.get("status"),
            "attempt": row.get("attempt"),
            "output": row.get("output"),
            "error": row.get("error"),
            "started_at": row.get("started_at"),
            "finished_at": row.get("finished_at"),
        }
        for row in s.run_steps(run_id)
    ]
    out = {
        "ok": True,
        "run": run,
        "steps": steps,
        "usage": s.usage_summary(run["project"], by="run", run_id=run_id),
    }
    target = run.get("target") if isinstance(run.get("target"), dict) else {}
    hid = target.get("handoff_id")
    if hid:
        handoff = s.handoff_get(hid)
        if handoff is not None:
            out["handoff"] = {
                "id": handoff["id"],
                "kind": handoff["kind"],
                "status": handoff["status"],
                "prompt": handoff.get("prompt"),
            }
    return out


def list_runs(s: Store, project: Optional[str] = None,
              status: Optional[str] = None, limit: int = 50) -> list:
    """按创建时间倒序列出运行（limit 默认 50）。"""
    return s.run_list(project=project, status=status, limit=limit)


def resume(s: Store, run_id: str, decision: Optional[str] = None) -> dict:
    """推进一次运行：人闸门裁决，或从第一个非 done 步骤继续。"""
    run = s.run_get(run_id)
    if run is None:
        raise LongformError(f"运行不存在：{run_id}")
    if decision == "":
        decision = None
    if decision not in (None, "approve", "reject"):
        raise LongformError(
            f"decision 只允许 'approve' 或 'reject'，实际收到 {decision!r}")

    state = run.get("status")
    pid = run["project"]
    target = run.get("target") or {}
    n = int(target.get("chapter_n") or 0)

    if state == "awaiting_input":
        raise LongformError(
            f"运行 {run_id} 正在等待正文交接单，请用 studio_submit / "
            "runner.submit_draft 提交正文，不能直接 resume")

    if state == "awaiting_human":
        if decision is None:
            return status(s, run_id)
        prev = {row["seq"]: row for row in s.run_steps(run_id)}.get(6)
        attempt = (prev["attempt"] if prev else 0) + 1
        version = _current_version(s, pid, n)
        output = {"chapter_n": n, "version": version}
        if decision == "approve":
            s.chapter_set_status(pid, n, "approved")
            output["approved"] = True
            s.run_step_upsert(pid, run_id, 6, "commit", "done",
                              attempt=attempt, output=output)
            s.run_update(run_id, status="done", error=None)
            chapter_title = target.get("title") or f"第{n}章"
            _notify("run_done",
                    f"项目 {pid} 第 {n} 章《{chapter_title}》已完成并定稿。"
                    f"运行：{run_id}")
        else:
            output["decision"] = "reject"
            s.run_update(run_id, status="cancelled")
            s.run_step_upsert(pid, run_id, 6, "commit", "skipped",
                              attempt=attempt, output=output)
        return status(s, run_id)

    if state in _TERMINAL_RUN_STATUSES:
        return status(s, run_id)

    # pending / running / blocked / failed：统一交给 _execute 从断点继续。
    s.run_update(run_id, status="running", error=None)
    _execute(s, run_id)
    return status(s, run_id)


def cancel(s: Store, run_id: str) -> dict:
    """取消未完成的运行；已完成 / 已取消的运行原样返回。"""
    run = s.run_get(run_id)
    if run is None:
        raise LongformError(f"运行不存在：{run_id}")
    if run.get("status") in _TERMINAL_RUN_STATUSES:
        return status(s, run_id)

    pid = run["project"]
    for row in s.run_steps(run_id):
        if row.get("status") == "done":
            continue
        s.run_step_upsert(
            pid, run_id, int(row["seq"]), row["name"], "skipped",
            attempt=int(row["attempt"]), output=row.get("output"),
            error=row.get("error"))
    s.run_update(run_id, status="cancelled")
    return status(s, run_id)


def submit_draft(s: Store, run_id: str, text: str) -> dict:
    """studio 模式：提交正文，保存草稿并继续 lint/summary/commit。"""
    run = s.run_get(run_id)
    if run is None:
        raise LongformError(f"运行不存在：{run_id}")
    pid = run["project"]
    target = dict(run.get("target") or {})

    hid = target.get("handoff_id")
    if not hid:
        for row in s.run_steps(run_id):
            if int(row.get("seq") or 0) == 3:
                hid = (row.get("output") or {}).get("handoff_id")
                break
    if hid:
        handoff = s.handoff_get(hid)
        if handoff is not None and handoff.get("status") == "applied":
            out = status(s, run_id)
            out["already_applied"] = True
            return out

    if run.get("status") != "awaiting_input":
        raise LongformError(
            f"运行 {run_id} 当前状态为 {run.get('status')}，"
            "只有 awaiting_input 才能提交正文")
    if not hid:
        raise LongformError(f"运行 {run_id} 没有待提交的正文交接单")

    if not isinstance(text, str):
        raise LongformError(
            f"参数 `text` 必须是字符串，实际收到 {type(text).__name__}")
    stripped = text.strip()
    if len(stripped) < _MIN_DRAFT_CHARS:
        raise LongformError(
            f"正文过短：至少 {_MIN_DRAFT_CHARS} 字，实际 {len(stripped)} 字")
    if len(stripped) > MAX_TEXT_LEN:
        raise LongformError(
            f"正文过长：上限 {MAX_TEXT_LEN} 字，实际 {len(stripped)} 字")

    n = int(target.get("chapter_n") or 0)
    title = target.get("title") or f"第{n}章"
    saved = s.chapter_save(pid, n, title, text, status="draft",
                           data={"run_id": run_id, "handoff_id": hid})

    prev = {int(row["seq"]): row for row in s.run_steps(run_id)}.get(3)
    attempt = (int(prev["attempt"]) + 1) if prev else 1
    s.run_step_upsert(pid, run_id, 3, "draft", "done", attempt=attempt,
                      output={"handoff_id": hid, "version": saved["version"],
                              "chars": len(text)})
    s.handoff_submit(hid, {"text": text})

    target["handoff_id"] = None
    s.run_update(run_id, status="running", target=target)
    _execute(s, run_id)
    return status(s, run_id)


# ---------------------------------------------------------------- 执行内核
def _execute(s: Store, run_id: str) -> None:
    run = s.run_get(run_id)
    if run is None:
        raise LongformError(f"运行不存在：{run_id}")
    if run.get("status") in _TERMINAL_RUN_STATUSES:
        return

    s.run_update(run_id, status="running", error=None)
    pid = run["project"]
    policy = dict(DEFAULT_POLICY)
    if isinstance(run.get("policy"), dict):
        policy.update(run["policy"])
    target = run.get("target") if isinstance(run.get("target"), dict) else {}
    n = int(target.get("chapter_n") or 0)
    title = target.get("title") or f"第{n}章"
    if n <= 0 or n > MAX_CHAPTER_N:
        s.run_update(run_id, status="failed", error="运行缺少合法的目标章号")
        return

    steps: Dict[int, dict] = {int(row["seq"]): row
                              for row in s.run_steps(run_id)}

    def is_done(seq: int) -> bool:
        row = steps.get(seq)
        return bool(row and row.get("status") == "done")

    def begin(seq: int, name: str) -> int:
        prev = steps.get(seq)
        attempt = (int(prev["attempt"]) + 1) if prev else 1
        s.run_step_upsert(pid, run_id, seq, name, "running", attempt=attempt)
        return attempt

    def finish(seq: int, name: str, attempt: int, output: Any,
               input_hash: Optional[str] = None) -> None:
        s.run_step_upsert(pid, run_id, seq, name, "done", attempt=attempt,
                          input_hash=input_hash, output=output)
        steps[seq] = {"seq": seq, "name": name, "status": "done",
                      "attempt": attempt, "output": output}

    stopped = False

    def stop(seq: int, name: str, attempt: int, state: str,
             message: str) -> None:
        nonlocal stopped
        s.run_step_upsert(pid, run_id, seq, name, state,
                          attempt=attempt, error=message)
        s.run_update(run_id, status=state, error=message)
        stopped = True
        if state == "blocked":
            _notify("gate_waiting",
                    f"项目 {pid} 第 {n} 章《{title}》被阻断：{message}。"
                    f"运行：{run_id}")

    def run_step(seq: int, name: str, fn):
        """执行一个步骤；done 直接跳过，blocked/failed/pending 重跑。"""
        if is_done(seq):
            return steps[seq].get("output")
        attempt = begin(seq, name)
        try:
            result = fn()
        except _StepBlocked as e:
            stop(seq, name, attempt, "blocked", str(e))
            return None
        except Exception as e:          # noqa: BLE001 - 统一转成可读失败
            stop(seq, name, attempt, "failed", _message(e))
            return None
        output, input_hash = (result if isinstance(result, tuple)
                              else (result, None))
        finish(seq, name, attempt, output, input_hash)
        return output

    state: Dict[str, Any] = {}

    # 1) plan：重新取章纲，落 input_hash，章纲变了也能看出来。
    if is_done(1):
        plan = steps[1].get("output") or {}
        state["brief"] = plan.get("brief") or ""
        state["title"] = plan.get("title") or title
    else:
        def _plan():
            info = _require_outline(s, pid, n)
            brief = info.get("brief") or ""
            state["brief"] = brief
            state["title"] = info.get("title") or title
            output = {"chapter_n": n, "title": state["title"], "brief": brief}
            return output, _sha256(brief)
        run_step(1, "plan", _plan)
        if stopped:
            return

    # 2) assemble：稳定前缀（meta/style/entities）与动态上下文分开。
    def _assemble():
        parts = packet.prompt_parts(s, pid, n, state.get("brief") or "")
        return {
            "chars": parts["chars"],
            "sections": parts["sections"],
            "stable_hash": _sha256(parts["stable"]),
            "prompt_hash": _sha256(parts["stable"] + "\n" + parts["dynamic"]),
            "trace": parts.get("trace"),
        }
    run_step(2, "assemble", _assemble)
    if stopped:
        return

    # 3) draft：studio 模式落交接单；API 模式预算检查 → 生成 → 落版本。
    provider = _resolve_provider(s, pid, policy)
    policy["provider"] = provider

    if provider == "studio" and not is_done(3):
        system, prompt = _draft_prompt(
            s, pid, n, state.get("brief") or "",
            state.get("title") or title, policy)
        handoff = s.handoff_create(
            pid, "chapter_draft", system + "\n\n" + prompt,
            ref_id=run_id, chapter_n=n)
        target["handoff_id"] = handoff["id"]
        s.run_update(run_id, status="awaiting_input", target=target)
        prev = steps.get(3)
        attempt = (int(prev["attempt"]) + 1) if prev else 1
        s.run_step_upsert(pid, run_id, 3, "draft", "pending",
                          attempt=attempt, output={"handoff_id": handoff["id"]})
        return

    def _draft():
        budget = int((run.get("budget") or {}).get("tokens")
                     or policy.get("budget_tokens") or 0)
        usage = s.usage_summary(pid, by="run", run_id=run_id)
        used = int(usage.get("total_tokens") or 0)
        if budget and used >= budget:
            raise _StepBlocked(
                f"本次运行预算已用尽（{used} tokens），"
                "请调大 budget_tokens 或新建运行")

        system, prompt = _draft_prompt(
            s, pid, n, state.get("brief") or "",
            state.get("title") or title, policy)

        res = llm_client.generate(
            prompt, system=system,
            temperature=float(policy.get("temperature") or DEFAULT_POLICY["temperature"]),
            max_tokens=int(policy.get("max_output_tokens")
                           or DEFAULT_POLICY["max_output_tokens"]),
            timeout=None)
        text = res.get("text") if isinstance(res, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise LongformError("LLM 返回空文本，无法写入正文")

        saved = s.chapter_save(pid, n, state.get("title") or title, text,
                               status="draft", data={"run_id": run_id})
        tokens = _usage_tokens((res.get("usage") if isinstance(res, dict)
                                else None))
        s.llm_call_add(
            pid, run_id=run_id, step_name="draft", chapter_n=n,
            provider=str(res.get("provider") or ""),
            model=str(res.get("model") or ""), tier="production",
            prompt_hash=_sha256(system + "\n" + prompt),
            input_tokens=tokens["input"], output_tokens=tokens["output"],
            cached_tokens=tokens["cached"])
        state["text"] = text
        stats = word_stats.count_text(text)
        return {"version": saved["version"], "words": stats["chars"],
                "chars": stats["raw_chars"],
                "llm_model": str(res.get("model") or "")}
    run_step(3, "draft", _draft)
    if stopped:
        return

    # 4) lint：L0 block 即停，不自动放行。
    def _lint():
        result = lint.lint_chapter(s, pid, n, run_id=run_id, persist=True)
        if result.get("blocked"):
            findings = (result.get("findings") or [])[:3]
            message = "L0 检查阻断：" + "；".join(
                _finding_line(f) for f in findings)
            raise _StepBlocked(message)
        return {"blocked": False,
                "counts": result.get("counts") or {},
                "findings": len(result.get("findings") or [])}
    run_step(4, "lint", _lint)
    if stopped:
        return

    # 5) summary：只做抽取式摘要，不调模型；失败仅 warn。
    if not bool(policy.get("write_summary", True)):
        prev = steps.get(5)
        attempt = (int(prev["attempt"]) + 1) if prev else 1
        s.run_step_upsert(pid, run_id, 5, "summary", "skipped",
                          attempt=attempt, output={"written": False})
        steps[5] = {"seq": 5, "name": "summary", "status": "skipped",
                    "attempt": attempt, "output": {"written": False}}
    else:
        def _summary():
            content = state.get("text")
            if content is None:
                chapter = s.chapter_get(pid, n)
                content = (chapter or {}).get("content") or ""
            try:
                summary = token_saver.digest_text(
                    content,
                    max_chars=int(policy.get("summary_max_chars")
                                  or DEFAULT_POLICY["summary_max_chars"]),
                    title=state.get("title") or title)
                s.summary_set(pid, n, summary)
                return {"written": True, "chars": len(summary)}
            except Exception as e:      # noqa: BLE001 - 摘要失败不阻断流程
                return {"written": False, "warning": _message(e)}
        run_step(5, "summary", _summary)
        if stopped:
            return

    # 6) commit：人闸门默认开启；关闭时才自动定稿。
    if bool(policy.get("require_human_gate", True)):
        prev = steps.get(6)
        attempt = (int(prev["attempt"]) + 1) if prev else 1
        output = {"chapter_n": n, "version": _current_version(s, pid, n)}
        s.run_step_upsert(pid, run_id, 6, "commit", "pending",
                          attempt=attempt, output=output)
        s.run_update(run_id, status="awaiting_human", error=None)
        _notify("gate_waiting",
                f"项目 {pid} 第 {n} 章《{state.get('title') or title}》"
                f"已生成，等待人工确认。运行：{run_id}")
        return

    def _commit():
        version = _current_version(s, pid, n)
        s.chapter_set_status(pid, n, "approved")
        return {"chapter_n": n, "version": version, "approved": True}
    run_step(6, "commit", _commit)
    if stopped:
        return
    s.run_update(run_id, status="done", error=None)
    _notify("run_done",
            f"项目 {pid} 第 {n} 章《{state.get('title') or title}》"
            f"已完成并定稿。运行：{run_id}")


# ---------------------------------------------------------------- 整本批跑
#
# 父运行 kind="batch" 只做编排，不写正文；每个子运行仍是完整的单章六步。
# 父 target 记录 {start_chapter, chapters, current_chapter, current_child,
# child_ids}，状态推进复用单章 start/resume/cancel，避免第二套内核。
def _batch_policy(policy: Optional[dict]) -> Dict[str, Any]:
    merged = _merge_policy(policy)
    pause = merged.get("pause_on_blocked", True)
    if not isinstance(pause, bool):
        raise LongformError(
            f"参数 `pause_on_blocked` 必须是布尔值，实际收到 {type(pause).__name__}：{pause!r}")
    merged["pause_on_blocked"] = pause
    return merged


def _batch_run(s: Store, batch_id: str) -> dict:
    run = s.run_get(batch_id)
    if run is None:
        raise LongformError(f"批次不存在：{batch_id}")
    if run.get("kind") != "batch":
        raise LongformError(f"运行 {batch_id} 不是整本批次（kind={run.get('kind')}）")
    return run


def _child_chapter(child: Optional[dict]) -> int:
    if not isinstance(child, dict):
        return 0
    return int((child.get("target") or {}).get("chapter_n") or 0)


def _child_ids(run: dict) -> List[str]:
    target = run.get("target") if isinstance(run.get("target"), dict) else {}
    ids = target.get("child_ids")
    if not isinstance(ids, list):
        return []
    return [x for x in ids if isinstance(x, str)]


def _children_usage(s: Store, pid: str, ids: List[str]) -> int:
    total = 0
    for cid in ids:
        total += int(s.usage_summary(pid, by="run", run_id=cid)
                     .get("total_tokens") or 0)
    return total


def _budget_hit(s: Store, run: dict, ids: List[str]) -> bool:
    budget = int((run.get("budget") or {}).get("tokens") or 0)
    if budget <= 0:
        return False
    return _children_usage(s, run["project"], ids) >= budget


def batch_start(s: Store, project: str, chapters: int,
                policy: Optional[dict] = None) -> dict:
    """整本批跑：从下一章起连续跑 ``chapters`` 章，父运行汇总子运行。"""
    s._require_project(project)
    chapters = _require_int(chapters, "chapters", minimum=1,
                            maximum=_MAX_BATCH_CHAPTERS)
    merged = _batch_policy(policy)
    merged["provider"] = _resolve_provider(s, project, merged)

    advice = pipeline.outline_next(s, project)
    start_n = int(advice.get("next_chapter") or 1)
    target = {
        "start_chapter": start_n,
        "chapters": chapters,
        "current_chapter": start_n,
        "current_child": None,
        "child_ids": [],
    }
    run = s.run_create(project, kind="batch", policy=merged,
                       budget={"tokens": merged["budget_tokens"]},
                       target=target)
    return _batch_drive(s, run["id"])


def _batch_drive(s: Store, batch_id: str) -> dict:
    """从 target.current_chapter 起逐章推进，直到闸门 / 阻断 / 预算 / 完成。"""
    run = _batch_run(s, batch_id)
    pid = run["project"]
    target = dict(run.get("target") or {})
    policy = dict(DEFAULT_POLICY)
    if isinstance(run.get("policy"), dict):
        policy.update(run["policy"])

    start_n = int(target.get("start_chapter") or 1)
    total = int(target.get("chapters") or 0)
    stop = start_n + total
    current = int(target.get("current_chapter") or start_n)
    ids = _child_ids(run)

    s.run_update(batch_id, status="running", error=None)
    while current < stop:
        if _budget_hit(s, run, ids):
            target.update({"current_chapter": current, "current_child": None,
                           "child_ids": ids, "exit_reason": "budget"})
            s.run_update(batch_id, status="blocked",
                         error="批次预算已用尽，请调大 budget_tokens 或分批继续",
                         target=target)
            return batch_status(s, batch_id)

        child = start(s, pid, policy, auto_run=True)
        child_run = child.get("run") or {}
        cid = child_run.get("id")
        if cid and cid not in ids:
            ids.append(cid)
        got = _child_chapter(child_run) or current
        state = child_run.get("status")
        target.update({"child_ids": ids, "current_chapter": current,
                       "current_child": cid})

        if got != current:
            target["exit_reason"] = "outline_gap"
            s.run_update(
                batch_id, status="blocked",
                error=f"第 {current} 章无法启动（新运行排到了第 {got} 章）："
                      "请先补齐该章章纲",
                target=target)
            return batch_status(s, batch_id)

        if state == "done":
            current = got + 1
            target.update({"current_chapter": current, "current_child": None})
            s.run_update(batch_id, target=target)
            continue

        if state in ("awaiting_human", "awaiting_input"):
            s.run_update(batch_id, status="awaiting_human", target=target)
            _notify("gate_waiting",
                    f"项目 {pid} 整本批跑在第 {current} 章等待人工确认。"
                    f"批次：{batch_id}")
            return batch_status(s, batch_id)

        if state == "blocked":
            target["exit_reason"] = "blocked"
            if bool(policy.get("pause_on_blocked", True)):
                s.run_update(batch_id, status="blocked",
                             error=child_run.get("error") or "子运行被 L0 阻断",
                             target=target)
                return batch_status(s, batch_id)
            current = got + 1
            target.update({"current_chapter": current, "current_child": None,
                           "exit_reason": None})
            s.run_update(batch_id, target=target)
            continue

        target["exit_reason"] = state or "failed"
        s.run_update(batch_id, status="failed",
                     error=child_run.get("error") or f"子运行状态为 {state}",
                     target=target)
        _notify("batch_failed",
                f"项目 {pid} 整本批跑在第 {current} 章失败："
                f"{child_run.get('error') or state}。批次：{batch_id}")
        return batch_status(s, batch_id)

    target.update({"current_chapter": stop, "current_child": None,
                   "child_ids": ids, "exit_reason": None})
    s.run_update(batch_id, status="done", error=None, target=target)
    return batch_status(s, batch_id)


def batch_status(s: Store, batch_id: str) -> dict:
    """返回父运行、逐章子运行摘要与整批 token 汇总。"""
    run = _batch_run(s, batch_id)
    pid = run["project"]
    target = run.get("target") if isinstance(run.get("target"), dict) else {}
    children: List[dict] = []
    agg = {"input": 0, "output": 0, "cached": 0,
           "total_tokens": 0, "cost": 0.0}
    for cid in _child_ids(run):
        child = s.run_get(cid)
        if child is None:
            continue
        u = s.usage_summary(pid, by="run", run_id=cid)
        agg["input"] += int(u.get("total_input_tokens") or 0)
        agg["output"] += int(u.get("total_output_tokens") or 0)
        agg["cached"] += int(u.get("total_cached_tokens") or 0)
        agg["total_tokens"] += int(u.get("total_tokens") or 0)
        agg["cost"] += float(u.get("total_cost") or 0)
        children.append({
            "id": cid,
            "status": child.get("status"),
            "chapter": _child_chapter(child),
            "usage": int(u.get("total_tokens") or 0),
            "error": child.get("error"),
        })
    return {
        "ok": True,
        "run": run,
        "children": children,
        "usage": agg,
        "exit_reason": target.get("exit_reason"),
    }


def batch_resume(s: Store, batch_id: str,
                 decision: Optional[str] = None) -> dict:
    """恢复批次：先推进当前子运行，成功后再继续下一章。"""
    run = _batch_run(s, batch_id)
    if run.get("status") in _TERMINAL_RUN_STATUSES:
        return batch_status(s, batch_id)
    if decision == "":
        decision = None
    if decision not in (None, "approve", "reject"):
        raise LongformError(
            f"decision 只允许 'approve' 或 'reject'，实际收到 {decision!r}")

    pid = run["project"]
    target = dict(run.get("target") or {})
    cid = target.get("current_child")

    if cid:
        child = s.run_get(cid)
        if child is None:
            target["current_child"] = None
            s.run_update(batch_id, target=target)
            cid = None
        else:
            state = child.get("status")
            if state == "awaiting_input":
                raise LongformError(
                    f"子运行 {cid} 正在等待正文交接单，请用 studio_submit / "
                    "runner.submit_draft 提交正文后再恢复批次")
            if state == "awaiting_human" and decision is None:
                return batch_status(s, batch_id)
            if state in ("awaiting_human", "blocked", "failed"):
                res = resume(s, cid, decision)
                new_state = (res.get("run") or {}).get("status")
                if new_state == "done":
                    target.update({
                        "current_chapter": _child_chapter(res.get("run")) + 1,
                        "current_child": None,
                        "exit_reason": None,
                    })
                    s.run_update(batch_id, status="running", error=None,
                                 target=target)
                    return _batch_drive(s, batch_id)
                if new_state == "cancelled":
                    s.run_update(batch_id, status="cancelled", target=target)
                    return batch_status(s, batch_id)
                s.run_update(
                    batch_id,
                    status=("awaiting_human" if new_state == "awaiting_human"
                            else ("blocked" if new_state in ("blocked", "failed")
                                  else "running")),
                    error=(res.get("run") or {}).get("error"),
                    target=target)
                return batch_status(s, batch_id)
            if state == "done":
                target.update({
                    "current_chapter": _child_chapter(child) + 1,
                    "current_child": None,
                    "exit_reason": None,
                })
                s.run_update(batch_id, status="running", error=None,
                             target=target)
                return _batch_drive(s, batch_id)
            if state == "cancelled":
                s.run_update(batch_id, status="cancelled", target=target)
                return batch_status(s, batch_id)

    s.run_update(batch_id, status="running", error=None, target=target)
    return _batch_drive(s, batch_id)


def batch_cancel(s: Store, batch_id: str) -> dict:
    """取消父批次与当前正在跑的子运行；已完成批次原样返回。"""
    run = _batch_run(s, batch_id)
    if run.get("status") in _TERMINAL_RUN_STATUSES:
        return batch_status(s, batch_id)
    target = dict(run.get("target") or {})
    cid = target.get("current_child")
    if cid:
        child = s.run_get(cid)
        if child is not None and child.get("status") not in _TERMINAL_RUN_STATUSES:
            cancel(s, cid)
    target["current_child"] = None
    target["exit_reason"] = "cancelled"
    s.run_update(batch_id, status="cancelled", target=target)
    return batch_status(s, batch_id)
