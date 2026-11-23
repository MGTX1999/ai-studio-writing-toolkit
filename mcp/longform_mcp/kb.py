"""素材库 → 知识库：分块导入、全文检索与章节素材包。

这里不新建任何数据源，全部复用 ``materials`` 表：导入即多条 ``material_add``。
检索采用"精确短语优先 + 词项召回 + 短词/零结果 LIKE 兜底"的轻量混合策略：
FTS5 负责三字及以上的短语与词项，LIKE 负责中文两字词、短英文词和 FTS 零结果。
两条路径合并后按标题、来源、标签、章节和可核查等级重排，再按内容/来源去重。
界面与模型拿到的都是同一套字段，不需要区分后端。

分块的意义：一份 30 万字的资料如果整条塞进素材，单条既超过
``MAX_MATERIAL_LEN``，检索片段也会失去定位价值。按行边界切成 4000 字
左右的多条，共享同一 ``source``，既能追溯出处，也能精确命中。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import unicodedata
from typing import Any, Dict, List, Optional, Sequence, Tuple

from store import (
    DEFAULT_SEARCH_LIMIT,
    MATERIAL_KINDS,
    MAX_BRIEF_LEN,
    MAX_CHAPTER_N,
    MAX_MATERIAL_LEN,
    MAX_SEARCH_LEN,
    MAX_SEARCH_LIMIT,
    MAX_SOURCE_LEN,
    MAX_TAG_LEN,
    MAX_TITLE_LEN,
    LongformError,
    _as_int,
    _load_json,
    _require_enum,
    _require_int,
    _require_str,
)

CHUNK_CHARS = 4000
# 单条素材的硬上限是 MAX_MATERIAL_LEN；当前 4000 < 20000，仍取 min 防御未来调整。
_CHUNK_LIMIT = min(CHUNK_CHARS, MAX_MATERIAL_LEN)

# 检索片段：围绕第一个命中词取前后约 220 字，控制在一次阅读能判断的范围内。
_SNIPPET_RADIUS = 220
_FALLBACK_SNIPPET_CHARS = 480
_MAX_SNIPPET_MARKS = 8
_MIN_FTS_CHARS = 3
_MAX_QUERY_TERMS = 16

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_QUERY_SPLIT_RE = re.compile(
    r"[\s,，、;；:：/|\\()（）\[\]【】{}<>《》\"'“”‘’…—\-]+")
_CJK_RE = re.compile(r"[\u3400-\u9fff]")

# 只挡住极短、几乎必然放大候选集的虚词；不做完整中文分词，避免引入依赖。
_STOP_TERMS = {
    "的", "了", "和", "与", "及", "在", "对", "把", "被", "为", "是",
    "他", "她", "它", "我", "你", "这", "那", "一个", "一种", "没有",
    "然后", "以及", "但是", "因为", "所以", "如果", "已经", "正在",
}

# 素材地图：给素材一个稳定的分类，检索时能按"我要哪类参考"缩小范围。
# terms 既用于导入时自动分类，也用于展示"这一类通常收录什么"。
MATERIAL_CATEGORIES = [
    {"key": "writing", "name": "写作教程",
     "hint": "校准节奏、视角与叙述技巧；写正文前用来选方法。",
     "terms": ["写作技巧", "节奏", "视角"]},
    {"key": "character", "name": "人物设定",
     "hint": "人设、角色弧光与动机；写人物戏前查一致性。",
     "terms": ["人设", "角色弧光", "动机"]},
    {"key": "plot", "name": "大纲情节",
     "hint": "大纲、冲突与反转；章节规划阶段优先参考。",
     "terms": ["大纲", "情节", "反转"]},
    {"key": "description", "name": "描写技法",
     "hint": "环境、感官与动作描写；写场景前找可直接仿写的句式。",
     "terms": ["环境描写", "感官", "动作"]},
    {"key": "world", "name": "世界观",
     "hint": "设定规则、势力与地理；铺陈世界时用来保持自洽。",
     "terms": ["设定", "规则", "势力"]},
    {"key": "sample", "name": "范文",
     "hint": "片段仿写与语感对齐；需要例句时优先检索。",
     "terms": ["范文", "片段", "仿写"]},
    {"key": "other", "name": "其他",
     "hint": "暂未分类；可手动补充 category 后再检索。",
     "terms": []},
]
CATEGORY_KEYS = tuple(c["key"] for c in MATERIAL_CATEGORIES)
_CATEGORY_BY_KEY = {c["key"]: c for c in MATERIAL_CATEGORIES}

# 同义词组：查询命中任一词时，把同组其余词也加进 OR 召回，
# 解决"作者搜修炼体系、素材写升级体系"这类词面不一致。
SYNONYMS = {
    "修炼体系": ["升级体系", "力量等级", "境界"],
    "金手指": ["外挂", "系统", "作弊器"],
    "人物弧光": ["角色成长", "人物变化"],
    "伏笔": ["埋线", "扣子", "悬念"],
}

# 自动分类的补充关键词：分类 terms 之外，再收一批标题/正文常见词。
_CATEGORY_KEYWORDS = {
    "writing": ["叙事", "结构", "文笔", "写法", "教程", "技巧"],
    "character": ["人物", "角色", "性格", "成长", "弧光"],
    "plot": ["剧情", "冲突", "转折", "情节", "反转"],
    "description": ["描写", "环境", "感官", "动作", "细节", "氛围"],
    "world": ["世界观", "规则", "势力", "设定", "地图", "体系"],
    "sample": ["范文", "样章", "片段", "仿写", "摘抄"],
}


def category_meta(key: str) -> dict:
    return _CATEGORY_BY_KEY.get(key) or _CATEGORY_BY_KEY["other"]


def categories() -> dict:
    """素材地图 + 同义词表，供 ``kb_categories`` 工具直接返回。"""
    return {
        "ok": True,
        "categories": [
            {"key": c["key"], "name": c["name"], "hint": c["hint"],
             "terms": list(c["terms"])}
            for c in MATERIAL_CATEGORIES
        ],
        "synonyms": {k: list(v) for k, v in SYNONYMS.items()},
    }


def infer_category(title: str = "", content: str = "",
                   tags: Optional[Sequence[str]] = None) -> str:
    """按标题（权重最高）/ 标签 / 正文关键词给素材分类，识别不到归 other。

    刻意保持朴素：不做分词，只数关键词出现次数；宁可归到 other，
    也不把一条素材误塞进错误分类，影响检索结果的信任度。
    """
    head = normalize_query(title or "")[:MAX_TITLE_LEN]
    body = normalize_query(content or "")[:4000]
    tag_text = " ".join(normalize_query(t) for t in (tags or [])
                        if isinstance(t, str))
    best_key = "other"
    best_score = 0
    for cat in MATERIAL_CATEGORIES:
        key = cat["key"]
        if key == "other":
            continue
        words = list(cat["terms"]) + list(_CATEGORY_KEYWORDS.get(key, []))
        score = 0
        for word in words:
            score += head.count(word) * 3
            score += tag_text.count(word) * 2
            score += body.count(word)
        if score > best_score:
            best_score = score
            best_key = key
    return best_key



# ---------------------------------------------------------------- 分块
def split_chunks(content: str, limit: int = _CHUNK_LIMIT) -> List[str]:
    """按行边界把正文切成 ≤limit 的块；超长单行硬切。"""
    chunks: List[str] = []
    buf: List[str] = []
    size = 0

    for line in content.splitlines(keepends=True):
        if len(line) > limit:
            if buf:
                chunks.append("".join(buf))
                buf, size = [], 0
            for i in range(0, len(line), limit):
                chunks.append(line[i:i + limit])
            continue
        if size + len(line) > limit:
            chunks.append("".join(buf))
            buf, size = [line], len(line)
        else:
            buf.append(line)
            size += len(line)
    if buf:
        chunks.append("".join(buf))

    # 纯空白块没有检索价值，直接丢弃（内容非空时至少还剩一块）。
    return [c for c in chunks if c.strip()]


def _chunk_title(title: str, index: int, total: int) -> str:
    """第 1 块用原标题，其余追加 ``（i/n）``；必要时截断基数以满足标题上限。"""
    if total <= 1 or index == 1:
        return title[:MAX_TITLE_LEN]
    suffix = f"（{index}/{total}）"
    base = title[:max(1, MAX_TITLE_LEN - len(suffix))]
    return base + suffix


def _add_material(s, pid: str, title: str, content: str, kind: str,
                  source: str, source_kind: Optional[str],
                  tags: Optional[Sequence[str]],
                  chapter: Optional[int],
                  data: Optional[dict] = None) -> dict:
    kwargs: Dict[str, Any] = {
        "title": title, "content": content, "kind": kind,
        "source": source, "tags": list(tags or []), "chapter": chapter,
    }
    if data:
        kwargs["data"] = data
    # 不传时交给 material_add 按 source 形态自动推断（file / url / ima）。
    if source_kind is not None:
        kwargs["source_kind"] = source_kind
    return s.material_add(pid, **kwargs)


# ---------------------------------------------------------------- 导入
def import_text(s, pid: str, title: str, content: str,
                kind: str = "reference", source: str = "",
                source_kind: Optional[str] = None,
                tags: Optional[Sequence[str]] = None,
                chapter: Optional[int] = None) -> dict:
    """把一段文本切块写入素材库，返回 ``{ok, count, ids}``。"""
    if not isinstance(content, str):
        raise LongformError(
            f"参数 `content` 必须是字符串，实际收到 {type(content).__name__}")
    if not content.strip():
        raise LongformError("参数 `content` 不能为空")
    title = _require_str(title, "title", max_len=MAX_TITLE_LEN)
    kind = _require_enum(kind, "kind", MATERIAL_KINDS)
    source = _require_str(source or "", "source", allow_empty=True,
                          max_len=MAX_SOURCE_LEN).strip()
    if chapter is not None:
        chapter = _require_int(chapter, "chapter", minimum=0,
                               maximum=MAX_CHAPTER_N)

    chunks = split_chunks(content)
    total = len(chunks)
    cat_key = infer_category(title, content, tags)
    cat = category_meta(cat_key)
    data = {"category": cat_key, "use_hint": cat["hint"]}
    ids: List[str] = []
    for i, chunk in enumerate(chunks, 1):
        added = _add_material(s, pid, _chunk_title(title, i, total), chunk,
                              kind, source, source_kind, tags, chapter,
                              data=data)
        ids.append(added["id"])
    return {"ok": True, "count": total, "ids": ids,
            "category": cat_key, "use_hint": cat["hint"]}


def import_file(s, pid: str, path: str, title: Optional[str] = None,
                kind: str = "reference", source: Optional[str] = None,
                source_kind: Optional[str] = None,
                tags: Optional[Sequence[str]] = None,
                chapter: Optional[int] = None) -> dict:
    """读入一个 UTF-8 文本文件并导入；默认用绝对路径作为来源。"""
    path = _require_str(path, "path", max_len=1000)
    full = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(full):
        raise LongformError(f"文件不存在：{path}")
    try:
        with open(full, encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError as e:
        raise LongformError(f"读取文件失败：{path}（{e}）") from None

    if title is None:
        title = os.path.splitext(os.path.basename(full))[0] or "未命名素材"
    if source is None:
        source = full
    return import_text(s, pid, title, content, kind=kind, source=source,
                       source_kind=source_kind, tags=tags, chapter=chapter)


def _match_patterns(name: str, patterns: Sequence[str]) -> bool:
    low = name.lower()
    return any(low.endswith(p.lower()) for p in patterns)


def import_dir(s, pid: str, directory: str,
               patterns: Sequence[str] = (".md", ".txt"), recursive: bool = True,
               kind: str = "reference", source_kind: Optional[str] = None,
               tags: Optional[Sequence[str]] = None,
               chapter: Optional[int] = None) -> dict:
    """批量导入目录下的文本文件；单个文件失败只记 warning，不中断整体。"""
    directory = _require_str(directory, "directory", max_len=1000)
    root = os.path.abspath(os.path.expanduser(directory))
    if not os.path.isdir(root):
        raise LongformError(f"目录不存在：{directory}")

    files: List[str] = []
    if recursive:
        for cur, _dirs, names in os.walk(root):
            for name in names:
                if _match_patterns(name, patterns):
                    files.append(os.path.join(cur, name))
    else:
        for name in os.listdir(root):
            full = os.path.join(root, name)
            if os.path.isfile(full) and _match_patterns(name, patterns):
                files.append(full)
    files.sort()

    count = 0
    ids: List[str] = []
    warnings: List[str] = []
    for full in files:
        try:
            res = import_file(s, pid, full, kind=kind, source_kind=source_kind,
                              tags=tags, chapter=chapter)
        except LongformError as e:
            warnings.append(f"{full}：{e}")
            continue
        count += res["count"]
        ids.extend(res["ids"])
    return {"ok": True, "count": count, "ids": ids, "warnings": warnings}


# ---------------------------------------------------------------- 检索基础
def normalize_query(text: str) -> str:
    """统一全角/半角、空白与控制字符；查询和素材文本的显示保持原样。"""
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = text.replace("\u3000", " ")
    text = _CONTROL_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _is_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def _term_list(query: str) -> List[str]:
    """把查询拆成"完整短语 + 词项 + 中文双字切片"的召回候选。

    这里不做完整中文分词：双字切片只用于补充召回，最终排序仍由完整查询、
    标题和字段命中决定，避免把"旧仓""仓库"之类的噪声当成精确结果。
    """
    query = normalize_query(query)
    terms: List[str] = []
    seen: set = set()

    def add(term: str) -> None:
        term = term.strip()
        if not term or len(term) < 2 or term in _STOP_TERMS:
            return
        if term not in seen:
            seen.add(term)
            terms.append(term)

    add(query)
    parts = [p.strip() for p in _QUERY_SPLIT_RE.split(query) if p.strip()]
    for part in parts:
        add(part)
        # 中文长词补双字切片；只对含中文的片段做，英文仍按空格词处理。
        if _is_cjk(part) and len(part) >= 4:
            for i in range(len(part) - 1):
                add(part[i:i + 2])

    return terms[:_MAX_QUERY_TERMS]


def _fts_terms(terms: Sequence[str]) -> List[str]:
    """FTS 只接受至少 3 个字符、且不含空白的词项。"""
    out: List[str] = []
    for term in terms:
        if len(term) >= _MIN_FTS_CHARS and not any(c.isspace() for c in term):
            if term not in out:
                out.append(term)
    return out[:_MAX_QUERY_TERMS]


def _fts_phrase(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def _tag_clause(tag: Optional[str]) -> Tuple[str, List[Any]]:
    """标签用 JSON1 精确匹配，避免把整表取回 Python 再过滤。"""
    if tag is None:
        return "", []
    tag = _require_str(tag, "tag", max_len=MAX_TAG_LEN).strip()
    return (" AND EXISTS (SELECT 1 FROM json_each(m.tags_json) AS jt"
            " WHERE jt.value = ?)"), [tag]


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _content_hash(text: str) -> str:
    normalized = re.sub(r"\s+", "", (text or "").casefold())
    return hashlib.sha1(normalized.encode("utf-8", "replace")).hexdigest()


def _contains(text: str, term: str) -> bool:
    if not text or not term:
        return False
    return normalize_query(term).casefold() in normalize_query(text).casefold()


def _find_first(text: str, terms: Sequence[str]) -> Tuple[int, int]:
    """返回正文中最早出现的命中位置与长度；完整查询优先。"""
    low = normalize_query(text).casefold()
    best: Optional[Tuple[int, int]] = None
    for term in terms:
        q = normalize_query(term).casefold()
        if not q:
            continue
        pos = low.find(q)
        if pos >= 0:
            # 完整查询通常最长；同位置时保留更长者。
            if best is None or (pos, -len(q)) < (best[0], -best[1]):
                best = (pos, len(q))
    return best if best is not None else (-1, 0)


def _mark_terms(text: str, terms: Sequence[str]) -> str:
    """在片段中高亮命中词；合并重叠区间，避免嵌套 ``<mark>``。"""
    if not text:
        return ""
    low = normalize_query(text).casefold()
    intervals: List[Tuple[int, int]] = []
    for term in terms:
        q = normalize_query(term).casefold()
        if not q:
            continue
        start = 0
        while len(intervals) < _MAX_SNIPPET_MARKS:
            pos = low.find(q, start)
            if pos < 0:
                break
            intervals.append((pos, pos + len(q)))
            start = pos + max(1, len(q))
    if not intervals:
        return text

    intervals.sort()
    merged: List[Tuple[int, int]] = []
    for start, end in intervals:
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    out: List[str] = []
    cursor = 0
    for start, end in merged:
        out.append(text[cursor:start])
        out.append("<mark>" + text[start:end] + "</mark>")
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


def _make_snippet(text: str, query: str, terms: Sequence[str]) -> str:
    """围绕最早命中词截取约 440 字，并给所有命中词加高亮。"""
    if not text:
        return ""
    all_terms = [query] + [t for t in terms if t != query]
    pos, length = _find_first(text, all_terms)
    if pos < 0:
        return text[:_FALLBACK_SNIPPET_CHARS]
    start = max(0, pos - _SNIPPET_RADIUS)
    end = min(len(text), pos + max(length, 1) + _SNIPPET_RADIUS)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    piece = text[start:end]
    return prefix + _mark_terms(piece, all_terms) + suffix


def _item(mid: str, title: str, kind: str, source: str, source_kind: str,
          tags: Any, chapter: Any, snippet: Any, score: float = 0.0,
          matched_terms: Optional[Sequence[str]] = None,
          match_reason: str = "", category: str = "",
          synonym_terms: Optional[Sequence[str]] = None,
          use_hint: str = "") -> dict:
    return {
        "id": mid,
        "title": title or "",
        "kind": kind or "",
        "source": source or "",
        "source_kind": source_kind or "",
        "tags": tags if isinstance(tags, list) else [],
        "chapter": chapter,
        "snippet": snippet or "",
        "score": int(round(score)),
        "matched_terms": list(matched_terms or []),
        "match_reason": match_reason,
        "category": category or "",
        "synonym_terms": list(synonym_terms or []),
        "use_hint": use_hint or "",
    }


def _category_clause(category: Optional[str]) -> Tuple[str, List[Any]]:
    """分类用 JSON1 精确匹配；调用方负责在不支持时回退 Python 过滤。"""
    if category is None:
        return "", []
    return " AND json_extract(m.data_json,'$.category') = ?", [category]


def _fts_search(s, pid: str, terms: Sequence[str], kind: Optional[str],
                chapter: Optional[int], tag: Optional[str],
                limit: int,
                category: Optional[str] = None) -> Optional[List[dict]]:
    """FTS5 短语/词项召回；索引查询本身出错时返回 None，让调用方回退 LIKE。"""
    fts_terms = _fts_terms(terms)
    if not fts_terms:
        return []
    match = " OR ".join(_fts_phrase(t) for t in fts_terms)
    sql = ("SELECT m.*, bm25(materials_fts) AS _fts_rank"
           " FROM materials_fts JOIN materials m"
           " ON m.rowid = materials_fts.rowid"
           " WHERE materials_fts MATCH ? AND m.project = ?")
    args: List[Any] = [match, pid]
    if kind is not None:
        sql += " AND m.kind = ?"
        args.append(kind)
    if chapter is not None:
        sql += " AND m.chapter = ?"
        args.append(chapter)
    tag_sql, tag_args = _tag_clause(tag)
    sql += tag_sql
    args.extend(tag_args)
    cat_sql, cat_args = _category_clause(category)
    sql += cat_sql
    args.extend(cat_args)
    sql += " ORDER BY bm25(materials_fts) LIMIT ?"
    args.append(limit)

    try:
        rows = s.conn.execute(sql, args).fetchall()
    except sqlite3.Error:
        return None
    return [dict(r) for r in rows]


def _category_of(row: dict) -> str:
    data = _load_json(row.get("data_json"), {})
    if not isinstance(data, dict):
        return ""
    return str(data.get("category") or "")


def _like_search(s, pid: str, terms: Sequence[str], kind: Optional[str],
                 chapter: Optional[int], tag: Optional[str],
                 limit: int,
                 category: Optional[str] = None) -> List[dict]:
    """子串召回：覆盖中文两字词、短英文词以及 FTS 零结果的情况。"""
    if not terms:
        return []
    sql = "SELECT m.* FROM materials m WHERE m.project=?"
    args: List[Any] = [pid]
    if kind is not None:
        sql += " AND m.kind = ?"
        args.append(kind)
    if chapter is not None:
        sql += " AND m.chapter = ?"
        args.append(chapter)
    tag_sql, tag_args = _tag_clause(tag)
    sql += tag_sql
    args.extend(tag_args)

    clauses: List[str] = []
    for term in terms:
        like = f"%{_like_escape(term)}%"
        clauses.append("(m.title LIKE ? ESCAPE '\\' OR m.content LIKE ? ESCAPE '\\'"
                       " OR m.source LIKE ? ESCAPE '\\')")
        args.extend([like, like, like])
    sql += " AND (" + " OR ".join(clauses) + ")"
    tail = " ORDER BY m.created_at DESC, m.id DESC LIMIT ?"

    cat_sql, cat_args = _category_clause(category)
    try:
        rows = s.conn.execute(sql + cat_sql + tail, args + cat_args + [limit]).fetchall()
    except sqlite3.Error:
        if category is None:
            return []
        # JSON1 不可用：退回无分类 SQL，再在 Python 里按 data_json 过滤。
        rows = s.conn.execute(sql + tail, args + [limit]).fetchall()
        return [dict(r) for r in rows if _category_of(dict(r)) == category]
    return [dict(r) for r in rows]


def _score_row(row: dict, query: str, terms: Sequence[str],
               fts_rank: Optional[float],
               synonym_terms: Optional[Sequence[str]] = None) -> dict:
    """按字段命中、词项覆盖和 BM25 排名合成一个可解释的相关度。"""
    title = str(row.get("title") or "")
    content = str(row.get("content") or "")
    source = str(row.get("source") or "")
    tags = _load_json(row.get("tags_json"), [])
    data = _load_json(row.get("data_json"), {})
    if not isinstance(data, dict):
        data = {}
    tag_values = tags if isinstance(tags, list) else []

    score = 0.0
    matched: List[str] = []
    title_full = _contains(title, query)
    source_full = _contains(source, query)
    content_full = _contains(content, query)

    if title_full:
        score += 90
    if source_full:
        score += 28
    if content_full:
        score += 22

    for term in terms:
        if term == query:
            continue
        if _contains(title, term):
            score += 16
            matched.append(term)
        elif _contains(source, term):
            score += 8
            matched.append(term)
        elif _contains(content, term):
            score += 5
            matched.append(term)

    if query in matched:
        matched.remove(query)
    matched = matched[:8]

    all_terms = [t for t in terms if t != query]
    if all_terms and all(t in matched for t in all_terms):
        score += 12
    elif len(matched) >= 2:
        score += 6

    # 标签命中也算弱证据；标签本身已经用于过滤，这里只影响排序。
    for tag in tag_values:
        if _contains(tag, query):
            score += 6
            break

    level = str(data.get("evidence_level") or "").upper()
    if data.get("catalog_only") or level == "E0":
        pass
    elif level == "E3":
        score += 6
    elif level == "E2":
        score += 4
    elif level == "E1":
        score += 2

    if fts_rank is not None:
        # bm25 是越小越相关；取有限的正向加成，避免单一排名压过字段命中。
        score += min(12.0, max(0.0, -float(fts_rank)))

    reasons: List[str] = []
    if title_full:
        reasons.append("标题含完整查询")
    elif source_full:
        reasons.append("来源含完整查询")
    elif content_full:
        reasons.append("正文含完整查询")
    if len(matched) >= 2:
        reasons.append("命中 " + "、".join(matched[:4]))
    if fts_rank is not None and not reasons:
        reasons.append("BM25 相关")
    if not reasons:
        reasons.append("字段子串命中")

    snippet = _make_snippet(content, query, terms)
    if "<mark>" not in snippet:
        alt = _make_snippet(title, query, terms)
        if "<mark>" not in alt:
            alt = _make_snippet(source, query, terms)
        if "<mark>" in alt:
            snippet = alt

    reason = "；".join(reasons)
    if len(matched) == len(all_terms) and all_terms:
        reason = "全部词命中；" + reason
    return _item(row.get("id"), title, row.get("kind"), source,
                 row.get("source_kind"), tag_values, row.get("chapter"),
                 snippet, score, matched, reason,
                 category=str(data.get("category") or ""),
                 synonym_terms=synonym_terms,
                 use_hint=str(data.get("use_hint") or ""))


def _dedupe_items(items: List[dict]) -> List[dict]:
    """合并同来源、同规范化内容的重复素材，保留分更高的一条。"""
    buckets: Dict[Tuple[str, str], dict] = {}
    for item in items:
        key = (str(item.get("source") or ""), _content_hash(
            str(item.get("_content") or "")))
        existing = buckets.get(key)
        if existing is None:
            buckets[key] = item
            continue
        # 同一条正文被重复导入时，不把重复条目当成额外证据。
        if item.get("score", 0) > existing.get("score", 0):
            buckets[key] = item

    out = list(buckets.values())
    for item in out:
        item.pop("_content", None)
        item.pop("_fts_rank", None)
    return out


def _expand_synonyms(query: str) -> List[str]:
    """把查询命中的同义词组展开为 OR 词，返回"新增"的词（不含原查询词）。"""
    norm = normalize_query(query)
    out: List[str] = []
    for key, values in SYNONYMS.items():
        group = [key] + list(values)
        if not any(word in norm for word in group):
            continue
        for word in group:
            if word not in norm and word not in out:
                out.append(word)
    return out


def search(s, pid: str, query: str, kind: Optional[str] = None,
           tag: Optional[str] = None, chapter: Optional[int] = None,
           limit: Optional[int] = None, category: Optional[str] = None,
           expand_synonyms: bool = True) -> List[dict]:
    """在素材库中检索，返回统一结构：相关度、命中词和可读片段。

    检索顺序：
    1. 三字及以上词项走 FTS5（BM25）；
    2. 含两字词、短英文词或 FTS 零结果时补 LIKE 子串召回；
    3. 合并、按字段重排，并按"来源 + 内容 hash"去重。

    ``category`` 按素材地图过滤；``expand_synonyms=False`` 时关闭同义词扩展。
    """
    query = normalize_query(_require_str(query, "query", max_len=MAX_SEARCH_LEN))
    if not query:
        raise LongformError("query 不能为空")
    if kind is not None:
        kind = _require_enum(kind, "kind", MATERIAL_KINDS)
    if chapter is not None:
        chapter = _require_int(chapter, "chapter", minimum=0,
                               maximum=MAX_CHAPTER_N)
    if tag is not None:
        tag = _require_str(tag, "tag", max_len=MAX_TAG_LEN).strip()
    if category is not None:
        category = _require_enum(category, "category", CATEGORY_KEYS)
    if not isinstance(expand_synonyms, (bool, int)) or expand_synonyms not in (0, 1):
        raise LongformError(
            f"参数 `expand_synonyms` 必须是布尔值，实际收到 {expand_synonyms!r}")
    lim = _as_int(limit, DEFAULT_SEARCH_LIMIT,
                  minimum=1, maximum=MAX_SEARCH_LIMIT)
    # 分类过滤会砍掉候选，候选池按 limit*4 起算，避免过滤后不足。
    base_limit = lim * 4 if category is not None else lim * 8
    candidate_limit = min(MAX_SEARCH_LIMIT, max(80, base_limit))
    terms = _term_list(query)
    synonym_terms = _expand_synonyms(query) if expand_synonyms else []
    search_terms = list(terms)
    for term in synonym_terms:
        if term not in search_terms:
            search_terms.append(term)
    search_terms = search_terms[:_MAX_QUERY_TERMS]

    fts_rows: Optional[List[dict]] = None
    if getattr(s, "fts_available", False):
        fts_rows = _fts_search(s, pid, search_terms, kind, chapter, tag,
                               candidate_limit, category)
    fts_empty = not fts_rows

    # 短词必须走 LIKE；FTS 空结果也必须兜底，否则两字中文会静默变成"库里没有"。
    short_terms = [t for t in search_terms if len(t) < _MIN_FTS_CHARS]
    like_rows: List[dict] = []
    if fts_rows is None or fts_empty or short_terms:
        like_rows = _like_search(s, pid, search_terms, kind, chapter, tag,
                                 candidate_limit, category)

    merged: Dict[str, dict] = {}
    for row in (fts_rows or []):
        item = _score_row(row, query, search_terms, row.get("_fts_rank"),
                          synonym_terms)
        item["_content"] = row.get("content") or ""
        merged[item["id"]] = item
    for row in like_rows:
        item = _score_row(row, query, search_terms, None, synonym_terms)
        item["_content"] = row.get("content") or ""
        existing = merged.get(item["id"])
        if existing is None or item["score"] > existing["score"]:
            merged[item["id"]] = item

    items = _dedupe_items(list(merged.values()))
    if tag is not None:
        # 防御性过滤：即使某个后端不支持 JSON1，也不能把错标签的结果交出去。
        items = [m for m in items if tag in m["tags"]]
    if category is not None:
        # 同上：JSON1 不可用时后端已回退；这里再兜一层，确保不泄露错分类。
        items = [m for m in items if m.get("category") == category]
    items.sort(key=lambda m: (-m["score"], m["title"]))
    return items[:lim]


# ---------------------------------------------------------------- 章节素材包
def _chapter_meta(s, pid: str, chapter_n: int) -> List[dict]:
    rows = s.conn.execute(
        "SELECT id,title,tags_json,source,source_kind,chapter FROM materials"
        " WHERE project=? AND chapter=? ORDER BY created_at DESC, id DESC LIMIT 50",
        (pid, chapter_n)).fetchall()
    return [dict(r) for r in rows]


def _known_material_terms(s, pid: str, brief: str,
                          limit: int = 500) -> List[str]:
    """从素材标题/标签中挑出真正出现在细纲里的短语，优先于盲目双字切片。"""
    text = normalize_query(brief)
    if not text:
        return []
    rows = s.conn.execute(
        "SELECT title, tags_json FROM materials WHERE project=?"
        " ORDER BY created_at DESC, id DESC LIMIT ?", (pid, limit)).fetchall()
    out: List[str] = []
    seen: set = set()

    def add(term: str) -> None:
        term = normalize_query(term)
        if 2 <= len(term) <= 20 and term not in seen:
            seen.add(term)
            out.append(term)

    for row in rows:
        title = normalize_query(row["title"] or "")
        tags = _load_json(row["tags_json"], [])
        candidates = [title] + (tags if isinstance(tags, list) else [])
        for candidate in candidates:
            candidate = normalize_query(candidate)
            # 标题常是"雾港旧码头地理参考"这种短语，取其中出现在细纲的
            # 2–8 字子串，比从细纲盲切双字更接近作者真实用词。
            for size in range(min(8, len(candidate)), 1, -1):
                for start in range(0, len(candidate) - size + 1):
                    sub = candidate[start:start + size]
                    if sub in text:
                        add(sub)
    return out


def _brief_terms(brief: str) -> List[str]:
    text = normalize_query(brief)
    out: List[str] = []
    for part in [p.strip() for p in _QUERY_SPLIT_RE.split(text) if p.strip()]:
        if len(part) < 2 or part in _STOP_TERMS:
            continue
        if 2 <= len(part) <= 4:
            out.append(part)
        elif _is_cjk(part):
            # 无分词器：中文长句用双字切片补召回，完整短语仍由实体名提供。
            for i in range(len(part) - 1):
                seg = part[i:i + 2]
                if seg not in _STOP_TERMS:
                    out.append(seg)
        else:
            out.append(part)
    return out


def material_queries(s, pid: str, chapter_n: int, brief: str = "",
                     max_queries: int = 12) -> List[str]:
    """从本章细纲、实体名和章节素材标题/标签生成检索词。

    这不是一个"智能查询规划器"：没有引入分词模型，只用项目自己的实体名、
    章节标注、素材标题/标签和中文双字切片。它的目的是让素材包有稳定的
    召回入口，而不是猜作者想问什么。
    """
    chapter_n = _require_int(chapter_n, "chapter_n", minimum=0,
                             maximum=MAX_CHAPTER_N)
    brief = _require_str(brief, "brief", allow_empty=True,
                         max_len=MAX_BRIEF_LEN)
    max_queries = _as_int(max_queries, 12, minimum=1, maximum=20)
    queries: List[str] = []
    seen: set = set()

    def add(term: Any) -> None:
        if not isinstance(term, str):
            return
        term = normalize_query(term)
        if len(term) < 2 or len(term) > 40 or term in _STOP_TERMS or term in seen:
            return
        seen.add(term)
        queries.append(term)

    # 章节标注的素材由 material_packet 直接作为候选加入，不在这里占用
    # 查询名额；否则标签里的 ima/导出测试 会把真正的对象词挤掉。

    # 素材标题/标签是项目自己的"词表"：只取真正出现在本章细纲里的子串。
    for term in _known_material_terms(s, pid, brief):
        add(term)

    names: List[str] = []
    for entity in s.bible_list(pid):
        name = normalize_query(entity.get("name") or "")
        if 2 <= len(name) <= 40:
            names.append(name)
    brief_norm = normalize_query(brief)
    for name in names:
        if name in brief_norm:
            add(name)

    brief_terms = [t for t in _brief_terms(brief) if t not in seen]
    remaining = max_queries - len(queries)
    if remaining > 0 and brief_terms:
        if len(brief_terms) <= remaining:
            for term in brief_terms:
                add(term)
        else:
            # 长细纲最容易丢尾部对象词（"寻找沉船编号"里的"沉船"）。
            # 已有关键词优先，剩余名额给开头与结尾词，避免只取前 N 个。
            head = brief_terms[:max(1, remaining - 3)]
            tail = brief_terms[-3:]
            for term in head + tail:
                add(term)
    # 细纲为空时才用实体名兜底；非空细纲不应让全量实体挤掉对象词。
    if not brief_norm:
        for name in names:
            add(name)
    return queries[:max_queries]


def _evidence_for(m: dict) -> dict:
    """按 v1.7 的 E0–E3 口径判断可核查程度；不做质量评分。"""
    try:
        from insights import _material_evidence
        return _material_evidence(m)
    except Exception:
        return {"level": "E0", "label": "来源待补",
                "usable_for": "待读目录", "next_action": "补正文"}


def _plain_excerpt(text: str, terms: Sequence[str], limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return ""
    pos, length = _find_first(text, terms)
    if pos < 0:
        return text[:limit]
    start = max(0, pos - min(80, limit // 3))
    end = min(len(text), pos + max(length, 1) + limit - (pos - start))
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return prefix + text[start:end] + suffix


def _section_block(item: dict) -> str:
    level = item["level"]
    title = item["title"] or "（无标题）"
    source = item["source"] or "未标注"
    why = item["why"]
    excerpt = item.get("excerpt") or ""
    lines = [f"- [{level}] {title}｜来源：{source}｜{why}"]
    if excerpt:
        lines.append(f"  摘录：{excerpt}")
    else:
        lines.append("  摘录：（正文为空，只有题录）")
    return "\n".join(lines)


def material_packet(s, pid: str, chapter_n: int, brief: str = "",
                    max_items: int = 6, max_chars: int = 5600,
                    min_evidence: str = "E2", clue_limit: int = 3,
                    queries: Optional[Sequence[str]] = None,
                    max_queries: int = 12) -> dict:
    """按本章细纲召回并组装素材包。

    E2/E3 进入"可用素材"，E0/E1 只放在"待读线索"，不会被当成原文事实。
    同一来源最多两条、同来源同内容 hash 去重，保证素材包有基本多样性。
    """
    chapter_n = _require_int(chapter_n, "chapter_n", minimum=0,
                             maximum=MAX_CHAPTER_N)
    brief = _require_str(brief, "brief", allow_empty=True, max_len=MAX_BRIEF_LEN)
    max_items = _as_int(max_items, 6, minimum=0, maximum=20)
    max_chars = _as_int(max_chars, 5600, minimum=200, maximum=20_000)
    clue_limit = _as_int(clue_limit, 3, minimum=0, maximum=10)
    min_evidence = str(min_evidence or "E2").upper()
    if min_evidence not in ("E0", "E1", "E2", "E3"):
        min_evidence = "E2"

    qs = ([normalize_query(q) for q in queries if normalize_query(q)]
          if queries is not None else material_queries(
              s, pid, chapter_n, brief, max_queries=max_queries))

    usable_levels = {
        "E3": ("E3",),
        "E2": ("E2", "E3"),
        "E1": ("E1", "E2", "E3"),
        "E0": ("E0", "E1", "E2", "E3"),
    }[min_evidence]

    qs = ([normalize_query(q) for q in queries if normalize_query(q)]
          if queries is not None else material_queries(
              s, pid, chapter_n, brief, max_queries=max_queries))

    candidates: Dict[str, dict] = {}

    def add_candidate(item: dict, query: str, bonus: int = 0) -> None:
        entry = candidates.setdefault(item["id"], {
            "id": item["id"], "score": 0, "queries": [], "bonus": 0,
            "item": item,
        })
        entry["score"] += int(item.get("score") or 0) + bonus
        if query and query not in entry["queries"]:
            entry["queries"].append(query)

    for query in qs:
        for hit in search(s, pid, query, limit=10):
            add_candidate(hit, query)

    # 本章明确标注过的素材直接进入候选，哪怕细纲没有出现它的标题。
    for row in _chapter_meta(s, pid, chapter_n):
        item = _item(row.get("id"), row.get("title") or "", "", row.get("source") or "",
                     row.get("source_kind") or "", _load_json(row.get("tags_json"), []),
                     row.get("chapter"), "", 0, [], "本章标注")
        add_candidate(item, "本章标注", bonus=25)

    ranked = sorted(candidates.values(), key=lambda c: -c["score"])
    usable: List[dict] = []
    clues: List[dict] = []
    used_hashes: set = set()
    per_source: Dict[str, int] = {}
    duplicates = 0

    for entry in ranked:
        m = s.material_get(pid, entry["id"])
        if not m:
            continue
        evidence = _evidence_for(m)
        level = evidence.get("level", "E0")
        source = str(m.get("source") or "")
        content = str(m.get("content") or "")
        content_hash = _content_hash(content)
        key = (source, content_hash)
        if content and key in used_hashes:
            duplicates += 1
            continue
        source_key = source or m.get("id") or ""
        if source_key and per_source.get(source_key, 0) >= 2:
            continue
        if content:
            used_hashes.add(key)

        title = str(m.get("title") or "（无标题）")
        why_bits = []
        if entry["queries"]:
            why_bits.append("检索命中：" + "、".join(entry["queries"][:3]))
        if any(q == "本章标注" for q in entry["queries"]):
            why_bits.append("本章标注")
        why = "；".join(why_bits) or "相关素材"
        base = {
            "id": entry["id"], "title": title, "source": source,
            "source_kind": m.get("source_kind") or "",
            "chapter": m.get("chapter"), "level": level,
            "label": evidence.get("label", ""),
            "usable_for": evidence.get("usable_for", ""),
            "next_action": evidence.get("next_action", ""),
            "why": why, "queries": entry["queries"][:5],
            "score": entry["score"],
        }
        if level in usable_levels and len(usable) < max_items:
            base["excerpt"] = _plain_excerpt(
                content, qs + [title], limit=220)
            usable.append(base)
        elif level not in usable_levels and len(clues) < clue_limit:
            clues.append(base)

    # 预算内组装；宁可少放，也不把标题和出处截成半句。
    markdown = ""
    notes: List[str] = []
    if usable or clues:
        lines = [f"## 本章素材与参考（{min_evidence}+ 才可作事实；"
                 f"低于 {min_evidence} 只作待读线索）"]
        remaining = max_chars - len(lines[0])
        placed = 0
        for item in usable:
            block = _section_block(item)
            if len(block) + 1 > remaining:
                break
            lines.append(block)
            remaining -= len(block) + 1
            placed += 1
        if clues:
            clue_header = "\n### 待读线索（正文未核，不得当事实引用）"
            if len(clue_header) + 1 <= remaining:
                lines.append(clue_header)
                remaining -= len(clue_header) + 1
                for item in clues:
                    block = (f"- [{item['level']}] {item['title']}"
                             f"｜来源：{item['source'] or '未标注'}"
                             f"｜下一步：{item['next_action'] or '补正文'}")
                    if len(block) + 1 > remaining:
                        break
                    lines.append(block)
                    remaining -= len(block) + 1
        dropped = len(usable) - placed
        if dropped > 0:
            notes.append(f"{dropped} 条可用素材因字符预算未放入")
        markdown = "\n".join(lines)
        if len(markdown) > max_chars:
            markdown = markdown[:max_chars]
            notes.append("素材包已按 max_chars 截断")

    if not qs:
        notes.append("细纲为空且没有实体名/章节素材，未执行素材检索")
    if duplicates:
        notes.append(f"合并了 {duplicates} 条同来源重复正文")

    return {
        "ok": True,
        "chapter_n": chapter_n,
        "queries": qs,
        "min_evidence": min_evidence,
        "items": usable,
        "clues": clues,
        "count": len(usable) + len(clues),
        "usable_count": len(usable),
        "clue_count": len(clues),
        "chars": len(markdown),
        "markdown": markdown,
        "notes": notes,
        "duplicates_collapsed": duplicates,
    }
