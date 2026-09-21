/**
 * 界面证据截图（阶段 3 起）
 *
 * 为什么要自己写：Ego 浏览器的截图接口在本机超时（`Page.captureScreenshot` 无响应），
 * 而阶段 3 每个子阶段都要交截图证据。本脚本用系统自带的 Chrome 无头模式 + CDP 直接截图。
 *
 * 用法：
 *   node scripts/capture-ui.mjs --base http://localhost:5174 --out ../docs/evidence/.../阶段3/screenshots [--flow]
 *
 * `--flow` 会在 /research 上真实触发一次「开始研究」（配合离线后端跑失败路径，零成本）。
 * 依赖：本机已装 Google Chrome；仅使用 Node 内置能力（fetch + WebSocket）。
 */

import { spawn } from "node:child_process";
import { mkdir, writeFile, rm, mkdtemp } from "node:fs/promises";
import os from "node:os";
import { existsSync } from "node:fs";
import path from "node:path";

const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

function arg(name, fallback) {
  const index = process.argv.indexOf(`--${name}`);
  return index === -1 ? fallback : process.argv[index + 1];
}

const BASE = arg("base", "http://localhost:5174");
const OUT = path.resolve(arg("out", "./screenshots"));
const FLOW = process.argv.includes("--flow");
const PORT = Number(arg("port", "9333"));

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function connect() {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    try {
      const list = await fetch(`http://127.0.0.1:${PORT}/json/list`).then(r => r.json());
      const page = list.find(item => item.type === "page");
      if (page?.webSocketDebuggerUrl) return page.webSocketDebuggerUrl;
    } catch {
      /* 还没起来 */
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
  if (!existsSync(CHROME)) throw new Error(`找不到 Chrome：${CHROME}`);
  await mkdir(OUT, { recursive: true });
  // Chrome 的临时用户目录放在系统临时目录里（2026-09-20 修：原先放在 OUT 下，
  // 截图目录里会残留 .chrome-profile，且删除与 git add 抢文件会报错）
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
      "--window-size=1440,1200",
      "about:blank",
    ],
    { stdio: "ignore" },
  );

  try {
    const client = createClient(await connect());
    await client.ready;
    await client.send("Page.enable");
    await client.send("Runtime.enable");

    const evaluate = async expression => {
      const result = await client.send("Runtime.evaluate", {
        expression,
        awaitPromise: true,
        returnByValue: true,
      });
      if (result.exceptionDetails) throw new Error(result.exceptionDetails.text ?? "页面脚本报错");
      return result.result?.value;
    };

    const goto = async (url, waitMs = 2500) => {
      await client.send("Page.navigate", { url });
      await sleep(waitMs);
    };

    const shoot = async (name, { width = 1440, height = 1200, full = true } = {}) => {
      await client.send("Emulation.setDeviceMetricsOverride", {
        width,
        height,
        deviceScaleFactor: 1,
        mobile: width < 600,
      });
      await sleep(700);
      const shot = await client.send("Page.captureScreenshot", {
        format: "png",
        captureBeyondViewport: full,
      });
      const file = path.join(OUT, `${name}.png`);
      await writeFile(file, Buffer.from(shot.data, "base64"));
      console.log(`✓ ${file}`);
    };

    // 1) 首页（桌面）
    await goto(`${BASE}/`);
    await shoot("01-首页-后端状态");
    console.log("  首页状态条：", await evaluate('document.querySelector(".fzx-status")?.textContent?.trim().slice(0,120)'));

    // 2) 类案研究：空态
    await goto(`${BASE}/research`);
    await shoot("02-类案研究-初始态");

    // 3) 触发一次研究（离线后端：模型 stub + 检索地址不可达 → 失败路径，零成本）
    if (FLOW) {
      await evaluate(`(() => {
        const box = document.querySelector("#topic");
        const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
        setter.call(box, "房屋租赁中，提前退租时押金是否可以全部不予返还？");
        box.dispatchEvent(new Event("input", { bubbles: true }));
        return true;
      })()`);
      await sleep(300);
      await evaluate(`(() => {
        const button = [...document.querySelectorAll("button")].find(b => b.textContent.includes("开始研究"));
        button.click();
        return true;
      })()`);
      // 等运行收尾：按**文字**判定（进行中 → 未完成/已完成），最多等 90 秒
      let settled = false;
      for (let attempt = 0; attempt < 90; attempt += 1) {
        const pill = await evaluate(
          'document.querySelector(".fzx-monitor .fzx-pill:last-of-type")?.textContent ?? ""',
        );
        if (/未完成|已完成|等待你确认/.test(pill)) {
          settled = true;
          break;
        }
        await sleep(1000);
      }
      if (!settled) console.log("  ⚠ 运行未在 90 秒内收尾，截图可能不是终态");
      await sleep(800);
      await shoot("03-类案研究-运行结果与依据缺口");
      console.log(
        "  运行面板：",
        await evaluate('document.querySelector(".fzx-monitor")?.textContent?.trim().slice(0,260)'),
      );
      console.log(
        "  导出按钮禁用：",
        await evaluate('[...document.querySelectorAll("button")].filter(b => b.textContent.includes("导出 Word")).map(b => b.disabled)'),
      );
    }

    // 4) 未接线模块（诚实占位）
    await goto(`${BASE}/consult`);
    await shoot("04-法律咨询-未接线");
    await goto(`${BASE}/contract`);
    await shoot("05-合同审查-未接线");

    // 5) 关于页
    await goto(`${BASE}/about`);
    await shoot("06-关于页");

    // 6) 手机宽度 390px
    await goto(`${BASE}/`);
    await shoot("07-手机390-首页", { width: 390, height: 900, full: false });
    await goto(`${BASE}/research`);
    await shoot("08-手机390-类案研究", { width: 390, height: 900, full: false });
    const overflow = await evaluate(
      'document.documentElement.scrollWidth - document.documentElement.clientWidth',
    );
    console.log("  390px 横向溢出像素：", overflow);

    client.close();
  } finally {
    chrome.kill();
    await rm(profile, { recursive: true, force: true }).catch(() => undefined);
  }
};

await main();
