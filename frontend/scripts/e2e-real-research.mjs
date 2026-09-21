/**
 * 类案研究**真实链路**联调（会花钱：≈¥0.10–0.25 / 次）
 *
 * 与 `e2e-offline-research.mjs` 的区别：**不预置材料**，走真实检索：
 *   议题 → 真法宝检索 → 候选池 → 确认样本（人工门）→ 逐案提炼 → 对比矩阵
 *   → 带引用的结论 → 门禁（真实逐字核验）→ 导出 Word → 下载校验 → 费用统计
 *
 * 运行前必须确认：
 *   1. 后端是**真实模式**（`MODEL_PROVIDER=deepseek` 且有 Key），脚本会先自检，是 stub 就直接退出；
 *   2. 产品经理已同意这次花费。
 *
 * 用法：
 *   node scripts/e2e-real-research.mjs --base http://localhost:5174 \
 *     --out ../../../docs/evidence/法小智agent开发文档/阶段3-2-real/screenshots \
 *     --runs ../../runs --samples 3
 */

import { spawn } from "node:child_process";
import { mkdir, rm, readdir, stat, readFile, mkdtemp } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import os from "node:os";

const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const arg = (name, fallback) => {
  const index = process.argv.indexOf(`--${name}`);
  return index === -1 ? fallback : process.argv[index + 1];
};
const BASE = arg("base", "http://localhost:5174");
const OUT = path.resolve(arg("out", "./real-shots"));
const RUNS = path.resolve(arg("runs", "./runs"));
const SAMPLES = Number(arg("samples", "3"));
const PORT = Number(arg("port", "9377"));
const DOWNLOADS = "/tmp/fzx-real-downloads";
const TOPIC = arg("topic", "房屋租赁合同中，承租人提前退租时押金能否全部不予返还？");
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

// DeepSeek deepseek-chat 计价（元 / 百万 tokens），与 smoke_real.py 一致
const PRICE_IN_PER_M = 2.0;
const PRICE_OUT_PER_M = 8.0;
const cost = usage =>
  (Number(usage?.in ?? 0) / 1e6) * PRICE_IN_PER_M + (Number(usage?.out ?? 0) / 1e6) * PRICE_OUT_PER_M;

async function connect() {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    try {
      const list = await fetch(`http://127.0.0.1:${PORT}/json/list`).then(r => r.json());
      const page = list.find(item => item.type === "page");
      if (page?.webSocketDebuggerUrl) return page.webSocketDebuggerUrl;
    } catch {
      /* not ready */
    }
    await sleep(250);
  }
  throw new Error("连不上 Chrome 调试端口");
}

function createClient(url) {
  const socket = new WebSocket(url);
  const pending = new Map();
  let id = 0;
  const ready = new Promise((resolve, reject) => {
    socket.addEventListener("open", () => resolve());
    socket.addEventListener("error", reject);
  });
  socket.addEventListener("message", event => {
    const message = JSON.parse(event.data);
    const entry = pending.get(message.id);
    if (!entry) return;
    pending.delete(message.id);
    if (message.error) entry.reject(new Error(JSON.stringify(message.error)));
    else entry.resolve(message.result);
  });
  const send = (method, params = {}) =>
    new Promise((resolve, reject) => {
      id += 1;
      pending.set(id, { resolve, reject });
      socket.send(JSON.stringify({ id, method, params }));
    });
  return { ready, send, close: () => socket.close() };
}

