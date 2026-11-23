# 叙事工作台（原长篇创作工具箱 · AI Studio 版）

面向「用 LLM 写长篇作品」的一套**可移植、可后期调整**的本地创作工作台。
核心思路一句话：**模型是副驾，不是记忆体，也不是替身**。本工作台把「记忆」外置成结构化数据库，把「流程」固化成技能，把「判断力」封装成专家角色与确定性检查，三者通过一个 MCP 服务器连接。

2026-11 升级新增：人物卡与语言指纹、三层合规预检（词层/语义层/事件层）、红色报告阻断导出、8 角色会诊、12 写作妙招、AI 使用声明；v1.6 补三栏创作台、结构体检、全章串声、行为/关系/能力 OOC、故事图谱、预期账本、申诉包与 IMA 客户端导出扫描；v1.7 再补四步审稿提示链、素材 E0–E3 证据审计、预期“承诺—触发—兑现”记录与审计；v1.8 补统一素材检索（两字/多词/零结果兜底）与章节素材包；v1.9 新增章节局部改稿（MCP `chapter_patch` + Web 人手预览/应用）；v1.10 新增字数统计、抽取式摘要与 token 账、章节修订建议、灵感访谈与灵感卡；v1.11 新增单章自动化运行（Run Engine）、L0 确定性检查、调用账本与自动驾舱；v1.12 新增 AI Studio 桥接（设置包、提示词、导入导出）；v1.13 新增质量环（七维评分 / 阈值 / 轮数 / 元循环）与文风蒸馏（九维画像 / 思维与写作基因 / 禁用清单）；v1.14 新增 AI Studio 自驾（交接单 / 下一步协议 / 一键设计，不配 API 也能跑完一章）与工作台 UI 重设计（19 → 8 组导航、驾驶舱、专注模式、命令面板）；v1.15 新增条目化世界书（锚点 / 优先级 / content_hash / 输入包轨迹）、引证验真（精确匹配 + 模糊阈值 0.86 + 失败证据）、7 类素材地图与同义词扩展、事实与质量 finding 引证徽章、IMA 链接导入与文件上传（缺 Node/COS 脚本时明确降级）、整本批跑（启动 / 状态 / 恢复 / 取消）、质量元循环调权守卫、`citations` 表、Git/Gitee CI 与备份/通知脚本、5 个题材模板与三步上手向导；v1.15.1 新增文风盲测（八维特征对照 / 禁用词扣分 / 审计）。工具数 **135**，测试 **642 passed**，schema **v10**，Web **1.15.1**。产品资料与参赛材料见 [`docs/叙事工作台/`](docs/叙事工作台/README.md)。

---

## 1. 目录结构

```
ai-studio-writing-toolkit/
├── README.md                  ← 你在这里
├── ARCHITECTURE.md            ← 设计原理 + 如何二次开发
├── cli.py                     ← 命令行入口：serve / doctor / tools / projects / export / settings / llm / kb / run / lint / usage / studio / loop / style
├── config/
│   ├── registry.yaml          ← 总开关：启用哪些技能/专家/MCP 工具
│   ├── defaults.yaml          ← 可调参数：模型路由、阈值、预算、文风默认值
│   ├── models.json            ← 模型档位 + 阶段剧本（MCP 读它；与 defaults.yaml 双写）
│   ├── compliance_rules.yaml  ← 三层合规规则（词层/语义层/事件层，可替换）
│   └── settings.json          ← 运行环境配置：模型服务 + IMA（首次写入设置时生成）
├── skills/                    ← 技能层（流程与操作规范）
│   ├── longform-orchestrator/ 主控编排（入口技能）
│   ├── project-bootstrap/     立项与设定档初始化
│   ├── outline-architect/     四级大纲流水线：总纲 → 卷纲 → 章纲 → 交棒
│   ├── chapter-writer/        逐章扩写（输入包驱动）
│   ├── consistency-auditor/   一致性与伏笔审计
│   ├── style-voice-keeper/    文风与声线维护
│   ├── fact-copyright-guard/  事实核查与版权风险
│   ├── token-thrifty/         字数统计、摘要与 token 账
│   ├── revision-advisor/      规则体检与可执行修订
│   └── inspiration-interviewer/ 访谈出题与灵感卡
├── experts/                   ← 专家层（人格化提示词库）
│   ├── INDEX.md               专家索引与调用协议
│   ├── 01-策划专家.md
│   ├── 02-架构专家.md
│   ├── 03-写作专家.md
│   ├── 04-审校专家.md
│   ├── 05-文风声线专家.md
│   └── 06-事实与版权专家.md
├── mcp/longform_mcp/          ← 记忆层（MCP 服务器，SQLite 持久化）
│   ├── server.py              MCP over stdio 实现
│   ├── store.py               数据层（schema v10：runs / run_steps / llm_calls / lint_findings / loop_runs / loop_metrics / style_profiles / handoffs / citations）
│   ├── settings.py            运行环境配置（密钥脱敏 / 环境变量覆盖）
│   ├── llm_client.py          OpenAI 兼容模型调用（stdlib urllib）
│   ├── kb.py                  知识库：分块导入与全文检索
│   ├── ima_client.py          IMA 适配器（真实 OpenAPI + 旧 HTTP 桩；订阅库题录/补正文）
│   ├── ima_export.py          IMA 客户端导出目录扫描（docx/txt/md/html，hash 去重）
│   ├── insights.py            结构体检/串声/OOC/故事图谱/流水线/申诉包
│   ├── citations.py           引证登记与验真（精确/模糊 0.86/失败证据）
│   ├── narrative.py           合规/OOC/角色会诊/妙招/AI 声明引擎
│   ├── runner.py              单章自动流水线（状态机/检查点/续跑/预算/人闸门）
│   ├── lint.py                L0 确定性检查（禁用词/AI 味/重复/字数/对话占比/事实/伏笔）
│   ├── studio_bridge.py       AI Studio 桥接（设置包 / 提示词 / 导入导出）
│   ├── studio_autopilot.py    AI Studio 自驾（下一步 / 交接单 / 一键设计 / 取消）
│   ├── quality_loop.py        质量环（L0 → 七维评分 → 定向修复 → 退出条件 / 元循环）
│   ├── style_distill.py       文风蒸馏（九维画像 / 基因 / 禁用清单 / 应用）
│   ├── notify.py              外部通知（Webhook / 本地 CLI，未配置时静默降级）
│   ├── web_server.py          本地 Web API（127.0.0.1 + 令牌，v1.15）
│   ├── profile.py             作品变体（独立文件夹微调 / 正文落盘）
│   ├── requirements.txt
│   └── README.md
├── webui/                     ← 单页可视化编辑器（原生 HTML/CSS/JS，无构建链）
│   ├── index.html
│   ├── app.js
│   └── style.css
├── docs/
│   └── 叙事工作台/             ← 产品定位、材料汇编、差距分析、MVP、参赛材料、演示脚本
├── profiles/                  ← 作品变体（一个文件夹 = 一个写作方向）
│   ├── _default/              内置模板：overrides/ + exports/
│   └── 二创-示例/              可直接复制改用的二创变体
├── schemas/                   ← 数据结构契约（改字段先改这里）
│   ├── world_bible.schema.json
│   ├── chapter_packet.schema.json
│   ├── outline_node.schema.json
│   ├── material.schema.json
│   └── audit_report.schema.json
├── prompts/                   ← 可直接复制的提示词片段
│   ├── packet-template.md     章节输入包组装模板
│   ├── revision-rubric.md     修订评分表
│   └── consistency-checklist.md
├── templates/
│   ├── project-init.md        新项目初始化清单
│   ├── character-card.md      五维人物卡（身份/性格/灵魂场/语言指纹/成长弧）
│   └── genres/                5 个题材模板（仙侠/都市/悬疑/克苏鲁/历史）+ 索引
├── scripts/
│   ├── smoke_test.py          端到端自检（带断言与退出码）
│   └── check_config.py        配置一致性：models.json ↔ defaults.yaml ↔ registry.yaml
└── tests/                     ← 单元测试（标准库 unittest，零新增依赖）
    ├── support.py
    ├── test_store.py / test_server.py / test_profile.py / test_outline.py
    ├── test_models.py / test_workshop.py / test_config_defaults.py
    └── test_settings.py / test_llm.py / test_kb.py / test_ima.py
        / test_web.py / test_cli.py / test_tools_ext.py / test_narrative.py
```

