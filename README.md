# 法小智 · 法律类案检索与研究 Agent

> 阶段 2 第 1–4 阶段（2-1 / 2-2 / 2-3 / 2-4）工程成果：Harness 内核 + 三条门禁 + 类案研究链路 + 最小验收界面
> + 降级矩阵与稳定性实测 + 重试闭环 + 数据/测试卫生 + Word 模板 v1
> + 多源可插拔来源与本地依据库（法条/判例命中复用、逐字核验、双源印证）
> + **主路径改造：用户自带材料为主、检索为辅**（材料角色/标识/版本、批量上传、补充检索、来源冲突与人工裁决）。
> 文档：`docs/阶段文档/法小智agent开发文档/`（阶段 2-1 ~ 2-4 开发文档、材料为主路径说明、降级矩阵表、MCP 稳定性与配额基线）；
> 决策台账见 `docs/项目状态/法小智agent开发文档.md`。

## 动态追问（2026-09-20）

每次只追问一个关键情况，补充后重新判断是否继续，最多三轮且可提前结束。界面显示当前/最大轮次和后续最多补充次数。可以“跳过，直接看分析”，保留已填文字，并说明未知情况对回答的限制；引用核验条件不变。

`POST /api/session/{sid}/consult/answer` 在 `facts` 外接受可选 `skip_clarification: boolean` 和 `expected_round: number`，旧客户端仍兼容。跳过时 `facts` 可为空；跳过不伪装成用户事实、不增加轮数、不中断失败重试语义。现有会话内存存储限制不变。

## 法律问答入口（2026-09-20 纠正）

主要用户为律师、实习律师与法务，普通用户同样可以访问和使用。主要用户不构成专业表达门槛，用户可以用日常语言描述事情，必要的信息由系统追问补充。

`/consult` 统一称为“法律问答”，导航与输入文案简明直白。回答保留引用依据、核验状态、需确认的情况与下一步建议，支持 Word 导出。正常界面继续隐藏模型、并发等技术配置，异常和离线演示仍明确提示。9 月 19 日的“法律问题分析”命名及专业化输入要求已撤回。

验证：在 `frontend/` 运行 `pnpm test`、`pnpm typecheck`、`pnpm lint`、`pnpm build`；访问 `http://localhost:5174/consult`。本轮仅调整界面与文案，不改变追问、引用核验、接口协议或数据留存规则。

## 咨询解答失败修复（2026-09-19）

修复咨询提示词把所有数组要求为字符串、而校验器要求结论为对象的冲突。最终输出使用同一份 JSON Schema；有限格式修正后仍失败时，返回真实错误并允许在原会话重试，保留已回答的事实和已取得的依据。格式兼容不生成来源或引用，引用核验规则继续生效。

离线回归在项目目录运行：

```bash
uv run pytest
cd frontend
pnpm test
pnpm typecheck
pnpm lint
pnpm build
```

修复记录：`../../docs/evidence/法小智agent开发文档/咨询解答恢复修复/修复说明.md`。
咨询私有事实仍只留在当前后端进程内存；同会话重试可保留，重启后不能恢复。本次修复不改变该存储约定。真实模型复验与运行服务切换状态以修复记录为准。

## 一、它是什么

```
【材料为主】我把自己的案例材料传进来 → 逐个「本人已核验」 → 候选池（我的材料在前）
  → 我勾选/排除并「确认样本」        ← 人工门，未确认拿不到矩阵
  → 逐案提炼要素 → 横向对比矩阵 + 观点分布（代码计算）→ 带逐字引用的结论 → 导出 Word
  → 材料不够 或 我主动点「补充检索」时才联网（补充来源单独标注，不改变材料主体地位）
  → 同案号原文不一致 = 来源冲突 → 默认拦住 → 我显式点「以我上传的材料为准」才放行（导出必带标注）
```

没有材料时，行为与 2-1~2-3 完全一致（检索主路径）：

```
我提一个法律议题 → 检索（北大法宝 MCP）→ 候选池 → 确认样本 → 提炼 → 矩阵/分布 → 结论（带引用）→ 导出 Word
                引用核验没过 / 样本残留 / 话说过头 / 来源冲突 → 导出被拒
```

一句话原则：**模型负责「想」，脚本负责「顺序」，钩子负责「不许」。**

## 二、启动（默认离线可跑，不需要任何密钥）

