#!/usr/bin/env python3
"""叙事工作台命令行入口（零第三方依赖）。

    python cli.py serve                 # 启动本地 Web 编辑器
    python cli.py doctor                # 环境自检（有 FAIL 时退出码 1）
    python cli.py tools [--json]        # 打印 MCP 工具清单
    python cli.py projects              # 列出项目
    python cli.py export <project> [--dir DIR] [--profile NAME]
    python cli.py settings get
    python cli.py settings set --json '{"llm": {...}}'
    python cli.py notify test [--text TEXT] [--json]
    python cli.py llm <prompt> [--system TEXT] [--temperature FLOAT] [--max-tokens INT]
    python cli.py kb import <path> [--project P] [--tag T]...
    python cli.py kb search <query> [--project P] [--limit N]
    python cli.py kb categories [--json]
    python cli.py citation add <project> --chapter N --claim TEXT [--source S] [--locator L]
    python cli.py citation verify <project> --id ID
    python cli.py citation list <project> [--chapter N] [--verified|--unverified] [--limit N] [--json]
    python cli.py run start <project> [--budget N] [--target-words N]
    python cli.py run status <run_id> [--json]
    python cli.py run list [--project P] [--status S] [--json]
    python cli.py run resume <run_id> [--approve|--reject]
    python cli.py run cancel <run_id>
    python cli.py run batch <project> --chapters N [--budget N] [--target-words N] [--no-human-gate]
    python cli.py run batch-status <batch_id> [--json]
    python cli.py run batch-resume <batch_id> [--approve|--reject]
    python cli.py run batch-cancel <batch_id>
    python cli.py ima import-urls <project> --urls u1,u2 [--kb ID]
    python cli.py ima upload <project> --file PATH [--kb ID]
    python cli.py lint <project> --chapter N [--json]
    python cli.py loop start <project> [--chapter N] [--max-rounds 2] [--threshold 8.5] [--apply]
    python cli.py loop report <project> [--loop-id ID] [--chapter N] [--json]
    python cli.py loop meta <project> [--window 10] [--json]
    python cli.py loop meta-apply <project> --json '{"threshold":8.2}' [--confirm]
    python cli.py usage <project> [--by chapter|model|day|run] [--json]
    python cli.py studio show <project> [--json]
    python cli.py studio prompt <project> [--task settings|bootstrap|outline|chapter|quality|style|review] [--chapter N] [--out FILE]
    python cli.py studio export <project> [--out DIR] [--format both|json|prompt]
    python cli.py studio import <project> --file PATH [--dry-run]
    python cli.py studio set <project> [--mode ai-studio|direct] [--requirements TEXT] [--tool-groups 组1,组2]
    python cli.py studio next <project> [--json]
    python cli.py studio submit <handoff_id> (--file PATH | --text TEXT) [--json]
    python cli.py studio handoffs <project> [--status pending|applied|...] [--json]
    python cli.py studio design <project> --premise TEXT [--genre G] [--chapters N] [--target-words N]
    python cli.py studio cancel <handoff_id>
    python cli.py style distill <project> (--files a.md,b.md | --chapters 1,2,3) [--name NAME]
    python cli.py style show <project> [--id ID] [--json]
    python cli.py style apply <project> --id ID
    python cli.py style blind-test <project> --reference-file REF.txt --candidate-file CAND.txt [--profile ID] [--threshold 75] [--json]
    python cli.py entity meta-get <project> --kind K --name N [--json]
    python cli.py entity meta-set <project> --kind K --name N [--anchors a,b] [--priority always|high|normal] [--first-chapter N]
    python cli.py context trace <project> [--chapter N] [--brief TEXT] [--json]

与 MCP 服务共用同一批模块与同一份 SQLite：CLI 只是人手动调试/运维的入口，
不引入任何新的写入路径。``server.py`` 的 ``--list-tools`` / ``--self-test``
行为完全不变。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from typing import List, Optional

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
MCP_DIR = os.path.join(REPO_ROOT, "mcp", "longform_mcp")
# 必须置顶：便携运行时的 ._pth 会预先包含该目录，而标准库里也有 profile.py；
# 若只做“不存在才插入”，标准库会抢先命中，导致 profile.PROFILES_DIR 缺失。
if MCP_DIR in sys.path:
    sys.path.remove(MCP_DIR)
sys.path.insert(0, MCP_DIR)

# JSON / 中文正文必须能完整输出；Windows 管道默认跟随 locale（cp936）会
# 在遇到 emoji 或书名号时直接抛 UnicodeEncodeError。
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="replace")

import exporting  # noqa: E402
import kb  # noqa: E402
import llm_client  # noqa: E402
import profile as profiles  # noqa: E402
import server  # noqa: E402
import settings  # noqa: E402
from store import LongformError, Store  # noqa: E402

DEFAULT_PROJECT = "default"


# ============================================================
# serve
# ============================================================
def cmd_serve(args: argparse.Namespace) -> int:
    # 延迟导入：只有真正要起服务时才加载 http.server 与路由表。
    import web_server
    return web_server.run(host=args.host, port=args.port,
                          open_browser=not args.no_open)


# ============================================================
# doctor
# ============================================================
def _check_python() -> bool:
    version = sys.version_info
    if version >= (3, 9):
        print(f"[ok] Python {version.major}.{version.minor}.{version.micro}（要求 ≥3.9）")
        return True
    print(f"[FAIL] Python {version.major}.{version.minor}.{version.micro} 过低，需要 ≥3.9")
    return False


def _check_sqlite() -> bool:
    fd, db_file = tempfile.mkstemp(prefix="longform_doctor_", suffix=".db")
    os.close(fd)
    store: Optional[Store] = None
    ok = True
    try:
        store = Store(db_file)
        if store.fts_available:
            print("[ok] SQLite 可用，FTS5 trigram 已启用")
        else:
            print("[warn] SQLite 可用，但 FTS5 trigram 不可用；素材检索将回退 LIKE")
    except Exception as e:  # noqa: BLE001 自检必须给出结论而不是崩溃
        print(f"[FAIL] SQLite 初始化失败：{type(e).__name__}: {e}")
        ok = False
    finally:
        if store is not None:
            try:
                store.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            os.unlink(db_file)
        except OSError:
            pass
    return ok


def _check_config() -> bool:
    checker = os.path.join(REPO_ROOT, "scripts", "check_config.py")
    if not os.path.isfile(checker):
        print(f"[FAIL] 缺少配置检查脚本：{checker}")
        return False
    proc = subprocess.run([sys.executable, checker], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    if proc.returncode == 0:
        print("[ok] 配置一致性检查通过（models.json / defaults.yaml / registry.yaml）")
        return True
    lines = [ln.strip() for ln in (proc.stdout or proc.stderr or "").splitlines()
             if ln.strip()]
    detail = lines[-1] if lines else f"退出码 {proc.returncode}"
    print(f"[FAIL] 配置一致性检查失败：{detail}")
    return False


def _check_profiles() -> bool:
    if os.path.isdir(profiles.PROFILES_DIR):
        print(f"[ok] 变体目录存在：{profiles.PROFILES_DIR}")
        return True
    print(f"[FAIL] 变体目录不存在：{profiles.PROFILES_DIR}"
          "（可创建该目录或设置 LONGFORM_PROFILES_DIR）")
    return False


def _check_db_writable() -> bool:
    db_dir = os.path.dirname(os.path.abspath(server.DB_PATH)) or "."
    try:
        os.makedirs(db_dir, exist_ok=True)
        fd, probe = tempfile.mkstemp(prefix=".write_probe_", dir=db_dir)
        os.close(fd)
        os.unlink(probe)
    except OSError as e:
        print(f"[FAIL] 数据库目录不可写：{db_dir}（{e}）")
        return False
    print(f"[ok] 数据库目录可写：{db_dir}")
    return True


def _check_settings() -> None:
    view = settings.public_view()
    for name, label in (("llm", "LLM"), ("ima", "IMA")):
        if view.get(name, {}).get("configured"):
            print(f"[ok] {label} 已配置")
        else:
            print(f"[warn] {label} 未配置；相关功能会降级。"
                  "可在设置页或 config/settings.json 中配置，"
                  "也可用 LONGFORM_* 环境变量覆盖")


def cmd_doctor(_args: argparse.Namespace) -> int:
    print("叙事工作台 · 环境自检")
    checks = [_check_python(), _check_sqlite(), _check_config(),
              _check_profiles(), _check_db_writable()]
    _check_settings()
    failures = checks.count(False)
    if failures:
        print(f"\n共 {failures} 项 FAIL，请按上面的提示修复。")
        return 1
    print("\n全部检查通过。")
    return 0


# ============================================================
# tools / projects / export
# ============================================================
def cmd_tools(args: argparse.Namespace) -> int:
    if args.json:
        print(json.dumps(server.TOOLS, ensure_ascii=False, indent=2))
        return 0
    print(f"共 {len(server.TOOLS)} 个工具：")
    for tool in server.TOOLS:
        print(f"  - {tool['name']}")
    return 0


def cmd_projects(_args: argparse.Namespace) -> int:
    rows = server.get_store().project_list()
    if not rows:
        print("（暂无项目）")
        return 0
    print(f"共 {len(rows)} 个项目：")
    for row in rows:
        print(f"  {row['id']}\t{row['kind']}\t{row['chapters']} 章\t{row['name']}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    store = server.get_store()
    cfg = exporting.export_cfg(store, args.project, profile_name=args.profile,
                               override_dir=args.dir)
    result = profiles.export_project(store, args.project, cfg)
    print(f"已导出 {result['count']} 章 → {result['dir']}")
    for item in result["files"]:
        print(f"  v{item['version']}\t{item['path']}")
    return 0


# ============================================================
# settings / llm
# ============================================================
def cmd_settings_get(_args: argparse.Namespace) -> int:
    print(json.dumps(settings.public_view(), ensure_ascii=False, indent=2))
    return 0


def cmd_settings_set(args: argparse.Namespace) -> int:
    try:
        patch = json.loads(args.json_text)
    except ValueError as e:
        raise LongformError(f"--json 不是合法 JSON：{e}") from None
    if not isinstance(patch, dict):
        raise LongformError("--json 必须是 JSON 对象")
    view = settings.save_settings(patch)
    print(json.dumps(view, ensure_ascii=False, indent=2))
    return 0


def cmd_llm(args: argparse.Namespace) -> int:
    result = llm_client.generate(args.prompt, system=args.system,
                                 temperature=args.temperature,
                                 max_tokens=args.max_tokens)
    print(result.get("text", ""))
    return 0


# ============================================================
# notify（延迟导入；未配置时退出码 1）
# ============================================================
def cmd_notify_test(args: argparse.Namespace) -> int:
    import notify
    text = args.text or "叙事工作台通知测试"
    out = notify.send("test", text)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    elif out.get("ok"):
        print(f"已发送测试通知（{out.get('channel')}）")
    elif out.get("skipped"):
        print(f"未发送：{out.get('reason') or '通知未配置'}。"
              "请在设置里开启 notify，或设置 "
              "LONGFORM_NOTIFY_WEBHOOK / LONGFORM_NOTIFY_CLI")
    else:
        print(f"发送失败：{out.get('error')}")
    return 0 if out.get("ok") else 1


# ============================================================
# kb
# ============================================================
def _project_id(args: argparse.Namespace) -> str:
    """未指定 --project 时用默认项目；必要时就地创建，避免导入落空。"""
    if args.project:
        return args.project
    store = server.get_store()
    if store.project_get(DEFAULT_PROJECT) is None:
        store.project_init(DEFAULT_PROJECT, overwrite=False)
        print(f"[warn] 未指定 --project，已使用默认项目 {DEFAULT_PROJECT}",
              file=sys.stderr)
    return DEFAULT_PROJECT


def cmd_kb_import(args: argparse.Namespace) -> int:
    store = server.get_store()
    pid = _project_id(args)
    path = args.path
    tags = args.tag or None
    if os.path.isdir(path):
        result = kb.import_dir(store, pid, path, tags=tags)
    elif os.path.isfile(path):
        result = kb.import_file(store, pid, path, tags=tags)
    else:
        raise LongformError(f"路径不存在：{path}")

    print(f"已导入 {result['count']} 条素材到项目 {pid}")
    for warning in result.get("warnings") or []:
        print(f"[warn] {warning}", file=sys.stderr)
    return 0


def cmd_kb_search(args: argparse.Namespace) -> int:
    store = server.get_store()
    pid = _project_id(args)
    items = kb.search(store, pid, args.query, limit=args.limit)
    print(json.dumps(items, ensure_ascii=False, indent=2))
    return 0


def cmd_kb_categories(args: argparse.Namespace) -> int:
    out = kb.categories()
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    print(f"素材分类（{len(out['categories'])} 类）：")
    for cat in out["categories"]:
        print(f"  {cat['key']}\t{cat['name']}\t{cat['hint']}")
    print("同义词：")
    for key, values in out["synonyms"].items():
        print(f"  {key} → {'、'.join(values)}")
    return 0


# ============================================================
# citation（引证登记与验真；延迟导入 citations）
# ============================================================
def cmd_citation_add(args: argparse.Namespace) -> int:
    store = server.get_store()
    out = store.citation_add(args.project, args.chapter, args.claim,
                             source=args.source or "",
                             locator=args.locator or "")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_citation_verify(args: argparse.Namespace) -> int:
    import citations
    out = citations.verify(server.get_store(), args.project, args.id)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_citation_list(args: argparse.Namespace) -> int:
    verified = True if args.verified else (False if args.unverified else None)
    rows = server.get_store().citation_list(
        args.project, chapter_n=args.chapter, verified=verified,
        limit=args.limit)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("（暂无引证）")
        return 0
    for row in rows:
        status = "已验真" if row["verified"] else "未验真"
        chapter = "" if row.get("chapter_n") is None else f"第 {row['chapter_n']} 章\t"
        print(f"{row['id']}\t{status}\t{chapter}{row['claim']}"
              f"\t来源：{row.get('source') or '（无）'}")
    return 0


# ============================================================
# run / lint / usage（延迟导入，--help 不碰数据库）
# ============================================================
def _fmt_time(value) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(value)))
    except (TypeError, ValueError, OSError):
        return str(value or "")


def _print_run(out: dict) -> None:
    run = out.get("run") or {}
    target = run.get("target") or {}
    print(f"运行 {run.get('id', '-')}：{run.get('status', '-')}")
    print(f"  项目：{run.get('project', '-')}\t第 {target.get('chapter_n', '-')} 章"
          f"\t{target.get('title') or ''}".rstrip())
    if run.get("error"):
        print(f"  错误：{run['error']}")
    steps = out.get("steps") or []
    if steps:
        print("  步骤：" + " → ".join(
            f"{row.get('name')}({row.get('status')})" for row in steps))
    usage = out.get("usage") or {}
    print(f"  本次 tokens：{usage.get('total_tokens', 0)}")


def cmd_run_start(args: argparse.Namespace) -> int:
    import runner
    policy = {}
    if args.budget is not None:
        policy["budget_tokens"] = args.budget
    if args.target_words is not None:
        policy["target_words"] = args.target_words
    if args.no_human_gate:
        policy["require_human_gate"] = False
    if args.temperature is not None:
        policy["temperature"] = args.temperature
    out = runner.start(server.get_store(), args.project, policy or None)
    _print_run(out)
    return 0


def cmd_run_status(args: argparse.Namespace) -> int:
    import runner
    out = runner.status(server.get_store(), args.run_id)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        _print_run(out)
    return 0


def cmd_run_list(args: argparse.Namespace) -> int:
    import runner
    rows = runner.list_runs(server.get_store(), project=args.project,
                            status=args.status)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("（暂无运行）")
        return 0
    print(f"共 {len(rows)} 条运行：")
    for row in rows:
        target = row.get("target") or {}
        print(f"  {row.get('id')}\t{row.get('status')}\t{row.get('project')}"
              f"\t第 {target.get('chapter_n', '-')} 章\t"
              f"{_fmt_time(row.get('created_at'))}")
    return 0


def cmd_run_resume(args: argparse.Namespace) -> int:
    import runner
    decision = "approve" if args.approve else ("reject" if args.reject else None)
    out = runner.resume(server.get_store(), args.run_id, decision)
    _print_run(out)
    return 0


def cmd_run_cancel(args: argparse.Namespace) -> int:
    import runner
    out = runner.cancel(server.get_store(), args.run_id)
    _print_run(out)
    return 0


def _print_batch(out: dict) -> None:
    run = out.get("run") or {}
    target = run.get("target") or {}
    print(f"批次 {run.get('id', '-')}：{run.get('status', '-')}")
    print(f"  项目：{run.get('project', '-')}\t第 {target.get('start_chapter', '-')} 章起"
          f"共 {target.get('chapters', '-')} 章"
          f"\t当前第 {target.get('current_chapter', '-')} 章")
    if run.get("error"):
        print(f"  错误：{run['error']}")
    if out.get("exit_reason"):
        print(f"  退出原因：{out['exit_reason']}")
    usage = out.get("usage") or {}
    print(f"  整批 tokens：{usage.get('total_tokens', 0)}")
    for row in out.get("children") or []:
        print(f"  第 {row.get('chapter', '-')} 章\t{row.get('status', '-')}"
              f"\t{row.get('usage', 0)} tokens")


def cmd_run_batch_start(args: argparse.Namespace) -> int:
    import runner
    policy = {}
    if args.budget is not None:
        policy["budget_tokens"] = args.budget
    if args.target_words is not None:
        policy["target_words"] = args.target_words
    if args.no_human_gate:
        policy["require_human_gate"] = False
    out = runner.batch_start(server.get_store(), args.project,
                             args.chapters, policy or None)
    _print_batch(out)
    return 0


def cmd_run_batch_status(args: argparse.Namespace) -> int:
    import runner
    out = runner.batch_status(server.get_store(), args.batch_id)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        _print_batch(out)
    return 0


def cmd_run_batch_resume(args: argparse.Namespace) -> int:
    import runner
    decision = "approve" if args.approve else ("reject" if args.reject else None)
    out = runner.batch_resume(server.get_store(), args.batch_id, decision)
    _print_batch(out)
    return 0


def cmd_run_batch_cancel(args: argparse.Namespace) -> int:
    import runner
    out = runner.batch_cancel(server.get_store(), args.batch_id)
    _print_batch(out)
    return 0


def cmd_ima_import_urls(args: argparse.Namespace) -> int:
    import ima_client
    urls = [u.strip() for u in (args.urls or "").split(",") if u.strip()]
    out = ima_client.import_urls(urls, kb_id=args.kb)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("ok") else 1


def cmd_ima_upload(args: argparse.Namespace) -> int:
    import ima_client
    out = ima_client.upload_file(args.file, kb_id=args.kb)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("ok") else 1


def cmd_lint(args: argparse.Namespace) -> int:
    import lint
    out = lint.lint_chapter(server.get_store(), args.project, args.chapter,
                            persist=True)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    counts = out.get("counts") or {}
    print(f"第 {out['chapter_n']} 章 L0 检查：block {counts.get('block', 0)}"
          f" / warn {counts.get('warn', 0)}")
    for f in out.get("findings") or []:
        locator = f"@{f['locator']}" if f.get("locator") else ""
        print(f"  [{f.get('level')}] {f.get('rule')}{locator} "
              f"{f.get('suggestion', '')}".rstrip())
    if not out.get("findings"):
        print("  未发现问题")
    return 0


def cmd_loop_start(args: argparse.Namespace) -> int:
    import quality_loop
    out = quality_loop.start(
        server.get_store(), args.project, args.chapter,
        max_rounds=args.max_rounds, threshold=args.threshold,
        apply=bool(args.apply))
    if out.get("status") == "blocked":
        lint_out = out.get("lint") or {}
        counts = lint_out.get("counts") or {}
        print(f"第 {out['chapter_n']} 章被 L0 阻断（质量环 {out['loop_id']}）："
              f"block {counts.get('block', 0)} / warn {counts.get('warn', 0)}")
        return 0
    print(f"质量环 {out['loop_id']}：{out.get('status')}，"
          f"最佳分 {out.get('best_score')}，退出原因 {out.get('exit_reason')}")
    if out.get("applied"):
        print(f"已写入新版本 v{out.get('version')}（第 {out['chapter_n']} 章）")
    else:
        text = out.get("best_text") or ""
        print("未写正文（apply=false）。最佳正文前 500 字：")
        print(text[:500])
    return 0


def cmd_loop_report(args: argparse.Namespace) -> int:
    import quality_loop
    out = quality_loop.report(server.get_store(), args.project,
                              loop_id=args.loop_id, chapter_n=args.chapter)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    if out.get("loop"):
        run = out["loop"]
        print(f"质量环 {run['id']}：{run.get('status')}，最佳分 "
              f"{run.get('best_score')}，退出原因 {run.get('exit_reason')}")
        metrics = out.get("metrics") or []
        print(f"  评分 {len(metrics)} 条：")
        for m in metrics:
            print(f"  r{m.get('round')}\t{m.get('dimension')}\t"
                  f"{m.get('score')}\toverall {m.get('overall')}")
        return 0
    runs = out.get("runs") or []
    if not runs:
        print("（暂无质量环记录）")
        return 0
    print(f"共 {len(runs)} 条质量环记录：")
    for row in runs:
        print(f"  {row['id']}\t{row.get('status')}\t第 {row.get('chapter_n')} 章"
              f"\t均分 {row.get('avg_score')}\t轮数 {row.get('rounds')}"
              f"\t{row.get('exit_reason') or ''}".rstrip())
    return 0


def cmd_loop_meta(args: argparse.Namespace) -> int:
    import quality_loop
    out = quality_loop.meta(server.get_store(), args.project, window=args.window)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    print(f"质量环复盘：{len(out.get('runs') or [])} 次已完成运行")
    print(f"  均分 {out.get('avg_score')}\t通过率 {out.get('pass_rate')}")
    for v in out.get("top_violations") or []:
        print(f"  违规 {v['rule']}：{v['count']} 次")
    for p in out.get("proposals") or []:
        print(f"  建议：{p}")
    if not out.get("proposals"):
        print("  建议：（暂无）")
    return 0


def cmd_loop_meta_apply(args: argparse.Namespace) -> int:
    import quality_loop
    try:
        changes = json.loads(args.changes_json)
    except ValueError as e:
        raise LongformError(f"--json 不是合法 JSON：{e}") from None
    out = quality_loop.meta_apply(server.get_store(), args.project, changes,
                                  confirm=bool(args.confirm))
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_usage(args: argparse.Namespace) -> int:
    out = server.get_store().usage_summary(args.project, args.by or "chapter")
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    calls = sum(int(g.get("calls") or 0) for g in out.get("groups") or [])
    print(f"项目 {out['project']} 的 LLM 调用账（按 {out['by']}）：")
    print(f"  调用 {calls} 次，tokens {out['total_tokens']}"
          f"（输入 {out['total_input_tokens']} / 输出 {out['total_output_tokens']}"
          f" / 缓存 {out['total_cached_tokens']}），成本 {out['total_cost']:.4f}")
    for g in out.get("groups") or []:
        print(f"  {g.get('key')}\t调用 {g.get('calls')}\t"
              f"tokens {int(g.get('input_tokens') or 0) + int(g.get('output_tokens') or 0)}"
              f"\t成本 {float(g.get('cost') or 0):.4f}")
    return 0


# ============================================================
# studio（AI Studio 桥接；延迟导入 studio_bridge）
# ============================================================
def _write_text(path: str, text: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def cmd_studio_show(args: argparse.Namespace) -> int:
    import studio_bridge
    pack = studio_bridge.build_pack(server.get_store(), args.project)
    if args.json:
        print(json.dumps(pack, ensure_ascii=False, indent=2))
        return 0
    project = pack["project"]
    studio = pack["studio"]
    tools = pack.get("tools") or {}
    print(f"项目 {project['id']}（{project['name']}，{project['kind']}）")
    print(f"  模式：{studio['mode']}")
    print(f"  写作要求：{studio['requirements'] or '（未填写）'}")
    print(f"  启用工具组：{'、'.join(tools.get('enabled_groups') or []) or '（全部）'}")
    print(f"  文风项：{len(pack['style'])}，配置项：{len(pack['config'])}")
    for warning in pack.get("warnings") or []:
        print(f"  [warn] {warning}")
    return 0


def cmd_studio_prompt(args: argparse.Namespace) -> int:
    import studio_bridge
    out = studio_bridge.build_prompt(server.get_store(), args.project, args.task,
                                     chapter_n=args.chapter)
    text = out["prompt"]
    if args.out:
        _write_text(args.out, text)
        print(f"已写入 {args.out}")
    else:
        print(text)
    return 0


def cmd_studio_export(args: argparse.Namespace) -> int:
    import studio_bridge
    out_dir = args.out or "."
    os.makedirs(out_dir, exist_ok=True)
    pack = studio_bridge.build_pack(server.get_store(), args.project)
    paths: List[str] = []
    if args.format in ("both", "json"):
        path = os.path.join(out_dir, f"{args.project}.studio.json")
        _write_text(path, json.dumps(pack, ensure_ascii=False, indent=2))
        paths.append(path)
    if args.format in ("both", "prompt"):
        path = os.path.join(out_dir, f"{args.project}.studio.prompt.md")
        _write_text(path, studio_bridge.pack_prompt(pack, "settings"))
        paths.append(path)
    for path in paths:
        print(f"已写入 {path}")
    return 0


def cmd_studio_import(args: argparse.Namespace) -> int:
    import studio_bridge
    if not os.path.isfile(args.file):
        raise LongformError(f"文件不存在：{args.file}")
    with open(args.file, encoding="utf-8") as f:
        text = f.read()
    out = studio_bridge.import_pack(server.get_store(), args.project, text,
                                    dry_run=bool(args.dry_run))
    counts = out.get("counts") or {}
    if out.get("dry_run"):
        print(f"[dry-run] 将写入 {len(out.get('changes') or [])} 项：")
        for change in out.get("changes") or []:
            print(f"  {change['scope']}\t{change['key']}")
    print(f"style {counts.get('style', 0)} 项，config {counts.get('config', 0)} 项，"
          f"studio {counts.get('studio', 0)} 项")
    return 0


def cmd_studio_next(args: argparse.Namespace) -> int:
    import studio_autopilot
    out = studio_autopilot.next_task(server.get_store(), args.project)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    print(f"动作：{out.get('action')}")
    print(f"原因：{out.get('why')}")
    action = out.get("action")
    if action == "tool":
        print(f"工具：{out.get('tool')}")
        print("参数：" + json.dumps(out.get("args") or {}, ensure_ascii=False))
    elif action == "handoff":
        handoff = out.get("handoff") or {}
        print(f"交接单：{handoff.get('handoff_id')}（{handoff.get('kind')}）")
        print("提示词：")
        print(handoff.get("prompt") or "")
        schema = handoff.get("schema")
        if schema:
            print("期望 JSON：")
            print(json.dumps(schema, ensure_ascii=False, indent=2))
    elif action == "human":
        print(f"人工闸门：{out.get('gate')}")
    elif action == "done":
        summary = out.get("summary") or {}
        print("已完成：" + json.dumps(summary, ensure_ascii=False))
    return 0


def _read_submit_result(path: Optional[str], text: Optional[str]) -> object:
    """--text 直接当字符串；--file 先按 utf-8 读入，能解析 JSON 就用 JSON。"""
    if text is not None:
        return text
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def cmd_studio_submit(args: argparse.Namespace) -> int:
    import studio_autopilot
    result = _read_submit_result(args.file, args.text)
    out = studio_autopilot.submit(server.get_store(), args.handoff_id, result)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out.get("ok") else 1
    if out.get("already_applied"):
        print("该交接单已应用（幂等，未重复写入）")
        return 0
    if not out.get("ok"):
        print(f"错误：{out.get('error')}", file=sys.stderr)
        return 1
    print(f"已应用 {out.get('kind')} 交接单 {out.get('handoff_id')}")
    if out.get("result") is not None:
        print(json.dumps(out["result"], ensure_ascii=False, indent=2))
    return 0


def cmd_studio_handoffs(args: argparse.Namespace) -> int:
    import studio_autopilot
    out = studio_autopilot.handoffs(server.get_store(), args.project,
                                    args.status)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    rows = out.get("handoffs") or []
    if not rows:
        print("（暂无交接单）")
        return 0
    for row in rows:
        chapter = row.get("chapter_n")
        suffix = "" if chapter is None else "\t第%d章" % chapter
        print(f"{row['id']}\t{row['kind']}\t{row['status']}{suffix}")
    return 0


def cmd_studio_design(args: argparse.Namespace) -> int:
    import studio_autopilot
    out = studio_autopilot.start_design(
        server.get_store(), args.project, premise=args.premise,
        genre=args.genre or "", chapters=args.chapters or 0,
        target_words=args.target_words or 0)
    print(f"交接单：{out['handoff_id']}（{out['kind']}）")
    print("提示词：")
    print(out["prompt"])
    return 0


def cmd_studio_cancel(args: argparse.Namespace) -> int:
    import studio_autopilot
    out = studio_autopilot.cancel(server.get_store(), args.handoff_id)
    print(f"已取消交接单 {out['id']}（{out['kind']}）")
    return 0


def cmd_studio_set(args: argparse.Namespace) -> int:
    if args.mode is None and args.requirements is None and args.tool_groups is None:
        raise LongformError("studio set 至少需要 --mode / --requirements / --tool-groups 之一")
    store = server.get_store()
    if store.project_get(args.project) is None:
        raise LongformError(f"项目不存在：{args.project}")
    if args.mode is not None:
        store.config_set(args.project, "studio.mode", args.mode)
    if args.requirements is not None:
        store.config_set(args.project, "studio.requirements", args.requirements)
    if args.tool_groups is not None:
        groups = [g.strip() for g in args.tool_groups.split(",") if g.strip()]
        store.config_set(args.project, "studio.tool_groups", groups)
    print(json.dumps(store.config_get(args.project, "studio") or {},
                     ensure_ascii=False, indent=2))
    return 0


# ============================================================
# style（文风蒸馏；延迟导入 style_distill）
# ============================================================
def _read_style_files(raw: str) -> List[str]:
    """逗号分隔的样本文件；逐个以 utf-8 读入，不存在或为空立即报错。"""
    paths = [p.strip() for p in (raw or "").split(",") if p.strip()]
    if not paths:
        raise LongformError("--files 不能为空，逗号分隔多个文件")
    texts: List[str] = []
    for path in paths:
        if not os.path.isfile(path):
            raise LongformError(f"文件不存在：{path}")
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except OSError as e:
            raise LongformError(f"读取文件失败：{path}（{e}）") from None
        if not text.strip():
            raise LongformError(f"文件为空：{path}")
        texts.append(text)
    return texts


def _parse_chapter_list(raw: str) -> List[int]:
    chapters: List[int] = []
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            chapters.append(int(item))
        except ValueError:
            raise LongformError(f"--chapters 含非法章号：{item}") from None
    if not chapters:
        raise LongformError("--chapters 不能为空，逗号分隔多个章号")
    return chapters


def cmd_style_distill(args: argparse.Namespace) -> int:
    import style_distill
    texts = None
    chapter_ns = None
    if args.files:
        texts = _read_style_files(args.files)
    else:
        chapter_ns = _parse_chapter_list(args.chapters)
    out = style_distill.distill(
        server.get_store(), args.project, texts=texts,
        chapter_ns=chapter_ns, name=args.name)
    source = out["source"]
    print(f"文风档案 {out['id']}：{out['name']}"
          f"（{len(source['samples'])} 个样本 / {source['total_chars']} 字）")
    for warning in out.get("warnings") or []:
        print(f"[warn] {warning}", file=sys.stderr)
    return 0


def cmd_style_show(args: argparse.Namespace) -> int:
    import style_distill
    out = style_distill.get(server.get_store(), args.project, args.id)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    if out.get("profile"):
        row = out["profile"]
        print(f"文风档案 {row['id']}：{row['name']}（{row['created_at']}）")
        dims = (row.get("profile") or {}).get("dimensions") or {}
        for key, value in dims.items():
            print(f"  {key}: {value}")
        return 0
    profiles = out.get("profiles") or []
    if not profiles:
        print("（暂无文风档案）")
        return 0
    print(f"共 {len(profiles)} 个文风档案：")
    for item in profiles:
        print(f"  {item['id']}\t{item['name']}\t{item['created_at']}"
              f"\t禁用词 {item.get('banned_count', 0)} 个")
    return 0


def cmd_style_apply(args: argparse.Namespace) -> int:
    import style_distill
    out = style_distill.apply(server.get_store(), args.project, args.id)
    print(f"已应用文风档案 {out['id']}（{out['name']}）："
          f"写入 {len(out['applied'])} 项，禁用词 {out['banned_count']} 个")
    return 0


def _read_text_file(path: str, flag: str) -> str:
    """按 UTF-8 读入单个样本文件；缺失或为空立即报错。"""
    if not os.path.isfile(path):
        raise LongformError(f"{flag} 文件不存在：{path}")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        raise LongformError(f"{flag} 读取文件失败：{path}（{e}）") from None
    if not text.strip():
        raise LongformError(f"{flag} 文件为空：{path}")
    return text


def cmd_style_blind_test(args: argparse.Namespace) -> int:
    import style_distill
    reference = _read_text_file(args.reference_file, "--reference-file")
    candidate = _read_text_file(args.candidate_file, "--candidate-file")
    out = style_distill.blind_test(
        server.get_store(), args.project,
        reference_texts=[reference], candidate_texts=[candidate],
        profile_id=args.profile, threshold=args.threshold)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    status = "通过" if out["passed"] else "未通过"
    print(f"文风盲测：{out['score']} / {status}")
    for dim in out["dimensions"]:
        print(f"  {dim['label']}：参考 {dim['reference']} / "
              f"候选 {dim['candidate']} / 相似度 {dim['similarity']}")
    if out["banned_hits"]:
        print(f"  禁用词命中 {out['banned_hits']} 次，扣 {out['penalty']} 分")
    return 0


# ============================================================
# entity meta / context trace
# ============================================================
def cmd_entity_meta_get(args: argparse.Namespace) -> int:
    out = server.get_store().entity_meta_get(
        args.project, args.kind, args.name)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    print(f"{args.kind}:{args.name}")
    print(f"  priority          : {out['priority']}")
    print(f"  anchors           : {', '.join(out['anchors']) or '（无）'}")
    print(f"  content_hash      : {out['content_hash']}")
    print(f"  first_seen_chapter: {out['first_seen_chapter']}")
    print(f"  last_seen_chapter : {out['last_seen_chapter']}")
    return 0


def cmd_entity_meta_set(args: argparse.Namespace) -> int:
    anchors = None
    if args.anchors is not None:
        anchors = [a.strip() for a in args.anchors.split(",") if a.strip()]
    out = server.get_store().entity_meta_set(
        args.project, args.kind, args.name, anchors=anchors,
        priority=args.priority, first_seen_chapter=args.first_chapter)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_context_trace(args: argparse.Namespace) -> int:
    import packet
    store = server.get_store()
    n = args.chapter
    if n is None:
        rows = store.chapter_list(args.project)
        if not rows:
            raise LongformError(
                "项目还没有任何章节：请显式传 --chapter，或先写第 1 章")
        n = max(int(r["n"]) for r in rows)
    out = packet.packet_trace(store, args.project, n, args.brief or "")
    out = {"project": args.project, "chapter_n": n, **out}
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    budget = out["budget"]
    print(f"第 {n} 章设定激活轨迹：{len(out['activated'])} 条激活 / "
          f"{len(out['dropped'])} 条未激活；预算 {budget['used']}/"
          f"{budget['tokens'] * 4} 字符（{budget['ratio']:.2%}）")
    for item in out["activated"]:
        print(f"  + [{item['kind']}] {item['name']}　{item['reason']}"
              f"　{item['chars']} 字符")
    for item in out["dropped"]:
        print(f"  - [{item['kind']}] {item['name']}　{item['reason']}")
    for w in out["warnings"]:
        print(f"  ! {w}")
    return 0


# ============================================================
# 解析
# ============================================================
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="叙事工作台命令行入口（零第三方依赖）")
    sub = parser.add_subparsers(dest="cmd")

    serve = sub.add_parser("serve", help="启动本地 Web 编辑器")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    serve.set_defaults(func=cmd_serve)

    doctor = sub.add_parser("doctor", help="环境自检（Python / SQLite / 配置 / 目录 / 设置）")
    doctor.set_defaults(func=cmd_doctor)

    tools = sub.add_parser("tools", help="打印 MCP 工具清单")
    tools.add_argument("--json", action="store_true", help="输出 JSON")
    tools.set_defaults(func=cmd_tools)

    projects = sub.add_parser("projects", help="列出项目")
    projects.set_defaults(func=cmd_projects)

    export = sub.add_parser("export", help="导出项目正文")
    export.add_argument("project")
    export.add_argument("--dir", default=None, help="导出目录（覆盖变体配置）")
    export.add_argument("--profile", default=None, help="临时指定作品变体")
    export.set_defaults(func=cmd_export)

    cfg = sub.add_parser("settings", help="查看或修改运行环境配置")
    cfg_sub = cfg.add_subparsers(dest="settings_cmd")
    cfg_get = cfg_sub.add_parser("get", help="打印脱敏后的配置")
    cfg_get.set_defaults(func=cmd_settings_get)
    cfg_set = cfg_sub.add_parser("set", help="按 JSON 补丁写入配置")
    cfg_set.add_argument("--json", dest="json_text", required=True,
                         help="JSON 补丁，例如 '{\"llm\": {\"model\": \"x\"}}'")
    cfg_set.set_defaults(func=cmd_settings_set)

    llm = sub.add_parser("llm", help="直接调用已配置的模型服务")
    llm.add_argument("prompt")
    llm.add_argument("--system", default=None)
    llm.add_argument("--temperature", type=float, default=None)
    llm.add_argument("--max-tokens", type=int, default=None)
    llm.set_defaults(func=cmd_llm)

    notify_parser = sub.add_parser(
        "notify", help="外部通知：Lark webhook / 本地 CLI")
    notify_sub = notify_parser.add_subparsers(dest="notify_cmd")
    notify_test = notify_sub.add_parser("test", help="发送一条测试通知")
    notify_test.add_argument("--text", default=None, help="通知正文")
    notify_test.add_argument("--json", action="store_true", help="输出 JSON")
    notify_test.set_defaults(func=cmd_notify_test)

    kb_parser = sub.add_parser("kb", help="知识库导入与检索")
    kb_sub = kb_parser.add_subparsers(dest="kb_cmd")
    kb_import = kb_sub.add_parser("import", help="导入文本文件或目录")
    kb_import.add_argument("path")
    kb_import.add_argument("--project", default=None)
    kb_import.add_argument("--tag", action="append", default=None,
                           help="标签，可重复")
    kb_import.set_defaults(func=cmd_kb_import)
    kb_search = kb_sub.add_parser("search", help="全文检索素材")
    kb_search.add_argument("query")
    kb_search.add_argument("--project", default=None)
    kb_search.add_argument("--limit", type=int, default=None)
    kb_search.set_defaults(func=cmd_kb_search)
    kb_cat = kb_sub.add_parser("categories", help="查看素材地图与同义词")
    kb_cat.add_argument("--json", action="store_true", help="输出 JSON")
    kb_cat.set_defaults(func=cmd_kb_categories)

    citation_parser = sub.add_parser("citation", help="引证登记与验真")
    citation_sub = citation_parser.add_subparsers(dest="citation_cmd")
    citation_add = citation_sub.add_parser("add", help="登记一条引证")
    citation_add.add_argument("project")
    citation_add.add_argument("--chapter", type=int, required=True,
                              help="关联章号")
    citation_add.add_argument("--claim", required=True, help="正文里的断言")
    citation_add.add_argument("--source", default=None,
                              help="素材 id / ch:N / 原始来源文本")
    citation_add.add_argument("--locator", default=None, help="出处定位，如 第3段")
    citation_add.set_defaults(func=cmd_citation_add)
    citation_verify = citation_sub.add_parser("verify", help="验真一条引证")
    citation_verify.add_argument("project")
    citation_verify.add_argument("--id", required=True, help="引证 id")
    citation_verify.set_defaults(func=cmd_citation_verify)
    citation_list = citation_sub.add_parser("list", help="列出引证")
    citation_list.add_argument("project")
    citation_list.add_argument("--chapter", type=int, default=None)
    citation_flag = citation_list.add_mutually_exclusive_group()
    citation_flag.add_argument("--verified", action="store_true",
                               help="只看已验真")
    citation_flag.add_argument("--unverified", action="store_true",
                               help="只看未验真")
    citation_list.add_argument("--limit", type=int, default=None)
    citation_list.add_argument("--json", action="store_true", help="输出 JSON")
    citation_list.set_defaults(func=cmd_citation_list)

    run_parser = sub.add_parser("run", help="单章自动流水线（Run Engine）")
    run_sub = run_parser.add_subparsers(dest="run_cmd")
    run_start = run_sub.add_parser("start", help="启动「下一章」流水线")
    run_start.add_argument("project")
    run_start.add_argument("--budget", type=int, default=None,
                           help="本次运行 token 预算")
    run_start.add_argument("--target-words", type=int, default=None,
                           help="目标字数")
    run_start.add_argument("--no-human-gate", action="store_true",
                           help="关闭人工闸门，跑完直接定稿")
    run_start.add_argument("--temperature", type=float, default=None)
    run_start.set_defaults(func=cmd_run_start)
    run_status = run_sub.add_parser("status", help="查看一次运行")
    run_status.add_argument("run_id")
    run_status.add_argument("--json", action="store_true", help="输出 JSON")
    run_status.set_defaults(func=cmd_run_status)
    run_list = run_sub.add_parser("list", help="列出运行记录")
    run_list.add_argument("--project", default=None)
    run_list.add_argument("--status", default=None)
    run_list.add_argument("--json", action="store_true", help="输出 JSON")
    run_list.set_defaults(func=cmd_run_list)
    run_resume = run_sub.add_parser("resume", help="推进运行 / 人工裁决")
    run_resume.add_argument("run_id")
    gate = run_resume.add_mutually_exclusive_group()
    gate.add_argument("--approve", action="store_true", help="批准并定稿")
    gate.add_argument("--reject", action="store_true", help="拒绝并取消运行")
    run_resume.set_defaults(func=cmd_run_resume)
    run_cancel = run_sub.add_parser("cancel", help="取消一次未完成的运行")
    run_cancel.add_argument("run_id")
    run_cancel.set_defaults(func=cmd_run_cancel)
    run_batch = run_sub.add_parser("batch", help="整本批跑：连续写 N 章")
    run_batch.add_argument("project")
    run_batch.add_argument("--chapters", type=int, required=True,
                           help="连续写多少章")
    run_batch.add_argument("--budget", type=int, default=None,
                           help="整批 token 预算")
    run_batch.add_argument("--target-words", type=int, default=None,
                           help="单章目标字数")
    run_batch.add_argument("--no-human-gate", action="store_true",
                           help="关闭人工闸门，每章直接定稿")
    run_batch.set_defaults(func=cmd_run_batch_start)
    run_batch_status = run_sub.add_parser("batch-status", help="查看批次进度")
    run_batch_status.add_argument("batch_id")
    run_batch_status.add_argument("--json", action="store_true", help="输出 JSON")
    run_batch_status.set_defaults(func=cmd_run_batch_status)
    run_batch_resume = run_sub.add_parser("batch-resume", help="恢复批次 / 人工裁决")
    run_batch_resume.add_argument("batch_id")
    batch_gate = run_batch_resume.add_mutually_exclusive_group()
    batch_gate.add_argument("--approve", action="store_true", help="批准当前章并继续")
    batch_gate.add_argument("--reject", action="store_true", help="拒绝当前章并取消批次")
    run_batch_resume.set_defaults(func=cmd_run_batch_resume)
    run_batch_cancel = run_sub.add_parser("batch-cancel", help="取消批次与当前子运行")
    run_batch_cancel.add_argument("batch_id")
    run_batch_cancel.set_defaults(func=cmd_run_batch_cancel)

    lint_parser = sub.add_parser("lint", help="对某章跑 L0 确定性检查")
    lint_parser.add_argument("project")
    lint_parser.add_argument("--chapter", type=int, required=True)
    lint_parser.add_argument("--json", action="store_true", help="输出 JSON")
    lint_parser.set_defaults(func=cmd_lint)

    loop_parser = sub.add_parser("loop", help="质量环：先 L0，再评分与定向修复")
    loop_sub = loop_parser.add_subparsers(dest="loop_cmd")
    loop_start = loop_sub.add_parser("start", help="启动一次质量环")
    loop_start.add_argument("project")
    loop_start.add_argument("--chapter", type=int, default=None,
                            help="章号；省略取最新章")
    loop_start.add_argument("--max-rounds", type=int, default=2)
    loop_start.add_argument("--threshold", type=float, default=8.5)
    loop_start.add_argument("--apply", action="store_true",
                            help="把最佳文本写成新版本（默认不写正文）")
    loop_start.set_defaults(func=cmd_loop_start)
    loop_report = loop_sub.add_parser("report", help="查看质量环结果")
    loop_report.add_argument("project")
    loop_report.add_argument("--loop-id", default=None)
    loop_report.add_argument("--chapter", type=int, default=None)
    loop_report.add_argument("--json", action="store_true", help="输出 JSON")
    loop_report.set_defaults(func=cmd_loop_report)
    loop_meta = loop_sub.add_parser("meta", help="质量环复盘与改进建议")
    loop_meta.add_argument("project")
    loop_meta.add_argument("--window", type=int, default=10)
    loop_meta.add_argument("--json", action="store_true", help="输出 JSON")
    loop_meta.set_defaults(func=cmd_loop_meta)
    loop_meta_apply = loop_sub.add_parser("meta-apply", help="应用调权建议（默认只预览）")
    loop_meta_apply.add_argument("project")
    loop_meta_apply.add_argument("--json", dest="changes_json", required=True,
                                 help='变更 JSON，例如 {"threshold":8.2}')
    loop_meta_apply.add_argument("--confirm", action="store_true",
                                 help="确认写入配置与禁用词；省略只预览")
    loop_meta_apply.set_defaults(func=cmd_loop_meta_apply)

    usage_parser = sub.add_parser("usage", help="查看 LLM 调用账本")
    usage_parser.add_argument("project")
    usage_parser.add_argument("--by", choices=("chapter", "model", "day", "run"),
                              default="chapter")
    usage_parser.add_argument("--json", action="store_true", help="输出 JSON")
    usage_parser.set_defaults(func=cmd_usage)

    ima_parser = sub.add_parser("ima", help="IMA 知识库写入：URL 导入 / 文件上传")
    ima_sub = ima_parser.add_subparsers(dest="ima_cmd")
    ima_import = ima_sub.add_parser("import-urls", help="批量导入网页 URL")
    ima_import.add_argument("project")
    ima_import.add_argument("--urls", required=True,
                            help="逗号分隔的 http/https 地址（≤50 条）")
    ima_import.add_argument("--kb", default=None, help="知识库 id")
    ima_import.set_defaults(func=cmd_ima_import_urls)
    ima_upload = ima_sub.add_parser("upload", help="上传本地文件（≤50MB）")
    ima_upload.add_argument("project")
    ima_upload.add_argument("--file", required=True, help="本地文件路径")
    ima_upload.add_argument("--kb", default=None, help="知识库 id")
    ima_upload.set_defaults(func=cmd_ima_upload)

    studio_parser = sub.add_parser(
        "studio", help="AI Studio 桥接：读取网页设置 / 导出提示词 / 导入设置补丁")
    studio_sub = studio_parser.add_subparsers(dest="studio_cmd")

    studio_show = studio_sub.add_parser("show", help="查看网页里设置的文风 / 工具 / 写作要求")
    studio_show.add_argument("project")
    studio_show.add_argument("--json", action="store_true", help="输出完整设置包")
    studio_show.set_defaults(func=cmd_studio_show)

    studio_prompt = studio_sub.add_parser("prompt", help="生成可粘贴到 AI Studio 的提示词")
    studio_prompt.add_argument("project")
    studio_prompt.add_argument("--task",
                               choices=("settings", "bootstrap", "outline",
                                        "chapter", "quality", "style", "review"),
                               default="settings")
    studio_prompt.add_argument("--chapter", type=int, default=None,
                               help="task=chapter 时的章号")
    studio_prompt.add_argument("--out", default=None, help="写入文件；省略则打印")
    studio_prompt.set_defaults(func=cmd_studio_prompt)

    studio_export = studio_sub.add_parser("export", help="导出设置包与提示词文件")
    studio_export.add_argument("project")
    studio_export.add_argument("--out", default=None, help="输出目录（默认当前目录）")
    studio_export.add_argument("--format", choices=("both", "json", "prompt"),
                               default="both")
    studio_export.set_defaults(func=cmd_studio_export)

    studio_import = studio_sub.add_parser("import", help="导入 AI Studio 设置补丁")
    studio_import.add_argument("project")
    studio_import.add_argument("--file", required=True, help="设置补丁 JSON 文件")
    studio_import.add_argument("--dry-run", action="store_true",
                               help="只打印变更清单，不写库")
    studio_import.set_defaults(func=cmd_studio_import)

    studio_set = studio_sub.add_parser("set", help="修改项目的 studio 设置")
    studio_set.add_argument("project")
    studio_set.add_argument("--mode", choices=("ai-studio", "direct"), default=None)
    studio_set.add_argument("--requirements", default=None,
                            help="写作要求文本")
    studio_set.add_argument("--tool-groups", default=None,
                            help="逗号分隔的启用工具组；空字符串表示全部")
    studio_set.set_defaults(func=cmd_studio_set)

    studio_next = studio_sub.add_parser(
        "next", help="AI Studio 自驾：查看下一步（工具 / 交接 / 人工 / 完成）")
    studio_next.add_argument("project")
    studio_next.add_argument("--json", action="store_true", help="输出 JSON")
    studio_next.set_defaults(func=cmd_studio_next)

    studio_submit = studio_sub.add_parser(
        "submit", help="提交 AI Studio 对话返回的交接结果")
    studio_submit.add_argument("handoff_id")
    source = studio_submit.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", default=None, help="utf-8 结果文件（JSON 优先）")
    source.add_argument("--text", default=None, help="直接传入字符串结果")
    studio_submit.add_argument("--json", action="store_true", help="输出 JSON")
    studio_submit.set_defaults(func=cmd_studio_submit)

    studio_handoffs = studio_sub.add_parser("handoffs", help="列出交接单")
    studio_handoffs.add_argument("project")
    studio_handoffs.add_argument("--status", default=None,
                                 help="pending / applied / failed 等")
    studio_handoffs.add_argument("--json", action="store_true", help="输出 JSON")
    studio_handoffs.set_defaults(func=cmd_studio_handoffs)

    studio_design = studio_sub.add_parser("design", help="一键立项：创建立项交接单")
    studio_design.add_argument("project")
    studio_design.add_argument("--premise", required=True, help="核心设定 / 一句话梗概")
    studio_design.add_argument("--genre", default=None, help="题材")
    studio_design.add_argument("--chapters", type=int, default=None, help="计划章数")
    studio_design.add_argument("--target-words", type=int, default=None,
                               help="目标总字数")
    studio_design.set_defaults(func=cmd_studio_design)

    studio_cancel = studio_sub.add_parser("cancel", help="取消未应用的交接单")
    studio_cancel.add_argument("handoff_id")
    studio_cancel.set_defaults(func=cmd_studio_cancel)

    style_parser = sub.add_parser("style", help="文风蒸馏：提炼 / 查看 / 应用文风档案")
    style_sub = style_parser.add_subparsers(dest="style_cmd")

    style_distill = style_sub.add_parser(
        "distill", help="从文件或章节蒸馏文风档案（需要 3 篇/2000 字）")
    style_distill.add_argument("project")
    style_src = style_distill.add_mutually_exclusive_group(required=True)
    style_src.add_argument("--files", default=None,
                           help="逗号分隔的样本文件（utf-8）")
    style_src.add_argument("--chapters", default=None,
                           help="逗号分隔的章号，取各章最新正文")
    style_distill.add_argument("--name", default="默认画像")
    style_distill.set_defaults(func=cmd_style_distill)

    style_show = style_sub.add_parser("show", help="查看文风档案列表或详情")
    style_show.add_argument("project")
    style_show.add_argument("--id", default=None, help="档案 id；省略则列出全部")
    style_show.add_argument("--json", action="store_true", help="输出 JSON")
    style_show.set_defaults(func=cmd_style_show)

    style_apply = style_sub.add_parser("apply", help="把档案写入项目 style（不改正文）")
    style_apply.add_argument("project")
    style_apply.add_argument("--id", required=True, help="档案 id")
    style_apply.set_defaults(func=cmd_style_apply)

    style_blind = style_sub.add_parser(
        "blind-test", help="文风盲测：参考样本 vs 候选样本（只比特征，不改正文）")
    style_blind.add_argument("project")
    style_blind.add_argument("--reference-file", required=True,
                             help="参考样本文件（utf-8）")
    style_blind.add_argument("--candidate-file", required=True,
                             help="候选样本/待检查稿文件（utf-8）")
    style_blind.add_argument("--profile", default=None, help="用该文风档案的禁用词")
    style_blind.add_argument("--threshold", type=float, default=75,
                             help="达标阈值 50–100，默认 75")
    style_blind.add_argument("--json", action="store_true", help="输出 JSON")
    style_blind.set_defaults(func=cmd_style_blind_test)

    entity_parser = sub.add_parser("entity", help="世界书条目元数据（激活优先级与别名）")
    entity_sub = entity_parser.add_subparsers(dest="entity_cmd")
    entity_get = entity_sub.add_parser("meta-get", help="读取条目的激活元数据")
    entity_get.add_argument("project")
    entity_get.add_argument("--kind", required=True)
    entity_get.add_argument("--name", required=True)
    entity_get.add_argument("--json", action="store_true", help="输出 JSON")
    entity_get.set_defaults(func=cmd_entity_meta_get)
    entity_set = entity_sub.add_parser("meta-set", help="设置条目的激活元数据")
    entity_set.add_argument("project")
    entity_set.add_argument("--kind", required=True)
    entity_set.add_argument("--name", required=True)
    entity_set.add_argument("--anchors", default=None,
                            help="逗号分隔的别名/关键词，如 松香味,匿名信")
    entity_set.add_argument("--priority", default=None,
                            choices=("always", "high", "normal"))
    entity_set.add_argument("--first-chapter", type=int, default=None,
                            help="首次出现章号（正整数）")
    entity_set.set_defaults(func=cmd_entity_meta_set)

    context_parser = sub.add_parser("context", help="输入包上下文账与激活轨迹")
    context_sub = context_parser.add_subparsers(dest="context_cmd")
    context_trace = context_sub.add_parser(
        "trace", help="解释本章激活/丢弃了哪些设定")
    context_trace.add_argument("project")
    context_trace.add_argument("--chapter", type=int, default=None,
                               help="章号；省略取最新章")
    context_trace.add_argument("--brief", default=None, help="本章细纲（用于复现激活）")
    context_trace.add_argument("--json", action="store_true", help="输出 JSON")
    context_trace.set_defaults(func=cmd_context_trace)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 0
    try:
        return int(func(args))
    except LongformError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    except Exception as e:  # noqa: BLE001 命令行入口统一兜底
        print(f"[FAIL] {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
