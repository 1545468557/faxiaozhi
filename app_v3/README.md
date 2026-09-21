# 法小智 v3 后端（重写版）

> 2026-09-20 产品决定：旧后端的**问答形状**整个不要（四块答案、追问计数、引用核验门禁、单会话 SSE 队列），
> 技术栈仍是 **Python + FastAPI**；**不做逐条核验**，只要能聊。
> 旧 `app/` 暂时保留，等前端切过来、验收通过后再退役。

## 启动

```sh
# 离线（不联网、不花钱，用于开发与回归）
MODEL_PROVIDER=stub FAXIAOZHI_OFFLINE=1 APP_PORT=8011 uv run python -m app_v3.main

# 真实（用 .env 里的 DeepSeek 与法宝配置；会真实计费）
APP_PORT=8011 uv run python -m app_v3.main
```

- 端口默认 **8011**（用 `APP_PORT` 改），与旧后端 8010 并存，互不影响。
- 会话库默认 `data/v3.sqlite3`（用 `V3_DB` 改）。
- `FAXIAOZHI_OFFLINE_RETRIEVAL=1`：离线模式下仍去查法规库（占用法宝额度），模型仍走 stub；用于单独验检索链路。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 探活，返回 `{ok, is_stub}` |
| GET | `/api/bootstrap` | 能力与离线状态。界面据此标注"离线示例" |
| POST | `/api/chat` | 发一句话，**SSE 流式**返回 |
| GET | `/api/chat/{sid}` | 取整条时间线（刷新/切页恢复用） |
| GET | `/api/sessions` | 会话列表（"我的"页用） |
| DELETE | `/api/session/{sid}` | 删除一个会话 |

### POST /api/chat

请求体：

```json
{ "session_id": "可省略，省略则新建", "message": "用户这句话", "use_retrieval": true }
```

响应是 `text/event-stream`，事件序列：

| 事件 | 载荷 | 说明 |
| --- | --- | --- |
| `meta` | `{session_id, is_stub, title}` | 必到，第一帧。`is_stub=true` 时界面必须标注离线示例 |
| `sources` | `{items:[{kind,title,identifier,court,decided_on,uri,origin_text,quote}], notes:[]}` | 检索到的材料，**仅作参考链接，未核验**；可能没有 |
| `note` | `{text}` | 检索没成功之类的说明；可能没有 |
| `delta` | `{text}` | 增量文本，边收边显示 |
| `done` | `{session_id, message_id, chars, sources, elapsed_ms}` | 正常收尾 |
| `error` | `{code, message}` | 失败收尾；message 是给人看的话。**中断不等于"没有相关规定"** |

### 与旧接口的关系

旧接口（`/api/session/{sid}/message`、`/events`、`/consult/answer`、`/state` …）**不再使用**。
新接口只有上面 6 个，前端 BFF 直接转发。

## 代码结构

| 文件 | 职责 |
| --- | --- |
| `main.py` | HTTP 入口、SSE 编码、路由 |
| `chat.py` | 一轮对话的编排：落库 → 检索 → 流式生成 → 落库；失败文案 |
| `model.py` | DeepSeek 流式 + 离线 stub；`offline()` 判定 |
| `prompt.py` | 系统提示词（像同行聊天，不写公文结构）+ 材料渲染 |
| `retrieve.py` | 法宝检索：复用 `app.tools.mcp.McpProvider`，**不做核验** |
| `store.py` | SQLite 会话与消息（重启不丢） |

**复用旧代码的两处（有意为之）**：`app.config` 与 `app.tools.mcp`。
它们是纯管道（配置读取、法宝 12 个端点的调用与字段映射），重写只会把坑再踩一遍。

## 已验证（2026-09-20，离线 stub）

- SSE 流式：`meta → delta×N → done`，`done` 带 chars/elapsed_ms。
- 多轮：同一 `session_id` 连发两轮，时间线按 `seq` 累积。
- **重启持久化**：杀掉进程重启后，取回完整 4 条消息并接着聊到 6 条。
- 检索在离线模式下不外发请求；失败会以 `note` 事件如实说明。
- 服务端日志 0 ERROR。
- **经前端 BFF 打通**（浏览器 → 5174 → BFF → 8011）：`health` / `bootstrap` / `chat(SSE)` 三条都通，
  事件序列 `meta → 30×delta → done`。
- **界面验收 17 项全过**：`frontend/scripts/verify-v3-ui.mjs`
  （空态渲染、流式长出来、生成中有「停止」、收尾无光标、离线示例明说、会话 id 落 localStorage、
  刷新后回答原样还在、桌面/手机均无横向溢出）。
- **行内引用验收 12 项全过**：`frontend/scripts/verify-v3-citations.mjs`
  （用 CDP 拦截对话请求喂固定 SSE，不消耗法宝额度；验证简称《民法典》第七百二十五条 与案号都变可点链接、
  未点名的材料落到「参考材料」、点链接能看到原文与「未逐条核验」）。
- `components/consult/citations.ts` 单测 7 项（含「同一部法多条材料时不链」的保守规则与负对照）。

## 前端怎么接的

| 位置 | 说明 |
| --- | --- |
| `frontend/app/api/bff/v3/**` | 6 个转发路由，指向 8011（旧接口仍指 8010，互不影响） |
| `frontend/lib/server/bff.ts` | 改成按后端分组的工厂：`legacy`(8010) / `v3`(8011) |
| `frontend/lib/api/v3.ts` | v3 客户端：`chat`(SSE) / `timeline` / `sessions` / `deleteSession` / `bootstrap` / `health` |
| `frontend/components/consult/use-ask.ts` | 对话状态：发一句、接 SSE、刷新后把时间线拉回来 |
| `frontend/components/consult/ask-home.tsx` | 首页：空态 + 对话态（回答是"一段话"，不再是四块表格） |
| `frontend/components/consult/citations.ts` | 把正文里的法规名/案号对回材料，做成**行内**链接 |
| `frontend/components/consult/source-sheet.tsx` | 材料详情（**不复用**旧 source-sheet：旧版带核验字段，v3 没有） |

`V3_BACKEND_BASE_URL` 可改 8011 以外的地址；旧 `app/` 退役后把 `BACKEND_BASE_URL` 指到 8011 即可收口。

## 还没做

- pytest 测试（`app_v3` 目前只有手工验证与上面两份界面验收脚本）。
- 真实模型/法宝联调（需先确认计费）。
- **材料上传**：v3 没有上传接口，首页的「上传文件」目前只给一句说明，还没接。
- **导出 Word**：v3 没有导出接口，首页已去掉该按钮；法律咨询页要用需新做接口。
- 三个入口页：`/consult` 目前仍是**旧页面**（旧视觉 + 旧接口），`/cases`、`/statutes` 是占位页。
- 四个一级页共用一个 layout（现在每页各渲染一次左导航，切页会重建 DOM）。
- 旧的 `app/` 与 `tests/` 退役（`tests/consult-live.test.tsx` 有 2 条失败是**改动前就存在**的，
  指向旧组件，未修）。

