"""IMA 知识库适配器（真实 OpenAPI + 本地导出兜底）。

两条通道并存：

1. **官方 OpenAPI**（默认）：`https://ima.qq.com/openapi/wiki/v1/*`，
   凭证优先读设置，其次读环境变量，最后读 `~/.config/ima/{client_id,api_key}`。
2. **旧版自定义 HTTP 桩**：当设置里显式给了非官方 `base_url` 时走原来的
   `GET /search`、`GET /items/<id>` 契约，保持既有测试与私有网关兼容。

订阅知识库的正文读取受 IMA 产品权限限制，`get_media_info` 会返回
`220030`。这里不做任何绕过：`fetch` 会明确返回 `catalog_only=true`，
`import_to_materials` 只落一条“题录卡”（标题 + 来源 + media_id），
正文请走 IMA 客户端导出，再由 `ima_export.scan` 导入。
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from settings import resolve_ima
from store import (
    MAX_MATERIAL_LEN,
    MAX_SEARCH_LEN,
    MAX_SEARCH_LIMIT,
    MAX_TITLE_LEN,
    LongformError,
    _as_int,
    _require_str,
)

_UNCONFIGURED_NOTE = "未配置 IMA；可继续使用本地知识库"
_ERROR_BODY_CHARS = 300
_MAX_TIMEOUT = 600
_MIN_TIMEOUT = 1
_DEFAULT_LIMIT = 10
_OFFICIAL_BASE = "https://ima.qq.com"
_API_PREFIX = "/openapi/wiki/v1/"
_CRED_DIR = os.path.join(os.path.expanduser("~"), ".config", "ima")
_MAX_AUTO_KBS = 8
_MAX_OPENAPI_TEXT = 20_000
# 写入侧限制：URL 批量导入与文件上传都走官方 OpenAPI。
_MAX_IMPORT_URLS = 50
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_DEFAULT_MEDIA_TYPE = 11
_COS_TIMEOUT = 600


# ============================================================
# 通用
# ============================================================
def _s(value: Any) -> str:
    return value if isinstance(value, str) else ("" if value is None else str(value))


def _cfg_timeout(cfg: Dict[str, Any]) -> int:
    return _as_int(cfg.get("timeout"), _MIN_TIMEOUT,
                   minimum=_MIN_TIMEOUT, maximum=_MAX_TIMEOUT)


def _read_cred_file(name: str) -> str:
    path = os.path.join(_CRED_DIR, name)
    try:
        with open(path, encoding="utf-8-sig") as f:
            return f.read().strip()
    except OSError:
        return ""


def _credentials(cfg: Dict[str, Any]) -> Dict[str, Any]:
    if cfg.get("_explicit_off") or not cfg.get("enabled"):
        return {"client_id": "", "api_key": "", "configured": False,
                "source": ""}
    client_id = (_s(cfg.get("client_id")) or
                 os.environ.get("IMA_CLIENT_ID") or
                 os.environ.get("IMA_OPENAPI_CLIENTID") or
                 _read_cred_file("client_id"))
    api_key = (_s(cfg.get("api_key")) or
               os.environ.get("IMA_API_KEY") or
               os.environ.get("IMA_OPENAPI_APIKEY") or
               _read_cred_file("api_key"))
    source = "settings"
    if not _s(cfg.get("api_key")) and api_key:
        source = "env/file"
    return {
        "client_id": client_id.strip().replace("\ufeff", ""),
        "api_key": api_key.strip().replace("\ufeff", ""),
        "configured": bool(client_id.strip().replace("\ufeff", "")
                           and api_key.strip().replace("\ufeff", "")),
        "source": source,
    }


def _is_legacy(cfg: Dict[str, Any]) -> bool:
    base = _s(cfg.get("base_url")).rstrip("/")
    return bool(base) and base != _OFFICIAL_BASE


def _base_url(cfg: Dict[str, Any]) -> str:
    return (_s(cfg.get("base_url")) or _OFFICIAL_BASE).rstrip("/")


def _resolve_cfg(settings: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    cfg = resolve_ima(settings)
    # 只有调用方显式传了 enabled=false 才视为“关掉 IMA”；
    # 默认配置里的 enabled=false 不应挡住本机已配置的 ~/.config/ima 凭证。
    explicit = (isinstance(settings, dict)
                and isinstance(settings.get("ima"), dict)
                and settings["ima"].get("enabled") is False)
    cfg["_explicit_off"] = bool(explicit)
    return cfg


# ============================================================
# 旧版自定义 HTTP 桩（保持向后兼容）
# ============================================================
def _request_json(url: str, cfg: Dict[str, Any]) -> Any:
    headers = {"Accept": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = "Bearer " + cfg["api_key"]
    req = urllib.request.Request(url, headers=headers, method="GET")
    timeout = _cfg_timeout(cfg)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        try:
            body = (e.read() or b"")[:_ERROR_BODY_CHARS].decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            body = ""
        raise LongformError(f"IMA 服务返回 HTTP {e.code}：{body}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", None) or e
        raise LongformError(f"IMA 请求超时或网络不可达：{reason}") from None

    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        raise LongformError(
            f"IMA 返回的不是合法 JSON：{raw[:200].decode('utf-8', 'replace')}"
        ) from None


def _norm_search_item(raw: Any) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    return {
        "id": _s(raw.get("id")),
        "title": _s(raw.get("title")),
        "snippet": _s(raw.get("snippet")),
        "source": _s(raw.get("source")),
    }


def _search_items(payload: Any) -> Optional[List[dict]]:
    if isinstance(payload, list):
        raw_items = payload
    elif isinstance(payload, dict):
        raw_items = None
        for key in ("items", "data"):
            if isinstance(payload.get(key), list):
                raw_items = payload[key]
                break
        if raw_items is None:
            return None
    else:
        return None
    items = [_norm_search_item(it) for it in raw_items]
    return [it for it in items if it is not None]


def _legacy_search(query: str, limit: int, cfg: Dict[str, Any]) -> dict:
    url = (f"{_base_url(cfg)}/search?"
           + urllib.parse.urlencode({"q": query, "limit": limit}))
    payload = _request_json(url, cfg)
    items = _search_items(payload)
    if items is None:
        raise LongformError(
            "IMA 搜索返回结构无法识别（期望数组或 {items: [...]} / {data: [...]}）")
    return {"ok": True, "configured": True, "items": items[:limit], "note": ""}


def _legacy_fetch(item_id: str, cfg: Dict[str, Any]) -> dict:
    url = f"{_base_url(cfg)}/items/{urllib.parse.quote(item_id, safe='')}"
    payload = _request_json(url, cfg)
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        payload = payload["data"]
    if not isinstance(payload, dict):
        raise LongformError("IMA 条目返回结构无法识别（期望 JSON 对象）")
    item = {
        "id": _s(payload.get("id")) or item_id,
        "title": _s(payload.get("title")),
        "content": _s(payload.get("content")),
        "source": _s(payload.get("source")),
    }
    return {"ok": True, "configured": True, "item": item}


# ============================================================
# 官方 OpenAPI
# ============================================================
def _openapi_post(path: str, body: Dict[str, Any], cfg: Dict[str, Any]) -> dict:
    cred = _credentials(cfg)
    if not cred["configured"]:
        raise LongformError(
            "未配置 IMA OpenAPI 凭证：请设置 IMA_CLIENT_ID / IMA_API_KEY，"
            "或把 client_id、api_key 写入 ~/.config/ima/")
    url = _base_url(cfg) + _API_PREFIX + path.lstrip("/")
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
        "ima-openapi-clientid": cred["client_id"],
        "ima-openapi-apikey": cred["api_key"],
    }
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    timeout = _cfg_timeout(cfg)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        try:
            body_text = (e.read() or b"")[:_ERROR_BODY_CHARS].decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            body_text = ""
        raise LongformError(f"IMA OpenAPI 返回 HTTP {e.code}：{body_text}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", None) or e
        raise LongformError(f"IMA 请求超时或网络不可达：{reason}") from None
    try:
        out = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        raise LongformError("IMA OpenAPI 返回的不是合法 JSON") from None
    if not isinstance(out, dict):
        raise LongformError("IMA OpenAPI 返回结构不是对象")
    return out


def _openapi_data(payload: dict) -> dict:
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def _openapi_error(payload: dict, action: str) -> None:
    code = payload.get("code")
    if code == 0:
        return
    msg = _s(payload.get("msg")) or "未知错误"
    raise LongformError(f"IMA {action}失败（code={code}）：{msg}")


def _media_url_text(url: str, headers: Optional[Dict[str, str]]) -> str:
    if not url:
        return ""
    req_headers = {"Accept": "text/html, text/plain, */*",
                   "User-Agent": "Mozilla/5.0 NarrativeWorkbench/1.6"}
    for key, value in (headers or {}).items():
        req_headers[str(key)] = str(value)
    try:
        req = urllib.request.Request(url, headers=req_headers, method="GET")
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read(5_000_000)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return ""
    text = raw.decode("utf-8", "replace")
    if "<html" in text.lower() or "<body" in text.lower():
        text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", text)
        text = re.sub(r"(?i)<br\s*/?>", "\n", text)
        text = re.sub(r"(?i)</p>", "\n\n", text)
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:_MAX_OPENAPI_TEXT]


def list_kbs(settings: Optional[Dict[str, Any]] = None) -> dict:
    cfg = _resolve_cfg(settings)
    if _is_legacy(cfg):
        return {"ok": True, "configured": True, "items": [],
                "note": "自定义 HTTP 网关不提供知识库列表"}
    cred = _credentials(cfg)
    if not cred["configured"]:
        return {"ok": True, "configured": False, "items": [],
                "note": _UNCONFIGURED_NOTE}
    payload = _openapi_post("search_knowledge_base",
                            {"query": "", "cursor": "", "limit": 20}, cfg)
    _openapi_error(payload, "读取知识库列表")
    data = _openapi_data(payload)
    items = list(data.get("info_list") or [])
    cursor = _s(data.get("next_cursor"))
    is_end = bool(data.get("is_end"))
    pages = 1
    while cursor and not is_end and pages < 5:
        payload = _openapi_post("search_knowledge_base",
                                {"query": "", "cursor": cursor, "limit": 20}, cfg)
        _openapi_error(payload, "读取知识库列表")
        data = _openapi_data(payload)
        items.extend(data.get("info_list") or [])
        cursor = _s(data.get("next_cursor"))
        is_end = bool(data.get("is_end"))
        pages += 1
    out = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict):
            out.append({"id": _s(item.get("kb_id") or item.get("id")),
                        "name": _s(item.get("kb_name") or item.get("name")),
                        "cover_url": _s(item.get("cover_url")),
                        "role_type": _s(item.get("role_type")),
                        "content_count": _s(item.get("content_count"))})
    return {"ok": True, "configured": True, "items": out, "note": ""}


def _openapi_search_one(query: str, kb_id: str, limit: int,
                        cfg: Dict[str, Any]) -> List[dict]:
    payload = _openapi_post("search_knowledge", {
        "query": query, "knowledge_base_id": kb_id, "cursor": "",
    }, cfg)
    _openapi_error(payload, "检索知识库")
    items = _openapi_data(payload).get("info_list") or []
    out = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        media_id = _s(item.get("media_id"))
        if not media_id:
            continue
        out.append({
            "id": media_id,
            "title": _s(item.get("title")),
            "snippet": _s(item.get("highlight_content")),
            "source": f"ima://{kb_id}/{media_id}",
            "media_type": item.get("media_type"),
            "kb_id": kb_id,
        })
        if len(out) >= limit:
            break
    return out


def _openapi_search(query: str, limit: int, kb_id: Optional[str],
                    cfg: Dict[str, Any]) -> dict:
    cred = _credentials(cfg)
    if not cred["configured"]:
        return {"ok": True, "configured": False, "items": [],
                "note": _UNCONFIGURED_NOTE}
    if kb_id:
        kb_ids = [kb_id]
    else:
        configured_kb = _s(cfg.get("knowledge_base"))
        if configured_kb:
            kb_ids = [configured_kb]
        else:
            listed = list_kbs(None)
            kb_ids = [k["id"] for k in listed.get("items", [])][:_MAX_AUTO_KBS]
            if not kb_ids:
                return {"ok": True, "configured": True, "items": [],
                        "note": "没有找到可检索的知识库"}
    items: List[dict] = []
    for kid in kb_ids:
        try:
            items.extend(_openapi_search_one(query, kid, limit, cfg))
        except LongformError:
            # 单个库失败不阻断其他库；最终把一个可读错误放在 note 里。
            continue
        if len(items) >= limit:
            break
    return {"ok": True, "configured": True, "items": items[:limit],
            "kb_ids": kb_ids, "note": ""}


def _openapi_fetch(item_id: str, cfg: Dict[str, Any]) -> dict:
    payload = _openapi_post("get_media_info", {"media_id": item_id}, cfg)
    code = payload.get("code")
    if code == 220030:
        return {
            "ok": True,
            "configured": True,
            "catalog_only": True,
            "code": code,
            "item": {"id": item_id, "title": "", "content": "", "source": ""},
            "note": "该条目属于订阅知识库，IMA 不允许通过 OpenAPI 读取正文。"
                    "请用 IMA 客户端导出 docx/txt/md，再在“素材与知识库”页扫描导入。",
        }
    if code != 0:
        _openapi_error(payload, "读取条目")
    data = _openapi_data(payload)
    media_type = data.get("media_type")
    url_info = data.get("url_info") if isinstance(data.get("url_info"), dict) else {}
    content = _media_url_text(_s(url_info.get("url")), url_info.get("headers"))
    notebook = data.get("notebook_ext_info") if isinstance(
        data.get("notebook_ext_info"), dict) else {}
    note = ""
    if not content and notebook.get("notebook_id"):
        note = ("这是 IMA 笔记类型；请用 notes 模块 get_doc_content 读取，"
                "或在 IMA 客户端打开后复制正文")
    if not content and not note:
        note = "IMA 未返回可读取的正文；请用 IMA 客户端导出后再导入"
    return {
        "ok": True,
        "configured": True,
        "catalog_only": not bool(content),
        "item": {
            "id": item_id,
            "title": _s(data.get("title")),
            "content": content,
            "source": _s(url_info.get("url")),
            "media_type": media_type,
            "notebook_id": _s(notebook.get("notebook_id")),
        },
        "note": note,
    }


# ============================================================
# 对外 API
# ============================================================
def status(settings: Optional[Dict[str, Any]] = None) -> dict:
    cfg = _resolve_cfg(settings)
    if not cfg.get("enabled"):
        return {"configured": False, "base_url": "", "channel": "disabled",
                "note": "IMA 未启用；可继续使用本地知识库。"
                        "本机若已有 ~/.config/ima 凭证，可在设置里打开 IMA 后使用"}
    if _is_legacy(cfg):
        configured = bool(cfg.get("configured"))
        return {"configured": configured, "base_url": cfg.get("base_url", ""),
                "channel": "legacy-http",
                "note": (f"IMA 已配置：{cfg.get('base_url', '')}" if configured
                         else _UNCONFIGURED_NOTE)}
    cred = _credentials(cfg)
    return {
        "configured": cred["configured"],
        "base_url": _OFFICIAL_BASE,
        "channel": "openapi",
        "credential_source": cred["source"] if cred["configured"] else "",
        "note": ("IMA OpenAPI 已就绪" if cred["configured"]
                 else "未检测到 IMA OpenAPI 凭证；请配置 ~/.config/ima/ 或环境变量"),
    }


def search(query: str, limit: int = _DEFAULT_LIMIT,
           kb_id: Optional[str] = None,
           settings: Optional[Dict[str, Any]] = None) -> dict:
    query = _require_str(query, "query", max_len=MAX_SEARCH_LEN)
    lim = _as_int(limit, _DEFAULT_LIMIT, minimum=1, maximum=MAX_SEARCH_LIMIT)
    cfg = _resolve_cfg(settings)
    if _is_legacy(cfg):
        return _legacy_search(query, lim, cfg)
    return _openapi_search(query, lim, kb_id, cfg)


def fetch(item_id: str, settings: Optional[Dict[str, Any]] = None) -> dict:
    item_id = _require_str(item_id, "id" if False else "item_id", max_len=300)
    cfg = _resolve_cfg(settings)
    if _is_legacy(cfg):
        return _legacy_fetch(item_id, cfg)
    cred = _credentials(cfg)
    if not cred["configured"]:
        return {"ok": True, "configured": False, "item": None,
                "note": _UNCONFIGURED_NOTE}
    return _openapi_fetch(item_id, cfg)


def import_to_materials(s, pid: str, item_id: str,
                        tags: Optional[List[str]] = None,
                        chapter: Optional[int] = None,
                        data: Optional[dict] = None,
                        content: Optional[str] = None,
                        title: Optional[str] = None,
                        kb_id: Optional[str] = None,
                        settings: Optional[Dict[str, Any]] = None) -> dict:
    """落库为素材。

    ``content`` 显式给定时直接用它（用于“从 IMA 客户端复制正文”补全）；
    否则尝试 fetch；订阅库 220030 时只落题录卡，不伪造正文。
    """
    item_id = _require_str(item_id, "item_id", max_len=300)
    cfg = _resolve_cfg(settings)
    if cfg.get("_explicit_off"):
        return {"ok": True, "configured": False, "material": None,
                "note": _UNCONFIGURED_NOTE}
    if _is_legacy(cfg):
        got = _legacy_fetch(item_id, cfg)
        item = got["item"]
        kb = _s(kb_id) or _s(cfg.get("knowledge_base")) or _s(item.get("kb_id"))
    else:
        if content is not None:
            item = {"id": item_id, "title": title or item_id,
                    "content": content, "source": "", "kb_id": ""}
            kb = _s(kb_id) or _s(cfg.get("knowledge_base")) or _s(item.get("kb_id"))
        else:
            got = _openapi_fetch(item_id, cfg) if _credentials(cfg)["configured"] else {
                "ok": True, "configured": False, "item": None,
                "note": _UNCONFIGURED_NOTE}
            item = got.get("item") or {"id": item_id, "title": title or item_id,
                                       "content": "", "source": ""}
            if got.get("catalog_only"):
                item["_catalog_only"] = True
                item["_note"] = got.get("note", "")
            kb = _s(kb_id) or _s(cfg.get("knowledge_base")) or _s(item.get("kb_id"))

    body = (content if content is not None else (item.get("content") or ""))
    mid = item.get("id") or item_id
    source = f"ima://{kb}/{mid}" if kb else f"ima://{mid}"
    material_title = (title or item.get("title") or mid)[:MAX_TITLE_LEN]
    catalog_only = bool(item.get("_catalog_only"))
    payload = dict(data or {})
    if catalog_only:
        payload.setdefault("catalog_only", True)
        payload.setdefault("ima_note", item.get("_note", ""))
        payload.setdefault("media_id", mid)
    elif content is not None:
        payload["catalog_only"] = False
        payload["content_source"] = "user_paste"
        payload["ima_note"] = ""
    short_source = f"ima://{mid}"
    existing = next((m for m in s.material_list(pid, limit=MAX_SEARCH_LIMIT)
                     if m.get("source") in (source, short_source)), None)
    if existing:
        added = s.material_update(
            pid, existing["id"], title=material_title,
            content=body[:MAX_MATERIAL_LEN], tags=tags or existing.get("tags"),
            data=payload)
        added["reused"] = True
    else:
        added = s.material_add(
            pid, title=material_title, content=body[:MAX_MATERIAL_LEN],
            kind="reference", source=source, source_kind="ima",
            tags=tags, chapter=chapter, data=payload)
        added["reused"] = False
    return {
        "ok": True,
        "configured": True,
        "catalog_only": catalog_only,
        "material": added,
        "item": {k: v for k, v in item.items() if not k.startswith("_")},
        "note": item.get("_note", ""),
    }


# ============================================================
# 写入侧：URL 批量导入 / 笔记关联 / 文件上传
# ============================================================
def _unconfigured(**extra: Any) -> dict:
    out = {"ok": False, "configured": False, "error": _UNCONFIGURED_NOTE}
    out.update(extra)
    return out


def _write_kb(cfg: Dict[str, Any], kb_id: Optional[str]) -> str:
    return _s(kb_id) or _s(cfg.get("knowledge_base"))


def _parse_urls(urls: Any) -> List[str]:
    if isinstance(urls, str):
        urls = [u.strip() for u in urls.split(",") if u.strip()]
    if not isinstance(urls, (list, tuple)) or not urls:
        raise LongformError("参数 `urls` 必须是非空数组")
    if len(urls) > _MAX_IMPORT_URLS:
        raise LongformError(
            f"一次最多导入 {_MAX_IMPORT_URLS} 个 URL，实际 {len(urls)} 条")
    clean: List[str] = []
    for i, url in enumerate(urls):
        if not isinstance(url, str):
            raise LongformError(f"urls[{i}] 必须是字符串")
        text = url.strip()
        parts = urllib.parse.urlsplit(text)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise LongformError(
                f"urls[{i}] 不是合法的 http/https 地址：{text[:80]!r}")
        clean.append(text)
    return clean


def import_urls(urls: Any, kb_id: Optional[str] = None,
                settings: Optional[Dict[str, Any]] = None) -> dict:
    """把一组网页 URL 批量导入 IMA 知识库（纯 HTTP，不走 COS）。

    先做本地校验（非空、≤50、必须是 http/https），再检查凭证；
    未配置时返回 ``{ok: False, configured: False, error: ...}``，不抛异常。
    """
    clean = _parse_urls(urls)
    cfg = _resolve_cfg(settings)
    if _is_legacy(cfg):
        return {"ok": False, "configured": True,
                "error": "自定义 HTTP 网关不支持 URL 导入"}
    if not _credentials(cfg)["configured"]:
        return _unconfigured()
    kb = _write_kb(cfg, kb_id)
    if not kb:
        return {"ok": False, "configured": True,
                "error": "需要 kb_id（或在 IMA 设置里配置 knowledge_base）"}
    payload = _openapi_post("import_urls",
                            {"knowledge_base_id": kb, "url_list": clean}, cfg)
    _openapi_error(payload, "导入 URL")
    data = _openapi_data(payload)
    return {
        "ok": True,
        "configured": True,
        "kb_id": kb,
        "count": len(clean),
        "urls": clean,
        "result": (data.get("media_list") or data.get("results")
                   or data.get("items") or data),
        "note": "",
    }


def _create_media_cfg(name: str, media_type: int, cfg: Dict[str, Any]) -> dict:
    payload = _openapi_post("create_media",
                            {"name": name, "media_type": media_type}, cfg)
    _openapi_error(payload, "创建媒资")
    data = _openapi_data(payload)
    return {
        "ok": True,
        "configured": True,
        "media_id": _s(data.get("media_id") or data.get("id")),
        "name": name,
        "media_type": media_type,
        "data": data,
    }


def create_media(name: str, media_type: int = _DEFAULT_MEDIA_TYPE,
                 settings: Optional[Dict[str, Any]] = None) -> dict:
    """创建一条 IMA 媒资，返回 media_id；用于把笔记/文件关联进知识库。"""
    name = _require_str(name, "name", max_len=MAX_TITLE_LEN)
    media_type = _as_int(media_type, _DEFAULT_MEDIA_TYPE,
                         minimum=1, maximum=100)
    cfg = _resolve_cfg(settings)
    if _is_legacy(cfg):
        return {"ok": False, "configured": True,
                "error": "自定义 HTTP 网关不支持创建媒资"}
    if not _credentials(cfg)["configured"]:
        return _unconfigured()
    return _create_media_cfg(name, media_type, cfg)


def _add_knowledge_cfg(media_id: str, kb: str, name: Optional[str],
                       cfg: Dict[str, Any]) -> dict:
    body: Dict[str, Any] = {"knowledge_base_id": kb, "media_id": media_id}
    if name:
        body["name"] = name
    payload = _openapi_post("add_knowledge", body, cfg)
    _openapi_error(payload, "关联知识库")
    return {"ok": True, "configured": True, "kb_id": kb,
            "media_id": media_id, "data": _openapi_data(payload)}


def add_knowledge(media_id: str, kb_id: Optional[str] = None,
                  name: Optional[str] = None,
                  settings: Optional[Dict[str, Any]] = None) -> dict:
    """把一条已创建的媒资关联进知识库（笔记关联）。"""
    media_id = _require_str(media_id, "media_id", max_len=300)
    if name is not None:
        name = _require_str(name, "name", allow_empty=True,
                            max_len=MAX_TITLE_LEN)
    cfg = _resolve_cfg(settings)
    if _is_legacy(cfg):
        return {"ok": False, "configured": True,
                "error": "自定义 HTTP 网关不支持关联知识库"}
    if not _credentials(cfg)["configured"]:
        return _unconfigured()
    kb = _write_kb(cfg, kb_id)
    if not kb:
        return {"ok": False, "configured": True,
                "error": "需要 kb_id（或在 IMA 设置里配置 knowledge_base）"}
    return _add_knowledge_cfg(media_id, kb, name, cfg)


def _cos_script(cfg: Dict[str, Any]) -> str:
    """COS 上传脚本路径：显式配置 > 环境变量；不做磁盘扫描。"""
    for candidate in (_s(cfg.get("cos_script")),
                      os.environ.get("IMA_COS_SCRIPT", "")):
        if candidate and os.path.isfile(candidate):
            return candidate
    return ""


def _node_path() -> str:
    try:
        return shutil.which("node") or ""
    except OSError:
        return ""


def _upload_with_node(path: str, name: str, kb: str, node: str,
                      script: str, cfg: Dict[str, Any]) -> dict:
    """preflight → check_repeated_names → create_media → COS → add_knowledge。"""
    size = os.path.getsize(path)
    pre = _openapi_post("preflight", {
        "knowledge_base_id": kb, "file_name": name, "file_size": size}, cfg)
    _openapi_error(pre, "上传预检")

    repeated = _openapi_post("check_repeated_names", {
        "knowledge_base_id": kb, "names": [name]}, cfg)
    _openapi_error(repeated, "检查重名")
    dup_data = _openapi_data(repeated)
    if (dup_data.get("repeated") or dup_data.get("exists")
            or name in (dup_data.get("names") or [])):
        return {"ok": False, "configured": True, "degraded": False,
                "duplicate": True, "path": path, "name": name, "kb_id": kb,
                "reason": f"知识库已有同名文件：{name}"}

    created = _create_media_cfg(name, _DEFAULT_MEDIA_TYPE, cfg)
    media_id = created.get("media_id")
    if not media_id:
        return {"ok": False, "configured": True, "degraded": False,
                "path": path, "name": name, "kb_id": kb,
                "error": "IMA 未返回 media_id，无法继续上传"}

    cred = _credentials(cfg)
    env = dict(os.environ)
    # 凭证只经环境变量传给可选 Node 脚本，不出现在命令行与返回值里。
    env["IMA_CLIENT_ID"] = cred["client_id"]
    env["IMA_API_KEY"] = cred["api_key"]
    cmd = [node, script, "--file", path, "--media-id", media_id,
           "--kb-id", kb]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=_COS_TIMEOUT, env=env)
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "configured": True, "degraded": False,
                "path": path, "name": name, "kb_id": kb, "media_id": media_id,
                "error": f"COS 上传脚本执行失败：{type(e).__name__}"}
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:300]
        return {"ok": False, "configured": True, "degraded": False,
                "path": path, "name": name, "kb_id": kb, "media_id": media_id,
                "error": f"COS 上传失败（退出码 {proc.returncode}）：{detail}"}

    linked = _add_knowledge_cfg(media_id, kb, name, cfg)
    return {
        "ok": True,
        "configured": True,
        "degraded": False,
        "path": path,
        "name": name,
        "size": size,
        "kb_id": kb,
        "media_id": media_id,
        "knowledge": linked.get("data"),
        "cos_output": (proc.stdout or "").strip()[:300],
    }


def upload_file(path: str, kb_id: Optional[str] = None,
                settings: Optional[Dict[str, Any]] = None) -> dict:
    """上传本地文件到 IMA 知识库。

    未配置 / 缺少 Node 或 COS 脚本时**明确降级**并返回原因，不抛未捕获异常；
    只有路径与大小这类调用方错误才抛 ``LongformError``。
    """
    raw = _require_str(path, "path", max_len=4096)
    full = os.path.abspath(os.path.expanduser(raw))
    if not os.path.exists(full):
        raise LongformError(f"文件不存在：{raw}")
    if not os.path.isfile(full):
        raise LongformError(f"不是普通文件：{raw}")
    size = os.path.getsize(full)
    if size > _MAX_UPLOAD_BYTES:
        raise LongformError(
            f"文件过大：{size} 字节，上限 {_MAX_UPLOAD_BYTES // (1024 * 1024)}MB")
    name = os.path.basename(full)

    cfg = _resolve_cfg(settings)
    base = {"path": full, "name": name, "size": size,
            "kb_id": _write_kb(cfg, kb_id)}
    if _is_legacy(cfg):
        return {**base, "ok": False, "configured": True, "degraded": True,
                "reason": "自定义 HTTP 网关不支持文件上传"}
    if not _credentials(cfg)["configured"]:
        return {**base, "ok": False, "configured": False, "degraded": True,
                "reason": _UNCONFIGURED_NOTE, "error": _UNCONFIGURED_NOTE}
    if not base["kb_id"]:
        return {**base, "ok": False, "configured": True,
                "error": "需要 kb_id（或在 IMA 设置里配置 knowledge_base）"}

    node = _node_path()
    script = _cos_script(cfg)
    if not node or not script:
        missing = []
        if not node:
            missing.append("node")
        if not script:
            missing.append("cos-upload.cjs")
        return {**base, "ok": False, "configured": True, "degraded": True,
                "reason": "需要 Node/COS 上传脚本", "missing": missing,
                "hint": "安装 Node 并配置 IMA_COS_SCRIPT 或 ima.cos_script 后重试"}

    try:
        return _upload_with_node(full, name, base["kb_id"], node, script, cfg)
    except LongformError as e:
        return {**base, "ok": False, "configured": True, "degraded": False,
                "error": str(e)}
    except Exception as e:          # noqa: BLE001 - 上传失败不吞进程
        return {**base, "ok": False, "configured": True, "degraded": False,
                "error": f"{type(e).__name__}: {e}"}
