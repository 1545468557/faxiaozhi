# 法小智 · 后端 AI 交接说明

本包保留已完成的多页面前端、GSAP 动效，以及已有的真实类案研究后端参考实现。下一阶段主要工作是补齐法律咨询、合同审查的真实后端，并把现有页面的演示处理函数改为真实请求。

本文依据本包源码编写；“建议新增”接口尚不存在，不能当作已实现能力。

## 1. 当前页面与实现边界

| 地址 | 页面 | 当前能力 |
| --- | --- | --- |
| `/` | 首页 | 功能入口、问题输入、教学预览 |
| `/research` | 类案研究 | 固定教学样本、选案、示例备忘录 |
| `/research?mode=real` | 类案研究的真实模式 | 北大法宝案例检索、确认选案、DeepSeek 研究草稿 |
| `/consult` | 法律咨询 | 接收问题，但回答来自前端固定结构模板 |
| `/contract` | 合同审查 | 只分析预置教学合同，风险和改写均为固定数据 |
| `/about` | 关于与能力说明 | 说明数据来源、当前能力和使用边界 |

共有五个页面；`mode=real` 是类案研究页的模式参数。导航由 `next/link`、`useRouter()` 和 `app/*/page.tsx` 实现，保留独立网址、刷新访问、前进和后退。

实际运行框架是 **Vinext + Vite + Cloudflare Workers 适配**，React 19，使用 Next App Router 风格 API；不要直接按普通 `next dev` 项目重写配置。Node.js 需 ≥ 22.13，包管理器使用 pnpm；启动命令和本包默认端口见根目录 README。

## 2. 先阅读这些文件

| 文件 | 作用 |
| --- | --- |
| `components/legal-workspace.tsx` | 三个工作台页面、演示逻辑、咨询和合同接入位置 |
| `components/real-research.tsx` | 已有真实接口调用、选案、引用追溯、错误和计费提示 |
| `components/workspace-session.tsx` | 跨页面内存状态、研究请求锁、首页携带问题进入工作台 |
| `lib/legal-types.ts` | 真实研究请求结果和报告类型 |
| `app/api/legal/*/route.ts` | 已存在的四个服务端接口 |
| `lib/server/research.ts` | MCP 工具选择、案例归一化、证据凭证、报告引用校验 |
| `lib/server/mcp.ts` / `deepseek.ts` | 北大法宝 MCP / DeepSeek 适配 |
| `lib/server/legal-config.ts` / `request-dedup.ts` | 服务端配置、请求校验、限流、重复请求保护 |
| `components/use-interface-motion.ts` / `motion-main.tsx` | GSAP 入场和结果展示动画 |
| `app/home.css` / `workspace.css` / `interactions.css` | 主要视觉与交互样式 |

## 3. 已存在的 API：请保持前端契约兼容

前端通过同源 `fetch('/api/legal/...')` 请求，不直接连接北大法宝或 DeepSeek。返回为普通 JSON，没有 SSE 流式问答协议。真实模式请求入口为 `components/real-research.tsx` 的 `api<T>()`。

- GET 不带请求正文；POST 使用 `Content-Type: application/json`。
- 前端 POST 会生成 `Idempotency-Key: crypto.randomUUID()`；`search`、`report` 服务端强制要求有效 UUID。
- `fetch` 使用 `cache: 'no-store'`，支持 `AbortSignal`；服务端响应也禁止缓存。
- 相同请求标识与相同输入可复用结果；相同标识配不同输入返回 409。当前去重仅在服务进程内短期生效，并非分布式幂等系统。
- 所有接口当前仅允许 localhost / 127.0.0.1 / IPv6 本机地址并校验来源。若改为公开服务，需要单独设计身份验证、授权、配额、幂等存储等，不能只删除本机限制。

### GET `/api/legal/status`

仅检查配置是否填写及地址是否符合允许范围，不验证余额、权限或实际连通性。

```ts
// 无请求正文；成功响应：
{
  deepseekConfigured: boolean;
  pkulawConfigured: boolean;
  endpointValid: boolean;
  ready: boolean;
  connectionTested: false;
  localOnly: true;
}
```

### POST `/api/legal/search`

严格校验请求，不接受额外字段；`query` 去除两端空白后为 1–4000 字符。

