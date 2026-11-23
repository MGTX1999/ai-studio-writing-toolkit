"""运行环境配置：模型服务（OpenAI 兼容）与 IMA 连接。

与 ``config/defaults.yaml`` 的分工：defaults.yaml 参与项目/变体的**写作参数**
合并；这里只放「这台机器怎么连外部服务」，不进入任何项目配置。优先级：

    环境变量 > config/settings.json > 内置 DEFAULT_SETTINGS

密钥默认只从环境变量读取（``api_key_env`` 指定变量名），落盘到
settings.json 属于用户显式选择；读取一律脱敏，``public_view`` 是唯一
可以返回给界面/模型的形态。

本模块**绝不因为配置写坏而抛异常**：``load_settings`` 在任何情况下都返回
一份可用的默认结构，让本地写作不被外部服务拖垮。
"""

from __future__ import annotations

import copy
import json
import os
from typing import Any, Dict, List, Optional

from store import LongformError

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
CONFIG_DIR = os.environ.get("LONGFORM_CONFIG_DIR", os.path.join(REPO_ROOT, "config"))
SETTINGS_PATH = os.environ.get("LONGFORM_SETTINGS_FILE",
                               os.path.join(CONFIG_DIR, "settings.json"))

DEFAULT_SETTINGS: Dict[str, Any] = {
    "llm": {
        "enabled": False,
        "base_url": "",
        "model": "",
        "api_key": "",
        "api_key_env": "LONGFORM_LLM_API_KEY",
        "timeout": 60,
    },
    "ima": {
        "enabled": False,
        "base_url": "",
        "api_key": "",
        "api_key_env": "LONGFORM_IMA_API_KEY",
        "timeout": 15,
        "knowledge_base": "",
    },
    # 外部通知：webhook（Lark 群机器人）与本地 CLI 二选一，都为空即不发送。
    "notify": {
        "enabled": False,
        "webhook": "",
        "cli": "",
    },
}

# 环境变量名表；resolve_* 按字段逐个叠加，缺省时保留文件值。
_ENV_KEYS = {
    "llm": {
        "base_url": "LONGFORM_LLM_BASE_URL",
        "model": "LONGFORM_LLM_MODEL",
        "api_key": "LONGFORM_LLM_API_KEY",
        "timeout": "LONGFORM_LLM_TIMEOUT",
        "enabled": "LONGFORM_LLM_ENABLED",
    },
    "ima": {
        "base_url": "LONGFORM_IMA_BASE_URL",
        "api_key": "LONGFORM_IMA_API_KEY",
        "timeout": "LONGFORM_IMA_TIMEOUT",
        "enabled": "LONGFORM_IMA_ENABLED",
        "knowledge_base": "LONGFORM_IMA_KB",
    },
}

# configured 判定所需的字段，顺序即 missing 的输出顺序。
_REQUIRED = {
    "llm": ("enabled", "base_url", "model", "api_key"),
    "ima": ("enabled", "base_url", "api_key"),
}

_TRUE_WORDS = ("1", "true", "yes", "on", "y", "t")
_FALSE_WORDS = ("0", "false", "no", "off", "n", "f")

_MAX_TIMEOUT = 600
_MIN_TIMEOUT = 1


# ---------------------------------------------------------------- 通用工具
def _deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    """递归合并：字符串/数字/布尔直接覆盖，字典逐键合并。

    返回新对象，不修改入参——调用方经常直接把 DEFAULT_SETTINGS 当 base。
    """
    out = copy.deepcopy(base) if isinstance(base, dict) else {}
    if not isinstance(patch, dict):
        return out
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _section(settings: Optional[Dict[str, Any]], name: str) -> Dict[str, Any]:
    """取某个配置段；类型不对或缺失时视为空段，交给默认值兜底。"""
    if not isinstance(settings, dict):
        return {}
    sec = settings.get(name)
    return sec if isinstance(sec, dict) else {}