## 2. 三层的分工

| 层 | 回答什么问题 | 载体 | 可否后期调整 |
|---|---|---|---|
| **技能（Skill）** | 这一步「怎么做」 | `skills/*/SKILL.md` | ✅ 直接改 Markdown |
| **专家（Expert）** | 这一步「以什么身份、什么标准判断」 | `experts/*.md` | ✅ 直接改 Markdown |
| **MCP（记忆）** | 「事实、设定、进度」存在哪、怎么查 | `mcp/longform_mcp/` | ✅ 加工具 / 改 `defaults.yaml` |

**关键设计**：技能与专家都是纯 Markdown，改完即生效，不需要重启服务、不需要改代码。
MCP 服务器是唯一的"有状态"组件，所有跨章节记忆都经它落地，换平台时只需把它的输出接到新的上下文里。

## 3. 快速开始

### 3.0 一键体验（Windows）

不装 Python，直接双击：

```
dist\叙事工作台-体验包_v1.15.1_20261123.zip
  解压 → 双击 start.bat → 浏览器自动打开
```

包内已内置 Python 3.14.7（含 SQLite 3.50.4 / FTS5）、应用源码、演示库 `data/longform.db` 与资料包。
启动后只监听 `127.0.0.1`，地址带一次性令牌；关闭窗口即停止。详细说明见包内 `启动说明.txt`。
> 该体验包为 v1.15.1，已包含至世界书、引证验真、整本批跑、三步向导与文风盲测；内置 135 工具、642 条测试与 19 份资料。字节数与 SHA-256 见同目录 `.sha256` 文件（避免包内自引用）。

需要 EXE 启动：`dist\叙事工作台-体验包_v1.15.1_20261123_含启动器.zip`，解压后双击 `叙事工作台启动器.exe`；SHA-256 `507C9B40DB18D761CEE8D64080B830E45D3DD077AC92ABD31B84A07299DE7C50`。

### 3.0.1 项目根目录主服务（开发 / 演示）

也可以直接双击根目录的 `叙事工作台启动器.exe`：自动识别仓库或体验包布局、在 8765–8790 中选空闲端口并打开浏览器；关闭窗口即停止服务。

```powershell
.\start_web.ps1                    # 首选 8766；被占用则自动顺延到 8767–8790，并打开浏览器
.\start_web.ps1 -NoBrowser         # 只启动，不自动开浏览器
.\stop_web.ps1                     # 只停止本项目记录的主服务
```

