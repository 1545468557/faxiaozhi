/**
 * v3 首页对话链路验收（真实浏览器 + 真实 BFF）
 *
 * 为什么单独写一个：截图脚本只能证明"画出来了"，证明不了
 * 「点了发送 → SSE 真的在流 → 收尾 → 刷新后还能接着看」。
 *
 * 用法：
 *   node scripts/verify-v3-ui.mjs --base http://[::1]:5174 --out /tmp/v3-ui
 *
 * 前置：v3 后端已起在 8011（离线 stub 模式即可，不花钱），前端 dev server 在 5174。
 * 输出全部走 stdout，脚本自己收尾退出（headless Chrome 需要 --no-sandbox）。
 */

import { spawn } from "node:child_process";
import { mkdir, writeFile, mkdtemp } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import os from "node:os";

const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

function arg(name, fallback) {
  const index = process.argv.indexOf(`--${name}`);
  return index === -1 ? fallback : process.argv[index + 1];
}

const BASE = arg("base", "http://[::1]:5174");
const OUT = path.resolve(arg("out", "/tmp/v3-ui"));
const PORT = Number(arg("port", "9355"));
const QUESTION = arg("question", "房东把房子卖了，新房东让我搬走，我该怎么办？");

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function connect() {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    try {
      const list = await fetch(`http://127.0.0.1:${PORT}/json/list`).then(r => r.json());
      const page = list.find(item => item.type === "page");
      if (page?.webSocketDebuggerUrl) return page.webSocketDebuggerUrl;
    } catch {
      /* 调试端口还没起来 */
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

const checks = [];
const check = (name, ok, detail = "") => {
  checks.push({ name, ok, detail });
  console.log(`${ok ? "✓" : "✗"} ${name}${detail ? `  — ${detail}` : ""}`);
};

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
      // 沙箱里 Chrome 起不了自己的沙箱与 GPU 进程，缺这两个开关会 FATAL
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
    await client.send("Network.enable");

    const evaluate = async expression => {
      const result = await client.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
      if (result.exceptionDetails) throw new Error(result.exceptionDetails.text ?? "页面脚本报错");
      return result.result?.value;
    };

    await client.send("Emulation.setDeviceMetricsOverride", {
      width: 1440,
      height: 1000,
      deviceScaleFactor: 1,
      mobile: false,
    });

    await client.send("Page.navigate", { url: `${BASE}/` });
    await sleep(3000);

    // 1. 空态
    const home = await evaluate(`(() => ({
      title: document.querySelector(".v2-home-head h1")?.textContent?.trim() ?? "",
      composer: Boolean(document.querySelector(".v2-composer textarea")),
      entries: [...document.querySelectorAll(".v2-entry")].map(e => e.textContent.trim().split("\\n")[0]),
      scenes: document.querySelectorAll(".v2-scene").length,
      nav: [...document.querySelectorAll(".v2-nav a")].map(a => a.textContent.trim()),
    }))()`);
    check("空态渲染出标题与输入框", home.title.includes("问问小智") && home.composer, home.title);
    check("下面挂了三个入口", home.entries.length === 3, home.entries.join(" / "));
    check("导航四项齐全", JSON.stringify(home.nav) === JSON.stringify(["法律问答", "案例查询", "法规查找", "我的"]), home.nav.join("/"));

    // 2. 离线徽标：bootstrap 说 is_stub=true，界面必须明说
    const stubAlert = await evaluate(
      `(() => { const el = [...document.querySelectorAll(".v2-alert")].find(n => n.textContent.includes("离线示例")); return el ? el.textContent.replace(/\\s+/g, " ").trim().slice(0, 60) : ""; })()`,
    );
    check("离线状态在界面上明说了", Boolean(stubAlert), stubAlert);

    // 3. 发一句话
    await evaluate(`(() => {
      const box = document.querySelector(".v2-composer textarea");
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
      setter.call(box, ${JSON.stringify(QUESTION)});
      box.dispatchEvent(new Event("input", { bubbles: true }));
      return true;
    })()`);
    await sleep(250);
    await evaluate(`(() => { document.querySelector(".v2-send").click(); return true; })()`);

    // 4. 流式过程中："停止"按钮出现过，且画面在变长
    let sawStop = false;
    let sawGrowing = false;
    let lastLen = 0;
    for (let attempt = 0; attempt < 60; attempt += 1) {
      const state = await evaluate(`(() => {
        const prose = document.querySelector(".v3-prose");
        return {
          stop: [...document.querySelectorAll(".v2-send")].some(b => b.textContent.includes("停止")),
          thinking: Boolean(document.querySelector(".v2-thinking")),
          len: prose ? prose.textContent.length : 0,
          caret: Boolean(document.querySelector(".v3-caret")),
        };
      })()`);
      if (state.stop) sawStop = true;
      if (state.len > lastLen && lastLen >= 0) sawGrowing = sawGrowing || state.len > 0;
      lastLen = state.len;
      if (!state.stop && !state.thinking && state.len > 0) break;
      await sleep(250);
    }
    check("生成中有「停止」按钮", sawStop);
    check("回答是流式长出来的", sawGrowing, `最终 ${lastLen} 字`);

    await sleep(900);

    // 5. 收尾后的形状
    const done = await evaluate(`(() => {
      const prose = document.querySelector(".v3-prose");
      const turns = [...document.querySelectorAll(".v2-turn")].map(t => t.getAttribute("data-role"));
      const sid = localStorage.getItem("faxiaozhi:v3:sid");
      return {
        text: prose ? prose.textContent : "",
        caret: Boolean(document.querySelector(".v3-caret")),
        badge: Boolean(document.querySelector(".v3-badge")),
        turns,
        sid,
        overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        userBubble: document.querySelector(".v2-turn[data-role='user'] .v2-bubble")?.textContent?.trim() ?? "",
        foot: document.querySelector(".v3-foot")?.textContent?.trim() ?? "",
      };
    })()`);
    check("回答正文有内容", done.text.length > 40, `${done.text.length} 字`);
    check("收尾后光标消失", !done.caret);
    check("收尾后没有误报「中断」", !done.foot.includes("中断"), done.foot);
    check("离线示例徽标挂在回答上", done.badge);
    check("用户那句话在右侧气泡里", done.userBubble === QUESTION, done.userBubble.slice(0, 24));
    check("会话 id 落到 localStorage", Boolean(done.sid), done.sid ?? "");
    check("桌面视口无横向溢出", done.overflow === 0, `${done.overflow}px`);

    const shot1 = await client.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
    await writeFile(path.join(OUT, "对话态.png"), Buffer.from(shot1.data, "base64"));

    // 6. 刷新后能不能接上（v3 的持久化卖点）
    const before = done.text;
    await client.send("Page.navigate", { url: `${BASE}/` });
    await sleep(3200);
    const after = await evaluate(
      `(() => { const p = document.querySelector(".v3-prose"); return { text: p ? p.textContent : "", started: Boolean(document.querySelector(".v2-thread")), thinking: Boolean(document.querySelector(".v2-thinking")) }; })()`,
    );
    check("刷新后回到对话态", after.started);
    check("刷新后回答原样还在", after.text === before, `${after.text.length} 字`);
    check("刷新后没有卡在生成中", !after.thinking);

    // 7. 手机视口不炸
    await client.send("Emulation.setDeviceMetricsOverride", {
      width: 390,
      height: 844,
      deviceScaleFactor: 1,
      mobile: true,
    });
    await sleep(700);
    const narrow = await evaluate(
      `(() => ({ overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth, nav: [...document.querySelectorAll(".v2-nav a")].length }))()`,
    );
    check("手机视口无横向溢出", narrow.overflow === 0, `${narrow.overflow}px`);
    const shot2 = await client.send("Page.captureScreenshot", { format: "png" });
    await writeFile(path.join(OUT, "对话态-手机390.png"), Buffer.from(shot2.data, "base64"));

    client.close();
    console.log(`\n截图：${OUT}/对话态.png、${OUT}/对话态-手机390.png`);
  } finally {
    chrome.kill();
  }

  const failed = checks.filter(item => !item.ok);
  console.log(`\n结果：${checks.length - failed.length}/${checks.length} 项通过`);
  if (failed.length) {
    console.log("未通过：");
    for (const item of failed) console.log(`  - ${item.name}${item.detail ? `（${item.detail}）` : ""}`);
    process.exitCode = 1;
  }
};

main()
  .then(() => process.exit(process.exitCode ?? 0))
  .catch(error => {
    console.error("验收失败：", error?.message ?? error);
    process.exit(1);
  });
