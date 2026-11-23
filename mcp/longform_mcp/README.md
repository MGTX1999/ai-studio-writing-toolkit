# longform-writing MCP 服务器

长篇创作的**唯一事实来源**。设定、时间线、伏笔、章节稿、待核事实全部落在这里，
模型不再依赖上下文窗口"记住"任何东西。

## 运行

```bash
python server.py                                   # stdio，等待客户端连接
LONGFORM_DATA_DIR=/path/to/data python server.py   # 指定数据目录（默认 ./data）
LONGFORM_PROFILES_DIR=/path/to/profiles python server.py  # 指定变体目录（默认 ../profiles）
LONGFORM_CONFIG_DIR=/path/to/config python server.py      # 指定配置目录（默认 ../../config）
python server.py --list-tools                      # 输出工具清单 JSON（用于生成函数声明）
python server.py --self-test                       # 内置自检，无需客户端
```

在仓库根目录还有命令行封装（与 MCP 共用同一份 `Store` 与服务模块）：

```bash
python cli.py serve                 # 本地 Web 编辑器（127.0.0.1 + 令牌）
python cli.py doctor                # 环境自检，退出码 0 表示通过
python cli.py tools                 # 工具清单（--json 结构化）
python cli.py settings get|set      # 模型服务 / IMA 配置（密钥脱敏）
python cli.py llm "提示词"           # 直接调用已配置的模型
python cli.py kb import <path>      # 导入知识库；kb search <query> 检索
python cli.py run start <project>   # 单章自动流水线：start/status/list/resume/cancel
python cli.py lint <project> --chapter 3   # L0 确定性检查（只报告）
python cli.py usage <project> --by chapter # 实际 LLM 调用账本
python cli.py studio show <project>        # AI Studio 设置包（show/prompt/export/import/set）
python cli.py studio next <project>        # AI Studio 自驾：next/submit/handoffs/design/cancel
python cli.py loop start <project>         # 质量环：start/report/meta（L0 → 评分 → 定向修复）
python cli.py style distill <project> --chapters 1,2,3   # 文风蒸馏：distill/show/apply
```

零第三方依赖，Python 3.9+ 即可。数据文件：`$LONGFORM_DATA_DIR/longform.db`（SQLite）。
两个目录都可以放到网盘里（如 Dropbox / OneDrive），这样"记忆"和"作品变体"都能跨设备。

## 模块划分

| 文件 | 职责 |
|---|---|
| `server.py` | 传输层（stdio 分帧）+ JSON-RPC 协议 + 兼容导出 |
| `tool_specs.py` | 135 个工具的声明（纯数据） |
| `handlers.py` | 表驱动派发：`HANDLERS[name](args, store)` |
| `packet.py` | 章节输入包组装、预算降级、上下文账 |
| `pipeline.py` | 阶段建议（`stage_advice`）与大纲流水线（`outline_next`） |
| `exporting.py` | 导出配置优先级与降级导出 |
| `config_loader.py` | 读取 `config/defaults.yaml`（零依赖 YAML 子集 + mtime 缓存） |
| `settings.py` | 运行环境配置（模型服务 / IMA、环境变量覆盖、密钥脱敏） |
| `llm_client.py` | OpenAI 兼容的模型调用（`urllib`，单次请求、错误映射） |
| `kb.py` | 知识库：分块导入、FTS5/LIKE 检索 |
| `ima_client.py` | IMA 真实 OpenAPI + 旧 HTTP 桩；订阅库题录/补正文 |
| `ima_export.py` | IMA 客户端导出扫描、切块、hash 去重 |
| `insights.py` | 结构体检/串声/OOC 四类/图谱/素材与预期证据审计/流水线/申诉包 |
| `citations.py` | 引证登记与验真（精确/模糊 0.86/失败证据），不改正文 |
| `notify.py` | 外部通知（Webhook / 本地 CLI），未配置时静默降级 |
| `prose_patch.py` | 章节正文精确局部替换、预览、版本保护与 `chapter_save` 落库 |
| `runner.py` | 单章自动流水线（六步状态机、检查点续跑、预算护栏、默认人闸门）与整本批跑 |
| `lint.py` | L0 确定性检查：禁用词/AI 味/重复段/字数/对话占比/待核事实/伏笔逾期 |
| `studio_bridge.py` | AI Studio 桥接：设置包、三类提示词、工具分组、导入导出、密钥剔除 |
| `studio_autopilot.py` | AI Studio 自驾：下一步协议、交接单创建/校验/应用、一键立项设计、取消 |
| `quality_loop.py` | 质量环：L0 先行、七维评分、定向修复、阈值/无提升/轮数退出、元循环建议 |
| `style_distill.py` | 文风蒸馏：样本约束、九维分析、基因/禁用清单/风格提示词、画像落库与应用；含本地文风盲测（八维特征对照 + 禁用词扣分） |
| `web_server.py` | 本地 Web API（`127.0.0.1` + 令牌 + `webui/` 静态资源，v1.15） |
| `store.py` | SQLite 数据层（schema v10：新增 `runs` / `run_steps` / `llm_calls` / `lint_findings` / `loop_runs` / `loop_metrics` / `style_profiles` / `handoffs` / `citations`；不碰文件系统导出） |
| `profile.py` | 变体层（全部文件系统逻辑、models.json 读取） |
| `selftest.py` | `--self-test` 的实现 |