启动器把 PID、端口、版本和一次性令牌写入 `.runtime\web_server.json`，运行日志写入 `.runtime\logs\`。
重复执行 `start_web.ps1` 会复用已经在运行的本项目实例，不会重复占端口；
`stop_web.ps1` 会先核对 PID 命令行，不会误杀其他 Python 程序。不要把 `.runtime/` 放进公开分享或交付包。

### 3.1 启动 MCP 服务器

```bash
cd mcp/longform_mcp
pip install -r requirements.txt      # 仅标准库，通常无需安装
python server.py                     # stdio 模式，等待客户端连接
# 可选：指定数据目录 / 变体目录
LONGFORM_DATA_DIR=/path/to/data python server.py
LONGFORM_PROFILES_DIR=/path/to/profiles python server.py
```

自检（不依赖任何 MCP 客户端）：

```bash
cd ai-studio-writing-toolkit       # 仓库根目录
python scripts/smoke_test.py        # 端到端，带断言与退出码
python scripts/check_config.py      # 两份配置是否漂移（退出码 0/1）
python mcp/longform_mcp/server.py --self-test   # 服务端内置自检
```

浏览器编辑器与命令行（同样在仓库根目录）：

```bash
python cli.py serve                 # 启动本地编辑器：自动开浏览器、仅监听 127.0.0.1、URL 带一次性令牌
python cli.py doctor                # 环境自检：Python / SQLite+FTS5 / 配置 / 目录 / LLM/IMA 状态
python cli.py tools                 # 打印 135 个工具清单（--json 输出结构化）
```

`serve` 默认端口 `8765`（`--port` 可改），`--no-open` 可不开浏览器；
所有 `/api/*` 请求需带启动时生成的 `X-Longform-Token`，静态资源里不含任何密钥。
直接执行 `python cli.py serve` 不负责端口冲突处理；日常启动主服务请优先用根目录 `start_web.ps1`。

单元测试（覆盖数据层与协议层的边界与错误处理）：

```bash
python -m unittest discover -s tests -t .   # 当前 642 passed
python tests/test_store.py      # 数据层：校验、事务、边界
python tests/test_server.py     # 协议层：分帧、派发、输入包
python tests/test_profile.py    # 变体层：增删、覆盖、正文落盘
python tests/test_outline.py    # 大纲层：三级、卷区间、章号、阶段流转
python tests/test_models.py     # 阶段建议：档位映射与四类降级
python tests/test_workshop.py   # 写作现场：素材、逐段写作、估算、分目录导出
```

CI：`.github/workflows/ci.yml` 在 Python 3.9 / 3.14 上运行单元测试、
`scripts/check_config.py`、`scripts/smoke_test.py` 与 `server.py --self-test`，
并做一次密钥扫描；Playwright 不安装时 UI 测试自动 skip。
`.gitee/workflows/ci.yml` 是等价的 GitHub Actions 兼容写法，接入 Gitee Go 时需按平台语法调整。

> 套件耗时几乎都由**临时目录的清理**支配：在部分 Windows 机器上删掉一个空目录
> 要约 0.6 秒（OS / 安全软件层面，非本工具箱行为），而写一个文件只要 0.01 秒。
> 所以 `tests/` 刻意共用临时目录、只在类结束时清理一次。若你的机器删除很快，
> 整个套件应在 1 分钟内跑完。

### 3.2 在 AI Studio 里接入

1. 把 `config/registry.yaml` 中你不想用的技能/专家/工具置为 `enabled: false`。
2. 把 `skills/longform-orchestrator/SKILL.md` 的正文整体作为 **System Instructions**（或作为第一条对话指令）贴入 AI Studio。
3. 挂载 MCP：`server.py` 作为 stdio 服务器注册到你的客户端；若平台暂不支持 MCP，见 `mcp/longform_mcp/README.md` 的「降级方案：手工 JSON 交换」。
4. 发第一句话：`新建项目：《书名》，类型：长篇小说`，主控技能会引导后续流程。

### 3.3 最小可用流程

```
立项 → 设定档(bible) → 总纲要 → 卷纲要(有卷时) → 章纲要 → 组装输入包 → 逐章写作
     → 一致性审计 → 修订 → 归档下一章
   ↑                                                                    │
   └────────────────────── 每章循环，状态全部落在 MCP ───────────────────┘
```

大纲三级全部落库（`outline_nodes` 表），不是停留在对话里的 Markdown。
每一级开工前先问 `outline_next`「现在是哪个阶段」，再问 `stage_advice`
「这个阶段该用什么模型」——不要凭印象判断。

## 3.4 四级大纲与阶段化模型建议

| 阶段 | 产物 | 落库调用 | 建议档位 |
|---|---|---|---|
| 总纲要 | 骨架 / 总章数 / has_volumes / 结局 | `outline_set(level="master")` | planning |
| 卷纲要（有卷时） | 卷的章号区间 + 目标/转折/收束 | `outline_set(level="volume", start_chapter, end_chapter)` | planning |
| 章纲要 | 每章六要素，绑定 `chapter_n` | `outline_set(level="chapter", chapter_n=N)` | planning |
| 正文 | 章节正文 | `chapter_save` | production |
| 审校 | 审计报告 | `audit_log` | qa |
| 归档 / 杂务 | 导出 | `chapter_export` / `project_export` | utility |

`stage_advice(stage=...)` 返回该阶段的模型档位、该找哪位专家、该调哪些工具、
该交什么产物。它是**建议不是强制**——MCP 改不了你挂的模型，但照它排模型是流程纪律。

## 3.5 写作现场：素材、逐段写作与上下文账

写长篇时真正耗时的不是"生成"而是"管理"——素材从哪来、哪一段要重修、
这一章的输入包到底占了多少上下文。补了四组能力：

| 能力 | 工具 | 解决什么 |
|---|---|---|
| 素材库 | `material_add/get/list/delete` | 素材带 `source` 与 `source_kind`，引用处能一跳追溯到出处；按 kind 分流到工作目录的 `02-素材/` 与 `03-参考资料/` |
| 逐段写作 | `section_save/get/list/merge/delete` | 一章分段落稿、段级留版本，某段写歪只重修那一段；`section_merge` 拼成整章 |
| 大纲对比 | `outline_diff` | 看清"这一版改了什么"，重排结构时不把已论证要删的东西捡回来 |
| 上下文账 | `context_estimate` | 先算账再生成：各小节 token 占用、是否超预算、可执行的省 token 手段 |
| 分目录导出 | `workspace_export` | 一次把大纲/草稿/素材/参考资料/成品五类产物各归其位 |

`section_merge` 的两条硬规矩：**段落不连续拒绝合并**、**空段拒绝合并**——
宁可不拼，也不拼出一篇缺段的正文。

`context_estimate` 与 `packet_assemble` **共用同一套取数与组装逻辑**，
所以估算出来的就是待会儿真正喂进去的内容，不存在"估算一套、实跑另一套"。

模型档位写在 `config/models.json`（MCP 读它）；`config/defaults.yaml` 的
`models:` 段是给人读的同源副本。**改一处必须改另一处**，
`python scripts/check_config.py` 会拦截漂移。

## 3.6 浏览器编辑器与命令行

不想经过 AI 客户端时，可以直接用浏览器改稿。v1.14 起一级导航收敛为 **8 组**
（驾驶舱 / 写作 / 世界 / 素材 / 质量 / 自动化 / AI Studio / 设置），旧路由自动重定向；
应用壳为「可折叠左导航 + 顶栏（项目切换 / 命令面板 / 下一步）+ 主画布（最大 1440px）」：

| 能力 | 说明 |
|---|---|
| 驾驶舱 | 进度 / 伏笔逾期 / 质量分 / Token 四张指标卡、下一步卡、流水线看板、最近运行、L0 警报 |
| 写作 | 大纲、章节、创作台、摘要、故事状态；「大纲 / 正文 / Inspector」三栏 + 专注模式 |
| 世界 | 人物与设定、故事图谱、伏笔、事实；人物卡含语言指纹、行为规则、能力边界 |
| 素材 | 素材与知识库、IMA（题录入库 / 剪贴板补正文 / 导出目录扫描） |
| 质量 | 质量环与七维分数、元循环建议、文风画像、写作助手（审校部分） |
| 自动化 | 单章运行状态机、运行列表与检查点、approve/reject、L0 检查、token 账 |
| AI Studio | 设置包与提示词、工具组开关、导出/导入、**自驾面板（下一步 / 交接单 / 设计向导）** |
| 设置 | 项目配置与全局设置；模型服务与 IMA，密钥只显示掩码 |

通用交互：命令面板可搜索页面与项目；空态必须给下一步按钮，不再渲染 `null`、
`undefined`、`NaN`；质量页无 L0 报告时显示「还没有 L0 报告」+「运行检查」。
响应式三档：<1180px 导航折叠为图标；<900px 单栏 + 抽屉；表格横向滚动不撑破布局。

界面上的写操作全部走 `Store` 既有方法（Web 层不写 SQL），章节保存与 CLI 共用同一条写入路径；
删除项目/变体/段落等破坏性操作要求输入与目标 id 完全一致的确认串。
服务只绑定 `127.0.0.1`，`/api/*` 校验 `X-Longform-Token`，不做跨域写接口。

| CLI 子命令 | 用途 |
|---|---|
| `serve` | 启动本地编辑器（`--host` 只接受本机地址，`--port` 默认 8765，`--no-open` 不自动开浏览器） |
| `doctor` | 依次检查 Python、SQLite/FTS5、配置漂移、变体目录、数据库可写、LLM/IMA 状态，返回退出码 |
| `tools` | 打印工具清单（`--json` 输出结构化） |
| `projects` / `export` | 列项目；`export <project> --dir` 复用既有导出逻辑 |
| `settings get` / `settings set` | 查看脱敏配置；按 JSON 补丁写入 |
| `llm` | 向已配置的模型发起一次调用 |
| `kb import` / `kb search` | 导入文件或目录、全文检索素材 |
| `run start/status/list/resume/cancel` | 单章自动流水线：启动、查检查点、续跑/裁决、取消 |
| `lint` | 对某章跑 L0 确定性检查，只报告不改正文 |
| `usage` | 按章/模型/天/运行查看实际 LLM 调用账本 |
| `studio show/prompt/export/import/set` | AI Studio 桥接：查看设置、生成提示词、导出/导入设置包 |
| `studio next/submit/handoffs/design/cancel` | AI Studio 自驾：下一步、提交交接结果、列交接单、一键立项设计、取消交接单 |
| `loop start/report/meta` | 质量环：先 L0 再评分与定向修复；查看结果与复盘建议（默认不写正文） |
| `style distill/show/apply` | 文风蒸馏：从样本提炼九维画像，查看或应用到项目 style |

`server.py --list-tools` / `--self-test` 的行为保持不变，CLI 只是更顺手的封装。

## 3.7 API 设置与模型调用

模型服务按 **OpenAI 兼容**契约接入：`base_url`、`model`、`api_key`、`api_key_env`、`timeout`、`enabled`。
配置写在 `config/settings.json`（首次通过 `settings_set` 或设置页写入时生成），
也可用环境变量覆盖：`LONGFORM_LLM_BASE_URL` / `LONGFORM_LLM_MODEL` / `LONGFORM_LLM_API_KEY` /
`LONGFORM_LLM_TIMEOUT` / `LONGFORM_LLM_ENABLED`（IMA 对应 `LONGFORM_IMA_*`）。

- **优先级**：环境变量 > `config/settings.json` > 内置默认值。
- **密钥**：默认只从环境变量读取（`api_key_env` 指定变量名）；要落盘明文需显式写入，读取一律脱敏（`sk-***a1b2`），日志、错误与导出里都不出现明文。
- **调用**：`llm_generate` 向 `{base_url}/chat/completions` 发**单次**请求，不循环重试；
  未配置或 401/403、429、5xx、超时都转成可读中文错误，说明缺哪一项、去哪里配。
- **编辑器内生成/润色**：结果先展示，由用户确认后才写回正文。

## 3.8 知识库与 IMA

素材库升级为可检索知识库，数据仍只有 `materials` 一张表，不新增平行数据源：

| 能力 | 工具 | 要点 |
|---|---|---|
| 导入 | `kb_import` | 支持文本、单文件、目录；大文件按标题/长度分块（每条 ≤ `MAX_MATERIAL_LEN`），共享同一 `source` |
| 检索 | `kb_search` | 三字以上走 FTS5 `trigram`；两字中文、多词查询和 FTS 零结果自动补 `LIKE`；返回 `score`、`matched_terms`、`match_reason`，按同来源同内容 hash 去重 |
| 遍历 | `material_list` | 只做批量浏览与 kind/tag/chapter 过滤；传 `query` 时转交 `kb_search`，不再维护第二套检索语义 |
| 章节素材包 | `packet_assemble` / `context_estimate` | 按本章细纲、实体名、本章素材标题/标签召回；E2/E3 进可用素材，E0/E1 只作待读线索，并受独立预算约束 |

IMA 走**真实 OpenAPI**（`ima_client.py` 也保留旧 HTTP 桩兼容），凭证读取顺序为设置 → 环境变量 → `~/.config/ima/client_id` / `api_key`。订阅知识库的 `get_media_info` 统一返回 `220030`，这是权限边界，产品不绕过：

- `ima_search` / `ima_catalog_search`：可列知识库（真实字段 `kb_id/kb_name`）、搜标题、拿 `media_id`；订阅库 `highlight_content` 为空，只做题录。
- `ima_fetch`：按 id 取正文；订阅库取不到时，用户可在 IMA 客户端复制正文，用「从剪贴板粘贴」补入；同一 `source` 就地升级题录卡（`catalog_only=false`、`content_source=user_paste`），不产生重复素材。
- `ima_export_scan`：扫描 `data/ima_exports` 或指定目录的 `docx/txt/md/html`，按块切分、hash 去重入库，来源 `ima-export://<文件名>`；PDF 请先在客户端转 docx。
- 未配置时返回 `{configured: false, note: ...}`，**不抛错**，本地知识库照常可用；已配置但超时/HTTP 出错才转可读错误。
- 凭证文件可能带 UTF-8 BOM，读取必须 `utf-8-sig`，否则 HTTP header 会触发 latin-1 编码错误。

## 3.9 省 token：摘要与故事状态

输入包按固定顺序增加三个小节，并进入预算的可选丢弃序列：

| 小节 | 内容 | 开关 |
|---|---|---|
| 前情摘要 | 最近若干章的滚动摘要，替代整章正文 | `packet.include_summaries` / `summary_lookback_chapters` / `summary_max_chars` |
| 故事状态 | `character-state` / `next-hook` / `arc-status` 等 | `packet.include_story_state` / `story_state_keys` |
| 本章素材与参考 | 按细纲召回的 E2/E3 素材摘录与 E0/E1 待读线索 | `packet.include_materials` / `material_limit` / `material_budget_ratio` / `material_min_evidence` / `material_clue_limit` |

- 摘要用 `summary_set` 写入（同一章再写即覆盖，传空串删除），`summary_list` 按章号区间读取。
- 状态用 `story_state_set` / `story_state_get` / `story_state_list` 维护；未维护时该小节省略，不产生空标题。
- `context_estimate` 会给出新小节的字符占用与预算占比，并建议「用摘要替代整章」；
  估算与 `packet_assemble` 仍共用同一套取数与组装逻辑。
- 配置既可在 `config/defaults.yaml` 全局改，也可用 `config_set(key='packet.include_summaries', value=false)` 只改本项目。

## 3.10 叙事工作台：人物卡、合规预检、角色会诊

这是 2026-11 新增的产品层。入口在 Web 编辑器左侧的「人物与设定」「叙事工作台」。

| 能力 | 工具 / API | 要点 |
|---|---|---|
| 人物卡 | `bible_upsert` / `GET/POST /api/projects/{pid}/bible` | 人物卡 `data.voice` 存自称/口癖/禁用词/句长/语域 |
| 三层合规 | `compliance_check` / `POST /workbench/compliance` | 词层 → 语义层 → 事件层；红/黄/灰/绿；红色 `export_blocked` |
| OOC 检查 | `voice_check` / `POST /workbench/voice` | 禁用词、句长、口癖、串声、AI 腔、自称 |
| 角色会诊 | `role_deck_list` / `role_prompt` | 8 个角色：商业/结构/人物/台词/连续性/平台/毒舌/类型 |
| 妙招 | `trick_list` / `trick_prompt` | 12 个动作：三问开局、钩子九宫格、伏笔三连、去 AI 腔等 |
| AI 声明 | `ai_declaration` / `GET /workbench/ai-declaration` | 按项目统计生成 Markdown；不含“去 AI 痕迹” |

合规规则在 `config/compliance_rules.yaml`，可整体替换；报告存入 `audits`（`kind=compliance`）。
`chapter_export` 与 `project_export` 会读该章最近一次报告：红色且 `export_blocked=true` 时阻断导出。

## 3.11 v1.6 创作台、体检、图谱与申诉包

| 能力 | 工具 / API | 要点 |
|---|---|---|
| 章节树 | `chapter_tree` / `GET /api/projects/{pid}/studio` | 状态/版本/字数/合规风险/导出阻断/到期伏笔/素材/章纲一屏 |
| 结构体检 | `structure_check` / `GET .../structure` | 字数/段落/句长/对白占比/超长句/场景标记/章末钩子/主题句/重复开头/10 段情绪曲线；只提示不改正文 |
| 全章串声 | `voice_cross_check` / `GET .../voice-cross` | 按引号与「角色：台词」切分整章对白，逐句比对自称/口癖/禁用词/句长/语域；给“更像谁”、分差、置信 |
| OOC 四类 | `ooc_check` / `GET .../ooc` | 语言指纹 + `data.ooc.behavior_rules` + `relations[].forbidden` + `ability_limits` + `character-state`；分 voice/behavior/relation/ability |
| 故事图谱 | `story_graph` / `GET .../graph` | 人物一跳关系、伏笔甘特、故事/叙述双轨、素材双链与 hash 去重 |
| 预期账本 | `expectation_add/list/stats` / `GET .../expectations` | 满足/延迟/换维/违背；连续同态默认 3 章告警，可配 `expectation.quota.alert_after` |
| 流水线 | `pipeline_status` / `GET .../pipeline` | 细纲→组包→正文→合规→声线→导出 六步 |
| 申诉包 | `appeal_pack` / `POST .../appeal` + `GET .../appeal/download` | 第 N 章 ZIP：全部版本、相邻 diff、合规/声线报告、`meta.json`、AI 声明、README |
| 素材网络 | `GET .../materials/network` | 出链/反链、未解析链接、重复 hash 提示 |

申诉包是“给平台/编辑的说明材料”，不伪造审核记录、不承诺申诉成功；声线分差是风格距离，不是作者身份证据。

## 3.12 v1.7 审稿链、素材证据与承诺兑现

| 能力 | 工具 / API | 要点 |
|---|---|---|
| 四步审稿链 | `review_chain_prompt` / `POST .../workbench/review-chain` | 证据提取 → 角色审稿 → 最小修订 → 自检；每步含输出契约、停止条件、交接字段 |
| 素材证据审计 | `material_audit` / `GET .../materials/audit` | E0 题录/来源待补、E1 摘要或未核、E2 正文可用、E3 交叉核验；不是质量评分 |
| 素材就地升级 | `material_update` / `PATCH .../materials/{id}` | 补正文、标签和证据元数据；不可借此改写原始 source |
| 承诺兑现记录 | `expectation_add` 新可选字段 | setup / trigger / payoff / payoff_evidence；旧 note 调用不变 |
| 预期记录审计 | `expectation_audit` / `GET .../expectations` | 满足缺兑现证据、延迟缺触发、换维/违背缺理由；不自动判文学性兑现 |

提示词链只生成可复制提示词，不调用外部模型；证据等级只表示可核查程度。详见
[`docs/叙事工作台/10_材料收集与证据驱动优化_20261121.md`](docs/叙事工作台/10_材料收集与证据驱动优化_20261121.md)。

## 3.13 v1.8 统一检索与章节素材包

| 能力 | 工具 / API | 要点 |
|---|---|---|
| 两字/短词召回 | `kb_search` | FTS5 `trigram` 不足 3 字无法命中，自动走 `LIKE`；不再把两字中文当成"库里没有" |
| 多词 OR 召回 | `kb_search` | 完整查询 + 空格/标点拆词 + 中文双字切片；FTS 用 `OR` 短语，LIKE 对全部词项做子串匹配 |
| 零结果兜底 | `kb_search` | FTS 不可用、FTS 零结果或含短词时都会补 LIKE，合并后按字段与 BM25 重排 |
| 结果可解释 | `kb_search` | 新增 `score` / `matched_terms` / `match_reason`；同来源同内容 hash 去重，避免重复素材冒充多条证据 |
| 章节素材包 | `packet_assemble` | 新增可选 `include_materials`；按细纲、实体名、本章素材标题/标签召回，E2/E3 进可用素材，E0/E1 进待读线索 |
| 素材预算 | `context_estimate` | 素材小节默认不超过总预算的 12%，估算与实际输入包看到同一份素材；`include_materials=false` 可临时关闭 |
| 统一入口 | `material_list` | 只保留浏览/过滤；传 `query` 时转发到 `kb_search`，避免两套检索语义漂移 |

素材包不是"替作者写事实"：E2/E3 才可作事实引用，E0/E1 只提示"这里有线索、下一步补正文"。
仍未接入云端 embedding、重排模型或自动摘要生成，检索质量的上限仍取决于素材本身的来源与正文完整度。

## 3.14 v1.9 章节局部改稿

| 能力 | 工具 / API | 要点 |
|---|---|---|
| 精确局部替换 | `chapter_patch` / `POST .../chapters/{n}/patch` | `find` 必须逐字命中；只替换目标片段，其余正文不动；结果通过 `chapter_save` 落成 `version+1` |
| 第 N 处 / 全部 | `occurrence` / `all_matches` | 默认第 1 处；`all_matches=true` 替换全部非重叠命中；两者口径与 Python `str.count` 一致 |
| 预览不写库 | `preview=true` | 返回命中数、替换数、字数变化和改前/改后上下文摘录；不生成版本 |
| 防覆盖 | `expected_version` | 传入页面刚读到的版本号；最新版已变化时直接报错，不覆盖他人刚保存的稿 |
| 人手操作 | 章节编辑器「局部改稿」卡片 | 可手填，也可把正文选区一键填入查找框；先预览、再点「应用并生成新版本」 |
| 版本与字段 | `chapter_versions` / `chapter_diff` | 标题、状态、`data` 原样保留；旧版本仍可载入、对比和回退 |

边界：不做模糊匹配、不做整章生成、不自动改写未命中片段；正文有未保存修改或正在查看历史版本时，Web 端会阻止局部改稿，避免覆盖当前编辑内容。详见
[`docs/叙事工作台/13_v1.9_局部改稿.md`](docs/叙事工作台/13_v1.9_局部改稿.md)。

## 3.15 v1.10 写作助手

| 能力 | 工具 / API | 要点 |
|---|---|---|
| 字数统计 | `word_stats` / `GET .../word-stats` | 字数=去空白字符数（含标点）；同时给原始字符、CJK、标点、段落、句数、约 token、阅读分钟；项目只计每章最新版 |
| 抽取式摘要 | `chapter_digest` / `POST .../chapters/{n}/digest` | 首句/末句必留，其余按位置、长度、实体与悬念词打分；默认只预览，`save=true` 才写库 |
| 批量补摘要 | `chapter_digest(all_missing=true)` / `POST .../digests` | 只补原本无摘要的章节，不覆盖作者手写摘要 |
| token 账 | `token_audit` / `GET .../token-audit` | 最近 N 章全量 tokens、摘要 tokens、可省 tokens 与覆盖率；缺摘要用同一套抽取规则实算，短章如实显示“可省 0” |
| 修订建议 | `revision_plan` / `POST .../chapters/{n}/revision-plan` | 规则体检；每项给证据、原因、改法；只有唯一超长段落给拆段补丁 |
| 应用改动 | `chapter_patch` | 修订补丁仍走 v1.9 的预览/应用，生成新版本并保留 diff |
| 灵感访谈 | `inspiration_interview` / `inspiration_synthesize` | 按人物、章节、伏笔、主题出 3–10 题；回答合成 1–4 张灵感卡 |
| 灵感落库 | `materials(kind=idea, source_kind=dialog)` | 来源标「灵感访谈」，不新增表 |

边界：不调用模型生成问题/摘要/评分；不做文学评分；灵感卡是素材，不自动覆盖正文。真实浏览器已走通「三个 tab → 逐章字数 → 实时字数 → 修订体检 → 6 题访谈并保存 3 张卡 → 批量补摘要保留 tab」。详见
[`docs/叙事工作台/14_v1.10_写作助手.md`](docs/叙事工作台/14_v1.10_写作助手.md)。

## 3.16 v1.11 自动化运行

把「写一章」固化成可恢复、可拒绝、可计量的六步流水线，而不是一键生成整本。

```
plan → assemble → draft → lint → summary → commit
```

| 命令 | 用途 |
|---|---|
| `python cli.py run start <project>` | 为「下一章」新建运行并执行到人闸门 / 阻断 / 完成 |
| `python cli.py run status <run_id>` | 查看运行状态、六步检查点与本次 token 账 |
| `python cli.py run resume <run_id> --approve` | 人闸门通过：章节定稿为 `approved` |
| `python cli.py run resume <run_id> --reject` | 人闸门拒绝：运行取消，不改正文状态 |
| `python cli.py run cancel <run_id>` | 取消未完成的运行 |
| `python cli.py lint <project> --chapter N` | L0 确定性检查，只报告不改正文 |
| `python cli.py usage <project> --by chapter` | 实际调用账本（chapter/model/day/run） |

Web 编辑器对应「自动化」页签，API 为 `GET /api/projects/{pid}/automation`、`GET|POST /api/projects/{pid}/runs`、`GET /api/runs/{id}`、`POST /api/runs/{id}/resume|cancel`、`GET /api/projects/{pid}/lint`、`GET /api/projects/{pid}/usage`。

- **默认人工闸门**：正文写完、检查跑完后停在 `awaiting_human`，不自动定稿；只有 `approve` 才把章节状态置 `approved`。
- **默认预算**：`budget_tokens=200000`，`draft` 前按本次运行已花 token 核对，超限暂停为 `blocked`。
- **L0 block 即停**：禁用词、正文写入 pending 事实、空章/严重超标等直接阻断，不自动放行。
- **断点续跑**：已完成步骤不重跑；重跑只针对 `pending` / `blocked` / `failed` 的步骤。
- **边界**：单章运行；不自动发布；不自动调权；LLM 未配置时运行 `failed`；L0 只报告不改正文。详见
  [`docs/叙事工作台/15_v1.11_自动化运行.md`](docs/叙事工作台/15_v1.11_自动化运行.md)。

## 3.17 v1.12 AI Studio 模式（不配 API）

如果不想在本机配置任何 LLM API，可以让 **AI Studio 对话框主控写作**：本项目只负责存数据、
生成可粘贴的提示词、接收设置补丁。整条链路不需要 `config/settings.json` 里的模型密钥。

1. 网页里改设置：进入项目页「AI Studio」页签，填文风档案、写作要求、启用工具组与补充说明。
2. 导出：点「导出」得到 `<project>.studio.json` 与 `<project>.studio.prompt.md`，或直接把页面上的提示词复制走。
3. 粘贴到 AI Studio：把提示词作为系统指令/首条消息，由对话框主控；需要数据时按提示词里的工作流调用 MCP 工具。
4. 回写设置：如需调整，只让 AI Studio 输出一个 JSON 补丁，再用导入功能写回。

等价的 CLI：

```bash
python cli.py studio show <project>                         # 查看当前设置与启用的工具组
python cli.py studio prompt <project> --task settings       # 生成设置提示词（chapter/review 亦可）
python cli.py studio export <project> --out ./out           # 写 .studio.json + .studio.prompt.md
python cli.py studio import <project> --file patch.json --dry-run
python cli.py studio set <project> --mode ai-studio --requirements "第三人称，克制冷峻"
```

- **设置包不含密钥**：导出前递归剔除 `api_key` 等字段；导入遇到密钥键直接报错，不会静默吞掉。
- **导入只改 `style` / `config` / `studio`**：不写章节、不改项目元信息；JSON 上限 512KB。
- 工具组共 8 类（项目与设定/大纲/章节/素材与知识库/审校与质量/自动化/导出与提示词/设置与变体），
  空 `tool_groups` 表示全部启用，未匹配的工具归「其他」。
- 注意路由变化：`GET /api/projects/<pid>/studio` 现在是设置包，原「创作台」数据视图在
  `GET /api/projects/<pid>/studio/view`。详见
  [`docs/叙事工作台/16_v1.12_AI Studio桥接.md`](docs/叙事工作台/16_v1.12_AI%20Studio桥接.md)。

## 3.18 v1.13 质量环（可评分、可退出、可审计）

把「审校」升级成一个有分数、有阈值、有轮次上限的循环，而不是一次次凭感觉改稿：

```
L0 确定性检查 → 七维评分 → 定向修复 → 再评分 → 达标 / 无提升 / 轮数耗尽退出
```

| 维度 | 权重 | 看什么 |
|---|---|---|
| 留白 | 1.5 | 该停的地方有没有停，是否把话说满 |
| 节奏 | 1.5 | 长短句与场景推进的呼吸感 |
| 叙述态度 | 1.4 | 叙述者是否稳定、克制，不突然说教 |
| 感官颗粒 | 1.4 | 细节是否具体可感，而非抽象概括 |
| 人物一致 | 1.5 | 言行是否符合人物卡与已知弧线 |
| 情节推进 | 1.5 | 本章是否真的向前走了 |
| 对话潜台词 | 1.2 | 台词是否只有字面意思 |

合计 10 分，默认阈值 **8.5**，轮数 **1–5**（默认 2）。

```bash
python cli.py loop start <project>                  # 默认对最新章，max_rounds=2，threshold=8.5
python cli.py loop start <project> --chapter 3 --max-rounds 3 --threshold 8.8
python cli.py loop start <project> --chapter 3 --apply   # 应用最佳稿为新草稿版本
python cli.py loop report <project>                 # 最近质量环汇总
python cli.py loop report <project> --loop-id <id>  # 单次明细：逐轮逐维分数与违规
python cli.py loop meta <project> --window 10       # 元循环建议（只建议，不改权）
```

- **L0 先跑**：禁用词、AI 味 block、空章等硬伤直接记 `lint_blocked` 并停止，**不调模型**。
- **退出原因**：`threshold`（达标）/ `no_improvement`（提升小于 `min_delta`）/ `max_rounds`（轮数用尽）。
- **默认不写正文**：`apply=false` 只返回 `best_text`；`apply=true` 且最佳稿确有变化时才新建草稿版本。
- **元循环不改权**：`loop meta` 只统计通过率、均分、高频违规并给建议，权重/阈值调整必须人工确认。

Web 对应「质量」页签（`#/p/<pid>/quality`）：质量环表单、逐轮分数条形图、违规清单、
元循环建议、文风画像与最近运行一屏可见。详见
[`docs/叙事工作台/17_v1.13_质量环与文风蒸馏.md`](docs/叙事工作台/17_v1.13_质量环与文风蒸馏.md)。

## 3.19 v1.13 文风蒸馏（把「像某种文风」变成资产）

从作者自己的样本里提炼可复用的文风画像，而不是每次手写一遍文风描述：

```bash
python cli.py style distill <project> --chapters 1,2,3 --name 冷峻短句
python cli.py style distill <project> --files a.md,b.md,c.md
python cli.py style show <project>               # 列出画像（轻量摘要）
python cli.py style show <project> --id <id>     # 单个画像完整内容
python cli.py style apply <project> --id <id>    # 应用到项目 style
```

**输入约束**：至少 3 个样本、去空白总字数 ≥2000、总量 ≤200000、单条 ≤50000；
不足或超限直接报错，不调用模型。样本来自 `texts` 或项目已有章节均可。

**产出**：九维分析（词汇 / 句法 / 篇章 / 修辞 / 情感 / 视角 / 语气 / 格式适配 / 思维框架）、
写作基因、思维基因、禁用清单、可直接注入提示词的 `style_prompt` 与 `limitations`，
落 `style_profiles` 表。

**应用边界**：`style apply` 只写 `style_profile_id / style_prompt / thinking_genes /
writing_genes / banned_words / style_limitations` 六个键；`banned_words` 与原列表合并去重，
其它文风键原样保留，**不写正文、不覆盖原始档案**。

Web 同在「质量」页签：可粘贴样本文本或勾选章节、执行蒸馏、查看九维摘要并一键应用。
未配置 LLM 时，默认走 studio 交接（返回交接单而非报错）；显式 `provider="api"` 才会要求模型配置。

## 3.20 v1.14 AI Studio 自驾（不配 API 跑完整流程）

v1.12 的桥接只解决「导出设置/提示词」，真正要生成的环节仍调用 API。v1.14 起，
这些环节全部可以**交接**给 AI Studio 对话框完成：系统把完整指令落成 `handoffs` 交接单，
你把 AI Studio 返回的结果提交回来，系统校验、落库并继续状态机。**全程不需要
`config/settings.json` 里的模型密钥**。

```
设计 → 下一步 → 提交 → 下一步 → … → 一章完成（draft → lint → summary → 人闸门/approved）
```

### 3.20.1 交接单

- 表 `handoffs`；状态 `pending → submitted → applied`，异常终结为
  `failed / rejected / cancelled`。
- 类型：`chapter_draft / quality_critique / quality_revise / style_distill /
  project_design / outline / llm_generate / review`。
- 提交先全量校验再落库：JSON 不合法、缺维度、分数越界、正文过短都直接报错，
  交接单保持 `pending`，不写任何数据。
- 幂等：已 `applied` 的单重复提交返回 `already_applied`，不会重复写正文；`result_hash`
  用于识别同一结果。

### 3.20.2 下一步协议（`studio_next`）

按固定优先级返回可执行的下一步：

```
待处理交接单 → 运行中/待人工/阻断的 run → 立项设计 → 总纲/卷纲 → 章纲
→ 正文 → L0 → 质量环 → 摘要 → 下一章 → done
```

`action` 四种取值：

| action | 含义 | 你要做什么 |
|---|---|---|
| `tool` | 可直接调用的现有 MCP 工具 | 按 `tool` / `args` 执行 |
| `handoff` | 需要 AI Studio 生成 | 复制 `prompt` 到对话框，把返回结果提交 |
| `human` | 人工闸门或需修复 | 按 `gate` / `evidence` 处理 |
| `done` | 已规划章节全部完成 | 查看 `summary` |

### 3.20.3 CLI 完整示例

```bash
# 1) 一键立项：生成 project_design 交接单
python cli.py studio design <project> --premise "雾港、失踪的引航员、一封迟到十年的信" \
  --genre 悬疑 --chapters 30 --target-words 60000

# 2) 把 prompt 粘贴到 AI Studio，把返回 JSON 存成 design.json，再提交
python cli.py studio submit <handoff_id> --file design.json

# 3) 反复取下一步；handoff 就复制 prompt，tool 就按提示调用
python cli.py studio next <project> --json
python cli.py studio next <project>

# 4) 查看/取消交接单
python cli.py studio handoffs <project> --status pending
python cli.py studio cancel <handoff_id>
```

`submit` 也支持直接传文本：`python cli.py studio submit <id> --text "…"`。

### 3.20.4 各环节怎么交接

| 环节 | provider=studio 时的行为 |
|---|---|
| 单章运行 `run_start` | draft 不调 API，创建 `chapter_draft` 交接单，run 置 `awaiting_input`；提交后继续 lint/summary/commit |
| 质量环 `loop_start` | critique/revise 以交接单推进，逐轮落 `loop_metrics`；`apply` 语义不变 |
| 文风蒸馏 `style_distill` | 九维分析以交接单提交后校验并落 `style_profiles` |
| `llm_generate` / Web 润色 | 返回 `{status:"awaiting_input", handoff}`，不再报“未配置模型” |

`provider` 支持 `auto / api / studio`：`auto` 时有 API 走 API，否则走 studio；显式值优先。
人工闸门仍是默认，L0 阻断仍优先。

### 3.20.5 Web 自驾面板

项目页「AI Studio」新增自驾面板：下一步、交接单列表（提交/取消）、设计向导。
对应路由：`GET /api/projects/<pid>/autopilot`、`GET /api/projects/<pid>/handoffs`、
`POST /api/handoffs/<id>/submit`、`POST /api/handoffs/<id>/cancel`、
`POST /api/projects/<pid>/design`；既有 `/studio` 设置包路由不变。

**无 API 验收**：`design → next → submit` 完成一章，`llm_client.generate` 调用 **0** 次；
章节状态 `approved`、摘要写入。详见
[`docs/叙事工作台/18_v1.14_AI Studio自驾与UI重设计.md`](docs/叙事工作台/18_v1.14_AI%20Studio自驾与UI重设计.md)。

## 3.21 v1.15 世界书、引证与整本批跑

v1.15 把「设定召回、引用可信、批量推进、工程化与上手」五件事补齐，仍然是本地单机、
默认人闸门、零第三方依赖。

| 能力 | 工具 / API | 要点 |
|---|---|---|
| 条目化世界书 | `entity_meta_get/set` / `packet_assemble` | 每条设定可带 `anchors`（别名/关键词）、`priority`（always/high/normal）、`content_hash`、`last_seen_chapter`；输入包由 `_select_entities` 按「always → 命中 → 近期出现」稳定排序 |
| 输入包可解释 | `packet_trace` / `context_trace` | 说明本章带了哪些设定、丢了哪些、为什么；`context_estimate` 继续与实跑同源 |
| 世界页与 Inspector | 世界页编辑 / 写作页 Inspector | 世界页可直接编辑激活元数据；Inspector 显示本章每条设定的激活原因；自动化运行也保留同一份轨迹 |
| 引证登记与验真 | `citation_add/verify/list` + `citations` 表 | claim（正文断言）→ source（素材 id / `ch:3` / 原文）；规范化后精确匹配，不足时模糊匹配，阈值 **0.86**；失败也保留证据摘录，便于人工复核 |
| 素材地图 | `kb_categories` / `kb_search(category, expand_synonyms)` | 7 类（写作教程/人物设定/大纲情节/描写技法/世界观/范文/其他）与同义词扩展；素材带分类与使用建议 |
| 事实验真 | `fact_*` + 引证徽章 | 事实核查可引用 `citations` 证据；质量 finding 展示引证徽章，未验真不冒充已核 |
| IMA 导入 | `ima_import_urls` / `ima_upload` | 批量网页导入（≤50 条）与本地文件上传（≤50MB，preflight → 重名检查 → `create_media → COS → add_knowledge`）；缺少可选 Node/COS 脚本时返回 `degraded` 与原因 |
| 整本批跑 | `run_batch_start/status/resume/cancel` | 父运行 `kind=batch` 逐章驱动子运行（单批 ≤200 章）；每章仍是完整六步，默认人闸门、预算与 L0 阻断不降级 |
| 元循环调权守卫 | `loop_meta_apply` | 先预览后确认；阈值 6–10、七维权重合计 10、单项 1–4、单次变化 ≤0.3，写入 `audits(kind=quality_meta)` 留痕 |
| 外部通知 | `notify.py` / `cli.py notify test` | Webhook 或本地 CLI；未配置时静默降级，不影响运行 |
| 工程化 | `.gitignore` / `.github/workflows/ci.yml` / `.gitee/workflows/ci.yml` / `scripts/git_init.ps1` | CI 跑单测、配置漂移、冒烟与自检；`git_init.ps1` 只在未初始化时 `git init`，不自动提交 |
| 备份与恢复 | `scripts/backup_db.py` / `restore_db.py` | 复制式备份，恢复前要求显式确认；仍建议备份前停写 |
| 题材模板与向导 | `GET /api/studio/templates` / `#/onboarding` | 5 个题材模板（仙侠/都市/悬疑/克苏鲁/历史）；三步向导（题材 → 视角与字数 → 生成项目）；偏好落 `profiles/_default/profile.json` |
| 偏好接口 | `GET/PUT /api/preferences` | 读写题材、视角、文风、每章字数、禁用词与默认模板；不新增数据表 |

**边界**：IMA 文件上传依赖可选 Node/COS 脚本，缺失时明确 `degraded`，不伪造成功；
文风盲测为本地八维特征对照（含禁用词扣分），非 LLM 评判、不自动改文风；仓库不会自动 `git init`，需要版本管理请手动执行脚本。

## 4. 作品变体（同一作品的多套写法）

写作方向会变。同一部作品可能同时有「正传（第三人称、2500 字/章）」和
「二创（第一人称、1500 字/章、沿用原世界观但改了时间线）」——两套配置如果混在一个项目
里，改到第十章一定乱。

**变体 = `profiles/` 下的一个文件夹**，里面就三样东西：

```
profiles/二创-雾港同人/
├── profile.json     元信息 + overrides（覆盖哪些默认配置）+ 正文保存到哪
├── overrides/       风格指南、AI 腔红线词表、相对原作的设定补丁（给人/技能读）
└── exports/         导出的正文（删了可重建）
```

| 你要做的事 | 调用 |
|---|---|
| 看看有哪些变体 | `profile_list`（传 `project` 会标出当前绑定的） |
| 加一个变体 | `profile_create(name=..., base="_default", export_dir=...)` |
| 把正文存到我的目录 | `profile_set_export_dir(name, dir)` —— `dir` 可以是任意本地绝对路径 |
| 把变体绑到项目 | `project_init(profile=...)` 立项时绑，或 `profile_bind(project, name)` 后期换 |
| 删一个变体 | `profile_delete(name, confirm=<同名>)` —— 可先 `dry_run=true` 预览 |
| 导出正文 | 绑定后 `chapter_save` 自动落盘；`chapter_export` 随时补导 |

关键取舍（已在代码里固化）：

- **SQLite 是唯一权威，`exports/` 是派生视图。** 改稿默认覆盖同名文件，
  设 `export.versioned: true` 则每版各留一份（`第003章_开端_v2.md`），永不覆盖。
- **变体的 `overrides` 盖住项目配置**，因为变体存在的意义就是"刻意覆盖"；
  但变体被删掉时读操作不失败，会回退为项目配置并在包尾说明。
- **变体的 `style` 覆盖只影响喂给模型的文风档案**，不写回数据库——
  换变体试两种声线时，原始文风档案始终完好。

`profile.json` 用**带注释的 JSON**（支持 `//` 与 `/* */`，解析器自带、零依赖）。
理由：标准库没有 YAML，为一个配置文件引入第三方依赖不值得；而手写配置时
"临时注释掉某一行"是最高频的动作。

## 5. 后期怎么调整（重点）

| 你想改什么 | 改哪里 | 影响范围 |
|---|---|---|
| 增删某个技能/专家/工具 | `config/registry.yaml` | 全局开关，无需删文件 |
| 换主力模型、调整温度/长度 | `config/defaults.yaml` → `models` **与** `config/models.json` → `tiers`（双写） | 编排层与写作层 |
| 改某阶段用哪个档位 / 找谁 / 交什么 | `config/models.json` → `stages` | `stage_advice` 与 `outline_next` |
| 改一致性判严/判松 | `config/defaults.yaml` → `audit` | 审计技能 |
| 改输入包各小节开关 | `config/defaults.yaml` → `packet.include_*`（全局生效）或 `config_set(key='packet.include_facts', value=false)`（仅本项目） | 输入包组装 |
| 改摘要 / 故事状态是否进包 | `config/defaults.yaml` → `packet.include_summaries` / `packet.include_story_state`（及 `summary_lookback_chapters` / `story_state_keys`） | 输入包组装 |
| 改章节素材包 | `config/defaults.yaml` → `packet.include_materials` / `material_limit` / `material_budget_ratio` / `material_min_evidence` / `material_clue_limit` | 输入包组装 |
| 换模型服务 / 接 IMA | `config/settings.json` 或 `python cli.py settings set`；环境变量 `LONGFORM_LLM_*` / `LONGFORM_IMA_*` 优先 | 仅本机外部调用，不参与项目/变体写作参数合并 |
| 改设定召回策略 | `config/defaults.yaml` → `packet.entity_match`（keyword/all/embedding） | 输入包组装 |
| 改上章引用长度 | `config/defaults.yaml` → `chapter.tail_words` 或 `config_set(key='chapter.tail_words', ...)` | 输入包组装 |
| 改正文导出格式 / 是否保留每版 | `config/defaults.yaml` → `output.export_format`（md/txt）/ `output.save_every_version` | 变体导出 |
| **换写作方向 / 做二创** | 新建一个变体文件夹 + `profile_bind` | 仅该项目，正文库不动 |
| **改正文保存到哪个目录** | `profile_set_export_dir` 或变体的 `export.dir` | 仅该变体 |
| 把变体放到别的盘 / 网盘 | 环境变量 `LONGFORM_PROFILES_DIR` | 全局 |
| 改数据结构（如给人物加新字段） | `schemas/*.json` + `mcp/longform_mcp/store.py` | 需同步，见 ARCHITECTURE.md |
| 改流程顺序、加新环节 | `skills/*/SKILL.md` 的 `## 流程` | 仅该技能 |
| 改专家口吻与评判标准 | `experts/*.md` 的「判断标准」段 | 仅该专家 |
| 加一个全新能力 | 在 `store.py` 加表（**记得登记 `_CHILD_TABLES`**）+ `tool_specs.py` 声明 + `handlers.py` 实现 + 写一个 `SKILL.md` | 三层联动 |
| 改大纲结构（如加"部"这一级） | `schemas/outline_node.schema.json` + `store.py` 的 `OUTLINE_LEVELS` | 大纲三级与 `outline_next` |

