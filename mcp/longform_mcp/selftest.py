"""内置自检：``python server.py --self-test``。

跑一遍关键路径，带断言与退出码，且自检结束后清掉自己建的项目。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

import profile as profiles
from exporting import export_cfg, try_export
from packet import context_estimate, packet_text
from pipeline import outline_brief, outline_next, stage_advice
from store import (
    MAX_BRIEF_LEN,
    MAX_CHAPTER_OUTLINE_LEN,
    MIN_TOKEN_BUDGET,
    LongformError,
    Store,
    _CHILD_TABLES,
)


def _raises(exc, fn) -> bool:
    try:
        fn()
    except exc:
        return True
    return False


def run_self_test(s: Store) -> int:
    """跑一遍关键路径，带断言、有退出码，且不留下垃圾项目。"""
    pid = "__selftest__"
    failures: list = []

    def check(cond, label):
        print(("  [ok]   " if cond else "  [FAIL] ") + label)
        if not cond:
            failures.append(label)

    try:
        s.project_init(pid, kind="novel", overwrite=True)
        s.bible_upsert(pid, "character", "林越",
                       {"age": 29, "traits": ["寡言"], "alive": True})
        s.timeline_add(pid, "林越抵达废港", 1)
        s.foreshadow_add(pid, "铜钥匙的来历", 1, 9)
        s.chapter_save(pid, 1, "第一章", "他很早就到了。港口的雾像旧纱布。" * 50)
        s.style_set(pid, "pov", "第三人称限知")
        s.fact_add(pid, "废港每年雾季为三个月")
        s.audit_log(pid, 1, {"verdict": "pass", "reviewer": "04-审校专家"})

        stats = s.ledger_stats(pid)
        check(stats["entities"] == 1, "设定档写入")
        check(stats["events"] == 1, "时间线写入")
        check(stats["foreshadow_open"] == 1, "伏笔登记")
        check(stats["chapters"] == 1, "章节保存")
        check(stats["facts_pending"] == 1, "待核事实登记")
        check(stats["audits"] == 1, "审计报告落盘")

        text, included = packet_text(s, pid, 2, "林越在码头遇到旧识")
        check("章节输入包" in text, "输入包组装")
        check("林越" in text, "输入包含设定条目")
        check("文风档案" in text, "输入包含文风档案")
        check("上一章" in text, "输入包含上章结尾")

        # 回归红线：静默失败必须已消灭
        check(_raises(LongformError, lambda: s.foreshadow_resolve(pid, "fs_不存在", 3)),
              "回收不存在的伏笔必须报错")
        check(_raises(LongformError, lambda: s.chapter_save(pid, 2, "t", "x", status="aproved")),
              "非法章节状态必须报错")
        check(_raises(LongformError, lambda: s.bible_upsert("幽灵项目", "character", "x", {})),
              "向不存在的项目写设定必须报错")
        check(_raises(LongformError, lambda: s.project_export("幽灵项目")),
              "导出不存在的项目必须报错")

        # ---- 作品变体：独立文件夹微调 / 便于增删 / 指定保存目录 ----
        # 自检绝不碰仓库里的 profiles/，先切到一个临时目录再玩。
        _tmp = tempfile.mkdtemp(prefix="longform_selftest_profiles_")
        _old_profiles_dir = profiles.PROFILES_DIR
        profiles.PROFILES_DIR = _tmp
        try:
            profiles.ensure_template()
            v = profiles.create("selftest-变体", description="自检用变体",
                                export_dir="正文")
            check(os.path.isdir(os.path.join(_tmp, "selftest-变体")), "变体创建")
            check(v["export_dir"] == os.path.join(_tmp, "selftest-变体", "正文"),
                  "变体输出目录相对变体文件夹解析")
            check(len(profiles.list_profiles()) == 2,
                  "变体列举（含 _default 模板）")

            s.project_set_profile(pid, "selftest-变体")
            check(s.project_get_profile(pid) == "selftest-变体", "变体绑定到项目")

            saved = s.chapter_save(pid, 1, "第一章", "雾从海面上压过来。" * 20)
            exp = try_export(s, pid, saved["n"], saved["version"],
                             "selftest-变体", None)
            check(exp.get("ok") is True, "章节导出到变体目录")
            check("第001章" in os.path.basename(exp.get("path", "")),
                  "导出文件名带三位章号")

            # 改稿不覆盖：versioned=true 时每版各留一份
            profiles.create("selftest-改稿", description="改稿不覆盖")
            cpath = os.path.join(_tmp, "selftest-改稿", "profile.json")
            ccfg = profiles.load_jsonc(cpath)
            ccfg["export"]["versioned"] = True
            profiles.dump_jsonc(cpath, ccfg)
            cfg2 = export_cfg(s, pid, "selftest-改稿")
            s.chapter_save(pid, 2, "第二章", "初稿。" * 10)
            a = profiles.export_chapter(s.chapter_get(pid, 2, 1), cfg2)
            s.chapter_save(pid, 2, "第二章", "二稿重写了开头。" * 10)
            b = profiles.export_chapter(s.chapter_get(pid, 2, 2), cfg2)
            check(a["path"] != b["path"] and os.path.isfile(a["path"]),
                  "versioned=true 时历史稿不被覆盖")

            # 注释剥离器不得破坏字符串里的 "//" 与 "/*"
            raw = '{"a": "https://x.example/a//b", /* 段注 */ "b": 1 // 行注\n}'
            check(json.loads(profiles.strip_json_comments(raw))["a"]
                  == "https://x.example/a//b", "注释剥离不误伤 URL")

            # 删除是破坏性操作，必须逐字确认
            check(_raises(LongformError, lambda: profiles.delete("selftest-变体")),
                  "删除变体缺少 confirm 必须报错")
            check(_raises(LongformError,
                          lambda: profiles.delete("selftest-变体", confirm="selftest")),
                  "confirm 与变体名不一致必须报错")
            check(profiles.delete("selftest-变体", confirm="selftest-变体",
                                  dry_run=True).get("dry_run") is True,
                  "dry_run 返回预览结果")
            check(os.path.isdir(os.path.join(_tmp, "selftest-变体")),
                  "dry_run 后变体仍在")
            check(profiles.delete("selftest-变体", confirm="selftest-变体").get("ok") is True,
                  "变体删除")
            check(not os.path.isdir(os.path.join(_tmp, "selftest-变体")),
                  "变体删除后目录消失")

            # 变体被删之后，读操作要宽容：packet 仍应能组装出来
            text2, _inc = packet_text(s, pid, 1, "林越返回废港")
            check("章节输入包" in text2, "变体丢失后输入包仍可组装（降级不中断）")
        finally:
            profiles.PROFILES_DIR = _old_profiles_dir
            shutil.rmtree(_tmp, ignore_errors=True)

        # ---- 大纲四级流水线：总纲 → 卷纲 → 章纲 → 正文 ----
        nxt0 = outline_next(s, pid)
        check(nxt0["stage"] == "master", "无总纲时 outline_next 指向 master")
        s.outline_set(pid, "master", status="approved", body="三幕",
                      data={"骨架": "起承转合", "总章数": 4,
                            "has_volumes": True, "结局": "雾散"})
        check(outline_next(s, pid)["stage"] == "volume", "有总纲+分卷 → 下一步卷纲")
        s.outline_set(pid, "volume", seq=1, start_chapter=1, end_chapter=2)
        s.outline_set(pid, "volume", seq=2, start_chapter=3, end_chapter=4)
        check(_raises(LongformError, lambda: s.outline_set(
            pid, "volume", seq=3, start_chapter=2, end_chapter=3)), "卷区间重叠必须报错")
        nxt1 = outline_next(s, pid)
        check(nxt1["stage"] == "chapter" and nxt1["next_chapter"] == 1,
              "卷齐了 → 下一步第 1 章章纲")
        c1 = s.outline_set(pid, "chapter", chapter_n=1, body="林越抵达废港")
        check(c1["id"] == "c001" and c1["parent_id"] == "v01",
              "章纲自动挂到覆盖它的卷")
        brief = outline_brief(s, pid, 1)
        check(brief["source"] == "outline" and brief["volume"] == "v01",
              "outline_brief 可取且带卷归属")
        check(s.outline_set(pid, "chapter", chapter_n=1, body="展开版")["version"] == 2,
              "章纲同 id 再写即修订")
        check(_raises(LongformError, lambda: s.outline_set(
            pid, "chapter", chapter_n=1, node_id="other")),
              "同一章号被另一 id 占用必须报错")
        # 硬不变量：章纲上限 == MAX_BRIEF_LEN，任何章纲都能喂 packet_assemble
        check(MAX_CHAPTER_OUTLINE_LEN == MAX_BRIEF_LEN, "章纲长度上限与 brief 同域")

        # ---- 阶段化 LLM 建议 ----
        adv = stage_advice(stage="chapter")
        check(adv["model"]["tier"] == "planning", "章纲阶段建议 planning 档")
        check(adv["expert"] == "02-架构专家" and "outline_set" in adv["tools"],
              "章纲阶段给出专家与工具")
        models_cfg, _mw = profiles.load_models()
        check(models_cfg["source"] == "models.json", "模型配置可读")
        check(all(models_cfg["tiers"][t]["name"] for t in profiles.MODEL_TIERS),
              "四个模型档位齐备")
        check(_raises(LongformError, lambda: stage_advice(stage="写正文")),
              "非法阶段必须报错")

        # 配置漂移：tiers 双写 + registry ↔ 代码工具集合。真跑一次脚本，
        # 不在这里再实现一遍 YAML 行解析——两份逻辑会各自漂移。
        _cc = os.path.join(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))), "scripts", "check_config.py")
        if os.path.isfile(_cc):
            _r = subprocess.run([sys.executable, _cc],
                                capture_output=True, text=True, encoding="utf-8")
            check(_r.returncode == 0,
                  "models.json ↔ defaults.yaml ↔ registry.yaml 无漂移"
                  + ("" if _r.returncode == 0 else "：" + _r.stdout.strip()[:200]))

        # ---- 大纲版本对比 ----
        diff = s.outline_diff(pid, "c001")
        check(diff["from_version"] == 1 and diff["to_version"] == 2,
              "outline_diff 对比首版与最新版")
        check(diff["added_lines"] >= 1 and diff["removed_lines"] >= 1,
              "outline_diff 统计增删行")
        check(_raises(LongformError, lambda: s.outline_diff(pid, "master")),
              "单版本节点对比必须报错")

        # ---- 素材库（来源可追溯）----
        mt = s.material_add(pid, "雾季时长", "雾季约三个月", kind="research",
                            source="ima://7515060821851215/x", tags=["背景"])
        check(mt["source_kind"] == "ima", "素材来源种类按 source 形态自动判定")
        s.material_add(pid, "松香墨", "松香墨多产于南方", kind="quote",
                       source="D:/资料/笔记.md", tags=["设定"])
        check(s.material_list(pid, query="松香")[0]["title"] == "松香墨",
              "素材可按关键词检索")
        check(len(s.material_list(pid, tag="背景")) == 1, "素材可按标签过滤")
        check(_raises(LongformError,
                      lambda: s.material_add(pid, "x", kind="摘录")),
              "非法素材种类必须报错")
        check(_raises(LongformError, lambda: s.material_delete(pid, mt["id"],
                                                               confirm="错")),
              "删除素材缺少正确 confirm 必须报错")

        # ---- 逐段写作 ----
        s.section_save(pid, 9, 1, "第一段。")
        s.section_save(pid, 9, 3, "第三段。")      # 故意跳过第 2 段
        check(len(s.section_list(pid, 9)) == 2, "分段落稿")
        check(_raises(LongformError, lambda: s.section_merge(pid, 9)),
              "段落不连续时拒绝合并（宁可不拼，也不拼出缺段的正文）")
        s.section_save(pid, 9, 1, "第一段（重写）。")
        check(s.section_get(pid, 9, 1)["version"] == 2, "同段再写即修订")
        s.section_save(pid, 9, 2, "第二段。")
        merged = s.section_merge(pid, 9, title="第九章", status="draft")
        check(merged["version"] >= 1 and merged["words"] >= 6,
              "段落合并为整章并写入 chapter_save")
        check(_raises(LongformError,
                      lambda: s.section_delete(pid, 9, 1, confirm="9")),
              "删除段落的 confirm 必须是「章号:段序」")

        # ---- 上下文估算（与 packet_assemble 同源）----
        est = context_estimate(s, pid, 2, "林越按信上地址找到旧仓库")
        check(est["approx_tokens"] > 0 and isinstance(est["fits"], bool),
              "context_estimate 给出 token 估算")
        check([r["section"] for r in est["sections"]]
              == [r["section"] for r in sorted(
                  est["sections"], key=lambda r: -r["chars"])],
              "context_estimate 按占用从大到小排序")
        check(bool(est["suggestions"]), "context_estimate 给出可执行建议")
        small = context_estimate(s, pid, 2, "", token_budget=MIN_TOKEN_BUDGET)
        check(small["over_chars"] == max(0, small["approx_chars"]
                                         - small["budget_chars"]),
              "context_estimate 的超额计算自洽")
        check(small["token_budget"] == MIN_TOKEN_BUDGET,
              "token_budget 被钳制到下界")

        # ---- 工作目录分目录导出 ----
        _ws = tempfile.mkdtemp(prefix="longform_selftest_workspace_")
        try:
            we = profiles.workspace_export(s, pid, _ws)
            check(we["count"] >= 3 and bool(we["counts"]),
                  "工作目录分目录导出")
            check(os.path.isdir(os.path.join(_ws, "00-大纲"))
                  and os.path.isdir(os.path.join(_ws, "01-章节草稿")),
                  "工作目录包含大纲与草稿子目录")
            check(_raises(LongformError,
                          lambda: profiles.workspace_export(s, pid, _ws,
                                                            include=["不存在"])),
                  "include 取值非法必须报错")
        finally:
            shutil.rmtree(_ws, ignore_errors=True)

        # 重置必须真的清空（新表都已登记 _CHILD_TABLES）
        s.project_init(pid, kind="novel", overwrite=True)
        check(s.ledger_stats(pid)["entities"] == 0, "overwrite 重置清空子表")
        check(s.outline_list(pid) == [], "overwrite 重置清空大纲")
        check(s.material_list(pid) == [], "overwrite 重置清空素材")
        check(s.section_list(pid, 9) == [], "overwrite 重置清空段落")
    finally:
        # 自检不该污染数据库
        for table in _CHILD_TABLES:
            s.conn.execute(f"DELETE FROM {table} WHERE project=?", (pid,))
        s.conn.execute("DELETE FROM projects WHERE id=?", (pid,))

    print("\n=== 内置自检" + ("通过 ✅" if not failures else f"失败 ❌（{len(failures)} 项）") + " ===")
    return 0 if not failures else 1