## 客户端配置示例

```json
{
  "mcpServers": {
    "longform": {
      "command": "python",
      "args": ["/abs/path/to/mcp/longform_mcp/server.py"],
      "env": {
        "LONGFORM_DATA_DIR": "/abs/path/to/data",
        "LONGFORM_PROFILES_DIR": "/abs/path/to/my-novels/profiles"
      }
    }
  }
}
```

## 降级方案：平台不支持 MCP 时

很多 AI Studio 类平台支持 Function Calling 但不支持 MCP。两种过渡做法：

**方案 A：函数声明直连（推荐）**
1. `python server.py --list-tools > tools.json`
2. 把 `tools.json` 转成你平台的函数声明格式（多数平台接受 JSON Schema 子集）。
3. 起一个本地小代理：`python -m jsonrpc_proxy` 之类，把函数调用转发到 `server.py` 的 stdio。
   `scripts/smoke_test.py` 里就是一段可直接复用的 stdio JSON-RPC 客户端代码。

**方案 B：纯手工交换**
1. 每次需要上下文时调用 `packet_assemble`，把返回的 `packet` 文本整段贴进对话。
2. 写完一章后手动把正文交给 `chapter_save`（用一个本地 `python -c` 或临时脚本即可）。
3. 设定变更手动维护 `bible_upsert`。
缺点是多几次复制，但一致性收益仍在——因为**上下文包始终是结构化组装的**，不是靠模型回忆。

## 工具清单

