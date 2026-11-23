"""工具派发：表驱动，一个工具一个处理函数。

``dispatch`` 只做两件事：查表、调用。参数校验与默认值全部留在各处理函数里，
这样"某个工具的默认值是什么、错误文案是什么"只需要看一个函数。

顺序敏感的两处必须保持：
1. 变体类工具与 ``stage_advice`` 在 project 校验**之前**处理；
2. 其余工具一律先校验 project，再校验其它参数。
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict

import citations
import profile as profiles
import ima_client
import ima_export
import insights
import inspiration
import kb
import lint
import llm_client
import narrative
import prose_patch
import quality_loop
import revision_advisor
import runner
import settings
import studio_autopilot
import studio_bridge
import style_distill
import token_saver
import word_stats
from exporting import bound_profile, compliance_gate, export_cfg, try_export
from packet import _PACKET_LABELS, context_estimate, packet_text, packet_trace
from pipeline import outline_brief, outline_next, stage_advice
from store import (
    MAX_CHAPTER_N,
    MAX_KEY_LEN,
    MAX_NAME_LEN,
    MAX_TEXT_LEN,
    LongformError,
    Store,
    _require_enum,
    _require_int,
    _require_str,
)
from tool_specs import TOOL_MAP


def _get(args: Dict[str, Any], key: str) -> Any:
    """取必填参数；缺失时给出可读提示，而不是抛 KeyError。"""
    if key not in args:
        raise LongformError(f"缺少必填参数 `{key}`")
    return args[key]


def _require_project_arg(args: Dict[str, Any]) -> str:
    return _require_str(_get(args, "project"), "project")


# ============================================================
# 与项目无关的工具（先于 project 校验）
# ============================================================
def h_profile_list(args: dict, s: Store):
    bound = None
    if args.get("project"):
        bound = s.project_get_profile(_require_project_arg(args))
    items = profiles.list_profiles(bound)
    return {"profiles_dir": profiles.root(), "count": len(items),
            "bound": bound, "profiles": items}


def h_profile_get(args: dict, s: Store):
    cfg = profiles.read(_get(args, "name"))
    return {"name": cfg["name"], "description": cfg["description"],
            "base": cfg["base"], "overrides": cfg["overrides"],
            "export": cfg["export"], "dir": cfg["dir"],
            "export_dir": profiles.resolve_export_dir(cfg)}


def h_profile_create(args: dict, s: Store):
    return profiles.create(_get(args, "name"), args.get("description", ""),
                           args.get("base", profiles.TEMPLATE_NAME),
                           args.get("export_dir"))


def h_profile_delete(args: dict, s: Store):
    return profiles.delete(_get(args, "name"), args.get("confirm"),
                           dry_run=bool(args.get("dry_run", False)))


def h_profile_set_export_dir(args: dict, s: Store):
    return profiles.set_export_dir(_get(args, "name"), _get(args, "dir"))


def h_stage_advice(args: dict, s: Store):
    # project 可选：只想查"这个阶段该用什么模型"时不必先建项目。
    proj = args.get("project")
    if proj is not None:
        proj = _require_str(proj, "project", allow_empty=True).strip() or None
    pname = args.get("profile")
    if pname is not None:
        pname = _require_str(pname, "profile", allow_empty=True,
                             max_len=MAX_KEY_LEN).strip() or None
    if proj and pname is None:
        pname = s.project_get_profile(proj)      # 未显式指定就用项目绑定的变体
    return stage_advice(args.get("stage"), proj, pname, s)


def h_project_init(args: dict, s: Store):
    pname = args.get("profile")
    if pname is not None:
        pname = _require_str(pname, "profile", allow_empty=True,
                             max_len=MAX_KEY_LEN).strip()
        if pname:
            # 先确认变体真的存在，别把一个拼错的名字写进项目配置，
            # 否则之后每次 packet_assemble 都会静默降级。
            profiles.read(pname)
    return s.project_init(_get(args, "name"), args.get("kind", "novel"),
                          args.get("config"), bool(args.get("overwrite", False)),
                          pname)


def h_project_list(args: dict, s: Store):
    return s.project_list()


def h_project_export(args: dict, s: Store):
    return s.project_export(_require_project_arg(args))


def h_project_delete(args: dict, s: Store):
    return s.project_delete(_require_project_arg(args), args.get("confirm"))


# ============================================================
# 设置 / 模型 / IMA 搜索（与项目无关）
# ============================================================
def h_settings_get(args: dict, s: Store):
    # public_view 是唯一出口：密钥在这里被压成掩码。
    return settings.public_view()


def h_settings_set(args: dict, s: Store):
    patch = {k: args[k] for k in ("llm", "ima") if k in args}
    if not patch:
        raise LongformError("settings_set 至少需要 `llm` 或 `ima` 之一")
    return settings.save_settings(patch)


def _studio_mode(s: Store, pid: str) -> bool:
    """项目是否处于 AI Studio 模式；读配置失败按非 studio 处理。"""
    try:
        return s.config_get(pid, "studio.mode") == "ai-studio"
    except LongformError:
        return False


def h_llm_generate(args: dict, s: Store):
    # 参数全部透传；未配置与上游错误都由 llm_client 转成可读的 LongformError。
    prompt = _get(args, "prompt")
    project = args.get("project")
    if project is not None:
        project = _require_str(project, "project", allow_empty=True).strip() or None
    if project:
        s._require_project(project)
        if _studio_mode(s, project):
            system = args.get("system")
            text = (f"{system}\n\n{prompt}" if isinstance(system, str) and system.strip()
                    else prompt)
            handoff = s.handoff_create(project, "llm_generate", text,
                                       schema={"type": "text"})
            return {"ok": True, "status": "awaiting_input",
                    "handoff_id": handoff["id"], "prompt": text}
    return llm_client.generate(
        prompt,
        system=args.get("system"),
        temperature=args.get("temperature"),
        max_tokens=args.get("max_tokens"),
        timeout=args.get("timeout"))


def h_ima_search(args: dict, s: Store):
    return ima_client.search(_get(args, "query"), args.get("limit", 10),
                             kb_id=args.get("kb_id"))


# ============================================================
# 需要 project 的工具
# ============================================================
def h_profile_bind(args: dict, s: Store):
    p = _require_project_arg(args)
    bname = _get(args, "name")
    _require_str(bname, "name", allow_empty=True, max_len=MAX_KEY_LEN)
    if bname.strip():
        profiles.read(bname.strip())     # 不允许绑定不存在的变体
    return s.project_set_profile(p, bname)


def h_bible_upsert(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.bible_upsert(p, _get(args, "kind"), _get(args, "name"),
                          args.get("data", {}))


def h_bible_get(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.bible_get(p, _get(args, "kind"), _get(args, "name"))


def h_bible_list(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.bible_list(p, args.get("kind"))


def h_bible_search(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.bible_search(p, _get(args, "query"), args.get("limit"))


def h_entity_meta_get(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.entity_meta_get(p, _get(args, "kind"), _get(args, "name"))


def h_entity_meta_set(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.entity_meta_set(p, _get(args, "kind"), _get(args, "name"),
                             anchors=args.get("anchors"),
                             priority=args.get("priority"),
                             first_seen_chapter=args.get("first_seen_chapter"))


def h_timeline_add(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.timeline_add(p, _get(args, "title"), _get(args, "chapter"),
                          args.get("seq", 0), args.get("data"))


def h_timeline_query(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.timeline_query(p, args.get("from_chapter", 0),
                            args.get("to_chapter", MAX_CHAPTER_N), args.get("limit"))


def h_foreshadow_add(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.foreshadow_add(p, _get(args, "text"), _get(args, "planted_chapter"),
                            args.get("due_chapter"))


def h_foreshadow_list(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.foreshadow_list(p, args.get("status"))


def h_foreshadow_resolve(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.foreshadow_resolve(p, _get(args, "id"), _get(args, "chapter"),
                                args.get("status", "resolved"))


def h_foreshadow_overdue(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.foreshadow_overdue(p, _get(args, "current"), args.get("after", 8))


def h_chapter_save(args: dict, s: Store):
    p = _require_project_arg(args)
    n = _get(args, "n")
    out = s.chapter_save(p, n, args.get("title", f"第{n}章"), _get(args, "content"),
                         args.get("status", "draft"), args.get("data"))
    # 自动落盘：项目绑定了变体（或本次显式给了 export_dir）就把正文同步
    # 写到本地文件夹。export 显式为 false 时跳过，方便"先存草稿、定稿再导"。
    override_dir = args.get("export_dir")
    pname = bound_profile(s, p, args.get("profile"))
    want = args.get("export")
    if want is None:
        want = bool(pname or override_dir)
    if want:
        out["export"] = try_export(s, p, out["n"], out["version"],
                                   pname, override_dir)
    return out


def h_chapter_get(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.chapter_get(p, _get(args, "n"), args.get("version"))


def h_chapter_patch(args: dict, s: Store):
    p = _require_project_arg(args)
    out = prose_patch.chapter_patch(
        s, p, _get(args, "n"), _get(args, "find"), _get(args, "replace"),
        occurrence=args.get("occurrence", 1),
        all_matches=args.get("all_matches", False),
        preview=args.get("preview", False),
        expected_version=args.get("expected_version"),
        status=args.get("status"),
    )
    if out.get("preview"):
        return out
    # 与 chapter_save 一致：项目绑定变体时，落库后同步导出，避免库里是最新、
    # 本地文件夹还停在旧版本。
    override_dir = args.get("export_dir")
    pname = bound_profile(s, p, args.get("profile"))
    want = args.get("export")
    if want is None:
        want = bool(pname or override_dir)
    if want:
        out["export"] = try_export(s, p, out["n"], out["to_version"],
                                   pname, override_dir)
    return out


def h_chapter_list(args: dict, s: Store):
    return s.chapter_list(_require_project_arg(args))


def h_chapter_versions(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.chapter_versions(p, _get(args, "n"))


def h_chapter_diff(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.chapter_diff(p, _get(args, "n"), args.get("a"), args.get("b"))


def h_chapter_export(args: dict, s: Store):
    p = _require_project_arg(args)
    # 手动补导：数据库是权威，这个目录随时可以删掉重建。
    cfg = export_cfg(s, p, bound_profile(s, p, args.get("profile")), args.get("dir"))
    out_dir = profiles.resolve_export_dir(cfg)
    if args.get("n") is None:
        for ch in s.chapter_list(p):
            gate = compliance_gate(s, p, ch["n"])
            if gate and gate.get("export_blocked"):
                return {"ok": False,
                        "error": (f"第 {ch['n']} 章最近一次合规预检为红色"
                                  f"（audit {gate.get('id', '-')}），已阻断整项目导出。")}
        return profiles.export_project(s, p, cfg, directory=out_dir)
    n = _require_int(args.get("n"), "n", minimum=0, maximum=MAX_CHAPTER_N)
    gate = compliance_gate(s, p, n)
    if gate and gate.get("export_blocked"):
        return {"ok": False,
                "error": (f"第 {n} 章最近一次合规预检为红色"
                          f"（audit {gate.get('id', '-')}），已阻断导出。"
                          "请先改写命中项，再重新预检并保存报告。"),
                "compliance": {"verdict": gate.get("verdict"),
                               "counts": gate.get("counts")}}
    ch = s.chapter_get(p, n, args.get("version"))
    if ch is None:
        ver = args.get("version")
        raise LongformError(f"第 {n} 章不存在" +
                            (f"（version={ver}）" if ver is not None else ""))
    return profiles.export_chapter(ch, cfg, directory=out_dir)


def h_style_set(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.style_set(p, _get(args, "key"), args.get("value"))


def h_style_get(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.style_get(p, args.get("key"))


def h_style_distill(args: dict, s: Store):
    p = _require_project_arg(args)
    return style_distill.distill(
        s, p, texts=args.get("texts"), chapter_ns=args.get("chapter_ns"),
        name=args.get("name") or "默认画像")


def h_style_profile_get(args: dict, s: Store):
    p = _require_project_arg(args)
    return style_distill.get(s, p, args.get("profile_id"))


def h_style_profile_apply(args: dict, s: Store):
    p = _require_project_arg(args)
    return style_distill.apply(s, p, _get(args, "profile_id"))


def h_style_blind_test(args: dict, s: Store):
    p = _require_project_arg(args)
    return style_distill.blind_test(
        s, p,
        reference_texts=args.get("reference_texts"),
        candidate_texts=args.get("candidate_texts"),
        reference_chapter_ns=args.get("reference_chapter_ns"),
        candidate_chapter_ns=args.get("candidate_chapter_ns"),
        profile_id=args.get("profile_id"),
        threshold=args.get("threshold", 75))


def h_fact_add(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.fact_add(p, _get(args, "claim"), args.get("source"), args.get("chapter"))


def h_fact_set_status(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.fact_set_status(p, _get(args, "id"), _get(args, "status"),
                             args.get("note"))


def h_fact_list(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.fact_list(p, args.get("status"))


def h_audit_log(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.audit_log(p, _get(args, "chapter_n"), args.get("report", {}))


def h_audit_list(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.audit_list(p, args.get("chapter_n"))


def h_outline_set(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.outline_set(p, _get(args, "level"),
                         node_id=args.get("id"), parent_id=args.get("parent_id"),
                         title=args.get("title"), body=args.get("body", ""),
                         status=args.get("status") or "draft",
                         seq=args.get("seq") or 0, chapter_n=args.get("chapter_n"),
                         start_chapter=args.get("start_chapter"),
                         end_chapter=args.get("end_chapter"), data=args.get("data"))


def h_outline_get(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.outline_get(p, _get(args, "id"), args.get("version"))


def h_outline_list(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.outline_list(p, args.get("level"), args.get("parent_id"),
                          args.get("status"))


def h_outline_tree(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.outline_tree(p, bool(args.get("include_body", False)))


def h_outline_next(args: dict, s: Store):
    p = _require_project_arg(args)
    return outline_next(s, p, args.get("profile"))


def h_outline_brief(args: dict, s: Store):
    p = _require_project_arg(args)
    return outline_brief(s, p, _get(args, "chapter_n"))


def h_outline_versions(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.outline_versions(p, _get(args, "id"))


def h_outline_delete(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.outline_delete(p, _get(args, "id"), args.get("confirm"))


def h_outline_diff(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.outline_diff(p, _get(args, "id"), args.get("a"), args.get("b"))


def h_material_add(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.material_add(p, _get(args, "title"), args.get("content", ""),
                          args.get("kind", "reference"), args.get("source", ""),
                          args.get("source_kind", "other"), args.get("tags"),
                          args.get("chapter"), args.get("data"))


def h_material_get(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.material_get(p, _get(args, "id"))


def h_material_update(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.material_update(
        p, _get(args, "id"), title=args.get("title"),
        content=args.get("content"), tags=args.get("tags"),
        data=args.get("data"))


def h_material_audit(args: dict, s: Store):
    p = _require_project_arg(args)
    return insights.material_audit(s, p, args.get("level"))


def h_material_list(args: dict, s: Store):
    p = _require_project_arg(args)
    query = args.get("query")
    if query:
        # 检索只保留 kb.search 一个入口；material_list 只负责浏览/过滤。
        return kb.search(s, p, query, kind=args.get("kind"),
                         tag=args.get("tag"), chapter=args.get("chapter"),
                         limit=args.get("limit"),
                         category=args.get("category"),
                         expand_synonyms=args.get("expand_synonyms", True))
    return s.material_list(p, args.get("kind"), args.get("tag"),
                           args.get("chapter"), None, args.get("limit"))


def h_material_delete(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.material_delete(p, _get(args, "id"), args.get("confirm"))


def h_section_save(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.section_save(p, _get(args, "n"), _get(args, "idx"),
                          _get(args, "content"), args.get("title", ""),
                          args.get("status", "draft"), args.get("data"))


def h_section_get(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.section_get(p, _get(args, "n"), _get(args, "idx"),
                         args.get("version"))


def h_section_list(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.section_list(p, _get(args, "n"))


def h_section_merge(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.section_merge(p, _get(args, "n"), args.get("title"),
                           args.get("separator", "\n\n"),
                           args.get("status", "draft"))


def h_section_delete(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.section_delete(p, _get(args, "n"), _get(args, "idx"),
                            args.get("confirm"))


def h_context_estimate(args: dict, s: Store):
    p = _require_project_arg(args)
    return context_estimate(s, p, _get(args, "chapter_n"),
                            args.get("brief", ""), args.get("token_budget"),
                            args.get("include_materials"))


def h_context_trace(args: dict, s: Store):
    p = _require_project_arg(args)
    n = args.get("chapter_n")
    if n is None:
        rows = s.chapter_list(p)
        if not rows:
            raise LongformError(
                "项目还没有任何章节：请显式传 chapter_n，或先写第 1 章")
        n = max(int(r["n"]) for r in rows)
    trace = packet_trace(s, p, n, args.get("brief", "") or "")
    return {"project": p, "chapter_n": n, **trace}


def h_workspace_export(args: dict, s: Store):
    p = _require_project_arg(args)
    # 文件系统逻辑在 profile 里，这里只做参数校验——与 chapter_export 同构。
    return profiles.workspace_export(s, p, _get(args, "dir"), args.get("include"))


def h_packet_assemble(args: dict, s: Store):
    p = _require_project_arg(args)
    n = _get(args, "chapter_n")
    text, included = packet_text(s, p, n, args.get("brief", ""),
                                 args.get("token_budget"),
                                 args.get("include_materials"))
    return {"chapter_n": n, "approx_tokens": len(text) // 4,
            "packet": text, "sections": [_PACKET_LABELS[k] for k in included]}


def h_ledger_stats(args: dict, s: Store):
    return s.ledger_stats(_require_project_arg(args))


def h_config_set(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.config_set(p, _get(args, "key"), args.get("value"))


def h_config_get(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.config_get(p, args.get("key"))


def h_kb_import(args: dict, s: Store):
    p = _require_project_arg(args)
    kind = args.get("kind", "reference")
    source_kind = args.get("source_kind")
    tags = args.get("tags")
    chapter = args.get("chapter")
    # 优先级 dir > path > content：目录是一次导入多文件，单文件次之，
    # 裸文本最后；三者都没有才报错。
    if args.get("dir"):
        return kb.import_dir(s, p, args["dir"], kind=kind,
                             source_kind=source_kind, tags=tags, chapter=chapter)
    if args.get("path"):
        return kb.import_file(s, p, args["path"], title=args.get("title"),
                              kind=kind, source=args.get("source"),
                              source_kind=source_kind, tags=tags, chapter=chapter)
    if args.get("content"):
        return kb.import_text(s, p, args.get("title") or "未命名素材",
                              args["content"], kind=kind,
                              source=args.get("source", ""),
                              source_kind=source_kind, tags=tags, chapter=chapter)
    raise LongformError("kb_import 需要 content / path / dir 之一")


def h_kb_search(args: dict, s: Store):
    p = _require_project_arg(args)
    return kb.search(s, p, _get(args, "query"), kind=args.get("kind"),
                     tag=args.get("tag"), chapter=args.get("chapter"),
                     limit=args.get("limit"),
                     category=args.get("category"),
                     expand_synonyms=args.get("expand_synonyms", True))


def h_kb_categories(_args: dict, _s: Store):
    return kb.categories()


# ---------------------------------------------------------------- 引证
def h_citation_add(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.citation_add(p, args.get("chapter_n"), _get(args, "claim"),
                          source=args.get("source", ""),
                          locator=args.get("locator", ""))


def h_citation_verify(args: dict, s: Store):
    p = _require_project_arg(args)
    return citations.verify(s, p, _get(args, "citation_id"))


def h_citation_list(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.citation_list(p, chapter_n=args.get("chapter_n"),
                           verified=args.get("verified"),
                           limit=args.get("limit"))


def h_ima_fetch(args: dict, s: Store):
    item_id = _get(args, "id")
    if args.get("save"):
        return ima_client.import_to_materials(
            s, _require_project_arg(args), item_id,
            tags=args.get("tags"), chapter=args.get("chapter"),
            content=args.get("content"), title=args.get("title"))
    return ima_client.fetch(item_id)


def h_ima_import_urls(args: dict, s: Store):
    return ima_client.import_urls(_get(args, "urls"), kb_id=args.get("kb_id"))


def h_ima_upload(args: dict, s: Store):
    return ima_client.upload_file(_get(args, "path"), kb_id=args.get("kb_id"))


def h_summary_set(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.summary_set(p, _get(args, "chapter_n"), _get(args, "summary"))


def h_summary_list(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.summary_list(p, args.get("from_chapter", 0),
                          args.get("to_chapter", MAX_CHAPTER_N))


def h_story_state_set(args: dict, s: Store):
    p = _require_project_arg(args)
    # value 允许 null：store 里 null 表示删除该键。
    return s.story_state_set(p, _get(args, "key"), _get(args, "value"))


def h_story_state_get(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.story_state_get(p, _get(args, "key"))


def h_story_state_list(args: dict, s: Store):
    return s.story_state_list(_require_project_arg(args))


# ============================================================
# 叙事工作台：合规 / OOC / 角色会诊 / 妙招 / AI 声明
# ============================================================
def _audit_chapter(args: dict) -> int:
    chapter_n = args.get("chapter_n", 0)
    return _require_int(chapter_n, "chapter_n", minimum=0, maximum=MAX_CHAPTER_N)


def h_compliance_check(args: dict, s: Store):
    p = _require_project_arg(args)
    text = _require_str(_get(args, "text"), "text", max_len=MAX_TEXT_LEN)
    try:
        report = narrative.compliance_check(text)
    except ValueError as e:
        raise LongformError(str(e)) from None
    report["project"] = p
    if args.get("save"):
        chapter_n = _audit_chapter(args)
        report["chapter_n"] = chapter_n
        saved = s.audit_log(p, chapter_n, report)
        report["audit_id"] = saved["id"]
    return report


def h_voice_check(args: dict, s: Store):
    p = _require_project_arg(args)
    character = _require_str(_get(args, "character"), "character", max_len=MAX_NAME_LEN)
    text = _require_str(_get(args, "text"), "text", max_len=MAX_TEXT_LEN)
    data = s.bible_get(p, "character", character)
    if not isinstance(data, dict):
        raise LongformError(
            f"未找到人物卡「{character}」；先用 bible_upsert 写入 character，"
            "并在 data.voice 里填 self_ref / verbal_tics / banned_words / sentence_max")
    profile = dict(data)
    profile.setdefault("name", character)
    others = [item for item in s.bible_list(p, "character")
              if item.get("name") != character]
    try:
        report = narrative.voice_check(profile, text, others)
    except ValueError as e:
        raise LongformError(str(e)) from None
    report["project"] = p
    if args.get("save"):
        chapter_n = _audit_chapter(args)
        report["chapter_n"] = chapter_n
        saved = s.audit_log(p, chapter_n, report)
        report["audit_id"] = saved["id"]
    return report


def h_role_deck_list(args: dict, s: Store):
    return narrative.role_deck()


def h_trick_list(args: dict, s: Store):
    return narrative.trick_deck()


def h_role_prompt(args: dict, s: Store):
    try:
        return narrative.build_role_prompt(
            _get(args, "role"), _get(args, "text"), args.get("context", ""))
    except ValueError as e:
        raise LongformError(str(e)) from None


def h_trick_prompt(args: dict, s: Store):
    try:
        return narrative.build_trick_prompt(
            _get(args, "trick"), _get(args, "text"), args.get("context", ""))
    except ValueError as e:
        raise LongformError(str(e)) from None


def h_review_chain_prompt(args: dict, s: Store):
    try:
        return narrative.build_review_chain_prompt(
            _get(args, "text"), args.get("context", ""),
            args.get("role", "structure-editor"))
    except ValueError as e:
        raise LongformError(str(e)) from None


def h_ai_declaration(args: dict, s: Store):
    p = _require_project_arg(args)
    proj = s.project_get(p) or {}
    name = proj.get("name") or p
    return narrative.ai_declaration(name, p, s.ledger_stats(p))


# ============================================================
# 叙事工作台 v1.6：章节树 / 体检 / 串声 / 图谱 / 预期 / 流水线 / 申诉
# ============================================================
def h_chapter_tree(args: dict, s: Store):
    return insights.chapter_tree(s, _require_project_arg(args))


def _chapter_n_arg(args: dict, default: int = 0) -> int:
    return _require_int(args.get("chapter_n", default), "chapter_n",
                        minimum=0, maximum=MAX_CHAPTER_N)


def _text_or_chapter(args: dict, s: Store, p: str) -> str:
    if args.get("text") is not None:
        return _require_str(args["text"], "text", max_len=MAX_TEXT_LEN)
    n = _chapter_n_arg(args)
    chapter = s.chapter_get(p, n)
    if not chapter or not (chapter.get("content") or "").strip():
        raise LongformError(f"第 {n} 章还没有正文；请传 text 或先保存章节")
    return chapter["content"]


def h_structure_check(args: dict, s: Store):
    p = _require_project_arg(args)
    text = _text_or_chapter(args, s, p)
    try:
        report = insights.structure_check(text)
    except ValueError as e:
        raise LongformError(str(e)) from None
    report["project"] = p
    return report


def h_voice_cross_check(args: dict, s: Store):
    p = _require_project_arg(args)
    text = _text_or_chapter(args, s, p)
    report = insights.voice_cross_check(s, p, text)
    if args.get("save"):
        report["chapter_n"] = _chapter_n_arg(args)
        report["audit_id"] = s.audit_log(p, report["chapter_n"], report)["id"]
    return report


def h_ooc_check(args: dict, s: Store):
    p = _require_project_arg(args)
    character = _require_str(_get(args, "character"), "character",
                             max_len=MAX_NAME_LEN)
    text = _text_or_chapter(args, s, p)
    report = insights.ooc_check(s, p, character, text)
    if args.get("save"):
        report["chapter_n"] = _chapter_n_arg(args)
        report["audit_id"] = s.audit_log(p, report["chapter_n"], report)["id"]
    return report


def h_story_graph(args: dict, s: Store):
    p = _require_project_arg(args)
    return insights.story_graph(s, p, args.get("view", "all"),
                                 args.get("character"))


def h_expectation_add(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.expectation_add(
        p, _get(args, "chapter_n"), _get(args, "state"), args.get("note", ""),
        args.get("setup", ""), args.get("trigger", ""),
        args.get("payoff", ""), args.get("payoff_evidence", ""))


def h_expectation_audit(args: dict, s: Store):
    p = _require_project_arg(args)
    return insights.expectation_audit(s, p, args.get("chapter_n"))


def h_expectation_list(args: dict, s: Store):
    p = _require_project_arg(args)
    chapter_n = args.get("chapter_n")
    if chapter_n is not None:
        chapter_n = _chapter_n_arg(args)
    return {"ok": True,
            "stats": s.expectation_stats(p),
            "items": s.expectation_list(p, chapter_n)}


def h_pipeline_status(args: dict, s: Store):
    p = _require_project_arg(args)
    chapter_n = args.get("chapter_n")
    if chapter_n is not None:
        chapter_n = _chapter_n_arg(args)
    return insights.pipeline_status(s, p, chapter_n)


def h_appeal_pack(args: dict, s: Store):
    p = _require_project_arg(args)
    return insights.appeal_pack(s, p, _get(args, "chapter_n"))


def h_ima_catalog_search(args: dict, s: Store):
    return ima_client.search(_get(args, "query"), args.get("limit", 10),
                             kb_id=args.get("kb_id"))


def h_ima_export_scan(args: dict, s: Store):
    p = _require_project_arg(args)
    return ima_export.scan(
        s, p, directory=args.get("dir"),
        recursive=bool(args.get("recursive", True)),
        kind=args.get("kind") or "reference",
        tags=args.get("tags"), chapter=args.get("chapter"))


# ============================================================
# v1.10：字数统计 / 省 token / 修订建议 / 灵感访谈
# ============================================================
def h_word_stats(args: dict, s: Store):
    p = _require_project_arg(args)
    chapter_n = args.get("chapter_n")
    if chapter_n is None:
        return word_stats.project_word_stats(s, p)
    return word_stats.chapter_word_stats(
        s, p, _require_int(chapter_n, "chapter_n", minimum=0,
                           maximum=MAX_CHAPTER_N))


def h_chapter_digest(args: dict, s: Store):
    p = _require_project_arg(args)
    return token_saver.chapter_digest(
        s, p, n=args.get("chapter_n"),
        all_missing=bool(args.get("all_missing", False)),
        max_chars=args.get("max_chars", 600),
        save=bool(args.get("save", False)),
        overwrite=bool(args.get("overwrite", False)))


def h_token_audit(args: dict, s: Store):
    p = _require_project_arg(args)
    return token_saver.token_audit(
        s, p, chapter_n=args.get("chapter_n"),
        lookback=args.get("lookback", 10))


def h_revision_plan(args: dict, s: Store):
    p = _require_project_arg(args)
    return revision_advisor.revision_plan(
        s, p, _get(args, "n"), max_items=args.get("max_items", 8))


def h_inspiration_interview(args: dict, s: Store):
    p = _require_project_arg(args)
    return inspiration.inspiration_interview(
        s, p, chapter_n=args.get("chapter_n"),
        theme=args.get("theme", ""), count=args.get("count", 6))


def h_inspiration_synthesize(args: dict, s: Store):
    p = _require_project_arg(args)
    return inspiration.inspiration_synthesize(
        s, p, answers=_get(args, "answers"),
        chapter_n=args.get("chapter_n"), theme=args.get("theme", ""),
        save=bool(args.get("save", True)), tags=args.get("tags"))


# ============================================================
# 自动化与账本：单章流水线 / L0 检查 / 调用账本
# ============================================================
def h_run_start(args: dict, s: Store):
    p = _require_project_arg(args)
    return runner.start(s, p, args.get("policy"))


def h_run_status(args: dict, s: Store):
    return runner.status(s, _get(args, "run_id"))


def h_run_list(args: dict, s: Store):
    project = args.get("project")
    if project is not None:
        project = _require_str(project, "project")
    status = args.get("status")
    if status is not None:
        status = _require_str(status, "status", max_len=MAX_KEY_LEN)
    return runner.list_runs(s, project=project, status=status,
                            limit=args.get("limit", 50))


def h_run_resume(args: dict, s: Store):
    return runner.resume(s, _get(args, "run_id"), args.get("decision"))


def h_run_cancel(args: dict, s: Store):
    return runner.cancel(s, _get(args, "run_id"))


def h_run_batch_start(args: dict, s: Store):
    p = _require_project_arg(args)
    return runner.batch_start(s, p, _get(args, "chapters"),
                              args.get("policy"))


def h_run_batch_status(args: dict, s: Store):
    return runner.batch_status(s, _get(args, "batch_id"))


def h_run_batch_resume(args: dict, s: Store):
    return runner.batch_resume(s, _get(args, "batch_id"),
                               args.get("decision"))


def h_run_batch_cancel(args: dict, s: Store):
    return runner.batch_cancel(s, _get(args, "batch_id"))


def h_lint_check(args: dict, s: Store):
    p = _require_project_arg(args)
    n = _require_int(_get(args, "chapter_n"), "chapter_n",
                     minimum=0, maximum=MAX_CHAPTER_N)
    return lint.lint_chapter(s, p, n, persist=True)


def h_usage_report(args: dict, s: Store):
    p = _require_project_arg(args)
    return s.usage_summary(p, args.get("by") or "chapter")


# ============================================================
# 质量环：先 L0，再评分 / 修复
# ============================================================
def h_loop_start(args: dict, s: Store):
    p = _require_project_arg(args)
    chapter_n = args.get("chapter_n")
    if chapter_n is not None:
        chapter_n = _require_int(chapter_n, "chapter_n",
                                 minimum=0, maximum=MAX_CHAPTER_N)
    max_rounds = args.get("max_rounds")
    if max_rounds is None:
        max_rounds = 2
    threshold = args.get("threshold")
    if threshold is None:
        threshold = quality_loop.DEFAULT_THRESHOLD
    return quality_loop.start(
        s, p, chapter_n, max_rounds=max_rounds, threshold=threshold,
        apply=bool(args.get("apply", False)))


def h_loop_report(args: dict, s: Store):
    p = _require_project_arg(args)
    chapter_n = args.get("chapter_n")
    if chapter_n is not None:
        chapter_n = _require_int(chapter_n, "chapter_n",
                                 minimum=0, maximum=MAX_CHAPTER_N)
    limit = args.get("limit")
    if limit is None:
        limit = 20
    return quality_loop.report(s, p, loop_id=args.get("loop_id"),
                               chapter_n=chapter_n, limit=limit)


def h_loop_meta(args: dict, s: Store):
    p = _require_project_arg(args)
    window = args.get("window")
    if window is None:
        window = 10
    return quality_loop.meta(s, p, window=window)


def h_loop_meta_apply(args: dict, s: Store):
    p = _require_project_arg(args)
    return quality_loop.meta_apply(s, p, _get(args, "changes"),
                                   confirm=bool(args.get("confirm", False)))


# ============================================================
# AI Studio 桥接：读取网页设置 / 导出提示词 / 导入设置补丁
# ============================================================
def h_studio_pack_get(args: dict, s: Store):
    p = _require_project_arg(args)
    fmt = args.get("format") or "both"
    fmt = _require_enum(fmt, "format", ("json", "prompt", "both"))
    pack = studio_bridge.build_pack(s, p)
    out: Dict[str, Any] = {"ok": True}
    if fmt in ("json", "both"):
        out["pack"] = pack
        out["json"] = json.dumps(pack, ensure_ascii=False, indent=2)
    if fmt in ("prompt", "both"):
        out["prompt"] = studio_bridge.pack_prompt(pack, "settings")
    return out


def h_studio_pack_import(args: dict, s: Store):
    p = _require_project_arg(args)
    return studio_bridge.import_pack(s, p, _get(args, "pack"),
                                     dry_run=bool(args.get("dry_run", False)))


def h_studio_prompt(args: dict, s: Store):
    p = _require_project_arg(args)
    task = args.get("task") or "settings"
    chapter_n = args.get("chapter_n")
    if chapter_n is not None:
        chapter_n = _require_int(chapter_n, "chapter_n",
                                 minimum=0, maximum=MAX_CHAPTER_N)
    return studio_bridge.build_prompt(s, p, task, chapter_n=chapter_n)


# ---- AI Studio 自驾：next / submit / handoffs / design / cancel ----
def h_studio_next(args: dict, s: Store):
    p = _require_project_arg(args)
    return studio_autopilot.next_task(s, p)


def h_studio_submit(args: dict, s: Store):
    return studio_autopilot.submit(s, _get(args, "handoff_id"),
                                   _get(args, "result"))


def h_studio_handoffs(args: dict, s: Store):
    p = _require_project_arg(args)
    return studio_autopilot.handoffs(s, p, status=args.get("status"))


def h_studio_design(args: dict, s: Store):
    p = _require_project_arg(args)
    return studio_autopilot.start_design(
        s, p, premise=args.get("premise") or "",
        genre=args.get("genre") or "",
        chapters=args.get("chapters") or 0,
        target_words=args.get("target_words") or 0)


def h_studio_cancel(args: dict, s: Store):
    return studio_autopilot.cancel(s, _get(args, "handoff_id"))


# ============================================================
# 表
# ============================================================
Handler = Callable[[Dict[str, Any], Store], Any]

HANDLERS: Dict[str, Handler] = {
    # 与项目无关（先于 project 校验）
    "profile_list": h_profile_list,
    "profile_get": h_profile_get,
    "profile_create": h_profile_create,
    "profile_delete": h_profile_delete,
    "profile_set_export_dir": h_profile_set_export_dir,
    "stage_advice": h_stage_advice,
    "project_init": h_project_init,
    "project_list": h_project_list,
    "project_export": h_project_export,
    "project_delete": h_project_delete,
    # 设置 / 模型 / IMA 搜索（与项目无关）
    "settings_get": h_settings_get,
    "settings_set": h_settings_set,
    "llm_generate": h_llm_generate,
    "ima_search": h_ima_search,
    "role_deck_list": h_role_deck_list,
    "trick_list": h_trick_list,
    "role_prompt": h_role_prompt,
    "trick_prompt": h_trick_prompt,
    "review_chain_prompt": h_review_chain_prompt,
    # 项目级
    "profile_bind": h_profile_bind,
    "bible_upsert": h_bible_upsert,
    "bible_get": h_bible_get,
    "bible_list": h_bible_list,
    "bible_search": h_bible_search,
    "entity_meta_get": h_entity_meta_get,
    "entity_meta_set": h_entity_meta_set,
    "timeline_add": h_timeline_add,
    "timeline_query": h_timeline_query,
    "foreshadow_add": h_foreshadow_add,
    "foreshadow_list": h_foreshadow_list,
    "foreshadow_resolve": h_foreshadow_resolve,
    "foreshadow_overdue": h_foreshadow_overdue,
    "chapter_save": h_chapter_save,
    "chapter_patch": h_chapter_patch,
    "chapter_get": h_chapter_get,
    "chapter_list": h_chapter_list,
    "chapter_versions": h_chapter_versions,
    "chapter_diff": h_chapter_diff,
    "chapter_export": h_chapter_export,
    "style_set": h_style_set,
    "style_get": h_style_get,
    "style_distill": h_style_distill,
    "style_profile_get": h_style_profile_get,
    "style_profile_apply": h_style_profile_apply,
    "style_blind_test": h_style_blind_test,
    "fact_add": h_fact_add,
    "fact_set_status": h_fact_set_status,
    "fact_list": h_fact_list,
    "audit_log": h_audit_log,
    "audit_list": h_audit_list,
    "outline_set": h_outline_set,
    "outline_get": h_outline_get,
    "outline_list": h_outline_list,
    "outline_tree": h_outline_tree,
    "outline_next": h_outline_next,
    "outline_brief": h_outline_brief,
    "outline_versions": h_outline_versions,
    "outline_delete": h_outline_delete,
    "outline_diff": h_outline_diff,
    "material_add": h_material_add,
    "material_get": h_material_get,
    "material_update": h_material_update,
    "material_audit": h_material_audit,
    "material_list": h_material_list,
    "material_delete": h_material_delete,
    "section_save": h_section_save,
    "section_get": h_section_get,
    "section_list": h_section_list,
    "section_merge": h_section_merge,
    "section_delete": h_section_delete,
    "context_estimate": h_context_estimate,
    "context_trace": h_context_trace,
    "workspace_export": h_workspace_export,
    "packet_assemble": h_packet_assemble,
    "ledger_stats": h_ledger_stats,
    "config_set": h_config_set,
    "config_get": h_config_get,
    # 知识库 / IMA 落库
    "kb_import": h_kb_import,
    "kb_search": h_kb_search,
    "kb_categories": h_kb_categories,
    # 引用与素材
    "citation_add": h_citation_add,
    "citation_verify": h_citation_verify,
    "citation_list": h_citation_list,
    "ima_fetch": h_ima_fetch,
    # 摘要与故事状态
    "summary_set": h_summary_set,
    "summary_list": h_summary_list,
    "story_state_set": h_story_state_set,
    "story_state_get": h_story_state_get,
    "story_state_list": h_story_state_list,
    # 叙事工作台
    "compliance_check": h_compliance_check,
    "voice_check": h_voice_check,
    "ai_declaration": h_ai_declaration,
    # 叙事工作台 v1.6
    "chapter_tree": h_chapter_tree,
    "structure_check": h_structure_check,
    "voice_cross_check": h_voice_cross_check,
    "ooc_check": h_ooc_check,
    "story_graph": h_story_graph,
    "expectation_add": h_expectation_add,
    "expectation_list": h_expectation_list,
    "expectation_audit": h_expectation_audit,
    "pipeline_status": h_pipeline_status,
    "appeal_pack": h_appeal_pack,
    "ima_catalog_search": h_ima_catalog_search,
    "ima_export_scan": h_ima_export_scan,
    "ima_import_urls": h_ima_import_urls,
    "ima_upload": h_ima_upload,
    # v1.10
    "word_stats": h_word_stats,
    "chapter_digest": h_chapter_digest,
    "token_audit": h_token_audit,
    "revision_plan": h_revision_plan,
    "inspiration_interview": h_inspiration_interview,
    "inspiration_synthesize": h_inspiration_synthesize,
    # 自动化与账本
    "run_start": h_run_start,
    "run_status": h_run_status,
    "run_list": h_run_list,
    "run_resume": h_run_resume,
    "run_cancel": h_run_cancel,
    "run_batch_start": h_run_batch_start,
    "run_batch_status": h_run_batch_status,
    "run_batch_resume": h_run_batch_resume,
    "run_batch_cancel": h_run_batch_cancel,
    "lint_check": h_lint_check,
    "usage_report": h_usage_report,
    # 质量环
    "loop_start": h_loop_start,
    "loop_report": h_loop_report,
    "loop_meta": h_loop_meta,
    "loop_meta_apply": h_loop_meta_apply,
    # AI Studio 桥接
    "studio_pack_get": h_studio_pack_get,
    "studio_pack_import": h_studio_pack_import,
    "studio_prompt": h_studio_prompt,
    # AI Studio 自驾
    "studio_next": h_studio_next,
    "studio_submit": h_studio_submit,
    "studio_handoffs": h_studio_handoffs,
    "studio_design": h_studio_design,
    "studio_cancel": h_studio_cancel,
}


def dispatch(name: str, args: Dict[str, Any], s: Store) -> Any:
    handler = HANDLERS.get(name)
    if handler is None:
        raise LongformError(f"未知工具：{name}；可用工具：{sorted(TOOL_MAP)}")
    return handler(args, s)