> 所有改动都建议先在 `examples/` 的演示项目上跑一遍再用于正式作品。

## 6. 已知边界

- MCP 服务器是**单机本地**方案，不做多端同步；需要协作请自行把数据目录放进网盘/Git。
- 它**不替你写正文**，只保证写正文时上下文是完整、一致、可追溯的。质量上限仍取决于模型与你的人工判断。
- 事实核查工具只做「登记与追踪」，**不自动联网验证**（避免幻觉背书）；核查结论必须由人或可信来源填写。
- `packet.entity_match=embedding` 尚未接入嵌入模型，运行时会退化为 `keyword`
  并在包尾「组装说明」里注明；要真正启用需自行实现召回逻辑（见
  ARCHITECTURE.md 第 9 节）。
- 未启用 WAL：备份时直接复制 `longform.db` 即可，代价是并发写入性能一般。
  单用户场景无影响；若改为多进程高频写入，可自行开启 WAL（同时要改备份策略）。
- 变体配置用**带注释的 JSON** 而非 YAML：标准库没有 YAML，为了一个配置文件引入
  第三方依赖不值得；代价是不能直接用 YAML 的锚点/多文档特性，语法错误会定位到行列号。
- `profiles/` 与数据库是**两个独立的落点**：换变体不会动数据库，删变体也不会删章节。
  但反过来，`exports/` 里的 md 只是派生视图，**手工编辑它不会同步回数据库**——
  要改正文请走 `chapter_save`。