| 分类 | 工具 |
|---|---|
| 项目 | `project_init` `project_list` `project_export` `project_delete` |
| 设定档 | `bible_upsert` `bible_get` `bible_list` `bible_search` |
| 时间线 | `timeline_add` `timeline_query` |
| 伏笔 | `foreshadow_add` `foreshadow_list` `foreshadow_resolve` `foreshadow_overdue` |
| 章节 | `chapter_save` `chapter_get` `chapter_list` `chapter_versions` `chapter_diff` `chapter_export` |
| 文风 | `style_set` `style_get` |
| 事实 | `fact_add` `fact_set_status` `fact_list` |
| 审计 | `audit_log` `audit_list` |
| 核心 | `packet_assemble` |
| 统计/配置 | `ledger_stats` `config_set` `config_get` |
| **作品变体** | `profile_list` `profile_get` `profile_create` `profile_delete` `profile_bind` `profile_set_export_dir` |
| **大纲** | `outline_set` `outline_get` `outline_list` `outline_tree` `outline_next` `outline_brief` `outline_versions` `outline_delete` `outline_diff` |
| **编排与建议** | `stage_advice` |
| **素材库** | `material_add` `material_get` `material_list` `material_delete` |
| **逐段写作** | `section_save` `section_get` `section_list` `section_merge` `section_delete` |
| **上下文与导出** | `context_estimate` `workspace_export` |
| **运行环境设置** | `settings_get` `settings_set` |
| **模型调用** | `llm_generate` |
| **知识库** | `kb_import` `kb_search` |
| **IMA** | `ima_search` `ima_fetch` |
| **摘要与故事状态** | `summary_set` `summary_list` `story_state_set` `story_state_get` `story_state_list` |
| **自动化运行** | `run_start` `run_status` `run_list` `run_resume` `run_cancel` |
| **质量与成本** | `lint_check` `usage_report` |
| **写作助手** | `word_stats` `chapter_digest` `token_audit` `revision_plan` `inspiration_interview` `inspiration_synthesize` |
| **AI Studio 桥接** | `studio_pack_get` `studio_pack_import` `studio_prompt` |
| **AI Studio 自驾** | `studio_next` `studio_submit` `studio_handoffs` `studio_design` `studio_cancel` |
| **质量环** | `loop_start` `loop_report` `loop_meta` |
| **文风蒸馏** | `style_distill` `style_profile_get` `style_profile_apply` |

### 素材库：引用处必须能追溯到来源

`materials` 表与 `facts` 表的分工是：

- **`facts`** = 写进正文前**必须核实**的断言，带 `pending/verified/disputed/rejected` 状态机；
- **`materials`** = 写作时**参考的原材料**（摘抄、资料、灵感、背景调研），不进正文，
  但每条都带 `source` 与 `source_kind`，导出时在素材索引里逐条列出。

四种 `kind`：`quote` 摘抄 / `reference` 参考资料 / `idea` 灵感 / `research` 背景调研。
六种 `source_kind`：`file` / `url` / `ima` / `book` / `dialog` / `other`。
**省略 `source_kind` 时会按 `source` 形态自动判定**：`ima://…` → ima，
`http(s)://…` → url，含路径分隔符或绝对路径 → file。

素材按 kind 分流到工作目录：`quote`/`idea` → `02-素材/`，
`reference`/`research` → `03-参考资料/`。

### 逐段写作

一章一气呵成，中间崩了就得整章重来。分段落稿后"第 3 段写歪了"只需重修第 3 段：

```
section_save(n, idx=1..N)      # 分段落稿，同段再写即修订（version+1）
section_list(n)                # 各段最新版概览
section_merge(n)               # 拼成整章 → chapter_save
```

`section_merge` 的两条硬规矩：**段落不连续拒绝合并**（1、2、4 缺 3）、
**空段拒绝合并**。宁可不拼，也不拼出一篇缺段的正文——缺陷一旦被后面
所有步骤当成正常内容，排查成本远高于重修一段。

### 上下文估算与节约

```
context_estimate(project, chapter_n, brief?, token_budget?)
→ {approx_tokens, budget_chars, over_chars, fits,
   sections:[{label, chars, approx_tokens, share, optional}], suggestions}
```

它与 `packet_assemble` **共用同一套取数与组装逻辑**（`_packet_prepare`），
所以看到的就是待会儿真正会被喂进去的内容，不存在"估算一套、实跑另一套"。

`suggestions` 给的是可执行动作（关掉哪个 `packet.include_*` 省多少 token、
放宽预算到多少能保留全部小节），不是泛泛而谈的"注意长度"。
只做只读统计，不写任何数据。

### 运行环境、模型与知识库

- **设置**：`settings_get` 返回脱敏后的模型/IMA 配置（`api_key` 形如 `sk-***a1b2`）；
  `settings_set` 按 `llm` / `ima` 分节更新，带 `***` 的密钥视为未修改。
  存储为 `config/settings.json`，环境变量 `LONGFORM_LLM_*` / `LONGFORM_IMA_*` 优先。
