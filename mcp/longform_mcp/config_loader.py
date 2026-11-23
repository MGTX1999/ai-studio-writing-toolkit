"""配置层：读取 ``config/defaults.yaml``（零第三方依赖）。

标准库没有 YAML 解析器，而本工具箱坚持「零第三方依赖」。这里实现一个
**只覆盖 defaults.yaml 实际语法**的极简解析器：

- 缩进式嵌套映射（2 空格递增，禁止 tab）
- 标量：true / false / null / 整数 / 浮点 / 单双引号字符串
- 行内列表 ``[a, b]``、空列表 ``[]``、块列表 ``- item``
- 整行注释与行尾注释（引号外的 ``#`` 才算注释，不误伤 ``http://``）

遇到不支持的构造（如列表套映射）会抛 ``LongformError`` 并带行号；
``load_defaults()`` 会吞掉异常并回退为空字典——配置写坏不该让服务起不来。

为什么单独一个模块：``store.py`` 不碰文件系统，``profile.py`` 负责变体与
models.json，配置读取同属「文件 → 内存」，但不依赖数据库与变体，独立更清晰。
"""

from __future__ import annotations

import copy
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from store import LongformError

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
CONFIG_DIR = os.environ.get("LONGFORM_CONFIG_DIR", os.path.join(REPO_ROOT, "config"))

# 模块级变量，但**只在函数体内读取**——测试靠 monkeypatch 它来切换配置文件。
DEFAULTS_PATH = os.environ.get(
    "LONGFORM_DEFAULTS_FILE", os.path.join(CONFIG_DIR, "defaults.yaml"))

_CACHE: Dict[str, Any] = {}
_INT_RE = re.compile(r"[+-]?\d+")
_FLOAT_RE = re.compile(r"[+-]?(?:\d+\.\d*|\.\d+|\d+[eE][+-]?\d+|\d+\.\d*[eE][+-]?\d+)")


# ============================================================
# 注释与行预处理
# ============================================================
def _strip_comment(line: str) -> str:
    """剥离行尾注释：只有引号外、且 ``#`` 前是空白（或行首）才算注释。"""
    in_quote: Optional[str] = None
    escaped = False
    for i, ch in enumerate(line):
        if in_quote:
            if escaped:
                escaped = False
            elif ch == "\\" and in_quote == '"':
                escaped = True
            elif ch == in_quote:
                in_quote = None
            continue
        if ch in ("'", '"'):
            in_quote = ch
            continue
        if ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line


def _preprocess(text: str) -> List[Tuple[int, str, int]]:
    """返回 ``[(缩进, 去注释后的正文, 行号)]``；空行与纯注释行跳过。"""
    lines: List[Tuple[int, str, int]] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        if lineno == 1:
            raw = raw.lstrip("\ufeff")
        body = _strip_comment(raw)
        if not body.strip():
            continue
        stripped = body.lstrip(" ")
        if stripped.startswith("\t") or (stripped and body[:len(body) - len(stripped)].find("\t") >= 0):
            raise LongformError(f"第 {lineno} 行：缩进不得包含 tab")
        indent = len(body) - len(stripped)
        lines.append((indent, stripped.rstrip(), lineno))
    return lines


# ============================================================
# 标量 / 键值 / 流式列表
# ============================================================
def _split_kv(body: str, lineno: int) -> Tuple[str, str]:
    """按引号外的第一个 ``:`` 拆分 ``key: value``；无冒号返回 ``(body, "")``。"""
    in_quote: Optional[str] = None
    escaped = False
    for i, ch in enumerate(body):
        if in_quote:
            if escaped:
                escaped = False
            elif ch == "\\" and in_quote == '"':
                escaped = True
            elif ch == in_quote:
                in_quote = None
            continue
        if ch in ("'", '"'):
            in_quote = ch
            continue
        if ch == ":":
            return body[:i].strip(), body[i + 1:].strip()
    return body.strip(), ""


def _split_flow(inner: str) -> List[str]:
    """按引号与嵌套括号外的逗号拆分行内列表。"""
    parts: List[str] = []
    buf: List[str] = []
    in_quote: Optional[str] = None
    depth = 0
    escaped = False
    for ch in inner:
        if in_quote:
            buf.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\" and in_quote == '"':
                escaped = True
            elif ch == in_quote:
                in_quote = None
            continue
        if ch in ("'", '"'):
            in_quote = ch
            buf.append(ch)
        elif ch in "[{":
            depth += 1
            buf.append(ch)
        elif ch in "]}":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def _parse_scalar(raw: str, lineno: int) -> Any:
    v = raw.strip()
    if not v:
        return ""
    if v[0] in ("'", '"'):
        if len(v) < 2 or v[-1] != v[0]:
            raise LongformError(f"第 {lineno} 行：引号未闭合：{raw!r}")
        if v[0] == '"':
            try:
                return json.loads(v)
            except json.JSONDecodeError as e:
                raise LongformError(
                    f"第 {lineno} 行：双引号字符串非法（{e.msg}）：{raw!r}") from None
        return v[1:-1].replace("''", "'")
    if v.startswith("["):
        if not v.endswith("]"):
            raise LongformError(f"第 {lineno} 行：行内列表未闭合：{raw!r}")
        inner = v[1:-1].strip()
        return [] if not inner else [_parse_scalar(p, lineno) for p in _split_flow(inner)]
    low = v.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "~"):
        return None
    if _INT_RE.fullmatch(v):
        return int(v)
    if _FLOAT_RE.fullmatch(v):
        return float(v)
    return v


