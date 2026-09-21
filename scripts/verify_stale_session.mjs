/**
 * 回归验证：旧存档 + 服务重启后，界面不得卡在「无会话」状态，上传必须成功。
 *
 * 背景（2-4 验收实测缺陷 3）：浏览器 localStorage 里存着上次的 `sid`+`run`，服务重启后旧会话已释放，
 * `ensureSession()` 走进「可按存档续跑」分支后直接 return，`state.sessionId` 仍为 null，
 * 上传于是发往 `/api/session/null/upload` 得到 404，界面表现为「点了没反应」。
 *
 * 本脚本用真实 Chrome headless + CDP 复现该场景并真的选文件上传：
 *   1) 正常打开页面 → 写入失效的 {sid, run} → 刷新；
 *   2) 断言 state.sessionId 已是新会话、且没有发往 /api/session/null/ 的请求；
 *   3) 真的通过 #file 选一个文件、点 #btn-upload、等待「成功」。
 *
 * 用法：
 *   node scripts/verify_stale_session.mjs
 *   MATERIAL_PATH=/path/to/x.txt node scripts/verify_stale_session.mjs
 *
 * 只走文档解析，**不调模型、不调法宝检索，零费用**。退出码 0 = 通过。
 */
import { spawn } from "node:child_process";
import path from "node:path";
import { access } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const PROJECT_ROOT_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const REPO_ROOT = path.resolve(PROJECT_ROOT_DIR, "..", "..");
const BASE = process.argv.slice(2).find((a) => a.startsWith("http")) ?? "http://127.0.0.1:8010";
const CHROME = process.env.CHROME_BIN ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9800 + (process.pid % 100);
// 公开裁判文书放在仓库之外（不提交），默认沿用 2-4 取证目录
const DEFAULT_MATERIAL = path.join(path.resolve(REPO_ROOT, ".."), "法小智-公开案例材料", "(2022)苏0903民初3159号.txt");
const MATERIAL = process.env.MATERIAL_PATH ?? DEFAULT_MATERIAL;
const STALE_SID = "s_deadbeef0000";
const STALE_RUN = "deadbeef0000";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

await access(MATERIAL).catch(() => {
  throw new Error(`找不到上传用样例文件：${MATERIAL}\n可用 MATERIAL_PATH 环境变量指定一个 .txt/.md/.docx/.pdf`);
});

const chrome = spawn(
  CHROME,
  [
    "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    "--hide-scrollbars", "--no-proxy-server",
    `--user-data-dir=/tmp/fxz-verify-${Date.now()}`,
    `--remote-debugging-port=${PORT}`,
    "--window-size=1440,1200", "about:blank",
  ],
  { stdio: "ignore" },
);

async function waitForDevtools() {
  for (let i = 0; i < 80; i += 1) {
    try { const r = await fetch(`http://127.0.0.1:${PORT}/json/version`); if (r.ok) return; } catch { /* retry */ }
    await sleep(250);
  }
  throw new Error("Chrome 调试端口未就绪");
}

let ws;
async function cleanup() {
  try { ws?.close(); } catch { /* ignore */ }
  try { chrome.kill(); } catch { /* ignore */ }
}

try {
  await waitForDevtools();
  const targets = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json();
  const target = targets.find((t) => t.type === "page");
  if (!target) throw new Error("未找到可用的页面 target");

  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

  let nextId = 1;
  const pending = new Map();
  const badResponses = [];
  ws.onmessage = (m) => {
    const p = JSON.parse(m.data);
    if (p.method === "Network.responseReceived") {
      const { status, url } = p.params.response;
      if (url.includes("/api/") && status >= 400) badResponses.push(`${status} ${url}`);
    }
    if (p.id && pending.has(p.id)) {
      const { resolve, reject } = pending.get(p.id);
      pending.delete(p.id);
      p.error ? reject(new Error(JSON.stringify(p.error))) : resolve(p.result);
    }
  };
  const send = (method, params = {}) => {
    const id = nextId++;
    ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
  };
  async function evaluate(expression) {
    const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description ?? "页面脚本执行失败");
    return r.result.value;
  }
  async function waitFor(expr, { timeout = 25000, label = expr } = {}) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      if (await evaluate(`(() => { try { return Boolean(${expr}); } catch (e) { return false; } })()`)) return;
      await sleep(300);
    }
    throw new Error(`等待超时：${label}`);
  }

  await send("Page.enable");
  await send("Runtime.enable");
  await send("DOM.enable");
  await send("Network.enable");

  // 1) 先正常打开一次（拿到 origin 才能写 localStorage），再塞入旧存档并刷新
  await send("Page.navigate", { url: `${BASE}/` });
  await waitFor("document.querySelector('#statusbar')", { label: "首页加载" });
  await evaluate(`localStorage.setItem("fxz.session", JSON.stringify({ sid: "${STALE_SID}", run: "${STALE_RUN}" }))`);
  await send("Page.reload");
  await waitFor("document.querySelector('#statusbar')", { label: "刷新后页面加载" });
  await sleep(2500);

  const sid = await evaluate("(() => { try { return state.sessionId; } catch (e) { return 'ERR:' + e.message; } })()");
  const sessionOk = typeof sid === "string" && sid.startsWith("s_") && sid !== STALE_SID;
  console.log(`[1] 刷新后 state.sessionId = ${JSON.stringify(sid)} → 自动建立新会话：${sessionOk ? "是 ✅" : "否 ❌"}`);

  const nullCalls = badResponses.filter((x) => x.includes("/api/session/null/"));
  console.log(`[2] 发往 /api/session/null/ 的请求：${nullCalls.length} 条 ${nullCalls.length === 0 ? "✅" : "❌"}`);

  // 2) 真的走一次上传
  const doc = await send("DOM.getDocument", { depth: -1 });
  const fileNode = await send("DOM.querySelector", { nodeId: doc.root.nodeId, selector: "#file" });
  if (!fileNode.nodeId) throw new Error("找不到文件输入框 #file");
  await send("DOM.setFileInputFiles", { nodeId: fileNode.nodeId, files: [MATERIAL] });
  await evaluate("document.querySelector('#btn-upload').click()");
  await waitFor("document.querySelector('#upload-result').textContent.includes('成功')", { label: "上传结果出现「成功」" });
  const uploadText = await evaluate("document.querySelector('#upload-result').textContent");
  const uploadOk = uploadText.includes("成功");
  console.log(`[3] 上传结果：${uploadText.trim()} ${uploadOk ? "✅" : "❌"}`);

  const others = badResponses.filter((x) => !x.includes("/api/session/null/"));
  console.log(`[4] 其他 4xx/5xx 接口：${others.length ? others.join(" | ") : "无 ✅"}（探测旧会话的 404 属预期）`);

  const pass = sessionOk && nullCalls.length === 0 && uploadOk;
  console.log(`\n结论：${pass ? "PASS ✅ 缺陷已修复" : "FAIL ❌"}`);
  await cleanup();
  process.exit(pass ? 0 : 1);
} catch (err) {
  console.error("验证失败：", err.message);
  await cleanup();
  process.exit(1);
}