- **模型调用**：`llm_generate(prompt, system?, temperature?, max_tokens?, timeout?)`
  向 `{base_url}/chat/completions` 发单次请求，返回 `text`、`usage`（若有）、`provider`、`model`；
  未配置、401/403、429、5xx、超时、非 JSON 都转可读错误，不重试、不回显密钥。
- **知识库**：`kb_import` 接受 `content` / `path` / `dir`（目录优先），按标题与长度分块后写入
  `materials`，共享同一 `source`；`kb_search` 优先 FTS5 `trigram`，不可用时回退 `LIKE`，
  支持 `kind` / `tag` / `chapter` / `limit`，返回带高亮片段与来源的结果。
- **IMA**：`ima_search` 未配置时返回 `{configured: false, note: ...}` 而不是报错；
  `ima_fetch(id, save=true, project=...)` 可把命中条目落库为 `source=ima://<kb>/<item>`、
  `source_kind=ima`。HTTP 契约见 `ima_client.py` 模块说明，替换真实接口只改该文件。
- **摘要与状态**：`summary_set` 写/覆盖某章摘要（空串删除），`summary_list` 按章号区间读取；
  `story_state_set/get/list` 维护 `character-state` / `next-hook` / `arc-status` 等状态。
  两者进入输入包的「前情摘要」「故事状态」小节，由 `packet.include_summaries` /
  `packet.include_story_state` 控制，未维护时整节省略。

### AI Studio 桥接（不配 API）

`studio_bridge.py` 面向「不配置 LLM API、由 AI Studio 对话框主控」的用法：本服务只存数据、
出提示词、收设置补丁，不调模型、不写正文。v1.14 起 `studio_autopilot.py` 进一步把
真正需要生成的环节做成交接单，见下方「AI Studio 自驾」。

- **设置包**：格式 `longform-studio-pack` v1，含 `project / style / config / studio / tools / workflow / warnings`；
  项目配置 `studio` 段为 `mode`（`ai-studio|direct`）/ `requirements` / `tool_groups` / `notes`。
- **工具分组**：8 类——项目与设定、大纲、章节、素材与知识库、审校与质量、自动化、导出与提示词、设置与变体；
  未匹配归「其他」；空 `tool_groups` 表示全部。
- **固定工作流**：`config_get/style_get → outline_next/outline_brief → packet_assemble → 写正文 →
  chapter_save(draft) → lint_check → chapter_save(approved) → summary_set/story_state_set → usage_report`。
- **提示词**：`settings` / `bootstrap` / `outline` / `chapter` / `quality` / `style` / `review`
  七类，均为可粘贴的 Markdown；返回结构仍是 `{ok, project, task, prompt}`。
- **安全**：导出递归剔除 `api_key` 等密钥；导入遇密钥键直接报错；JSON 上限 512KB；导入只写
  `style / config / studio`。

```bash
python cli.py studio show <project> [--json]
python cli.py studio prompt <project> --task settings|bootstrap|outline|chapter|quality|style|review [--chapter N] [--out FILE]
python cli.py studio export <project> [--out DIR] [--format both|json|prompt]
python cli.py studio import <project> --file patch.json [--dry-run]
python cli.py studio set <project> --mode ai-studio --requirements "..." --tool-groups 大纲,章节
```

Web 路由（v1.12）：

| 方法 | 路径 | 用途 |
|---|---|---|
| GET / PUT | `/api/projects/<pid>/studio` | 设置包 / 提示词 / 工具组 / 保存 studio 设置 |
| GET | `/api/projects/<pid>/studio/view` | **原「创作台」数据视图**（从 `/studio` 迁来） |
| GET | `/api/projects/<pid>/studio/export?format=json\|prompt` | 附件下载设置包或提示词 |
| POST | `/api/projects/<pid>/studio/import` | 导入设置补丁（`dry_run` 可预览） |
| POST | `/api/projects/<pid>/studio/prompt` | 按 `settings\|bootstrap\|outline\|chapter\|quality\|style\|review` 生成提示词 |

