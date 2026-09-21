/**
 * v2 界面证据截图与横向溢出体检
 *
 * 为什么要自己写：headless Chrome 的 `--window-size` 有最小窗口宽度，
 * `--screenshot` 直接截出来的「手机尺寸」是被裁的假图。这里用 CDP 的
 * Emulation.setDeviceMetricsOverride 真正把视口设成 390，并量出横向溢出像素。
 *
 * 用法：
 *   node scripts/capture-v2.mjs --base http://localhost:5174 --out /tmp/v2-shots
 *   node scripts/capture-v2.mjs --flow   # 额外在首页真的发一条消息（会调用真实模型，可能收费）
 *
 * 依赖：本机已装 Google Chrome；仅用 Node 内置能力（fetch + WebSocket）。
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
const OUT = path.resolve(arg("out", "/tmp/v2-shots"));
const PORT = Number(arg("port", "9344"));
const FLOW = process.argv.includes("--flow");
const QUESTION = arg("question", "房东把房子卖了，新房东让我搬走，我该怎么办？");

const WIDTHS = [
  { name: "桌面1440", width: 1440, height: 1000 },
  { name: "平板768", width: 768, height: 1000 },
  { name: "手机390", width: 390, height: 844 },
];

const DEFAULT_ROUTES = [
  { path: "/", name: "法律问答首页" },
  { path: "/research", name: "类案检索" },
  { path: "/contract", name: "合同审查" },
];

/** --routes "/路径|名称,/路径|名称" 可只截指定页面（设计稿等临时页面用） */
const ROUTES = (() => {
  const custom = arg("routes", "");
  if (!custom) return DEFAULT_ROUTES;
  return custom.split(",").map(spec => {
    const [path, name] = spec.split("|");
    return { path: path.trim(), name: (name || path).trim() };
  });
})();

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
      // 本机沙箱不允许 Chrome 初始化自己的沙箱与 GPU 进程，缺这两个开关会直接 FATAL 退出
      "--no-sandbox",
      "--disable-dev-shm-usage",
      "--hide-scrollbars",
      "--no-first-run",
      "--no-default-browser-check",
      "--proxy-server=direct://",
      "--proxy-bypass-list=*",
      `--remote-debugging-port=${PORT}`,
      `--user-data-dir=${profile}`,
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
      const result = await client.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
      if (result.exceptionDetails) throw new Error(result.exceptionDetails.text ?? "页面脚本报错");
      return result.result?.value;
    };

    const viewport = async (width, height) => {
      await client.send("Emulation.setDeviceMetricsOverride", {
        width,
        height,
        deviceScaleFactor: 1,
        mobile: width < 600,
      });
      await sleep(400);
    };

    const goto = async (url, waitMs = 2600) => {
      await client.send("Page.navigate", { url });
      await sleep(waitMs);
    };

    const shoot = async (name, { full = true } = {}) => {
      const shot = await client.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: full });
      const file = path.join(OUT, `${name}.png`);
      await writeFile(file, Buffer.from(shot.data, "base64"));
      return file;
    };

    const metrics = () =>
      evaluate(`(() => ({
        overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        width: document.documentElement.clientWidth,
        nav: [...document.querySelectorAll(".v2-nav a")].map(a => a.textContent.trim()),
        active: document.querySelector(".v2-nav a[data-active='true']")?.textContent?.trim() ?? null,
      }))()`);

    console.log("路由 × 视口 横向溢出体检（单位：像素，0 才算过）");
    const bad = [];
    for (const size of WIDTHS) {
      await viewport(size.width, size.height);
      for (const route of ROUTES) {
        await goto(`${BASE}${route.path}`, 1800);
        const m = await metrics();
        const flag = m.overflow > 0 ? "  ← 溢出" : "";
        if (m.overflow > 0) bad.push(`${size.name} ${route.path} 溢出 ${m.overflow}px`);
        console.log(
          `  ${size.name.padEnd(9)} ${route.path.padEnd(10)} 视口 ${String(m.width).padStart(4)}  溢出 ${String(m.overflow).padStart(4)}${flag}  当前页 ${m.active}  导航 ${m.nav.join("/")}`,
        );
      }
    }

    // 默认（未传 --routes）时保持原有行为：整页截首页 + 视口截案例查询。
    // 传了 --routes 时，逐个截指定页面（第一个整页、其余按视口），文件名用给定名称。
    const customRoutes = arg("routes", "") !== "";
    const shotList = customRoutes
      ? ROUTES.map((route, index) => ({
          path: route.path,
          name: `${route.name}`,
          full: index === 0,
          wait: 1800,
        }))
      : [
          { path: "/", name: "法律问答首页", full: true, wait: 2200 },
          { path: "/research", name: "类案检索", full: false, wait: 1800 },
        ];

    for (const size of WIDTHS) {
      await viewport(size.width, size.height);
      for (const route of shotList) {
        await goto(`${BASE}${route.path}`, route.wait);
        console.log(`✓ ${await shoot(`${size.name}-${route.name}`, { full: route.full })}`);
      }
    }

    if (FLOW) {
      await viewport(1440, 1000);
      await goto(`${BASE}/`, 2200);
      await evaluate(`(() => {
        const box = document.querySelector(".v2-composer textarea");
        const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
        setter.call(box, ${JSON.stringify(QUESTION)});
        box.dispatchEvent(new Event("input", { bubbles: true }));
        return true;
      })()`);
      await sleep(300);
      await evaluate(`(() => {
        document.querySelector(".v2-send").click();
        return true;
      })()`);
      let settled = false;
      for (let attempt = 0; attempt < 120; attempt += 1) {
        const state = await evaluate(
          `(() => ({ thinking: Boolean(document.querySelector(".v2-thinking")), turns: document.querySelectorAll(".v2-turn").length }))()`,
        );
        if (!state.thinking && state.turns > 1) {
          settled = true;
          break;
        }
        await sleep(1000);
      }
      console.log(settled ? "  对话已收尾" : "  ⚠ 120 秒内未见终态");
      await sleep(600);
      console.log(`✓ ${await shoot("对话态-首页提问后")}`);
      console.log("  最后一轮：", await evaluate('document.querySelector(".v2-turn:last-of-type")?.textContent?.trim().slice(0, 220)'));
    }

    console.log(bad.length ? `\n横向溢出问题 ${bad.length} 处：\n  - ${bad.join("\n  - ")}` : "\n全部视口无横向溢出。");
    client.close();
  } finally {
    chrome.kill();
    await rm(profile, { recursive: true, force: true }).catch(() => undefined);
  }
};

await main()
  .then(() => process.exit(0))
  .catch(error => {
    console.error("采集失败：", error?.message ?? error);
    process.exit(1);
  });
