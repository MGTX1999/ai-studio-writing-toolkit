"""本地可视化编辑器的 HTTP 服务（只用标准库，零第三方依赖）。

安全边界（与 Spec 的「仅本机可访问」一致）：

- 只绑定调用方传入的 host，默认 ``127.0.0.1``；显式传入通配地址会被拒绝。
- 所有 ``/api/*`` 请求必须带 ``X-Longform-Token``，令牌在启动时随机生成。
- 静态资源只从 ``webui/`` 目录读取，路径经 ``realpath`` 校验，越界一律 404。
- 不返回任何 ``Access-Control-Allow-*`` 头，不提供跨域写接口。
- 日志只写 ``METHOD path``（丢弃 query，避免把搜索词/令牌写进日志）。

写操作全部走 ``Store`` 的既有方法，本模块不写任何 SQL。

关于线程：``sqlite3`` 的连接是「线程亲和」的，默认不允许跨线程使用。服务用
``ThreadingHTTPServer``（每个请求一个线程），因此这里用一个**单线程 Store 桥**
持有唯一的连接，所有 API 调用排队交给它执行。既满足线程模型要求，也把本地
单用户场景下的写入天然串行化，无需改动 ``store.py``。
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import queue
import secrets
import sys
import tempfile
import threading
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple

import citations
import ima_client
import ima_export
import insights
import inspiration
import kb
import lint
import llm_client
import narrative
import packet
import profile as profiles
import prose_patch
import quality_loop
import revision_advisor
import runner
import server as app_server
import settings
import studio_autopilot
import studio_bridge
import style_distill
import token_saver
import word_stats
from store import LongformError, Store

VERSION = "1.15.1"
MAX_BODY_BYTES = 8 * 1024 * 1024
# AI Studio 写作要求 / 补充说明的单字段上限，与 8MB 请求体上限分开控制。
MAX_STUDIO_TEXT = 20000
# 浏览器上传到 IMA 的单个文件上限；base64 解码后再校验，与请求体上限分开。
MAX_IMA_UPLOAD_BYTES = 10 * 1024 * 1024
# base64 会膨胀约 4/3，10MB 文件的请求体需要比默认 8MB 更宽。
MAX_IMA_UPLOAD_REQUEST_BYTES = 16 * 1024 * 1024
_IMA_UPLOAD_SUFFIXES = (".md", ".txt", ".pdf", ".docx", ".epub")
_WILDCARD_HOSTS = ("", "0.0.0.0", "::", "[::]")

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
WEBUI_DIR = os.path.join(REPO_ROOT, "webui")

_MIME = {
    "html": "text/html; charset=utf-8",
    "htm": "text/html; charset=utf-8",
    "js": "application/javascript; charset=utf-8",
    "mjs": "application/javascript; charset=utf-8",
    "css": "text/css; charset=utf-8",
    "json": "application/json; charset=utf-8",
    "svg": "image/svg+xml",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "ico": "image/x-icon",
    "woff": "font/woff",
    "woff2": "font/woff2",
    "txt": "text/plain; charset=utf-8",
}

_PLACEHOLDER_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>叙事工作台</title>
<style>
  body { margin: 0; min-height: 100vh; display: grid; place-items: center;
         background: #faf7f0; color: #1f1b16;
         font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
  main { max-width: 34rem; padding: 2rem; line-height: 1.7; }
  h1 { font-size: 1.35rem; color: #a63a2b; }
  code { background: #efe8dc; padding: .1rem .35rem; border-radius: .25rem; }
</style>
</head>
<body>
<main>
  <h1>webui 尚未生成</h1>
  <p>API 服务已就绪，但静态界面目录 <code>webui/</code> 还不存在。</p>
  <p>请由 Task 10 产出 <code>webui/index.html</code>、<code>webui/app.js</code>
     与 <code>webui/style.css</code> 后刷新本页。</p>
</main>
</body>
</html>
"""