```ts
// 请求
{
  query: string;
  purpose: "课程学习" | "教学备课" | "学术研究" | "实务参考";
}
// 成功响应 SearchResponse
{
  cases: RetrievedCase[];
  evidenceToken: string;
  retrievedAt: string; // ISO 时间
  tool: string;       // 实际选用的 MCP 工具名
  sourceStatus: "retrieved-unverified";
}
// RetrievedCase
{
  id: string;         // 本批次内的 C1、C2…，不是跨批次数据库主键
  title: string;
  court: string;
  caseNumber: string;
  date: string;
  url: string | null;
  material: string;   // 返回记录的 JSON 文本，不保证是裁判全文
  sourceStatus: "retrieved-unverified";
}
```

法院、案号、日期可能为空，来源链接可能为 null。前端应显示缺失状态。返回零条案例就是零条，不以教学数据补齐。当前实际流程为 MCP 发现工具 → DeepSeek 规划参数 → 参数校验 → 调用检索工具 → 归一化材料。

### POST `/api/legal/report`

严格校验请求，不接受额外字段。只能分析本次检索中由用户确认的样本。

```ts
// 请求
{
  evidenceToken: string; // 原样传回 search 返回值，1–500000 字符
  selectedIds: string[]; // 1–10 个、不重复、匹配 /^C\d+$/、属于该批次
}
// 成功响应
{
  report: {
    title: string;
    comparisons: {
      caseId: string;
      fact: string;
      viewpoint: string;
      citations: { caseId: string; quote: string }[];
    }[];
    findings: {
      text: string;
      citations: { caseId: string; quote: string }[];
    }[];
    boundaries: string[];
    memo: string;
  };
  sourceStatus: "retrieved-unverified";
  citationCheck: "exact-excerpt-match-only";
}
```

`evidenceToken` 是包含查询、用途、时间和本批材料的签名凭证，当前有效期 30 分钟。它经过 HMAC 签名但没有加密，不应写到网址或日志。服务端校验签名、期限和样本归属，不接受前端替换材料；当前签名密钥派生自服务端 DeepSeek 密钥，后续可改为独立服务端签名密钥。

报告逐案项必须恰好覆盖确认样本；引用摘录须为对应 `material` 中连续原文（8–2000 字符）。这只是摘录对应检查，不代表裁判原文、法条效力或法律结论已独立核验。`memo` 是服务端从通过校验的报告结构生成的可编辑文本。

### POST `/api/legal/tools`

这是工具发现接口，前端当前没有直接调用。请求没有必需正文，处理器不读取正文；只连接 MCP 并列出工具，不执行案例检索。仍要求服务端配置完整。

```ts
// 成功响应
{
  tools: { name: string; description?: string; inputSchema: Record<string, unknown> }[];
  connectionTested: true;
}
```

### 统一失败格式

```json
{ "error": { "code": "INVALID_INPUT", "message": "面向用户的中文错误信息" } }
```

错误使用非 2xx HTTP 状态。常见码包括 `INVALID_INPUT`、`INVALID_BODY`、`BODY_TOO_LARGE`、`NOT_CONFIGURED`、`LOCAL_ONLY`、`ORIGIN_REJECTED`、`REQUEST_ID_REQUIRED`、`REQUEST_ID_CONFLICT`、`RATE_LIMIT`、`EVIDENCE_INVALID`、`EVIDENCE_EXPIRED`、`SELECTION_INVALID`、`REPORT_CITATION_INVALID` 和 `INTERNAL_ERROR`；具体以源码为准。前端目前展示 `error.message`。不要在失败时返回 200 或伪造成功结果；不要把上游完整响应、密钥或堆栈透传给浏览器。

## 4. 法律咨询：需要替换的位置

在 `components/legal-workspace.tsx` 中，`ask()` 只是把问题嵌入固定回答；`messages` 当前类型为 `{q: string; a: string}[]`，`question` 是输入。发送按钮、回答来源标签和“结构演示”文案也在该文件中。

建议新增 **POST `/api/legal/consult`（尚未实现）**：请求可包含 `question`、`purpose` 和经限制的会话上下文；响应应包含回答、事实缺口、引用来源及核验状态。先定义共享 TypeScript 类型和服务端 schema，再替换 `ask()` 并适配结果渲染。若提供多轮能力，不要只把最后一个问题作为完整上下文。

保留现有问题输入、建议问题、问答布局，以及“围绕此问题开展类案研究”跳转。增加真实的 pending / error 状态和重复提交保护；只有在接口成功后才能更新为真实分析文案。没有来源支持时明确标注材料不足，不生成虚构法条和案号。

## 5. 合同审查：需要替换的位置