> **路由变更**：旧脚本若用 `GET /api/projects/<pid>/studio` 取创作台数据，需改为 `/studio/view`；
> `/studio` 返回 AI Studio 设置包。

### AI Studio 自驾（v1.14，不配 API）

`studio_autopilot.py` 把需要模型生成的环节交给 AI Studio 对话框：系统落 `handoffs`
交接单，提交结果后校验、落库并继续状态机。全程不调用 `llm_client`。

- **交接单**：`handoffs` 表；状态 `pending → submitted → applied`，异常为
  `failed / rejected / cancelled`；类型 `chapter_draft / quality_critique /
  quality_revise / style_distill / project_design / outline / llm_generate / review`。
- **下一步**：`studio_next` 按固定优先级返回 `tool / handoff / human / done`。
  优先级：待处理交接单 → 最近运行 → 立项设计 → 总纲/卷纲 → 章纲 → 正文 → L0 →
  质量环 → 摘要 → 下一章 → `done`。
- **幂等与失败不写**：已 applied 重复提交返回 `already_applied`；校验失败保持
  `pending`，不写任何数据。
- **一键设计**：`studio_design` 生成 `project_design` 交接单，提交后一次性应用
  项目配置 / 文风 / 人物 / 地点 / 总纲 / 卷纲 / 前 3 章章纲。
- **provider**：`auto / api / studio`；`auto` 有 API 走 API，否则走 studio。
  `run_start`、`loop_start`、`style_distill`、`llm_generate` 与 Web 润色均支持 studio。

```bash
python cli.py studio design <project> --premise "..." --genre 悬疑 --chapters 30
python cli.py studio next <project> [--json]
python cli.py studio submit <handoff_id> (--file PATH | --text TEXT) [--json]
python cli.py studio handoffs <project> [--status pending|applied|...]
python cli.py studio cancel <handoff_id>
```

Web 路由（v1.14）：

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/projects/<pid>/autopilot` | 下一步（tool / handoff / human / done） |
| GET | `/api/projects/<pid>/handoffs?status=` | 列出交接单 |
| POST | `/api/handoffs/<id>/submit` | 提交交接结果（校验后应用） |
| POST | `/api/handoffs/<id>/cancel` | 取消未应用的交接单 |
| POST | `/api/projects/<pid>/design` | 创建 `project_design` 交接单 |

### 大纲四级流水线

大纲本体此前完全不落库，只活在对话里——换个会话就没了。现在三级都在
`outline_nodes` 表里，一张表存三级，靠 `level` 区分：

```
master  (parent=NULL)        一部作品只有一个
  └─ volume (parent=master)  卷 = 记录 + 章号区间 [start_chapter, end_chapter]
       └─ chapter (parent=volume)  绑定扁平章号 chapter_n
```

- `outline_next` 是「下一步该做什么」的唯一权威口径，返回
  `stage / reason / missing / gaps / next_chapter / suggested_tools / expert / skill`。
  技能与主控都听它，不要自己判断阶段。
- `outline_set` 同一 id 再写一次即修订（`version+1`），旧版本永不覆盖。
  省略 id 时自动命名：`master` / `v01` / `c012`。
- `outline_brief(chapter_n)` 取第 N 章章纲，可直接喂 `packet_assemble(brief=)`。
  新表读不到时回退读旧章节的 `data.brief`，返回标 `source="legacy-chapter-data"`
  ——**只读不写回**，旧数据不会被自动迁移进新表。
- **卷不改变章号**：`chapters.n` 仍是扁平整数，导出文件名仍是 `第{n:03d}章`；
  卷归属一律反查，`outline_brief` 的 `volume` 字段直接给出。

### stage_advice：每阶段该用什么 LLM

```
stage_advice(stage="chapter", project=..., profile=...)
→ {stage, stage_label, model:{tier, name, temperature, max_output_tokens},
   reason, expert, skill, tools, output_contract, source, warnings}
