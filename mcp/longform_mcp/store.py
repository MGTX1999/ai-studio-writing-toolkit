"""数据层：SQLite 持久化。

设计原则：
1. 纯标准库（sqlite3 / json / re / difflib / time），零第三方依赖，任何环境都能跑。
2. schema 全部使用 CREATE TABLE IF NOT EXISTS，向前兼容，改字段不会毁掉旧项目。
3. 所有"结构化"内容以 JSON 存 data_json，方便后期扩展字段而无需改表。
4. **写入即校验**：类型 / 枚举 / 边界 / 长度在数据层拦截，脏数据不落盘，
   目标状态不存在的写操作一律报错，绝不静默成功。
"""

from __future__ import annotations

import difflib
import hashlib
import itertools
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

# ---------------------------------------------------------------- 边界常量
# 集中定义，避免各方法各写一套魔数；改这里即可整体收紧/放宽。
MAX_NAME_LEN = 120          # 项目名 / 设定条目名
MAX_TITLE_LEN = 300         # 章节标题 / 事件标题
MAX_KEY_LEN = 80            # style key / config key
MAX_BRIEF_LEN = 10_000      # 本章细纲
MAX_CLAIM_LEN = 2_000       # 待核事实陈述
MAX_CITATION_CLAIM_LEN = 20_000   # 引证断言（可容纳整段待验文本）
MAX_TEXT_LEN = 200_000      # 章节正文（约合 20 万字符）
MAX_SEARCH_LEN = 120        # 检索关键词
DEFAULT_SEARCH_LIMIT = 50   # 检索默认返回条数
MAX_SEARCH_LIMIT = 500      # 检索返回条数上限
MAX_TIMELINE_ROWS = 1_000   # 时间线单次返回上限
MAX_DIFF_LINES = 400        # 版本对比输出上限
MIN_TOKEN_BUDGET = 500      # 输入包预算下界（防御 0 / 负数）
MAX_TOKEN_BUDGET = 200_000  # 输入包预算上界
MAX_CHAPTER_N = 100_000     # 章号上界（防御越界）
MAX_OUTLINE_ID_LEN = 80     # 大纲节点 id
MAX_OUTLINE_BODY_LEN = 20_000        # 总纲 / 卷纲正文
MAX_OUTLINE_SEQ = MAX_CHAPTER_N      # 卷序号上界，与章号同域
MAX_MATERIAL_LEN = 20_000   # 单条素材正文（摘抄、参考资料摘录）
MAX_SOURCE_LEN = 500        # 素材来源标识（路径 / URL / ima:// 定位串）
MAX_TAG_LEN = 40            # 单个素材标签
MAX_TAGS = 20               # 单条素材标签上限
MAX_SECTION_LEN = 50_000    # 单段正文上限（一章拆 4-8 段足够）
MAX_SUMMARY_LEN = 4_000     # 单章摘要上限（省 token 时用它替代整章正文）

# 硬不变量：章纲长度必须等于 MAX_BRIEF_LEN。
# 任何一条章纲都能原样传给 packet_assemble(brief=...)，
# 不会出现「库里有纲却因超长传不进去」的尴尬。改任一侧都要同步另一侧。
MAX_CHAPTER_OUTLINE_LEN = MAX_BRIEF_LEN

# ---------------------------------------------------------------- 枚举
# 库结构版本。新增表/索引时 +1，见 _init_schema()。
# v2：新增 outline_nodes（大纲三级：总纲 / 卷纲 / 章纲）。
# v3：新增 materials（素材库，带来源追溯）与 sections（逐段写作草稿）。
# v4：新增 summaries（章节摘要）与 story_state（故事状态），支撑省 token 输入包。
# v5：新增 expectations（预期账本），记录每章“满足 / 延迟 / 换维 / 违背”四态。
# v6：expectations 增加 data_json，保存承诺—触发—兑现及兑现证据；旧库显式补列。
# v7：新增 runs / run_steps（运行与检查点）、llm_calls（逐次成本账本）、
#     lint_findings（L0 确定性检查结果）。只加表，不影响既有数据。
# v8：新增 loop_runs / loop_metrics（质量环运行与逐维评分）、
#     style_profiles（文风蒸馏档案）。只加表，不影响既有数据。
# v9：新增 handoffs（AI Studio 交接单）。只加表，不影响既有数据。
# v10：新增 citations（引证与验真结果）。只加表，不影响既有数据。
SCHEMA_VERSION = 10

CHAPTER_STATUSES = ("draft", "revise", "approved", "blocked")
FORESHADOW_STATUSES = ("open", "resolved", "dropped")
FACT_STATUSES = ("pending", "verified", "disputed", "rejected")
PROJECT_KINDS = ("novel", "nonfiction", "script", "academic")
ENTITY_KINDS = ("character", "world", "org", "item", "rule")
EXPECTATION_STATES = ("meet", "delay", "dimension", "violate")

# 运行状态机：pending → running →（awaiting_human / blocked / done / failed / cancelled）。
# blocked = 确定性闸门拦截，需人工处理；awaiting_human = 主动等待人工签署。
# awaiting_input = 等待外部（AI Studio 对话）提交交接结果，不消耗模型调用。
RUN_STATUSES = ("pending", "running", "awaiting_input", "awaiting_human",
                "blocked", "done", "failed", "cancelled")
STEP_STATUSES = ("pending", "running", "done", "failed", "skipped", "blocked")
# AI Studio 交接单：which 环节需要外部生成，以及结果如何校验落库。
HANDOFF_KINDS = ("chapter_draft", "quality_critique", "quality_revise",
                 "style_distill", "project_design", "outline", "llm_generate",
                 "review")
# pending → submitted → applied；异常终结为 failed / rejected / cancelled。
HANDOFF_STATUSES = ("pending", "submitted", "applied", "failed", "rejected",
                    "cancelled")
# 质量环状态机：running → awaiting_input →（done / blocked / failed）。
# awaiting_input = studio 模式等待交接结果；blocked = 被 L0 确定性闸门拦下，
# 未消耗模型调用；failed = 模型未配置或调用失败。
LOOP_STATUSES = ("running", "awaiting_input", "done", "blocked", "failed")
# 终态步骤：只有这些状态才写 finished_at，便于计算耗时。
_TERMINAL_STEP_STATUSES = ("done", "failed", "blocked", "skipped")
# L0 检查结论：block 不可自动通过，warn 仅提示。
LINT_LEVELS = ("block", "warn")

# 大纲三级。master 唯一，volume 可选，chapter 绑定扁平章号。
OUTLINE_LEVELS = ("master", "volume", "chapter")
OUTLINE_STATUSES = CHAPTER_STATUSES          # 与章节状态机同域，便于统一判据
LEVEL_RANK = {"master": 0, "volume": 1, "chapter": 2}

# 素材种类。quote=原文摘抄，reference=参考资料，idea=灵感/待用点子，
# research=背景调研（区别于 facts 表：facts 是"待核事实"，有核查状态机）。
MATERIAL_KINDS = ("quote", "reference", "idea", "research")
# 素材来源种类，用于机器判断"这条能不能追溯到原始出处"。
MATERIAL_SOURCE_KINDS = ("file", "url", "ima", "book", "dialog", "other")

# 文档里的「地点」与 schema 枚举的 `world` 长期不一致，用别名收敛，
# 两种写法都能写、读出来统一为 world，不破坏任何已有调用。
_ENTITY_KIND_ALIASES = {
    "location": "world", "place": "world",
    "地点": "world", "世界观": "world", "世界": "world",
    "person": "character", "人物": "character", "角色": "character",
    "organization": "org", "组织": "org", "机构": "org",
    "artifact": "item", "object": "item", "物件": "item", "物品": "item",
    "规则": "rule",
}

# 设定条目的保留元数据键：直接住在 entities.data_json 里，避免为它们加表。
# 选择器（packet._select_entities）只认这几个键，其余字段原样保留。
ENTITY_PRIORITIES = ("always", "high", "normal")
ENTITY_META_KEYS = ("anchors", "priority", "content_hash",
                    "first_seen_chapter", "last_seen_chapter")
MAX_ENTITY_ANCHORS = 20
MAX_ANCHOR_LEN = 64

# 一个项目的全部从属表；overwrite 重建与 project_delete 都依赖它，
# 新增表时只需在这里登记，避免遗漏造成残留。
# 漏登记 = 重置/删除后大纲仍残留，且极难发现。
_CHILD_TABLES = ("entities", "events", "foreshadow", "chapters",
                 "style", "facts", "audits", "outline_nodes",
                 "materials", "sections", "summaries", "story_state",
                 "expectations", "runs", "run_steps", "llm_calls",
                 "lint_findings", "loop_runs", "loop_metrics",
                 "style_profiles", "handoffs", "citations")

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    kind        TEXT,
    config_json TEXT,
    created_at  REAL
);
CREATE TABLE IF NOT EXISTS entities (
    project    TEXT NOT NULL,
    id         TEXT NOT NULL,
    kind       TEXT,
    name       TEXT,
    data_json  TEXT,
    updated_at REAL,
    PRIMARY KEY (project, id)
);
CREATE TABLE IF NOT EXISTS events (
    project    TEXT NOT NULL,
    id         TEXT NOT NULL,
    chapter    INTEGER,
    seq        INTEGER,
    title      TEXT,
    data_json  TEXT,
    PRIMARY KEY (project, id)
);
CREATE TABLE IF NOT EXISTS foreshadow (
    project         TEXT NOT NULL,
    id              TEXT NOT NULL,
    text            TEXT,
    planted_chapter INTEGER,
    due_chapter     INTEGER,
    status          TEXT,
    resolved_chapter INTEGER,
    PRIMARY KEY (project, id)
);
CREATE TABLE IF NOT EXISTS chapters (
    project    TEXT NOT NULL,
    n          INTEGER NOT NULL,
    version    INTEGER NOT NULL,
    title      TEXT,
    content    TEXT,
    status     TEXT,
    data_json  TEXT,
    created_at REAL,
    PRIMARY KEY (project, n, version)
);
CREATE TABLE IF NOT EXISTS style (
    project    TEXT NOT NULL,
    key        TEXT NOT NULL,
    value_json TEXT,
    PRIMARY KEY (project, key)
);
CREATE TABLE IF NOT EXISTS facts (
    project    TEXT NOT NULL,
    id         TEXT NOT NULL,
    claim      TEXT,
    source     TEXT,
    status     TEXT,
    note       TEXT,
    chapter    INTEGER,
    PRIMARY KEY (project, id)
);
CREATE TABLE IF NOT EXISTS audits (
    project     TEXT NOT NULL,
    id          TEXT NOT NULL,
    chapter_n   INTEGER,
    report_json TEXT,
    created_at  REAL,
    PRIMARY KEY (project, id)
);
CREATE INDEX IF NOT EXISTS idx_events_chapter ON events(project, chapter);
CREATE INDEX IF NOT EXISTS idx_foreshadow_status ON foreshadow(project, status);
CREATE INDEX IF NOT EXISTS idx_facts_status ON facts(project, status);
CREATE INDEX IF NOT EXISTS idx_audits_chapter ON audits(project, chapter_n);

-- v2 新增：大纲三级（总纲 / 卷纲 / 章纲）。
-- 只新增表，不改动 v1 任何既有表，旧库无需迁移即可升级。
-- 注意：IF NOT EXISTS 意味着将来「加列」会静默失效——
-- 若需加列必须写显式 ALTER TABLE 并用 PRAGMA table_info 探测后再执行。
CREATE TABLE IF NOT EXISTS outline_nodes (
    project       TEXT    NOT NULL,
    id            TEXT    NOT NULL,          -- 稳定 id：master / v01 / c012
    version       INTEGER NOT NULL,          -- 同 id 每 set 一次 +1，旧版本永不覆盖
    level         TEXT    NOT NULL,          -- master | volume | chapter
    parent_id     TEXT,                      -- 上级节点 id；master 为 NULL
    seq           INTEGER NOT NULL DEFAULT 0,
    title         TEXT,
    chapter_n     INTEGER,                   -- level=chapter 时绑定扁平章号（与 chapters.n 同域）
    start_chapter INTEGER,                   -- level=volume 的章号区间（含）
    end_chapter   INTEGER,                   -- 同上（含）
    status        TEXT    NOT NULL DEFAULT 'draft',
    body          TEXT,
    data_json     TEXT,
    created_at    REAL,
    PRIMARY KEY (project, id, version)
);
CREATE INDEX IF NOT EXISTS idx_outline_level ON outline_nodes(project, level, seq);
CREATE INDEX IF NOT EXISTS idx_outline_chapter ON outline_nodes(project, chapter_n);
CREATE INDEX IF NOT EXISTS idx_outline_parent ON outline_nodes(project, parent_id);

-- v3 新增：素材库。与 facts 表的分工是：
-- facts = 写入正文前必须核实的断言（有 pending/verified 状态机）；
-- materials = 写作时参考的原材料（摘抄、资料、灵感），不进正文、只需可追溯。
CREATE TABLE IF NOT EXISTS materials (
    project    TEXT NOT NULL,
    id         TEXT NOT NULL,
    kind       TEXT NOT NULL,          -- quote | reference | idea | research
    title      TEXT,
    content    TEXT,
    source     TEXT,                   -- 来源标识：路径 / URL / ima://<kb>/<media>
    source_kind TEXT,                  -- file | url | ima | book | dialog | other
    tags_json  TEXT,                   -- 标签数组
    chapter    INTEGER,                -- 计划用于第几章（可空 = 未指派）
    data_json  TEXT,
    created_at REAL,
    PRIMARY KEY (project, id)
);
CREATE INDEX IF NOT EXISTS idx_materials_kind ON materials(project, kind);
CREATE INDEX IF NOT EXISTS idx_materials_chapter ON materials(project, chapter);

-- v3 新增：逐段写作草稿。一章拆成若干段分别落稿，段级留版本，
-- 最后由 section_merge 拼成整章交给 chapter_save。
-- 这样"某一段写崩了"只需重修那一段，不必整章重写。
CREATE TABLE IF NOT EXISTS sections (
    project    TEXT NOT NULL,
    chapter_n  INTEGER NOT NULL,
    idx        INTEGER NOT NULL,       -- 段序，从 1 开始
    version    INTEGER NOT NULL,
    title      TEXT,
    content    TEXT,
    status     TEXT,                   -- draft | revise | approved
    data_json  TEXT,
    created_at REAL,
    PRIMARY KEY (project, chapter_n, idx, version)
);
CREATE INDEX IF NOT EXISTS idx_sections_chapter ON sections(project, chapter_n, idx);

-- v4 新增：章节摘要。整章正文动辄数万字，长期写作只靠"前文回顾"必然爆预算；
-- 每章一条摘要（≤ MAX_SUMMARY_LEN），packet 优先用摘要替代整章正文。
CREATE TABLE IF NOT EXISTS summaries (
    project    TEXT NOT NULL,
    chapter_n  INTEGER NOT NULL,
    summary    TEXT,
    updated_at REAL,
    PRIMARY KEY (project, chapter_n)
);

-- v4 新增：故事状态（key → 任意 JSON）。对应 story-craft 的六类状态文件：
-- character-state / next-hook / used-tropes / established-facts 等，
-- 用一张键值表承接，不必为每类状态各建一张表。
CREATE TABLE IF NOT EXISTS story_state (
    project    TEXT NOT NULL,
    key        TEXT NOT NULL,
    value_json TEXT,
    updated_at REAL,
    PRIMARY KEY (project, key)
);

-- v5 新增：预期账本。每章记一条“读者预期发生了什么”，
-- 四态：meet 满足 / delay 延迟 / dimension 换维 / violate 违背。
-- 连续同态由 insights.expectation_stats 计算，不在表里冗余存。
CREATE TABLE IF NOT EXISTS expectations (
    project    TEXT NOT NULL,
    id         TEXT NOT NULL,
    chapter_n  INTEGER NOT NULL,
    state      TEXT NOT NULL,
    note       TEXT,
    data_json  TEXT,
    created_at REAL,
    PRIMARY KEY (project, id)
);
CREATE INDEX IF NOT EXISTS idx_expectations_chapter
    ON expectations(project, chapter_n, created_at);

-- v7 新增：运行实例（Run Engine 的持久化状态）。status 走 RUN_STATUSES，
-- policy/budget/target 存 JSON，恢复时无需再问调用方。
CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    project     TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'chapter',
    status      TEXT NOT NULL,
    policy_json TEXT,
    budget_json TEXT,
    target_json TEXT,
    error       TEXT,
    created_at  REAL,
    updated_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_runs_project_status ON runs(project, status);

