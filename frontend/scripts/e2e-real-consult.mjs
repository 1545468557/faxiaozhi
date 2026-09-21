/**
 * 法律咨询**真实链路**联调（会花钱：≈¥0.05–0.15 / 次）
 *
 * 真实点一遍：提问 →（真模型可能追问）→ 补充事实 → 真法宝检索（法条 + 案例）
 *   → 引用逐字核验 → **四块解答** → 导出咨询备忘 → 费用统计
 *
 * 运行前：后端必须是真实模式（脚本会自检，是 stub 直接退出），且产品经理已同意花费。
 *
 * 用法：
 *   node scripts/e2e-real-consult.mjs --base http://localhost:5174 \
 *     --out ../../../docs/evidence/法小智agent开发文档/阶段3-3/screenshots-real \
 *     --runs ../runs
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
const OUT = path.resolve(arg("out", "./consult-real-shots"));
const RUNS = path.resolve(arg("runs", "./runs"));
const PORT = Number(arg("port", "9399"));
const DOWNLOADS = "/tmp/fzx-consult-real-downloads";
const QUESTION = arg("question", "公司拖欠我三个月工资，没有签劳动合同，我可以要求什么？");
const FACTS = arg(
  "facts",
  "我在一家小公司做设计，2024 年 3 月入职，没有签书面劳动合同，工资通过微信转账，每月一万元。公司从 2024 年 10 月起开始拖欠工资，到 2025 年 1 月共拖欠三个月，我还在职，手上有微信聊天记录和银行流水。",
);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
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
  const bootstrap = await fetch(`${BASE}/api/bff/bootstrap`).then(r => r.json());
  if (bootstrap?.model?.is_stub) {
    console.error("✗ 后端当前是离线（stub）模式，本脚本只用于真实链路。已退出，未产生费用。");
    process.exitCode = 2;
    return;
  }
  console.log(`后端：${bootstrap.model.model_id}（真实模式）`);
  console.log(`问题：${QUESTION}`);

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

    await client.send("Page.navigate", { url: `${BASE}/consult` });
    await waitFor('!!document.querySelector(".fzx-status")', 30000, "后端状态条");
    check("后端为真实模式", !/离线模式/.test(await evaluate('document.querySelector(".fzx-status")?.textContent ?? ""')));

    // ---------- 提问 ----------
    await evaluate(`(() => {
      const box = document.querySelector("#question");
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
      setter.call(box, ${JSON.stringify(QUESTION)});
      box.dispatchEvent(new Event("input", { bubbles: true }));
      [...document.querySelectorAll("button")].find(b => b.textContent.includes("发送问题")).click();
      return true;
    })()`);

    // 真模型可能追问 1~3 轮，也可能直接作答 —— 用循环把每一轮追问都答掉
    const ROUND_ANSWERS = [
      FACTS,
      "公司没有给我任何不支付工资的说明；我在 2024 年 12 月通过微信向负责人催过一次，对方说资金紧张；工资是负责人个人微信转账，我跟着项目组走，不需要打卡但每天要在群里汇报进度。",
      "拖欠的是 2024 年 10 月、11 月、12 月三个月；每月约定一万元；这三个月的工资一分没发；我目前仍在职，可以随时提供微信聊天记录和银行流水。",
    ];
    let roundsAnswered = 0;
    for (let round = 0; round < 3; round += 1) {
      await waitFor(
        '!!document.querySelector(".fzx-clarify") || document.body.innerText.includes("一、结论")',
        300000,
        `第 ${round + 1} 次等待（追问或解答）`,
      );
      const askingClarify = await evaluate('!!document.querySelector(".fzx-clarify")');
      if (!askingClarify) break;

      const rounds = await evaluate('document.querySelector(".fzx-clarify .fzx-pill")?.textContent?.trim()');
      const questions = await evaluate('document.querySelectorAll(".fzx-clarify ol li").length');
      check(
        `追问第 ${round + 1} 轮：显示轮数与问题`,
        /第\s*\d+\s*轮\s*\/\s*最多\s*3\s*轮/.test(rounds ?? "") && questions > 0,
        `${rounds} / ${questions} 个问题`,
      );
      if (round === 0) await shoot("01-真实追问卡");

      const answer = ROUND_ANSWERS[Math.min(round, ROUND_ANSWERS.length - 1)];
      await evaluate(`(() => {
        const box = document.querySelector("#facts");
        const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
        setter.call(box, ${JSON.stringify(answer)});
        box.dispatchEvent(new Event("input", { bubbles: true }));
        [...document.querySelectorAll("button")].find(b => b.textContent.includes("提交补充事实")).click();
        return true;
      })()`);
      roundsAnswered += 1;
      await sleep(1500);
    }
    if (!roundsAnswered) check("真实模型未追问，直接给出解答（同样是合法路径）", true);
    else console.log(`  已回答 ${roundsAnswered} 轮追问`);

    // ---------- 四块解答 ----------
    await waitFor('document.body.innerText.includes("一、结论")', 420000, "四块解答");
    const blocks = await evaluate(`({
      conclusion: document.body.innerText.includes("一、结论"),
      cites: document.body.innerText.includes("二、引用"),
      risks: document.body.innerText.includes("三、不确定与风险"),
      next: document.body.innerText.includes("四、建议的下一步"),
      conclusions: document.querySelectorAll(".fzx-conclusion").length,
      citeButtons: document.querySelectorAll(".fzx-cite").length,
      conclusionTexts: [...document.querySelectorAll(".fzx-conclusion-text")].map(n => n.textContent.trim()).slice(0, 3)
    })`);
    check("四块解答齐全", blocks.conclusion && blocks.cites && blocks.risks && blocks.next);
    check("结论有内容（真实模型）", blocks.conclusions > 0, `${blocks.conclusions} 条`);
    check("每条结论带可点开的引用", blocks.citeButtons >= blocks.conclusions, `${blocks.citeButtons} 个引用`);
    if (blocks.conclusionTexts.length) console.log(`  结论示例：${blocks.conclusionTexts[0].slice(0, 90)}`);
    await shoot("02-真实四块解答");

    await evaluate(`(() => {
      [...document.querySelectorAll(".fzx-tab-nav button")].find(b => b.textContent.includes("门禁报告")).click();
      return true;
    })()`);
    await sleep(600);
    const gateBody = await evaluate('document.querySelector(".fzx-tab-body")?.textContent ?? ""');
    const coverage = /证据覆盖率\s*(\d+)%/.exec(gateBody)?.[1];
    check("引用逐字核验完成", coverage !== undefined, `覆盖率 ${coverage}%`);

    // ---------- 导出咨询备忘 ----------
    await evaluate(`(() => {
      [...document.querySelectorAll(".fzx-tab-nav button")].find(b => b.textContent.includes("依据区")).click();
      return true;
    })()`);
    await sleep(500);
    const exportState = await evaluate(`(() => {
      const button = [...document.querySelectorAll("button")].find(b => b.textContent.includes("导出咨询备忘"));
      const blockers = [...document.querySelectorAll(".fzx-note")].map(n => n.textContent).filter(t => t.includes("不能导出"));
      return { disabled: Boolean(button?.disabled), blockers: blockers.slice(0, 3) };
    })()`);
    if (exportState.disabled) {
      check("导出被拦时原因原文可见", exportState.blockers.length > 0, exportState.blockers.join(" / ").slice(0, 130));
    } else {
      await evaluate(`(() => {
        [...document.querySelectorAll("button")].find(b => b.textContent.includes("导出咨询备忘")).click();
        return true;
      })()`);
      await sleep(6000);
      const files = (await readdir(DOWNLOADS)).filter(name => name.endsWith(".docx"));
      const size = files.length ? (await stat(path.join(DOWNLOADS, files[0]))).size : 0;
      check("导出咨询备忘成功并真的下载", files.length > 0 && size > 3000, `${files[0] ?? "无"} / ${size} 字节`);
      await shoot("03-真实导出完成");
    }

    client.close();
  } finally {
    chrome.kill();
    await rm(profile, { recursive: true, force: true }).catch(() => undefined);
  }

  // 费用
  let usage = null;
  if (existsSync(RUNS)) {
    const files = (await readdir(RUNS)).filter(name => name.endsWith(".output.json"));
    const stats = await Promise.all(files.map(async file => ({ file: path.join(RUNS, file), mtime: (await stat(path.join(RUNS, file))).mtimeMs })));
    stats.sort((a, b) => b.mtime - a.mtime);
    for (const item of stats.slice(0, 4)) {
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
  }

  const failed = checks.filter(item => !item.ok);
  console.log(`\n合计 ${checks.length} 项，失败 ${failed.length} 项`);
  for (const item of failed) console.log(`  ✗ ${item.name} ${item.detail}`);
  if (failed.length) process.exitCode = 1;
};

await main();