```

六个阶段：`master` 总纲 / `volume` 卷纲 / `chapter` 章纲 / `draft` 正文 /
`qa` 审校 / `utility` 杂务。省略 `stage` 返回全部阶段总表；非法 stage 报错并列出允许值。

- `project` 可选——只想问"这个阶段该用什么模型"时不必先建项目。
- 模型名是**平台相关标识**，档位（`planning` / `production` / `qa` / `utility`）
  才是跨平台语义锚点：只有两三个模型的平台可自行把档位映射到自己的模型。
- 配置来自 `config/models.json`，并按既有优先级叠加项目 `config["models"]` 与
  变体 `overrides["models"]`。

**降级阶梯（读操作宽容，绝不抛错）**：

| 情形 | 行为 |
|---|---|
| 文件不存在 / 坏 JSON / 顶层非对象 | `source="builtin-fallback"`，`warnings` 说明原因（坏 JSON 带行列号） |
| 档位缺失 | 该档位回落内置值，`warnings` 点名 |
| 字段类型错或越界 | 收敛并钳制（`temperature` ∈ [0,2]，`max_output_tokens` ∈ [1,200000]），`warnings` 点名 |
| `name` 缺失 / 空 / 仍是 `your-` 开头 | `warnings` 提示「模型名仍是占位符」 |
| 变体不可读 | 跳过该覆盖，`warnings` 说明 |

> **`config/models.json` 与 `config/defaults.yaml` 同源双写**：标准库没有 YAML
> 解析器，MCP 只能读 JSON。改模型请两处都改，
> `python scripts/check_config.py` 会拦截漂移（退出码非 0），
> 它同时比对 `registry.yaml` 与代码工具集合。

### 工作目录分目录导出

`chapter_export` 是"正文交稿"（一个平铺目录）；`workspace_export` 是
"写作现场"——一次把五类产物各归其位，可反复导出而不互相覆盖：

```
workspace_export(project, dir, include=["outline","drafts","materials","references","final"])
→ <dir>/
   ├── 00-大纲/      总纲要.md / 卷纲要.md / 章纲要.md
   ├── 01-章节草稿/  第001章_标题.md（全部章节最新版）
   ├── 02-素材/      素材索引.md（quote + idea，带来源）
   ├── 03-参考资料/  参考资料索引.md（reference + research，带来源）
   └── 04-成品/      第00N章_标题.md（仅 status=approved 的章节）
```

`dir` 为绝对路径时原样使用，相对路径相对当前工作目录解析。

> `bible_search` 与 `timeline_query` 额外接受可选 `limit` 参数（见下方「分帧与限制」）。
> `chapter_save` 额外接受可选 `export` / `export_dir` / `profile`（见下方「作品变体」）。

## 作品变体（profile）：在独立文件夹里做微调

一部作品写下去，方向会变：正传与二创要分开、试写一种新声线要能随时退回、
正文想落在你自己的目录里而不是仓库里。**变体**就是为此存在的一层。

```
profiles/                         ← 可用 LONGFORM_PROFILES_DIR 指到任意位置
├── _default/                     ← 内置模板：不覆盖任何默认值
│   ├── profile.json              ← 元信息 + overrides + 正文保存位置（支持 // 与 /* */ 注释）
│   ├── overrides/                ← 可选：风格指南、红线词表、设定补丁（给人/技能读）
│   └── exports/                  ← 正文落盘处
└── 二创-示例/                     ← 一个真实可用的二创变体，复制它改改就行
```

**三条原则**：

1. **源码只读，作品可写。** 工具箱本体不动；所有针对某个写作方向 / 某部二创的微调
   都落在 `profiles/` 下。加一个变体 = 建一个文件夹，删一个变体 = 删掉这个文件夹。
2. **SQLite 是唯一权威，`exports/` 是派生视图。** 导出的 md 删了随时能用
   `chapter_export` 重建；反过来，**不会**用你编辑过的 md 去覆盖数据库。
3. **配置优先级（高 → 低）**：变体 `overrides` > 项目 `config` > 代码内默认值。
   变体存在的意义就是"刻意覆盖"，所以它必须能盖住项目默认值。

**正文什么时候写到文件夹里**：

| 场景 | 怎么做 |
|---|---|
| 项目绑定了变体 | `chapter_save` 自动落盘到该变体的 `export.dir`；传 `export=false` 可跳过本次 |
| 没绑变体，但这次想落盘 | `chapter_save(..., export_dir="D:/我的小说/雾港")` |
| 事后补导 / 改了导出目录 | `chapter_export(project)` 导出全部最新章节；带 `n` 导出单章；带 `version` 导出某一版 |
| 改稿不想覆盖旧稿 | 变体里设 `export.versioned: true`，文件名带 `_v<N>` |

落盘失败（目录只读、盘符不存在）**不会**让 `chapter_save` 失败——数据库已经写进去了，
导出结果降级成 `{"ok": false, "error": ...}` 放在返回值的 `export` 字段里。

**绑定与解绑**：`project_init(profile=...)` 立项时直接绑；写到一半想换方向用
`profile_bind(project, name)`，`name` 传空串即解绑。绑定一个不存在的变体会当场报错，
不会把拼错的名字写进项目配置导致之后每章静默降级。

**变体被删了怎么办**：读操作保持宽容。`packet_assemble` 会发现变体不可读，
回退为项目自身配置，并在包尾「组装说明」里写明，不会让开写流程断掉。

**为什么用「带注释的 JSON」而不是 YAML**：标准库没有 YAML，为一个配置文件引入
第三方依赖不值得；而手写配置时"临时注释掉某一行"是最高频的动作。所以 `profile.json`
支持 `//` 与 `/* */`，解析器自带、零依赖，且不会误伤字符串里的 `//`（比如 URL）。