# ============================================================
# 块解析
# ============================================================
def _parse_block(lines: List[Tuple[int, str, int]], idx: int,
                 indent: int) -> Tuple[Any, int]:
    if idx >= len(lines):
        return {}, idx
    if lines[idx][0] != indent:
        raise LongformError(
            f"第 {lines[idx][2]} 行：缩进不一致（期望 {indent}，实际 {lines[idx][0]}）")
    if lines[idx][1].startswith("- "):
        return _parse_seq(lines, idx, indent)
    return _parse_map(lines, idx, indent)


def _parse_map(lines: List[Tuple[int, str, int]], idx: int,
               indent: int) -> Tuple[Dict[str, Any], int]:
    out: Dict[str, Any] = {}
    while idx < len(lines):
        ind, body, lineno = lines[idx]
        if ind < indent:
            break
        if ind > indent:
            raise LongformError(
                f"第 {lineno} 行：缩进不一致（期望 {indent}，实际 {ind}）")
        if body.startswith("- "):
            raise LongformError(f"第 {lineno} 行：映射中不应出现列表项")
        key, rest = _split_kv(body, lineno)
        if not key:
            raise LongformError(f"第 {lineno} 行：缺少键名")
        idx += 1
        if rest == "":
            if idx < len(lines) and lines[idx][0] > indent:
                child, idx = _parse_block(lines, idx, lines[idx][0])
                out[key] = child
            else:
                out[key] = {}
        else:
            out[key] = _parse_scalar(rest, lineno)
    return out, idx


def _parse_seq(lines: List[Tuple[int, str, int]], idx: int,
               indent: int) -> Tuple[List[Any], int]:
    out: List[Any] = []
    while idx < len(lines):
        ind, body, lineno = lines[idx]
        if ind < indent:
            break
        if ind > indent:
            raise LongformError(
                f"第 {lineno} 行：缩进不一致（期望 {indent}，实际 {ind}）")
        if not body.startswith("- "):
            raise LongformError(f"第 {lineno} 行：列表项必须以 '- ' 开头")
        item = body[2:].strip()
        if not item:
            raise LongformError(f"第 {lineno} 行：不支持空列表项")
        if re.match(r"^[^:'\"]+:(\s|$)", item):
            raise LongformError(f"第 {lineno} 行：不支持列表套映射（- key: value）")
        out.append(_parse_scalar(item, lineno))
        idx += 1
    return out, idx


def parse_simple_yaml(text: str) -> Dict[str, Any]:
    """解析 defaults.yaml 用到的 YAML 子集；顶层必须是映射。"""
    lines = _preprocess(text)
    if not lines:
        return {}
    value, idx = _parse_block(lines, 0, lines[0][0])
    if idx != len(lines):
        raise LongformError(f"第 {lines[idx][2]} 行：多余内容（缩进异常）")
    if not isinstance(value, dict):
        raise LongformError("defaults.yaml 顶层必须是映射（key: value）")
    return value


# ============================================================
# 读取（带缓存）
# ============================================================
def load_defaults(path: Optional[str] = None) -> Dict[str, Any]:
    """读取并解析 defaults.yaml。

    **任何异常都回退为空字典**：配置写坏最多是默认值不生效，
    不该让整个 MCP 服务起不来。按 ``(绝对路径, mtime)`` 缓存，
    返回深拷贝，避免调用方修改缓存对象。
    """
    p = path or DEFAULTS_PATH
    try:
        stat = os.stat(p)
    except OSError:
        return {}
    key = (os.path.abspath(p), stat.st_mtime_ns)
    entry = _CACHE.get("entry")
    if entry and entry[0] == key:
        return copy.deepcopy(entry[1])
    try:
        with open(p, encoding="utf-8") as f:
            value = parse_simple_yaml(f.read())
    except (OSError, LongformError, ValueError):
        value = {}
    _CACHE["entry"] = (key, value)
    return copy.deepcopy(value)


def clear_cache() -> None:
    """清空缓存（测试用：切配置文件后立刻生效）。"""
    _CACHE.clear()
