"""作品变体（profile）档案夹。

一个变体 = ``profiles/`` 下的一个文件夹：:

    profiles/<变体名>/
    ├── profile.json       元信息 + 覆盖 + 输出目录（支持 // 与 /* */ 注释）
    ├── overrides/         可选，与 defaults.yaml 同构的覆盖片段
    └── exports/           正文落盘处（可在 profile.json 里改到任意本地绝对路径）

设计原则（来自对若干开源长篇写作项目的归纳）：

1. **源码只读，作品可写。** 工具箱本体保持不动，所有针对某一写作方向或某一
   二创作品的微调都落在 ``profiles/`` 下；删掉文件夹即删除变体。
2. **SQLite 是唯一权威，导出目录是派生视图。** ``exports/`` 里的 md 删了可以
   从数据库重建，反过来不行——所以不允许用编辑过的 md 反向覆盖数据库。
3. **零第三方依赖。** 标准库没有 YAML，因此用「支持注释的 JSON」并自带解析器。

环境变量 ``LONGFORM_PROFILES_DIR`` 可把整套变体放到任意位置（如网盘目录）。

本模块还负责读取 ``config/models.json``（模型档位 + 创作阶段剧本）——
之所以放这里而不是 store，是因为它就是"读文件 + 归一化"，与数据库无关，
符合"文件系统逻辑只在 profile.py"的分工纪律。
环境变量 ``LONGFORM_CONFIG_DIR`` 可把配置目录指到别处。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from typing import Any, Dict, List, Optional, Tuple

from store import (  # 不会形成循环依赖
    MAX_SEARCH_LIMIT,
    LongformError,
    _require_int,
    _require_str,
)

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
DEFAULT_PROFILES_DIR = os.path.join(REPO_ROOT, "profiles")
PROFILES_DIR = os.environ.get("LONGFORM_PROFILES_DIR", DEFAULT_PROFILES_DIR)

PROFILE_FILE = "profile.json"
TEMPLATE_NAME = "_default"
MAX_NAME_LEN = 80
MAX_DESC_LEN = 500
MAX_EXPORT_DIR_LEN = 400
# 覆盖片段的嵌套深度上限。手写配置出现 12 层以上的嵌套基本可以断定是
# 复制粘贴出的环或脚本产物，早报错比让 deep_merge 递归到栈溢出友好。
MAX_OVERRIDE_DEPTH = 12

# 导出默认值。变体未写 export 段时用它；项目 config["output"] 的
# export_format / save_every_version 也会映射到这里（见 server._export_cfg）。
DEFAULT_EXPORT: Dict[str, Any] = {
    "dir": "exports",       # 相对变体文件夹；也可填任意本地绝对路径
    "format": "md",         # md | txt
    "versioned": False,     # true 时文件名带 _v<N>，改稿永不覆盖历史稿
    "front_matter": True,   # 是否在正文前写 YAML front matter
}

# 全局创作偏好：存在 profiles/_default/profile.json 的 preferences 段。
# 不新增表，也不覆盖变体的其他字段；项目级覆盖仍走项目 config。
DEFAULT_PREFERENCES: Dict[str, Any] = {
    "genre": "",
    "pov": "第三人称",
    "style": "",
    "chapter_words": 2500,
    "banned_words": [],
    "template": "",
}
CHAPTER_WORDS_RANGE = (500, 20000)
MAX_POV_LEN = 40
MAX_BANNED_WORDS = 50
MAX_BANNED_WORD_LEN = 64

# ---------------------------------------------------------------- 配置目录
# 模型路由与阶段剧本的落点。与 DEFAULT_PROFILES_DIR 同级，都可用环境变量挪走。
CONFIG_DIR = os.environ.get("LONGFORM_CONFIG_DIR", os.path.join(REPO_ROOT, "config"))
# 模块级变量，但**只在函数体内读取**——测试靠 monkeypatch 这个变量做降级回归。
MODELS_JSON = os.path.join(CONFIG_DIR, "models.json")

MODEL_TIERS = ("planning", "production", "qa", "utility")
STAGES = ("master", "volume", "chapter", "draft", "qa", "utility")
PLACEHOLDER_PREFIX = "your-"        # name 仍以此开头 = 使用人还没填真实模型
_TEMPERATURE_RANGE = (0.0, 2.0)
_MAX_OUTPUT_TOKENS_RANGE = (1, 200_000)

# 内置兜底：models.json 缺失或损坏时用它，保证 stage_advice 永远有话说。
# 日常改配置请改 config/models.json；这里改了不会被 check_config.py 校验，
# 因此只应作为"最后一道防线"存在，不要当成配置源。
DEFAULT_MODELS: Dict[str, Any] = {
    "planning": {"name": "your-strong-model", "temperature": 0.4, "max_output_tokens": 4000},
    "production": {"name": "your-writing-model", "temperature": 0.8, "max_output_tokens": 8000},
    "qa": {"name": "your-strong-model", "temperature": 0.2, "max_output_tokens": 3000},
    "utility": {"name": "your-cheap-model", "temperature": 0.1, "max_output_tokens": 1500},
}

# 阶段 → 档位。只有 6 行，是"语义"而非"可调参数"，因此留在代码里当兜底。
BUILTIN_STAGE_TIER: Dict[str, str] = {
    "master": "planning", "volume": "planning", "chapter": "planning",
    "draft": "production", "qa": "qa", "utility": "utility",
}
BUILTIN_STAGE_LABEL: Dict[str, str] = {
    "master": "总纲要", "volume": "卷纲要", "chapter": "章纲要",
    "draft": "正文", "qa": "审校", "utility": "杂务",
}
# 兜底剧本：够用于"该用什么模型 + 找谁 + 调什么"，不含 output_contract
# （契约是给人看的长文本，缺失时宁可留空并提示，也不在代码里维护第二份）。
DEFAULT_STAGES: Dict[str, Any] = {
    "master": {"tier": "planning", "expert": "02-架构专家", "skill": "outline-architect",
               "tools": ["stage_advice", "outline_set", "outline_tree", "outline_next"],
               "reason": "一锤定音的全局结构决策，改一次成本极高，要强推理"},
    "volume": {"tier": "planning", "expert": "02-架构专家", "skill": "outline-architect",
               "tools": ["stage_advice", "outline_set", "outline_list", "outline_next"],
               "reason": "一卷的起承转合与章号区间，仍是结构决策"},
    "chapter": {"tier": "planning", "expert": "02-架构专家", "skill": "outline-architect",
                "tools": ["stage_advice", "outline_set", "outline_brief", "outline_next"],
                "reason": "章纲决定正文走向，写坏了正文全废，值得用好模型"},
    "draft": {"tier": "production", "expert": "03-写作专家", "skill": "chapter-writer",
              "tools": ["outline_brief", "packet_assemble", "chapter_save"],
              "reason": "长文扩写，要文风稳定与篇幅控制，随机性可以高一点"},
    "qa": {"tier": "qa", "expert": "04-审校专家", "skill": "consistency-auditor",
           "tools": ["audit_log", "chapter_diff", "foreshadow_overdue", "fact_list"],
           "reason": "一致性核查，要严谨、低随机，避免为了'好看'改坏事实"},
    "utility": {"tier": "utility", "expert": None, "skill": None,
                "tools": ["ledger_stats", "project_export", "chapter_export"],
                "reason": "摘要、抽取、格式化这类机械活，便宜模型足够"},
}

_UNSAFE_NAME = re.compile(r'[\x00-\x1f<>:"\\|?*/]+')
_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


# ============================================================
# 带注释的 JSON（零依赖）
# ============================================================
def strip_json_comments(text: str) -> str:
    """剥离 JSON 中的 ``//`` 与 ``/* */`` 注释，不误伤字符串字面量。

    手写配置时，"临时注释掉某一行" 是最高频的动作；为此引入 PyYAML 不值得，
    标准库又没有 YAML。这个约 30 行的剥离器让 profile.json 既能被机器解析，
    又能像人写的配置文件那样带说明。
    """
    out: List[str] = []
    i, n = 0, len(text)
    in_string = False
    escaped = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def load_jsonc(path: str, *, field: str = "profile") -> Dict[str, Any]:
    """读取带注释的 JSON 对象文件。"""
    if not os.path.isfile(path):
        raise LongformError(f"{field} 文件不存在：{path}")
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except OSError as e:
        raise LongformError(f"{field} 文件无法读取：{e}") from None
    try:
        obj = json.loads(strip_json_comments(raw))
    except json.JSONDecodeError as e:
        raise LongformError(
            f"{field} 文件不是合法 JSON（第 {e.lineno} 行第 {e.colno} 列）：{e.msg}"
        ) from None
    if not isinstance(obj, dict):
        raise LongformError(f"{field} 文件顶层必须是对象，实际是 {type(obj).__name__}")
    return obj


def dump_jsonc(path: str, obj: Dict[str, Any]) -> None:
    """写成缩进 JSON，中文不转义，便于直接手写。

    先写同目录 ``.tmp`` 再 ``os.replace``，避免写到一半被中断时留下半截
    profile.json——偏好与导出目录都靠这个文件，损坏后整台服务器都会读不到变体。
    """
    target = os.path.abspath(path)
    directory = os.path.dirname(target) or "."
    os.makedirs(directory, exist_ok=True)
    tmp = target + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


# ============================================================
# 工具
# ============================================================
def safe_name(name: str) -> str:
    """文件夹名：去掉路径分隔符与控制字符。"""
    s = _UNSAFE_NAME.sub("_", name.strip()).strip().strip(".")
    return s[:MAX_NAME_LEN]


def safe_filename(text: str, max_len: int = 60) -> str:
    """文件名片段：去掉 Windows 与 POSIX 都不接受的字符。"""
    s = _UNSAFE_FILENAME.sub("_", (text or "").strip()).strip().strip(".")
    return s[:max_len] or "untitled"


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """深度合并，后者覆盖前者；只合并 dict，其余类型整体替换。"""
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _depth(obj: Any, level: int = 0) -> int:
    """测量嵌套深度，用于拒绝病态的覆盖片段。"""
    if not isinstance(obj, dict) or not obj:
        return level
    return max(_depth(v, level + 1) for v in obj.values())


def ensure_root() -> str:
    """确保 ``profiles/`` 存在并返回其路径。"""
    os.makedirs(PROFILES_DIR, exist_ok=True)
    return PROFILES_DIR


def ensure_template(force: bool = False) -> str:
    """确保 ``_default`` 模板存在；没有就现场生成一份。

    模板是"复制出新变体"的源头，缺了它 profile_create 就只能得到空壳。
    ``force=True`` 时把 ``profile.json`` 复位——手写配置改坏了可以直接修回来。
    """
    root = ensure_root()
    tdir = os.path.join(root, TEMPLATE_NAME)
    path = os.path.join(tdir, PROFILE_FILE)
    if os.path.isfile(path) and not force:
        return tdir
    os.makedirs(os.path.join(tdir, "overrides"), exist_ok=True)
    dump_jsonc(path, {
        "name": TEMPLATE_NAME,
        "description": "基线模板：不覆盖任何默认值，仅提供目录约定。可用作 profile_create 的 base。",
        "base": "",
        "overrides": {},
        "export": dict(DEFAULT_EXPORT),
    })
    return tdir


def profile_dir(name: str) -> str:
    return os.path.join(PROFILES_DIR, name)


def root() -> str:
    return PROFILES_DIR


# ============================================================
# 读取 / 校验
# ============================================================
def _normalize_export(raw: Any, where: str) -> Dict[str, Any]:
    """补齐 export 段并校验取值。

    手写配置里 ``"versioned": "true"`` 这种值不该让整台服务器崩，
    但也不能静默当成默认——这里统一收敛成合法值。
    """
    if raw is None:
        return dict(DEFAULT_EXPORT)
    if not isinstance(raw, dict):
        raise LongformError(f"{where} 的 export 必须是对象，实际是 {type(raw).__name__}")
    out = dict(DEFAULT_EXPORT)
    for k, v in raw.items():
        if v is None:
            continue
        out[k] = v
    d = out.get("dir")
    if not isinstance(d, str) or not d.strip():
        out["dir"] = DEFAULT_EXPORT["dir"]
    else:
        out["dir"] = _require_str(d.strip(), f"{where}.export.dir",
                                  max_len=MAX_EXPORT_DIR_LEN)
    fmt = str(out.get("format") or "md").strip().lower()
    out["format"] = fmt if fmt in ("md", "txt", "markdown") else DEFAULT_EXPORT["format"]
    if fmt == "markdown":
        out["format"] = "md"
    for flag in ("versioned", "front_matter"):
        out[flag] = bool(out.get(flag, DEFAULT_EXPORT[flag]))
    return out


def read(name: str) -> Dict[str, Any]:
    """读取并规范化一个变体。缺失的键补默认值。

    变体名先经 ``safe_name`` 收敛并校验必须落在 ``profiles/`` 之内：
    ``read`` 是 ``profile_get`` 等工具的入口，不能让 ``../../x`` 读到外部文件。
    """
    _require_str(name, "name", max_len=MAX_NAME_LEN)
    if ("/" in name or "\\" in name or name in (".", "..")
            or name.startswith("..")):
        raise LongformError(f"变体名不合法：{name!r}")
    resolved = safe_name(name)
    pdir = profile_dir(resolved)
    if not resolved or not _within_root(pdir):
        raise LongformError(f"变体名不合法：{name!r}")
    if not os.path.isdir(pdir):
        raise LongformError(f"变体不存在：{name}（可用 profile_list 查看现有变体）")
    raw = load_jsonc(os.path.join(pdir, PROFILE_FILE))
    cfg: Dict[str, Any] = dict(_BASE_PROFILE)
    cfg.update(raw)
    cfg["name"] = resolved
    cfg["dir"] = pdir
    ov = cfg.get("overrides")
    if ov is None:
        ov = {}
    if not isinstance(ov, dict):
        raise LongformError(f"变体 {name} 的 overrides 必须是对象，实际是 {type(ov).__name__}")
    if _depth(ov) > MAX_OVERRIDE_DEPTH:
        raise LongformError(
            f"变体 {name} 的 overrides 嵌套过深（{_depth(ov)} 层，上限 {MAX_OVERRIDE_DEPTH}）")
    cfg["overrides"] = ov
    cfg["export"] = _normalize_export(cfg.get("export"), f"变体 {name}")
    cfg["description"] = str(cfg.get("description") or "")[:MAX_DESC_LEN]
    cfg["base"] = str(cfg.get("base") or "")
    return cfg


_BASE_PROFILE: Dict[str, Any] = {
    "name": TEMPLATE_NAME,
    "description": "基线变体：不覆盖任何默认值，仅提供输出目录约定",
    "base": "",
    "overrides": {},
    "export": dict(DEFAULT_EXPORT),
}


def effective_config(name: Optional[str], defaults: Optional[dict] = None) -> Dict[str, Any]:
    """把变体覆盖应用到一份默认配置上，得到最终生效值。"""
    merged = dict(defaults or {})
    if not name:
        return merged
    return deep_merge(merged, read(name).get("overrides") or {})


def list_profiles(bound: Optional[str] = None) -> List[dict]:
    """列出所有变体。以 ``.`` 开头的目录（如 .git）一律跳过。

    ``bound`` 传入当前项目绑定的变体名时，结果里会多一个 ``bound`` 布尔字段，
    省掉调用方再比对一次。
    """
    if not os.path.isdir(PROFILES_DIR):
        return []
    out = []
    for entry in sorted(os.listdir(PROFILES_DIR)):
        pdir = os.path.join(PROFILES_DIR, entry)
        if not os.path.isdir(pdir) or entry.startswith("."):
            continue
        item: Dict[str, Any] = {"name": entry, "dir": pdir, "valid": True,
                                "error": None, "is_template": entry == TEMPLATE_NAME}
        if bound is not None:
            item["bound"] = (entry == bound)
        try:
            cfg = read(entry)
            edir = resolve_export_dir(cfg)
            item.update({
                "description": cfg.get("description", ""),
                "export_dir": edir,
                "has_overrides": bool(cfg.get("overrides")),
                "base": cfg.get("base", ""),
                "exported_files": _count_exports(edir),
            })
        except LongformError as e:
            item.update(valid=False, error=str(e))
        out.append(item)
    return out


def _count_exports(directory: str) -> int:
    try:
        return len([f for f in os.listdir(directory) if f.endswith((".md", ".txt"))])
    except OSError:
        return 0


def resolve_export_dir(cfg: Dict[str, Any], base_dir: Optional[str] = None) -> str:
    """解析输出目录：相对路径相对 ``base_dir``，绝对路径直接使用。

    ``base_dir`` 缺省时取 ``cfg["dir"]``（变体文件夹）——这是变体内
    ``export.dir`` 的语义。调用方想换基准（例如"相对当前工作目录"）时显式传入，
    这样"没绑定变体也要导出"的场景才有确定的落点。
    """
    d = (cfg.get("export") or {}).get("dir") or DEFAULT_EXPORT["dir"]
    if not isinstance(d, str) or not d.strip():
        d = DEFAULT_EXPORT["dir"]
    d = d.strip()
    if os.path.isabs(d):
        return os.path.normpath(d)
    base = base_dir or cfg.get("dir") or PROFILES_DIR
    return os.path.normpath(os.path.join(base, d))


# ============================================================
# 模型路由与阶段剧本（config/models.json）
# ============================================================
def _clamp_number(value: Any, default: float, lo: float, hi: float) -> Tuple[float, bool]:
    """把配置里的数值收敛到合法区间；返回 ``(值, 是否被纠正)``。

    手写 JSON 出现 ``"0.8"`` 或 ``true`` 不该让服务崩，也不该静默当成默认——
    收敛的同时把"被纠正过"告诉调用方，由它决定是否提示使用人。
    """
    if value is None:
        return default, False
    if isinstance(value, bool):
        return default, True
    if isinstance(value, (int, float)):
        v = float(value)
    elif isinstance(value, str):
        try:
            v = float(value.strip())
        except ValueError:
            return default, True
    else:
        return default, True
    if v < lo or v > hi:
        return max(lo, min(hi, v)), True
    return v, False


def _normalize_tiers(raw: Any, warnings: List[str], where: str) -> Dict[str, Any]:
    """逐档位归一化：缺失回落、类型错与越界收敛、占位符提示。"""
    out: Dict[str, Any] = {k: dict(v) for k, v in DEFAULT_MODELS.items()}
    if not isinstance(raw, dict):
        if raw is not None:
            warnings.append(f"{where} 的 tiers 不是对象（{type(raw).__name__}）；全部回落内置默认值")
        return out
    for tier in MODEL_TIERS:
        base = out[tier]
        item = raw.get(tier)
        if item is None:
            continue            # 内置值已经在那儿，不算错误
        if not isinstance(item, dict):
            warnings.append(f"{where} 档位 {tier} 不是对象（{type(item).__name__}）；回落内置默认值")
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            warnings.append(f"{where} 档位 {tier} 的 name 缺失；回落内置默认值 {base['name']}")
            name = base["name"]
        else:
            name = name.strip()
            if name.startswith(PLACEHOLDER_PREFIX):
                warnings.append(
                    f"{where} 档位 {tier} 的 name 仍是占位符（{name}），"
                    f"请替换成你平台上的真实模型标识")
        temp, fixed_t = _clamp_number(item.get("temperature"), base["temperature"],
                                      *_TEMPERATURE_RANGE)
        if fixed_t:
            warnings.append(
                f"{where} 档位 {tier} 的 temperature 非法或越界（{item.get('temperature')!r}）；"
                f"已收敛为 {temp}（合法区间 {list(_TEMPERATURE_RANGE)}）")
        toks, fixed_n = _clamp_number(item.get("max_output_tokens"),
                                      base["max_output_tokens"], *_MAX_OUTPUT_TOKENS_RANGE)
        if fixed_n:
            warnings.append(
                f"{where} 档位 {tier} 的 max_output_tokens 非法或越界"
                f"（{item.get('max_output_tokens')!r}）；已收敛为 {int(toks)}")
        out[tier] = {"name": name, "temperature": round(temp, 3),
                     "max_output_tokens": int(toks)}
    return out


def _normalize_stages(raw: Any, warnings: List[str], where: str) -> Dict[str, Any]:
    """逐阶段归一化剧本；不认识的阶段直接跳过（配置可以只写关心的阶段）。"""
    out: Dict[str, Any] = {}
    if not isinstance(raw, dict):
        if raw is not None:
            warnings.append(f"{where} 的 stages 不是对象（{type(raw).__name__}）；阶段剧本不可用")
        return out
    for stage, item in raw.items():
        if not isinstance(item, dict):
            warnings.append(f"{where} 阶段 {stage} 不是对象（{type(item).__name__}）；已跳过")
            continue
        tier = item.get("tier")
        if tier not in MODEL_TIERS:
            warnings.append(
                f"{where} 阶段 {stage} 的 tier 非法（{tier!r}）；"
                f"回落 {BUILTIN_STAGE_TIER.get(stage, 'planning')}")
            tier = BUILTIN_STAGE_TIER.get(stage, "planning")
        tools = item.get("tools")
        out[str(stage)] = {
            "tier": tier,
            "label": str(item.get("label") or BUILTIN_STAGE_LABEL.get(stage, stage))[:60],
            "reason": str(item.get("reason") or "")[:500],
            "expert": item.get("expert") or None,
            "skill": item.get("skill") or None,
            "tools": [str(t) for t in tools] if isinstance(tools, list) else [],
            "output_contract": (item.get("output_contract")
                                if isinstance(item.get("output_contract"), dict) else {}),
        }
    return out


def load_models(path: Optional[str] = None,
                layers: Optional[List[dict]] = None) -> Tuple[Dict[str, Any], List[str]]:
    """读取模型路由与阶段剧本，**任何异常都不抛出**，一律转成 warnings。

    这是"读操作宽容"的典型场景：配置写坏了最多是建议不准，
    不该让整个 MCP 服务起不来。

    ``layers`` 是按优先级从低到高排列的覆盖层（项目 config、变体 overrides 等），
    逐层 deep_merge 到文件配置之上，复用项目既有的优先级不变量。
    """
    warnings: List[str] = []
    p = path or MODELS_JSON

    if not os.path.isfile(p):
        warnings.append(f"模型配置文件不存在：{p}；已回落到内置默认值")
        return {"tiers": {k: dict(v) for k, v in DEFAULT_MODELS.items()},
                "stages": {k: dict(v) for k, v in DEFAULT_STAGES.items()},
                "source": "builtin-fallback", "path": p}, warnings
    try:
        obj = load_jsonc(p, field="models")
    except LongformError as e:
        warnings.append(f"模型配置文件不可读（{e}）；已回落到内置默认值")
        return {"tiers": {k: dict(v) for k, v in DEFAULT_MODELS.items()},
                "stages": {k: dict(v) for k, v in DEFAULT_STAGES.items()},
                "source": "builtin-fallback", "path": p}, warnings

    tiers = _normalize_tiers(obj.get("tiers"), warnings, "models.json")
    stages = _normalize_stages(obj.get("stages"), warnings, "models.json")
    if not stages:
        warnings.append("models.json 的 stages 为空；已回落到内置阶段剧本")

    for i, layer in enumerate(layers or []):
        if not isinstance(layer, dict):
            warnings.append(f"第 {i + 1} 层模型覆盖不是对象（{type(layer).__name__}）；已跳过")
            continue
        if "tiers" in layer:
            tiers = deep_merge(tiers, _normalize_tiers(
                layer.get("tiers"), warnings, f"覆盖层#{i + 1}"))
        if "stages" in layer:
            stages = deep_merge(stages, _normalize_stages(
                layer.get("stages"), warnings, f"覆盖层#{i + 1}"))

    # 兜底：模型名被覆盖成空串的极端情况，补回内置值并提示。
    for tier in MODEL_TIERS:
        if not str(tiers[tier].get("name") or "").strip():
            warnings.append(f"档位 {tier} 的 name 最终为空；补回内置默认值")
            tiers[tier]["name"] = DEFAULT_MODELS[tier]["name"]

    return {"tiers": tiers, "stages": stages, "source": "models.json",
            "path": p}, warnings


def stage_defaults() -> Dict[str, Any]:
    """阶段剧本的内置兜底（供 stage_advice 在文件缺失时展示）。"""
    return {k: dict(v) for k, v in DEFAULT_STAGES.items()}


# ============================================================
# 增 / 删
# ============================================================
def _within_root(path: str) -> bool:
    """路径必须落在 profiles/ 之内。

    ``safe_name`` 已经清掉了分隔符，这里是第二道闸：万一将来有人改了
    ``PROFILES_DIR`` 的解析方式（软链接、``..`` 拼接），删除操作也不会溢出到
    变体目录之外。
    """
    root = os.path.abspath(PROFILES_DIR)
    target = os.path.abspath(path)
    return target == root or target.startswith(root + os.sep)


def create(name: str, description: str = "", base: str = TEMPLATE_NAME,
           export_dir: Optional[str] = None) -> dict:
    """从模板复制出一个新变体。

    复制除 ``exports/`` 之外的全部内容——历史正文不该跟着模板走。
    """
    _require_str(name, "name", max_len=MAX_NAME_LEN)
    ensure_root()
    target_name = safe_name(name)
    if not target_name or target_name in (".", ".."):
        raise LongformError(f"变体名不合法：{name!r}")
    target = profile_dir(target_name)
    if os.path.exists(target):
        raise LongformError(f"变体已存在：{target_name}")
    if not _within_root(target):
        raise LongformError(f"变体目录越界：{target}")

    base = (base or "").strip()
    if base:
        if safe_name(base) == TEMPLATE_NAME:
            # 内置模板随时可以现场生成——全新 profiles/ 上的第一次
            # profile_create 不该因为"模板还没建"而失败。
            ensure_template()
        src = profile_dir(safe_name(base))
        if not os.path.isdir(src):
            raise LongformError(
                f"模板变体不存在：{safe_name(base)}（可用 profile_list 查看现有变体）")
        shutil.copytree(src, target, ignore=shutil.ignore_patterns("exports"))
    else:
        os.makedirs(os.path.join(target, "overrides"), exist_ok=True)

    exp = dict(DEFAULT_EXPORT)
    if export_dir:
        exp["dir"] = _require_str(export_dir.strip(), "export_dir",
                                  max_len=MAX_EXPORT_DIR_LEN)
    cfg: Dict[str, Any] = {
        "name": target_name,
        "description": _require_str(description or "", "description",
                                    allow_empty=True, max_len=MAX_DESC_LEN),
        "base": safe_name(base) if base else "",
        "overrides": {},
        "export": exp,
    }
    dump_jsonc(os.path.join(target, PROFILE_FILE), cfg)
    return {"ok": True, "name": target_name, "dir": target,
            "export_dir": resolve_export_dir({"dir": target, "export": exp})}


def delete(name: str, confirm: Optional[str] = None, *, dry_run: bool = False) -> dict:
    """删除整个变体文件夹。破坏性操作，要求 confirm 与变体名逐字一致。"""
    _require_str(name, "name", max_len=MAX_NAME_LEN)
    target_name = safe_name(name)
    if confirm != target_name:
        raise LongformError(
            f"删除变体需要显式确认：请传 confirm=\"{target_name}\"（必须与变体名完全一致）")
    pdir = profile_dir(target_name)
    if not os.path.isdir(pdir):
        raise LongformError(f"变体不存在：{target_name}")
    if not _within_root(pdir):
        raise LongformError(f"变体目录越界，拒绝删除：{pdir}")

    files = 0
    for _, _, names in os.walk(pdir):
        files += len(names)
    if dry_run:
        return {"ok": True, "dry_run": True, "name": target_name,
                "dir": pdir, "files": files, "would_remove": files}

    shutil.rmtree(pdir)
    return {"ok": True, "name": target_name, "dir": pdir, "files_removed": files}


def set_export_dir(name: str, directory: str) -> dict:
    """改输出目录。可以是绝对路径（任意本地位置），也可以是相对变体文件夹的路径。"""
    _require_str(name, "name", max_len=MAX_NAME_LEN)
    _require_str(directory, "dir", max_len=MAX_EXPORT_DIR_LEN)
    pdir = profile_dir(safe_name(name))
    if not os.path.isdir(pdir):
        raise LongformError(f"变体不存在：{safe_name(name)}")
    cfg_path = os.path.join(pdir, PROFILE_FILE)
    cfg = load_jsonc(cfg_path)
    cfg.setdefault("export", {})
    if not isinstance(cfg["export"], dict):
        cfg["export"] = {}
    cfg["export"]["dir"] = directory.strip()
    dump_jsonc(cfg_path, cfg)
    return {"ok": True, "name": safe_name(name),
            "export_dir": resolve_export_dir(
                {"dir": pdir, "export": _normalize_export(cfg["export"], f"变体 {name}")})}


# ============================================================
# 创作偏好（profiles/_default/profile.json 的 preferences 段）
# ============================================================
def _preferences_path() -> str:
    return os.path.join(PROFILES_DIR, TEMPLATE_NAME, PROFILE_FILE)


def _default_preferences() -> Dict[str, Any]:
    """返回默认偏好的独立副本；调用方随意修改都不会污染模块常量。"""
    return json.loads(json.dumps(DEFAULT_PREFERENCES))


def get_preferences() -> Dict[str, Any]:
    """读取全局创作偏好；文件或字段缺失时回落到默认值，任何异常都不抛出。"""
    out = _default_preferences()
    path = _preferences_path()
    if not os.path.isfile(path):
        return out
    try:
        raw = load_jsonc(path)
    except LongformError:
        return out
    prefs = raw.get("preferences")
    if not isinstance(prefs, dict):
        return out
    merged = deep_merge(out, {key: prefs[key] for key in prefs
                              if key in DEFAULT_PREFERENCES})
    return {key: merged.get(key, out[key]) for key in DEFAULT_PREFERENCES}


def _validate_preferences(patch: Dict[str, Any]) -> Dict[str, Any]:
    """校验偏好补丁：只允许白名单键，类型/范围不对就报可读错误。"""
    if not isinstance(patch, dict):
        raise LongformError(
            f"偏好设置必须是对象，实际收到 {type(patch).__name__}")
    unknown = [str(key) for key in patch if key not in DEFAULT_PREFERENCES]
    if unknown:
        raise LongformError(
            "不支持的偏好字段：" + "、".join(sorted(unknown))
            + "（允许：" + "、".join(DEFAULT_PREFERENCES) + "）")

    out: Dict[str, Any] = {}
    if "genre" in patch:
        out["genre"] = _require_str(
            patch["genre"], "genre", allow_empty=True,
            max_len=MAX_NAME_LEN).strip()
    if "pov" in patch:
        pov = _require_str(patch["pov"], "pov", max_len=MAX_POV_LEN).strip()
        if not pov:
            raise LongformError("参数 `pov` 不能为空")
        out["pov"] = pov
    if "style" in patch:
        out["style"] = _require_str(
            patch["style"], "style", allow_empty=True,
            max_len=MAX_DESC_LEN).strip()
    if "chapter_words" in patch:
        out["chapter_words"] = _require_int(
            patch["chapter_words"], "chapter_words",
            minimum=CHAPTER_WORDS_RANGE[0], maximum=CHAPTER_WORDS_RANGE[1])
    if "banned_words" in patch:
        words = patch["banned_words"]
        if not isinstance(words, list):
            raise LongformError(
                f"参数 `banned_words` 必须是字符串数组，实际收到 {type(words).__name__}")
        if len(words) > MAX_BANNED_WORDS:
            raise LongformError(
                f"参数 `banned_words` 条目过多：{len(words)}，上限 {MAX_BANNED_WORDS}")
        cleaned: List[str] = []
        for i, word in enumerate(words):
            if not isinstance(word, str):
                raise LongformError(
                    f"参数 `banned_words[{i}]` 必须是字符串，实际收到 {type(word).__name__}")
            text = word.strip()
            if not text:
                continue
            if len(text) > MAX_BANNED_WORD_LEN:
                raise LongformError(
                    f"参数 `banned_words[{i}]` 过长：{len(text)} 字符，"
                    f"上限 {MAX_BANNED_WORD_LEN}")
            if text not in cleaned:
                cleaned.append(text)
        out["banned_words"] = cleaned
    if "template" in patch:
        out["template"] = _require_str(
            patch["template"], "template", allow_empty=True,
            max_len=MAX_NAME_LEN).strip()
    return out


def set_preferences(patch: Dict[str, Any]) -> Dict[str, Any]:
    """合并写入全局创作偏好并返回完整偏好；保留 profile 的其他字段。"""
    validated = _validate_preferences(patch)
    path = _preferences_path()
    if not os.path.isfile(path):
        ensure_template()
    cfg = load_jsonc(path)
    current = cfg.get("preferences")
    merged_prefs = _default_preferences()
    if isinstance(current, dict):
        merged_prefs = deep_merge(
            merged_prefs,
            {key: current[key] for key in current if key in DEFAULT_PREFERENCES})
    merged_prefs = deep_merge(merged_prefs, validated)
    cfg["preferences"] = merged_prefs
    dump_jsonc(path, cfg)
    return merged_prefs


# ============================================================
# 正文导出（派生视图）
# ============================================================
def _fmt_time(ts: Optional[float]) -> Optional[str]:
    if not ts:
        return None
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def render_chapter_md(ch: dict, *, front_matter: bool = True) -> str:
    """把一条章节记录渲染成 markdown。带 front matter 供后续工具消费。"""
    n = ch.get("n")
    title = ch.get("title") or ""
    head = f"第{n}章 {title}".rstrip()
    lines: List[str] = []
    if front_matter:
        lines.append("---")
        lines.append(f"chapter: {n}")
        lines.append(f"version: {ch.get('version')}")
        lines.append(f"status: {ch.get('status')}")
        lines.append(f"words: {ch.get('words', len(ch.get('content') or ''))}")
        lines.append(f"title: {json.dumps(title, ensure_ascii=False)}")
        saved = _fmt_time(ch.get("created_at"))
        if saved:
            lines.append(f"saved_at: \"{saved}\"")
        lines.append(f"exported_at: \"{_fmt_time(time.time())}\"")
        lines.append("---")
        lines.append("")
    lines.append(f"# {head}")
    lines.append("")
    lines.append(ch.get("content") or "")
    return "\n".join(lines) + "\n"


def export_chapter(ch: dict, cfg: Dict[str, Any],
                   *, directory: Optional[str] = None,
                   force_version: Optional[int] = None) -> dict:
    """把一条章节记录写到文件，返回落盘路径。

    默认只写最新版（覆盖同名文件）；``export.versioned=true`` 时带 ``_v<N>``
    后缀，历史稿永不覆盖。
    """
    n = ch.get("n")
    version = force_version or ch.get("version")
    out_dir = directory or resolve_export_dir(cfg)
    os.makedirs(out_dir, exist_ok=True)

    ext = (cfg.get("export") or {}).get("format") or "md"
    if ext not in ("md", "txt"):
        ext = "md"
    base = f"第{n:03d}章_{safe_filename(ch.get('title') or '')}"
    if (cfg.get("export") or {}).get("versioned"):
        base = f"{base}_v{version}"
    path = os.path.join(out_dir, f"{base}.{ext}")

    text = render_chapter_md(ch, front_matter=bool(
        (cfg.get("export") or {}).get("front_matter", True)))
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return {"ok": True, "path": path, "n": n, "version": version,
            "bytes": len(text.encode("utf-8"))}


def export_project(s, pid: str, cfg: Dict[str, Any],
                   *, directory: Optional[str] = None) -> dict:
    """把一部作品的全部章节最新版一次性导出。"""
    out_dir = directory or resolve_export_dir(cfg)
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for row in s.chapter_list(pid):
        ch = s.chapter_get(pid, row["n"])
        if ch:
            written.append(export_chapter(ch, cfg, directory=out_dir))
    return {"ok": True, "dir": out_dir, "count": len(written), "files": written}


# ============================================================
# 工作目录分目录导出
# ============================================================
# 变体 export 只有一个平铺目录，适合"正文交稿"；但真正的写作现场需要
# 区分"大纲 / 草稿 / 素材 / 参考资料 / 成品"五类产物，否则改稿时会把
# 大纲和草稿混在一起，导一次覆盖一次。
WORKSPACE_SUBDIRS: Dict[str, str] = {
    "outline": "00-大纲",
    "drafts": "01-章节草稿",
    "materials": "02-素材",
    "references": "03-参考资料",
    "final": "04-成品",
}
# 素材与参考资料的分工：前者是要化进正文的原材料（摘抄、灵感），
# 后者是作者的案头资料（参考资料、背景调研），通常不进正文只影响判断。
_MATERIAL_DIRS: Dict[str, str] = {
    "quote": "materials", "idea": "materials",
    "reference": "references", "research": "references",
}


def _write_text(path: str, text: str) -> dict:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return {"path": path, "bytes": len(text.encode("utf-8"))}


def _outline_md(s, pid: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """渲染三份大纲文档：总纲 / 卷纲 / 章纲。无对应内容时返回 None。"""
    tree = s.outline_tree(pid, include_body=True)
    master, volumes, chapters = tree.get("master"), tree.get("volumes") or [], tree.get("chapters") or []

    m_md = None
    if master:
        lines = ["# 总纲要", "", f"- 节点 id：{master['id']}",
                 f"- 版本：v{master['version']}",
                 f"- 状态：{master['status']}", ""]
        data = master.get("data_json") or {}
        if data:
            lines.append("## 结构化字段")
            lines.append("")
            lines.append("```yaml")
            for k in sorted(data):
                lines.append(f"{k}: {json.dumps(data[k], ensure_ascii=False)}")
            lines.append("```")
            lines.append("")
        if (master.get("body") or "").strip():
            lines += ["## 正文", "", master["body"].strip(), ""]
        m_md = "\n".join(lines)

    v_md = None
    if volumes:
        lines = ["# 卷纲要", ""]
        for v in volumes:
            lines.append(f"## {v.get('title') or v['id']}（{v['id']}）")
            lines.append("")
            lines.append(f"- 章号区间：第 {v['start_chapter']}-{v['end_chapter']} 章")
            lines.append(f"- 版本：v{v['version']}　状态：{v['status']}")
            data = v.get("data_json") or {}
            for k in sorted(data):
                lines.append(f"- {k}: {json.dumps(data[k], ensure_ascii=False)}")
            lines.append("")
            if (v.get("body") or "").strip():
                lines += [v["body"].strip(), ""]
        v_md = "\n".join(lines)

    c_md = None
    if chapters:
        lines = ["# 章纲要", ""]
        for c in chapters:
            head = f"## 第 {c['chapter_n']} 章"
            if c.get("title"):
                head += f"　{c['title']}"
            vol = f"　（卷：{c['volume']}）" if c.get("volume") else ""
            lines.append(head + vol)
            lines.append("")
            lines.append(f"- 节点 id：{c['id']}　版本：v{c['version']}　状态：{c['status']}")
            data = c.get("data_json") or {}
            for k in sorted(data):
                lines.append(f"- {k}: {json.dumps(data[k], ensure_ascii=False)}")
            lines.append("")
            if (c.get("body") or "").strip():
                lines += [c["body"].strip(), ""]
        c_md = "\n".join(lines)
    return m_md, v_md, c_md


def _materials_md(materials: List[dict], heading: str) -> Optional[str]:
    if not materials:
        return None
    lines = [f"# {heading}", "",
             "> 每条素材都带来源；写作时引用处应标注来源，便于后期核查。", ""]
    for m in materials:
        title = m.get("title") or m.get("id")
        lines.append(f"## {title}")
        lines.append("")
        lines.append(f"- id：{m.get('id')}　种类：{m.get('kind')}")
        lines.append(f"- 来源：{m.get('source') or '（未标注）'}"
                     f"（{m.get('source_kind')}）")
        if m.get("tags"):
            lines.append(f"- 标签：{'、'.join(m['tags'])}")
        if m.get("chapter") is not None:
            lines.append(f"- 拟用于：第 {m['chapter']} 章")
        lines.append("")
        if (m.get("content") or "").strip():
            lines += [m["content"].strip(), ""]
    return "\n".join(lines)


def workspace_export(s, pid: str, directory: str,
                     include: Optional[List[str]] = None) -> dict:
    """把一部作品按「大纲 / 草稿 / 素材 / 参考资料 / 成品」分目录导出。

    与 ``export_project`` 的区别：后者是"正文交稿"（一个平铺目录），
    这里是"写作现场"（五类产物各归其位，可反复导出而不互相覆盖）。
    """
    _require_str(directory, "dir", max_len=MAX_EXPORT_DIR_LEN)
    wanted = [str(k) for k in (include or [])] or list(WORKSPACE_SUBDIRS)
    unknown = [k for k in wanted if k not in WORKSPACE_SUBDIRS]
    if unknown:
        raise LongformError(
            f"include 取值非法：{unknown}，允许的值为 {sorted(WORKSPACE_SUBDIRS)}")

    base = os.path.abspath(directory)
    os.makedirs(base, exist_ok=True)
    files: List[dict] = []
    counts: Dict[str, int] = {}

    def put(sub: str, name: str, text: Optional[str]) -> None:
        if not text:
            return
        files.append(_write_text(os.path.join(base, WORKSPACE_SUBDIRS[sub], name), text))
        counts[sub] = counts.get(sub, 0) + 1

    if "outline" in wanted:
        m_md, v_md, c_md = _outline_md(s, pid)
        put("outline", "总纲要.md", m_md)
        put("outline", "卷纲要.md", v_md)
        put("outline", "章纲要.md", c_md)

    if "drafts" in wanted or "final" in wanted:
        chapters = s.chapter_list(pid)
        for row in chapters:
            ch = s.chapter_get(pid, row["n"])
            if not ch:
                continue
            name = f"第{ch['n']:03d}章_{safe_filename(ch.get('title') or '')}.md"
            text = render_chapter_md(ch, front_matter=True)
            if "drafts" in wanted:
                put("drafts", name, text)
            if "final" in wanted and ch.get("status") == "approved":
                put("final", name, text)

    if "materials" in wanted or "references" in wanted:
        mats = s.material_list(pid, limit=MAX_SEARCH_LIMIT)
        buckets: Dict[str, List[dict]] = {"materials": [], "references": []}
        for m in mats:
            buckets[_MATERIAL_DIRS.get(m.get("kind") or "", "references")].append(m)
        if "materials" in wanted:
            put("materials", "素材索引.md",
                _materials_md(buckets["materials"], "素材（摘抄与灵感）"))
        if "references" in wanted:
            put("references", "参考资料索引.md",
                _materials_md(buckets["references"], "参考资料与背景调研"))

    return {"ok": True, "project": pid, "dir": base,
            "subdirs": {k: os.path.join(base, v)
                        for k, v in WORKSPACE_SUBDIRS.items() if k in wanted},
            "count": len(files), "counts": counts, "files": files}