```bash
cd projects/法小智agent开发文档
uv sync
uv run python -m app.server            # → http://127.0.0.1:8010/
```

端口用 **8010**（8000 被氢能项目占用）。

> 上线/联调前建议先跑一次 `uv run python scripts/clean_runs.py`（只报告）看数据目录是否干净。

## 三、配置

| 文件 | 作用 | 是否可提交 |
| --- | --- | --- |
| `.env` | 密钥与地址（模型 Key、北大法宝 MCP 地址与 Token） | ❌ 已被 `.gitignore` 排除 |
| `.env.example` | 模板 | ✅ |
| `config.yaml` | 策略：阈值、门禁开关、表达边界正则、MCP 字段映射 | ✅ |

`.env` 里需要填的地方：

- `MODEL_API_KEY`：DeepSeek 的 API Key（留空 → 自动进入离线 `stub` 模式）
- `PKULAW_URL_*`：北大法宝各家服务的地址（留空 → 检索返回「接口失败」，**不等于没有相关案例**）
- `PKULAW_MCP_TOKEN`：北大法宝鉴权 Key（带不带 `Bearer ` 前缀都行）

## 四、北大法宝 MCP 探针（换地址或接口变更后先跑这个）

```bash
uv run python scripts/probe_mcp.py          # 只拉 tools/list，不产生检索调用
uv run python scripts/probe_mcp.py --call   # 额外各 1 次真实检索（会产生费用）
uv run python scripts/probe_mcp.py --stress 20 --stress-tool search_cases --stress-delay 1.0
                                            # 2-2：受控次数连续调用，出 p50/p95、失败率、是否限流（只调检索、不调模型）
```

输出：每个地址能否连上、有哪些真实工具名、必填参数，以及给 `config.yaml: mcp.field_map` 的校准建议。
**密钥不会被打印**，地址按脱敏形式显示。

### 已实测的服务 → 工具对应（2026-09-17）

| 逻辑名 | 真实工具名 | 用途 |
| --- | --- | --- |
| `search_statutes` | `mcp-law-search-service.search_article` | 法条检索（返回 `article` 全文 + `timeliness` 效力状态 + `implementation_date`） |
| `search_cases` | `mcp-case-search-service.search_case` | 类案检索（返回 `identified` 裁判理由 + `caseNumber` + `courthouseName` + `decisionDate`） |
| `exact_statute` | `mcp-fatiao.get_law_item_content` | 按法规名 + 条号取条文全文 |
| `recognize_statutes` / `recognize_case_number` | `law_recognition.law_recognition` / `case_number_recognition.anhao_recognition` | 从文本里识别法条 / 案号并与法宝库核对 |
| `statute_history` | `pkulaw-statute-history.get_article_history` | 条文沿革（判断某时点是否有效） |
| `hyperlink` | `add-doc-link.get_linked_content` | 给文本加法规超链接 |
| `fix_hallucination` | `pku_citation_validator.adjust_provisions` | 修正生成幻觉-法条 |
| 聚合端点 | 16 个工具合一（`<服务>.<工具>` 命名空间） | 兜底 |

> 同一个服务在**专用端点**与**聚合端点**上的工具名不同（聚合端点带命名空间前缀）。适配层会按端点自动解析，无需人工判断。

## 五、验证方法

```bash
uv run pytest          # 全部 mock 自动化测试（离线，无需网络与密钥）→ 394 通过 / 0 跳过
uv run ruff check .    # 静态检查
```

界面行为回归（真实 Chrome headless，零模型/零检索调用；需服务已在 8010 运行）：

```bash
node scripts/verify_stale_session.mjs   # 旧存档+服务重启后不得卡死会话，上传必须成功（2-4 缺陷 3 的回归）
```

法律咨询（2-5；**会产生真实费用，必须加 `--yes`**）：

```bash
uv run python scripts/smoke_real.py --mode consult --consult-scene C1 --yes   # 事实完整（约 ¥0.14）
uv run python scripts/smoke_real.py --mode consult --consult-scene C2 --yes   # 事实不足，会走追问（约 ¥0.14）
uv run python scripts/smoke_real.py --mode consult --consult-scene C3 --yes   # 检索不到依据（约 ¥0.20）
```

> 咨询的**问题原文不落盘**（C-12）：不要在里面写真实当事人的敏感信息。
> 追问上限 3 轮是**硬约束**（由代码计数，不靠模型自觉）；用户继续提问不受限。

