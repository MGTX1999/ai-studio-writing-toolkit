"""外部通知（Lark webhook / 本地 CLI）。

只做一件事：把事件文本送出去。渠道来自 settings 的 ``notify`` 段或环境变量
``LONGFORM_NOTIFY_WEBHOOK`` / ``LONGFORM_NOTIFY_CLI``，优先级：

    环境变量 > config/settings.json 的 notify 段 > 未配置（skipped）

约定：

- **绝不抛异常**：未配置返回 ``{ok: false, skipped: true}``，网络 / CLI 失败
  返回 ``{ok: false, error: ...}``，调用方不需要 try/except 也能继续干活；
- **绝不用 shell 拼接**：``cli`` 先按命令行拆成 argv，再交给
  ``subprocess.run([...])``；
- **返回值不含密钥**：webhook URL 与 cli 命令行原文不会出现在返回结果或
  错误信息里（错误里出现时统一替换为 ``***``）。
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import urllib.request
from typing import Any, Dict, List, Optional

import settings as settings_mod

EVENTS = ("run_done", "gate_waiting", "batch_failed", "test")

_ENV_WEBHOOK = "LONGFORM_NOTIFY_WEBHOOK"
_ENV_CLI = "LONGFORM_NOTIFY_CLI"
_TIMEOUT = 10
_TRUE_WORDS = ("1", "true", "yes", "on", "y", "t")


def _section(settings: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """取 ``notify`` 段；settings 缺省时按当前配置文件读取。"""
    if settings is None:
        base = settings_mod.load_settings()
    elif isinstance(settings, dict):
        base = settings
    else:
        base = {}
    sec = base.get("notify") if isinstance(base, dict) else None
    return sec if isinstance(sec, dict) else {}


def _as_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _resolve(settings: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """合并 settings 与环境变量；仅在模块内部使用（含明文渠道）。"""
    sec = _section(settings)
    webhook = _as_text(os.environ.get(_ENV_WEBHOOK)) or _as_text(sec.get("webhook"))
    cli = _as_text(os.environ.get(_ENV_CLI)) or _as_text(sec.get("cli"))

    enabled = sec.get("enabled")
    if not isinstance(enabled, bool):
        enabled = isinstance(enabled, str) and enabled.strip().lower() in _TRUE_WORDS
    # 只给了环境变量时视为显式启用，否则 ``export 完还得再改 settings`` 太别扭。
    if not enabled and (os.environ.get(_ENV_WEBHOOK) or os.environ.get(_ENV_CLI)):
        enabled = True
    return {"enabled": enabled, "webhook": webhook, "cli": cli}


def _message(event: str, text: str) -> str:
    return f"[叙事工作台] {event}\n{text}"


def _safe_error(exc: Exception, *secrets: str) -> str:
    """错误文本脱敏：渠道原文（可能带 token）一律替换掉，并截断长度。"""
    msg = f"{type(exc).__name__}: {exc}"
    for secret in secrets:
        if secret:
            msg = msg.replace(secret, "***")
    return msg[:300]


def _split_cli(command: str) -> List[str]:
    """把 cli 字符串拆成 argv；兼容 Windows 路径与带引号的参数。"""
    if not command.strip():
        return []
    try:
        parts = shlex.split(command, posix=(os.name != "nt"))
    except ValueError:
        parts = command.split()
    if os.name == "nt":
        parts = [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'"
                 else p for p in parts]
    return [p for p in parts if p]


def _send_webhook(url: str, event: str, text: str) -> Dict[str, Any]:
    payload = {"msg_type": "text",
               "content": {"text": _message(event, text)}}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            status = getattr(resp, "status", 200)
            resp.read()
    except Exception as e:          # noqa: BLE001 - 通知失败不影响主流程
        return {"ok": False, "event": event, "channel": "webhook",
                "error": _safe_error(e, url)}
    return {"ok": True, "event": event, "channel": "webhook", "status": status}


def _send_cli(command: str, event: str, text: str) -> Dict[str, Any]:
    argv = _split_cli(command)
    if not argv:
        return {"ok": False, "event": event, "channel": "cli",
                "error": "cli 命令为空"}
    argv = argv + [event, text]
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=_TIMEOUT,
                              encoding="utf-8", errors="replace")
    except Exception as e:          # noqa: BLE001 - 同上
        return {"ok": False, "event": event, "channel": "cli",
                "error": _safe_error(e, command)}

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        for secret in (command,):
            if secret:
                detail = detail.replace(secret, "***")
        suffix = f"：{detail[:200]}" if detail else ""
        return {"ok": False, "event": event, "channel": "cli",
                "error": f"退出码 {proc.returncode}{suffix}"}
    return {"ok": True, "event": event, "channel": "cli", "status": 0}


def send(event: str, text: str, *,
         settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """发送一条通知；未配置跳过、失败返回错误，**任何情况都不抛异常**。"""
    event = _as_text(event)
    text = str(text or "").strip()
    if not event:
        return {"ok": False, "error": "事件不能为空"}

    cfg = _resolve(settings)
    if not cfg["enabled"]:
        return {"ok": False, "skipped": True, "event": event,
                "reason": "通知未启用"}
    if not cfg["webhook"] and not cfg["cli"]:
        return {"ok": False, "skipped": True, "event": event,
                "reason": "未配置 webhook / cli"}

    if cfg["webhook"]:
        return _send_webhook(cfg["webhook"], event, text)
    return _send_cli(cfg["cli"], event, text)
