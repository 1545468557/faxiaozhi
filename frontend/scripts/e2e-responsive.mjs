/**
 * 六档宽度全量复查（阶段 3-5）
 *
 * 对 5 条路由 × 6 档宽度（375 / 390 / 768 / 1024 / 1280 / 1440）逐个检查：
 * - **横向溢出必须为 0**（宽表要能在容器内滚动，不能撑破页面）；
 * - 记录关键元素的可见性（导航、主内容、状态条、各模块关键区域）；
 * - 顺手收集 Console 错误（有则打印并计入失败）。
 *
 * 截图只保留 375 / 768 / 1440 三档（证据够用，避免几十张图刷仓库）。
 *
 * 用法：
 *   node scripts/e2e-responsive.mjs --base http://localhost:5174 \
 *     --out ../../../docs/evidence/法小智agent开发文档/阶段3-5/screenshots
 */

import { spawn } from "node:child_process";
import { mkdir, rm, writeFile, mkdtemp } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import os from "node:os";

const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const arg = (name, fallback) => {
  const index = process.argv.indexOf(`--${name}`);
  return index === -1 ? fallback : process.argv[index + 1];
};
const BASE = arg("base", "http://localhost:5174");
const OUT = path.resolve(arg("out", "./responsive-shots"));
const PORT = Number(arg("port", "9433"));
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

const WIDTHS = [375, 390, 768, 1024, 1280, 1440];
const SCREENSHOT_WIDTHS = new Set([375, 768, 1440]);
const ROUTES = [
  { path: "/", name: "home", label: "首页", needsStatus: true },
  { path: "/research", name: "research", label: "类案研究", needsStatus: true },
  { path: "/consult", name: "consult", label: "法律咨询", needsStatus: true },
  { path: "/contract", name: "contract", label: "合同审查", needsStatus: true },
  // 关于页是纯静态说明页，不连后端，因此不要求出现后端状态条
  { path: "/about", name: "about", label: "关于", needsStatus: false },
];

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
  const consoleErrors = [];
  let id = 0;
  const ready = new Promise((resolve, reject) => {
    socket.addEventListener("open", () => resolve());
    socket.addEventListener("error", reject);
  });
  socket.addEventListener("message", event => {
    const message = JSON.parse(event.data);
    if (message.method === "Runtime.consoleAPICalled" && message.params.type === "error") {
      consoleErrors.push(message.params.args.map(item => item.value ?? item.description).join(" ").slice(0, 200));
    }
    if (message.method === "Runtime.exceptionThrown") {
      consoleErrors.push(String(message.params.exceptionDetails?.exception?.description ?? "exception").slice(0, 200));
    }
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
  return { ready, send, consoleErrors, close: () => socket.close() };
}

const main = async () => {
  if (!existsSync(CHROME)) throw new Error(`找不到 Chrome：${CHROME}`);
  await mkdir(OUT, { recursive: true });
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
      "--window-size=1440,1200",
      "about:blank",
    ],
    { stdio: "ignore" },
  );

  const rows = [];
  const failures = [];

  try {
    const client = createClient(await connect());
    await client.ready;
    await client.send("Page.enable");
    await client.send("Runtime.enable");

    const evaluate = async expression => {
      const result = await client.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
      if (result.exceptionDetails) throw new Error(result.exceptionDetails.text ?? "页面脚本报错");
      return result.result?.value;
    };

    for (const route of ROUTES) {
      for (const width of WIDTHS) {
        await client.send("Emulation.setDeviceMetricsOverride", {
          width,
          height: width < 600 ? 900 : 1200,
          deviceScaleFactor: 1,
          mobile: width < 600,
        });
        await client.send("Page.navigate", { url: `${BASE}${route.path}` });
        await sleep(width < 600 ? 1400 : 1100);

        const probe = await evaluate(`(() => {
          const de = document.documentElement;
          const body = document.body;
          const overflow = Math.max(de.scrollWidth - de.clientWidth, body.scrollWidth - body.clientWidth);
          const wide = [...document.querySelectorAll("*")]
            .filter(node => node.getBoundingClientRect().right > de.clientWidth + 2)
            .slice(0, 3)
            .map(node => (node.tagName + "." + (node.className || "")).toString().slice(0, 60));
          return {
            overflow,
            wide,
            hasNav: Boolean(document.querySelector(".fs-header")),
            hasMain: Boolean(document.querySelector("#main-content")),
            hasStatus: Boolean(document.querySelector(".fzx-status")),
            title: document.title,
            scrollableTables: [...document.querySelectorAll(".fzx-table-wrap, .fzx-contract-text, .fzx-risks")]
              .every(node => node.scrollWidth <= node.clientWidth || getComputedStyle(node).overflowX !== "visible")
          };
        })()`);

        rows.push({ route: route.label, width, overflow: probe.overflow, wide: probe.wide });
        if (probe.overflow > 0) {
          failures.push(`${route.label} @${width}px 横向溢出 ${probe.overflow}px（元素：${probe.wide.join(" / ")}）`);
          console.log(`✗ ${route.label} @${width}px 溢出 ${probe.overflow}px ${probe.wide.join(" / ")}`);
        } else {
          console.log(`✓ ${route.label} @${width}px 无横向溢出`);
        }
        const missing = [
          !probe.hasNav ? "导航" : "",
          !probe.hasMain ? "主内容" : "",
          route.needsStatus && !probe.hasStatus ? "后端状态条" : "",
        ].filter(Boolean);
        if (missing.length) {
          failures.push(`${route.label} @${width}px 关键元素缺失（${missing.join("/")}）`);
        }

        if (SCREENSHOT_WIDTHS.has(width)) {
          const shot = await client.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
          await writeFile(
            path.join(OUT, `${route.name}-${width}.png`),
            Buffer.from(shot.data, "base64"),
          );
        }
      }
    }

    // Console 错误（整轮收集）
    const realErrors = client.consoleErrors.filter(
      item => !/favicon|Download the React DevTools|vite/i.test(item),
    );
    if (realErrors.length) {
      failures.push(`Console 错误 ${realErrors.length} 条：${realErrors.slice(0, 3).join(" | ")}`);
      console.log(`✗ Console 错误：${realErrors.slice(0, 3).join(" | ")}`);
    } else {
      console.log("✓ 全轮无 Console 错误");
    }

    client.close();
  } finally {
    chrome.kill();
    await rm(profile, { recursive: true, force: true }).catch(() => undefined);
  }

  await writeFile(
    path.join(OUT, "宽度检查结果.json"),
    JSON.stringify({ checkedAt: new Date().toISOString(), rows, failures }, null, 2),
    "utf8",
  );

  console.log(`\n共检查 ${rows.length} 个组合（5 路由 × 6 档宽度），失败 ${failures.length} 项`);
  for (const item of failures) console.log(`  ✗ ${item}`);
  if (failures.length) process.exitCode = 1;
};

await main();