## 错误处理约定

所有入参在数据层校验，失败抛 `LongformError` 并由协议层转成 MCP 的
`isError: true` 响应，消息形如：

```
参数错误：参数 `status` 取值非法：'resolvd'，允许的值为 ['open', 'resolved', 'dropped']
```

设计取舍：**宁可报错也不静默成功**。旧实现里「回收一条不存在的伏笔」会返回
`{"ok": true}`，模型据此判定流程通过，问题要等到几十章后才暴露。

## 分帧与限制

- 请求支持裸 JSON（一行一条）与 `Content-Length: N` + 空行 + N 字节正文两种写法。
- 坏 JSON 一定返回 `-32700`，绝不会静默丢弃导致客户端死等。
- `bible_search` 默认最多返回 50 条（`%` 与 `_` 按普通字符处理）。
- `timeline_query` 单次最多返回 1000 行，防止把整张时间线灌进上下文。
- 单段文本（正文 / 事实陈述等）长度上限见 `store.py` 顶部常量区。

## 扩展新工具

见根目录 `ARCHITECTURE.md` 第 6 节。要点：`store.py` 加方法 → `tool_specs.py` 的 `TOOLS` 注册 →
`handlers.py` 写 `h_<name>` 并登记进 `HANDLERS` → `config/registry.yaml` 登记开关。
schema 全部 `IF NOT EXISTS`，升级不丢数据。

新增**表**时务必同时登记 `store.py` 的 `_CHILD_TABLES`，否则
`project_init(overwrite=True)` 与 `project_delete` 会留下该表的残留。
新增**工具**后跑一次 `python scripts/check_config.py`，它会比对 registry 与代码。

- `profile.json` 的 `overrides` 嵌套超过 12 层会被拒绝（多半是粘贴出的环或脚本产物）。
- 变体名会做路径净化（`../`、`/`、`\` 等一律替换），删除前还会再校验目标目录确实
  位于 `profiles/` 之内——删变体是破坏性操作，两道闸都不许越界。

## 备份

- **必须备份**：`longform.db`（唯一权威）与 `profiles/` 下的 `profile.json` + `overrides/`。
- **不必备份**：各变体的 `exports/`，它们是从数据库导出的派生视图，`chapter_export` 可重建。
- 建议每完成 5 章 `project_export` 一次，存成 JSON 版本快照。
