"""OpenAI 兼容的 LLM 客户端（仅标准库 ``urllib``）。

只做一件事：把一段 prompt 变成一段文本。配置来源统一走
``settings.resolve_llm``，因此环境变量、settings.json、默认值三层的优先级
与设置页保持同一套逻辑。

行为约定：

- **单次请求，绝不重试**：上游 429/5xx 直接转成可读错误，避免把一次
  失败放大成一次轰炸；重试策略留给调用方。
- 未配置时抛 ``LongformError``，消息里写清缺哪些字段、去哪里配置——
  模型看到错误后能自己修复，而不是反复调用。
- 返回值与错误信息里都不出现密钥。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

from settings import resolve_llm
from store import MAX_TEXT_LEN, LongformError, _as_int, _require_str

_MAX_TIMEOUT = 600
_MIN_TIMEOUT = 1
_ERROR_BODY_CHARS = 300


def endpoint(base_url: str) -> str:
    """``{base_url}/chat/completions``，容忍 base_url 末尾的斜杠。"""
    return base_url.rstrip("/") + "/chat/completions"


def _unconfigured(cfg: Dict[str, Any]) -> LongformError:
    missing = "、".join(cfg.get("missing") or [])
    return LongformError(
        f"LLM 未配置（缺少：{missing}）。请在设置页或 config/settings.json 的 `llm` 段"
        "填写 base_url / model / api_key，也可用环境变量 LONGFORM_LLM_BASE_URL、"
        "LONGFORM_LLM_MODEL、LONGFORM_LLM_API_KEY 覆盖。")


def _error_body(err: urllib.error.HTTPError) -> str:
    """读取错误响应体用于排障；读不到就返回空串，绝不二次抛错。"""
    try:
        raw = err.read() or b""
    except Exception:  # noqa: BLE001
        return ""
    return raw[:_ERROR_BODY_CHARS].decode("utf-8", "replace")


def generate(prompt: str, system: Optional[str] = None,
             temperature: Optional[float] = None,
             max_tokens: Optional[int] = None,
             timeout: Optional[int] = None,
             settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """向配置的模型服务发起一次对话补全。

    返回 ``{"text", "usage", "model", "provider"}``；任何失败都以
    ``LongformError`` 的形式给出可读中文原因。
    """
    prompt = _require_str(prompt, "prompt", max_len=MAX_TEXT_LEN)
    cfg = resolve_llm(settings)
    if not cfg.get("configured"):
        raise _unconfigured(cfg)

    if system is not None:
        system = _require_str(system, "system", allow_empty=True,
                              max_len=MAX_TEXT_LEN)

    messages = []
    if system and system.strip():
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    body: Dict[str, Any] = {"model": cfg["model"], "messages": messages}
    if temperature is not None:
        body["temperature"] = temperature
    if max_tokens is not None:
        body["max_tokens"] = max_tokens

    limit = _as_int(timeout if timeout is not None else cfg.get("timeout"),
                    int(cfg.get("timeout") or _MIN_TIMEOUT),
                    minimum=_MIN_TIMEOUT, maximum=_MAX_TIMEOUT)
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = "Bearer " + cfg["api_key"]

    req = urllib.request.Request(endpoint(cfg["base_url"]), data=data,
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=limit) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raise LongformError(
            f"LLM 服务返回 HTTP {e.code}：{_error_body(e)}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", None) or e
        raise LongformError(f"LLM 请求超时或网络不可达：{reason}") from None

    try:
        payload = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        raise LongformError(
            f"LLM 服务返回的不是合法 JSON：{raw[:200].decode('utf-8', 'replace')}"
        ) from None

    if not isinstance(payload, dict):
        raise LongformError("LLM 响应顶层必须是 JSON 对象，无法解析文本")

    choices = payload.get("choices")
    text = None
    if isinstance(choices, list) and choices:
        first = choices[0]
        message = first.get("message") if isinstance(first, dict) else None
        if isinstance(message, dict):
            text = message.get("content")
    if not isinstance(text, str):
        raise LongformError(
            "LLM 响应缺少 choices[0].message.content，无法解析文本")

    usage = payload.get("usage")
    return {
        "text": text,
        "usage": usage if isinstance(usage, dict) else {},
        "model": payload.get("model") or cfg.get("model", ""),
        "provider": urllib.parse.urlsplit(cfg["base_url"]).netloc or "openai-compatible",
    }
