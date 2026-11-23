"""导出配置与降级导出（文件系统落地都在 profile.py）。

``store.py`` 不认识"变体"，``profile.py`` 不碰数据库；这里只做两者之间的
参数归一化：把「工具参数 / 变体 overrides / 项目 config / defaults.yaml」
按优先级压成一份可直接喂给 ``profiles.export_chapter`` 的配置。
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import config_loader
import profile as profiles
from store import MAX_KEY_LEN, Store, _load_json, _require_str


def project_config(s: Store, pid: str) -> Dict[str, Any]:
    """读取项目级配置字典；脏数据一律回落到空字典。"""
    proj = s.project_get(pid)
    if not proj:
        return {}
    cfg = _load_json(proj.get("config_json"), {})
    return cfg if isinstance(cfg, dict) else {}


def bound_profile(s: Store, pid: str, override: Optional[str] = None) -> Optional[str]:
    """确定本次调用要用的变体。

    ``override`` 是工具参数里的 ``profile``——它优先于项目绑定，
    方便"临时用另一个变体的设置导出一次"而不必改动绑定关系。
    """
    if override is not None:
        return _require_str(override, "profile", allow_empty=True,
                            max_len=MAX_KEY_LEN).strip() or None
    return s.project_get_profile(pid)


def export_cfg(s: Store, pid: str, profile_name: Optional[str] = None,
               override_dir: Optional[str] = None) -> Dict[str, Any]:
    """构造导出配置，返回可直接喂给 ``profiles.export_chapter`` 的字典。

    优先级（高 → 低）：

    1. 工具参数 ``export_dir`` / ``profile``
    2. 变体 ``profile.json`` 的 ``export`` 段
    3. 变体 ``overrides.output`` / 项目 ``output`` / ``config/defaults.yaml``
       （``export_format``、``save_every_version``）
    4. ``profiles.DEFAULT_EXPORT``

    目录基准：变体里的 ``export.dir`` 相对**变体文件夹**解析；
    工具参数给的 ``dir`` 相对**当前工作目录**解析，绝对路径则原样使用。
    """
    exp = dict(profiles.DEFAULT_EXPORT)
    base = os.getcwd()
    proj_cfg = project_config(s, pid)
    pcfg = None

    if profile_name:
        # 变体不存在就在这里炸掉，绝不静默落到"看起来对"的默认目录。
        pcfg = profiles.read(profile_name)
        base = pcfg["dir"]

    # 优先级：变体 overrides > 项目 config > config/defaults.yaml > 代码默认值
    merged = profiles.deep_merge(config_loader.load_defaults(), proj_cfg)
    if pcfg is not None:
        merged = profiles.deep_merge(merged, pcfg.get("overrides") or {})

    out_cfg = merged.get("output") if isinstance(merged.get("output"), dict) else {}
    fmt = out_cfg.get("export_format")
    if isinstance(fmt, str) and fmt.strip().lower() in ("md", "txt", "markdown"):
        fmt = fmt.strip().lower()
        exp["format"] = "md" if fmt == "markdown" else fmt
    if isinstance(out_cfg.get("save_every_version"), bool):
        exp["versioned"] = bool(out_cfg["save_every_version"])

    if pcfg:
        exp.update({k: v for k, v in (pcfg.get("export") or {}).items() if v is not None})

    if override_dir:
        exp["dir"] = _require_str(override_dir, "export_dir",
                                  max_len=profiles.MAX_EXPORT_DIR_LEN).strip()
        base = os.getcwd()
    return {"dir": base, "export": exp}


def compliance_gate(s: Store, pid: str, n: int) -> Optional[dict]:
    """返回该章最近一次合规预检报告；没有报告时返回 None。

    只拦“红色且明确要求阻断”的报告；黄/灰报告不阻断导出。
    """
    try:
        audits = s.audit_list(pid, n)
    except Exception:  # noqa: BLE001 审计读失败不能反过来阻断正常导出
        return None
    for item in reversed(audits):
        if item.get("kind") == "compliance":
            return item
    return None


def try_export(s: Store, pid: str, n: int, version: int,
               profile_name: Optional[str], override_dir: Optional[str]) -> dict:
    """导出一章，失败降级为 ``{ok: False, error: ...}`` 而非抛异常。

    SQLite 是唯一权威，``exports/`` 只是派生视图。目录被设为只读、
    路径指向了不存在的盘符，都不该让"保存章节"这件事本身失败——
    否则模型会因为一次落盘错误而认为正文丢了，进而重复生成。

    但合规硬红是例外：最近一次 ``compliance_check(save=true)`` 为红色时，
    导出被阻断，直到作者改写并重新预检。
    """
    gate = compliance_gate(s, pid, n)
    if gate and gate.get("export_blocked"):
        return {
            "ok": False,
            "error": (f"第 {n} 章最近一次合规预检为红色（audit {gate.get('id', '-')}），"
                      "已阻断导出。请先改写命中项，再重新预检并保存报告。"),
            "compliance": {"verdict": gate.get("verdict"),
                           "counts": gate.get("counts")},
        }
    try:
        cfg = export_cfg(s, pid, profile_name, override_dir)
        ch = s.chapter_get(pid, n, version)
        if ch is None:
            return {"ok": False, "error": f"第 {n} 章（v{version}）不存在"}
        return profiles.export_chapter(ch, cfg)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
