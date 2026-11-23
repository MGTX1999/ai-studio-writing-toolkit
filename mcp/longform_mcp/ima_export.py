"""IMA 客户端导出目录 → 素材库。

IMA 订阅知识库的正文没有 OpenAPI 读取权限（`get_media_info` 返回 220030），
这是产品侧的合规限制，不能绕过。可行的正路有两条：

1. 在 IMA 客户端里打开条目，手动复制正文，粘贴到素材库（题录仍保留 media_id）；
2. 在 IMA 客户端里导出 docx / txt / md，放到本模块扫描的目录，一次导入。

本模块只做第 2 条的“落地”部分：读文件、切块、去重、写来源。
不解析 PDF（零依赖环境下 PDF 文本抽取不可靠），遇到 PDF 会给出明确提示。
"""

from __future__ import annotations

import hashlib
import os
import re
import zipfile
from typing import Any, Dict, List, Optional, Sequence
from xml.etree import ElementTree

import kb
from store import (
    MAX_CHAPTER_N,
    MAX_MATERIAL_LEN,
    MAX_SEARCH_LIMIT,
    MAX_SOURCE_LEN,
    LongformError,
    _require_int,
    _require_str,
)

TEXT_EXTS = (".txt", ".md", ".markdown")
DOCX_EXTS = (".docx",)
HTML_EXTS = (".html", ".htm")
DEFAULT_PATTERNS = TEXT_EXTS + DOCX_EXTS + HTML_EXTS
MAX_FILE_BYTES = 5_000_000
_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def default_dir(s) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(s.db_path)), "ima_exports")


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _hash(text: str) -> str:
    return hashlib.sha1(_norm(text).encode("utf-8", "replace")).hexdigest()


def _decode(path: str) -> str:
    with open(path, "rb") as f:
        raw = f.read()
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _docx_text(path: str) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml")
    except (KeyError, zipfile.BadZipFile, OSError) as e:
        raise LongformError(f"不是可读的 docx：{e}") from None
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as e:
        raise LongformError(f"docx 正文 XML 解析失败：{e}") from None
    paragraphs = []
    for para in root.iter(_W_NS + "p"):
        parts = [node.text or "" for node in para.iter(_W_NS + "t")]
        text = "".join(parts).strip()
        if text:
            paragraphs.append(text)
    return "\n\n".join(paragraphs)


def _html_text(path: str) -> str:
    raw = _decode(path)
    raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", raw)
    raw = re.sub(r"(?i)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?i)</p>", "\n\n", raw)
    raw = re.sub(r"<[^>]+>", "", raw)
    raw = (raw.replace("&nbsp;", " ").replace("&amp;", "&")
           .replace("&lt;", "<").replace("&gt;", ">")
           .replace("&quot;", "\"").replace("&#39;", "'"))
    return re.sub(r"\n{3,}", "\n\n", raw).strip()


def extract_text(path: str) -> Dict[str, Any]:
    ext = os.path.splitext(path)[1].lower()
    if ext in TEXT_EXTS:
        text, fmt = _decode(path), "text"
    elif ext in DOCX_EXTS:
        text, fmt = _docx_text(path), "docx"
    elif ext in HTML_EXTS:
        text, fmt = _html_text(path), "html"
    elif ext == ".pdf":
        raise LongformError(
            "PDF 暂不支持自动抽取；请在 IMA 客户端导出为 docx / txt / md，"
            "或手动复制正文到素材库")
    else:
        raise LongformError(f"不支持的文件类型：{ext or '(无扩展名)'}")
    text = text.strip()
    if not text:
        raise LongformError("文件正文为空")
    return {"text": text, "format": fmt, "chars": len(text)}


def _chunk_title(title: str, index: int, total: int) -> str:
    if total <= 1 or index == 1:
        return title[:300]
    suffix = f"（{index}/{total}）"
    return title[:max(1, 300 - len(suffix))] + suffix