- 一个项目同时只能绑定一个变体（`profile_bind` 是覆盖语义）。需要"同一章两种声线并存"
  时，用 `chapter_versions` 存两版，而不是建两个变体来回切。
- **模型配置有两份**（`config/models.json` 与 `config/defaults.yaml`）：标准库没有 YAML
  解析器，MCP 只能读 JSON，而 YAML 那份给人读、给文档引用。这是刻意的双写，
  由 `scripts/check_config.py` 兜底——它同时比对 registry 与代码工具集合，
  建议把它挂进 CI。
- `stage_advice` **只建议不强制**：MCP 无法替你切换模型。返回里的 `tier`
  （planning/production/qa/utility）是跨平台语义锚点，只有两三个模型的平台
  可自行把档位映射到自己的模型上。
- **`config/defaults.yaml` 在进程启动时读取并缓存**（按文件 mtime 失效）。
  改完重启连接器即可；mcporter 每次调用都会重启 stdio 服务，所以通常无需手动操作。
  项目级 `config_set` 的优先级高于它，变体 `overrides` 又高于项目配置。
- **`profile_set_export_dir` 会重写 `profile.json`**：由于解析器会先剥离 `//` 与
  `/* */` 注释，改写后文件里的注释会丢失（这是"带注释的 JSON"读改写的固有代价）。
  需要保留注释时请手工编辑该文件。