def _env_bool(name: str) -> Optional[bool]:
    raw = os.environ.get(name)
    if raw is None:
        return None
    low = raw.strip().lower()
    if low in _TRUE_WORDS:
        return True
    if low in _FALSE_WORDS:
        return False
    return None


def _env_int(name: str) -> Optional[int]:
    raw = os.environ.get(name)
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


def mask_secret(value: Any) -> str:
    """把密钥压成可展示的掩码：空→空串，短密钥→``***``，长密钥→首3+尾4。"""
    if not isinstance(value, str) or not value:
        return ""
    if len(value) <= 6:
        return "***"
    return value[:3] + "***" + value[-4:]


# ---------------------------------------------------------------- 读取
def load_settings(path: Optional[str] = None) -> Dict[str, Any]:
    """读取 settings.json 并与默认结构深度合并。

    文件不存在、不是 JSON、顶层不是对象、编码损坏——一律回退默认值，
    **绝不抛异常**。``SETTINGS_PATH`` 在函数体内读取，便于测试切换。
    """
    p = path or SETTINGS_PATH
    try:
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    # 只保留已知的配置段，防止历史遗留字段被当成配置读进来。
    known = {k: raw[k] for k in DEFAULT_SETTINGS if isinstance(raw.get(k), dict)}
    return _deep_merge(DEFAULT_SETTINGS, known)


# ---------------------------------------------------------------- 校验
def validate(settings: Dict[str, Any]) -> List[str]:
    """检查边界，返回中文 warning 列表；**只报告不抛**。

    空 base_url 视为「未配置」，不算错误——否则刚装好的工具箱会满屏警告。
    """
    warnings: List[str] = []
    if not isinstance(settings, dict):
        return ["配置根节点必须是对象"]

    for name in DEFAULT_SETTINGS:
        sec = settings.get(name)
        if sec is None:
            continue
        if not isinstance(sec, dict):
            warnings.append(f"{name} 配置必须是对象")
            continue

        # notify 与 llm/ima 的字段形态不同，单独校验后跳过通用规则。
        if name == "notify":
            if not isinstance(sec.get("enabled"), bool):
                warnings.append("notify.enabled 必须是布尔值")
            for field in ("webhook", "cli"):
                value = sec.get(field, "")
                if not isinstance(value, str):
                    warnings.append(f"notify.{field} 必须是字符串")
            hook = sec.get("webhook")
            if isinstance(hook, str) and hook \
                    and not (hook.startswith("http://") or hook.startswith("https://")):
                warnings.append("notify.webhook 必须以 http:// 或 https:// 开头")
            continue

        url = sec.get("base_url", "")
        if not isinstance(url, str):
            warnings.append(f"{name}.base_url 必须是字符串")
        elif url and not (url.startswith("http://") or url.startswith("https://")):
            warnings.append(f"{name}.base_url 必须以 http:// 或 https:// 开头")

        to = sec.get("timeout")
        if isinstance(to, bool) or not isinstance(to, int) \
                or not (_MIN_TIMEOUT <= to <= _MAX_TIMEOUT):
            warnings.append(f"{name}.timeout 必须是 {_MIN_TIMEOUT}–{_MAX_TIMEOUT} 的整数")

        if name == "llm" and not isinstance(sec.get("model", ""), str):
            warnings.append("llm.model 必须是字符串")

    return warnings