合同审查（2-6；**会产生真实费用，必须加 `--yes`**）：

```bash
# 合同必须是可复制文字的 docx/pdf/txt/md（扫描件暂不支持）；不要用真实涉密合同
uv run python scripts/smoke_real.py --mode contract --contract-file ./合同.txt --stance party_b --yes
uv run python scripts/smoke_real.py --mode contract --contract-file ./合同.txt --stance party_a --yes
```

> `--stance`：`party_a` 甲方 / `party_b` 乙方 / `neutral` 中立。**立场不同，风险结论不同**。
> 合同原文按 C-12 **不落盘**；重启后按 `run_id` 恢复会明确拒绝。

真实链路冒烟（**会产生真实费用，必须加 `--yes`**）：

```bash
uv run python scripts/smoke_real.py --mode resume --yes       # M1 真实链路+导出 + M5 重启恢复（约 ¥0.25）
uv run python scripts/smoke_real.py --mode bad-address --yes  # M2 地址错（自由本机地址，几乎不花钱）
uv run python scripts/smoke_real.py --mode bad-token   --yes  # M3 Token 错（不调模型）
uv run python scripts/smoke_real.py --mode stability --stress 20 --yes   # M4 稳定性（只调检索）
```

材料为主路径冒烟（2-4；**需要你提供有权使用的公开裁判文书**）：

```bash
uv run python scripts/smoke_real.py --mode material --material-dir ./公开案例 --yes
uv run python scripts/smoke_real.py --mode material-supplement --material-dir ./公开案例 --yes
uv run python scripts/smoke_real.py --mode material-conflict   --material-dir ./公开案例 --yes
```

> `--mode material` 材料充足时**不调法宝检索**，只花模型费（约 ¥0.10–0.20）。
> 材料目录下的 txt/md/docx/pdf 会被全部使用；**不要放真实客户的涉密材料**（本阶段无账户隔离）。

界面截图（本机 Chrome headless，零调用）：

```bash
SESSION_ID=s_xxx RUN_ID=yyy STAGE=阶段2-2 node scripts/capture_evidence.mjs   # 恢复会话截图
SESSION_ID=s_xxx DEGRADE=1 STAGE=阶段2-2 node scripts/capture_evidence.mjs    # 降级/重试面板截图
PROJECT_DIR=法小智agent开发文档 STAGE=阶段2-4 node scripts/capture_evidence.mjs --materials   # 2-4 材料为主全流程（离线夹具，零真实调用）
```

离线场景可切换（用于回归与降级演练）：

```bash
OFFLINE_SCENARIO=no_match uv run pytest
OFFLINE_SCENARIO=interface_error uv run python -m app.server
```

可选值：`clean`（默认）、`no_match`、`interface_error`、`abstract_only`、`insufficient`。

演示模式（允许引用合成夹具数据，界面会显示黄色提示条、导出文档会标注来源）：

```yaml
# config.yaml
policy:
  allow_synthetic: true      # 默认 false；**默认关闭是刻意的**
```

## 六、多源与本地依据库（2-3 新增）

- **来源可插拔**：`config.yaml: sources.<kind>.providers` 决定优先级（默认 `local_library → pkulaw_mcp → fixtures`）。
  新增来源只需加一个 provider，**不改业务代码**。
- **本地依据库**：把真实检索到的**公开**法条与判例原文跨会话沉淀到 `data/library/`（不提交仓库），
  支持「同一检索式命中复用」与「按标识取原文」，法宝抖动时**已入库内容仍能核验并导出**。
  - **三分区**：`citable`（通过门禁，可引用）/ `clue`（仅摘要或未过门禁，**不可引用**）/ 不入库（失败、用户材料、夹具）。
  - **不做本地优先检索**：候选池始终来自实时检索，避免偏向历史数据。
- **双源印证**：本地与法宝一致 → `dual`；单侧 → `single_mcp` / `single_local`；**不一致 → `conflict`（门禁 R7，阻止导出）**。
- **隐私红线**：**用户上传材料原文绝不入库、绝不落盘**；库中不含密钥与真实地址。
- 查看状态：`curl -s 'http://127.0.0.1:8010/api/library'`；导入合法文件：
  `uv run python scripts/import_library.py --file x.json --apply`（默认 dry-run，**不抓取任何站点**）。

