"""文风蒸馏（Style Distillation）：从作者样本提炼可复用的九维文风档案。

流程与 ``quality_loop`` 同源：本地先做确定性校验（样本数 / 字数 / 单条上限），
达标后才调用一次模型，要求只输出 JSON；解析并逐项校验后落库
``style_profiles``，并把本次调用记入 ``llm_calls``（tier=qa）。

约束：

- 至少 3 个样本、总字数（去空白）≥2000、总量 ≤200000、单条 ≤50000；
- 九个维度缺一不可且必须是非空字符串；
- 只写 ``style_profiles`` 与 ``llm_calls``，不写正文、不覆盖原始 style。

``apply`` 才把档案写进项目 ``style``：新增 ``style_profile_id`` / ``style_prompt`` /
``thinking_genes`` / ``writing_genes`` / ``banned_words``（与原禁用词合并去重）/
``style_limitations``，其它键原样保留。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Tuple

import lint
import llm_client
from store import (
    MAX_CHAPTER_N,
    MAX_NAME_LEN,
    MAX_TEXT_LEN,
    LongformError,
    _require_int,
    _require_str,
)

NINE_DIMENSIONS = (
    "vocabulary", "syntax", "paragraph", "rhetoric", "emotion",
    "pov", "tone", "format", "thinking",
)
MIN_SAMPLES = 3
MIN_CHARS = 2000
MAX_TOTAL_CHARS = 200000
MAX_SAMPLE_CHARS = 50000

_SYSTEM = (
    "你是文体分析与风格蒸馏专家。只做可核查的文体判断，不评价内容好坏，"
    "不虚构样本里不存在的特征。只输出 JSON。")
# 给指令与 JSON 骨架留出余量，避免超大样本把 prompt 顶过 MAX_TEXT_LEN。
_PROMPT_RESERVE = 4000
_LESS_CORPUS_WARNING = "语料较少，建议 5–10 篇"


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
    prompt = usage.get("prompt_tokens")
    if prompt is None:
        prompt = usage.get("input_tokens")
    completion = usage.get("completion_tokens")
    if completion is None:
        completion = usage.get("output_tokens")
    return {"input": _int_token(prompt), "output": _int_token(completion)}


def _json_from_text(text: str) -> Any:
    """从模型输出里抠出 JSON；容忍 ```json 围栏与前后解释文字。"""
    raw = (text or "").strip()
    if not raw:
        raise LongformError("模型返回空文本，无法解析文风档案 JSON")
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


# ---------------------------------------------------------------- 样本收集
def _chapter_sample(s: Any, pid: str, n: Any) -> Tuple[dict, str]:
    n = _require_int(n, "chapter_ns[]", minimum=0, maximum=MAX_CHAPTER_N)
    chapter = s.chapter_get(pid, n)
    if chapter is None:
        raise LongformError(f"第 {n} 章还没有任何版本，无法作为文风样本")
    text = chapter.get("content") or ""
    if not text.strip():
        raise LongformError(f"第 {n} 章正文为空，无法作为文风样本")
    if len(text) > MAX_SAMPLE_CHARS:
        raise LongformError(
            f"第 {n} 章正文 {len(text)} 字符，超过单条上限 {MAX_SAMPLE_CHARS}")
    stats = lint.text_stats(text)
    sample = {"kind": "chapter", "chapter_n": n,
              "chars": stats["chars_no_space"], "sha256": _sha256(text)}
    return sample, text


def _text_sample(text: Any, index: int) -> Tuple[dict, str]:
    if not isinstance(text, str):
        raise LongformError(
            f"texts[{index}] 必须是字符串，实际收到 {type(text).__name__}")
    if not text.strip():
        raise LongformError(f"texts[{index}] 不能为空")
    if len(text) > MAX_SAMPLE_CHARS:
        raise LongformError(
            f"texts[{index}] 共 {len(text)} 字符，超过单条上限 {MAX_SAMPLE_CHARS}")
    stats = lint.text_stats(text)
    sample = {"kind": "text", "index": index + 1,
              "chars": stats["chars_no_space"], "sha256": _sha256(text)}
    return sample, text


def _collect(s: Any, pid: str, texts: Optional[list],
             chapter_ns: Optional[list]) -> Tuple[List[dict], List[str]]:
    """按 texts / chapter_ns 收集样本，返回 (source.samples, 样本正文)。"""
    if texts is None and chapter_ns is None:
        raise LongformError(
            "style_distill 需要 texts 或 chapter_ns 之一；"
            "至少 3 个样本 / 2000 字；可多选几章或粘贴更多文本")
    samples: List[dict] = []
    bodies: List[str] = []

    if texts is not None:
        if not isinstance(texts, list):
            raise LongformError("texts 必须是字符串数组")
        for i, text in enumerate(texts):
            sample, body = _text_sample(text, i)
            samples.append(sample)
            bodies.append(body)

    if chapter_ns is not None:
        if not isinstance(chapter_ns, list):
            raise LongformError("chapter_ns 必须是整数数组")
        for n in chapter_ns:
            sample, body = _chapter_sample(s, pid, n)
            samples.append(sample)
            bodies.append(body)

    return samples, bodies


def _check_limits(samples: List[dict]) -> int:
    total = sum(int(item["chars"]) for item in samples)
    if len(samples) < MIN_SAMPLES:
        raise LongformError(
            f"文风样本不足：至少 {MIN_SAMPLES} 个样本 / {MIN_CHARS} 字，"
            f"当前 {len(samples)} 个；可多选几章或粘贴更多文本")
    if total < MIN_CHARS:
        raise LongformError(
            f"文风样本字数不足：至少 {MIN_SAMPLES} 个样本 / {MIN_CHARS} 字，"
            f"当前 {total} 字；可多选几章或粘贴更多文本")
    if total > MAX_TOTAL_CHARS:
        raise LongformError(
            f"文风样本总字数 {total} 超过上限 {MAX_TOTAL_CHARS}；请减少样本")
    return total


def _build_prompt(bodies: List[str]) -> str:
    parts: List[str] = []
    for i, body in enumerate(bodies, 1):
        parts.append(f"【样本 {i}】\n{body}")
    body_block = "\n\n".join(parts)
    budget = MAX_TEXT_LEN - _PROMPT_RESERVE
    if len(body_block) > budget:
        body_block = body_block[:budget] + "\n（样本过长，已截断）"
    dims = "、".join(NINE_DIMENSIONS)
    return (
        "下面是同一作者的若干篇正文样本。请按九个维度提炼其文体特征：\n"
        f"{dims}\n\n"
        "同时给出可复用的写作基因（writing_genes）与思维基因（thinking_genes）、"
        "需要避开的表达（banned）、一段可直接放进写作提示词的 style_prompt，"
        "以及本次蒸馏的局限（limitations）。\n"
        "只输出 JSON，不要解释，不要代码块：\n"
        '{"dimensions":{"vocabulary":"...","syntax":"...","paragraph":"...",'
        '"rhetoric":"...","emotion":"...","pov":"...","tone":"...",'
        '"format":"...","thinking":"..."},'
        '"writing_genes":[{"name":"...","description":"...","example":"..."}],'
        '"thinking_genes":[{"name":"...","description":"...","example":"..."}],'
        '"banned":["..."],"style_prompt":"...","limitations":"..."}\n\n'
        f"{body_block}"
    )


# ---------------------------------------------------------------- 解析与校验
def _gene_list(value: Any, field: str) -> List[dict]:
    if not isinstance(value, list):
        raise LongformError(f"文风档案缺少 `{field}` 数组")
    out: List[dict] = []
    for i, item in enumerate(value):
        if not isinstance(item, dict):
            raise LongformError(f"{field}[{i}] 必须是对象")
        name = item.get("name")
        description = item.get("description")
        if not isinstance(name, str) or not name.strip():
            raise LongformError(f"{field}[{i}] 缺少非空 name")
        if not isinstance(description, str) or not description.strip():
            raise LongformError(f"{field}[{i}] 缺少非空 description")
        example = item.get("example")
        out.append({
            "name": name.strip(),
            "description": description.strip(),
            "example": example.strip() if isinstance(example, str) else "",
        })
    return out


def _parse_profile(text: str) -> Dict[str, Any]:
    return _validate_profile(_json_from_text(text))


def _validate_profile(payload: Any) -> Dict[str, Any]:
    """校验九维文风档案；模型输出与交接结果共用同一套规则。"""
    if not isinstance(payload, dict):
        raise LongformError("文风档案 JSON 顶层必须是对象")

    dimensions = payload.get("dimensions")
    if not isinstance(dimensions, dict):
        raise LongformError("文风档案缺少 dimensions 对象")
    dims: Dict[str, str] = {}
    for key in NINE_DIMENSIONS:
        value = dimensions.get(key)
        if not isinstance(value, str) or not value.strip():
            raise LongformError(f"文风档案缺少维度 `{key}` 或其为空")
        dims[key] = value.strip()

    writing = _gene_list(payload.get("writing_genes"), "writing_genes")
    thinking = _gene_list(payload.get("thinking_genes"), "thinking_genes")

    banned = payload.get("banned")
    if not isinstance(banned, list):
        raise LongformError("文风档案缺少 banned 字符串数组")
    banned_words: List[str] = []
    for i, word in enumerate(banned):
        if not isinstance(word, str):
            raise LongformError(f"banned[{i}] 必须是字符串")
        word = word.strip()
        if word:
            banned_words.append(word)

    style_prompt = payload.get("style_prompt")
    if not isinstance(style_prompt, str) or not style_prompt.strip():
        raise LongformError("文风档案缺少非空 style_prompt")

    limitations = payload.get("limitations")
    return {
        "dimensions": dims,
        "writing_genes": writing,
        "thinking_genes": thinking,
        "banned": banned_words,
        "style_prompt": style_prompt.strip(),
        "limitations": limitations.strip() if isinstance(limitations, str) else "",
    }


def _record_call(s: Any, pid: str, prompt: str, result: Any) -> None:
    res = result if isinstance(result, dict) else {}
    tokens = _usage_tokens(res.get("usage"))
    s.llm_call_add(
        pid, run_id=None, step_name="style_distill",
        provider=str(res.get("provider") or ""),
        model=str(res.get("model") or ""), tier="qa",
        prompt_hash=_sha256(prompt),
        input_tokens=tokens["input"], output_tokens=tokens["output"])


# ---------------------------------------------------------------- 对外接口
_PROVIDERS = ("auto", "api", "studio")


def _resolve_provider(s: Any, pid: str, provider: Any) -> str:
    """把 auto 解析成 api / studio；显式值原样返回。"""
    provider = _require_str(provider, "provider", max_len=MAX_NAME_LEN)
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


def distill(s: Any, pid: str, *, texts: Optional[list] = None,
            chapter_ns: Optional[list] = None,
            name: str = "默认画像", provider: Any = "auto") -> dict:
    """蒸馏一次文风并落库；studio 模式改为创建 ``style_distill`` 交接单。

    API 模式返回 ``{ok, id, name, profile, source, warnings}``；
    studio 模式返回 ``{ok, status:"awaiting_input", handoff}``。
    """
    s._require_project(pid)
    name = _require_str(name or "默认画像", "name", max_len=MAX_NAME_LEN)
    provider = _resolve_provider(s, pid, provider)

    samples, bodies = _collect(s, pid, texts, chapter_ns)
    total = _check_limits(samples)

    prompt = _build_prompt(bodies)
    if provider == "studio":
        handoff = s.handoff_create(
            pid, "style_distill", prompt,
            schema={"name": name, "type": "style_profile"})
        return {"ok": True, "status": "awaiting_input", "name": name,
                "handoff": handoff}

    result = llm_client.generate(prompt, system=_SYSTEM,
                                 temperature=0.2, max_tokens=4000)
    raw = result.get("text") if isinstance(result, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        raise LongformError("LLM 返回空文本，无法蒸馏文风")
    profile = _parse_profile(raw)

    _record_call(s, pid, prompt, result)

    source = {"samples": samples, "total_chars": total}
    saved = s.style_profile_add(pid, name, profile, source)

    warnings: List[str] = []
    if len(samples) < 5 or total < 5000:
        warnings.append(_LESS_CORPUS_WARNING)

    return {"ok": True, "id": saved["id"], "name": name,
            "profile": profile, "source": source, "warnings": warnings}


def submit_profile(s: Any, handoff_id: str, result: Any) -> dict:
    """提交 studio 模式的九维文风档案，校验并落 ``style_profiles``。"""
    handoff = s.handoff_get(handoff_id)
    if handoff is None:
        raise LongformError(f"交接单不存在：{handoff_id}")
    if handoff.get("kind") != "style_distill":
        raise LongformError(
            f"交接单 {handoff_id} 类型为 {handoff.get('kind')}，不是 style_distill")
    if handoff.get("status") == "applied":
        return {"ok": True, "handoff_id": handoff_id, "already_applied": True}

    if isinstance(result, str):
        payload = _json_from_text(result)
    elif isinstance(result, dict):
        if set(result.keys()) == {"text"} and isinstance(result.get("text"), str):
            payload = _json_from_text(result["text"])
        else:
            payload = result
    else:
        raise LongformError(
            f"文风档案必须是对象或 JSON 字符串，实际收到 {type(result).__name__}")
    profile = _validate_profile(payload)

    schema = handoff.get("schema")
    name = "默认画像"
    if isinstance(schema, dict) and isinstance(schema.get("name"), str) \
            and schema["name"].strip():
        name = schema["name"].strip()
    name = _require_str(name, "name", max_len=MAX_NAME_LEN)

    pid = handoff["project"]
    saved = s.style_profile_add(pid, name, profile,
                                {"handoff_id": handoff_id})
    s.handoff_submit(handoff_id, result)
    return {"ok": True, "id": saved["id"], "name": name, "profile": profile}


def _summary(profile: Any) -> Dict[str, str]:
    dims = profile.get("dimensions") if isinstance(profile, dict) else None
    if not isinstance(dims, dict):
        return {}
    return {k: (v[:60] if isinstance(v, str) else v) for k, v in dims.items()}


def get(s: Any, pid: str, profile_id: Optional[str] = None) -> dict:
    """给 id 返回完整档案；否则返回轻量列表（不含完整基因，控体积）。"""
    s._require_project(pid)
    if profile_id is not None:
        row = s.style_profile_get(pid, profile_id)
        if row is None:
            raise LongformError(f"文风档案不存在：{profile_id}（项目 {pid}）")
        return {"ok": True, "profile": row}

    items = []
    for row in s.style_profile_get(pid):
        profile = row.get("profile") if isinstance(row.get("profile"), dict) else {}
        items.append({
            "id": row["id"],
            "name": row["name"],
            "created_at": row["created_at"],
            "dimensions": _summary(profile),
            "banned_count": len(profile.get("banned") or []),
        })
    return {"ok": True, "profiles": items}


# ---------------------------------------------------------------- 文风盲测
BLIND_MIN_SAMPLES = 1
BLIND_MIN_CHARS = 100
BLIND_MAX_PENALTY = 20.0
BLIND_MIN_THRESHOLD = 50.0
BLIND_MAX_THRESHOLD = 100.0
BLIND_NOTE = "只比较文本特征，不使用来源/作者标签"

_BLIND_DIMENSIONS = (
    ("avg_sentence", "平均句长"),
    ("sentence_cv", "句长波动"),
    ("avg_paragraph", "平均段长"),
    ("dialogue_ratio", "对话占比"),
    ("punct_density", "标点密度"),
    ("short_ratio", "短句占比"),
    ("unique_ratio", "用字丰富度"),
    ("simile_density", "比喻密度"),
)
_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?…\n]+")
_DIALOGUE_RE = re.compile(
    r"“([^”]*)”|「([^」]*)」|『([^』]*)』|\"([^\"]*)\"")
_PUNCT_RE = re.compile(
    r"[，。！？；：、…—·「」『』“”‘’（）《》〈〉,.!?;:\"'()\[\]{}<>~～\-–—]")
_SIMILE_RE = re.compile(r"像|仿佛|如同|似乎|宛如|好比")


def _text_features(text: str) -> Dict[str, float]:
    """按单篇文本计算八项文体特征。"""
    nospace = re.sub(r"\s+", "", text)
    chars = len(nospace)
    if not chars:
        return {key: 0.0 for key, _label in _BLIND_DIMENSIONS}

    sentences = [part for part in _SENTENCE_SPLIT_RE.split(text) if part.strip()]
    lengths = [len(re.sub(r"\s+", "", part)) for part in sentences]
    sentence_count = len(lengths)
    avg_sentence = sum(lengths) / sentence_count if sentence_count else 0.0
    if sentence_count:
        mean = avg_sentence
        variance = sum((x - mean) ** 2 for x in lengths) / sentence_count
        sentence_cv = (variance ** 0.5) / mean if mean > 0 else 0.0
        short_ratio = sum(1 for x in lengths if x <= 15) / sentence_count
    else:
        sentence_cv = 0.0
        short_ratio = 0.0

    paragraphs = [line.strip() for line in text.splitlines() if line.strip()]
    para_lengths = [len(re.sub(r"\s+", "", line)) for line in paragraphs]
    avg_paragraph = (sum(para_lengths) / len(para_lengths)
                     if para_lengths else 0.0)

    dialogue_chars = 0
    for match in _DIALOGUE_RE.finditer(text):
        inner = next((g for g in match.groups() if g is not None), "")
        dialogue_chars += len(re.sub(r"\s+", "", inner))
    dialogue_ratio = dialogue_chars / chars

    punct_density = len(_PUNCT_RE.findall(text)) / chars * 100
    unique_ratio = len(set(nospace)) / chars
    simile_density = len(_SIMILE_RE.findall(text)) / chars * 1000

    return {
        "avg_sentence": avg_sentence,
        "sentence_cv": sentence_cv,
        "avg_paragraph": avg_paragraph,
        "dialogue_ratio": dialogue_ratio,
        "punct_density": punct_density,
        "short_ratio": short_ratio,
        "unique_ratio": unique_ratio,
        "simile_density": simile_density,
    }


def _corpus_features(bodies: List[str]) -> Dict[str, float]:
    """逐文本计算后取均值。"""
    if not bodies:
        return {key: 0.0 for key, _label in _BLIND_DIMENSIONS}
    totals = {key: 0.0 for key, _label in _BLIND_DIMENSIONS}
    for body in bodies:
        feats = _text_features(body)
        for key in totals:
            totals[key] += feats[key]
    return {key: value / len(bodies) for key, value in totals.items()}


def _similarity(reference: float, candidate: float) -> float:
    return 1 - min(1.0, abs(candidate - reference) / max(reference, 0.01))


def _blind_threshold(value: Any) -> float:
    if value is None:
        return 75.0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LongformError(
            "参数 `threshold` 必须是数字，"
            f"实际收到 {type(value).__name__}：{value!r}")
    value = float(value)
    if value < BLIND_MIN_THRESHOLD or value > BLIND_MAX_THRESHOLD:
        raise LongformError(
            f"参数 `threshold` 只允许 {BLIND_MIN_THRESHOLD:g}–"
            f"{BLIND_MAX_THRESHOLD:g}，实际收到 {value:g}")
    return value


def _blind_corpus(s: Any, pid: str, texts: Optional[list],
                  chapter_ns: Optional[list], label: str) -> Tuple[List[dict], List[str], int]:
    """收集参考/候选样本；每侧至少 1 个、总字数 ≥100、单条 ≤50000、总量 ≤200000。"""
    if texts is None and chapter_ns is None:
        raise LongformError(
            f"{label}样本为空：至少 {BLIND_MIN_SAMPLES} 个样本 / "
            f"{BLIND_MIN_CHARS} 字；可用 texts 或 chapter_ns 传入")
    samples: List[dict] = []
    bodies: List[str] = []

    if texts is not None:
        if not isinstance(texts, list):
            raise LongformError(f"{label} texts 必须是字符串数组")
        for i, text in enumerate(texts):
            if not isinstance(text, str):
                raise LongformError(
                    f"{label} texts[{i}] 必须是字符串，"
                    f"实际收到 {type(text).__name__}")
            if not text.strip():
                raise LongformError(f"{label} texts[{i}] 不能为空")
            if len(text) > MAX_SAMPLE_CHARS:
                raise LongformError(
                    f"{label} texts[{i}] 共 {len(text)} 字符，"
                    f"超过单条上限 {MAX_SAMPLE_CHARS}")
            stats = lint.text_stats(text)
            samples.append({"kind": "text", "index": i + 1,
                            "chars": stats["chars_no_space"],
                            "sha256": _sha256(text)})
            bodies.append(text)

    if chapter_ns is not None:
        if not isinstance(chapter_ns, list):
            raise LongformError(f"{label} chapter_ns 必须是整数数组")
        for n in chapter_ns:
            sample, body = _chapter_sample(s, pid, n)
            samples.append(sample)
            bodies.append(body)

    total = sum(int(item["chars"]) for item in samples)
    if len(samples) < BLIND_MIN_SAMPLES:
        raise LongformError(
            f"{label}样本不足：至少 {BLIND_MIN_SAMPLES} 个样本 / "
            f"{BLIND_MIN_CHARS} 字，当前 {len(samples)} 个")
    if total < BLIND_MIN_CHARS:
        raise LongformError(
            f"{label}样本字数不足：至少 {BLIND_MIN_CHARS} 字，当前 {total} 字")
    if total > MAX_TOTAL_CHARS:
        raise LongformError(
            f"{label}样本总字数 {total} 超过上限 {MAX_TOTAL_CHARS}；请减少样本")
    return samples, bodies, total


def blind_test(s: Any, pid: str, *, reference_texts: Optional[list] = None,
               candidate_texts: Optional[list] = None,
               reference_chapter_ns: Optional[list] = None,
               candidate_chapter_ns: Optional[list] = None,
               profile_id: Optional[str] = None,
               threshold: Any = 75) -> dict:
    """本地、确定性的文风盲测：只比文本特征，不读写正文与文风档案。

    两侧各至少 1 个样本 / 100 字；八项特征逐文本取均值后按相对误差算相似度，
    平均 ×100 得 ``base_score``；禁用词命中按候选每千字 ×2 扣分（最多 20）。
    结果写一条 ``style_blind_test`` 审计（只存 sha256 / 字数，不存正文）。
    """
    s._require_project(pid)
    threshold = _blind_threshold(threshold)

    banned: List[str] = []
    if profile_id is not None:
        row = s.style_profile_get(pid, profile_id)
        if row is None:
            raise LongformError(f"文风档案不存在：{profile_id}（项目 {pid}）")
        profile = row.get("profile")
        if isinstance(profile, dict) and isinstance(profile.get("banned"), list):
            banned = [w for w in profile["banned"] if isinstance(w, str) and w.strip()]
    else:
        stored = s.style_get(pid, "banned_words")
        if isinstance(stored, list):
            banned = [w for w in stored if isinstance(w, str) and w.strip()]

    ref_samples, ref_bodies, ref_total = _blind_corpus(
        s, pid, reference_texts, reference_chapter_ns, "参考")
    cand_samples, cand_bodies, cand_total = _blind_corpus(
        s, pid, candidate_texts, candidate_chapter_ns, "候选")

    ref_feats = _corpus_features(ref_bodies)
    cand_feats = _corpus_features(cand_bodies)
    dimensions = []
    similarities = []
    for key, label in _BLIND_DIMENSIONS:
        sim = _similarity(ref_feats[key], cand_feats[key])
        similarities.append(sim)
        dimensions.append({
            "key": key,
            "label": label,
            "reference": round(ref_feats[key], 3),
            "candidate": round(cand_feats[key], 3),
            "similarity": round(sim, 3),
        })

    base_score = round(sum(similarities) / len(similarities) * 100, 1)

    joined = "\n".join(cand_bodies)
    banned_hits = sum(joined.count(word) for word in banned)
    penalty = min(BLIND_MAX_PENALTY, banned_hits / cand_total * 1000 * 2) \
        if cand_total else 0.0
    penalty = round(penalty, 1)
    score = max(0.0, round(base_score - penalty, 1))
    passed = score >= threshold

    samples = {"reference": len(ref_samples), "candidate": len(cand_samples)}
    pid_out = profile_id if isinstance(profile_id, str) else None
    report = {
        "kind": "style_blind_test",
        "profile_id": pid_out,
        "score": score,
        "base_score": base_score,
        "penalty": penalty,
        "passed": passed,
        "threshold": threshold,
        "banned_hits": banned_hits,
        "samples": samples,
        "corpora": {
            "reference": {"chars": ref_total,
                          "sha256": _sha256("\n".join(ref_bodies))},
            "candidate": {"chars": cand_total,
                          "sha256": _sha256("\n".join(cand_bodies))},
        },
    }
    audit = s.audit_add(pid, report)

    return {
        "ok": True,
        "blind": True,
        "score": score,
        "base_score": base_score,
        "penalty": penalty,
        "passed": passed,
        "threshold": threshold,
        "dimensions": dimensions,
        "banned_hits": banned_hits,
        "samples": samples,
        "profile_id": pid_out,
        "audit_id": audit["id"],
        "note": BLIND_NOTE,
    }


def apply(s: Any, pid: str, profile_id: str) -> dict:
    """把档案写进项目 style；原禁用词合并去重，不覆盖其它键、不写正文。"""
    s._require_project(pid)
    profile_id = _require_str(profile_id, "profile_id", max_len=2 * MAX_NAME_LEN)
    row = s.style_profile_get(pid, profile_id)
    if row is None:
        raise LongformError(f"文风档案不存在：{profile_id}（项目 {pid}）")
    profile = row.get("profile")
    if not isinstance(profile, dict):
        raise LongformError(f"文风档案内容损坏：{profile_id}")

    existing = s.style_get(pid, "banned_words")
    merged: List[str] = []
    seen = set()
    for word in (existing if isinstance(existing, list) else []) + \
            (profile.get("banned") or []):
        if isinstance(word, str) and word not in seen:
            seen.add(word)
            merged.append(word)

    values = {
        "style_profile_id": profile_id,
        "style_prompt": profile.get("style_prompt") or "",
        "thinking_genes": profile.get("thinking_genes") or [],
        "writing_genes": profile.get("writing_genes") or [],
        "banned_words": merged,
        "style_limitations": profile.get("limitations") or "",
    }
    for key, value in values.items():
        s.style_set(pid, key, value)

    return {"ok": True, "id": profile_id, "name": row["name"],
            "applied": list(values), "banned_count": len(merged)}
