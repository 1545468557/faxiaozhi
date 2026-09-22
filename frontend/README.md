# 法小智 · 正式前端（阶段 3）

本目录是法小智的**正式前端**：Next.js 16（App Router 风格）+ React 19 + TypeScript + Tailwind 4。
界面视觉沿用已确认的浅色墨青设计；**业务能力全部来自后端** `projects/法小智agent开发文档/`（Python + FastAPI）。

> 重要：实际运行器是 **vinext + Vite + Cloudflare Workers 适配**，不是 `next dev`。
> 请用下面的 `pnpm` 命令，不要直接改成 `next dev`。

## 一、启动

```bash
# 终端 A：后端（必须是这个，前端才有数据）
cd projects/法小智agent开发文档
uv run python -m app.server            # http://127.0.0.1:8010

# 终端 B：前端
cd projects/法小智agent开发文档/frontend
corepack pnpm@11.19.0 install --frozen-lockfile
corepack pnpm@11.19.0 dev              # http://localhost:5174
```

Node.js ≥ 22.13。首次安装若没有 pnpm：`npm install -g pnpm@11.19.0`。

## 二、配置（**这里没有任何密钥**）

`.env.example` → 复制为 `.env.local`，只有一项：

```dotenv
BACKEND_BASE_URL=http://127.0.0.1:8010
```

- 只被服务端（代理层）读取，**不加 `NEXT_PUBLIC_` / `VITE_` 前缀**，不进浏览器。
- 模型密钥（`MODEL_API_KEY`）与北大法宝 Token（`PKULAW_MCP_TOKEN`）**只放在后端** `projects/法小智agent开发文档/.env`。
  前端工程里出现任何密钥都算事故（AGENTS.md 底线 1）。

## 三、架构：为什么要有 `app/api/bff/**`

浏览器**不直连后端**，只访问同源 `/api/bff/**`，由这些 route handler 转发到后端：

| 原因 | 说明 |
| --- | --- |
| 后端没有 CORS 头 | 浏览器直连会被拦；同源代理不需要后端开口子 |
| 收口 | 后端地址、错误原文、下载头只在一处处理 |
| 本机限制 | 代理层校验 `Host`/`Origin` 只允许本机（后端也只监听 127.0.0.1） |

实现要点（见 `lib/server/bff.ts`）：

1. **SSE 真流式**：`text/event-stream` 的响应体原样透传，不 `await text()`，并关掉缓冲；
2. **错误结构不改写**：后端 `{error:{code,message}}` 原样返回；
3. **multipart / 下载**：不手写 `content-type`（boundary 会坏）；透传 `Content-Disposition`；
4. 运行器不支持 catch-all 路由（`[...path]` 一律 404），所以是 **23 条显式路由**。

前端的业务代码只从 `lib/api/` 取数：

| 文件 | 作用 |
| --- | --- |
| `lib/api/client.ts` | 集中式请求封装（组件里不允许裸 `fetch`） |
| `lib/api/errors.ts` | 39 条错误码 → 文案与行为（后端 message 优先，本地兜底） |
| `lib/api/session.ts` | `session_id` 生命周期（sessionStorage + 会话丢失静默重开一次） |
| `lib/api/sse.ts` | SSE 客户端（11 种事件归约，`done`/`error` 收尾，忽略心跳） |
| `lib/api/types.ts` | 与后端 `/docs`（OpenAPI）对齐的类型 |

## 四、离线模式与真实模式（务必分清）

后端 `MODEL_PROVIDER=stub`（或没有 Key）时会进入**离线模式**：不调用真实模型。
界面会把这件事**显式标出来**（状态条「离线模式」+ 运行面板标记 + `meta.warning`）——
离线结果**不得**被当作真实结果展示（AGENTS.md 底线 10）。

| 场景 | 怎么跑 | 花钱吗 |
| --- | --- | --- |
| 界面/接线自查 | 后端 `MODEL_PROVIDER=stub` | 不花钱（但检索工具仍会尝试调用，建议同时把 `PKULAW_URL_*` 指向不可达地址） |
| 真实联调 | 后端用真实 Key + 真实法宝地址 | **会花钱**（≈¥0.1–0.3 / 次），需先确认 |

## 五、检查与测试

> 本机没有全局 `pnpm`，用 `corepack pnpm@11.19.0 …`，或直接跑 `./node_modules/.bin/vitest run`。