### 2-4 材料为主路径接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `POST` | `/api/session/{id}/upload` | 批量上传（字段 `files` 可重复；单文件旧字段 `file` 仍兼容；可选 `role=case\|statute`）。**部分失败不算整单失败** |
| `POST` | `/api/session/{id}/material/{source_id}/verify` | 「本人已核验」（未核验不得引用） |
| `POST` | `/api/session/{id}/material/{source_id}/role` | 改材料角色（改后**核验作废**，需重新核验） |
| `GET` | `/api/session/{id}/materials` | 材料清单（角色/标识/核验/版本/定位索引，**不含原文**） |
| `POST` | `/api/session/{id}/supplement` | 补充检索（受 `sources.supplement.max_supplement_rounds` 限制，超限 429 `supplement_exhausted`） |
| `POST` | `/api/session/{id}/conflict/resolve` | 人工裁决，只接受 `{"decision":"prefer_user_material"}`；非冲突来源返回 409 `not_conflict` |

策略开关在 `config.yaml`：`materials.*`（角色默认值/自动识别/批量上限/同标识取代/正文标识抽取）与
`sources.supplement.*`（材料不足自动补、补充轮数上限、补充来源标注）。

## 七、数据卫生（2-2 新增，重要）

跑测试**不会再污染真实数据**：`tests/conftest.py` 会把 `runs/`、`exports/`、`data/metrics.jsonl`
全部重定向到临时目录，并在真实目录上放**哨兵**（测试结束时比对，有新增即报错）。

指标分真实/测试两个来源，看真实成绩必须加 `?source=real`：

```bash
curl -s 'http://127.0.0.1:8010/api/metrics?source=real'    # 只看真实运行（北极星指标用这个）
curl -s 'http://127.0.0.1:8010/api/metrics?source=test'    # 只看自动化测试
```

清理历史残渣（**幂等，默认只报告，不删除任何东西**）：

```bash
uv run python scripts/clean_runs.py           # 只报告
uv run python scripts/clean_runs.py --apply   # 过期锁清理；测试残渣移入 runs/archive-tests/；
                                              # 指标里的测试行拆到 data/metrics.test-archive.jsonl
```

> 背景：2-2 实测发现旧版本跑 pytest 会把测试残渣写进真实 `runs/` 与指标文件，
> 导致「引用可溯源率」等指标混入测试数据而不可信。现已修复并有回归测试（`tests/test_hygiene.py`）。

## 八、目录结构

```
app/
├── server.py            FastAPI 应用、路由、SSE、静态页（2-4：批量上传/补充检索/冲突裁决/材料清单）
├── config.py            .env + config.yaml；启动自检（不回显密钥）
├── session.py           会话态（纯内存）+ 材料登记表 + 冲突裁决 + 导出前置条件判定
├── materials.py         【新】材料角色/标识/版本/定位索引（**不含原文**）
├── models.py            状态枚举、Source、ToolResult、Claim、GateReport
├── llm.py               模型客户端（DeepSeek / 离线 stub 双 provider）
├── loop.py              单一 Agent 循环 + 默认钩子
├── hooks.py             四事件注册表（阻断 / 仅观察两类）
├── permissions.py       权限三道闸门
├── assemble.py          工具池与系统提示词动态装配
├── knowledge.py         知识目录与惰性加载（知识包不含法条）
├── observability.py     日志 / 指标 / 轨迹（全部脱敏，不写原文）
├── prompts.py           检索 / 提炼 / 综合三份提示词
├── gates/               ⭐ 引用门禁 / 样本锁 / 表达边界 + 执行器
├── tools/               工具注册表、法条与案例检索、文档解析（角色/标识判定）、MCP 客户端、离线夹具
├── workflows/           编排原语（journal + checkpoint + 材料文本落盘前脱敏）、类案研究 workflow（材料为主）
└── render/docx.py       研究备忘录 Word 渲染（材料来源清单 / 来源构成 / 裁决标注）
knowledge/               领域口径（不含法条）
fixtures/                合成夹具（虚构案号，默认不可引用）
web/index.html           单页验收界面
tests/                   mock 自动化测试
scripts/probe_mcp.py     MCP 探针（含 --stress 稳定性实测）
scripts/smoke_real.py    真实链路冒烟（M1–M5 + 2-4 的 material/material-supplement/material-conflict，需 --yes）
scripts/import_library.py 合法文件导入本地依据库（默认 dry-run）
scripts/capture_evidence.mjs  界面截图（headless Chrome）
scripts/clean_runs.py    数据目录清理（幂等、默认 dry-run）
```

