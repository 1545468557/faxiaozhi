/**
 * 用本机已安装的 Chrome（headless + CDP）跑一遍 2-1 验收界面并截图。
 *
 * 用法：
 *   node scripts/capture_evidence.mjs                        # 恢复指定会话（不产生任何真实调用）
 *   SESSION_ID=s_xxx RUN_ID=yyy node scripts/capture_evidence.mjs
 *   node scripts/capture_evidence.mjs --flow                 # 跑一遍新流程（会产生真实检索与模型调用）
 *   node scripts/capture_evidence.mjs --materials            # 2-4 材料为主界面（离线夹具，不产生真实调用）
 *
 * 输出：docs/evidence/<PROJECT_DIR>/<STAGE>/*.png（脚本按自身位置反推仓库根，不依赖当前目录）
 */
import { spawn } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const PROJECT_ROOT_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const REPO_ROOT = path.resolve(PROJECT_ROOT_DIR, "..", "..");

const BASE = process.argv.slice(2).find((arg) => arg.startsWith("http")) ?? "http://127.0.0.1:8010";
const PROJECT = process.env.PROJECT_DIR ?? "法小智agent开发文档";
const STAGE = process.env.STAGE ?? "阶段2-1";
const OUT = process.env.OUT_DIR ?? path.join(REPO_ROOT, "docs", "evidence", PROJECT, STAGE);
const PORT = 9300 + (process.pid % 600);   // 每次用不同端口，避免连到上次没退干净的实例
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const RUN_FLOW = process.argv.includes("--flow");
const MATERIALS = process.argv.includes("--materials");
const DEGRADE = process.env.DEGRADE === "1";
const SESSION_ID = process.env.SESSION_ID ?? "";
const RUN_ID = process.env.RUN_ID ?? "";
const TOPIC = process.env.TOPIC ?? "买受人以设备质量存在瑕疵为由主张减少价款，法院一般如何认定？";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
await mkdir(OUT, { recursive: true });

const chrome = spawn(
  CHROME,
  [
    "--headless=new",
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    "--hide-scrollbars",
    "--no-proxy-server",
    `--user-data-dir=/tmp/fxz-chrome-profile-${Date.now()}`,
    `--remote-debugging-port=${PORT}`,
    "--window-size=1440,1200",
    "about:blank",
  ],
  { stdio: "ignore" },
);

async function waitForDevtools() {
  for (let i = 0; i < 80; i += 1) {
    try {
      const res = await fetch(`http://127.0.0.1:${PORT}/json/version`);
      if (res.ok) return;
    } catch {
      /* 还没起来 */
    }
    await sleep(250);
  }
  throw new Error("Chrome 调试端口未就绪");
}

await waitForDevtools();
const targets = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json();
const target = targets.find((t) => t.type === "page");
if (!target) throw new Error("未找到可用的页面 target");

const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  ws.onopen = resolve;
  ws.onerror = reject;
});

let nextId = 1;
const pending = new Map();
ws.onmessage = (message) => {
  const payload = JSON.parse(message.data);
  if (payload.id && pending.has(payload.id)) {
    const { resolve, reject } = pending.get(payload.id);
    pending.delete(payload.id);
    payload.error ? reject(new Error(JSON.stringify(payload.error))) : resolve(payload.result);
  }
};

function send(method, params = {}) {
  const id = nextId++;
  ws.send(JSON.stringify({ id, method, params }));
  return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
}

async function evaluate(expression) {
  const result = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.exception?.description ?? "页面脚本执行失败");
  }
  return result.result.value;
}

async function waitFor(expression, { timeout = 20000, label = expression } = {}) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const ok = await evaluate(`(() => { try { return Boolean(${expression}); } catch (e) { return false; } })()`);
    if (ok) return;
    await sleep(300);
  }
  throw new Error(`等待超时：${label}`);
}

process.on("uncaughtException", async (err) => {
  console.error("截图脚本异常：", err.message);
  await finish();
  process.exit(1);
});

async function shot(name) {
  const { data } = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
  const file = path.join(OUT, name);
  await writeFile(file, Buffer.from(data, "base64"));
  console.log(`saved ${file}`);
}