```bash
corepack pnpm@11.19.0 test        # Vitest（迭代 1-1 时为 210 通过 / 2 失败；2 条失败改动前即存在，指向旧 consult 组件）
corepack pnpm@11.19.0 typecheck   # tsc --noEmit
corepack pnpm@11.19.0 lint        # eslint
corepack pnpm@11.19.0 build       # 生产构建（vinext build）
```

界面截图与离线端到端验收：

```bash
# 截图（页面维度）
node scripts/capture-ui.mjs --base http://localhost:5174 \
  --out ../../../docs/evidence/法小智agent开发文档/阶段3-1/screenshots [--flow]

# 类案研究完整闭环（上传 → 核验 → 人工门 → 矩阵 → 结论 → 门禁 → 导出 Word），零成本
node scripts/e2e-offline-research.mjs --base http://localhost:5174 \
  --out ../../../docs/evidence/法小智agent开发文档/阶段3-2/screenshots
```

> 两个脚本都要在**离线后端**（`MODEL_PROVIDER=stub` + `PKULAW_URL_*` 指向不可达地址）下运行。
> 离线 E2E 之所以能跑出「引用通过」，是因为 stub 模型的引用取自材料原文首句；它**不代表**真实模型链路已验收。

> **加了新后端接口之后，两个后端都必须重启**（真实 `8010` 与离线 `8012`），否则前端代理会 404。
> 实测踩到过：法规查找的「看这份法规全文」在免费版报错，就是因为离线后端还是旧进程（新接口它不知道）
> 加上免费版前端目录没同步——**不是功能坏了**。重启两个后端 + 重新同步临时前端后即正常。
> 同理，免费版（5175）是仓库前端的一份**临时副本**，改动后要重新 rsync（含删除，避免残留已删页面）。

离线后端怎么起（不碰生产配置）：

```bash
cd projects/法小智agent开发文档
DEAD=http://127.0.0.1:9/mcp
ENVV="APP_PORT=8011 MODEL_PROVIDER=stub PKULAW_MCP_TOKEN=offline-test"
for k in $(grep -o '^PKULAW_URL_[A-Z_]*=' .env | tr -d '='); do ENVV="$ENVV $k=$DEAD"; done
env $ENVV uv run python -m app.server
# 前端临时指向它：.env.local 里 BACKEND_BASE_URL=http://127.0.0.1:8011
```

## 六、当前进度与已知问题

- **已完成（3-1）**：代理层、API 层、会话与恢复、后端状态显示；页面不再出现任何假数据。
- **已完成（3-2）**：类案研究完整闭环 —— 候选池勾选 → **确认样本人工门** → 对比矩阵（含来源列）→ 观点分布 → 带引用的结论（引用可回溯）→ 依据区 / 门禁报告 / 上传材料三页签 → 来源冲突人工裁决 → 补充检索 → 导出 Word；未核验的用户材料不可引用。
- **已完成（3-3）**：法律咨询 —— 追问卡（第 N 轮 / 最多 M 轮，轮数由代码计数）→ 四块解答（结论 / 引用 / 不确定与风险 / 建议的下一步）→ 咨询备忘导出；**咨询没有人工门是设计如此**。
- **已完成（3-4）**：合同审查 —— 上传合同（显式 role=contract）→ **立场硬门** → **原文 ↔ 风险联动** → 风险分级与依据核验 → 审查报告导出；依据未过核验的风险只作「提示性风险」。
- **三条链路均已通过真实联调**（真 DeepSeek + 真法宝）：类案研究 11/11、法律咨询 9/9（真模型连问 3 轮）、合同审查 12/12（真实风险 11 条，其中 7 条依据未过核验被降级为提示）。详见 `docs/evidence/法小智agent开发文档/阶段3-*/真实联调结果摘要.md`。
- **已完成（迭代 1-1，2026-09-20）**：合同审查**新界面**（`components/contract/contract-studio.tsx` + `contract-result.tsx` + `steps.ts`）已上线到 `/contract`，四步走：上传合同 → 确认立场 → 审查中 → 结果与导出；上一代 `components/contract-live.tsx` 保留未删（要回退改 `app/contract/page.tsx` 的引用即可）。**接口与业务规则一行未改**。离线端到端 `scripts/e2e-offline-contract.mjs` 已同步改造到新界面，**17 项断言全过**（新增“四步流程初始态”“核验后不误跳立场页”两项）；设计稿与截图见 `docs/evidence/法小智agent开发文档/迭代1-1合同审查/`。**真实模型联调尚未跑（需产品经理同意，约 ¥0.08）**。
- **迭代 1-1 实测修掉的两个真缺陷**（都写进了回归测试）：① 合同材料有“旧版本被取代”时取了 `materials[0]`，恰好是被取代那份 → 点「本人已核验」必然 404；改为只认 `!superseded` 的那份。② 立场提交后后端状态仍写着 `awaiting_stance`，若只看状态会让界面**卡在立场页**看不到后面的结果 → 改为“已选立场就一律不算在等”（`isAwaitingStance`）。
- **契约差异（留到 3-4 处理）**：交接契约说上传 `role` 支持 `contract`，后端只接受 `case` / `statute`；合同材料靠正文**自动识别**兜住，前端不显式传 `contract`。
- **已知缺陷（已在前端规避，建议后端修）**：后端事件队列是**每会话一个**，客户端断开后服务端那条流不会立刻结束，会继续取走事件 → 新开的流只能拿到 `meta`。前端已按"一次运行只订阅一条流"实现（`useRun.resume()`），并加 2.5 秒一次、最多 4 分钟的**有界轮询兜底**。建议后端改成按订阅者分发或检测断连即退出。
- **不做（本阶段范围外）**：登录、多用户隔离、部署上线、产物在线预览、手机真机。