## 九、必须知道的边界（不是 bug）

1. **接口失败 ≠ 没有案例**：检索失败时界面写「未获得可核验依据。这不等于无相关案例」，且不产出结论。
2. **合成夹具默认不可引用**：夹具来源 `synthetic=true`，被引用门禁 R2 拦截，导出被拒。
3. **用户上传材料不落盘**：只进内存；落 journal 的只有指纹与长度，恢复后无法逐字核验 → **导出被拒**（宁可拒绝，不放宽）。
4. **样本残留必拦**：被排除的案例若仍出现在结论里，导出会被点名拦住。
5. **越界表述必拦**：全国比例、胜诉率、确定性承诺、存在性断言等，命中即要求改写，导出被拒。
6. **没有账户与数据隔离**：本阶段为本地单用户工具，对外给他人使用**之前**必须补注册登录与数据隔离。
7. **接口失败后可以重试**（2-2 新增）：失败的步骤会被标记，点「重试」从失败那一步继续；
   **已完成的步骤命中 journal 存档，不重跑、不重复计费**。密钥错误这类重试无意义的失败会明确区分（不鼓励重试）。
   > 实测踩过的坑：如果失败步骤的存档不被作废，重试会原样复用失败结果 —— **等于没重试**。已修并有回归测试。
8. **失败的步骤不得被说成「候选池为空」**：接口失败时文案固定为「检索接口调用失败，本次未获得可核验依据。
   这不等于无相关案例」，且**不产出任何结论**；与「没匹配到」是两句不同的话。
9. **指标分三种来源**：`real`（真实外部调用）/ `offline`（stub 或故障演练）/ `test`（自动化测试）。
   看真实成绩必须用 `?source=real`；离线演练不参与北极星指标统计。
10. **Word 模板 v1**：页脚每页都有「须经人工复核」+ 页码 + 模板版本（`research_memo_v1`）+ 来源声明；
    引用块统一为「案号→原文→链接」；**被拦截引用的原文片段绝不进文档**。
11. **熔断**（2-2 新增）：同一端点连续可重试失败达阈值后快速失败，60 秒后放行一次探测；
    鉴权错/结构错不计入熔断（改好密钥即可恢复）。配置在 `config.yaml: mcp.circuit_breaker`，可关闭。
12. **来源冲突必拦**（2-3 / 2-4）：同一案号的原文在不同来源间不一致 → 门禁 R7 拒绝，导出被拒；
    **不交给模型裁决**；用户材料与法宝冲突时，只有人工显式点「以我上传的材料为准」才放行，且导出带标注。
13. **材料为主、检索为辅**（2-4）：你上传的案例材料进候选池并**排在最前面**；材料 ≥ 2 篇时**不调法宝检索**；
    材料不足自动补一次；「补充检索」最多 2 轮，新增来源标为「补充来源」，不改变材料主体地位。
14. **材料角色与标识是代码判定的**（2-4）：案例类抽案号、法条类抽「《法规名》第 X 条」，抽不到就用文件名并**如实标注「未识别到案号」**；
    角色可上传时指定或上传后手动改（改后核验作废）。
15. **同标识多版本只采用最新**（2-4）：旧版标 `superseded`，不参与候选与引用，界面会提示「检测到 N 个版本」。
16. **材料原文的落盘口径**（2-4，实测修过真缺陷）：材料原文**会**进入本次模型调用的提示词（否则无法提炼与逐字引用），
    但**不会**进日志/轨迹/运行存档/会话消息/本地依据库；提炼结果里含材料原文片段（≥40 字连续片段）时，**写盘前自动脱敏**并留痕 `redacted_material`。
    > 实测踩过的坑：修复前模型提炼出的 `facts/holding/basis` 会把材料原文片段原样写进 `runs/*.journal.jsonl` 与 `*.output.json`。已修并有回归测试。
17. **重启后材料不恢复**（2-4，设计而非故障）：若某次研究用过你的材料，重启后按 run_id 恢复会**明确报错「材料不落盘，无法继续」**，
    不会用脱敏占位冒充结果。请重新上传材料后重新发起。