# ---------------------------------------------------------------- 解析（叠加环境变量）
def _resolve(name: str, settings: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    base = load_settings() if settings is None else settings
    sec = _deep_merge(DEFAULT_SETTINGS[name], _section(base, name))

    for field, env_name in _ENV_KEYS[name].items():
        if field == "timeout":
            v = _env_int(env_name)
        elif field == "enabled":
            v = _env_bool(env_name)
        else:
            v = os.environ.get(env_name)
        if v is not None and v != "":
            sec[field] = v
    # 文件里手写的布尔/超时也可能来自 JSON 之外（例如测试注入），统一收敛类型。
    if not isinstance(sec.get("enabled"), bool):
        raw = sec.get("enabled")
        low = raw.strip().lower() if isinstance(raw, str) else ""
        sec["enabled"] = low in _TRUE_WORDS
    if isinstance(sec.get("timeout"), str):
        try:
            sec["timeout"] = int(sec["timeout"].strip())
        except ValueError:
            sec["timeout"] = DEFAULT_SETTINGS[name]["timeout"]

    # api_key：直接环境变量已在上一步覆盖；这里再按 api_key_env 补一次。
    if not sec.get("api_key"):
        env_name = sec.get("api_key_env")
        if isinstance(env_name, str) and env_name.strip():
            sec["api_key"] = os.environ.get(env_name.strip(), "")

    for field in ("base_url", "model", "api_key", "api_key_env", "knowledge_base"):
        if field in DEFAULT_SETTINGS[name] and not isinstance(sec.get(field), str):
            sec[field] = ""

    missing: List[str] = []
    for field in _REQUIRED[name]:
        value = sec.get(field)
        if field == "enabled":
            ok = value is True
        else:
            ok = isinstance(value, str) and bool(value.strip())
        if not ok:
            missing.append(field)

    sec["configured"] = not missing
    sec["missing"] = missing
    return sec


def resolve_llm(settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """叠加环境变量后返回 LLM 的生效配置（含 ``configured`` / ``missing``）。"""
    return _resolve("llm", settings)


def resolve_ima(settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """叠加环境变量后返回 IMA 的生效配置（含 ``configured`` / ``missing``）。"""
    return _resolve("ima", settings)


# ---------------------------------------------------------------- 对外视图
def public_view(settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """返回可展示的配置：密钥只出掩码，另附连通性判定与 warnings。

    这是设置页 / ``settings_get`` 唯一允许使用的出口；明文密钥永远不会
    出现在返回值里。
    """
    base = load_settings() if settings is None else settings
    view = _deep_merge(DEFAULT_SETTINGS, {
        k: v for k, v in (base.items() if isinstance(base, dict) else ())
        if k in DEFAULT_SETTINGS and isinstance(v, dict)
    })
    view.pop("warnings", None)

    llm = resolve_llm(base)
    ima = resolve_ima(base)
    view["llm"]["api_key"] = mask_secret(view["llm"].get("api_key"))
    view["ima"]["api_key"] = mask_secret(view["ima"].get("api_key"))
    view["llm"]["configured"] = llm["configured"]
    view["llm"]["has_api_key"] = bool(llm.get("api_key"))
    view["ima"]["configured"] = ima["configured"]
    view["ima"]["has_api_key"] = bool(ima.get("api_key"))
    view["warnings"] = validate(view)
    return view


# ---------------------------------------------------------------- 写入
def save_settings(patch: Dict[str, Any], path: Optional[str] = None) -> Dict[str, Any]:
    """深度合并 patch 后落盘，返回脱敏视图。

    带 ``***`` 的 ``api_key`` 视为「未修改」（界面回传的是掩码），
    原值保持不变；校验不通过时抛 ``LongformError`` 且不写文件。
    """
    if not isinstance(patch, dict):
        raise LongformError(
            f"参数 `patch` 必须是对象，实际收到 {type(patch).__name__}：{patch!r}")

    p = path or SETTINGS_PATH
    current = load_settings(p)
    clean = copy.deepcopy(patch)
    clean.pop("warnings", None)

    for name in DEFAULT_SETTINGS:
        sec = clean.get(name)
        if not isinstance(sec, dict):
            continue
        key = sec.get("api_key")
        if isinstance(key, str) and "***" in key:
            sec["api_key"] = current[name].get("api_key", "")

    merged = _deep_merge(current, clean)
    merged.pop("warnings", None)

    problems = validate(merged)
    if problems:
        raise LongformError("配置校验失败：" + "；".join(problems))

    try:
        parent = os.path.dirname(os.path.abspath(p))
        os.makedirs(parent, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
            f.write("\n")
    except OSError as e:
        raise LongformError(f"写入配置文件失败：{p}（{e}）") from None

    return public_view(merged)