## 六之二、发布说明与已知问题（阶段 3-5）

**可以发布的状态（本机范围内）**

| 项 | 状态 |
| --- | --- |
| 5 条路由 × 6 档宽度（375 / 390 / 768 / 1024 / 1280 / 1440） | **30/30 无横向溢出**（`scripts/e2e-responsive.mjs`） |
| Console 错误 | **0**（同一脚本整轮收集） |
| 无障碍基础 | 可交互元素全部有可访问名称、Tab 可达、焦点样式可见（`scripts/check-a11y.mjs`，5/5 页通过） |
| 类型 / Lint / 构建 | 全过（`pnpm typecheck` / `pnpm lint` / `pnpm build`） |
| 自动化测试 | 前端 **169 通过 / 0 失败**；后端 **402 通过 / 0 失败** |
| 三条链路离线 E2E | 研究 19/19 · 咨询 13/13 · 合同 15/15 |

**已知问题（真实存在，未隐藏）**

1. **只在本机可用**：前端与后端都只监听 `127.0.0.1`；对外使用前必须过阶段 4 的**登录与数据隔离**（底线 7），这是阻断项。
2. **合同审查的"依据通过率"可能偏低**：真实联调里 11 条风险只有 4 条依据通过逐字核验，其余降级为「仅作提示」。这是门禁在拦（宁缺勿编），但用户观感上会觉得"依据不足"——需要在阶段 4 前决定是否优化检索式或补充依据来源。
3. **咨询与合同没有"材料为主"策略**：依据必须来自检索；检索接口不可用时只能如实报错（不像类案研究有本地依据库回退）。
4. **超长合同**：条款数 > 200 或正文 > 6 万字会被截断，界面会显式列出"本次审查的范围限制"，但**未被审查的条款确实没审**。
5. **后端事件流缺陷（已被前端规避）**：事件队列是每会话一个，客户端断开后旧流可能继续取走事件；前端已按"一次运行只订阅一条流 + 有界轮询兜底"实现，建议后端后续改为按订阅者分发。
6. **不做**：手机真机（服务只监听本机）、产物在线预览（Word 需下载后本地打开）、多份合同一次审查。

## 七、SSE 使用约定（改代码前务必看）

一次运行只订阅**一条** `/api/bff/session/{sid}/events`：

| 动作 | 是否新开流 | 用什么 |
| --- | --- | --- |
| 开始研究 / 重试 / 发起合同审查 | 是（新运行） | `useRun.start()` |
| 确认样本 / 确认立场 / 补充检索 | **否** | `useRun.resume()` + `pollUntilSettled()` |
| 换页返回后发现会话仍在跑 | **否** | `pollUntilSettled()` |

原因见上一条缺陷：重开流会被尚未结束的旧流"抢走"事件。