const main = async () => {
  // 先自检后端模式：离线模式不许跑这个脚本（会产生"假成功"）
  const bootstrap = await fetch(`${BASE}/api/bff/bootstrap`).then(r => r.json());
  if (bootstrap?.model?.is_stub) {
    console.error("✗ 后端当前是离线（stub）模式，本脚本只用于真实链路。已退出，未产生费用。");
    process.exitCode = 2;
    return;
  }
  console.log(`后端：${bootstrap.model.model_id}（真实模式）· 检索接口已配置=${bootstrap.mcp.configured}`);

  await mkdir(OUT, { recursive: true });
  await rm(DOWNLOADS, { recursive: true, force: true });
  await mkdir(DOWNLOADS, { recursive: true });
  // 临时用户目录放系统临时目录（避免残留在证据目录里、也避免与 git add 抢文件）
  const profile = await mkdtemp(path.join(os.tmpdir(), "faxiaozhi-chrome-"));

  const chrome = spawn(
    CHROME,
    [
      "--headless=new",
      "--disable-gpu",
      "--hide-scrollbars",
      "--no-first-run",
      "--no-default-browser-check",
      `--remote-debugging-port=${PORT}`,
      `--user-data-dir=${profile}`,
      "--window-size=1440,1500",
      "about:blank",
    ],
    { stdio: "ignore" },
  );

  const checks = [];
  const check = (name, ok, detail = "") => {
    checks.push({ name, ok, detail });
    console.log(`${ok ? "✓" : "✗"} ${name}${detail ? ` — ${detail}` : ""}`);
  };

  try {
    const client = createClient(await connect());
    await client.ready;
    await client.send("Page.enable");
    await client.send("Runtime.enable");
    await client.send("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: DOWNLOADS });

    const evaluate = async expression => {
      const result = await client.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
      if (result.exceptionDetails) throw new Error(result.exceptionDetails.text ?? "页面脚本报错");
      return result.result?.value;
    };
    const waitFor = async (expression, timeoutMs, label) => {
      const deadline = Date.now() + timeoutMs;
      for (;;) {
        if (await evaluate(expression)) return true;
        if (Date.now() > deadline) throw new Error(`等待超时：${label}`);
        await sleep(800);
      }
    };
    const shoot = async name => {
      const shot = await client.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
      const { writeFile } = await import("node:fs/promises");
      await writeFile(path.join(OUT, `${name}.png`), Buffer.from(shot.data, "base64"));
      console.log(`  📷 ${name}.png`);
    };

    // ---------- 1. 发起真实研究 ----------
    await client.send("Page.navigate", { url: `${BASE}/research` });
    await waitFor('!!document.querySelector(".fzx-status")', 30000, "后端状态条");
    const status = await evaluate('document.querySelector(".fzx-status")?.textContent?.trim()');
    check("后端状态为真实模式（没有离线模式标记）", !/离线模式/.test(status ?? ""), status?.slice(0, 80));

    await evaluate(`(() => {
      const box = document.querySelector("#topic");
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
      setter.call(box, ${JSON.stringify(TOPIC)});
      box.dispatchEvent(new Event("input", { bubbles: true }));
      const button = [...document.querySelectorAll("button")].find(b => b.textContent.includes("开始研究"));
      button.click();
      return true;
    })()`);

    // ---------- 2. 真实检索 ----------
    await waitFor('!!document.querySelector(".fzx-candidates .fzx-candidate")', 300000, "真实检索候选池");
    const poolCount = await evaluate('document.querySelectorAll(".fzx-candidates .fzx-candidate").length');
    check("真实检索返回候选材料", poolCount > 0, `${poolCount} 篇`);
    const origins = await evaluate(
      '[...new Set([...document.querySelectorAll(".fzx-candidate-meta span")].map(n => n.textContent))].join(" / ")',
    );
    check("候选池标注来源（红线 1）", /法宝|用户材料|本地依据库/.test(origins), origins.slice(0, 80));
    await shoot("01-真实检索-候选池与人工门");

    // ---------- 3. 人工门：选前 N 篇并确认 ----------
    const gateDisabled = await evaluate(`(() => {
      const button = [...document.querySelectorAll("button")].find(b => b.textContent.trim() === "确认样本");
      return button ? button.disabled : null;
    })()`);
    check("未勾选时确认按钮被禁用", gateDisabled === true);

    // 逐个勾选：一次点多个会因 React 重渲染丢掉点击（实测只生效 1 个）
    const countChecked = () =>
      evaluate(`(() => {
        const boxes = [...document.querySelectorAll(".fzx-candidates .fzx-candidate [role=checkbox]")];
        return boxes.filter(box => box.getAttribute("aria-checked") === "true" || box.dataset.state === "checked").length;
      })()`);
    for (let attempt = 0; attempt < 12; attempt += 1) {
      const checked = await countChecked();
      if (checked >= SAMPLES) break;
      await evaluate(`(() => {
        const boxes = [...document.querySelectorAll(".fzx-candidates .fzx-candidate [role=checkbox]")];
        const next = boxes.find(box => box.getAttribute("aria-checked") !== "true" && box.dataset.state !== "checked");
        if (next) next.click();
        return Boolean(next);
      })()`);
      await sleep(350);
    }
    const selected = await countChecked();
    check(`勾选前 ${SAMPLES} 篇样本`, selected > 0, `${selected} 篇`);
    await evaluate(`(() => {
      const button = [...document.querySelectorAll("button")].find(b => b.textContent.trim() === "确认样本");
      button.click();
      return true;
    })()`);

    // ---------- 4. 矩阵 / 结论 / 门禁 ----------
    await waitFor('document.querySelectorAll(".fzx-table tbody tr").length > 0', 420000, "对比矩阵");
    const matrixRows = await evaluate('document.querySelectorAll(".fzx-table tbody tr").length');
    check("对比矩阵生成", matrixRows > 0, `${matrixRows} 行`);
    check(
      "矩阵含「来源」列",
      await evaluate('[...document.querySelectorAll(".fzx-table th")].some(th => th.textContent === "来源")'),
    );
    await shoot("02-真实结果-矩阵与结论");

    await waitFor('document.querySelectorAll(".fzx-conclusion").length > 0 || document.body.innerText.includes("还没有综合结论")', 420000, "综合结论");
    const conclusionCount = await evaluate('document.querySelectorAll(".fzx-conclusion").length');
    const citeCount = await evaluate('document.querySelectorAll(".fzx-cite").length');
    check("综合结论生成", conclusionCount > 0, `${conclusionCount} 条`);
    check("每条结论都带可点开的引用", citeCount >= conclusionCount, `${citeCount} 个引用`);

    await evaluate(`(() => {
      const tab = [...document.querySelectorAll(".fzx-tab-nav button")].find(b => b.textContent.includes("门禁报告"));
      tab.click();
      return true;
    })()`);
    await sleep(600);
    const gateBody = await evaluate('document.querySelector(".fzx-tab-body")?.textContent ?? ""');
    const coverage = /证据覆盖率\s*(\d+)%/.exec(gateBody)?.[1];
    check("门禁完成逐字核验并给出覆盖率", coverage !== undefined, `覆盖率 ${coverage}%`);
    await shoot("03-真实结果-门禁报告");

    // ---------- 5. 导出 Word ----------
    await evaluate(`(() => {
      const tab = [...document.querySelectorAll(".fzx-tab-nav button")].find(b => b.textContent.includes("依据区"));
      tab.click();
      return true;
    })()`);
    await sleep(400);
    const exportState = await evaluate(`(() => {
      const button = [...document.querySelectorAll("button")].find(b => b.textContent.includes("导出 Word"));
      const blockers = [...document.querySelectorAll(".fzx-note")].map(n => n.textContent).filter(t => t.includes("不能导出"));
      return { disabled: Boolean(button?.disabled), blockers: blockers.slice(0, 3) };
    })()`);

    if (exportState.disabled) {
      check("导出被拦时原因原文可见（门禁未通过）", exportState.blockers.length > 0, exportState.blockers.join(" / ").slice(0, 140));
    } else {
      await evaluate(`(() => {
        const button = [...document.querySelectorAll("button")].find(b => b.textContent.includes("导出 Word"));
        button.click();
        return true;
      })()`);
      await sleep(6000);
      const files = (await readdir(DOWNLOADS)).filter(name => name.endsWith(".docx"));
      const size = files.length ? (await stat(path.join(DOWNLOADS, files[0]))).size : 0;
      check("导出 Word 成功并真的下载", files.length > 0 && size > 5000, `${files[0] ?? "无"} / ${size} 字节`);
      await shoot("04-真实结果-导出完成");
    }

    client.close();
  } finally {
    chrome.kill();
    await rm(profile, { recursive: true, force: true }).catch(() => undefined);
  }

  // ---------- 6. 费用统计（从 run 产物读 token 用量）----------
  let usage = null;
  if (existsSync(RUNS)) {
    const files = (await readdir(RUNS))
      .filter(name => name.endsWith(".output.json"))
      .map(name => path.join(RUNS, name));
    const stats = await Promise.all(files.map(async file => ({ file, mtime: (await stat(file)).mtimeMs })));
    stats.sort((a, b) => b.mtime - a.mtime);
    for (const item of stats.slice(0, 3)) {
      const data = JSON.parse(await readFile(item.file, "utf8"));
      if (data?.usage && Number(data.usage.in || 0) + Number(data.usage.out || 0) > 0) {
        usage = data.usage;
        break;
      }
    }
  }
  if (usage) {
    console.log(`\n本次真实运行 token：输入 ${usage.in} / 输出 ${usage.out}`);
    console.log(`模型费用估算：≈ ¥${cost(usage).toFixed(4)}（不含北大法宝按次计费）`);
  } else {
    console.log("\n未找到含 token 用量的 run 产物，费用未统计。");
  }

  const failed = checks.filter(item => !item.ok);
  console.log(`\n合计 ${checks.length} 项，失败 ${failed.length} 项`);
  for (const item of failed) console.log(`  ✗ ${item.name} ${item.detail}`);
  if (failed.length) process.exitCode = 1;
};

await main();