def scan(s, pid: str, directory: Optional[str] = None,
         patterns: Sequence[str] = DEFAULT_PATTERNS,
         recursive: bool = True, kind: str = "reference",
         tags: Optional[Sequence[str]] = None,
         chapter: Optional[int] = None) -> dict:
    s._require_project(pid)
    folder = directory or default_dir(s)
    folder = _require_str(folder, "directory", max_len=1000)
    full = os.path.abspath(os.path.expanduser(folder))
    if not os.path.isdir(full):
        if directory:
            raise LongformError(f"目录不存在：{directory}")
        os.makedirs(full, exist_ok=True)
        return {"ok": True, "dir": full, "count": 0, "skipped": 0,
                "files": [], "warnings": ["目录已创建；把 IMA 导出的文件放进去再扫描"],
                "created": True}
    if chapter is not None:
        chapter = _require_int(chapter, "chapter", minimum=0,
                               maximum=MAX_CHAPTER_N)

    file_paths: List[str] = []
    if recursive:
        for cur, _dirs, names in os.walk(full):
            for name in names:
                ext = os.path.splitext(name)[1].lower()
                if ext in patterns or ext == ".pdf":
                    file_paths.append(os.path.join(cur, name))
    else:
        for name in os.listdir(full):
            path = os.path.join(full, name)
            ext = os.path.splitext(name)[1].lower()
            if os.path.isfile(path) and (ext in patterns or ext == ".pdf"):
                file_paths.append(path)
    file_paths.sort()

    existing = {_hash(m.get("content") or "")
                for m in s.material_list(pid, limit=MAX_SEARCH_LIMIT)
                if m.get("content")}
    count = 0
    skipped = 0
    imported_files = []
    warnings = []
    clean_tags = [t for t in (tags or []) if isinstance(t, str) and t.strip()]
    if "ima-export" not in clean_tags:
        clean_tags.append("ima-export")

    for path in file_paths:
        try:
            if os.path.getsize(path) > MAX_FILE_BYTES:
                warnings.append(f"{os.path.basename(path)}：超过 5MB，跳过")
                continue
            extracted = extract_text(path)
        except (LongformError, OSError) as e:
            warnings.append(f"{os.path.basename(path)}：{e}")
            continue
        text = extracted["text"]
        title = os.path.splitext(os.path.basename(path))[0] or "IMA 导出"
        source = f"ima-export://{os.path.basename(path)}"[:MAX_SOURCE_LEN]
        chunks = kb.split_chunks(text, limit=MAX_MATERIAL_LEN)
        total = len(chunks)
        added_for_file = 0
        for index, chunk in enumerate(chunks, 1):
            digest = _hash(chunk)
            if digest in existing:
                skipped += 1
                continue
            s.material_add(
                pid, title=_chunk_title(title, index, total), content=chunk,
                kind=kind, source=source, source_kind="ima",
                tags=clean_tags, chapter=chapter,
                data={"imported_from": "ima_client_export",
                      "export_file": path, "export_format": extracted["format"],
                      "chunk": index, "chunks": total})
            existing.add(digest)
            count += 1
            added_for_file += 1
        if added_for_file:
            imported_files.append({"file": path, "title": title,
                                   "format": extracted["format"],
                                   "chunks": added_for_file})
    return {
        "ok": True,
        "dir": full,
        "count": count,
        "skipped": skipped,
        "files": imported_files,
        "warnings": warnings,
        "scanned": len(file_paths),
    }


def guide(s) -> dict:
    return {
        "ok": True,
        "default_dir": default_dir(s),
        "steps": [
            "打开 IMA 客户端，进入订阅知识库，找到目标条目。",
            "在条目右上角选择“导出”或“下载”，优先导出 docx / txt / md。",
            f"把导出文件放进目录：{default_dir(s)}（或你来指定目录）。",
            "回到“素材与知识库”页，点“扫描导出目录”。",
            "扫描会按内容 hash 去重，并把来源记为 ima-export://<文件名>。",
        ],
        "why_not_api": "IMA 对订阅知识库的正文读取有产品级权限限制，"
                       "OpenAPI 返回 220030；本工具不绕过该限制，只接收你主动导出的文件。",
        "supported": ["docx", "txt", "md", "html"],
        "unsupported": ["pdf（请在 IMA 客户端先转 docx）"],
    }
