#!/usr/bin/env python3
"""长篇创作 MCP 服务器（stdio，JSON-RPC 2.0）。

零第三方依赖：仅用标准库实现 MCP 的核心方法
  initialize / notifications/initialized / tools/list / tools/call / ping

启动：
    python server.py
    LONGFORM_DATA_DIR=/path/to/dir python server.py     # 指定数据目录（SQLite）
    LONGFORM_PROFILES_DIR=/path/to/dir python server.py # 指定作品变体目录
    python server.py --list-tools                        # 打印工具清单后退出（便于生成函数声明）
    python server.py --self-test                         # 内置自检（带断言，有退出码）

请求分帧同时支持两种写法：
  - 裸 JSON，一行一条（默认，兼容旧客户端）
  - ``Content-Length: N\\r\\n\\r\\n`` + N 字节正文（LSP 风格）

模块划分（职责不互相渗透）：
  config_loader  读 config/defaults.yaml（零依赖 YAML 子集）
  tool_specs     58 个工具的声明（纯数据）
  exporting      导出配置与降级导出
  packet         输入包组装与上下文账
  pipeline       阶段建议与大纲流水线
  handlers       表驱动派发（一个工具一个处理函数）
  selftest       内置自检
  server         传输层 + 协议层 + 兼容导出（本文件）
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Iterator, Optional

# 嵌入式 Python 的 _pth 会把标准库置于脚本目录之前，导致 stdlib 的 profile
# 遮蔽本目录的 profile.py。把本目录放到最前，保证独立运行时也能正确导入。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config_loader  # noqa: F401  兼容导出：测试/脚本可通过 server.config_loader 取用
import profile as profiles
import selftest
from exporting import export_cfg as _export_cfg, try_export as _try_export
from handlers import HANDLERS, dispatch as _dispatch_tool
from packet import context_estimate as _context_estimate
from packet import packet_text as _packet_text
from pipeline import (
    outline_brief as _outline_brief,
    outline_next as _outline_next,
    stage_advice as _stage_advice,
)
from store import (  # noqa: F401  re-export：测试与外部脚本依赖这些符号
    MAX_BRIEF_LEN,
    MAX_CHAPTER_N,
    MAX_CHAPTER_OUTLINE_LEN,
    MAX_KEY_LEN,
    MAX_TEXT_LEN,
    MAX_TOKEN_BUDGET,
    MIN_TOKEN_BUDGET,
    LongformError,
    Store,
    _CHILD_TABLES,
    _as_int,
    _dump,
    _load_json,
    _require_enum,
    _require_int,
    _require_str,
)
from tool_specs import TOOLS, TOOL_MAP  # noqa: F401  re-export

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "longform-writing", "version": "1.0.0"}

DATA_DIR = os.environ.get("LONGFORM_DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
DB_PATH = os.path.join(DATA_DIR, "longform.db")

# 惰性单例：旧实现在 import 时就建库，导致 --list-tools 这类无状态操作
# 也会产生副作用，且测试无法替换 Store。
_STORE: Optional[Store] = None


def get_store() -> Store:
    global _STORE
    if _STORE is None:
        _STORE = Store(DB_PATH)
    return _STORE


# ============================================================
# 派发（表驱动在 handlers.py；这里只保留工具名前置校验）
# ============================================================
def dispatch(name: str, args: Dict[str, Any]) -> Any:
    # 工具名先校验：否则一个不存在的工具名会先被"缺少 project"拦下，
    # 模型看到的错误信息指向完全错误的方向。
    if not isinstance(name, str):
        raise LongformError(f"工具名必须是字符串，实际收到 {type(name).__name__}")
    if name not in TOOL_MAP:
        raise LongformError(f"未知工具：{name}；可用工具：{sorted(TOOL_MAP)}")
    return _dispatch_tool(name, args, get_store())


# ============================================================
# JSON-RPC 传输（stdio）
# ============================================================
def _result(req_id, payload):
    return {"jsonrpc": "2.0", "id": req_id, "result": payload}


def _error(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def handle(msg: dict):
    """处理一条 JSON-RPC 请求；通知与无法响应的情形返回 None。"""
    if not isinstance(msg, dict):
        return _error(None, -32600, "请求必须是 JSON 对象")

    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return _error(req_id, -32600, "params 必须是对象")

    if method == "initialize":
        return _result(req_id, {"protocolVersion": PROTOCOL_VERSION,
                                "capabilities": {"tools": {"listChanged": False}},
                                "serverInfo": SERVER_INFO})
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return _result(req_id, {})
    if method == "tools/list":
        return _result(req_id, {"tools": TOOLS})
    if method == "tools/call":
        if "name" not in params:
            return _error(req_id, -32602, "缺少 params.name")
        tname = params["name"]
        targs = params.get("arguments") or {}
        if not isinstance(targs, dict):
            return _error(req_id, -32602, "params.arguments 必须是对象")
        try:
            out = dispatch(tname, targs)
        except LongformError as e:
            return _result(req_id, {"content": [{"type": "text",
                                                 "text": f"参数错误：{e}"}],
                                    "isError": True})
        except Exception as e:  # noqa: BLE001
            return _result(req_id, {"content": [{"type": "text",
                                                 "text": f"工具调用失败："
                                                         f"{type(e).__name__}: {e}"}],
                                    "isError": True})
        text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False, indent=2)
        return _result(req_id, {"content": [{"type": "text", "text": text}],
                                "isError": False})
    if req_id is None:
        return None
    return _error(req_id, -32601, f"不支持的方法：{method}")


_SKIP = object()


def _read_message(stream) -> Any:
    """从二进制流读取一帧。

    返回消息体（bytes）、``_SKIP``（可忽略的空行/坏头）或 ``None``（EOF）。
    """
    first = stream.readline()
    if not first:
        return None
    if not first.strip():
        return _SKIP
    if first.lstrip().lower().startswith(b"content-length:"):
        # 读完剩余头部（MCP 只约定 Content-Length，其余头一律忽略）
        while True:
            line = stream.readline()
            if line in (b"", b"\r\n", b"\n"):
                break
        try:
            n = int(first.split(b":", 1)[1].strip())
        except (IndexError, ValueError):
            return _SKIP
        if n <= 0:
            return _SKIP
        return stream.read(n) or _SKIP
    return first


def _iter_messages(stream) -> Iterator[bytes]:
    while True:
        frame = _read_message(stream)
        if frame is None:
            return
        if frame is _SKIP:
            continue
        yield frame


def handle_raw(raw: bytes) -> Any:
    """解析一帧并产生响应；通知返回 None（不回包）。

    解析失败必须给出 -32700 响应。旧实现直接 continue，
    客户端会一直等待一个永远不会到达的回包。
    """
    try:
        msg = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        return _error(None, -32700, f"JSON 解析失败：{e}")
    if isinstance(msg, list):
        resps = [r for r in (handle(m) for m in msg if isinstance(m, dict))
                 if r is not None]
        return resps or None
    if not isinstance(msg, dict):
        return _error(None, -32600, "请求必须是 JSON 对象")
    return handle(msg)


def _write(resp: Any) -> None:
    sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _use_utf8_stdio() -> None:
    """JSON-RPC 与小说正文都必须是 UTF-8，但 Windows 的管道默认跟随 locale
    （简体中文是 cp936）。不强制的话，正文里一个 emoji 或伏笔台账的 ⚠ 就会
    让 _write 抛 UnicodeEncodeError，整个服务进程退出、客户端断连。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    _use_utf8_stdio()
    if "--list-tools" in sys.argv:
        print(json.dumps(TOOLS, ensure_ascii=False, indent=2))
        return 0
    if "--self-test" in sys.argv:
        return selftest.run_self_test(get_store())

    try:
        for payload in _iter_messages(sys.stdin.buffer):
            resp = handle_raw(payload)
            if resp is not None:
                _write(resp)
    except (KeyboardInterrupt, BrokenPipeError):
        # 客户端关掉管道是正常退出路径，不该甩 traceback。
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
