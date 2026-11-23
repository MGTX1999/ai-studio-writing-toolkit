"""阶段建议与大纲流水线：回答「现在该做什么、用什么模型、找谁」。

读操作一律宽容：配置或变体坏掉时降级提示，绝不抛错。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import profile as profiles
from exporting import project_config
from store import MAX_CHAPTER_N, LongformError, Store, _require_enum, _require_int


def _load_models_for(project: Optional[str] = None, profile_name: Optional[str] = None,
                     s: Optional[Store] = None) -> Tuple[Dict[str, Any], List[str]]:
    """按优先级组装模型配置。

    沿用项目不变量：变体 overrides > 项目 config > config/models.json > 内置。
    模型名是平台相关标识，档位（planning/production/qa/utility）才是跨平台语义锚点。
    """
    warnings: List[str] = []
    layers: List[dict] = []
    if project:
        store = s
        if store is None:
            import server      # 延迟导入，避免 server ↔ pipeline 循环依赖
            store = server.get_store()
        pcfg = project_config(store, project)
        if isinstance(pcfg.get("models"), dict):
            layers.append(pcfg["models"])
    if profile_name:
        try:
            ov = profiles.read(profile_name).get("overrides") or {}
        except LongformError as e:
            ov = {}
            warnings.append(f"变体 {profile_name} 不可读（{e}）；已跳过它的模型覆盖")
        if isinstance(ov.get("models"), dict):
            layers.append(ov["models"])
    cfg, warns = profiles.load_models(layers=layers or None)
    warnings.extend(warns)
    return cfg, warnings


def _stage_entry(cfg: Dict[str, Any], stage: str,
                 warnings: List[str]) -> Dict[str, Any]:
    """把「档位 + 阶段剧本」摊平成一条建议。"""
    st = (cfg.get("stages") or {}).get(stage)
    if not isinstance(st, dict):
        st = profiles.DEFAULT_STAGES.get(stage, {})
    tier = st.get("tier") or profiles.BUILTIN_STAGE_TIER.get(stage, "planning")
    raw_model = (cfg.get("tiers") or {}).get(tier)
    model = dict(raw_model if isinstance(raw_model, dict)
                 else profiles.DEFAULT_MODELS[tier])
    model["tier"] = tier
    return {
        "stage": stage,
        "stage_label": st.get("label") or profiles.BUILTIN_STAGE_LABEL.get(stage, stage),
        "model": model,
        "reason": st.get("reason") or "",
        "expert": st.get("expert"),
        "skill": st.get("skill"),
        "tools": list(st.get("tools") or []),
        "output_contract": st.get("output_contract") or {},
        "source": cfg.get("source", "builtin-fallback"),
        "warnings": list(warnings),
    }


def stage_advice(stage: Optional[str] = None, project: Optional[str] = None,
                 profile_name: Optional[str] = None,
                 s: Optional[Store] = None) -> Dict[str, Any]:
    """阶段化 LLM 建议。读操作宽容：配置坏掉时降级提示，绝不抛错。"""
    cfg, warnings = _load_models_for(project, profile_name, s)
    if stage is None:
        return {"source": cfg.get("source", "builtin-fallback"),
                "tiers": cfg.get("tiers"),
                "stages": {k: _stage_entry(cfg, k, warnings) for k in profiles.STAGES},
                "warnings": warnings}
    stage = _require_enum(stage, "stage", profiles.STAGES)
    return _stage_entry(cfg, stage, warnings)


def outline_next(s: Store, pid: str,
                 profile_name: Optional[str] = None) -> Dict[str, Any]:
    """判定下一阶段：总纲 → 卷纲 → 章纲 → 正文 → 审校 → 归档。

    这是"下一步该做什么"的唯一权威口径——技能与主控都听它的，
    避免同一份数据在不同层得出两个结论。
    """
    st = s.outline_stats(pid)
    written = {r["n"]: r for r in s.chapter_list(pid)}
    outlined = {c["chapter_n"]: c for c in s.outline_list(pid, level="chapter")
                if c["chapter_n"] is not None}
    total = st["total_chapters"] or 0
    gaps = st["gaps"] or []

    stage, reason = "chapter", ""
    missing: List[str] = []
    next_chapter = 1

    if st["master"] == 0:
        stage = "master"
        reason = "还没有总纲：先定骨架、总章数与结局（结局未定不许展开章纲）"
        missing.append("总纲要（outline_set level='master'）")
    elif st["has_volumes"] and (st["volumes"] == 0 or gaps):
        stage = "volume"
        if st["volumes"] == 0:
            reason = f"总纲声明分卷（总章数 {total or '未定'}），但还没有任何卷纲"
            missing.append(f"卷纲要：覆盖第 1-{total or '?'} 章")
        else:
            reason = f"卷区间还有空洞：{gaps}"
            missing.append(f"补齐缺口区间：{gaps}")
        next_chapter = gaps[0][0] if gaps else 1
    else:
        holes = [n for n in range(1, total + 1) if n not in outlined] if total else []
        if holes:
            stage, next_chapter = "chapter", holes[0]
            reason = f"第 {holes[0]} 章还没有章纲（还缺 {len(holes)} 章）"
            missing.append(f"第 {holes[0]} 章章纲")
        elif not outlined:
            next_chapter = (max(written) + 1) if written else 1
            stage = "chapter"
            reason = ("已写的章节都没留下章纲（旧项目），继续为下一章补章纲"
                      if written else "总章数未定且还没有章纲，先写第 1 章章纲")
            missing.append(f"第 {next_chapter} 章章纲")
        else:
            pending = sorted(n for n in outlined if n not in written)
            if pending:
                stage, next_chapter = "draft", pending[0]
                reason = f"第 {pending[0]} 章有章纲但还没有正文（还有 {len(pending)} 章待写）"
                missing.append(f"第 {pending[0]} 章正文")
            else:
                # 从前往后找还没定稿的章——只看最新一章会漏掉中间夹着的草稿。
                not_ok = sorted(n for n in outlined
                                if (written.get(n) or {}).get("status") != "approved")
                cur = max(n for n in outlined if n in written)
                if not_ok:
                    stage, next_chapter = "qa", not_ok[0]
                    reason = (f"第 {not_ok[0]} 章正文状态为 "
                              f"{(written.get(not_ok[0]) or {}).get('status')}，先过一致性审校")
                    missing.append(f"第 {not_ok[0]} 章审校报告（audit_log）")
                elif total and cur >= total:
                    stage, next_chapter = "utility", cur
                    reason = f"全书 {total} 章已全部定稿，可导出归档"
                    missing.append("导出/归档（chapter_export / project_export）")
                else:
                    stage, next_chapter = "chapter", cur + 1
                    reason = f"第 {cur} 章已定稿，继续第 {cur + 1} 章的章纲"
                    missing.append(f"第 {cur + 1} 章章纲")

    pname = profile_name if profile_name is not None else s.project_get_profile(pid)
    cfg, warnings = _load_models_for(pid, pname, s)
    entry = _stage_entry(cfg, stage, warnings)
    return {
        "project": pid, "stage": stage, "stage_label": entry["stage_label"],
        "reason": reason, "missing": missing, "gaps": gaps,
        "next_chapter": next_chapter, "outline": st,
        "suggested_tools": entry["tools"],
        "expert": entry["expert"], "skill": entry["skill"],
        "model": entry["model"], "source": entry["source"],
        "warnings": entry["warnings"],
    }


def outline_brief(s: Store, pid: str, n: Any) -> Dict[str, Any]:
    """取第 N 章章纲，直接喂 packet_assemble(brief=)。

    新表读不到时回退读旧章节的 ``data.brief``，但**只读不写回**——
    把旧数据自动搬进新表等于替使用人做迁移决策，应当是一次显式的动作。
    """
    n = _require_int(n, "chapter_n", minimum=0, maximum=MAX_CHAPTER_N)
    node = s.outline_by_chapter(pid, n)
    if node is not None:
        body = node.get("body") or ""
        parent = node.get("parent_id")
        return {"ok": True, "chapter_n": n, "id": node["id"],
                "version": node["version"], "status": node["status"],
                "title": node.get("title"),
                "volume": parent if parent and parent != "master" else None,
                "brief": body, "chars": len(body),
                "data": node.get("data_json") or {}, "source": "outline"}

    ch = s.chapter_get(pid, n)
    if ch:
        data = ch.get("data_json") if isinstance(ch.get("data_json"), dict) else {}
        legacy = str(data.get("brief") or "")
        if legacy.strip():
            return {"ok": True, "chapter_n": n, "id": None, "version": None,
                    "status": ch.get("status"), "title": ch.get("title"),
                    "volume": None, "brief": legacy, "chars": len(legacy),
                    "data": {}, "source": "legacy-chapter-data",
                    "note": "这条细纲来自旧版章节的 data.brief（只读回退，未迁移到大纲表）；"
                            "若要纳入版本管理，请对它调用 outline_set(level='chapter')"}
    raise LongformError(
        f"第 {n} 章没有章纲：大纲表里没有对应节点，章节 data 里也没有 brief。"
        f"请先 outline_set(level='chapter', chapter_n={n}, body=...)")