# ============================================================
# Store 桥（单线程持有 sqlite 连接）
# ============================================================
class _StoreBridge:
    """把 Store 调用串行地交给一个专用线程执行。

    ``sqlite3.Connection`` 只能由创建它的线程使用；HTTP 请求线程不能直接
    碰 ``Store.conn``。桥线程在内部建连，调用方通过队列提交
    ``fn(store, *args)`` 并等待结果，异常原样抛回调用线程。
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._jobs: "queue.Queue[Any]" = queue.Queue()
        self._lock = threading.Lock()
        self._closed = False
        self._thread = threading.Thread(target=self._loop, name="longform-store",
                                        daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        store: Optional[Store] = None
        init_error: Optional[BaseException] = None
        try:
            store = Store(self._db_path)
        except BaseException as e:  # noqa: BLE001 建连失败也要让请求方拿到原因
            init_error = e
        try:
            while True:
                job = self._jobs.get()
                if job is None:
                    return
                fn, args, kwargs, box = job
                try:
                    if init_error is not None:
                        raise init_error
                    box["value"] = fn(store, *args, **kwargs)
                except BaseException as e:  # noqa: BLE001 原样转交调用线程
                    box["error"] = e
                finally:
                    box["event"].set()
        finally:
            if store is not None:
                try:
                    store.close()
                except Exception:  # noqa: BLE001 关闭失败不影响进程退出
                    pass

    def call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        box: Dict[str, Any] = {"event": threading.Event(),
                               "value": None, "error": None}
        self._jobs.put((fn, args, kwargs, box))
        box["event"].wait()
        if box["error"] is not None:
            raise box["error"]
        return box["value"]

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._jobs.put(None)
        self._thread.join(timeout=5)


# ============================================================
# 请求解析
# ============================================================
def _body(value: Optional[dict]) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise LongformError("请求体必须是 JSON 对象")
    return value


def _need(body: dict, key: str) -> Any:
    if key not in body:
        raise LongformError(f"请求体缺少字段 `{key}`")
    return body[key]


def _qstr(query: Dict[str, List[str]], key: str) -> Optional[str]:
    values = query.get(key)
    if not values:
        return None
    # 前端清空筛选项时常发 `?level=`；空串按「未提供」处理，
    # 否则会被 Store 当成非法枚举值而报错。
    return values[0] if values[0] != "" else None


def _qint(query: Dict[str, List[str]], key: str) -> Optional[int]:
    raw = _qstr(query, key)
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw)
    except ValueError:
        raise LongformError(f"参数 `{key}` 必须是整数：{raw!r}") from None


def _qbool(query: Dict[str, List[str]], key: str) -> Optional[bool]:
    """解析可切换的布尔查询参数；缺省/空串视作「未提供」。"""
    raw = _qstr(query, key)
    if raw is None:
        return None
    low = raw.strip().lower()
    if low in ("1", "true", "yes", "on"):
        return True
    if low in ("0", "false", "no", "off"):
        return False
    raise LongformError(f"参数 `{key}` 必须是布尔值：{raw!r}")


def _chapter_content(store: Store, pid: str, chapter_n: Optional[int]) -> str:
    n = 0 if chapter_n is None else int(chapter_n)
    chapter = store.chapter_get(pid, n)
    if not chapter or not (chapter.get("content") or "").strip():
        raise LongformError(f"第 {n} 章还没有正文；请传 text 或先保存章节")
    return chapter["content"]


def _int_seg(raw: str, field: str) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise LongformError(f"路径参数 `{field}` 必须是整数：{raw!r}") from None


def _body_str(body: dict, key: str) -> str:
    """可选字符串字段：缺省或 null 视作空串，非字符串交给下游报可读错误。"""
    value = body.get(key)
    return "" if value is None else value


def _body_int(body: dict, key: str) -> int:
    """可选整数字段：缺省或空串视作 0，其余必须是整数。"""
    value = body.get(key)
    if value is None or value == "":
        return 0
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise LongformError(f"参数 `{key}` 必须是整数")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise LongformError(f"参数 `{key}` 必须是整数：{value!r}") from None


# ============================================================
# 路由
# ============================================================
def _dispatch(store: Store, method: str, segs: List[str],
              query: Dict[str, List[str]], body: Optional[dict]) -> Any:
    """API 总路由；返回对象直接序列化为响应 JSON。"""
    if not segs or segs[0] != "api":
        raise LongformError("接口不存在")
    rest = segs[1:]
    if not rest:
        raise LongformError(f"接口不存在：{method} /api")

    head, tail = rest[0], rest[1:]

    if head == "health" and not tail and method == "GET":
        return {"ok": True, "version": VERSION}

    if head == "profiles" and not tail and method == "GET":
        return profiles.list_profiles()

    if head == "settings" and not tail:
        if method == "GET":
            return settings.public_view()
        if method == "PUT":
            return settings.save_settings(_body(body))

    if head == "preferences" and not tail:
        if method == "GET":
            return {"ok": True, "preferences": profiles.get_preferences()}
        if method == "PUT":
            return {"ok": True,
                    "preferences": profiles.set_preferences(_body(body))}
        raise LongformError(f"接口不存在：{method} /api/preferences")

    if head == "studio" and tail == ["templates"] and method == "GET":
        return {"ok": True, "templates": studio_autopilot.list_genres(),
                "preferences": profiles.get_preferences()}

    if head == "llm" and tail == ["generate"] and method == "POST":
        b = _body(body)
        pid = b.get("project")
        if isinstance(pid, str) and pid.strip():
            pid = pid.strip()
            store._require_project(pid)
            if store.config_get(pid, "studio.mode") == "ai-studio":
                prompt = _need(b, "prompt")
                system = b.get("system")
                text = (f"{system}\n\n{prompt}"
                        if isinstance(system, str) and system.strip() else prompt)
                handoff = store.handoff_create(pid, "llm_generate", text,
                                               schema={"type": "text"})
                return {"ok": True, "status": "awaiting_input",
                        "handoff_id": handoff["id"], "prompt": text}
        return llm_client.generate(
            _need(b, "prompt"),
            system=b.get("system"),
            temperature=b.get("temperature"),
            max_tokens=b.get("max_tokens"),
            timeout=b.get("timeout"))

    if head == "ima":
        return _ima(store, method, tail, body)

    if head == "runs":
        return _runs(store, method, tail, body)

    if head == "batches":
        return _batches(store, method, tail, body)

    if head == "handoffs":
        return _handoffs(store, method, tail, body)

    if head != "projects":
        raise LongformError(f"接口不存在：{method} /{'/'.join(segs)}")
    return _projects(store, method, tail, query, body)


def _ima(store: Store, method: str, tail: List[str],
         body: Optional[dict]) -> Any:
    if tail == ["status"] and method == "GET":
        return ima_client.status()
    if tail == ["kbs"] and method == "GET":
        return ima_client.list_kbs()
    if tail == ["search"] and method == "POST":
        b = _body(body)
        return ima_client.search(_need(b, "query"), b.get("limit", 10),
                                 kb_id=b.get("kb_id"))
    if tail == ["import"] and method == "POST":
        b = _body(body)
        return ima_client.import_to_materials(
            store, _need(b, "project"), _need(b, "id"),
            tags=b.get("tags"), chapter=b.get("chapter"),
            content=b.get("content"), title=b.get("title"),
            kb_id=b.get("kb_id"), data=b.get("data"))
    if tail == ["exports", "guide"] and method == "GET":
        return ima_export.guide(store)
    if tail == ["exports", "scan"] and method == "POST":
        b = _body(body)
        return ima_export.scan(
            store, _need(b, "project"), directory=b.get("dir"),
            recursive=bool(b.get("recursive", True)),
            kind=b.get("kind") or "reference",
            tags=b.get("tags"), chapter=b.get("chapter"))
    raise LongformError(f"接口不存在：{method} /api/ima/{'/'.join(tail)}")


def _ima_upload(store: Store, pid: str, body: Optional[dict]) -> dict:
    """浏览器 base64 上传：落临时文件 → IMA 上传 → finally 删除。"""
    store._require_project(pid)
    b = _body(body)
    filename = _need(b, "filename")
    if not isinstance(filename, str) or not filename.strip():
        raise LongformError("参数 `filename` 必须是非空字符串")
    # 只保留 basename：不允许浏览器把路径塞进服务端。
    name = os.path.basename(filename.replace("\\", "/")).strip()
    suffix = os.path.splitext(name)[1].lower()
    if suffix not in _IMA_UPLOAD_SUFFIXES:
        raise LongformError(
            "不支持的文件类型：仅允许 " + " / ".join(_IMA_UPLOAD_SUFFIXES))
    raw = _need(b, "content_base64")
    if not isinstance(raw, str):
        raise LongformError("参数 `content_base64` 必须是 base64 字符串")
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise LongformError("content_base64 不是合法的 base64") from None
    if len(data) > MAX_IMA_UPLOAD_BYTES:
        raise LongformError(
            f"文件过大：{len(data)} 字节，上限 "
            f"{MAX_IMA_UPLOAD_BYTES // (1024 * 1024)}MB")

    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(
                prefix="longform-ima-", suffix=suffix, delete=False) as fh:
            fh.write(data)
            tmp_path = fh.name
        out = ima_client.upload_file(tmp_path, kb_id=b.get("kb_id"))
        result = dict(out) if isinstance(out, dict) else {"ok": False}
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    # 临时路径对前端无意义，且文件已删除；只回传业务字段。
    result.pop("path", None)
    result["filename"] = name
    return result


def _project_ima(store: Store, method: str, pid: str, sub: List[str],
                 body: Optional[dict]) -> Any:
    """项目级 IMA 写入：URL 批量导入与文件上传。"""
    store._require_project(pid)
    if sub == ["import-urls"] and method == "POST":
        b = _body(body)
        return ima_client.import_urls(_need(b, "urls"), kb_id=b.get("kb_id"))
    if sub == ["upload"] and method == "POST":
        return _ima_upload(store, pid, body)
    raise LongformError(
        f"接口不存在：{method} /api/projects/{pid}/ima/{'/'.join(sub)}")


def _runs(store: Store, method: str, tail: List[str],
          body: Optional[dict]) -> Any:
    """``/api/runs/<run_id>`` 及其 resume / cancel 子路由。"""
    if not tail:
        raise LongformError(f"接口不存在：{method} /api/runs")
    run_id = tail[0]
    if len(tail) == 1 and method == "GET":
        return runner.status(store, run_id)
    if len(tail) == 2 and method == "POST":
        b = _body(body)
        if tail[1] == "resume":
            return runner.resume(store, run_id, decision=b.get("decision"))
        if tail[1] == "cancel":
            return runner.cancel(store, run_id)
    raise LongformError(
        f"接口不存在：{method} /api/runs/{'/'.join(tail)}")


def _batches(store: Store, method: str, tail: List[str],
             body: Optional[dict]) -> Any:
    """``/api/batches/<batch_id>`` 及其 resume / cancel 子路由。"""
    if not tail:
        raise LongformError(f"接口不存在：{method} /api/batches")
    batch_id = tail[0]
    if len(tail) == 1 and method == "GET":
        return runner.batch_status(store, batch_id)
    if len(tail) == 2 and method == "POST":
        b = _body(body)
        if tail[1] == "resume":
            return runner.batch_resume(store, batch_id,
                                       decision=b.get("decision"))
        if tail[1] == "cancel":
            return runner.batch_cancel(store, batch_id)
    raise LongformError(
        f"接口不存在：{method} /api/batches/{'/'.join(tail)}")


def _handoffs(store: Store, method: str, tail: List[str],
              body: Optional[dict]) -> Any:
    """``/api/handoffs/<handoff_id>`` 的 submit / cancel 子路由。"""
    if not tail:
        raise LongformError(f"接口不存在：{method} /api/handoffs")
    handoff_id = tail[0]
    if len(tail) == 2 and method == "POST":
        if tail[1] == "submit":
            b = _body(body)
            return studio_autopilot.submit(store, handoff_id, _need(b, "result"))
        if tail[1] == "cancel":
            return studio_autopilot.cancel(store, handoff_id)
    raise LongformError(
        f"接口不存在：{method} /api/handoffs/{'/'.join(tail)}")


def _project_design(store: Store, pid: str, body: Optional[dict]) -> dict:
    """项目立项设计交接单；``/design`` 与 ``/studio/design`` 共用。"""
    b = _body(body)
    return studio_autopilot.start_design(
        store, pid,
        premise=_body_str(b, "premise"),
        genre=_body_str(b, "genre"),
        chapters=_body_int(b, "chapters"),
        target_words=_body_int(b, "target_words"))


def _projects(store: Store, method: str, tail: List[str],
              query: Dict[str, List[str]], body: Optional[dict]) -> Any:
    if not tail:
        if method == "GET":
            return store.project_list()
        if method == "POST":
            b = _body(body)
            name = _need(b, "name")
            profile = b.get("profile")
            if profile:
                profiles.read(profile)      # 不存在的变体在这里就报错
            return store.project_init(
                name, kind=b.get("kind") or "novel",
                config=b.get("config"),
                overwrite=bool(b.get("overwrite", False)),
                profile=profile)
        raise LongformError(f"接口不存在：{method} /api/projects")

    pid = tail[0]
    rest = tail[1:]
    if not rest:
        if method == "GET":
            return {"project": store.project_get(pid),
                    "profile": store.project_get_profile(pid),
                    "stats": store.ledger_stats(pid),
                    "outline": store.outline_stats(pid)}
        if method == "DELETE":
            return store.project_delete(pid, _qstr(query, "confirm"))
        raise LongformError(f"接口不存在：{method} /api/projects/{pid}")

    head, sub = rest[0], rest[1:]
    if head == "config":
        return _config(store, method, pid, sub, body)
    if head == "outline":
        return _outline(store, method, pid, sub, query, body)
    if head == "chapters":
        return _chapters(store, method, pid, sub, query, body)
    if head == "materials":
        return _materials(store, method, pid, sub, query, body)
    if head == "kb":
        return _kb(store, method, pid, sub, query, body)
    if head == "foreshadow":
        return _foreshadow(store, method, pid, sub, query, body)
    if head == "context-trace" and not sub:
        if method != "GET":
            raise LongformError(
                f"接口不存在：{method} /api/projects/{pid}/context-trace")
        chapter_n = _qint(query, "chapter_n")
        if chapter_n is None:
            chapter_n = _current_chapter_n(store, pid)
        if chapter_n is None:
            raise LongformError("还没有任何章节，无法计算设定激活轨迹")
        return packet.packet_trace(store, pid, chapter_n,
                                   _qstr(query, "brief") or "")
    if head == "bible":
        return _bible(store, method, pid, sub, query, body)
    if head == "workbench":
        return _workbench(store, method, pid, sub, body)
    if head == "word-stats" and not sub and method == "GET":
        chapter_n = _qint(query, "chapter_n")
        if chapter_n is None:
            return word_stats.project_word_stats(store, pid)
        return word_stats.chapter_word_stats(store, pid, chapter_n)
    if head == "token-audit" and not sub and method == "GET":
        return token_saver.token_audit(
            store, pid, chapter_n=_qint(query, "chapter_n"),
            lookback=_qint(query, "lookback") or 10)
    if head == "digests" and not sub and method == "POST":
        b = _body(body)
        return token_saver.chapter_digest(
            store, pid, n=None, all_missing=True,
            max_chars=b.get("max_chars", 600),
            save=bool(b.get("save", True)),
            overwrite=False)
    if head == "inspiration" and sub and method == "POST":
        b = _body(body)
        if sub == ["questions"]:
            return inspiration.inspiration_interview(
                store, pid, chapter_n=b.get("chapter_n"),
                theme=b.get("theme", ""), count=b.get("count", 6))
        if sub == ["synthesize"]:
            return inspiration.inspiration_synthesize(
                store, pid, answers=_need(b, "answers"),
                chapter_n=b.get("chapter_n"), theme=b.get("theme", ""),
                save=bool(b.get("save", True)), tags=b.get("tags"))
        raise LongformError(f"接口不存在：{method} /api/projects/{pid}/inspiration")
    if head == "autopilot" and not sub and method == "GET":
        return studio_autopilot.next_task(store, pid)
    if head == "handoffs" and not sub and method == "GET":
        return studio_autopilot.handoffs(store, pid, _qstr(query, "status"))
    if head == "design" and not sub and method == "POST":
        return _project_design(store, pid, body)
    if head == "studio":
        if sub == ["design"] and method == "POST":
            # 与 /design 同义：向导按 /studio/design 调用，老接口保持不变。
            return _project_design(store, pid, body)
        return _studio(store, method, pid, sub, query, body)
    if head == "graph" and not sub and method == "GET":
        return insights.story_graph(store, pid, _qstr(query, "view") or "all",
                                    _qstr(query, "character"))
    if head == "structure" and not sub:
        if method == "GET":
            return insights.structure_check(
                _chapter_content(store, pid, _qint(query, "chapter_n")))
        if method == "POST":
            b = _body(body)
            if b.get("text") is not None:
                return insights.structure_check(b["text"])
            return insights.structure_check(
                _chapter_content(store, pid, b.get("chapter_n")))
        raise LongformError(f"接口不存在：{method} /api/projects/{pid}/structure")
    if head == "voice-cross" and not sub and method == "POST":
        b = _body(body)
        text = b.get("text")
        if text is None:
            text = _chapter_content(store, pid, b.get("chapter_n"))
        report = insights.voice_cross_check(store, pid, text)
        if b.get("save"):
            ch = b.get("chapter_n") or 0
            store.audit_log(pid, ch, report)
        return report
    if head == "ooc" and not sub and method == "POST":
        b = _body(body)
        text = b.get("text")
        if text is None:
            text = _chapter_content(store, pid, b.get("chapter_n"))
        report = insights.ooc_check(store, pid, _need(b, "character"), text)
        if b.get("save"):
            ch = b.get("chapter_n") or 0
            store.audit_log(pid, ch, report)
        return report
    if head == "expectations" and not sub:
        if method == "GET":
            n = _qint(query, "chapter_n")
            return {"ok": True, "stats": store.expectation_stats(pid),
                    "items": store.expectation_list(pid, n),
                    "audit": insights.expectation_audit(store, pid, n)}
        if method == "POST":
            b = _body(body)
            return store.expectation_add(
                pid, b.get("chapter_n"), _need(b, "state"), b.get("note", ""),
                b.get("setup", ""), b.get("trigger", ""),
                b.get("payoff", ""), b.get("payoff_evidence", ""))
        raise LongformError(f"接口不存在：{method} /api/projects/{pid}/expectations")
    if head == "pipeline" and not sub and method == "GET":
        return insights.pipeline_status(store, pid, _qint(query, "chapter_n"))
    if head == "automation" and not sub and method == "GET":
        return _automation(store, pid)
    if head == "automation" and sub == ["batch"] and method == "POST":
        b = _body(body)
        return runner.batch_start(store, pid, _need(b, "chapters"),
                                  b.get("policy"))
    if head == "runs" and not sub:
        if method == "GET":
            limit = _qint(query, "limit")
            return runner.list_runs(store, pid, status=_qstr(query, "status"),
                                    limit=limit if limit else 50)
        if method == "POST":
            b = _body(body)
            return runner.start(store, pid, b.get("policy"))
        raise LongformError(f"接口不存在：{method} /api/projects/{pid}/runs")
    if head == "lint" and not sub and method == "GET":
        n = _qint(query, "chapter_n")
        if n is None:
            n = _current_chapter_n(store, pid)
            if n is None:
                raise LongformError("还没有任何章节，无法执行 L0 检查")
        return lint.lint_chapter(store, pid, n, persist=False)
    if head == "usage" and not sub and method == "GET":
        return store.usage_summary(pid, by=_qstr(query, "by") or "chapter")
    if head == "appeal" and not sub and method == "POST":
        b = _body(body)
        return insights.appeal_pack(store, pid, _need(b, "chapter_n"))
    if head == "appeal" and sub == ["download"] and method == "GET":
        filename = _qstr(query, "file")
        if not filename:
            raise LongformError("缺少 file 参数")
        safe = os.path.basename(filename)
        if safe != filename or not safe.endswith(".zip"):
            raise LongformError("file 必须是申诉包 zip 文件名")
        path = os.path.join(os.path.dirname(os.path.abspath(store.db_path)),
                            "appeals", safe)
        if not os.path.isfile(path):
            raise LongformError("申诉包不存在，请先生成")
        with open(path, "rb") as f:
            data = f.read()
        return _FileResponse(data, "application/zip", safe)
    if head == "facts":
        return _facts(store, method, pid, sub, body)
    if head == "citations":
        return _citations(store, method, pid, sub, query, body)
    if head == "quality":
        return _quality(store, method, pid, sub, body)
    if head == "ima":
        return _project_ima(store, method, pid, sub, body)
    if head == "style":
        # 更具体的前缀优先：/style/distill 等不走键值 CRUD。
        if sub and sub[0] == "distill":
            return _style_distill(store, method, pid, sub, body)
        if sub and sub[0] == "blind-test":
            return _style_blind_test(store, method, pid, sub, body)
        if sub and sub[0] == "profiles":
            return _style_profiles(store, method, pid, sub, body)
        return _style(store, method, pid, sub, body)
    if head == "summaries":
        return _summaries(store, method, pid, sub, query, body)
    if head == "story-state":
        return _story_state(store, method, pid, sub, query, body)
    raise LongformError(f"接口不存在：{method} /api/projects/{pid}/{head}")


def _current_chapter_n(store: Store, pid: str) -> Optional[int]:
    """优先取流水线当前章；该章还没有正文时回退到最新一章。"""
    chapter_n = insights.pipeline_status(store, pid).get("chapter_n")
    if chapter_n is not None and store.chapter_get(pid, chapter_n) is not None:
        return chapter_n
    chapters = store.chapter_list(pid)
    return chapters[-1]["n"] if chapters else None


def _automation(store: Store, pid: str) -> dict:
    """自动化驾驶舱聚合：状态、运行、成本与当前章 L0 检查。"""
    pipe = insights.pipeline_status(store, pid)
    chapter_n = pipe.get("chapter_n")
    chapter = store.chapter_get(pid, chapter_n) if chapter_n is not None else None
    if chapter is None:
        chapters = store.chapter_list(pid)
        chapter_n = chapters[-1]["n"] if chapters else None
        chapter = store.chapter_get(pid, chapter_n) if chapter_n is not None else None
    latest = None
    if chapter is not None:
        latest = {
            "chapter_n": chapter_n,
            # persist=False：驾驶舱只读检查，不能污染流水线持久化的检查结果。
            "lint": lint.lint_chapter(store, pid, chapter_n, persist=False),
        }
    return {
        "project": store.project_get(pid),
        "pipeline": pipe,
        "runs": runner.list_runs(store, pid, limit=20),
        # 批次父运行单独给前端，避免它混进单章 run 详情。
        "batches": [run for run in runner.list_runs(store, pid, limit=50)
                    if run.get("kind") == "batch"][:20],
        "usage": store.usage_summary(pid, by="chapter"),
        "latest": latest,
    }


def _config(store: Store, method: str, pid: str, sub: List[str],
            body: Optional[dict]) -> Any:
    if sub:
        raise LongformError(f"接口不存在：{method} /api/projects/{pid}/config")
    if method == "GET":
        return store.config_get(pid)
    if method == "PUT":
        b = _body(body)
        return store.config_set(pid, _need(b, "key"), _need(b, "value"))
    raise LongformError(f"接口不存在：{method} /api/projects/{pid}/config")


# ============================================================
# AI Studio 主控：设置包 / 提示词 / 导入导出
# ============================================================
def _studio_text(value: Any, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise LongformError(f"参数 `{field}` 必须是字符串")
    if len(value) > MAX_STUDIO_TEXT:
        raise LongformError(f"参数 `{field}` 超过 {MAX_STUDIO_TEXT} 字上限")
    return value


def _studio_get(store: Store, pid: str) -> dict:
    pack = studio_bridge.build_pack(store, pid)
    tools = pack.get("tools") or {}
    groups = list(tools.get("groups") or {})
    if not groups:
        # include_tools=False 时没有工具清单，仍要告诉前端有哪些组可选。
        groups = list(studio_bridge.TOOL_GROUPS)
    return {
        "ok": True,
        "pack": pack,
        "prompt": studio_bridge.pack_prompt(pack, "settings"),
        "groups": groups,
        "studio": pack["studio"],
        "style": pack["style"],
        "config": pack["config"],
    }


def _studio_put(store: Store, pid: str, body: Optional[dict]) -> dict:
    b = _body(body)
    cfg = store.config_get(pid) or {}
    current = cfg.get("studio") if isinstance(cfg, dict) else None
    merged: Dict[str, Any] = dict(current) if isinstance(current, dict) else {}

    if "mode" in b:
        mode = b["mode"]
        if mode not in ("ai-studio", "direct"):
            raise LongformError("参数 `mode` 只允许 ai-studio / direct")
        merged["mode"] = mode
    for field in ("requirements", "notes"):
        if field in b:
            merged[field] = _studio_text(b[field], field)
    if "tool_groups" in b:
        groups = b["tool_groups"]
        if (not isinstance(groups, list)
                or not all(isinstance(item, str) for item in groups)):
            raise LongformError("参数 `tool_groups` 必须是字符串数组")
        unknown = [item for item in groups
                   if item not in studio_bridge.TOOL_GROUPS]
        if unknown:
            raise LongformError("未知工具组：" + "、".join(unknown))
        # 去重但保持顺序；空数组表示「全部」。
        merged["tool_groups"] = list(dict.fromkeys(groups))

    store.config_set(pid, "studio", merged)
    pack = studio_bridge.build_pack(store, pid)
    return {"ok": True, "studio": pack["studio"]}


def _studio_export(store: Store, pid: str,
                   query: Dict[str, List[str]]) -> "_FileResponse":
    fmt = _qstr(query, "format") or "json"
    bundle = studio_bridge.export_bundle(store, pid)
    safe = _safe_filename(pid, "project")
    if fmt == "json":
        return _FileResponse(bundle["json"].encode("utf-8"),
                             "application/json; charset=utf-8",
                             f"{safe}.studio.json")
    if fmt == "prompt":
        return _FileResponse(bundle["prompt"].encode("utf-8"),
                             "text/markdown; charset=utf-8",
                             f"{safe}.studio.prompt.md")
    raise LongformError("参数 `format` 只支持 json / prompt")


def _studio_import(store: Store, pid: str, body: Optional[dict]) -> dict:
    b = _body(body)
    return studio_bridge.import_pack(
        store, pid, _need(b, "pack"), dry_run=bool(b.get("dry_run", False)))


def _studio_prompt(store: Store, pid: str, body: Optional[dict]) -> dict:
    b = _body(body)
    task = b.get("task") or "settings"
    chapter_n = b.get("chapter_n")
    if task == "chapter" and chapter_n is None:
        raise LongformError("task='chapter' 必须提供 chapter_n")
    if chapter_n is not None:
        try:
            chapter_n = int(chapter_n)
        except (TypeError, ValueError):
            raise LongformError("参数 `chapter_n` 必须是整数") from None
    return studio_bridge.build_prompt(store, pid, task, chapter_n=chapter_n)


def _studio(store: Store, method: str, pid: str, sub: List[str],
            query: Dict[str, List[str]], body: Optional[dict]) -> Any:
    if not sub:
        if method == "GET":
            return _studio_get(store, pid)
        if method == "PUT":
            return _studio_put(store, pid, body)
        raise LongformError(f"接口不存在：{method} /api/projects/{pid}/studio")
    if sub == ["view"] and method == "GET":
        # 创作台的数据视图；原 GET /studio 让位给 AI Studio 设置包。
        view = insights.studio_view(store, pid, _qint(query, "chapter_n"))
        # 激活轨迹只是解释性附加信息：算不出来时置 null，不能影响主响应。
        try:
            ctx = view.get("context")
            if not isinstance(ctx, dict):
                ctx = {}
                view["context"] = ctx
            ctx["entity_trace"] = packet.packet_trace(
                store, pid, int(view.get("chapter_n") or 0),
                ctx.get("brief") if isinstance(ctx.get("brief"), str) else "")
        except Exception:  # noqa: BLE001 轨迹失败不阻断创作台渲染
            ctx["entity_trace"] = None
        return view
    if sub == ["export"] and method == "GET":
        return _studio_export(store, pid, query)
    if sub == ["import"] and method == "POST":
        return _studio_import(store, pid, body)
    if sub == ["prompt"] and method == "POST":
        return _studio_prompt(store, pid, body)
    raise LongformError(
        f"接口不存在：{method} /api/projects/{pid}/studio/{'/'.join(sub)}")


def _outline(store: Store, method: str, pid: str, sub: List[str],
             query: Dict[str, List[str]], body: Optional[dict]) -> Any:
    if not sub:
        if method == "GET":
            return store.outline_list(
                pid, level=_qstr(query, "level"),
                parent_id=_qstr(query, "parent_id"),
                status=_qstr(query, "status"))
        if method == "POST":
            b = _body(body)
            return store.outline_set(
                pid, _need(b, "level"),
                node_id=b.get("id"),
                parent_id=b.get("parent_id"),
                title=b.get("title"),
                body=b.get("body", ""),
                status=b.get("status") or "draft",
                seq=b.get("seq", 0),
                chapter_n=b.get("chapter_n"),
                start_chapter=b.get("start_chapter"),
                end_chapter=b.get("end_chapter"),
                data=b.get("data"))
        raise LongformError(f"接口不存在：{method} /api/projects/{pid}/outline")

    node = sub[0]
    if len(sub) == 1:
        if method == "GET":
            return store.outline_get(pid, node, _qint(query, "version"))
        if method == "DELETE":
            return store.outline_delete(pid, node, _qstr(query, "confirm"))
        raise LongformError(
            f"接口不存在：{method} /api/projects/{pid}/outline/{node}")
    if sub[1:] == ["versions"] and method == "GET":
        return store.outline_versions(pid, node)
    raise LongformError(
        f"接口不存在：{method} /api/projects/{pid}/outline/{'/'.join(sub)}")


def _chapters(store: Store, method: str, pid: str, sub: List[str],
              query: Dict[str, List[str]], body: Optional[dict]) -> Any:
    if not sub:
        if method == "GET":
            return store.chapter_list(pid)
        if method == "POST":
            b = _body(body)
            n = _need(b, "n")
            return store.chapter_save(
                pid, n, b.get("title") or f"第{n}章",
                _need(b, "content"), b.get("status") or "draft",
                b.get("data"))
        raise LongformError(f"接口不存在：{method} /api/projects/{pid}/chapters")

    n = _int_seg(sub[0], "n")
    if len(sub) == 1 and method == "GET":
        return store.chapter_get(pid, n, _qint(query, "version"))
    if sub[1:] == ["versions"] and method == "GET":
        return store.chapter_versions(pid, n)
    if sub[1:] == ["diff"] and method == "GET":
        return store.chapter_diff(pid, n, _qint(query, "a"), _qint(query, "b"))
    if sub[1:] == ["patch"] and method == "POST":
        b = _body(body)
        return prose_patch.chapter_patch(
            store, pid, n, _need(b, "find"), _need(b, "replace"),
            occurrence=b.get("occurrence", 1),
            all_matches=b.get("all_matches", False),
            preview=b.get("preview", False),
            expected_version=b.get("expected_version"),
            status=b.get("status"),
        )
    if sub[1:] == ["digest"] and method == "POST":
        b = _body(body)
        return token_saver.chapter_digest(
            store, pid, n=n, max_chars=b.get("max_chars", 600),
            save=bool(b.get("save", False)),
            overwrite=bool(b.get("overwrite", False)))
    if sub[1:] == ["revision-plan"] and method == "POST":
        b = _body(body)
        return revision_advisor.revision_plan(
            store, pid, n, max_items=b.get("max_items", 8))
    raise LongformError(
        f"接口不存在：{method} /api/projects/{pid}/chapters/{'/'.join(sub)}")


def _materials(store: Store, method: str, pid: str, sub: List[str],
               query: Dict[str, List[str]], body: Optional[dict]) -> Any:
    if not sub:
        kind = _qstr(query, "kind")
        tag = _qstr(query, "tag")
        chapter = _qint(query, "chapter")
        limit = _qint(query, "limit")
        category = _qstr(query, "category")
        if method == "GET":
            q = _qstr(query, "query")
            if q:
                expand = _qbool(query, "expand_synonyms")
                return kb.search(store, pid, q, kind=kind, tag=tag,
                                 chapter=chapter, limit=limit,
                                 category=category,
                                 expand_synonyms=True if expand is None else expand)
            if category:
                # 没有关键词时按素材地图过滤全量；先取宽上限再切片，避免分类被 LIMIT 截断。
                rows = store.material_list(pid, kind=kind, tag=tag,
                                           chapter=chapter,
                                           limit=limit or 500)
                rows = [m for m in rows
                        if isinstance(m.get("data_json"), dict)
                        and m["data_json"].get("category") == category]
                return rows[:limit] if limit else rows
            return store.material_list(pid, kind=kind, tag=tag,
                                       chapter=chapter, limit=limit)
        if method == "POST":
            b = _body(body)
            allowed = ("title", "content", "kind", "source", "source_kind",
                       "tags", "chapter", "data")
            kwargs = {k: b[k] for k in allowed if k in b}
            kwargs["title"] = _need(b, "title")
            return store.material_add(pid, **kwargs)
        raise LongformError(f"接口不存在：{method} /api/projects/{pid}/materials")

    if sub == ["categories"] and method == "GET":
        return {"ok": True,
                "categories": kb.MATERIAL_CATEGORIES,
                "synonyms": kb.SYNONYMS}

    if sub == ["network"] and method == "GET":
        return insights.material_network(store, pid)
    if sub == ["audit"] and method == "GET":
        return insights.material_audit(store, pid, _qstr(query, "level"))

    mid = sub[0]
    if len(sub) == 1:
        if method == "GET":
            return store.material_get(pid, mid)
        if method in ("PATCH", "PUT"):
            b = _body(body)
            allowed = ("title", "content", "tags", "data")
            kwargs = {k: b[k] for k in allowed if k in b}
            return store.material_update(pid, mid, **kwargs)
        if method == "DELETE":
            return store.material_delete(pid, mid, _qstr(query, "confirm"))
    raise LongformError(
        f"接口不存在：{method} /api/projects/{pid}/materials/{'/'.join(sub)}")


def _kb(store: Store, method: str, pid: str, sub: List[str],
        query: Dict[str, List[str]], body: Optional[dict]) -> Any:
    if sub == ["search"] and method == "GET":
        q = _qstr(query, "q")
        if not q:
            raise LongformError("缺少查询参数 `q`")
        return kb.search(store, pid, q, kind=_qstr(query, "kind"),
                         tag=_qstr(query, "tag"),
                         chapter=_qint(query, "chapter"),
                         limit=_qint(query, "limit"))
    if sub == ["import"] and method == "POST":
        b = _body(body)
        # Web 层不接受本地路径：浏览器上传的内容必须已经在 body 里。
        if b.get("path") or b.get("dir"):
            raise LongformError(
                "Web 导入仅支持 content；本地 path/dir 请用 "
                "`python cli.py kb import <path>`")
        content = _need(b, "content")
        return kb.import_text(
            store, pid, b.get("title") or "未命名素材", content,
            kind=b.get("kind") or "reference",
            source=b.get("source", ""),
            source_kind=b.get("source_kind"),
            tags=b.get("tags"),
            chapter=b.get("chapter"))
    raise LongformError(f"接口不存在：{method} /api/projects/{pid}/kb/{'/'.join(sub)}")


def _foreshadow(store: Store, method: str, pid: str, sub: List[str],
                query: Dict[str, List[str]], body: Optional[dict]) -> Any:
    if not sub:
        if method == "GET":
            return store.foreshadow_list(pid, _qstr(query, "status"))
        if method == "POST":
            b = _body(body)
            return store.foreshadow_add(
                pid, _need(b, "text"), _need(b, "planted_chapter"),
                b.get("due_chapter"))
        raise LongformError(f"接口不存在：{method} /api/projects/{pid}/foreshadow")
    if len(sub) == 1 and method == "PUT":
        b = _body(body)
        return store.foreshadow_resolve(
            pid, sub[0], _need(b, "chapter"), b.get("status") or "resolved")
    raise LongformError(
        f"接口不存在：{method} /api/projects/{pid}/foreshadow/{'/'.join(sub)}")


def _facts(store: Store, method: str, pid: str, sub: List[str],
           body: Optional[dict]) -> Any:
    if not sub:
        if method == "GET":
            return store.fact_list(pid)
        if method == "POST":
            b = _body(body)
            return store.fact_add(pid, _need(b, "claim"),
                                  source=b.get("source"),
                                  chapter=b.get("chapter"))
        raise LongformError(f"接口不存在：{method} /api/projects/{pid}/facts")
    if len(sub) == 2 and sub[1] == "verify" and method == "POST":
        return _fact_verify(store, pid, sub[0], body)
    if len(sub) == 1 and method == "PUT":
        b = _body(body)
        return store.fact_set_status(pid, sub[0], _need(b, "status"),
                                     b.get("note"))
    raise LongformError(
        f"接口不存在：{method} /api/projects/{pid}/facts/{'/'.join(sub)}")


def _fact_verify(store: Store, pid: str, fid: str,
                 body: Optional[dict]) -> dict:
    """把事实的 claim/source 登记成一条引证并立即验真；不改事实状态。"""
    b = _body(body)
    fact = next((item for item in store.fact_list(pid) if item.get("id") == fid),
                None)
    if fact is None:
        raise LongformError(f"事实条目不存在：{fid}（项目 {pid}）")
    source = str(fact.get("source") or "").strip()
    if not source:
        raise LongformError(
            "事实缺少来源，无法验真；请先补 source（素材 id / ch:N / 原文）")
    chapter_n = fact.get("chapter")
    if chapter_n is None or int(chapter_n) < 1:
        # 事实允许 chapter=0（未关联章节）；引证表要求 >=1，回退到最新章。
        chapter_n = _current_chapter_n(store, pid)
    added = store.citation_add(pid, chapter_n, fact["claim"], source=source,
                               locator=str(fact.get("id") or ""))
    result = citations.verify(store, pid, added["id"],
                              threshold=b.get("threshold", citations.DEFAULT_THRESHOLD))
    return {"ok": True, "fact": fact, "citation": store.citation_get(pid, added["id"]),
            "verification": result}


def _citations(store: Store, method: str, pid: str, sub: List[str],
               query: Dict[str, List[str]], body: Optional[dict]) -> Any:
    if not sub:
        if method == "GET":
            return store.citation_list(
                pid, chapter_n=_qint(query, "chapter_n"),
                verified=_qbool(query, "verified"),
                limit=_qint(query, "limit"))
        if method == "POST":
            b = _body(body)
            return store.citation_add(
                pid, b.get("chapter_n"), _need(b, "claim"),
                source=_body_str(b, "source"),
                locator=_body_str(b, "locator"))
        raise LongformError(f"接口不存在：{method} /api/projects/{pid}/citations")
    if len(sub) == 2 and sub[1] == "verify" and method == "POST":
        b = _body(body)
        return citations.verify(
            store, pid, sub[0],
            threshold=b.get("threshold", citations.DEFAULT_THRESHOLD))
    raise LongformError(
        f"接口不存在：{method} /api/projects/{pid}/citations/{'/'.join(sub)}")


def _style(store: Store, method: str, pid: str, sub: List[str],
           body: Optional[dict]) -> Any:
    if sub:
        raise LongformError(f"接口不存在：{method} /api/projects/{pid}/style")
    if method == "GET":
        return store.style_get(pid)
    if method == "PUT":
        b = _body(body)
        return store.style_set(pid, _need(b, "key"), _need(b, "value"))
    raise LongformError(f"接口不存在：{method} /api/projects/{pid}/style")


def _latest_chapter(store: Store, pid: str) -> Tuple[Optional[int], Optional[dict]]:
    """与自动化驾驶舱一致：先取流水线当前章，没有正文时回退到最新章。"""
    chapter_n = insights.pipeline_status(store, pid).get("chapter_n")
    chapter = store.chapter_get(pid, chapter_n) if chapter_n is not None else None
    if chapter is None:
        chapters = store.chapter_list(pid)
        chapter_n = chapters[-1]["n"] if chapters else None
        chapter = store.chapter_get(pid, chapter_n) if chapter_n is not None else None
    return chapter_n, chapter


def _quality(store: Store, method: str, pid: str, sub: List[str],
             body: Optional[dict]) -> Any:
    if not sub and method == "GET":
        chapter_n, _chapter = _latest_chapter(store, pid)
        latest = None
        if chapter_n is not None:
            # persist=False：质量页只读，不能污染流水线持久化的检查结果。
            latest = {"chapter_n": chapter_n,
                      "lint": lint.lint_chapter(store, pid, chapter_n,
                                                persist=False)}
        return {
            "ok": True,
            "loops": store.loop_list(pid, limit=20),
            "profiles": style_distill.get(store, pid)["profiles"],
            "latest": latest,
        }
    if sub == ["loop"] and method == "POST":
        b = _body(body)
        return quality_loop.start(
            store, pid, b.get("chapter_n"),
            max_rounds=b.get("max_rounds", 2),
            threshold=b.get("threshold", quality_loop.DEFAULT_THRESHOLD),
            apply=b.get("apply", False))
    if len(sub) == 2 and sub[0] == "loops" and method == "GET":
        return quality_loop.report(store, pid, loop_id=sub[1])
    if sub == ["meta"] and method == "POST":
        b = _body(body)
        return quality_loop.meta(store, pid, window=b.get("window", 10))
    if sub == ["meta", "apply"] and method == "POST":
        b = _body(body)
        return quality_loop.meta_apply(store, pid, _need(b, "changes"),
                                       confirm=bool(b.get("confirm", False)))
    raise LongformError(
        f"接口不存在：{method} /api/projects/{pid}/quality"
        + ("/" + "/".join(sub) if sub else ""))


def _style_distill(store: Store, method: str, pid: str, sub: List[str],
                   body: Optional[dict]) -> Any:
    if sub == ["distill"] and method == "POST":
        b = _body(body)
        return style_distill.distill(
            store, pid, texts=b.get("texts"), chapter_ns=b.get("chapter_ns"),
            name=b.get("name") or "默认画像")
    raise LongformError(
        f"接口不存在：{method} /api/projects/{pid}/style/{'/'.join(sub)}")


def _style_blind_test(store: Store, method: str, pid: str, sub: List[str],
                      body: Optional[dict]) -> Any:
    if sub == ["blind-test"] and method == "POST":
        b = _body(body)
        return style_distill.blind_test(
            store, pid,
            reference_texts=b.get("reference_texts"),
            candidate_texts=b.get("candidate_texts"),
            reference_chapter_ns=b.get("reference_chapter_ns"),
            candidate_chapter_ns=b.get("candidate_chapter_ns"),
            profile_id=b.get("profile_id"),
            threshold=b.get("threshold", 75))
    raise LongformError(
        f"接口不存在：{method} /api/projects/{pid}/style/{'/'.join(sub)}")


def _style_profiles(store: Store, method: str, pid: str, sub: List[str],
                    body: Optional[dict]) -> Any:
    if len(sub) == 1 and method == "GET":
        return style_distill.get(store, pid)
    if len(sub) == 2 and method == "GET":
        return style_distill.get(store, pid, sub[1])
    if len(sub) == 3 and sub[2] == "apply" and method == "POST":
        _body(body)
        return style_distill.apply(store, pid, sub[1])
    raise LongformError(
        f"接口不存在：{method} /api/projects/{pid}/style/{'/'.join(sub)}")


def _summaries(store: Store, method: str, pid: str, sub: List[str],
               query: Dict[str, List[str]], body: Optional[dict]) -> Any:
    if not sub and method == "GET":
        return store.summary_list(pid)
    if len(sub) == 1:
        n = _int_seg(sub[0], "n")
        if method == "PUT":
            b = _body(body)
            return store.summary_set(pid, n, _need(b, "summary"))
        if method == "DELETE":
            return store.summary_delete(pid, n, _qstr(query, "confirm"))
    raise LongformError(
        f"接口不存在：{method} /api/projects/{pid}/summaries/{'/'.join(sub)}")


def _story_state(store: Store, method: str, pid: str, sub: List[str],
                 query: Dict[str, List[str]], body: Optional[dict]) -> Any:
    if not sub and method == "GET":
        return store.story_state_list(pid)
    if len(sub) == 1:
        key = sub[0]
        if method == "PUT":
            b = _body(body)
            return store.story_state_set(pid, key, _need(b, "value"))
        if method == "DELETE":
            return store.story_state_delete(pid, key, _qstr(query, "confirm"))
    raise LongformError(
        f"接口不存在：{method} /api/projects/{pid}/story-state/"
        + "/".join(sub))


# ============================================================
# 人物与设定（bible）与叙事工作台
# ============================================================
def _bible(store: Store, method: str, pid: str, sub: List[str],
           query: Dict[str, List[str]], body: Optional[dict]) -> Any:
    if not sub:
        if method == "GET":
            return store.bible_list(pid, kind=_qstr(query, "kind"))
        if method == "POST":
            b = _body(body)
            return store.bible_upsert(
                pid, _need(b, "kind"), _need(b, "name"), b.get("data") or {})
        raise LongformError(f"接口不存在：{method} /api/projects/{pid}/bible")
    if len(sub) >= 3 and sub[-1] == "meta":
        kind = urllib.parse.unquote(sub[0])
        name = urllib.parse.unquote("/".join(sub[1:-1]))
        if method == "GET":
            return store.entity_meta_get(pid, kind, name)
        if method == "PUT":
            b = _body(body)
            return store.entity_meta_set(
                pid, kind, name,
                anchors=b.get("anchors"),
                priority=b.get("priority"),
                first_seen_chapter=b.get("first_seen_chapter"))
        raise LongformError(
            f"接口不存在：{method} /api/projects/{pid}/bible/{'/'.join(sub)}")
    if len(sub) >= 2 and method == "GET":
        kind = urllib.parse.unquote(sub[0])
        name = urllib.parse.unquote("/".join(sub[1:]))
        return store.bible_get(pid, kind, name)
    raise LongformError(
        f"接口不存在：{method} /api/projects/{pid}/bible/{'/'.join(sub)}")


def _workbench(store: Store, method: str, pid: str, sub: List[str],
               body: Optional[dict]) -> Any:
    if not sub:
        if method == "GET":
            return {"ok": True,
                    "roles": narrative.role_deck()["roles"],
                    "tricks": narrative.trick_deck()["tricks"],
                    "rules": narrative.rules_summary()}
        raise LongformError(f"接口不存在：{method} /api/projects/{pid}/workbench")

    if sub == ["compliance"] and method == "POST":
        b = _body(body)
        report = narrative.compliance_check(_need(b, "text"))
        report["project"] = pid
        if b.get("save"):
            chapter_n = b.get("chapter_n", 0)
            report["chapter_n"] = chapter_n
            report["audit_id"] = store.audit_log(pid, chapter_n, report)["id"]
        return report

    if sub == ["voice"] and method == "POST":
        b = _body(body)
        character = _need(b, "character")
        data = store.bible_get(pid, "character", character)
        if not isinstance(data, dict):
            raise LongformError(
                f"未找到人物卡「{character}」；请先在“人物与设定”里建卡并填写语言指纹")
        profile = dict(data)
        profile.setdefault("name", character)
        others = [item for item in store.bible_list(pid, "character")
                  if item.get("name") != character]
        report = narrative.voice_check(profile, _need(b, "text"), others)
        report["project"] = pid
        if b.get("save"):
            chapter_n = b.get("chapter_n", 0)
            report["chapter_n"] = chapter_n
            report["audit_id"] = store.audit_log(pid, chapter_n, report)["id"]
        return report

    if sub == ["roles"] and method == "GET":
        return narrative.role_deck()
    if sub == ["tricks"] and method == "GET":
        return narrative.trick_deck()

    if sub == ["role-prompt"] and method == "POST":
        b = _body(body)
        return narrative.build_role_prompt(
            _need(b, "role"), _need(b, "text"), b.get("context", ""))

    if sub == ["trick-prompt"] and method == "POST":
        b = _body(body)
        return narrative.build_trick_prompt(
            _need(b, "trick"), _need(b, "text"), b.get("context", ""))

    if sub == ["review-chain"] and method == "POST":
        b = _body(body)
        return narrative.build_review_chain_prompt(
            _need(b, "text"), b.get("context", ""),
            b.get("role", "structure-editor"))

    if sub == ["ai-declaration"] and method == "GET":
        proj = store.project_get(pid) or {}
        return narrative.ai_declaration(
            proj.get("name") or pid, pid, store.ledger_stats(pid))

    raise LongformError(
        f"接口不存在：{method} /api/projects/{pid}/workbench/{'/'.join(sub)}")


# ============================================================
# HTTP 处理
class _FileResponse:
    """让路由可以返回二进制下载，而不走 JSON 序列化。"""

    def __init__(self, data: bytes, content_type: str, filename: str):
        self.data = data
        self.content_type = content_type
        self.filename = filename


def _safe_filename(name: str, fallback: str = "download") -> str:
    """把任意项目 id 压成只含 ASCII 的下载文件名，避免头部编码失败。"""
    keep = []
    for ch in str(name):
        if (("a" <= ch <= "z") or ("A" <= ch <= "Z")
                or ("0" <= ch <= "9") or ch in "._-"):
            keep.append(ch)
        else:
            keep.append("_")
    out = "".join(keep).strip("._")
    return out or fallback


# ============================================================
class _Handler(BaseHTTPRequestHandler):
    server_version = "LongformWeb/" + VERSION
    protocol_version = "HTTP/1.1"

    # -------------------------------------------------------- 基础响应
    def _send_bytes(self, status: int, payload: bytes, content_type: str,
                    extra_headers: Optional[Dict[str, str]] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_json(self, status: int, payload: Any) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, data, "application/json; charset=utf-8")

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """只记录方法与路径；query 可能含搜索词或令牌，绝不落日志。"""
        path = urllib.parse.urlsplit(getattr(self, "path", "") or "").path
        try:
            sys.stderr.write(f"{self.command or '-'} {path}\n")
            sys.stderr.flush()
        except Exception:  # noqa: BLE001 日志失败不能影响响应
            pass

    # -------------------------------------------------------- 请求体
    def _read_json(self, limit: int = MAX_BODY_BYTES) -> Optional[dict]:
        raw_len = self.headers.get("Content-Length")
        if raw_len is None:
            return None
        try:
            length = int(raw_len.strip())
        except (TypeError, ValueError):
            raise LongformError("Content-Length 不是合法整数") from None
        if length <= 0:
            return None
        if length > limit:
            raise LongformError(
                f"请求体过大（上限 {limit // (1024 * 1024)}MB）")
        raw = self.rfile.read(length)
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise LongformError("请求体不是合法 JSON") from None
        if not isinstance(obj, dict):
            raise LongformError("请求体必须是 JSON 对象")
        return obj

    # -------------------------------------------------------- 鉴权
    def _authorized(self) -> bool:
        expected = getattr(self.server, "token", "") or ""
        given = self.headers.get("X-Longform-Token") or ""
        if not expected or not given:
            return False
        try:
            return secrets.compare_digest(given, expected)
        except TypeError:  # 非 ASCII 头值
            return False

    # -------------------------------------------------------- 静态资源
    def _serve_static(self, path: str) -> None:
        rel = urllib.parse.unquote(path.lstrip("/"))
        root = os.path.realpath(WEBUI_DIR)
        target = root if rel == "" else os.path.realpath(os.path.join(root, rel))
        if target != root and not target.startswith(root + os.sep):
            self._send_json(404, {"ok": False, "error": "资源不存在"})
            return
        if os.path.isdir(target):
            target = os.path.join(target, "index.html")
        if os.path.isfile(target):
            try:
                with open(target, "rb") as f:
                    data = f.read()
            except OSError:
                self._send_json(404, {"ok": False, "error": "资源不存在"})
                return
            ext = os.path.splitext(target)[1].lstrip(".").lower()
            self._send_bytes(200, data, _MIME.get(ext, "application/octet-stream"))
            return
        if rel in ("", "index.html"):
            self._send_bytes(200, _PLACEHOLDER_HTML.encode("utf-8"),
                             "text/html; charset=utf-8")
            return
        self._send_json(404, {"ok": False, "error": "资源不存在"})

    # -------------------------------------------------------- 路由入口
    def _handle(self, method: str) -> None:
        try:
            split = urllib.parse.urlsplit(self.path)
        except ValueError:
            self._send_json(400, {"ok": False, "error": "请求路径不合法"})
            return

        path = split.path or "/"
        query = urllib.parse.parse_qs(split.query, keep_blank_values=True)

        if path == "/api" or path.startswith("/api/"):
            if not self._authorized():
                self._send_json(401, {"ok": False, "error": "unauthorized"})
                return
            try:
                limit = MAX_BODY_BYTES
                if path.endswith("/ima/upload"):
                    # 仅上传接口放宽，避免 base64 膨胀挡住 10MB 上限。
                    limit = MAX_IMA_UPLOAD_REQUEST_BYTES
                body = (self._read_json(limit)
                        if method in ("POST", "PUT", "PATCH") else None)
                segs = [urllib.parse.unquote(part)
                        for part in path.split("/") if part]
                result = self.server.bridge.call(  # type: ignore[attr-defined]
                    _dispatch, method, segs, query, body)
            except LongformError as e:
                self._send_json(400, {"ok": False, "error": str(e)})
            except Exception:  # noqa: BLE001 兜底：不向客户端泄露细节
                sys.stderr.write(traceback.format_exc(limit=6))
                sys.stderr.flush()
                self._send_json(500, {"ok": False, "error": "服务器内部错误"})
            else:
                if isinstance(result, _FileResponse):
                    quoted = urllib.parse.quote(result.filename)
                    ascii_name = _safe_filename(result.filename)
                    headers = {
                        "Content-Disposition":
                            f"attachment; filename=\"{ascii_name}\"; "
                            f"filename*=UTF-8''{quoted}",
                    }
                    self._send_bytes(200, result.data, result.content_type, headers)
                else:
                    self._send_json(200, result)
            return

        if method != "GET":
            self._send_json(404, {"ok": False,
                                  "error": f"接口不存在：{method} {path}"})
            return
        self._serve_static(path)

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._handle("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")


class _ThreadingServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    bridge: Optional[_StoreBridge] = None
    token: str = ""

    def server_close(self) -> None:
        try:
            super().server_close()
        finally:
            if self.bridge is not None:
                self.bridge.close()
                self.bridge = None


# ============================================================
# 对外 API
# ============================================================
def create_server(host: str = "127.0.0.1", port: int = 8765,
                  token: Optional[str] = None,
                  store: Optional[Store] = None) -> Tuple[ThreadingHTTPServer, str]:
    """创建服务实例，返回 ``(httpd, token)``；不启动事件循环。

    ``store`` 为 None 时用 ``server.get_store()`` 的数据库路径；传入的 Store
    只取其 ``db_path``——请求实际由服务内部的桥线程持有连接执行（sqlite3
    连接不能跨线程使用）。
    """
    if host in _WILDCARD_HOSTS:
        raise ValueError("拒绝监听通配地址：本服务只允许绑定本机回环地址")

    if store is None:
        store = app_server.get_store()
    tok = token or secrets.token_urlsafe(24)

    bridge = _StoreBridge(store.db_path)
    try:
        httpd = _ThreadingServer((host, port), _Handler)
    except OSError:
        bridge.close()
        raise
    httpd.bridge = bridge
    httpd.token = tok
    return httpd, tok


def run(host: str = "127.0.0.1", port: int = 8765,
        open_browser: bool = True,
        store: Optional[Store] = None) -> int:
    """启动服务并阻塞，直到 Ctrl+C。"""
    httpd, token = create_server(host=host, port=port, store=store)
    actual_port = httpd.server_address[1]
    url = f"http://{host}:{actual_port}/?token={token}"
    print(f"叙事工作台已启动：{url}", flush=True)
    print("按 Ctrl+C 停止。", flush=True)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 没有图形环境时不该阻塞启动
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。", flush=True)
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(run())
