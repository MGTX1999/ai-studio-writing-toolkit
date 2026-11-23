"""MCP 工具定义（纯数据，无副作用）。

``TOOLS`` 的顺序对外是稳定的：客户端据此生成函数声明，``--list-tools``
也直接打印它。新增工具时在 ``config/registry.yaml`` 同步登记，
``scripts/check_config.py`` 会比对两边。
"""

from __future__ import annotations

from typing import Any, Dict


def _obj(props: Dict[str, Any], required: list) -> dict:
    return {"type": "object", "properties": props, "required": required}


_S = {"type": "string"}
_I = {"type": "integer"}
_B = {"type": "boolean"}
_O = {"type": "object"}


TOOLS = [
    # ---- 项目 ----
    {"name": "project_init", "description": "创建/重置一个写作项目（overwrite=true 会连同设定、章节等全部清空）；"
                                            "可选 profile 直接绑定一个作品变体",
     "inputSchema": _obj({"name": _S, "kind": _S, "config": _O, "overwrite": _B, "profile": _S},
                         ["name"])},
    {"name": "project_list", "description": "列出所有项目及章节数", "inputSchema": _obj({}, [])},
    {"name": "project_export", "description": "导出项目全量数据（设定/时间线/伏笔/章节/事实/审计）",
     "inputSchema": _obj({"project": _S}, ["project"])},
    {"name": "project_delete", "description": "彻底删除一个项目及其全部数据；需传 confirm 且必须与项目 id 完全一致",
     "inputSchema": _obj({"project": _S, "confirm": _S}, ["project", "confirm"])},

    # ---- 设定档 ----
    {"name": "bible_upsert", "description": "写入或更新一条设定（character/world/org/item/rule）",
     "inputSchema": _obj({"project": _S, "kind": _S, "name": _S, "data": _O},
                        ["project", "kind", "name", "data"])},
    {"name": "bible_get", "description": "读取一条设定",
     "inputSchema": _obj({"project": _S, "kind": _S, "name": _S}, ["project", "kind", "name"])},
    {"name": "bible_list", "description": "列出设定档（可按 kind 过滤）",
     "inputSchema": _obj({"project": _S, "kind": _S}, ["project"])},
    {"name": "bible_search", "description": "在设定档中关键词检索（% 与 _ 视作普通字符）",
     "inputSchema": _obj({"project": _S, "query": _S, "limit": _I}, ["project", "query"])},
    {"name": "entity_meta_get", "description": "读取一条设定的激活元数据（anchors/priority/"
                                                "content_hash/first_seen_chapter/last_seen_chapter）",
     "inputSchema": _obj({"project": _S, "kind": _S, "name": _S},
                         ["project", "kind", "name"])},
    {"name": "entity_meta_set", "description": "设置一条设定的激活元数据：anchors 是别名/关键词"
                                                "（最多 20 条，每条 ≤64 字），priority 为 "
                                                "always/high/normal，first_seen_chapter 为正整数；"
                                                "未传的字段保持不变，未知 data 键原样保留",
     "inputSchema": _obj({"project": _S, "kind": _S, "name": _S,
                          "anchors": {"type": "array", "items": _S},
                          "priority": _S, "first_seen_chapter": _I},
                         ["project", "kind", "name"])},

    # ---- 时间线 ----
    {"name": "timeline_add", "description": "添加时间线事件",
     "inputSchema": _obj({"project": _S, "title": _S, "chapter": _I, "seq": _I, "data": _O},
                        ["project", "title", "chapter"])},
    {"name": "timeline_query", "description": "按章节区间查询时间线（from_chapter 不得大于 to_chapter）",
     "inputSchema": _obj({"project": _S, "from_chapter": _I, "to_chapter": _I, "limit": _I},
                        ["project"])},

    # ---- 伏笔 ----
    {"name": "foreshadow_add", "description": "登记一条伏笔（due_chapter 不得早于 planted_chapter）",
     "inputSchema": _obj({"project": _S, "text": _S, "planted_chapter": _I, "due_chapter": _I},
                        ["project", "text", "planted_chapter"])},
    {"name": "foreshadow_list", "description": "列出伏笔（可按 status 过滤：open/resolved/dropped）",
     "inputSchema": _obj({"project": _S, "status": _S}, ["project"])},
    {"name": "foreshadow_resolve", "description": "回收/废弃一条伏笔（status：resolved/dropped；id 不存在时报错）",
     "inputSchema": _obj({"project": _S, "id": _S, "chapter": _I, "status": _S},
                        ["project", "id", "chapter"])},
    {"name": "foreshadow_overdue", "description": "列出逾期未收的伏笔",
     "inputSchema": _obj({"project": _S, "current": _I, "after": _I}, ["project", "current"])},

    # ---- 章节 ----
    {"name": "chapter_save", "description": "保存章节（自动递增 version，永不覆盖旧稿）。"
                                            "若项目已绑定变体或显式给了 export_dir，会同时把正文写到本地文件夹（自动落盘）；"
                                            "传 export=false 可关掉本次落盘",
     "inputSchema": _obj({"project": _S, "n": _I, "title": _S, "content": _S, "status": _S,
                          "data": _O, "export": _B, "export_dir": _S, "profile": _S},
                         ["project", "n", "content"])},
    {"name": "chapter_patch", "description": "对章节当前版本做精确局部替换：把 find 指定的原文换成 replace，"
                                            "其余正文逐字保留，并生成新版本（旧版仍可查/可 diff）。"
                                            "find 必须与正文完全一致；默认只改第 1 处，occurrence 指定第 N 处，"
                                            "all_matches=true 替换全部非重叠命中；preview=true 只返回差异摘要不写库；"
                                            "expected_version 可在版本已变化时拒绝覆盖。替换为空字符串表示删除该段",
     "inputSchema": _obj({"project": _S, "n": _I, "find": _S, "replace": _S,
                          "occurrence": _I, "all_matches": _B, "preview": _B,
                          "expected_version": _I, "status": _S,
                          "export": _B, "export_dir": _S, "profile": _S},
                         ["project", "n", "find", "replace"])},
    {"name": "chapter_get", "description": "读取章节（默认最新版，可指定 version）",
     "inputSchema": _obj({"project": _S, "n": _I, "version": _I}, ["project", "n"])},
    {"name": "chapter_list", "description": "列出全部章节及最新版状态",
     "inputSchema": _obj({"project": _S}, ["project"])},
    {"name": "chapter_versions", "description": "列出某章的所有历史版本",
     "inputSchema": _obj({"project": _S, "n": _I}, ["project", "n"])},
    {"name": "chapter_diff", "description": "对比某章两个版本的正文差异（默认首版 vs 最新版）",
     "inputSchema": _obj({"project": _S, "n": _I, "a": _I, "b": _I}, ["project", "n"])},

    # ---- 文风 ----
    {"name": "style_set", "description": "设置文风档案的某一项（pov/tone/banned_words/...）",
     "inputSchema": _obj({"project": _S, "key": _S, "value": {}}, ["project", "key", "value"])},
    {"name": "style_get", "description": "读取文风档案（可指定 key）",
     "inputSchema": _obj({"project": _S, "key": _S}, ["project"])},

    # ---- 文风蒸馏：从样本提炼九维档案 ----
    {"name": "style_distill", "description": "文风蒸馏：从 texts（字符串数组）或 chapter_ns（章号数组）提炼九维文风档案"
                                             "（词汇/句法/段落/修辞/情绪/视角/语调/格式/思维），并落库为可复用的 style_profile。"
                                             "需要至少 3 个样本 / 2000 字，单条 ≤5 万字、总量 ≤20 万字；样本太少或模型输出不合规时报错",
     "inputSchema": _obj({"project": _S,
                          "texts": {"type": "array", "items": _S},
                          "chapter_ns": {"type": "array", "items": _I},
                          "name": _S}, ["project"])},
    {"name": "style_profile_get", "description": "读取文风蒸馏档案：给 profile_id 返回完整档案；省略则返回列表（含 id/name/created_at/维度摘要）",
     "inputSchema": _obj({"project": _S, "profile_id": _S}, ["project"])},
    {"name": "style_profile_apply", "description": "把蒸馏档案写进项目 style（style_profile_id / style_prompt / 写作与思维基因 / 禁用词 / 局限）；"
                                                   "禁用词与原有列表合并去重，不覆盖其它 style 键，也不写正文",
     "inputSchema": _obj({"project": _S, "profile_id": _S}, ["project", "profile_id"])},
    {"name": "style_blind_test", "description": "文风盲测：用参考样本与候选样本的八项文体特征对照，输出 0–100 分与是否达标；"
                                                "只比文本特征、不使用来源/作者标签，写审计但不改文风档案与正文。"
                                                "参考/候选各至少 1 个样本 / 100 字，单条 ≤5 万字、总量 ≤20 万字",
     "inputSchema": _obj({"project": _S,
                          "reference_texts": {"type": "array", "items": _S},
                          "candidate_texts": {"type": "array", "items": _S},
                          "reference_chapter_ns": {"type": "array", "items": _I},
                          "candidate_chapter_ns": {"type": "array", "items": _I},
                          "profile_id": _S,
                          "threshold": {"type": "number"}}, ["project"])},

    # ---- 事实 ----
    {"name": "fact_add", "description": "登记一条待核事实",
     "inputSchema": _obj({"project": _S, "claim": _S, "source": _S, "chapter": _I},
                        ["project", "claim"])},
    {"name": "fact_set_status", "description": "更新事实核查状态（pending/verified/disputed/rejected）",
     "inputSchema": _obj({"project": _S, "id": _S, "status": _S, "note": _S},
                        ["project", "id", "status"])},
    {"name": "fact_list", "description": "列出事实清单（可按 status 过滤）",
     "inputSchema": _obj({"project": _S, "status": _S}, ["project"])},

    # ---- 审计 ----
    {"name": "audit_log", "description": "记录一次章节审查报告",
     "inputSchema": _obj({"project": _S, "chapter_n": _I, "report": _O},
                        ["project", "chapter_n", "report"])},
    {"name": "audit_list", "description": "查看审计报告（可按章节过滤）",
     "inputSchema": _obj({"project": _S, "chapter_n": _I}, ["project"])},

    # ---- 大纲（总纲要 → 卷纲要 → 章纲要 → 正文）----
    {"name": "outline_set", "description": "写入/修订一个大纲节点：level=master 总纲 | volume 卷纲 | chapter 章纲。"
                                           "同一 id 再写一次即修订（version+1，旧版本永久保留）；"
                                           "省略 id 时自动命名（master / v01 / c012）",
     "inputSchema": _obj({"project": _S, "level": _S, "id": _S, "parent_id": _S, "title": _S,
                          "body": _S, "status": _S, "seq": _I, "chapter_n": _I,
                          "start_chapter": _I, "end_chapter": _I, "data": _O},
                         ["project", "level"])},
    {"name": "outline_get", "description": "读取一个大纲节点（默认最新版，可指定 version）",
     "inputSchema": _obj({"project": _S, "id": _S, "version": _I}, ["project", "id"])},
    {"name": "outline_list", "description": "列出大纲节点（可按 level / parent_id / status 过滤，只返回每个 id 的最新版）",
     "inputSchema": _obj({"project": _S, "level": _S, "parent_id": _S, "status": _S}, ["project"])},
    {"name": "outline_tree", "description": "一次取回整棵大纲树（master / volumes / chapters）；"
                                            "include_body 默认 false，避免几万字灌进上下文",
     "inputSchema": _obj({"project": _S, "include_body": _B}, ["project"])},
    {"name": "outline_next", "description": "判定「现在该做什么」：返回当前阶段、缺什么、下一章号、建议工具与专家。"
                                            "每一级大纲开工前先调它，不要自己判断阶段",
     "inputSchema": _obj({"project": _S, "profile": _S}, ["project"])},
    {"name": "outline_brief", "description": "取第 N 章的章纲，可直接喂 packet_assemble(brief=...)。"
                                             "新表读不到时回退读旧章节的 data.brief（标 legacy-chapter-data）",
     "inputSchema": _obj({"project": _S, "chapter_n": _I}, ["project", "chapter_n"])},
    {"name": "outline_versions", "description": "列出某个大纲节点的全部历史版本",
     "inputSchema": _obj({"project": _S, "id": _S}, ["project", "id"])},
    {"name": "outline_delete", "description": "删除一个大纲节点及其全部版本；confirm 必须与 id 完全一致，有子节点时拒绝",
     "inputSchema": _obj({"project": _S, "id": _S, "confirm": _S}, ["project", "id", "confirm"])},

    # ---- 阶段建议 ----
    {"name": "stage_advice", "description": "给出某个创作阶段该用什么 LLM 的建议，并附带阶段剧本"
                                            "（该找哪个专家、用哪个技能、调哪些工具、交什么产物）。"
                                            "省略 stage 则返回全部阶段总表；project 可选，传了会套用项目与变体的覆盖",
     "inputSchema": _obj({"stage": _S, "project": _S, "profile": _S}, [])},

    # ---- 素材库（可追溯来源）----
    {"name": "material_add", "description": "登记一条写作素材（quote 摘抄 / reference 参考资料 / idea 灵感 / research 背景调研）。"
                                            "source 填原始出处（本地路径 / URL / ima://<知识库id>/<条目id>），"
                                            "source_kind 省略时会按 source 形态自动判定",
     "inputSchema": _obj({"project": _S, "title": _S, "content": _S, "kind": _S,
                          "source": _S, "source_kind": _S,
                          "tags": {"type": "array", "items": _S},
                          "chapter": _I, "data": _O},
                         ["project", "title"])},
    {"name": "material_get", "description": "读取一条素材（含来源与标签）",
     "inputSchema": _obj({"project": _S, "id": _S}, ["project", "id"])},
    {"name": "material_update", "description": "就地更新素材标题、正文、标签和证据元数据；"
                                               "用于题录卡补正文，source 不可借此伪造",
     "inputSchema": _obj({"project": _S, "id": _S, "title": _S, "content": _S,
                          "tags": {"type": "array", "items": _S}, "data": _O},
                         ["project", "id"])},
    {"name": "material_audit", "description": "素材证据审计：E0 题录/来源待补、E1 摘要或未核、"
                                               "E2 正文可用、E3 交叉核验；只评估可核查程度，不打质量分",
     "inputSchema": _obj({"project": _S, "level": _S}, ["project"])},
    {"name": "material_list", "description": "浏览素材（可按 kind / tag / chapter 过滤）；传 query 时统一转交 kb_search，"
                                               "不再维护第二套检索语义",
     "inputSchema": _obj({"project": _S, "kind": _S, "tag": _S, "chapter": _I,
                          "query": _S, "limit": _I}, ["project"])},
    {"name": "material_delete", "description": "删除一条素材；confirm 必须与素材 id 完全一致",
     "inputSchema": _obj({"project": _S, "id": _S, "confirm": _S}, ["project", "id", "confirm"])},

    # ---- 逐段写作 ----
    {"name": "section_save", "description": "写一章里的某一段（idx 从 1 开始）；同段再写即修订（version+1）。"
                                            "段级落稿让「某段写歪了」只需重修那一段",
     "inputSchema": _obj({"project": _S, "n": _I, "idx": _I, "content": _S,
                          "title": _S, "status": _S, "data": _O},
                         ["project", "n", "idx", "content"])},
    {"name": "section_get", "description": "读取某章某一段（默认最新版，可指定 version）",
     "inputSchema": _obj({"project": _S, "n": _I, "idx": _I, "version": _I},
                         ["project", "n", "idx"])},
    {"name": "section_list", "description": "列出某章各段的最新版概览（段序、版本、字数、状态）",
     "inputSchema": _obj({"project": _S, "n": _I}, ["project", "n"])},
    {"name": "section_merge", "description": "把某章各段最新版拼成整章并写入 chapter_save；"
                                             "段落不连续或有空段时拒绝合并（宁可不拼，也不拼出缺段的正文）",
     "inputSchema": _obj({"project": _S, "n": _I, "title": _S, "separator": _S,
                          "status": _S}, ["project", "n"])},
    {"name": "section_delete", "description": "删除某一段及其全部版本；confirm 必须是 \"章号:段序\"，如 \"12:3\"",
     "inputSchema": _obj({"project": _S, "n": _I, "idx": _I, "confirm": _S},
                         ["project", "n", "idx", "confirm"])},

    # ---- 大纲版本对比 ----
    {"name": "outline_diff", "description": "对比同一大纲节点的两个版本（默认首版 vs 最新版），"
                                            "并给出变更字段与状态迁移；与 chapter_diff 同构",
     "inputSchema": _obj({"project": _S, "id": _S, "a": _I, "b": _I}, ["project", "id"])},

    # ---- 上下文估算 ----
    {"name": "context_estimate", "description": "预估第 N 章输入包的上下文账：各小节字符数/约 token 数/占比、"
                                                "是否超预算、超了多少、可执行的省 token 手段。"
                                                "只读不写，与 packet_assemble 看到的是同一份数据；"
                                                "include_materials=false 可临时不计素材小节",
     "inputSchema": _obj({"project": _S, "chapter_n": _I, "brief": _S,
                          "token_budget": _I, "include_materials": _B},
                         ["project", "chapter_n"])},

    # ---- 世界书激活轨迹 ----
    {"name": "context_trace", "description": "解释第 N 章输入包携带了哪些设定、丢弃了哪些、为什么"
                                               "（always/anchor/name/recent/fallback_all）以及预算占用；"
                                               "省略 chapter_n 取最新章，brief 用于复现同一章的激活",
     "inputSchema": _obj({"project": _S, "chapter_n": _I, "brief": _S},
                         ["project"])},

    # ---- 工作目录分目录导出 ----
    {"name": "workspace_export", "description": "把大纲/章节草稿/素材/参考资料/成品五类产物一次性导出到指定工作目录下的"
                                                "五个子文件夹。dir 为绝对路径时原样使用，相对路径相对当前工作目录",
     "inputSchema": _obj({"project": _S, "dir": _S,
                          "include": {"type": "array", "items": _S}}, ["project", "dir"])},

    # ---- 核心：输入包 ----
    {"name": "packet_assemble", "description": "组装第 N 章的上下文输入包（设定/时间线/伏笔/事实/本章素材/上章结尾），"
                                               "这是每章开写前必须调用的工具；显式传 token_budget 时优先于项目配置，"
                                               "include_materials=false 可临时关闭素材小节",
     "inputSchema": _obj({"project": _S, "chapter_n": _I, "brief": _S,
                          "token_budget": _I, "include_materials": _B},
                         ["project", "chapter_n"])},

    # ---- 统计与配置 ----
    {"name": "ledger_stats", "description": "项目健康度统计（设定数/伏笔未收/字数/待核事实）",
     "inputSchema": _obj({"project": _S}, ["project"])},
    {"name": "config_set", "description": "设置项目级配置（后期调整用）；key 支持点号嵌套，如 packet.token_budget",
     "inputSchema": _obj({"project": _S, "key": _S, "value": {}}, ["project", "key", "value"])},
    {"name": "config_get", "description": "读取项目级配置（key 可用点号取嵌套项）",
     "inputSchema": _obj({"project": _S, "key": _S}, ["project"])},

    # ---- 作品变体（独立文件夹，用于不同写作方向或二创）----
    {"name": "profile_list", "description": "列出所有作品变体；传 project 时会标出该项目当前绑定的变体",
     "inputSchema": _obj({"project": _S}, [])},
    {"name": "profile_get", "description": "读取一个变体的完整配置（overrides 覆盖片段 + 解析后的正文保存目录）",
     "inputSchema": _obj({"name": _S}, ["name"])},
    {"name": "profile_create", "description": "从模板复制出一个新变体（添加变体）；export_dir 可填任意本地路径",
     "inputSchema": _obj({"name": _S, "description": _S, "base": _S, "export_dir": _S}, ["name"])},
    {"name": "profile_delete", "description": "删除整个变体文件夹（删除变体）；confirm 必须与变体名完全一致，"
                                              "可先 dry_run=true 预览将删除多少文件",
     "inputSchema": _obj({"name": _S, "confirm": _S, "dry_run": _B}, ["name"])},
    {"name": "profile_bind", "description": "把变体绑定到项目（后期微调的入口）；name 传空字符串表示解绑。"
                                            "绑定后 packet_assemble 会套用该变体的覆盖配置",
     "inputSchema": _obj({"project": _S, "name": _S}, ["project", "name"])},
    {"name": "profile_set_export_dir", "description": "指定变体的正文保存目录（可填任意本地绝对路径，如 D:/我的小说/雾港）",
     "inputSchema": _obj({"name": _S, "dir": _S}, ["name", "dir"])},
    {"name": "chapter_export", "description": "把章节正文导出到本地文件夹（手动补导）；省略 n 则导出全部章节最新版",
     "inputSchema": _obj({"project": _S, "n": _I, "version": _I, "dir": _S, "profile": _S}, ["project"])},

    # ---- 运行环境设置与直接调用模型 ----
    {"name": "settings_get", "description": "读取运行环境设置（模型服务 / IMA）与校验 warnings；"
                                            "密钥只返回掩码（如 sk-***a1b2），明文永不出现在返回值里",
     "inputSchema": _obj({}, [])},
    {"name": "settings_set", "description": "更新运行环境设置（llm / ima 至少给一个）；"
                                            "带 *** 的 api_key 视为未修改，校验不通过时拒绝写入",
     "inputSchema": _obj({"llm": _O, "ima": _O}, [])},
    {"name": "llm_generate", "description": "向已配置的 OpenAI 兼容模型服务发起一次对话补全；"
                                            "单次请求、不重试，未配置时报错并说明缺哪些字段。"
                                            "传 project 且该项目处于 AI Studio 模式时改为创建 "
                                            "llm_generate 交接单，返回 awaiting_input，不调用模型",
     "inputSchema": _obj({"prompt": _S, "system": _S, "project": _S,
                          "temperature": {"type": "number"},
                          "max_tokens": _I, "timeout": _I}, ["prompt"])},

    # ---- 知识库（分块导入 + 全文检索）----
    {"name": "kb_import", "description": "把文本 / 单个文件 / 整个目录导入素材库并按长度分块"
                                         "（每条 ≤ MAX_MATERIAL_LEN，共享同一 source）；"
                                         "来源优先级 dir > path > content，三者皆无时报错",
     "inputSchema": _obj({"project": _S, "title": _S, "content": _S, "path": _S,
                          "dir": _S, "kind": _S, "source": _S, "source_kind": _S,
                          "tags": {"type": "array", "items": _S}, "chapter": _I},
                         ["project"])},
    {"name": "kb_search", "description": "素材库统一检索入口：三字以上走 FTS5/BM25，两字中文或多词自动补 LIKE，"
                                         "零结果也会兜底；返回高亮片段、score、matched_terms、match_reason"
                                         "与来源/标签，按同来源同内容去重；支持 kind/tag/chapter/limit，"
                                         "以及素材分类 category 与同义词扩展 expand_synonyms",
     "inputSchema": _obj({"project": _S, "query": _S, "kind": _S, "tag": _S,
                          "chapter": _I, "limit": _I, "category": _S,
                          "expand_synonyms": _B}, ["project", "query"])},
    {"name": "kb_categories", "description": "返回素材地图（写作教程/人物设定/大纲情节/描写技法/世界观/范文/其他）"
                                             "与同义词表，供检索时选分类、理解同义词扩展",
     "inputSchema": _obj({}, [])},

    # ---- 引用与素材：引证登记与验真（只写 citations，不改正文）----
    {"name": "citation_add", "description": "登记一条引证：claim 是正文里的断言，source 可为素材 id"
                                            "（mt_...）、章节定位（ch:3 或纯数字）或原始来源文本；"
                                            "chapter_n 可省略",
     "inputSchema": _obj({"project": _S, "chapter_n": _I, "claim": _S,
                          "source": _S, "locator": _S},
                         ["project", "claim"])},
    {"name": "citation_verify", "description": "验真一条引证：解析 source 取原文，规范化后精确/模糊匹配，"
                                               "把 verified/evidence 写回 citations；不改章节正文",
     "inputSchema": _obj({"project": _S, "citation_id": _S},
                         ["project", "citation_id"])},
    {"name": "citation_list", "description": "列出引证（按创建时间升序）；可按 chapter_n / verified 过滤，"
                                             "limit 默认 100、上限 500",
     "inputSchema": _obj({"project": _S, "chapter_n": _I, "verified": _B,
                          "limit": _I}, ["project"])},

    # ---- IMA 知识库（未配置时降级）----
    {"name": "ima_search", "description": "搜索 IMA 知识库；未配置时降级返回 configured=false 与说明，"
                                          "不抛错，本地知识库照常可用。kb_id 省略时自动检索已配置/前 8 个知识库",
     "inputSchema": _obj({"query": _S, "limit": _I, "kb_id": _S}, ["query"])},
    {"name": "ima_fetch", "description": "按 id 读取 IMA 条目；save=true 时落库为素材"
                                         "（source 形如 ima://<kb>/<id>，此时必须给 project）。"
                                         "订阅库返回 220030 时只落题录卡，不伪造正文；"
                                         "若用户已从客户端复制正文，可传 content 直接落库",
     "inputSchema": _obj({"id": _S, "project": _S, "save": _B,
                          "title": _S, "content": _S,
                          "tags": {"type": "array", "items": _S}, "chapter": _I}, ["id"])},
    {"name": "ima_import_urls", "description": "把一组网页 URL 批量导入 IMA 知识库（纯 HTTP）。"
                                               "本地先校验非空、≤50 条、必须是 http/https；"
                                               "未配置 IMA 时返回 ok=false 与原因，不抛错",
     "inputSchema": _obj({"urls": {"type": "array", "items": _S},
                          "kb_id": _S}, ["urls"])},
    {"name": "ima_upload", "description": "上传本地文件到 IMA 知识库：preflight → 重名检查 → "
                                          "create_media → COS → add_knowledge。文件必须存在、≤50MB；"
                                          "未配置或缺少 Node/COS 脚本时明确降级并返回原因，不抛未捕获异常",
     "inputSchema": _obj({"path": _S, "kb_id": _S}, ["path"])},

    # ---- 章节摘要与故事状态（省 token）----
    {"name": "summary_set", "description": "写入 / 覆盖某章的滚动摘要；summary 传空串表示删除该章摘要。"
                                           "摘要可在输入包中替代整章正文，显著降低 token 占用",
     "inputSchema": _obj({"project": _S, "chapter_n": _I, "summary": _S},
                         ["project", "chapter_n", "summary"])},
    {"name": "summary_list", "description": "按章号区间（含端点）列出章节摘要，供输入包用摘要替代整章正文",
     "inputSchema": _obj({"project": _S, "from_chapter": _I, "to_chapter": _I},
                         ["project"])},
    {"name": "story_state_set", "description": "写入一份故事状态（character-state / next-hook / arc-status 等）；"
                                               "value 传 null 表示删除该键",
     "inputSchema": _obj({"project": _S, "key": _S, "value": {}},
                         ["project", "key", "value"])},
    {"name": "story_state_get", "description": "读取一份故事状态；不存在或已删除时返回 null",
     "inputSchema": _obj({"project": _S, "key": _S}, ["project", "key"])},
    {"name": "story_state_list", "description": "列出项目全部故事状态（按 key 排序），用于下一章输入包",
     "inputSchema": _obj({"project": _S}, ["project"])},

    # ---- v1.10：字数统计 / 省 token / 修订建议 / 灵感访谈 ----
    {"name": "word_stats", "description": "字数统计：不传 chapter_n 返回项目逐章最新版总表与总量；"
                                         "传 chapter_n 返回该章单章统计。字数=去掉所有空白后的字符数（含标点），"
                                         "同时给原始字符、CJK、标点、段落、句数、约 token 与阅读分钟；只读不写",
     "inputSchema": _obj({"project": _S, "chapter_n": _I}, ["project"])},
    {"name": "chapter_digest", "description": "用规则抽取生成章节摘要（首句/末句必留，按位置、长度、实体与悬念词打分），"
                                            "默认只预览不写库；save=true 才写入摘要。all_missing=true 批量补齐"
                                            "所有原本无摘要章节；已有摘要默认不覆盖，需显式 overwrite=true",
     "inputSchema": _obj({"project": _S, "chapter_n": _I, "all_missing": _B,
                          "max_chars": _I, "save": _B, "overwrite": _B}, ["project"])},
    {"name": "token_audit", "description": "项目 token 账：统计最近 lookback 章的全量正文 tokens、摘要 tokens、"
                                          "可省 tokens 与摘要覆盖率；缺摘要章节按目标 600 字估算。"
                                          "传 chapter_n 时额外复用输入包预算账；只读不写",
     "inputSchema": _obj({"project": _S, "chapter_n": _I, "lookback": _I}, ["project"])},
    {"name": "revision_plan", "description": "对已有章节做规则体检并给修订建议：结构 findings、超长段落/句、"
                                            "重复句、段落开头重复、章节过短/过长；每项含证据、原因、改法。"
                                            "只有唯一可定位的超长段落会生成 patch，可交给 chapter_patch 应用；"
                                            "这是规则体检，不是文学评分",
     "inputSchema": _obj({"project": _S, "n": _I, "max_items": _I}, ["project", "n"])},
    {"name": "inspiration_interview", "description": "灵感访谈出题：结合项目人物、章节、未收伏笔与主题，"
                                                   "生成 3–10 个结构化问题（欲望/阻力/秘密/意象/反转/感官/关系/章末钩子）；"
                                                   "只出题，不写库，不调用模型",
     "inputSchema": _obj({"project": _S, "chapter_n": _I, "theme": _S, "count": _I},
                         ["project"])},
    {"name": "inspiration_synthesize", "description": "把作者回答组合成 1–4 张灵感卡（目标×阻力、秘密与关系反转、"
                                                     "可复用意象、中段失败与章末钩子）；save=true 时落库为"
                                                     " materials(kind=idea, source_kind=dialog, source=灵感访谈)。"
                                                     "answers 传 [{id,category,question,answer}] 数组",
     "inputSchema": _obj({"project": _S, "answers": {"type": "array", "items": _O},
                          "chapter_n": _I, "theme": _S, "save": _B,
                          "tags": {"type": "array", "items": _S}},
                         ["project", "answers"])},

    # ---- 叙事工作台：合规 / OOC / 角色会诊 / 妙招 / AI 声明 ----
    {"name": "compliance_check", "description": "三层合规预检（词层/语义层/事件三元组），"
                                                 "返回红黄灰风险、改写方向与 export_blocked；"
                                                 "save=true 时写入审校记录（kind=compliance）",
     "inputSchema": _obj({"project": _S, "text": _S, "chapter_n": _I, "save": _B},
                         ["project", "text"])},
    {"name": "voice_check", "description": "按人物卡里的 voice 语言指纹检查对白/独白是否 OOC；"
                                           "profile 需先用 bible_upsert 写入 character 的 data.voice",
     "inputSchema": _obj({"project": _S, "character": _S, "text": _S,
                          "chapter_n": _I, "save": _B},
                         ["project", "character", "text"])},
    {"name": "role_deck_list", "description": "列出叙事工作台的 8 个会诊角色（商业编辑/结构/人物/台词/连续性/平台/毒舌读者/类型写手）",
     "inputSchema": _obj({}, [])},
    {"name": "trick_list", "description": "列出叙事工作台的 12 个写作妙招及适用场景、步骤与产出要求",
     "inputSchema": _obj({}, [])},
    {"name": "role_prompt", "description": "把选定角色的会诊要求组装成可直接喂给任意模型的结构化提示词",
     "inputSchema": _obj({"role": _S, "text": _S, "context": _S}, ["role", "text"])},
    {"name": "trick_prompt", "description": "把选定妙招组装成可直接喂给任意模型的结构化提示词",
     "inputSchema": _obj({"trick": _S, "text": _S, "context": _S}, ["trick", "text"])},
    {"name": "review_chain_prompt", "description": "生成四步审稿提示链：证据提取 → 角色审稿 → 最小修订 → 完成前自检；"
                                                   "每步有独立输出契约、停止条件和交接字段，不调用外部模型",
     "inputSchema": _obj({"role": _S, "text": _S, "context": _S}, ["text"])},
    {"name": "ai_declaration", "description": "按项目统计生成 AI 使用声明草稿（Markdown），供投稿/平台说明使用",
     "inputSchema": _obj({"project": _S}, ["project"])},

    # ---- 叙事工作台 v1.6：章节树 / 体检 / 串声 / 图谱 / 预期 / 流水线 / 申诉 ----
    {"name": "chapter_tree", "description": "返回章节树：每章状态、版本、字数、合规风险、"
                                             "到期伏笔数与素材数；左侧工作台徽标的唯一数据源",
     "inputSchema": _obj({"project": _S}, ["project"])},
    {"name": "structure_check", "description": "章节结构体检：对白占比、平均句长、场景切换、章末钩子、"
                                                "主题句外显度、段落开头重复与 10 段情绪曲线；"
                                                "text 省略时读取 chapter_n 的正文",
     "inputSchema": _obj({"project": _S, "text": _S, "chapter_n": _I}, ["project"])},
    {"name": "voice_cross_check", "description": "整章对白归属交叉检查：按引号/角色标注切分，"
                                                  "逐句比对所有人物的语言指纹，输出“这句更像谁”、"
                                                  "分差、置信度与疑似串声；save=true 写审校记录",
     "inputSchema": _obj({"project": _S, "text": _S, "chapter_n": _I, "save": _B},
                         ["project"])},
    {"name": "ooc_check", "description": "OOC 补全检查：语言指纹 + 行为红线 + 关系约束 + 能力边界，"
                                          "并与 story_state.character-state 交叉核对；"
                                          "规则来自人物卡 data.ooc / relations / ability_limits",
     "inputSchema": _obj({"project": _S, "character": _S, "text": _S,
                          "chapter_n": _I, "save": _B},
                         ["project", "character"])},
    {"name": "story_graph", "description": "故事图谱：view=relations 人物一跳关系 / foreshadow 伏笔链路 / "
                                            "timeline 双轨时间线 / materials 素材双向链接与去重 / all 全部；"
                                            "character 用于人物关系聚焦",
     "inputSchema": _obj({"project": _S, "view": _S, "character": _S}, ["project"])},
    {"name": "expectation_add", "description": "登记一条读者预期：state=meet 满足 / delay 延迟 / "
                                                 "dimension 换维 / violate 违背；可补 setup 承诺、trigger 触发、"
                                                 "payoff 兑现事件与 payoff_evidence 兑现证据",
     "inputSchema": _obj({"project": _S, "chapter_n": _I, "state": _S, "note": _S,
                          "setup": _S, "trigger": _S, "payoff": _S,
                          "payoff_evidence": _S},
                         ["project", "chapter_n", "state"])},
    {"name": "expectation_list", "description": "列出预期账本与四态统计：连续同态告警、违背数、"
                                                  "配额与最近一章状态",
     "inputSchema": _obj({"project": _S, "chapter_n": _I}, ["project"])},
    {"name": "expectation_audit", "description": "审计预期承诺记录：满足缺兑现证据、延迟缺触发条件、"
                                                  "换维/违背缺理由；只查记录完整性，不自动判定文学性兑现",
     "inputSchema": _obj({"project": _S, "chapter_n": _I}, ["project"])},
    {"name": "pipeline_status", "description": "本章写作流水线：细纲 → 组包 → 正文 → 合规 → 声线 → 导出，"
                                                "返回每步状态、下一步与完成百分比",
     "inputSchema": _obj({"project": _S, "chapter_n": _I}, ["project"])},
    {"name": "appeal_pack", "description": "生成第 N 章申诉包 ZIP：全部版本、相邻 diff、合规/声线报告、"
                                            "AI 声明与 README；只打包本地已有证据，不做申诉成功承诺",
     "inputSchema": _obj({"project": _S, "chapter_n": _I}, ["project", "chapter_n"])},
    {"name": "ima_catalog_search", "description": "按关键词检索 IMA 知识库题录（media_id/标题/所属库），"
                                                   "用于订阅库只取标题与来源；正文需客户端导出",
     "inputSchema": _obj({"query": _S, "kb_id": _S, "limit": _I}, ["query"])},
    {"name": "ima_export_scan", "description": "扫描 IMA 客户端导出目录（docx/txt/md/html），"
                                                "切块去重后导入素材库，来源记为 ima-export://<文件名>",
     "inputSchema": _obj({"project": _S, "dir": _S, "recursive": _B,
                          "kind": _S, "tags": {"type": "array", "items": _S},
                          "chapter": _I}, ["project"])},

    # ---- 自动化与账本：单章流水线 / L0 检查 / 调用账本 ----
    {"name": "run_start", "description": "启动「下一章」的单章自动流水线（plan→assemble→draft→lint→summary→commit）。"
                                         "默认开启人工闸门，跑完停在 awaiting_human，需 run_resume 裁决；"
                                         "policy 可覆盖 require_human_gate / budget_tokens / target_words / temperature 等",
     "inputSchema": _obj({"project": _S, "policy": _O}, ["project"])},
    {"name": "run_status", "description": "读取一次运行的当前状态、六步检查点与本次 token 账",
     "inputSchema": _obj({"run_id": _S}, ["run_id"])},
    {"name": "run_list", "description": "列出运行记录（可按 project / status 过滤，默认最近 50 条）",
     "inputSchema": _obj({"project": _S, "status": _S, "limit": _I}, [])},
    {"name": "run_resume", "description": "推进一次运行：人工闸门下只有 decision=approve 才会定稿，"
                                          "reject 取消运行；省略 decision 则从断点继续执行",
     "inputSchema": _obj({"run_id": _S,
                          "decision": {"type": "string", "enum": ["approve", "reject"]}},
                         ["run_id"])},
    {"name": "run_cancel", "description": "取消一次未完成的运行；已完成 / 已取消的运行原样返回",
     "inputSchema": _obj({"run_id": _S}, ["run_id"])},
    {"name": "run_batch_start", "description": "整本批跑：从下一章起连续写 chapters 章，父运行 kind=batch 汇总子运行。"
                                               "policy 同单章，另支持 pause_on_blocked（默认 true）；"
                                               "每章结束检查人工闸门与整批 token 预算",
     "inputSchema": _obj({"project": _S, "chapters": _I, "policy": _O},
                         ["project", "chapters"])},
    {"name": "run_batch_status", "description": "读取批次父运行、逐章子运行（id/status/chapter/usage）"
                                                "与整批 token 汇总",
     "inputSchema": _obj({"batch_id": _S}, ["batch_id"])},
    {"name": "run_batch_resume", "description": "恢复批次：先推进当前子运行（人工闸门用 decision 裁决），"
                                                "子运行完成后继续下一章；仍失败则父运行保持 blocked",
     "inputSchema": _obj({"batch_id": _S,
                          "decision": {"type": "string",
                                       "enum": ["approve", "reject"]}},
                         ["batch_id"])},
    {"name": "run_batch_cancel", "description": "取消整本批次与当前子运行；已结束的批次原样返回",
     "inputSchema": _obj({"batch_id": _S}, ["batch_id"])},
    {"name": "lint_check", "description": "对某章最新版跑 L0 确定性检查（禁用词 / 待核事实 / 字数 / AI 味 / "
                                          "伏笔逾期），只报告不改正文，并把结果落库",
     "inputSchema": _obj({"project": _S, "chapter_n": _I}, ["project", "chapter_n"])},
    {"name": "usage_report", "description": "实际 LLM 调用账本：按章 / 模型 / 天 / 运行聚合输入、输出、"
                                            "缓存 token 与成本；by 默认 chapter",
     "inputSchema": _obj({"project": _S,
                          "by": {"type": "string",
                                 "enum": ["chapter", "model", "day", "run"]}},
                         ["project"])},

    # ---- 质量环：先 L0，再评分 / 修复 ----
    {"name": "loop_start", "description": "质量环：先跑 L0 确定性检查（block 即停、不调模型），"
                                          "再按 7 个维度评分并要求模型定向修复；"
                                          "apply=false（默认）不写正文，只返回最佳文本，apply=true 才写新版本",
     "inputSchema": _obj({"project": _S, "chapter_n": _I, "max_rounds": _I,
                          "threshold": {"type": "number"}, "apply": _B},
                         ["project"])},
    {"name": "loop_report", "description": "读取质量环结果：给 loop_id 返回该次运行与逐维评分；"
                                            "否则按 project / chapter_n 返回最近 limit 次运行汇总",
     "inputSchema": _obj({"project": _S, "loop_id": _S, "chapter_n": _I,
                          "limit": _I}, ["project"])},
    {"name": "loop_meta", "description": "质量环复盘：最近 window 次已完成运行的均分、通过率、"
                                          "高频违规与改进建议；只出建议，不写库、不改阈值",
     "inputSchema": _obj({"project": _S, "window": _I}, ["project"])},
    {"name": "loop_meta_apply", "description": "应用质量环调权建议：校验 threshold 6–10、"
                                               "权重合计 10±0.01 / 单项 1.0–4.0 / 单次变化 ≤0.3、"
                                               "banned_add ≤50 条；confirm=false 只预览不写库，"
                                               "confirm=true 才写 quality 配置与 banned_words 并记录审计",
     "inputSchema": _obj({"project": _S, "changes": _O, "confirm": _B},
                         ["project", "changes"])},

    # ---- AI Studio 桥接：读取网页设置 / 导出提示词 / 导入设置补丁 ----
    {"name": "studio_pack_get", "description": "AI Studio 模式下读取网页里设置的文风 / 写作要求 / 工具组，"
                                               "导出设置包：format=json 返回 pack，prompt 返回可粘贴到 AI Studio 的提示词，"
                                               "both（默认）两者都返回；不含任何 API 密钥",
     "inputSchema": _obj({"project": _S,
                          "format": {"type": "string",
                                     "enum": ["json", "prompt", "both"]}},
                         ["project"])},
    {"name": "studio_pack_import", "description": "导入 AI Studio 设置补丁（style / config / studio 三段）；"
                                                   "pack 可为对象或 JSON 字符串，dry_run=true 只返回变更清单不写库；"
                                                   "递归拒绝密钥字段，格式或版本不匹配时报错",
     "inputSchema": _obj({"project": _S, "pack": {}, "dry_run": _B}, ["project", "pack"])},
    {"name": "studio_prompt", "description": "生成可粘贴到 AI Studio 的主控提示词：task=settings 导出当前设置，"
                                              "bootstrap 立项 JSON，outline 大纲 JSON，quality 七维评分 JSON，"
                                              "style 九维画像 JSON，chapter 生成第 N 章写作提示（需 chapter_n），"
                                              "review 生成审校提示；不含 API 密钥",
     "inputSchema": _obj({"project": _S,
                          "task": {"type": "string",
                                   "enum": ["settings", "bootstrap", "outline",
                                            "chapter", "quality", "style", "review"]},
                          "chapter_n": _I},
                         ["project"])},

    # ---- AI Studio 自驾：不配 API 时由 AI Studio 对话完成生成，系统校验后继续状态机 ----
    {"name": "studio_next", "description": "AI Studio 自驾的下一步：按固定优先级返回 tool/handoff/human/done。"
                                           "不配 API 时多数环节返回 handoff（附完整 prompt 与期望 JSON），"
                                           "由 AI Studio 对话完成生成，系统校验后继续状态机",
     "inputSchema": _obj({"project": _S}, ["project"])},
    {"name": "studio_submit", "description": "提交 AI Studio 对话返回的结果并推进状态机：先全量校验，"
                                             "校验失败不落任何数据；result 可为 JSON 对象或 JSON 字符串。"
                                             "不配 API 时由 AI Studio 对话完成生成，系统校验后继续状态机",
     "inputSchema": _obj({"handoff_id": _S,
                          "result": {"type": ["object", "string"]}},
                         ["handoff_id", "result"])},
    {"name": "studio_handoffs", "description": "列出项目的 AI Studio 交接单（可按 status 过滤），"
                                               "用于查看哪些生成任务在等待对话完成",
     "inputSchema": _obj({"project": _S, "status": _S}, ["project"])},
    {"name": "studio_design", "description": "一键立项：创建 project_design 交接单，prompt 要求项目配置 / 文风 / "
                                             "人物 / 世界 / 总纲 / 前 3 章章纲 JSON。不配 API 时由 AI Studio "
                                             "对话完成生成，系统校验后继续状态机",
     "inputSchema": _obj({"project": _S, "premise": _S, "genre": _S,
                          "chapters": _I, "target_words": _I}, ["project"])},
    {"name": "studio_cancel", "description": "取消一张尚未应用的交接单；已 applied 的不允许取消。"
                                             "取消后 studio_next 不再返回该单",
     "inputSchema": _obj({"handoff_id": _S}, ["handoff_id"])},
]

TOOL_MAP = {t["name"]: t for t in TOOLS}