async function scrollTo(selector) {
  await evaluate(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); if (el) el.scrollIntoView({ block: "start" }); })()`);
  await sleep(400);
}

await send("Page.enable");
await send("Runtime.enable");
await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1200, deviceScaleFactor: 1, mobile: false });

let loaded = false;
for (let attempt = 1; attempt <= 3 && !loaded; attempt += 1) {
  await send("Page.navigate", { url: `${BASE}/` });
  await sleep(1500);
  loaded = await evaluate("(() => { try { return Boolean(document.querySelector('#statusbar')); } catch (e) { return false; } })()");
}
if (!loaded) throw new Error(`首页加载失败，当前 URL=${await evaluate("location.href")}`);
await sleep(500);

if (MATERIALS) {
  // 2-4：材料为主路径的界面证据（离线夹具 + stub 模型，不产生任何真实调用）。
  // 其中一篇材料故意使用夹具里的案号，用来制造「同案号不同原文」的来源冲突。
  await evaluate('localStorage.removeItem("fxz.session")');
  await send("Page.reload");
  await sleep(1500);

  const materialA = [
    "江苏省南京市中级人民法院",
    "民事判决书",
    "（2023）苏01民终8888号",
    "本院认为，设备交付后经检验存在质量瑕疵的，买受人可以主张减少价款，该主张于法有据。",
    "裁判结果：驳回上诉，维持原判。",
  ].join("\n");
  const materialB = [
    "浙江省杭州市中级人民法院",
    "民事判决书",
    "（2022）浙01民终6666号",
    "本院认为，买受人已经验收使用的，主张减少价款应当扣减相应的使用费用，该抗辩成立。",
    "裁判结果：改判部分支持上诉请求。",
  ].join("\n");
  // 与夹具同案号、但正文不同 → 补充检索后会构成「来源冲突」
  const materialC = [
    "示例买卖合同纠纷上诉案",
    "（2023）示例民终1001号",
    "本院认为，出卖人交付的货物存在质量瑕疵的，买受人可以主张减少价款，但应扣除已使用部分的价值。",
    "裁判结果：改判支持部分请求。",
  ].join("\n");

  await evaluate(`(() => { document.querySelector('[data-tab="upload"]').click(); })()`);
  await sleep(300);
  await evaluate(`(() => {
    const dt = new DataTransfer();
    dt.items.add(new File([${JSON.stringify(materialA)}], "公开案例-A-苏01民终8888号.txt", { type: "text/plain" }));
    dt.items.add(new File([${JSON.stringify(materialB)}], "公开案例-B-浙01民终6666号.txt", { type: "text/plain" }));
    dt.items.add(new File([${JSON.stringify(materialC)}], "公开案例-C-同案号冲突样例.txt", { type: "text/plain" }));
    const input = document.querySelector("#file");
    input.files = dt.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
  })()`);
  await evaluate('document.querySelector("#btn-upload").click()');
  await waitFor("document.querySelectorAll('#material-list .srcline').length >= 3", {
    timeout: 30000,
    label: "材料清单出现 3 条",
  });
  await sleep(500);
  await scrollTo("aside");
  await shot("01-批量上传与材料清单.png");

  // 逐个核验
  await evaluate(`(() => {
    document.querySelectorAll("#material-list button").forEach((b) => { if (b.innerText.includes("本人已核验")) b.click(); });
  })()`);
  await waitFor("document.querySelectorAll('#material-list .pill.ok').length >= 3", {
    timeout: 30000,
    label: "三条材料都已核验",
  });
  await scrollTo("aside");
  await shot("02-材料清单（已核验与版本）.png");

  // 议题专用于离线验收：与真实运行使用过的检索式区分开，避免命中本地依据库缓存（否则补充检索不走夹具，
  // 也就制造不出「与夹具同案号」的冲突样例）。
  const MATERIAL_TOPIC = "【2-4 验收】" + TOPIC;

  // 发起研究：材料充足 → 默认不检索
  await evaluate(`(() => { const el = document.querySelector("#topic"); el.value = ${JSON.stringify(MATERIAL_TOPIC)}; el.dispatchEvent(new Event("input", { bubbles: true })); })()`);
  await evaluate('document.querySelector("#btn-start").click()');
  await waitFor("document.querySelectorAll('#candidates input[type=checkbox]').length >= 3", {
    timeout: 180000,
    label: "候选池返回（材料）",
  });
  await scrollTo("#candidates");
  await shot("03-纯材料候选池（用户材料在前）.png");

  // 补充检索 → 补充来源单独标注；夹具同案号 → 冲突
  await waitFor("document.querySelector('#btn-supplement') && !document.querySelector('#btn-supplement').disabled", {
    timeout: 60000,
    label: "补充检索按钮可用",
  });
  await evaluate('document.querySelector("#btn-supplement").click()');
  await waitFor("document.querySelectorAll('#candidates .card').length > 3", {
    timeout: 60000,
    label: "补充来源出现",
  });
  await scrollTo("#candidates");
  await shot("04-补充检索（补充来源标注）.png");
  await waitFor("document.querySelector('#conflict-panel') && !document.querySelector('#conflict-panel').hidden", {
    timeout: 60000,
    label: "冲突面板出现",
  });
  await scrollTo("#conflict-panel");
  await sleep(400);
  await shot("05-来源冲突（默认拦住）.png");

  // 只确认三篇用户材料（把补充来源的勾都去掉）
  await evaluate(`(() => {
    document.querySelectorAll("#candidates input[type=checkbox]").forEach((b) => {
      const card = b.closest(".card").innerText;
      b.checked = card.includes("公开案例");
      b.dispatchEvent(new Event("change", { bubbles: true }));
    });
    document.querySelector("#btn-confirm").click();
  })()`);
  await waitFor("document.querySelectorAll('#matrix table tbody tr').length > 0", {
    timeout: 420000,
    label: "矩阵生成",
  });
  await waitFor("(document.querySelector('#log')?.innerText ?? '').includes('完成')", {
    timeout: 420000,
    label: "全链路完成（done 事件）",
  });
  await sleep(800);
  await scrollTo("#matrix");
  await shot("06-矩阵（含来源列）.png");
  await evaluate(`(() => { const b = document.querySelector('[data-tab="gate"]'); if (b) b.click(); })()`);
  await scrollTo("aside");
  await shot("07-门禁报告（R7 拦截）.png");

  // 导出被拒 → 人工裁决 → 允许导出
  await evaluate("window.scrollTo(0, document.body.scrollHeight)");
  await evaluate('document.querySelector("#btn-export").click()');
  await sleep(1500);
  await scrollTo("#export-result");
  await shot("08-导出被拒（来源冲突）.png");

  await scrollTo("#conflict-panel");
  await evaluate(`(() => {
    const btn = document.querySelector("#conflict-panel button");
    if (btn) btn.click();
  })()`);
  await waitFor("(document.querySelector('#conflict-panel')?.innerText ?? '').includes('已由人工确认')", {
    timeout: 30000,
    label: "裁决生效",
  });
  await shot("09-人工裁决（以我上传的材料为准）.png");
  await evaluate("window.scrollTo(0, document.body.scrollHeight)");
  await evaluate('document.querySelector("#btn-export").click()');
  await sleep(1800);
  await scrollTo("#export-result");
  await shot("10-裁决后导出成功.png");

  await send("Emulation.setDeviceMetricsOverride", { width: 375, height: 812, deviceScaleFactor: 2, mobile: true });
  await evaluate("window.scrollTo(0, 0)");
  await sleep(700);
  await evaluate(`(() => { document.querySelector('[data-tab="upload"]').click(); })()`);
  await sleep(400);
  await shot("11-移动端375px（材料清单）.png");

  const materialState = await evaluate(`
    (() => ({
      status: document.querySelector("#statusbar")?.innerText?.replace(/\\n/g, "｜") ?? "",
      candidates: document.querySelectorAll("#candidates .card").length,
      materialRows: document.querySelectorAll("#material-list .srcline").length,
      conflicts: document.querySelector("#conflict-panel")?.innerText?.slice(0, 200) ?? "",
      supplement: document.querySelector("#supplement-bar")?.innerText ?? "",
      export: document.querySelector("#export-result")?.innerText?.slice(0, 160) ?? "",
    }))()
  `);
  console.log("final:", JSON.stringify(materialState, null, 1));
  await finish();
  process.exit(0);
} else if (RUN_FLOW) {
  // 跑一遍新流程（会产生真实调用）；先清掉上一次残留的会话记录
  await evaluate('localStorage.removeItem("fxz.session")');
  await send("Page.reload");
  await sleep(1500);
  await evaluate(`(() => { const el = document.querySelector("#topic"); el.value = ${JSON.stringify(TOPIC)}; el.dispatchEvent(new Event("input", { bubbles: true })); })()`);
  await shot("01-真实模式与议题输入.png");
  await evaluate(`document.querySelector("#btn-start").click()`);
  await waitFor("document.querySelectorAll('#candidates input[type=checkbox]').length > 0", {
    timeout: 180000,
    label: "候选池返回",
  });
  await scrollTo("#candidates");
  await shot("02-真实候选案例池.png");
  // 必须等「人工确认点」真正到达（按钮可用）再操作，否则点击无效
  await waitFor("document.querySelector('#btn-confirm') && !document.querySelector('#btn-confirm').disabled", {
    timeout: 240000,
    label: "到达样本确认点",
  });
  // 只选前 3 篇（把其它勾掉），控制耗时与花费
  await evaluate(`(() => {
    document.querySelectorAll("#candidates input[type=checkbox]").forEach((b, i) => {
      b.checked = i < 3;
      b.dispatchEvent(new Event("change", { bubbles: true }));   // 走真实路径，界面会记住勾选
    });
  })()`);
  await sleep(300);
  await shot("02b-确认样本（选前 3 篇）.png");
  await evaluate(`document.querySelector("#btn-confirm").click()`);
  await waitFor("document.querySelectorAll('#matrix table tbody tr').length > 0", {
    timeout: 420000,
    label: "矩阵生成",
  });
  await waitFor("(document.querySelector('#log')?.innerText ?? '').includes('完成')", {
    timeout: 420000,
    label: "全链路完成（done 事件）",
  });
  await sleep(1000);
  await scrollTo("#matrix");
  await shot("03-矩阵与综合结论.png");
  await scrollTo("#conclusions");
  await shot("04-综合结论与引用.png");
} else if (DEGRADE) {
  // 降级/失败界面：恢复一个发生接口失败的会话来截图（不产生调用）
  if (!SESSION_ID) throw new Error("请通过 SESSION_ID 指定发生降级的会话");
  await evaluate(
    `localStorage.setItem("fxz.session", JSON.stringify({ sid: ${JSON.stringify(SESSION_ID)}, run: ${JSON.stringify(RUN_ID)}}))`,
  );
  await send("Page.reload");
  await sleep(2000);
  await waitFor("document.querySelector('#degrade-panel') && !document.querySelector('#degrade-panel').hidden", {
    timeout: 30000,
    label: "降级面板出现",
  });
  await scrollTo("#degrade-panel");
  await sleep(500);
  await shot("01-降级与失败面板.png");
  const degradeState = await evaluate(`
    (() => ({
      status: document.querySelector("#statusbar")?.innerText?.replace(/\\n/g, "｜") ?? "",
      panel: document.querySelector("#degrade-panel")?.innerText?.slice(0, 300) ?? "",
      retry: Boolean(document.querySelector("#btn-retry")),
    }))()
  `);
  console.log("retry_button_visible:", degradeState.retry);
  console.log("final:", JSON.stringify(degradeState, null, 1));
  await finish();
  process.exit(0);
} else {
  if (!SESSION_ID) throw new Error("请通过 SESSION_ID 指定要恢复的会话，或加 --flow 跑新流程");
  await evaluate(
    `localStorage.setItem("fxz.session", JSON.stringify({ sid: ${JSON.stringify(SESSION_ID)}, run: ${JSON.stringify(RUN_ID)}}))`,
  );
  await send("Page.reload");
  await sleep(1800);
  await waitFor("document.querySelectorAll('#candidates .card').length > 0", { timeout: 30000, label: "恢复会话结果" });
  await sleep(600);
  await shot("01-真实模式与恢复结果.png");
  await scrollTo("#candidates");
  await shot("02-真实候选案例池.png");
  await scrollTo("#matrix");
  await shot("03-矩阵与综合结论.png");
  await scrollTo("#conclusions");
  await shot("04-综合结论与引用.png");
}

// 门禁报告
await evaluate(`(() => { const b = document.querySelector('[data-tab="gate"]'); if (b) b.click(); })()`);
await scrollTo("aside");
await shot("05-门禁报告与依据区.png");

// 导出
await evaluate("window.scrollTo(0, document.body.scrollHeight)");
await sleep(300);
await evaluate(`document.querySelector("#btn-export")?.click()`);
await sleep(1800);
await evaluate(`(() => { const el = document.querySelector("#export-result"); if (el) el.scrollIntoView({ block: "center" }); })()`);
await sleep(400);
await shot("06-导出结果.png");
const exportText = await evaluate(`document.querySelector("#export-result")?.innerText ?? ""`);

// 移动端宽度
await send("Emulation.setDeviceMetricsOverride", { width: 375, height: 812, deviceScaleFactor: 2, mobile: true });
await evaluate("window.scrollTo(0, 0)");
await sleep(700);
await shot("07-移动端375px.png");

const finalState = await evaluate(`
  (() => ({
    status: document.querySelector("#statusbar")?.innerText?.replace(/\\n/g, "｜") ?? "",
    candidates: document.querySelectorAll("#candidates .card").length,
    matrixRows: document.querySelectorAll("#matrix table tbody tr").length,
    conclusions: document.querySelectorAll("#conclusions .card").length,
    gate: document.querySelector("#gate-count")?.innerText ?? "",
    export: document.querySelector("#export-result")?.innerText?.slice(0, 120) ?? "",
  }))()
`);
console.log("final:", JSON.stringify(finalState, null, 1));
console.log("export_text:", exportText.slice(0, 160));

await finish();

async function finish() {
  try { ws.close(); } catch { /* ignore */ }
  try { chrome.kill("SIGKILL"); } catch { /* ignore */ }
}