同一文件中的 `exampleContract`、`risks` 是固定教学内容。`contract`、`stance`、`review`、`activeRisk` 控制输入和结果；“开始示例审查”按钮的内联 `onClick` 只允许正文与示例完全一致。当前没有文件上传解析，也没有真实审查函数。

建议新增 **POST `/api/legal/contract`（尚未实现）**：请求可包含 `contractText`、`stance`、`purpose`；响应应包含风险项、风险级别、原条款精确定位、解释、建议改写、引用和核验状态。第一阶段可继续接收纯文本，再按需求增加 PDF / DOCX 上传及解析。

用服务端响应替换固定 `risks` 和“3 项 / 2 高 1 中”等汇总文案。当前立场选项是承租人、出租人、中立教学分析，示例结果只展示承租人结构；真实接入应按所选立场分析。保留“应用到正文”，但要按稳定条款 ID 或原文范围定位并确认匹配；合同编辑后应使旧结果失效或明确标记待重新审查，防止建议应用到错误版本。

## 6. 状态、动效与接入约束

- `app/layout.tsx` 中的 `WorkspaceSessionProvider` 为每个浏览器文档持有 Map。换页保留输入、样本、问答和草稿；刷新、关闭页面后清空。不使用 localStorage，不把问题正文写入 URL。
- `demo:*` 存演示状态，`real:*` 存真实研究状态。新增咨询和合同真实状态时建议用独立键，不污染演示数据。
- `useResearchOperation()` 持有跨页面请求锁和版本号。真实研究请求换页后继续归属于当前会话，回到页面可查看结果；不要因组件卸载丢失已发起的付费调用结果。
- 同问题复用当前结果，单纯修改问题不会清空旧批次，展示仍使用批次的 `activeQuery` / `activePurpose`。用户确认重新检索后，现有 `reset()` 会先清除旧批次和报告，再发起请求；失败不会恢复旧结果。后续可改进为新请求成功后替换，但这不是当前行为。
- 已有逻辑会提示第三方处理材料及可能计费，不自动重试失败的付费操作。新增模块也应提供清楚的提交、等待、失败和重试体验。
- 保留现有浅色墨青视觉、手机适配、独立页面和 GSAP。动画集中在 motion 组件及 `interactions.css`，已有 reduced-motion 处理；不要用后端接入为由重建整套 UI。
- 真实结果与教学示例须始终可区分。保持来源追溯、人工核验提示；引用匹配通过不能标成“法律依据已验证”。

## 7. 配置与最小验收

实际 `.env.local` 未包含在交接包中；`.env.example` 的密钥字段为空。可用配置名为 `DEEPSEEK_API_KEY`、`PKULAW_TOKEN`、`DEEPSEEK_MODEL`、`PKULAW_CASE_MCP_URL`，另有可选 `PKULAW_CASE_SEARCH_TOOL`。服务端从 Cloudflare bindings / 环境变量读取，切勿改成 `NEXT_PUBLIC_` 或 `VITE_` 密钥。既有后端是参考实现，历史联调记录不证明新环境已连通。

接入后至少检查：五页可直接打开和前进后退；咨询支持非预置问题；合同支持非预置正文且切换立场有效；等待时不能重复提交；失败时保留输入并显示真实错误；不存在来源时不编造；换页返回保留结果；刷新符合当前清空约定；手机无横向溢出；TypeScript 与构建通过。付费联调前配置自己的凭证并确认调用范围。

## 8. 可直接发给另一个 AI 的指令

> 请在这个项目里继续开发法小智后端。先阅读 README、后端AI交接说明.md、lib/legal-types.ts 和现有 app/api/legal 实现。保留已完成的五页面、导航、浅色墨青设计、响应式布局和 GSAP 动效。现有类案研究真实链路可作为参考并保持兼容；法律咨询与合同审查目前只是前端演示，请实现缺失后端，定义并校验真实请求/响应，然后替换 legal-workspace.tsx 中咨询 ask()、合同审查按钮处理及固定结果数据，适配前端请求与渲染。建议接口 /api/legal/consult 和 /api/legal/contract 尚未存在，请自行实现后再接入。密钥只放服务端；不要编造法条、案号或来源，不要把失败包装成示例成功。保留来源追溯、明确错误、重复提交保护和当前跨页内存状态。按项目现有 Vinext/Vite/Cloudflare 运行方式验证构建与交互，不要重写设计或直接套用普通 Next.js 启动配置。