-- v7 新增：运行步骤（检查点）。(run_id, seq) 幂等 upsert：
-- 已完成步骤重跑只更新状态，不重复插入，断点续跑才有意义。
CREATE TABLE IF NOT EXISTS run_steps (
    project     TEXT NOT NULL,
    run_id      TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    name        TEXT NOT NULL,
    status      TEXT NOT NULL,
    attempt     INTEGER NOT NULL DEFAULT 1,
    input_hash  TEXT,
    output_json TEXT,
    error       TEXT,
    started_at  REAL,
    finished_at REAL,
    PRIMARY KEY (run_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_run_steps_run ON run_steps(project, run_id);

-- v7 新增：逐次 LLM 调用账本。回答“这一章/这个模型/这一天花了多少”。
CREATE TABLE IF NOT EXISTS llm_calls (
    id            TEXT PRIMARY KEY,
    project       TEXT NOT NULL,
    run_id        TEXT,
    step_name     TEXT,
    chapter_n     INTEGER,
    provider      TEXT,
    model         TEXT,
    tier          TEXT,
    prompt_hash   TEXT,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    cost_estimate REAL NOT NULL DEFAULT 0,
    created_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_run
    ON llm_calls(project, run_id, chapter_n);

-- v7 新增：L0 确定性检查结果。每条必须带原文证据（evidence），
-- 否则视为不可验证；level=block 时不允许自动放行。
CREATE TABLE IF NOT EXISTS lint_findings (
    id          TEXT PRIMARY KEY,
    project     TEXT NOT NULL,
    chapter_n   INTEGER NOT NULL,
    run_id      TEXT,
    rule        TEXT NOT NULL,
    level       TEXT NOT NULL,
    evidence    TEXT,
    locator     TEXT,
    suggestion  TEXT,
    created_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_lint_findings_chapter
    ON lint_findings(project, chapter_n);

-- v8 新增：质量环运行。先 L0 再逐维评分 / 定向修复，policy 存轮数、阈值、
-- 维度与是否回写正文；best_score 是退出时的最佳分，exit_reason 说明为何停。
CREATE TABLE IF NOT EXISTS loop_runs (
    id          TEXT PRIMARY KEY,
    project     TEXT NOT NULL,
    chapter_n   INTEGER NOT NULL,
    status      TEXT NOT NULL,              -- running | done | blocked | failed
    policy_json TEXT,
    best_score  REAL,
    exit_reason TEXT,
    created_at  REAL,
    updated_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_loop_runs_project_chapter
    ON loop_runs(project, chapter_n);

-- v8 新增：逐轮逐维评分。一轮写 N 行（N = 维度数），overall 为该轮加权总分，
-- violations_json 保存该轮编辑意见，content_hash 用于识别正文是否变化。
CREATE TABLE IF NOT EXISTS loop_metrics (
    id             TEXT PRIMARY KEY,
    project        TEXT NOT NULL,
    loop_id        TEXT NOT NULL,
    round          INTEGER NOT NULL,
    dimension      TEXT NOT NULL,
    score          REAL NOT NULL,
    overall        REAL,
    violations_json TEXT,
    content_hash   TEXT,
    created_at     REAL
);
CREATE INDEX IF NOT EXISTS idx_loop_metrics_project_loop
    ON loop_metrics(project, loop_id);
CREATE INDEX IF NOT EXISTS idx_loop_metrics_project_created
    ON loop_metrics(project, created_at);

-- v8 新增：文风蒸馏档案。profile 是从样章提取的量化特征，
-- source 记录样章来源，便于回溯"这份档案是怎么来的"。
CREATE TABLE IF NOT EXISTS style_profiles (
    id           TEXT PRIMARY KEY,
    project      TEXT NOT NULL,
    name         TEXT NOT NULL,
    profile_json TEXT,
    source_json  TEXT,
    created_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_style_profiles_project
    ON style_profiles(project, created_at);

-- v9 新增：AI Studio 交接单。一个 LLM 环节被"交出去"生成时落一行，
-- 状态 pending → submitted → applied；失败/取消走 failed/rejected/cancelled。
-- result_hash 用于识别"同一结果重复提交"，保证幂等不重复写正文。
CREATE TABLE IF NOT EXISTS handoffs (
    id          TEXT PRIMARY KEY,
    project     TEXT NOT NULL,
    kind        TEXT NOT NULL,
    ref_id      TEXT,
    chapter_n   INTEGER,
    status      TEXT NOT NULL,
    prompt      TEXT NOT NULL,
    schema_json TEXT,
    result_json TEXT,
    result_hash TEXT,
    error       TEXT,
    created_at  REAL,
    updated_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_handoffs_project_status
    ON handoffs(project, status);
CREATE INDEX IF NOT EXISTS idx_handoffs_ref
    ON handoffs(ref_id);

-- v10 新增：引证。一条"正文里的断言 → 原始出处"的记录。
-- source 既可以是素材 id（mt_...）、章节定位（ch:3 / 纯数字），
-- 也可以直接是一段原始来源文本；verified/evidence 由 citations.verify 写回。
CREATE TABLE IF NOT EXISTS citations (
    id          TEXT PRIMARY KEY,
    project     TEXT NOT NULL,
    chapter_n   INTEGER,
    claim       TEXT NOT NULL,
    source      TEXT,
    locator     TEXT,
    verified    INTEGER NOT NULL DEFAULT 0,
    evidence    TEXT,
    created_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_citations_project_chapter
    ON citations(project, chapter_n);
CREATE INDEX IF NOT EXISTS idx_citations_project_verified
    ON citations(project, verified);
"""


# ---------------------------------------------------------------- 基础工具
def _now() -> float:
    return time.time()


_seq = itertools.count(1)


def _uid(prefix: str) -> str:
    """生成进程内唯一 ID。

    旧实现只取「毫秒 + pid」，同一毫秒内的连续调用会返回完全相同的字符串，
    而 events / foreshadow / facts / audits 都以它为主键，批量登记必然撞
    UNIQUE 约束。这里追加一个进程内单调递增的序号，保证唯一。
    """
    return f"{prefix}_{int(_now() * 1000)}_{os.getpid() % 10000}_{next(_seq):06d}"


def _j(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False)


def _dump(value: Any, field: str) -> str:
    """序列化为 JSON 文本；不可序列化时给出可读错误，而不是抛 TypeError。"""
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        raise LongformError(f"参数 `{field}` 无法序列化为 JSON：{e}") from None


def _hash_json(value: Any) -> str:
    """结果指纹：排序键 + 紧凑分隔符，保证"同内容同哈希"。

    用于交接单幂等判定——AI Studio 可能把同一份 JSON 原样提交两次，
    只有内容哈希一致才允许直接返回 already_applied。
    """
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_json(raw: Any, default: Any) -> Any:
    """宽容地反序列化 data_json / config_json 一类字段。

    历史数据可能是 NULL、空串或非对象结构，这里一律回落到 default，
    避免整表读取时因为一条脏数据崩溃。
    """
    if raw is None or raw == "":
        return default
    try:
        val = json.loads(raw)
    except (TypeError, ValueError):
        return default
    if default is None:
        # 只想"能解析就行"，不做类型约束（如 bible_get 返回 {} 是合法的）。
        return val
    return val if type(val) is type(default) else default


_UNSAFE_ID = re.compile(r"[\x00-\x1f\x7f/\\]+")


def _slugify(name: str) -> str:
    """由项目名派生 id。

    与旧实现的唯一差异是清理控制字符与路径分隔符；普通项目名的结果
    与旧实现逐字一致，因此不会打断已有调用。
    """
    s = _UNSAFE_ID.sub("_", name.strip()).replace(" ", "_")
    s = s.lstrip(".")
    return s[:MAX_NAME_LEN] or "untitled"


# ---------------------------------------------------------------- 错误与校验
class LongformError(ValueError):
    """面向调用方（模型）的可读错误。

    会被 server 层统一转成 MCP 的 ``isError: true`` 响应，
    消息里带上出错的参数名与允许的取值范围。
    """


def _require_str(value: Any, field: str, *, allow_empty: bool = False,
                 max_len: int = MAX_NAME_LEN) -> str:
    if not isinstance(value, str):
        raise LongformError(
            f"参数 `{field}` 必须是字符串，实际收到 {type(value).__name__}：{value!r}")
    if len(value) > max_len:
        raise LongformError(
            f"参数 `{field}` 过长：{len(value)} 字符，上限 {max_len}")
    if not allow_empty and not value.strip():
        raise LongformError(f"参数 `{field}` 不能为空")
    return value


def _require_int(value: Any, field: str, *, minimum: Optional[int] = None,
                 maximum: Optional[int] = None) -> int:
    # bool 是 int 的子类，但把 True 当成章号显然是调用方的错误。
    if isinstance(value, bool) or not isinstance(value, int):
        raise LongformError(
            f"参数 `{field}` 必须是整数，实际收到 {type(value).__name__}：{value!r}")
    if minimum is not None and value < minimum:
        raise LongformError(f"参数 `{field}` 不能小于 {minimum}，实际收到 {value}")
    if maximum is not None and value > maximum:
        raise LongformError(f"参数 `{field}` 不能大于 {maximum}，实际收到 {value}")
    return value


def _require_enum(value: Any, field: str, allowed: Iterable[str]) -> str:
    allowed = tuple(allowed)
    if not isinstance(value, str) or value not in allowed:
        raise LongformError(
            f"参数 `{field}` 取值非法：{value!r}，允许的值为 {list(allowed)}")
    return value


def _require_float(value: Any, field: str, *, minimum: float = 0.0) -> float:
    """弹性数值校验：接受 int / float（拒绝 bool），并守住下界。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LongformError(
            f"参数 `{field}` 必须是数字，实际收到 {type(value).__name__}：{value!r}")
    value = float(value)
    if value < minimum:
        raise LongformError(f"参数 `{field}` 不能小于 {minimum}，实际收到 {value}")
    return value


def _require_dict(value: Any, field: str) -> dict:
    if not isinstance(value, dict):
        raise LongformError(
            f"参数 `{field}` 必须是对象，实际收到 {type(value).__name__}：{value!r}")
    return value


def _require_score(value: Any, field: str) -> float:
    """质量环分数：0–10 的数字。越界直接拒绝，避免脏分污染聚合。"""
    v = _require_float(value, field, minimum=0.0)
    if v > 10.0:
        raise LongformError(f"参数 `{field}` 不能大于 10，实际收到 {v}")
    return v


def _as_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    """宽容的整型解析，用于读取配置文件里的值。

    配置是手写 YAML，出现 ``"5"`` 或写坏的值不应当让服务崩溃，
    越界或非法时静默回落到 default。
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, str):
        try:
            value = int(value.strip())
        except ValueError:
            return default
    if not isinstance(value, int):
        return default
    return max(minimum, min(maximum, value))


def _entity_row(row: sqlite3.Row) -> Dict[str, Any]:
    """把 entities 行摊平成 ``{id, kind, name, ...data}``。

    身份字段放在最后赋值，防止 data 里恰好有同名键把 id / kind / name 顶掉。
    """
    data = _load_json(row["data_json"], {})
    out: Dict[str, Any] = {k: v for k, v in data.items()
                           if k not in ("id", "kind", "name")}
    out["id"] = row["id"]
    out["kind"] = row["kind"]
    out["name"] = row["name"]
    return out


def _entity_content_hash(name: str, data: dict) -> str:
    """设定条目的内容指纹：由 name + data 算出，写 meta 时刷新。

    ``content_hash`` 自身不参与计算，否则每写一次都会把上一次的哈希算进去，
    指纹将永远不稳定。
    """
    payload = {k: v for k, v in (data or {}).items() if k != "content_hash"}
    raw = json.dumps({"name": name, "data": payload},
                     sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _entity_meta_view(name: str, data: dict) -> Dict[str, Any]:
    """把 data_json 里分散的元数据收敛成固定五键视图。"""
    data = data if isinstance(data, dict) else {}
    priority = data.get("priority")
    if priority not in ENTITY_PRIORITIES:
        priority = "normal"
    anchors = data.get("anchors")
    if not isinstance(anchors, list):
        anchors = []
    first = data.get("first_seen_chapter")
    last = data.get("last_seen_chapter")
    return {
        "anchors": [a for a in anchors if isinstance(a, str)],
        "priority": priority,
        "content_hash": (data.get("content_hash")
                         or _entity_content_hash(name, data)),
        "first_seen_chapter": first if isinstance(first, int) else 0,
        "last_seen_chapter": last if isinstance(last, int) else 0,
    }


def _handoff_row(row: sqlite3.Row) -> Dict[str, Any]:
    """把 handoffs 行转成对外字典：schema/result 解析回对象。"""
    d = dict(row)
    d["schema"] = _load_json(d.pop("schema_json", None), None)
    d["result"] = _load_json(d.pop("result_json", None), None)
    return d


# ---------------------------------------------------------------- Store
class Store:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = self._connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()
        # FTS5 是可选能力：探测失败只记 False，由 kb 层回退 LIKE，不影响既有功能。
        self.fts_available = self._setup_fts()

    def _init_schema(self):
        """只在首次（或升级后）执行 DDL。

        ``CREATE TABLE IF NOT EXISTS`` 虽然幂等，但每次启动都要重跑十几条
        DDL 并校验索引，实测占 ~1s 启动开销；用 ``user_version`` 做闸门后，
        常规启动退化为一次整数读取。
        """
        try:
            current = self.conn.execute("PRAGMA user_version").fetchone()[0]
        except sqlite3.DatabaseError:
            current = 0
        if current >= SCHEMA_VERSION:
            return
        self.conn.executescript(SCHEMA)
        if current < 6:
            columns = {row[1] for row in self.conn.execute(
                "PRAGMA table_info(expectations)").fetchall()}
            if "data_json" not in columns:
                self.conn.execute("ALTER TABLE expectations ADD COLUMN data_json TEXT")
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _setup_fts(self) -> bool:
        """尝试建立素材全文索引（FTS5 + trigram 分词）。

        materials 是普通 rowid 表，因此可以走 FTS5 的「外部内容」模式：
        索引只存倒排，正文仍只有 materials 一份，不产生双写不一致。
        三个触发器负责增量同步；建表后 rebuild 一次回填历史素材。

        任何一步失败（老版本 SQLite 没有 FTS5 / trigram）都整体回退到
        ``LIKE`` 检索，绝不因为可选能力缺失而让 Store 起不来。
        """
        try:
            self.conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS materials_fts USING fts5("
                "title, content, source, content='materials', "
                "content_rowid='rowid', tokenize='trigram')")
            self.conn.execute(
                "CREATE TRIGGER IF NOT EXISTS materials_ai AFTER INSERT ON materials BEGIN "
                "INSERT INTO materials_fts(rowid, title, content, source) "
                "VALUES (new.rowid, new.title, new.content, new.source); END")
            self.conn.execute(
                "CREATE TRIGGER IF NOT EXISTS materials_ad AFTER DELETE ON materials BEGIN "
                "INSERT INTO materials_fts(materials_fts, rowid, title, content, source) "
                "VALUES ('delete', old.rowid, old.title, old.content, old.source); END")
            self.conn.execute(
                "CREATE TRIGGER IF NOT EXISTS materials_au AFTER UPDATE ON materials BEGIN "
                "INSERT INTO materials_fts(materials_fts, rowid, title, content, source) "
                "VALUES ('delete', old.rowid, old.title, old.content, old.source); "
                "INSERT INTO materials_fts(rowid, title, content, source) "
                "VALUES (new.rowid, new.title, new.content, new.source); END")
            self.conn.execute("INSERT INTO materials_fts(materials_fts) VALUES('rebuild')")
            return True
        except sqlite3.OperationalError:
            for sql in ("DROP TRIGGER IF EXISTS materials_ai",
                        "DROP TRIGGER IF EXISTS materials_ad",
                        "DROP TRIGGER IF EXISTS materials_au",
                        "DROP TABLE IF EXISTS materials_fts"):
                try:
                    self.conn.execute(sql)
                except sqlite3.Error:
                    pass
            return False

    # ---------------- 连接与事务 ----------------
    def _connect(self, db_path: str) -> sqlite3.Connection:
        """建立连接；落盘失败时降级到临时目录。

        旧实现只捕获 sqlite3.OperationalError，而最常见的失败其实是
        ``os.makedirs`` 抛出的 OSError（无权限 / 只读盘），
        导致所谓"降级保护"在最需要它的场景下完全失效。
        """
        try:
            os.makedirs(os.path.dirname(os.path.abspath(db_path)) or ".", exist_ok=True)
            conn = sqlite3.connect(db_path, isolation_level=None)
            conn.execute("PRAGMA busy_timeout=5000")
            self._probe(conn)
            return conn
        except (sqlite3.Error, OSError) as e:
            fb = os.path.join(tempfile.gettempdir(), "longform_writing")
            os.makedirs(fb, exist_ok=True)
            self.db_path = os.path.join(fb, "longform.db")
            sys.stderr.write(
                f"[warn] 无法写入 {db_path}（{type(e).__name__}: {e}），"
                f"已降级到 {self.db_path}，本次数据不会持久化到原路径\n")
            conn = sqlite3.connect(self.db_path, isolation_level=None)
            conn.execute("PRAGMA busy_timeout=5000")
            return conn

    def _probe(self, conn: sqlite3.Connection):
        conn.execute("CREATE TABLE IF NOT EXISTS _probe(a)")
        conn.execute("DROP TABLE _probe")

    def _tx(self):
        """显式事务；BEGIN IMMEDIATE 立即取写锁，避免读-改-写竞态。"""
        return _Transaction(self.conn)

    # ---------------- projects ----------------
    def project_init(self, name: str, kind: str = "novel", config: Optional[dict] = None,
                     overwrite: bool = False, profile: Optional[str] = None) -> dict:
        """创建/重置项目；可选绑定一个作品变体。

        变体名落在 ``config_json["profile"]`` 里，不新增列——
        旧库无需迁移就能带上这项能力，``project_export`` 也天然能看到它。
        """
        _require_str(name, "name", max_len=MAX_NAME_LEN)
        _require_enum(kind, "kind", PROJECT_KINDS)
        if config is not None:
            _require_dict(config, "config")

        cfg = dict(config) if config else {}
        if profile is not None:
            # 只校验类型与长度；变体是否真实存在由 server 层判定，
            # 数据层保持"不认识 profile 这个概念"的纯粹性。
            profile = _require_str(profile, "profile", allow_empty=True,
                                   max_len=MAX_KEY_LEN).strip()
            if profile:
                cfg["profile"] = profile
            else:
                cfg.pop("profile", None)

        pid = _slugify(name)
        with self._tx():
            exists = self.conn.execute(
                "SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone()
            if exists and not overwrite:
                return {"ok": False,
                        "error": f"项目已存在：{pid}（如需覆盖请 overwrite=true）",
                        "id": pid}
            if exists:
                # 真正的重置：连同所有从属数据一起清掉，
                # 否则"重置"后旧设定仍会被 packet_assemble 读出来。
                for table in _CHILD_TABLES:
                    self.conn.execute(f"DELETE FROM {table} WHERE project=?", (pid,))
            self.conn.execute(
                "INSERT OR REPLACE INTO projects(id,name,kind,config_json,created_at)"
                " VALUES(?,?,?,?,?)",
                (pid, name, kind, _j(cfg), _now()))
        return {"ok": True, "id": pid, "name": name, "kind": kind,
                "profile": cfg.get("profile") or None}

    def project_get_profile(self, pid: str) -> Optional[str]:
        """读取项目绑定的变体名；未绑定或项目不存在时返回 None。

        读操作保持宽容：变体文件夹可能已经被删掉了，那也不该让读配置失败，
        真正的降级处理在 server 层（回退为项目自身配置并给出提示）。
        """
        p = self.project_get(pid)
        if not p:
            return None
        cfg = _load_json(p.get("config_json"), {})
        v = cfg.get("profile") if isinstance(cfg, dict) else None
        return v if isinstance(v, str) and v.strip() else None

    def project_set_profile(self, pid: str, name: str) -> dict:
        """绑定 / 解绑变体；``name`` 为空串表示解绑。

        这是"后期微调"的入口：项目写到一半才决定换写作方向或转成二创时，
        不需要重建项目，换一个变体即可。
        """
        self._require_project(pid)
        _require_str(name, "profile", allow_empty=True, max_len=MAX_KEY_LEN)
        name = name.strip()
        with self._tx():
            p = self.project_get(pid)
            cfg = _load_json(p.get("config_json"), {})
            if not isinstance(cfg, dict):
                cfg = {}
            if name:
                cfg["profile"] = name
            else:
                cfg.pop("profile", None)
            self.conn.execute("UPDATE projects SET config_json=? WHERE id=?",
                              (_dump(cfg, "config"), pid))
        return {"ok": True, "project": pid, "profile": cfg.get("profile") or None}

    def project_list(self) -> List[dict]:
        # 旧实现是 N+1 查询（每个项目再查一次 COUNT），改为单次聚合。
        rows = self.conn.execute(
            "SELECT p.id, p.name, p.kind, COUNT(DISTINCT c.n) AS chapters "
            "FROM projects p LEFT JOIN chapters c ON c.project = p.id "
            "GROUP BY p.id, p.name, p.kind "
            "ORDER BY p.created_at, p.id").fetchall()
        return [{"id": r["id"], "name": r["name"], "kind": r["kind"],
                 "chapters": r["chapters"]} for r in rows]

    def project_get(self, pid: str) -> Optional[dict]:
        _require_str(pid, "project")
        r = self.conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        return dict(r) if r else None

    def project_delete(self, pid: str, confirm: Optional[str] = None) -> dict:
        """彻底删除一个项目及其全部从属数据。

        破坏性操作，要求 ``confirm`` 与项目 id 逐字相同，
        防止模型误传一个近似名字就把整部作品删掉。
        """
        _require_str(pid, "project")
        if confirm != pid:
            raise LongformError(
                f"删除项目需要显式确认：请传 confirm=\"{pid}\"（必须与项目 id 完全一致）")
        if self.project_get(pid) is None:
            raise LongformError(f"项目不存在：{pid}")
        deleted = {}
        with self._tx():
            for table in _CHILD_TABLES:
                cur = self.conn.execute(f"DELETE FROM {table} WHERE project=?", (pid,))
                deleted[table] = cur.rowcount
            self.conn.execute("DELETE FROM projects WHERE id=?", (pid,))
        return {"ok": True, "project": pid, "deleted": deleted}

    def _require_project(self, pid: str) -> None:
        """写操作的前置校验：项目必须已存在。

        放任写操作会产生永远无法通过 project_list 发现的孤儿数据。
        读操作保持宽容（返回空），不在此收紧。
        """
        _require_str(pid, "project")
        if self.conn.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone() is None:
            raise LongformError(f"项目不存在：{pid}（请先调用 project_init）")

    # ---------------- entities (设定档) ----------------
    def bible_upsert(self, pid: str, kind: str, name: str, data: dict) -> dict:
        self._require_project(pid)
        _require_str(kind, "kind", max_len=MAX_KEY_LEN)
        _require_str(name, "name", max_len=MAX_NAME_LEN)
        _require_dict(data, "data")     # 从源头杜绝非对象 data 落盘

        kind = _ENTITY_KIND_ALIASES.get(kind, kind)
        eid = f"{kind}:{name}"
        with self._tx():
            self.conn.execute(
                "INSERT OR REPLACE INTO entities(project,id,kind,name,data_json,updated_at)"
                " VALUES(?,?,?,?,?,?)",
                (pid, eid, kind, name, _dump(data, "data"), _now()))
        return {"ok": True, "id": eid, "kind": kind, "name": name}

    def bible_get(self, pid: str, kind: str, name: str) -> Optional[dict]:
        self._require_project(pid)
        _require_str(kind, "kind", max_len=MAX_KEY_LEN)
        _require_str(name, "name", max_len=MAX_NAME_LEN)
        kind = _ENTITY_KIND_ALIASES.get(kind, kind)
        r = self.conn.execute(
            "SELECT * FROM entities WHERE project=? AND id=?", (pid, f"{kind}:{name}")
        ).fetchone()
        return _load_json(r["data_json"], None) if r else None

    def bible_list(self, pid: str, kind: Optional[str] = None) -> List[dict]:
        self._require_project(pid)
        if kind is not None:
            _require_str(kind, "kind", max_len=MAX_KEY_LEN)
            kind = _ENTITY_KIND_ALIASES.get(kind, kind)
            rows = self.conn.execute(
                "SELECT * FROM entities WHERE project=? AND kind=? ORDER BY name",
                (pid, kind)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM entities WHERE project=? ORDER BY kind,name",
                (pid,)).fetchall()
        return [_entity_row(r) for r in rows]

    def bible_search(self, pid: str, query: str, limit: Optional[int] = None) -> List[dict]:
        self._require_project(pid)
        _require_str(query, "query", max_len=MAX_SEARCH_LEN)
        lim = _as_int(limit, DEFAULT_SEARCH_LIMIT,
                      minimum=1, maximum=MAX_SEARCH_LIMIT)

        # LIKE 的 % 与 _ 是通配符，不转义的话一次 query="%" 就会命中全表。
        esc = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{esc}%"
        rows = self.conn.execute(
            "SELECT * FROM entities WHERE project=? AND "
            "(name LIKE ? ESCAPE '\\' OR data_json LIKE ? ESCAPE '\\') "
            "ORDER BY kind,name LIMIT ?", (pid, like, like, lim)).fetchall()
        return [_entity_row(r) for r in rows]

    # ---------------- entity meta（世界书激活元数据） ----------------
    def _entity_data_or_raise(self, pid: str, kind: str, name: str) -> dict:
        data = self.bible_get(pid, kind, name)
        if data is None:
            raise LongformError(f"设定条目不存在：{kind}:{name}"
                                "（请先调用 bible_upsert 写入）")
        return data if isinstance(data, dict) else {}

    def entity_meta_get(self, pid: str, kind: str, name: str) -> dict:
        """读取一条设定的激活元数据（不存在时报错，而不是返回空壳）。"""
        data = self._entity_data_or_raise(pid, kind, name)
        return _entity_meta_view(name, data)

    def entity_meta_set(self, pid: str, kind: str, name: str, *,
                        anchors: Optional[List[str]] = None,
                        priority: Optional[str] = None,
                        first_seen_chapter: Optional[int] = None) -> dict:
        """写入激活元数据；未知键原样保留，content_hash 每次刷新。"""
        data = self._entity_data_or_raise(pid, kind, name)

        if anchors is not None:
            if not isinstance(anchors, list):
                raise LongformError("anchors 必须是字符串列表")
            cleaned: List[str] = []
            seen = set()
            for a in anchors:
                if not isinstance(a, str):
                    raise LongformError("anchors 必须是字符串列表")
                a = a.strip()
                if not a:
                    continue
                if len(a) > MAX_ANCHOR_LEN:
                    raise LongformError(
                        f"anchors 单项不得超过 {MAX_ANCHOR_LEN} 字符：{a[:20]}…")
                if a not in seen:
                    seen.add(a)
                    cleaned.append(a)
            if len(cleaned) > MAX_ENTITY_ANCHORS:
                raise LongformError(
                    f"anchors 最多 {MAX_ENTITY_ANCHORS} 条，实际 {len(cleaned)} 条")
            data["anchors"] = cleaned

        if priority is not None:
            if priority not in ENTITY_PRIORITIES:
                raise LongformError(
                    f"priority 必须是 {'/'.join(ENTITY_PRIORITIES)} 之一")
            data["priority"] = priority

        if first_seen_chapter is not None:
            data["first_seen_chapter"] = _require_int(
                first_seen_chapter, "first_seen_chapter",
                minimum=1, maximum=MAX_CHAPTER_N)

        data["content_hash"] = _entity_content_hash(name, data)
        self.bible_upsert(pid, kind, name, data)
        return _entity_meta_view(name, data)

    def entity_touch(self, pid: str, kind: str, name: str, chapter: int) -> dict:
        """记录「本章又用到了它」；只推进 last_seen，不覆盖其他键。"""
        data = self._entity_data_or_raise(pid, kind, name)
        chapter = _require_int(chapter, "chapter", minimum=1,
                               maximum=MAX_CHAPTER_N)
        old = data.get("last_seen_chapter")
        old = old if isinstance(old, int) and not isinstance(old, bool) else 0
        data["last_seen_chapter"] = max(old, chapter)
        data["content_hash"] = _entity_content_hash(name, data)
        self.bible_upsert(pid, kind, name, data)
        return _entity_meta_view(name, data)

    # ---------------- events (时间线) ----------------
    def timeline_add(self, pid: str, title: str, chapter: int, seq: int = 0,
                     data: Optional[dict] = None) -> dict:
        self._require_project(pid)
        _require_str(title, "title", max_len=MAX_TITLE_LEN)
        chapter = _require_int(chapter, "chapter", minimum=0, maximum=MAX_CHAPTER_N)
        seq = _require_int(seq, "seq", minimum=0, maximum=MAX_CHAPTER_N)
        if data is not None:
            _require_dict(data, "data")

        eid = _uid("ev")
        with self._tx():
            self.conn.execute(
                "INSERT INTO events(project,id,chapter,seq,title,data_json) VALUES(?,?,?,?,?,?)",
                (pid, eid, chapter, seq, title, _dump(data or {}, "data")))
        return {"ok": True, "id": eid}

    def timeline_query(self, pid: str, from_chapter: int = 0,
                       to_chapter: int = MAX_CHAPTER_N,
                       limit: Optional[int] = None) -> List[dict]:
        self._require_project(pid)
        from_chapter = _require_int(from_chapter, "from_chapter",
                                    minimum=0, maximum=MAX_CHAPTER_N)
        to_chapter = _require_int(to_chapter, "to_chapter",
                                  minimum=0, maximum=MAX_CHAPTER_N)
        # 旧实现在参数传反时静默返回空表，调用方无从察觉。
        if from_chapter > to_chapter:
            raise LongformError(
                f"from_chapter({from_chapter}) 不能大于 to_chapter({to_chapter})")
        lim = _as_int(limit, MAX_TIMELINE_ROWS, minimum=1, maximum=MAX_TIMELINE_ROWS)

        rows = self.conn.execute(
            "SELECT * FROM events WHERE project=? AND chapter BETWEEN ? AND ? "
            "ORDER BY chapter,seq LIMIT ?",
            (pid, from_chapter, to_chapter, lim)).fetchall()
        out = []
        for r in rows:
            item = {"id": r["id"], "chapter": r["chapter"], "seq": r["seq"],
                    "title": r["title"]}
            item.update(_load_json(r["data_json"], {}))
            out.append(item)
        return out

    # ---------------- foreshadow (伏笔台账) ----------------
    def foreshadow_add(self, pid: str, text: str, planted_chapter: int,
                       due_chapter: Optional[int] = None) -> dict:
        self._require_project(pid)
        _require_str(text, "text", max_len=MAX_CLAIM_LEN)
        planted = _require_int(planted_chapter, "planted_chapter",
                               minimum=0, maximum=MAX_CHAPTER_N)
        if due_chapter is not None:
            due = _require_int(due_chapter, "due_chapter",
                               minimum=0, maximum=MAX_CHAPTER_N)
            if due < planted:
                raise LongformError(
                    f"due_chapter({due}) 不能早于 planted_chapter({planted})")
        fid = _uid("fs")
        with self._tx():
            self.conn.execute(
                "INSERT INTO foreshadow(project,id,text,planted_chapter,due_chapter,"
                                 "status,resolved_chapter)"
                " VALUES(?,?,?,?,?,?,?)",
                (pid, fid, text, planted, due_chapter, "open", None))
        return {"ok": True, "id": fid, "status": "open"}

    def foreshadow_list(self, pid: str, status: Optional[str] = None) -> List[dict]:
        self._require_project(pid)
        if status is not None:
            _require_enum(status, "status", FORESHADOW_STATUSES)
            rows = self.conn.execute(
                "SELECT * FROM foreshadow WHERE project=? AND status=? "
                "ORDER BY planted_chapter", (pid, status)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM foreshadow WHERE project=? ORDER BY planted_chapter",
                (pid,)).fetchall()
        return [dict(r) for r in rows]

    def foreshadow_resolve(self, pid: str, fid: str, chapter: int,
                           status: str = "resolved") -> dict:
        self._require_project(pid)
        _require_str(fid, "id", max_len=2 * MAX_KEY_LEN)
        chapter = _require_int(chapter, "chapter", minimum=0, maximum=MAX_CHAPTER_N)
        # 无白名单时，"resolvd" 这类拼写错误会被写进状态机，
        # 之后 resolved 查询与逾期检测双双失联。
        _require_enum(status, "status", FORESHADOW_STATUSES)

        with self._tx():
            cur = self.conn.execute(
                "UPDATE foreshadow SET status=?, resolved_chapter=? "
                "WHERE project=? AND id=?", (status, chapter, pid, fid))
            # 旧实现不看 rowcount，对不存在的 id 也返回 ok:true。
            if cur.rowcount == 0:
                raise LongformError(f"伏笔不存在：{fid}（项目 {pid}）")
        return {"ok": True, "id": fid, "status": status, "chapter": chapter}

    def foreshadow_overdue(self, pid: str, current: int, after: int = 8) -> List[dict]:
        self._require_project(pid)
        current = _require_int(current, "current", minimum=0, maximum=MAX_CHAPTER_N)
        after = _require_int(after, "after", minimum=0, maximum=MAX_CHAPTER_N)
        # 除了"埋下超过 N 章"，显式 due_chapter 已过也应算逾期——
        # due_chapter 字段本来就是为此存在的。
        rows = self.conn.execute(
            "SELECT * FROM foreshadow WHERE project=? AND status='open' AND "
            "(? - planted_chapter >= ? OR (due_chapter IS NOT NULL AND ? > due_chapter)) "
            "ORDER BY planted_chapter", (pid, current, after, current)).fetchall()
        return [dict(r) for r in rows]

    # ---------------- chapters ----------------
    def chapter_save(self, pid: str, n: int, title: str, content: str,
                     status: str = "draft", data: Optional[dict] = None) -> dict:
        self._require_project(pid)
        n = _require_int(n, "n", minimum=0, maximum=MAX_CHAPTER_N)
        _require_str(title, "title", max_len=MAX_TITLE_LEN)
        _require_str(content, "content", max_len=MAX_TEXT_LEN)
        _require_enum(status, "status", CHAPTER_STATUSES)
        if data is not None:
            _require_dict(data, "data")

        # 读 MAX(version) 再插入必须落在同一事务里，否则并发写入会撞主键。
        with self._tx():
            cur = self.conn.execute(
                "SELECT MAX(version) v FROM chapters WHERE project=? AND n=?",
                (pid, n)).fetchone()
            version = (cur["v"] or 0) + 1
            self.conn.execute(
                "INSERT INTO chapters(project,n,version,title,content,status,data_json,created_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (pid, n, version, title, content, status,
                 _dump(data or {}, "data"), _now()))
        return {"ok": True, "n": n, "version": version, "status": status,
                "words": len(content)}

    def chapter_get(self, pid: str, n: int, version: Optional[int] = None) -> Optional[dict]:
        self._require_project(pid)
        n = _require_int(n, "n", minimum=0, maximum=MAX_CHAPTER_N)
        if version is not None:
            version = _require_int(version, "version", minimum=1, maximum=MAX_CHAPTER_N)
            r = self.conn.execute(
                "SELECT * FROM chapters WHERE project=? AND n=? AND version=?",
                (pid, n, version)).fetchone()
        else:
            r = self.conn.execute(
                "SELECT * FROM chapters WHERE project=? AND n=? "
                "ORDER BY version DESC LIMIT 1", (pid, n)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["data_json"] = _load_json(d["data_json"], {})
        d["words"] = len(d["content"] or "")
        return d

    def chapter_list(self, pid: str) -> List[dict]:
        self._require_project(pid)
        # 旧实现同时用了 MAX(version) 与 MAX(created_at) 两个聚合，
        # 此时裸列 title / status 取自哪一行在 SQLite 中是未定义的；
        # 改用相关子查询，明确锁定"版本最大的那一行"。
        rows = self.conn.execute(
            "SELECT n, version AS latest_version, title, status, created_at "
            "FROM chapters c WHERE project=? AND version = "
            "  (SELECT MAX(c2.version) FROM chapters c2 "
            "   WHERE c2.project = c.project AND c2.n = c.n) "
            "ORDER BY n", (pid,)).fetchall()
        return [{"n": r["n"], "latest_version": r["latest_version"],
                 "title": r["title"], "status": r["status"]} for r in rows]

    def chapter_set_status(self, pid: str, n: int, status: str,
                           version: Optional[int] = None) -> dict:
        """只改某一版章节的状态，不写正文、不新增版本。

        流水线里“打回重写 / 定稿通过”只需要动状态机；若走 chapter_save
        会平白多出一个内容相同的版本，既污染历史又浪费磁盘。
        ``version`` 省略时取该章最新版。
        """
        self._require_project(pid)
        n = _require_int(n, "n", minimum=0, maximum=MAX_CHAPTER_N)
        status = _require_enum(status, "status", CHAPTER_STATUSES)
        if version is None:
            row = self.conn.execute(
                "SELECT MAX(version) v FROM chapters WHERE project=? AND n=?",
                (pid, n)).fetchone()
            version = row["v"] if row else None
            if version is None:
                raise LongformError(f"第 {n} 章还没有任何版本，无法设置状态")
        else:
            version = _require_int(version, "version", minimum=1,
                                   maximum=MAX_CHAPTER_N)
        with self._tx():
            cur = self.conn.execute(
                "UPDATE chapters SET status=? WHERE project=? AND n=? AND version=?",
                (status, pid, n, version))
            if cur.rowcount == 0:
                raise LongformError(f"第 {n} 章没有版本 v{version}（项目 {pid}）")
        return {"ok": True, "n": n, "version": version, "status": status}

    def chapter_versions(self, pid: str, n: int) -> List[dict]:
        self._require_project(pid)
        n = _require_int(n, "n", minimum=0, maximum=MAX_CHAPTER_N)
        rows = self.conn.execute(
            "SELECT version,status,created_at,LENGTH(content) words FROM chapters "
            "WHERE project=? AND n=? ORDER BY version", (pid, n)).fetchall()
        return [dict(r) for r in rows]

    def chapter_diff(self, pid: str, n: int, a: Optional[int] = None,
                     b: Optional[int] = None) -> dict:
        """对比同一章两个版本的正文差异，支撑「定点修订」。"""
        self._require_project(pid)
        n = _require_int(n, "n", minimum=0, maximum=MAX_CHAPTER_N)
        versions = [r["version"] for r in self.chapter_versions(pid, n)]
        if not versions:
            raise LongformError(f"第 {n} 章还没有任何版本，无法对比")
        if a is None and b is None:
            if len(versions) < 2:
                raise LongformError(
                    f"第 {n} 章只有 {len(versions)} 个版本，无法自动对比；"
                    f"请显式指定 a / b")
            a, b = versions[0], versions[-1]
        else:
            a = versions[0] if a is None else _require_int(
            a, "a", minimum=1, maximum=MAX_CHAPTER_N)
            b = versions[-1] if b is None else _require_int(
            b, "b", minimum=1, maximum=MAX_CHAPTER_N)

        ca = self.chapter_get(pid, n, a)
        cb = self.chapter_get(pid, n, b)
        if ca is None:
            raise LongformError(f"第 {n} 章没有版本 v{a}")
        if cb is None:
            raise LongformError(f"第 {n} 章没有版本 v{b}")

        alines = (ca["content"] or "").splitlines()
        blines = (cb["content"] or "").splitlines()
        full = list(difflib.unified_diff(
            alines, blines, fromfile=f"第{n}章 v{a}", tofile=f"第{n}章 v{b}",
            lineterm=""))
        truncated = len(full) > MAX_DIFF_LINES
        kept = full[:MAX_DIFF_LINES]
        if truncated:
            kept.append(f"...[差异超过 {MAX_DIFF_LINES} 行，已截断]")
        return {
            "ok": True, "n": n, "from_version": a, "to_version": b,
            "added_lines": sum(1 for d in full if d.startswith("+")
                               and not d.startswith("+++")),
            "removed_lines": sum(1 for d in full if d.startswith("-")
                                 and not d.startswith("---")),
            "truncated": truncated,
            "diff": "\n".join(kept),
        }

    # ---------------- outline (大纲三级：总纲 / 卷纲 / 章纲) ----------------
    # 大纲本体此前完全不落库，只活在对话里——换个会话就没了。
    # 一张表存三级：master 唯一；volume = 记录 + 章号区间；chapter 绑定扁平章号 n。
    # 同 id 每 set 一次即 +1 版本，旧版本永不覆盖（大纲是假设，不是契约）。
    def _outline_latest(self, pid: str, node_id: str) -> Optional[dict]:
        r = self.conn.execute(
            "SELECT * FROM outline_nodes WHERE project=? AND id=? "
            "ORDER BY version DESC LIMIT 1", (pid, node_id)).fetchone()
        return dict(r) if r else None

    def _outline_has_volume(self, pid: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM outline_nodes WHERE project=? AND level='volume' LIMIT 1",
            (pid,)).fetchone() is not None

    def _outline_volume_for(self, pid: str, n: int) -> Optional[dict]:
        """章号落在哪个卷的区间内；无卷或无命中返回 None。"""
        r = self.conn.execute(
            "SELECT * FROM outline_nodes o WHERE project=? AND level='volume' "
            "AND start_chapter<=? AND ?<=end_chapter AND version="
            "  (SELECT MAX(o2.version) FROM outline_nodes o2 "
            "   WHERE o2.project=o.project AND o2.id=o.id) LIMIT 1",
            (pid, n, n)).fetchone()
        return dict(r) if r else None

    def outline_set(self, pid: str, level: str, *, node_id: Optional[str] = None,
                    parent_id: Optional[str] = None, title: Optional[str] = None,
                    body: str = "", status: str = "draft", seq: int = 0,
                    chapter_n: Optional[int] = None, start_chapter: Optional[int] = None,
                    end_chapter: Optional[int] = None,
                    data: Optional[dict] = None) -> dict:
        """写入 / 修订一个大纲节点。同一 id 再写一次即修订（version+1）。"""
        self._require_project(pid)
        level = _require_enum(level, "level", OUTLINE_LEVELS)
        status = _require_enum(status, "status", OUTLINE_STATUSES)
        seq = _require_int(seq, "seq", minimum=0, maximum=MAX_OUTLINE_SEQ)
        if title is not None:
            title = _require_str(title, "title", max_len=MAX_TITLE_LEN)
        if body is None:
            body = ""
        if not isinstance(body, str):
            raise LongformError(
                f"参数 `body` 必须是字符串，实际收到 {type(body).__name__}：{body!r}")
        if data is not None:
            _require_dict(data, "data")

        # 长度按 level 分档。章纲与 packet_assemble 的 brief 同域，
        # 保证任何一条章纲都能原样喂进去，不会出现「库里有纲却传不进去」。
        limit = (MAX_CHAPTER_OUTLINE_LEN if level == "chapter"
                 else MAX_OUTLINE_BODY_LEN)
        if len(body) > limit:
            raise LongformError(
                f"参数 `body` 过长：{len(body)} 字符，{level} 级上限 {limit}")

        # 分级的专属字段；传错级就报错，不静默丢弃。
        if level == "chapter":
            if chapter_n is None:
                raise LongformError(
                    "level='chapter' 必须提供 chapter_n：章纲要绑定扁平章号，"
                    "否则 outline_brief 无法按章取用")
            chapter_n = _require_int(chapter_n, "chapter_n",
                                     minimum=0, maximum=MAX_CHAPTER_N)
            for k, v in (("start_chapter", start_chapter), ("end_chapter", end_chapter)):
                if v is not None:
                    raise LongformError(f"{k} 只用于 level='volume'")
        elif level == "volume":
            if start_chapter is None or end_chapter is None:
                raise LongformError(
                    "level='volume' 必须同时提供 start_chapter 与 end_chapter"
                    "（卷 = 记录 + 章号区间）")
            start_chapter = _require_int(start_chapter, "start_chapter",
                                         minimum=0, maximum=MAX_CHAPTER_N)
            end_chapter = _require_int(end_chapter, "end_chapter",
                                       minimum=0, maximum=MAX_CHAPTER_N)
            if start_chapter > end_chapter:
                raise LongformError(
                    f"start_chapter({start_chapter}) 不能大于 end_chapter({end_chapter})")
            if chapter_n is not None:
                raise LongformError("chapter_n 只用于 level='chapter'")
        else:
            for k, v in (("chapter_n", chapter_n), ("start_chapter", start_chapter),
                         ("end_chapter", end_chapter)):
                if v is not None:
                    raise LongformError(f"{k} 不适用于 level='master'")

        if node_id is None:
            # 注意别写成 dict 字面量——那样 chapter 会先算 c{chapter_n:03d}
            # 而在非 chapter 级 chapter_n 是 None，直接 TypeError。
            if level == "master":
                node_id = "master"
            elif level == "volume":
                node_id = f"v{seq:02d}"
            else:
                node_id = f"c{chapter_n:03d}"
        else:
            node_id = _require_str(node_id, "id", max_len=MAX_OUTLINE_ID_LEN)

        parent = None
        if parent_id is not None:
            parent_id = _require_str(parent_id, "parent_id", max_len=MAX_OUTLINE_ID_LEN)
            parent = self._outline_latest(pid, parent_id)
            if parent is None:
                raise LongformError(f"上级节点不存在：{parent_id}（项目 {pid}）")

        if level == "master":
            if parent_id:
                raise LongformError("level='master' 是根节点，不能有 parent_id")
        else:
            if parent is None:
                # 省略 parent_id 时自动挂载：章挂「覆盖该章号的卷」，
                # 卷（以及项目还没分卷时的章）挂总纲。
                if level == "chapter":
                    vol = self._outline_volume_for(pid, chapter_n)
                    if vol is not None:
                        parent_id, parent = vol["id"], vol
                    elif self._outline_has_volume(pid):
                        raise LongformError(
                            f"第 {chapter_n} 章不在任何卷的章号区间内：请先建卷或扩卷，"
                            f"再写该章章纲（也可显式传 parent_id 指定卷）")
                if parent is None:
                    parent_id = "master"
                    parent = self._outline_latest(pid, parent_id)
                    if parent is None:
                        raise LongformError(
                            f"还没有总纲：请先 outline_set(level='master')，再展开"
                            f"{'卷纲' if level == 'volume' else '章纲'}")
            elif LEVEL_RANK[level] != LEVEL_RANK[parent["level"]] + 1:
                want = "master" if level == "volume" else "volume"
                raise LongformError(
                    f"层级不合法：{level} 的上级必须是 {want}，"
                    f"而 {parent_id} 是 {parent['level']}")

        if level == "master":
            r = self.conn.execute(
                "SELECT id FROM outline_nodes WHERE project=? AND level='master' "
                "AND id<>? LIMIT 1", (pid, node_id)).fetchone()
            if r:
                raise LongformError(
                    f"已有总纲（id={r['id']}），一部作品只有一个总纲；"
                    f"修订请对同一 id 调用 outline_set")
        elif level == "chapter":
            r = self.conn.execute(
                "SELECT id FROM outline_nodes WHERE project=? AND chapter_n=? "
                "AND id<>? LIMIT 1", (pid, chapter_n, node_id)).fetchone()
            if r:
                raise LongformError(f"章号 {chapter_n} 已被节点 {r['id']} 占用")
            prev = self._outline_latest(pid, node_id)
            if prev and prev.get("chapter_n") is not None and prev["chapter_n"] != chapter_n:
                raise LongformError(
                    f"章号不可变更：节点 {node_id} 已绑定第 {prev['chapter_n']} 章，"
                    f"收到 {chapter_n}（如需换章号请另建节点）")
        else:  # volume
            rows = self.conn.execute(
                "SELECT id, start_chapter, end_chapter FROM outline_nodes o "
                "WHERE project=? AND level='volume' AND id<>? AND version="
                "  (SELECT MAX(o2.version) FROM outline_nodes o2 "
                "   WHERE o2.project=o.project AND o2.id=o.id)",
                (pid, node_id)).fetchall()
            for r in rows:
                if r["start_chapter"] is None or r["end_chapter"] is None:
                    continue
                if not (end_chapter < r["start_chapter"] or start_chapter > r["end_chapter"]):
                    raise LongformError(
                        f"卷区间 [{start_chapter},{end_chapter}] 与已有卷 {r['id']} 的 "
                        f"[{r['start_chapter']},{r['end_chapter']}] 重叠")

        with self._tx():
            cur = self.conn.execute(
                "SELECT MAX(version) v FROM outline_nodes WHERE project=? AND id=?",
                (pid, node_id)).fetchone()
            version = (cur["v"] or 0) + 1
            self.conn.execute(
                "INSERT INTO outline_nodes(project,id,version,level,parent_id,seq,title,"
                "chapter_n,start_chapter,end_chapter,status,body,data_json,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, node_id, version, level, parent_id, seq, title, chapter_n,
                 start_chapter, end_chapter, status, body,
                 _dump(data or {}, "data"), _now()))
        return {"ok": True, "id": node_id, "level": level, "version": version,
                "revised": version > 1, "parent_id": parent_id, "seq": seq,
                "chapter_n": chapter_n, "start_chapter": start_chapter,
                "end_chapter": end_chapter, "status": status}

    def outline_get(self, pid: str, node_id: str,
                    version: Optional[int] = None) -> Optional[dict]:
        """读单个节点；省略 version 时取最新版（读操作宽容，不存在返回 None）。"""
        self._require_project(pid)
        node_id = _require_str(node_id, "id", max_len=MAX_OUTLINE_ID_LEN)
        if version is None:
            r = self._outline_latest(pid, node_id)
        else:
            version = _require_int(version, "version", minimum=1, maximum=MAX_CHAPTER_N)
            row = self.conn.execute(
                "SELECT * FROM outline_nodes WHERE project=? AND id=? AND version=?",
                (pid, node_id, version)).fetchone()
            r = dict(row) if row else None
        if not r:
            return None
        r["data_json"] = _load_json(r["data_json"], {})
        return r

    def outline_by_chapter(self, pid: str, n: int) -> Optional[dict]:
        """按扁平章号反查章纲；卷归属由它的 parent_id 给出。"""
        self._require_project(pid)
        n = _require_int(n, "n", minimum=0, maximum=MAX_CHAPTER_N)
        row = self.conn.execute(
            "SELECT * FROM outline_nodes o WHERE project=? AND chapter_n=? AND version="
            "  (SELECT MAX(o2.version) FROM outline_nodes o2 "
            "   WHERE o2.project=o.project AND o2.id=o.id) LIMIT 1", (pid, n)).fetchone()
        if not row:
            return None
        r = dict(row)
        r["data_json"] = _load_json(r["data_json"], {})
        return r

    def outline_list(self, pid: str, level: Optional[str] = None,
                     parent_id: Optional[str] = None, status: Optional[str] = None,
                     include_body: bool = False) -> List[dict]:
        """列出各 id 的**最新版**。

        默认不带 body（几十条章纲会灌爆上下文），改用 body_chars 表示篇幅；
        需要正文请用 outline_get 或 outline_tree(include_body=true)。
        """
        self._require_project(pid)
        sql = ("SELECT * FROM outline_nodes o WHERE project=? AND version="
               "  (SELECT MAX(o2.version) FROM outline_nodes o2 "
               "   WHERE o2.project=o.project AND o2.id=o.id)")
        args: List[Any] = [pid]
        if level is not None:
            args.append(_require_enum(level, "level", OUTLINE_LEVELS))
            sql += " AND level=?"
        if parent_id is not None:
            args.append(_require_str(parent_id, "parent_id", max_len=MAX_OUTLINE_ID_LEN))
            sql += " AND parent_id=?"
        if status is not None:
            args.append(_require_enum(status, "status", OUTLINE_STATUSES))
            sql += " AND status=?"
        sql += (" ORDER BY CASE level WHEN 'master' THEN 0 WHEN 'volume' THEN 1 "
                "ELSE 2 END, seq, chapter_n, id")
        out = []
        for r in self.conn.execute(sql, args).fetchall():
            d = dict(r)
            d["data_json"] = _load_json(d["data_json"], {})
            body = d.pop("body") or ""
            if include_body:
                d["body"] = body
            d["body_chars"] = len(body)
            out.append(d)
        return out

    def outline_tree(self, pid: str, include_body: bool = False) -> dict:
        """一次拿全树：master / volumes / chapters。"""
        self._require_project(pid)
        nodes = self.outline_list(pid, include_body=bool(include_body))
        levels = {n["id"]: n["level"] for n in nodes}
        master: Optional[dict] = None
        volumes: List[dict] = []
        chapters: List[dict] = []
        for n in nodes:
            if n["level"] == "master" and master is None:
                master = n
            elif n["level"] == "volume":
                volumes.append(n)
            elif n["level"] == "chapter":
                n["volume"] = n["parent_id"] if levels.get(
                    n["parent_id"]) == "volume" else None
                chapters.append(n)
        return {"ok": True, "project": pid, "master": master,
                "volumes": volumes, "chapters": chapters,
                "counts": {"volumes": len(volumes), "chapters": len(chapters)}}

    def outline_versions(self, pid: str, node_id: str) -> List[dict]:
        self._require_project(pid)
        node_id = _require_str(node_id, "id", max_len=MAX_OUTLINE_ID_LEN)
        rows = self.conn.execute(
            "SELECT version,status,created_at,LENGTH(body) body_chars FROM outline_nodes "
            "WHERE project=? AND id=? ORDER BY version", (pid, node_id)).fetchall()
        return [dict(r) for r in rows]

    def outline_delete(self, pid: str, node_id: str,
                       confirm: Optional[str] = None) -> dict:
        """删除某个节点及其全部版本；有子节点则拒绝（先删叶子）。"""
        self._require_project(pid)
        node_id = _require_str(node_id, "id", max_len=MAX_OUTLINE_ID_LEN)
        if confirm != node_id:
            raise LongformError(
                f"删除大纲节点需要显式确认：请传 confirm=\"{node_id}\""
                f"（必须与节点 id 完全一致）")
        if self._outline_latest(pid, node_id) is None:
            raise LongformError(f"大纲节点不存在：{node_id}（项目 {pid}）")
        child = self.conn.execute(
            "SELECT id FROM outline_nodes WHERE project=? AND parent_id=? LIMIT 1",
            (pid, node_id)).fetchone()
        if child:
            raise LongformError(
                f"节点 {node_id} 还有下级（如 {child['id']}），请先删除/改挂子节点")
        with self._tx():
            cur = self.conn.execute(
                "DELETE FROM outline_nodes WHERE project=? AND id=?", (pid, node_id))
        return {"ok": True, "id": node_id, "deleted_versions": cur.rowcount}

    def outline_stats(self, pid: str) -> dict:
        """大纲完成度快照，供 outline_next 判定「现在该做什么」。"""
        self._require_project(pid)
        master = self._outline_latest(pid, "master")
        mdata = _load_json(master["data_json"], {}) if master else {}
        if not isinstance(mdata, dict):
            mdata = {}
        has_volumes = bool(mdata.get("has_volumes") or mdata.get("有卷"))
        total = None
        for key in ("总章数", "total_chapters"):
            if key in mdata:
                total = _as_int(mdata[key], 0, minimum=0, maximum=MAX_CHAPTER_N)
                break
        planned: Optional[int] = None
        for key in ("卷数", "volumes", "volumes_planned"):
            if key in mdata:
                planned = _as_int(mdata[key], 0, minimum=0, maximum=MAX_CHAPTER_N)
                break

        volumes = self.outline_list(pid, level="volume")
        chapters = self.outline_list(pid, level="chapter")
        nums = sorted(c["chapter_n"] for c in chapters if c["chapter_n"] is not None)

        # 区间并集 → [1, total] 内的空洞
        gaps: List[List[int]] = []
        if has_volumes:
            spans = sorted((v["start_chapter"], v["end_chapter"]) for v in volumes
                           if v["start_chapter"] is not None
                           and v["end_chapter"] is not None)
            merged: List[List[int]] = []
            for s, e in spans:
                if merged and s <= merged[-1][1] + 1:
                    merged[-1][1] = max(merged[-1][1], e)
                else:
                    merged.append([s, e])
            if total is None:
                total = merged[-1][1] if merged else 0
            cursor = 1
            for s, e in merged:
                if s > cursor:
                    gaps.append([cursor, s - 1])
                cursor = max(cursor, e + 1)
            if cursor <= total:
                gaps.append([cursor, total])

        return {
            "master": 1 if master else 0,
            "master_status": master["status"] if master else None,
            "has_volumes": has_volumes,
            "volumes_planned": planned,
            "total_chapters": total,
            "volumes": len(volumes),
            "chapters_outlined": len(chapters),
            "covered_from": nums[0] if nums else None,
            "covered_to": nums[-1] if nums else None,
            "gaps": gaps,
        }

    def outline_diff(self, pid: str, node_id: str, a: Optional[int] = None,
                     b: Optional[int] = None) -> dict:
        """对比同一大纲节点的两个版本（与 chapter_diff 同构）。

        大纲是假设不是契约，所以"当初为什么改这一版"必须能一眼看清，
        否则重排结构时会把已经论证过要删的东西又捡回来。
        """
        self._require_project(pid)
        node_id = _require_str(node_id, "id", max_len=MAX_OUTLINE_ID_LEN)
        versions = [r["version"] for r in self.outline_versions(pid, node_id)]
        if not versions:
            raise LongformError(f"大纲节点不存在：{node_id}（项目 {pid}）")
        if len(versions) < 2:
            raise LongformError(
                f"节点 {node_id} 只有 {len(versions)} 个版本，无法对比；"
                f"修订一次后再来（对同一 id 再 outline_set 一次）")
        if a is None and b is None:
            a, b = versions[0], versions[-1]
        else:
            a = versions[0] if a is None else _require_int(
                a, "a", minimum=1, maximum=MAX_CHAPTER_N)
            b = versions[-1] if b is None else _require_int(
                b, "b", minimum=1, maximum=MAX_CHAPTER_N)

        ca, cb = self.outline_get(pid, node_id, a), self.outline_get(pid, node_id, b)
        if ca is None:
            raise LongformError(f"节点 {node_id} 没有版本 v{a}")
        if cb is None:
            raise LongformError(f"节点 {node_id} 没有版本 v{b}")

        alines = (ca.get("body") or "").splitlines()
        blines = (cb.get("body") or "").splitlines()
        full = list(difflib.unified_diff(
            alines, blines, fromfile=f"{node_id} v{a}", tofile=f"{node_id} v{b}",
            lineterm=""))
        truncated = len(full) > MAX_DIFF_LINES
        kept = full[:MAX_DIFF_LINES]
        if truncated:
            kept.append(f"...[差异超过 {MAX_DIFF_LINES} 行，已截断]")

        da, db = ca.get("data_json") or {}, cb.get("data_json") or {}
        keys = sorted(set(da) | set(db))
        changed = [k for k in keys if da.get(k) != db.get(k)]

        return {
            "ok": True, "id": node_id, "level": cb.get("level"),
            "from_version": a, "to_version": b,
            "added_lines": sum(1 for d in full if d.startswith("+")
                               and not d.startswith("+++")),
            "removed_lines": sum(1 for d in full if d.startswith("-")
                                 and not d.startswith("---")),
            "changed_fields": changed,
            "status_from": ca.get("status"), "status_to": cb.get("status"),
            "truncated": truncated, "diff": "\n".join(kept),
        }

    # ---------------- materials (素材库，带来源追溯) ----------------
    def material_add(self, pid: str, title: str, content: str = "",
                     kind: str = "reference", source: str = "",
                     source_kind: str = "other", tags: Optional[List[str]] = None,
                     chapter: Optional[int] = None,
                     data: Optional[dict] = None) -> dict:
        """登记一条素材。

        ``source`` 是这条素材的全部可信度来源——写作时"这句话哪来的"必须能
        一跳追溯到原始出处，否则后期无法核查，也无法在成稿里标注引用。
        """
        self._require_project(pid)
        title = _require_str(title, "title", max_len=MAX_TITLE_LEN)
        content = _require_str(content or "", "content",
                               allow_empty=True, max_len=MAX_MATERIAL_LEN)
        kind = _require_enum(kind, "kind", MATERIAL_KINDS)
        source = _require_str(source or "", "source",
                              allow_empty=True, max_len=MAX_SOURCE_LEN).strip()
        source_kind = _require_enum(source_kind, "source_kind",
                                    MATERIAL_SOURCE_KINDS)
        if data is not None:
            _require_dict(data, "data")

        clean: List[str] = []
        for t in (tags or []):
            if not isinstance(t, str):
                raise LongformError(
                    f"参数 `tags` 必须是字符串数组，实际收到 {type(t).__name__}：{t!r}")
            t = t.strip()
            if t and t not in clean:
                clean.append(t[:MAX_TAG_LEN])
        if len(clean) > MAX_TAGS:
            raise LongformError(f"标签过多：{len(clean)} 个，上限 {MAX_TAGS}")

        if chapter is not None:
            chapter = _require_int(chapter, "chapter",
                                   minimum=0, maximum=MAX_CHAPTER_N)
        # 有来源但没标种类：按形态猜一个，免得调用方每次都要显式传。
        if source and source_kind == "other":
            low = source.lower()
            if low.startswith("ima://"):
                source_kind = "ima"
            elif low.startswith(("http://", "https://")):
                source_kind = "url"
            elif os.path.isabs(source) or "/" in source or "\\" in source:
                source_kind = "file"

        mid = _uid("mt")
        with self._tx():
            self.conn.execute(
                "INSERT INTO materials(project,id,kind,title,content,source,"
                "source_kind,tags_json,chapter,data_json,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (pid, mid, kind, title, content, source, source_kind,
                 _dump(clean, "tags"), chapter, _dump(data or {}, "data"), _now()))
        return {"ok": True, "id": mid, "kind": kind, "title": title,
                "source": source, "source_kind": source_kind,
                "tags": clean, "chapter": chapter, "chars": len(content)}

    def material_get(self, pid: str, mid: str) -> Optional[dict]:
        self._require_project(pid)
        _require_str(mid, "id", max_len=2 * MAX_KEY_LEN)
        r = self.conn.execute(
            "SELECT * FROM materials WHERE project=? AND id=?", (pid, mid)).fetchone()
        return self._material_row(r) if r else None

    def _material_row(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        tags = _load_json(d.pop("tags_json"), [])
        d["tags"] = tags if isinstance(tags, list) else []
        d["data_json"] = _load_json(d["data_json"], {})
        d["chars"] = len(d.get("content") or "")
        return d

    def material_update(self, pid: str, mid: str, *,
                        title: Optional[str] = None,
                        content: Optional[str] = None,
                        tags: Optional[List[str]] = None,
                        data: Optional[dict] = None) -> dict:
        """就地补全素材正文/标题/标签，用于“题录卡 → 正文卡”的升级。"""
        current = self.material_get(pid, mid)
        if not current:
            raise LongformError(f"素材不存在：{mid}")
        if title is not None:
            title = _require_str(title, "title", max_len=MAX_TITLE_LEN)
        else:
            title = current.get("title") or ""
        if content is not None:
            content = _require_str(content, "content", allow_empty=True,
                                   max_len=MAX_MATERIAL_LEN)
        else:
            content = current.get("content") or ""
        if tags is None:
            clean = list(current.get("tags") or [])
        else:
            clean = []
            for t in tags:
                if not isinstance(t, str):
                    raise LongformError(
                        f"参数 `tags` 必须是字符串数组，实际收到 {type(t).__name__}：{t!r}")
                t = t.strip()
                if t and t not in clean:
                    clean.append(t[:MAX_TAG_LEN])
            if len(clean) > MAX_TAGS:
                raise LongformError(f"标签过多：{len(clean)} 个，上限 {MAX_TAGS}")
        if data is not None:
            _require_dict(data, "data")
        merged = dict(current.get("data_json") or {})
        merged.update(data or {})
        with self._tx():
            self.conn.execute(
                "UPDATE materials SET title=?,content=?,tags_json=?,data_json=?"
                " WHERE project=? AND id=?",
                (title, content, _dump(clean, "tags"), _dump(merged, "data"),
                 pid, mid))
        return self.material_get(pid, mid)

    def material_list(self, pid: str, kind: Optional[str] = None,
                      tag: Optional[str] = None, chapter: Optional[int] = None,
                      query: Optional[str] = None,
                      limit: Optional[int] = None) -> List[dict]:
        """列出/检索素材。空条件即按创建时间倒序，默认最多返回 DEFAULT_SEARCH_LIMIT 条。

        ``tag`` 在 Python 里过滤，因为标签存在 ``tags_json`` 里；因此带 ``tag``
        时**不能先 LIMIT**——否则素材超过 50 条后，带该标签的旧素材会被静默漏掉。
        """
        self._require_project(pid)
        sql = "SELECT * FROM materials WHERE project=?"
        args: List[Any] = [pid]
        if kind is not None:
            args.append(_require_enum(kind, "kind", MATERIAL_KINDS))
            sql += " AND kind=?"
        if chapter is not None:
            args.append(_require_int(chapter, "chapter",
                                     minimum=0, maximum=MAX_CHAPTER_N))
            sql += " AND chapter=?"
        if query is not None:
            q = _require_str(query, "query", max_len=MAX_SEARCH_LEN)
            esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{esc}%"
            sql += (" AND (title LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\'"
                    " OR source LIKE ? ESCAPE '\\')")
            args.extend([like, like, like])
        sql += " ORDER BY created_at DESC, id DESC"
        lim = _as_int(limit, DEFAULT_SEARCH_LIMIT,
                      minimum=1, maximum=MAX_SEARCH_LIMIT)

        tag = (_require_str(tag, "tag", max_len=MAX_TAG_LEN).strip()
               if tag is not None else None)
        if tag is None:
            sql += " LIMIT ?"
            args.append(lim)

        rows = self.conn.execute(sql, args).fetchall()
        out = [self._material_row(r) for r in rows]
        if tag is not None:
            out = [m for m in out if tag in m.get("tags", [])][:lim]
        return out

    def material_delete(self, pid: str, mid: str,
                        confirm: Optional[str] = None) -> dict:
        self._require_project(pid)
        _require_str(mid, "id", max_len=2 * MAX_KEY_LEN)
        if confirm != mid:
            raise LongformError(
                f"删除素材需要显式确认：请传 confirm=\"{mid}\"（必须与素材 id 完全一致）")
        with self._tx():
            cur = self.conn.execute(
                "DELETE FROM materials WHERE project=? AND id=?", (pid, mid))
            if cur.rowcount == 0:
                raise LongformError(f"素材不存在：{mid}（项目 {pid}）")
        return {"ok": True, "id": mid}

    # ---------------- citations (引证与验真) ----------------
    def _citation_row(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        d["verified"] = bool(d.get("verified"))
        return d

    def citation_add(self, pid: str, chapter_n: Optional[int], claim: str,
                     *, source: str = "", locator: str = "") -> dict:
        """登记一条引证；``claim`` 是正文/结论里的断言，``source`` 指向出处。

        ``source`` 不在数据层解释（素材 id / ch:N / 原始文本都合法），
        解析规则只存在于 ``citations.verify``，这里保持纯存储。
        """
        self._require_project(pid)
        claim = _require_str(claim, "claim", max_len=MAX_CITATION_CLAIM_LEN)
        if chapter_n is not None:
            chapter_n = _require_int(chapter_n, "chapter_n",
                                     minimum=1, maximum=MAX_CHAPTER_N)
        source = _require_str(source or "", "source", allow_empty=True,
                              max_len=MAX_CITATION_CLAIM_LEN).strip()
        locator = _require_str(locator or "", "locator", allow_empty=True,
                               max_len=MAX_SOURCE_LEN).strip()
        cid = _uid("ct")
        created = _now()
        with self._tx():
            self.conn.execute(
                "INSERT INTO citations(id,project,chapter_n,claim,source,locator,"
                "verified,evidence,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (cid, pid, chapter_n, claim, source, locator, 0, None, created))
        return {"ok": True, "id": cid, "project": pid, "chapter_n": chapter_n,
                "claim": claim, "source": source, "locator": locator,
                "verified": False, "evidence": None, "created_at": created}

    def citation_get(self, pid: str, citation_id: str) -> Optional[dict]:
        self._require_project(pid)
        _require_str(citation_id, "citation_id", max_len=2 * MAX_KEY_LEN)
        r = self.conn.execute(
            "SELECT * FROM citations WHERE project=? AND id=?",
            (pid, citation_id)).fetchone()
        return self._citation_row(r) if r else None

    def citation_list(self, pid: str, *, chapter_n: Optional[int] = None,
                      verified: Optional[bool] = None,
                      limit: Optional[int] = 100) -> List[dict]:
        """按创建时间升序列出引证；``verified`` 只接受布尔值。"""
        self._require_project(pid)
        sql = "SELECT * FROM citations WHERE project=?"
        args: List[Any] = [pid]
        if chapter_n is not None:
            args.append(_require_int(chapter_n, "chapter_n",
                                     minimum=1, maximum=MAX_CHAPTER_N))
            sql += " AND chapter_n=?"
        if verified is not None:
            if not isinstance(verified, (bool, int)) or verified not in (0, 1):
                raise LongformError(
                    f"参数 `verified` 只允许 true/false，实际收到 {verified!r}")
            args.append(1 if verified else 0)
            sql += " AND verified=?"
        lim = _as_int(limit, 100, minimum=1, maximum=MAX_SEARCH_LIMIT)
        sql += " ORDER BY created_at ASC, id ASC LIMIT ?"
        args.append(lim)
        rows = self.conn.execute(sql, args).fetchall()
        return [self._citation_row(r) for r in rows]

    def citation_update(self, pid: str, citation_id: str, *,
                        verified: Optional[bool] = None,
                        evidence: Optional[str] = None,
                        locator: Optional[str] = None,
                        source: Optional[str] = None) -> dict:
        """就地更新验真结果/出处；只允许布尔 verified，避免 0.5 一类脏值。"""
        current = self.citation_get(pid, citation_id)
        if not current:
            raise LongformError(f"引证不存在：{citation_id}（项目 {pid}）")
        if verified is not None:
            if not isinstance(verified, (bool, int)) or verified not in (0, 1):
                raise LongformError(
                    f"参数 `verified` 只允许 true/false，实际收到 {verified!r}")
            verified = 1 if verified else 0
        if evidence is not None:
            evidence = _require_str(evidence, "evidence", allow_empty=True,
                                    max_len=MAX_CITATION_CLAIM_LEN)
        if locator is not None:
            locator = _require_str(locator, "locator", allow_empty=True,
                                   max_len=MAX_SOURCE_LEN).strip()
        if source is not None:
            source = _require_str(source, "source", allow_empty=True,
                                  max_len=MAX_CITATION_CLAIM_LEN).strip()

        fields: Dict[str, Any] = {}
        if verified is not None:
            fields["verified"] = verified
        if evidence is not None:
            fields["evidence"] = evidence
        if locator is not None:
            fields["locator"] = locator
        if source is not None:
            fields["source"] = source
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)
            with self._tx():
                self.conn.execute(
                    f"UPDATE citations SET {sets} WHERE project=? AND id=?",
                    list(fields.values()) + [pid, citation_id])
        return self.citation_get(pid, citation_id)

    # ---------------- sections (逐段写作) ----------------
    def section_save(self, pid: str, n: int, idx: int, content: str,
                     title: str = "", status: str = "draft",
                     data: Optional[dict] = None) -> dict:
        """写一章里的某一段；同段再写即修订（version+1）。

        整章一气呵成的写法一旦中间崩了就得整章重来。分段落稿后，
        "第 3 段写歪了"只需要重修第 3 段。
        """
        self._require_project(pid)
        n = _require_int(n, "n", minimum=0, maximum=MAX_CHAPTER_N)
        idx = _require_int(idx, "idx", minimum=1, maximum=MAX_CHAPTER_N)
        _require_str(content, "content", allow_empty=True, max_len=MAX_SECTION_LEN)
        _require_enum(status, "status", CHAPTER_STATUSES)
        if title:
            title = _require_str(title, "title", max_len=MAX_TITLE_LEN)
        if data is not None:
            _require_dict(data, "data")

        with self._tx():
            cur = self.conn.execute(
                "SELECT MAX(version) v FROM sections "
                "WHERE project=? AND chapter_n=? AND idx=?", (pid, n, idx)).fetchone()
            version = (cur["v"] or 0) + 1
            self.conn.execute(
                "INSERT INTO sections(project,chapter_n,idx,version,title,content,"
                "status,data_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (pid, n, idx, version, title, content, status,
                 _dump(data or {}, "data"), _now()))
        return {"ok": True, "chapter_n": n, "idx": idx, "version": version,
                "revised": version > 1, "status": status, "words": len(content)}

    def section_list(self, pid: str, n: int) -> List[dict]:
        """列出某章各段的**最新版**（不含正文，只给字数概览）。"""
        self._require_project(pid)
        n = _require_int(n, "n", minimum=0, maximum=MAX_CHAPTER_N)
        rows = self.conn.execute(
            "SELECT idx, version AS latest_version, title, status, created_at, "
            "LENGTH(content) words FROM sections c WHERE project=? AND chapter_n=? "
            "AND version=(SELECT MAX(c2.version) FROM sections c2 "
            "  WHERE c2.project=c.project AND c2.chapter_n=c.chapter_n "
            "  AND c2.idx=c.idx) ORDER BY idx", (pid, n)).fetchall()
        return [dict(r) for r in rows]

    def section_get(self, pid: str, n: int, idx: int,
                    version: Optional[int] = None) -> Optional[dict]:
        self._require_project(pid)
        n = _require_int(n, "n", minimum=0, maximum=MAX_CHAPTER_N)
        idx = _require_int(idx, "idx", minimum=1, maximum=MAX_CHAPTER_N)
        if version is None:
            r = self.conn.execute(
                "SELECT * FROM sections WHERE project=? AND chapter_n=? AND idx=? "
                "ORDER BY version DESC LIMIT 1", (pid, n, idx)).fetchone()
        else:
            version = _require_int(version, "version",
                                   minimum=1, maximum=MAX_CHAPTER_N)
            r = self.conn.execute(
                "SELECT * FROM sections WHERE project=? AND chapter_n=? "
                "AND idx=? AND version=?", (pid, n, idx, version)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["data_json"] = _load_json(d["data_json"], {})
        d["words"] = len(d.get("content") or "")
        return d

    def section_merge(self, pid: str, n: int, title: Optional[str] = None,
                      separator: str = "\n\n", status: str = "draft") -> dict:
        """把某章各段最新版拼成整章，交给 chapter_save。

        段序中断（1、2、4 缺 3）视为未写完，直接报错——拼出一篇缺段的正文
        比不拼更糟，因为缺陷会被后面所有步骤当成正常内容。
        """
        self._require_project(pid)
        n = _require_int(n, "n", minimum=0, maximum=MAX_CHAPTER_N)
        _require_enum(status, "status", CHAPTER_STATUSES)
        rows = self.section_list(pid, n)
        if not rows:
            raise LongformError(f"第 {n} 章还没有任何段落，无法合并（先 section_save）")
        want = list(range(1, rows[-1]["idx"] + 1))
        have = [r["idx"] for r in rows]
        missing = [i for i in want if i not in have]
        if missing:
            raise LongformError(
                f"第 {n} 章的段落不连续，缺第 {missing} 段；补齐后再合并")

        parts = []
        for i in want:
            seg = self.section_get(pid, n, i)
            text = (seg.get("content") or "").strip() if seg else ""
            if not text:
                raise LongformError(f"第 {n} 章第 {i} 段是空的，无法合并")
            parts.append(text)
        content = separator.join(parts)
        if title is None:
            title = f"第{n}章"
        return self.chapter_save(pid, n, title, content, status=status,
                                 data={"merged_from_sections": len(parts)})

    def section_delete(self, pid: str, n: int, idx: int,
                       confirm: Optional[str] = None) -> dict:
        self._require_project(pid)
        n = _require_int(n, "n", minimum=0, maximum=MAX_CHAPTER_N)
        idx = _require_int(idx, "idx", minimum=1, maximum=MAX_CHAPTER_N)
        token = f"{n}:{idx}"
        if confirm != token:
            raise LongformError(
                f"删除段落需要显式确认：请传 confirm=\"{token}\"（章号:段序）")
        with self._tx():
            cur = self.conn.execute(
                "DELETE FROM sections WHERE project=? AND chapter_n=? AND idx=?",
                (pid, n, idx))
            if cur.rowcount == 0:
                raise LongformError(f"第 {n} 章第 {idx} 段不存在")
        return {"ok": True, "chapter_n": n, "idx": idx,
                "deleted_versions": cur.rowcount}

    # ---------------- summaries (章节摘要) ----------------
    # 长篇写到几十章后，把整章正文全塞进输入包必然超预算。
    # 每章一条摘要，packet 就能用「几千字摘要」替代「几十万字正文」。
    def summary_set(self, pid: str, n: int, summary: str) -> dict:
        """写入 / 覆盖某章摘要；空串或纯空白视为删除该章摘要。"""
        self._require_project(pid)
        n = _require_int(n, "n", minimum=0, maximum=MAX_CHAPTER_N)
        summary = _require_str(summary, "summary", allow_empty=True,
                               max_len=MAX_SUMMARY_LEN)
        with self._tx():
            if not summary.strip():
                self.conn.execute(
                    "DELETE FROM summaries WHERE project=? AND chapter_n=?", (pid, n))
                return {"ok": True, "chapter_n": n, "deleted": True}
            self.conn.execute(
                "INSERT INTO summaries(project,chapter_n,summary,updated_at)"
                " VALUES(?,?,?,?)"
                " ON CONFLICT(project, chapter_n) DO UPDATE SET"
                " summary=excluded.summary, updated_at=excluded.updated_at",
                (pid, n, summary, _now()))
        return {"ok": True, "chapter_n": n, "chars": len(summary)}

    def summary_get(self, pid: str, n: int) -> Optional[dict]:
        self._require_project(pid)
        n = _require_int(n, "n", minimum=0, maximum=MAX_CHAPTER_N)
        r = self.conn.execute(
            "SELECT chapter_n, summary, updated_at FROM summaries "
            "WHERE project=? AND chapter_n=?", (pid, n)).fetchone()
        return dict(r) if r else None

    def summary_list(self, pid: str, from_chapter: int = 0,
                     to_chapter: int = MAX_CHAPTER_N) -> List[dict]:
        """按章号升序列出区间内的摘要（含端点）。"""
        self._require_project(pid)
        from_chapter = _require_int(from_chapter, "from_chapter",
                                    minimum=0, maximum=MAX_CHAPTER_N)
        to_chapter = _require_int(to_chapter, "to_chapter",
                                  minimum=0, maximum=MAX_CHAPTER_N)
        rows = self.conn.execute(
            "SELECT chapter_n, summary, updated_at FROM summaries "
            "WHERE project=? AND chapter_n BETWEEN ? AND ? "
            "ORDER BY chapter_n", (pid, from_chapter, to_chapter)).fetchall()
        return [dict(r) for r in rows]

    def summary_delete(self, pid: str, n: int,
                       confirm: Optional[str] = None) -> dict:
        self._require_project(pid)
        n = _require_int(n, "n", minimum=0, maximum=MAX_CHAPTER_N)
        if confirm != str(n):
            raise LongformError(
                f"删除摘要需要显式确认：请传 confirm=\"{n}\"（必须与章号完全一致）")
        with self._tx():
            cur = self.conn.execute(
                "DELETE FROM summaries WHERE project=? AND chapter_n=?", (pid, n))
            if cur.rowcount == 0:
                raise LongformError(f"第 {n} 章没有摘要（项目 {pid}）")
        return {"ok": True, "chapter_n": n}

    # ---------------- story_state (故事状态键值表) ----------------
    def story_state_set(self, pid: str, key: str, value: Any) -> dict:
        """写入一份故事状态；``value is None`` 表示删除该键。"""
        self._require_project(pid)
        key = _require_str(key, "key", max_len=MAX_KEY_LEN)
        with self._tx():
            if value is None:
                self.conn.execute(
                    "DELETE FROM story_state WHERE project=? AND key=?", (pid, key))
                return {"ok": True, "key": key, "deleted": True}
            self.conn.execute(
                "INSERT INTO story_state(project,key,value_json,updated_at)"
                " VALUES(?,?,?,?)"
                " ON CONFLICT(project, key) DO UPDATE SET"
                " value_json=excluded.value_json, updated_at=excluded.updated_at",
                (pid, key, _dump(value, "value"), _now()))
        return {"ok": True, "key": key}

    def story_state_get(self, pid: str, key: str) -> Any:
        self._require_project(pid)
        key = _require_str(key, "key", max_len=MAX_KEY_LEN)
        r = self.conn.execute(
            "SELECT value_json FROM story_state WHERE project=? AND key=?",
            (pid, key)).fetchone()
        return _load_json(r["value_json"], None) if r else None

    def story_state_list(self, pid: str) -> List[dict]:
        self._require_project(pid)
        rows = self.conn.execute(
            "SELECT key, value_json, updated_at FROM story_state "
            "WHERE project=? ORDER BY key", (pid,)).fetchall()
        return [{"key": r["key"], "value": _load_json(r["value_json"], None),
                 "updated_at": r["updated_at"]} for r in rows]

    def story_state_delete(self, pid: str, key: str,
                           confirm: Optional[str] = None) -> dict:
        self._require_project(pid)
        key = _require_str(key, "key", max_len=MAX_KEY_LEN)
        if confirm != key:
            raise LongformError(
                f"删除故事状态需要显式确认：请传 confirm=\"{key}\""
                f"（必须与状态 key 完全一致）")
        with self._tx():
            cur = self.conn.execute(
                "DELETE FROM story_state WHERE project=? AND key=?", (pid, key))
            if cur.rowcount == 0:
                raise LongformError(f"故事状态不存在：{key}（项目 {pid}）")
        return {"ok": True, "key": key}

    # ---------------- expectations (预期账本) ----------------
    def expectation_add(self, pid: str, chapter_n: int, state: str,
                        note: str = "", setup: str = "", trigger: str = "",
                        payoff: str = "", payoff_evidence: str = "") -> dict:
        """登记一条读者预期。同一章可多次登记，统计时取该章最后一条。

        v6 的可选字段把预期拆成承诺、触发条件、兑现事件和兑现证据；
        旧的 ``note`` 调用保持兼容，审计时也会把 note 当作承诺摘要。
        """
        self._require_project(pid)
        chapter_n = _require_int(chapter_n, "chapter_n",
                                 minimum=0, maximum=MAX_CHAPTER_N)
        state = _require_enum(state, "state", EXPECTATION_STATES)
        note = _require_str(note or "", "note", allow_empty=True,
                            max_len=MAX_CLAIM_LEN)
        payload = {}
        for key, value in (("setup", setup), ("trigger", trigger),
                           ("payoff", payoff), ("payoff_evidence", payoff_evidence)):
            payload[key] = _require_str(value or "", key, allow_empty=True,
                                       max_len=MAX_CLAIM_LEN).strip()
        eid = _uid("ex")
        with self._tx():
            self.conn.execute(
                "INSERT INTO expectations(project,id,chapter_n,state,note,data_json,created_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (pid, eid, chapter_n, state, note, _dump(payload, "data"), _now()))
        return {"ok": True, "id": eid, "chapter_n": chapter_n, "state": state,
                **payload}

    def expectation_list(self, pid: str,
                         chapter_n: Optional[int] = None) -> List[dict]:
        self._require_project(pid)
        if chapter_n is not None:
            chapter_n = _require_int(chapter_n, "chapter_n",
                                     minimum=0, maximum=MAX_CHAPTER_N)
            rows = self.conn.execute(
                "SELECT * FROM expectations WHERE project=? AND chapter_n=? "
                "ORDER BY created_at", (pid, chapter_n)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM expectations WHERE project=? "
                "ORDER BY chapter_n, created_at", (pid,)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            data = _load_json(item.get("data_json"), {})
            item["data_json"] = data if isinstance(data, dict) else {}
            for key in ("setup", "trigger", "payoff", "payoff_evidence"):
                item[key] = str(item["data_json"].get(key) or "")
            out.append(item)
        return out

    def expectation_stats(self, pid: str) -> dict:
        """四态计数 + 连续同态告警 + 配额。"""
        self._require_project(pid)
        rows = self.expectation_list(pid)
        latest: Dict[int, dict] = {}
        for row in rows:
            latest[int(row["chapter_n"])] = row
        counts = {state: 0 for state in EXPECTATION_STATES}
        for row in latest.values():
            counts[row["state"]] = counts.get(row["state"], 0) + 1

        streak_state = ""
        streak_len = 0
        if latest:
            ordered = [latest[n] for n in sorted(latest)]
            streak_state = ordered[-1]["state"]
            for row in reversed(ordered):
                if row["state"] != streak_state:
                    break
                streak_len += 1

        quota = self.config_get(pid, "expectation.quota")
        if not isinstance(quota, dict):
            quota = {}
        alert_after = _as_int(quota.get("alert_after"), 3,
                              minimum=1, maximum=MAX_CHAPTER_N)
        warnings: List[str] = []
        if streak_len >= alert_after and streak_state:
            warnings.append(
                f"最近连续 {streak_len} 章都是「{streak_state}」；"
                "连续同态会让读者预期失去弹性，建议下一章安排一次换维或满足")
        if counts.get("violate", 0) >= 2:
            warnings.append(
                f"已有 {counts['violate']} 章「违背」预期；确认这是有意为之，"
                "而不是人物动机或因果链断了")
        return {
            "ok": True,
            "counts": counts,
            "total": len(latest),
            "rows": len(rows),
            "streak": {"state": streak_state, "length": streak_len,
                       "alert_after": alert_after},
            "quota": quota,
            "warnings": warnings,
            "latest": [latest[n] for n in sorted(latest)],
        }

    # ---------------- runs / run_steps（运行与检查点） ----------------
    def _run_row(self, row: sqlite3.Row) -> dict:
        """把 runs 行摊平：policy/budget/target 解析为对象，去掉原始 JSON 列。"""
        d = dict(row)
        for key in ("policy", "budget", "target"):
            d[key] = _load_json(d.pop(key + "_json", None), None)
        return d

    def run_create(self, pid: str, kind: str = "chapter",
                   policy: Optional[dict] = None,
                   budget: Optional[dict] = None,
                   target: Optional[dict] = None) -> dict:
        """新建一次运行（默认 pending），返回含完整字段的运行快照。"""
        self._require_project(pid)
        kind = _require_str(kind, "kind", max_len=MAX_KEY_LEN)
        for name, value in (("policy", policy), ("budget", budget),
                            ("target", target)):
            if value is not None:
                _require_dict(value, name)
        rid = _uid("run")
        now = _now()
        with self._tx():
            self.conn.execute(
                "INSERT INTO runs(id,project,kind,status,policy_json,budget_json,"
                "target_json,error,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (rid, pid, kind, "pending",
                 None if policy is None else _dump(policy, "policy"),
                 None if budget is None else _dump(budget, "budget"),
                 None if target is None else _dump(target, "target"),
                 None, now, now))
        return {"ok": True, **self.run_get(rid)}

    def run_get(self, run_id: str) -> Optional[dict]:
        _require_str(run_id, "run_id", max_len=2 * MAX_KEY_LEN)
        row = self.conn.execute(
            "SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return self._run_row(row) if row else None

    def run_list(self, project: Optional[str] = None,
                 status: Optional[str] = None,
                 limit: Optional[int] = None) -> List[dict]:
        """按创建时间倒序列出运行；limit 默认 50、上限 200。"""
        clauses: List[str] = []
        args: List[Any] = []
        if project is not None:
            clauses.append("project=?")
            args.append(_require_str(project, "project"))
        if status is not None:
            clauses.append("status=?")
            args.append(_require_enum(status, "status", RUN_STATUSES))
        sql = "SELECT * FROM runs"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        args.append(_as_int(limit, 50, minimum=1, maximum=200))
        return [self._run_row(r) for r in self.conn.execute(sql, args).fetchall()]

    def run_update(self, run_id: str, *, status: Optional[str] = None,
                   error: Any = ..., policy: Any = ...,
                   budget: Any = ..., target: Any = ...) -> dict:
        """局部更新运行。

        参数默认值是哨兵 ``...``：不传 = 不改；显式传 ``None`` = 清空该字段。
        这样才能区分“只改状态”和“把上一次的错误信息擦掉”。
        """
        _require_str(run_id, "run_id", max_len=2 * MAX_KEY_LEN)
        if self.conn.execute(
                "SELECT 1 FROM runs WHERE id=?", (run_id,)).fetchone() is None:
            raise LongformError(f"运行不存在：{run_id}")

        sets: List[str] = []
        args: List[Any] = []
        if status is not None:
            sets.append("status=?")
            args.append(_require_enum(status, "status", RUN_STATUSES))
        if error is not ...:
            sets.append("error=?")
            if error is None:
                args.append(None)
            else:
                args.append(_require_str(error, "error", allow_empty=True,
                                         max_len=MAX_CLAIM_LEN))
        for name, value in (("policy", policy), ("budget", budget),
                            ("target", target)):
            if value is ...:
                continue
            sets.append(f"{name}_json=?")
            if value is None:
                args.append(None)
            else:
                args.append(_dump(_require_dict(value, name), name))
        sets.append("updated_at=?")
        args.append(_now())
        args.append(run_id)
        with self._tx():
            self.conn.execute(
                f"UPDATE runs SET {', '.join(sets)} WHERE id=?", args)
        return {"ok": True, "id": run_id}

    def run_step_upsert(self, project: str, run_id: str, seq: int, name: str,
                        status: str, *, attempt: int = 1,
                        input_hash: Optional[str] = None,
                        output: Any = None,
                        error: Optional[str] = None) -> dict:
        """幂等写入一个运行步骤（检查点）。

        同 ``(run_id, seq)`` 已存在则更新；``started_at`` 只在首次插入时写，
        重试不会把“最初开始时间”冲掉。
        """
        self._require_project(project)
        run_id = _require_str(run_id, "run_id", max_len=2 * MAX_KEY_LEN)
        seq = _require_int(seq, "seq", minimum=0, maximum=MAX_CHAPTER_N)
        name = _require_str(name, "name", max_len=MAX_KEY_LEN)
        status = _require_enum(status, "status", STEP_STATUSES)
        attempt = _require_int(attempt, "attempt", minimum=1, maximum=100_000)
        if input_hash is not None:
            input_hash = _require_str(input_hash, "input_hash", allow_empty=True,
                                      max_len=128)
        if error is not None:
            error = _require_str(error, "error", allow_empty=True,
                                 max_len=MAX_CLAIM_LEN)
        output_json = None if output is None else _dump(output, "output")
        now = _now()
        finished = now if status in _TERMINAL_STEP_STATUSES else None
        with self._tx():
            run = self.conn.execute(
                "SELECT project FROM runs WHERE id=?", (run_id,)).fetchone()
            if run is None or run["project"] != project:
                raise LongformError(f"运行不存在：{run_id}（项目 {project}）")
            exists = self.conn.execute(
                "SELECT 1 FROM run_steps WHERE run_id=? AND seq=?",
                (run_id, seq)).fetchone()
            if exists is None:
                self.conn.execute(
                    "INSERT INTO run_steps(project,run_id,seq,name,status,attempt,"
                    "input_hash,output_json,error,started_at,finished_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (project, run_id, seq, name, status, attempt, input_hash,
                     output_json, error, now, finished))
            else:
                self.conn.execute(
                    "UPDATE run_steps SET name=?,status=?,attempt=?,input_hash=?,"
                    "output_json=?,error=?,finished_at=? WHERE run_id=? AND seq=?",
                    (name, status, attempt, input_hash, output_json, error,
                     finished, run_id, seq))
        return {"ok": True, "run_id": run_id, "seq": seq, "name": name,
                "status": status, "attempt": attempt}

    def run_steps(self, run_id: str) -> List[dict]:
        _require_str(run_id, "run_id", max_len=2 * MAX_KEY_LEN)
        rows = self.conn.execute(
            "SELECT * FROM run_steps WHERE run_id=? ORDER BY seq",
            (run_id,)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["output"] = _load_json(item.pop("output_json", None), None)
            out.append(item)
        return out

    # ---------------- llm_calls（逐次成本账本） ----------------
    def llm_call_add(self, project: str, *, run_id: Optional[str] = None,
                     step_name: Optional[str] = None,
                     chapter_n: Optional[int] = None,
                     provider: str = "", model: str = "", tier: str = "",
                     prompt_hash: Optional[str] = None,
                     input_tokens: int = 0, output_tokens: int = 0,
                     cached_tokens: int = 0,
                     cost_estimate: float = 0.0) -> dict:
        """登记一次模型调用，供 usage_summary 聚合与预算护栏使用。"""
        self._require_project(project)
        if run_id is not None:
            run_id = _require_str(run_id, "run_id", max_len=2 * MAX_KEY_LEN)
        if step_name is not None:
            step_name = _require_str(step_name, "step_name", allow_empty=True,
                                     max_len=MAX_KEY_LEN)
        if chapter_n is not None:
            chapter_n = _require_int(chapter_n, "chapter_n",
                                     minimum=0, maximum=MAX_CHAPTER_N)
        provider = _require_str(provider or "", "provider", allow_empty=True,
                                max_len=MAX_NAME_LEN)
        model = _require_str(model or "", "model", allow_empty=True,
                             max_len=MAX_NAME_LEN)
        tier = _require_str(tier or "", "tier", allow_empty=True,
                            max_len=MAX_NAME_LEN)
        if prompt_hash is not None:
            prompt_hash = _require_str(prompt_hash, "prompt_hash",
                                       allow_empty=True, max_len=128)
        input_tokens = _require_int(input_tokens, "input_tokens",
                                    minimum=0, maximum=10 ** 9)
        output_tokens = _require_int(output_tokens, "output_tokens",
                                     minimum=0, maximum=10 ** 9)
        cached_tokens = _require_int(cached_tokens, "cached_tokens",
                                     minimum=0, maximum=10 ** 9)
        cost = _require_float(cost_estimate, "cost_estimate", minimum=0.0)

        cid = _uid("call")
        with self._tx():
            self.conn.execute(
                "INSERT INTO llm_calls(id,project,run_id,step_name,chapter_n,"
                "provider,model,tier,prompt_hash,input_tokens,output_tokens,"
                "cached_tokens,cost_estimate,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, project, run_id, step_name, chapter_n, provider, model,
                 tier, prompt_hash, input_tokens, output_tokens, cached_tokens,
                 cost, _now()))
        return {"ok": True, "id": cid,
                "total_tokens": input_tokens + output_tokens,
                "cost_estimate": cost}

    def llm_calls_list(self, project: Optional[str] = None, *,
                       run_id: Optional[str] = None,
                       chapter_n: Optional[int] = None,
                       limit: Optional[int] = None) -> List[dict]:
        """按时间倒序列出调用记录；limit 默认 500、上限 5000。"""
        clauses: List[str] = []
        args: List[Any] = []
        if project is not None:
            clauses.append("project=?")
            args.append(_require_str(project, "project"))
        if run_id is not None:
            clauses.append("run_id=?")
            args.append(_require_str(run_id, "run_id", max_len=2 * MAX_KEY_LEN))
        if chapter_n is not None:
            clauses.append("chapter_n=?")
            args.append(_require_int(chapter_n, "chapter_n",
                                     minimum=0, maximum=MAX_CHAPTER_N))
        sql = "SELECT * FROM llm_calls"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        args.append(_as_int(limit, 500, minimum=1, maximum=5000))
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def usage_summary(self, project: str, by: str = "chapter", *,
                      run_id: Optional[str] = None) -> dict:
        """按章 / 模型 / 天 / 运行聚合 token 与成本。"""
        by = _require_enum(by, "by", ("chapter", "model", "day", "run"))
        key_expr = {
            "chapter": "CASE WHEN chapter_n IS NULL THEN 'unknown' "
                       "ELSE CAST(chapter_n AS TEXT) END",
            "model": "COALESCE(model, 'unknown')",
            "day": "date(created_at, 'unixepoch', 'localtime')",
            "run": "COALESCE(run_id, 'unknown')",
        }[by]

        where = "project=?"
        args: List[Any] = [project]
        if run_id is not None:
            where += " AND run_id=?"
            args.append(_require_str(run_id, "run_id", max_len=2 * MAX_KEY_LEN))

        total = self.conn.execute(
            "SELECT COALESCE(SUM(input_tokens),0) i,"
            " COALESCE(SUM(output_tokens),0) o,"
            " COALESCE(SUM(cached_tokens),0) c,"
            " COALESCE(SUM(cost_estimate),0) cost"
            f" FROM llm_calls WHERE {where}", args).fetchone()
        total_input = int(total["i"] or 0)
        total_output = int(total["o"] or 0)
        total_cached = int(total["c"] or 0)
        total_cost = float(total["cost"] or 0)

        rows = self.conn.execute(
            f"SELECT {key_expr} AS k, SUM(input_tokens) AS i,"
            " SUM(output_tokens) AS o, SUM(cached_tokens) AS c,"
            " SUM(cost_estimate) AS cost, COUNT(*) AS calls"
            f" FROM llm_calls WHERE {where} GROUP BY k ORDER BY k",
            args).fetchall()
        groups = []
        for r in rows:
            key: Any = r["k"]
            if by == "chapter" and key != "unknown":
                try:
                    key = int(key)
                except (TypeError, ValueError):
                    pass
            groups.append({
                "key": key,
                "input_tokens": int(r["i"] or 0),
                "output_tokens": int(r["o"] or 0),
                "cached_tokens": int(r["c"] or 0),
                "cost": float(r["cost"] or 0),
                "calls": int(r["calls"] or 0),
            })

        return {
            "project": project,
            "by": by,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_cached_tokens": total_cached,
            "total_cost": total_cost,
            "total_tokens": total_input + total_output,
            "groups": groups,
        }

    # ---------------- lint_findings（L0 确定性检查结果） ----------------
    def lint_finding_add(self, project: str, chapter_n: int, rule: str,
                         level: str, *, run_id: Optional[str] = None,
                         evidence: str = "", locator: str = "",
                         suggestion: str = "") -> dict:
        self._require_project(project)
        chapter_n = _require_int(chapter_n, "chapter_n",
                                 minimum=0, maximum=MAX_CHAPTER_N)
        rule = _require_str(rule, "rule", max_len=MAX_KEY_LEN)
        level = _require_enum(level, "level", LINT_LEVELS)
        if run_id is not None:
            run_id = _require_str(run_id, "run_id", max_len=2 * MAX_KEY_LEN)
        evidence = _require_str(evidence or "", "evidence", allow_empty=True,
                                max_len=MAX_CLAIM_LEN)
        locator = _require_str(locator or "", "locator", allow_empty=True,
                              max_len=2 * MAX_KEY_LEN)
        suggestion = _require_str(suggestion or "", "suggestion",
                                  allow_empty=True, max_len=MAX_CLAIM_LEN)

        fid = _uid("lint")
        with self._tx():
            self.conn.execute(
                "INSERT INTO lint_findings(id,project,chapter_n,run_id,rule,level,"
                "evidence,locator,suggestion,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (fid, project, chapter_n, run_id, rule, level, evidence,
                 locator, suggestion, _now()))
        return {"ok": True, "id": fid}

    def lint_findings_list(self, project: str, *,
                           chapter_n: Optional[int] = None,
                           run_id: Optional[str] = None,
                           limit: Optional[int] = None) -> List[dict]:
        """按创建时间升序列出检查结果；limit 默认 500。"""
        self._require_project(project)
        clauses = ["project=?"]
        args: List[Any] = [project]
        if chapter_n is not None:
            clauses.append("chapter_n=?")
            args.append(_require_int(chapter_n, "chapter_n",
                                     minimum=0, maximum=MAX_CHAPTER_N))
        if run_id is not None:
            clauses.append("run_id=?")
            args.append(_require_str(run_id, "run_id", max_len=2 * MAX_KEY_LEN))
        sql = ("SELECT * FROM lint_findings WHERE " + " AND ".join(clauses)
               + " ORDER BY created_at, id LIMIT ?")
        args.append(_as_int(limit, 500, minimum=1, maximum=5000))
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def lint_findings_clear(self, project: str, *,
                            chapter_n: Optional[int] = None,
                            run_id: Optional[str] = None) -> dict:
        """清理检查结果。必须给 ``chapter_n`` 或 ``run_id`` 之一，
        否则一次误调用会清空整个项目的检查历史。"""
        self._require_project(project)
        if chapter_n is None and run_id is None:
            raise LongformError(
                "清空检查结果至少需要 chapter_n 或 run_id 之一（防止误清全项目）")
        clauses = ["project=?"]
        args: List[Any] = [project]
        if chapter_n is not None:
            clauses.append("chapter_n=?")
            args.append(_require_int(chapter_n, "chapter_n",
                                     minimum=0, maximum=MAX_CHAPTER_N))
        if run_id is not None:
            clauses.append("run_id=?")
            args.append(_require_str(run_id, "run_id", max_len=2 * MAX_KEY_LEN))
        with self._tx():
            cur = self.conn.execute(
                "DELETE FROM lint_findings WHERE " + " AND ".join(clauses), args)
        return {"ok": True, "deleted": cur.rowcount}

    # ---------------- loop_runs / loop_metrics（质量环） ----------------
    def _loop_row(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        d["policy"] = _load_json(d.pop("policy_json", None), None)
        return d

    def loop_create(self, project: str, chapter_n: int,
                    policy: Optional[dict] = None) -> dict:
        """新建一次质量环（默认 running）；返回含完整字段的快照。"""
        self._require_project(project)
        chapter_n = _require_int(chapter_n, "chapter_n",
                                 minimum=0, maximum=MAX_CHAPTER_N)
        if policy is not None:
            _require_dict(policy, "policy")
        lid = _uid("loop")
        now = _now()
        with self._tx():
            self.conn.execute(
                "INSERT INTO loop_runs(id,project,chapter_n,status,policy_json,"
                "best_score,exit_reason,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (lid, project, chapter_n, "running",
                 None if policy is None else _dump(policy, "policy"),
                 None, None, now, now))
        return {"ok": True, **self.loop_get(lid)}

    def loop_update(self, loop_id: str, *, status: Optional[str] = None,
                    best_score: Optional[float] = None,
                    exit_reason: Optional[str] = None,
                    policy: Any = ...) -> dict:
        """局部更新质量环；``None`` 表示不改该字段，``policy`` 默认不改。

        ``policy`` 用哨兵 ``...`` 区分「不传」与「显式清空」；studio 模式需要
        跨轮保存 current_text / best_text / round 等进度。
        """
        _require_str(loop_id, "loop_id", max_len=2 * MAX_KEY_LEN)
        if self.conn.execute(
                "SELECT 1 FROM loop_runs WHERE id=?", (loop_id,)).fetchone() is None:
            raise LongformError(f"质量环不存在：{loop_id}")

        sets: List[str] = []
        args: List[Any] = []
        if status is not None:
            sets.append("status=?")
            args.append(_require_enum(status, "status", LOOP_STATUSES))
        if best_score is not None:
            sets.append("best_score=?")
            args.append(_require_score(best_score, "best_score"))
        if exit_reason is not None:
            sets.append("exit_reason=?")
            args.append(_require_str(exit_reason, "exit_reason",
                                     allow_empty=True, max_len=MAX_CLAIM_LEN))
        if policy is not ...:
            sets.append("policy_json=?")
            if policy is None:
                args.append(None)
            else:
                args.append(_dump(_require_dict(policy, "policy"), "policy"))
        sets.append("updated_at=?")
        args.append(_now())
        args.append(loop_id)
        with self._tx():
            self.conn.execute(
                f"UPDATE loop_runs SET {', '.join(sets)} WHERE id=?", args)
        return {"ok": True, **self.loop_get(loop_id)}

    def loop_get(self, loop_id: str) -> Optional[dict]:
        _require_str(loop_id, "loop_id", max_len=2 * MAX_KEY_LEN)
        row = self.conn.execute(
            "SELECT * FROM loop_runs WHERE id=?", (loop_id,)).fetchone()
        return self._loop_row(row) if row else None

    def loop_list(self, project: str, *, chapter_n: Optional[int] = None,
                  limit: int = 20) -> List[dict]:
        """按创建时间倒序列出质量环；limit 默认 20、上限 100。"""
        _require_str(project, "project")
        sql = "SELECT * FROM loop_runs WHERE project=?"
        args: List[Any] = [project]
        if chapter_n is not None:
            args.append(_require_int(chapter_n, "chapter_n",
                                     minimum=0, maximum=MAX_CHAPTER_N))
            sql += " AND chapter_n=?"
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        args.append(_as_int(limit, 20, minimum=1, maximum=100))
        return [self._loop_row(r) for r in self.conn.execute(sql, args).fetchall()]

    def loop_metric_add(self, project: str, loop_id: str, round_no: int,
                        dimension: str, score: float, overall: float, *,
                        violations: Any = None,
                        content_hash: Optional[str] = None) -> dict:
        """登记一轮中一个维度的评分；overall 是该轮加权总分。"""
        self._require_project(project)
        loop_id = _require_str(loop_id, "loop_id", max_len=2 * MAX_KEY_LEN)
        loop = self.conn.execute(
            "SELECT project FROM loop_runs WHERE id=?", (loop_id,)).fetchone()
        if loop is None or loop["project"] != project:
            raise LongformError(f"质量环不存在：{loop_id}（项目 {project}）")
        round_no = _require_int(round_no, "round", minimum=1,
                                maximum=MAX_CHAPTER_N)
        dimension = _require_str(dimension, "dimension", max_len=MAX_KEY_LEN)
        score = _require_score(score, "score")
        overall = _require_score(overall, "overall")
        if content_hash is not None:
            content_hash = _require_str(content_hash, "content_hash",
                                        allow_empty=True, max_len=128)

        mid = _uid("lm")
        with self._tx():
            self.conn.execute(
                "INSERT INTO loop_metrics(id,project,loop_id,round,dimension,score,"
                "overall,violations_json,content_hash,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (mid, project, loop_id, round_no, dimension, score, overall,
                 None if violations is None else _dump(violations, "violations"),
                 content_hash, _now()))
        return {"ok": True, "id": mid, "loop_id": loop_id, "round": round_no,
                "dimension": dimension, "score": score, "overall": overall}

    def loop_metrics(self, loop_id: str) -> List[dict]:
        """按轮次升序列出一轮的全部维度评分。"""
        _require_str(loop_id, "loop_id", max_len=2 * MAX_KEY_LEN)
        rows = self.conn.execute(
            "SELECT * FROM loop_metrics WHERE loop_id=? "
            "ORDER BY round, created_at, id", (loop_id,)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["violations"] = _load_json(
                item.pop("violations_json", None), [])
            if not isinstance(item["violations"], list):
                item["violations"] = []
            out.append(item)
        return out

    # ---------------- style_profiles（文风蒸馏档案） ----------------
    def _style_profile_row(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        d["profile"] = _load_json(d.pop("profile_json", None), None)
        d["source"] = _load_json(d.pop("source_json", None), None)
        return d

    def style_profile_add(self, project: str, name: str, profile: Any,
                          source: Any = None) -> dict:
        self._require_project(project)
        name = _require_str(name, "name", max_len=MAX_NAME_LEN)
        spid = _uid("sp")
        with self._tx():
            self.conn.execute(
                "INSERT INTO style_profiles(id,project,name,profile_json,"
                "source_json,created_at) VALUES(?,?,?,?,?,?)",
                (spid, project, name, _dump(profile, "profile"),
                 None if source is None else _dump(source, "source"), _now()))
        return {"ok": True, **self.style_profile_get(project, spid)}

    def style_profile_get(self, project: str,
                          profile_id: Optional[str] = None) -> Any:
        """省略 id 返回列表（created_at 倒序）；给 id 返回对象或 None。"""
        _require_str(project, "project")
        if profile_id is None:
            rows = self.conn.execute(
                "SELECT * FROM style_profiles WHERE project=? "
                "ORDER BY created_at DESC, id DESC", (project,)).fetchall()
            return [self._style_profile_row(r) for r in rows]
        _require_str(profile_id, "profile_id", max_len=2 * MAX_KEY_LEN)
        row = self.conn.execute(
            "SELECT * FROM style_profiles WHERE project=? AND id=?",
            (project, profile_id)).fetchone()
        return self._style_profile_row(row) if row else None

    def style_profile_delete(self, project: str, profile_id: str) -> dict:
        self._require_project(project)
        profile_id = _require_str(profile_id, "profile_id", max_len=2 * MAX_KEY_LEN)
        with self._tx():
            cur = self.conn.execute(
                "DELETE FROM style_profiles WHERE project=? AND id=?",
                (project, profile_id))
            if cur.rowcount == 0:
                raise LongformError(
                    f"文风档案不存在：{profile_id}（项目 {project}）")
        return {"ok": True, "id": profile_id}

    # ---------------- handoffs（AI Studio 交接单） ----------------
    def handoff_create(self, project: str, kind: str, prompt: str, *,
                       ref_id: Optional[str] = None,
                       chapter_n: Optional[int] = None,
                       schema: Optional[Any] = None) -> dict:
        """创建一张 pending 交接单；prompt 是交给 AI Studio 的完整指令。"""
        self._require_project(project)
        kind = _require_enum(kind, "kind", HANDOFF_KINDS)
        prompt = _require_str(prompt, "prompt", max_len=MAX_TEXT_LEN)
        if ref_id is not None:
            ref_id = _require_str(ref_id, "ref_id", allow_empty=True,
                                  max_len=2 * MAX_KEY_LEN)
            ref_id = ref_id or None
        if chapter_n is not None:
            chapter_n = _require_int(chapter_n, "chapter_n",
                                     minimum=1, maximum=MAX_CHAPTER_N)
        if schema is not None and not isinstance(schema, (dict, str)):
            raise LongformError(
                f"参数 `schema` 必须是对象或 JSON 字符串，实际收到 "
                f"{type(schema).__name__}")
        schema_json = None
        if isinstance(schema, dict):
            schema_json = _dump(schema, "schema")
        elif isinstance(schema, str):
            schema_json = schema

        hid = _uid("ho")
        now = _now()
        with self._tx():
            self.conn.execute(
                "INSERT INTO handoffs(id,project,kind,ref_id,chapter_n,status,"
                "prompt,schema_json,result_json,result_hash,error,created_at,"
                "updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (hid, project, kind, ref_id, chapter_n, "pending", prompt,
                 schema_json, None, None, None, now, now))
        return {"ok": True, **self.handoff_get(hid)}

    def handoff_get(self, handoff_id: str) -> Optional[dict]:
        handoff_id = _require_str(handoff_id, "handoff_id",
                                  max_len=2 * MAX_KEY_LEN)
        row = self.conn.execute(
            "SELECT * FROM handoffs WHERE id=?", (handoff_id,)).fetchone()
        return _handoff_row(row) if row else None

    def handoff_list(self, project: str, status: Optional[str] = None,
                     limit: int = 50) -> List[dict]:
        """按创建时间倒序列出交接单；status 可筛选。"""
        self._require_project(project)
        sql = "SELECT * FROM handoffs WHERE project=?"
        args: List[Any] = [project]
        if status is not None:
            args.append(_require_enum(status, "status", HANDOFF_STATUSES))
            sql += " AND status=?"
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        args.append(_as_int(limit, 50, minimum=1, maximum=200))
        return [_handoff_row(r) for r in self.conn.execute(sql, args).fetchall()]

    def handoff_pending(self, project: str) -> List[dict]:
        """待处理交接单（pending / submitted），按创建时间升序。"""
        self._require_project(project)
        rows = self.conn.execute(
            "SELECT * FROM handoffs WHERE project=? AND status IN "
            "('pending','submitted') ORDER BY created_at ASC, id ASC",
            (project,)).fetchall()
        return [_handoff_row(r) for r in rows]

    def handoff_submit(self, handoff_id: str, result: Any, *,
                       status: str = "applied") -> dict:
        """落结果并推进状态；内容哈希相同且已 applied 时返回幂等标记。"""
        handoff_id = _require_str(handoff_id, "handoff_id",
                                  max_len=2 * MAX_KEY_LEN)
        status = _require_enum(status, "status", ("submitted", "applied"))
        h = self.handoff_get(handoff_id)
        if h is None:
            raise LongformError(f"交接单不存在：{handoff_id}")
        if h["status"] in ("cancelled", "rejected"):
            raise LongformError(
                f"交接单 {handoff_id} 已处于 {h['status']}，不能再提交")
        result_hash = _hash_json(result)
        if h["status"] == "applied" and h.get("result_hash") == result_hash:
            out = {"ok": True, "already_applied": True}
            out.update(h)
            return out

        payload = _dump(result, "result")
        with self._tx():
            self.conn.execute(
                "UPDATE handoffs SET result_json=?, result_hash=?, status=?,"
                " error=NULL, updated_at=? WHERE id=?",
                (payload, result_hash, status, _now(), handoff_id))
        out = {"ok": True, "already_applied": False}
        out.update(self.handoff_get(handoff_id))
        return out

    def handoff_fail(self, handoff_id: str, error: str) -> dict:
        """标记失败并保留可读原因；交接单仍可被新单替换。"""
        handoff_id = _require_str(handoff_id, "handoff_id",
                                  max_len=2 * MAX_KEY_LEN)
        error = _require_str(error, "error", max_len=MAX_CLAIM_LEN)
        if self.handoff_get(handoff_id) is None:
            raise LongformError(f"交接单不存在：{handoff_id}")
        with self._tx():
            self.conn.execute(
                "UPDATE handoffs SET status='failed', error=?, updated_at=? "
                "WHERE id=?", (error, _now(), handoff_id))
        return {"ok": True, **self.handoff_get(handoff_id)}

    def handoff_cancel(self, handoff_id: str) -> dict:
        """取消一张尚未落库的交接单；已 applied 的不允许取消。"""
        handoff_id = _require_str(handoff_id, "handoff_id",
                                  max_len=2 * MAX_KEY_LEN)
        h = self.handoff_get(handoff_id)
        if h is None:
            raise LongformError(f"交接单不存在：{handoff_id}")
        if h["status"] not in ("pending", "submitted"):
            raise LongformError(
                f"交接单 {handoff_id} 处于 {h['status']}，只有 pending/submitted "
                f"可以取消")
        with self._tx():
            self.conn.execute(
                "UPDATE handoffs SET status='cancelled', updated_at=? WHERE id=?",
                (_now(), handoff_id))
        return {"ok": True, **self.handoff_get(handoff_id)}

    # ---------------- style ----------------
    def style_set(self, pid: str, key: str, value: Any) -> dict:
        self._require_project(pid)
        _require_str(key, "key", max_len=MAX_KEY_LEN)
        with self._tx():
            self.conn.execute(
                "INSERT OR REPLACE INTO style(project,key,value_json) VALUES(?,?,?)",
                (pid, key, _dump(value, "value")))
        return {"ok": True, "key": key}

    def style_get(self, pid: str, key: Optional[str] = None) -> Any:
        self._require_project(pid)
        if key is not None:
            _require_str(key, "key", max_len=MAX_KEY_LEN)
            r = self.conn.execute(
                "SELECT value_json FROM style WHERE project=? AND key=?",
                (pid, key)).fetchone()
            return _load_json(r["value_json"], None) if r else None
        rows = self.conn.execute(
            "SELECT key,value_json FROM style WHERE project=?", (pid,)).fetchall()
        return {r["key"]: _load_json(r["value_json"], None) for r in rows}

    # ---------------- facts ----------------
    def fact_add(self, pid: str, claim: str, source: Optional[str] = None,
                 chapter: Optional[int] = None) -> dict:
        self._require_project(pid)
        _require_str(claim, "claim", max_len=MAX_CLAIM_LEN)
        if source is not None:
            _require_str(source, "source", allow_empty=True, max_len=MAX_CLAIM_LEN)
        if chapter is not None:
            chapter = _require_int(chapter, "chapter",
                                   minimum=0, maximum=MAX_CHAPTER_N)
        fid = _uid("ft")
        with self._tx():
            self.conn.execute(
                "INSERT INTO facts(project,id,claim,source,status,note,chapter)"
                " VALUES(?,?,?,?,?,?,?)",
                (pid, fid, claim, source, "pending", None, chapter))
        return {"ok": True, "id": fid, "status": "pending"}

    def fact_set_status(self, pid: str, fid: str, status: str,
                        note: Optional[str] = None) -> dict:
        self._require_project(pid)
        _require_str(fid, "id", max_len=2 * MAX_KEY_LEN)
        _require_enum(status, "status", FACT_STATUSES)
        if note is not None:
            _require_str(note, "note", allow_empty=True, max_len=MAX_CLAIM_LEN)
        with self._tx():
            cur = self.conn.execute(
                "UPDATE facts SET status=?, note=? WHERE project=? AND id=?",
                (status, note, pid, fid))
            if cur.rowcount == 0:
                raise LongformError(f"事实条目不存在：{fid}（项目 {pid}）")
        return {"ok": True, "id": fid, "status": status}

    def fact_list(self, pid: str, status: Optional[str] = None) -> List[dict]:
        self._require_project(pid)
        if status is not None:
            _require_enum(status, "status", FACT_STATUSES)
            rows = self.conn.execute(
                "SELECT * FROM facts WHERE project=? AND status=? ORDER BY id",
                (pid, status)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM facts WHERE project=? ORDER BY id", (pid,)).fetchall()
        return [dict(r) for r in rows]

    # ---------------- audits ----------------
    def audit_add(self, pid: str, report: dict,
                  chapter_n: Optional[int] = None) -> dict:
        """写一条不限章号的审计记录（chapter_n 可为空，如质量调权）。"""
        self._require_project(pid)
        _require_dict(report, "report")
        if chapter_n is not None:
            chapter_n = _require_int(chapter_n, "chapter_n",
                                     minimum=0, maximum=MAX_CHAPTER_N)
        aid = _uid("au")
        with self._tx():
            self.conn.execute(
                "INSERT INTO audits(project,id,chapter_n,report_json,created_at)"
                " VALUES(?,?,?,?,?)",
                (pid, aid, chapter_n, _dump(report, "report"), _now()))
        return {"ok": True, "id": aid}

    def audit_log(self, pid: str, chapter_n: int, report: dict) -> dict:
        self._require_project(pid)
        chapter_n = _require_int(chapter_n, "chapter_n",
                                 minimum=0, maximum=MAX_CHAPTER_N)
        _require_dict(report, "report")
        aid = _uid("au")
        with self._tx():
            self.conn.execute(
                "INSERT INTO audits(project,id,chapter_n,report_json,created_at)"
                " VALUES(?,?,?,?,?)",
                (pid, aid, chapter_n, _dump(report, "report"), _now()))
        return {"ok": True, "id": aid}

    def audit_list(self, pid: str, chapter_n: Optional[int] = None) -> List[dict]:
        self._require_project(pid)
        if chapter_n is not None:
            chapter_n = _require_int(chapter_n, "chapter_n",
                                     minimum=0, maximum=MAX_CHAPTER_N)
            rows = self.conn.execute(
                "SELECT * FROM audits WHERE project=? AND chapter_n=? ORDER BY created_at",
                (pid, chapter_n)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM audits WHERE project=? ORDER BY created_at",
                (pid,)).fetchall()
        out = []
        for r in rows:
            item = {"id": r["id"], "chapter_n": r["chapter_n"]}
            item.update(_load_json(r["report_json"], {}))
            out.append(item)
        return out

    # ---------------- config / export ----------------
    def config_set(self, pid: str, key: str, value: Any) -> dict:
        """写项目配置；``key`` 支持点号嵌套（``packet.token_budget``）。

        点号键让 ``context_estimate`` 给出的建议可以直接执行——
        只写一级键时 ``"packet.include_facts"`` 会变成一个谁也读不到的字面量。
        """
        self._require_project(pid)
        key = _require_str(key, "key", max_len=MAX_KEY_LEN)
        parts = [p.strip() for p in key.split(".")]
        if any(not p for p in parts):
            raise LongformError(f"参数 `key` 不能含空段：{key!r}")
        with self._tx():
            p = self.project_get(pid)
            cfg = _load_json(p["config_json"], {})
            if not isinstance(cfg, dict):
                cfg = {}
            cur = cfg
            for part in parts[:-1]:
                nxt = cur.get(part)
                if not isinstance(nxt, dict):
                    nxt = {}
                    cur[part] = nxt
                cur = nxt
            cur[parts[-1]] = value
            self.conn.execute("UPDATE projects SET config_json=? WHERE id=?",
                              (_dump(cfg, "config"), pid))
        return {"ok": True, "key": key, "value": value}

    def config_get(self, pid: str, key: Optional[str] = None) -> Any:
        """读项目配置；``key`` 支持点号取嵌套项（与 ``config_set`` 对称）。"""
        self._require_project(pid)
        if key is not None:
            key = _require_str(key, "key", max_len=MAX_KEY_LEN)
        p = self.project_get(pid)
        cfg = _load_json(p["config_json"], {}) if p else {}
        if key is None:
            return cfg
        cur: Any = cfg
        for part in (x.strip() for x in key.split(".")):
            if not isinstance(cur, dict):
                return None
            cur = cur.get(part)
        return cur

    def ledger_stats(self, pid: str) -> dict:
        self._require_project(pid)

        def c(sql: str, *a) -> int:
            return self.conn.execute(sql, (pid, *a)).fetchone()["c"]

        return {
            "entities": c("SELECT COUNT(*) c FROM entities WHERE project=?"),
            "events": c("SELECT COUNT(*) c FROM events WHERE project=?"),
            "foreshadow_open": c("SELECT COUNT(*) c FROM foreshadow WHERE project=? AND status='open'"),
            "foreshadow_resolved": c("SELECT COUNT(*) c FROM foreshadow WHERE project=? AND status='resolved'"),
            "chapters": c("SELECT COUNT(DISTINCT n) c FROM chapters WHERE project=?"),
            "total_words": c("SELECT COALESCE(SUM(LENGTH(content)),0) c FROM chapters "
                             "WHERE project=? AND (n,version) IN "
                             "(SELECT n,MAX(version) FROM chapters WHERE project=? GROUP BY n)", pid),
            "facts_pending": c("SELECT COUNT(*) c FROM facts WHERE project=? AND status='pending'"),
            "audits": c("SELECT COUNT(*) c FROM audits WHERE project=?"),
            # 大纲（加性新增，不影响既有键）。outline_nodes 计全部版本行，
            # 其余三个计「去重后的最新节点数」。
            "outline_master": c("SELECT COUNT(DISTINCT id) c FROM outline_nodes "
                                "WHERE project=? AND level='master'"),
            "outline_volumes": c("SELECT COUNT(DISTINCT id) c FROM outline_nodes "
                                 "WHERE project=? AND level='volume'"),
            "outline_chapters": c("SELECT COUNT(DISTINCT id) c FROM outline_nodes "
                                  "WHERE project=? AND level='chapter'"),
            "outline_nodes": c("SELECT COUNT(*) c FROM outline_nodes WHERE project=?"),
            "materials": c("SELECT COUNT(*) c FROM materials WHERE project=?"),
            "materials_unsourced": c("SELECT COUNT(*) c FROM materials "
                                     "WHERE project=? AND COALESCE(source,'')=''"),
            "sections": c("SELECT COUNT(DISTINCT chapter_n || ':' || idx) c "
                          "FROM sections WHERE project=?"),
        }

    def project_export(self, pid: str) -> dict:
        p = self.project_get(pid)
        if p is None:
            raise LongformError(f"项目不存在：{pid}")
        return {
            "project": p,
            "profile": self.project_get_profile(pid),
            "style": self.style_get(pid),
            "entities": self.bible_list(pid),
            "events": self.timeline_query(pid),
            "foreshadow": self.foreshadow_list(pid),
            "chapters": [self.chapter_get(pid, r["n"]) for r in self.chapter_list(pid)],
            "facts": self.fact_list(pid),
            "audits": self.audit_list(pid),
            "stats": self.ledger_stats(pid),
        }

    def close(self):
        self.conn.close()


class _Transaction:
    """``with store._tx():`` 事务上下文。"""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __enter__(self) -> "_Transaction":
        self.conn.execute("BEGIN IMMEDIATE")
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self.conn.execute("COMMIT")
        else:
            self.conn.execute("ROLLBACK")
        return False