- **`config/registry.yaml` 不是运行时开关**：MCP 服务始终暴露全部工具，
  该文件用于技能/专家的启用声明与 `check_config.py` 的漂移校验。
- 卷不改变章号：`chapters.n` 仍是扁平整数，导出文件名仍是 `第{n:03d}章`。
  卷归属一律反查（`outline_brief` 返回的 `volume` 字段）；按卷分目录导出尚未支持。
- **Web 编辑器只服务本机**：仅绑定 `127.0.0.1`，`/api/*` 必须带启动时生成的一次性令牌；
  不要改成 `0.0.0.0` 或对公网做反向代理——拿到 URL 里的令牌就等于拿到写权限。
- **`config/settings.json` 的明文密钥**：把 `api_key` 写进文件是用户的显式选择，
  读取与返回一律脱敏，但挡不住能读该文件的人。优先用 `api_key_env` + 环境变量，
  并确保它不进版本库、不进备份、不进导出。
- **IMA 已接真实 OpenAPI**：订阅知识库正文受 `220030` 权限限制，不抓取、不破解；只做题录、用户主动粘贴、客户端导出扫描。没有配置/凭证时 `ima_search` / `ima_fetch` 降级返回说明，本地知识库与写作流程不受影响。不要把空 `highlight_content` 当摘要证据，不要把题录说成全文研究。
- **IMA 文件上传是可选增强**：`ima_upload` 走 `create_media → COS → add_knowledge`，需要可选的 Node/COS 脚本；脚本缺失或未配置时返回 `degraded` 与原因，不会退化成伪造成功。纯 HTTP 的 `ima_import_urls` 不依赖 Node。
- **文风盲测只做本地特征对照**：`style_blind_test` 用八维文体特征与禁用词扣分给出 0–100 分并写审计，非 LLM 评判，也不等同于客观质量结论。
- **仓库不会自动初始化 Git**：v1.15 补了 `.gitignore`、GitHub/Gitee CI 与 `scripts/git_init.ps1`，但脚本只在手动执行时 `git init`，且不会自动 add/commit。
- **FTS5 可用性取决于本机 SQLite**：`materials_fts`（trigram）建不起来时
  `kb_search` 自动回退 `LIKE`，返回结构一致，只是大库检索会慢一些。`python cli.py doctor` 会报告实际状态。
