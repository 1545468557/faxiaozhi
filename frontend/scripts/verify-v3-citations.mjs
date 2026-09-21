/**
 * 「引用变行内链接」的真机验收
 *
 * 为什么不能只跑单元测试：单元测试只证明匹配函数对，证明不了
 * 「sources 事件 → 挂到气泡 → 渲染成可点的链接 → 点开看到原文」这条链是通的。
 *
 * 为什么用拦截而不是直接发请求：离线模式下后端不发检索请求（`sources` 事件根本不会出现），
 * 而打开真实检索会消耗法宝额度。这里用 CDP 的 Fetch 域把 `/api/bff/v3/chat` 拦下来、
 * 喂一段**固定的** SSE，就能在不花钱的前提下把渲染链路跑真。
 *
 * 用法：node scripts/verify-v3-citations.mjs --base http://[::1]:5174 --out /tmp/v3-cite
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
const OUT = path.resolve(arg("out", "/tmp/v3-cite"));
const PORT = Number(arg("port", "9366"));

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

// 三段材料：前两段会在正文里被点名，第三段故意不点名 —— 用来验证"没点名的落到参考材料里"
const SOURCES = [
  {
    kind: "statute",
    title: "中华人民共和国民法典",
    identifier: "《中华人民共和国民法典》第七百二十五条",
    court: "",
    decided_on: "",
    uri: "https://example.invalid/statute/725",
    origin_text: "",
    quote: "租赁物在承租人按照租赁合同占有期限内发生所有权变动的，不影响租赁合同的效力。",
  },
  {
    kind: "case",
    title: "某某与某某房屋租赁合同纠纷",
    identifier: "（2021）京01民终1234号",
    court: "北京市第一中级人民法院",
    decided_on: "2021-06-01",
    uri: "https://example.invalid/case/1234",
    origin_text: "",
    quote: "买受人所购房屋已出租的，承租人有权继续履行原租赁合同。",
  },
  {
    kind: "statute",
    title: "中华人民共和国民法典",
    identifier: "《中华人民共和国民法典》第七百三十四条",
    court: "",
    decided_on: "",
    uri: "https://example.invalid/statute/734",
    origin_text: "",
    quote: "租赁期限届满，承租人继续使用租赁物，出租人没有提出异议的，原租赁合同继续有效。",
  },
];

// 正文只点名前两段；简称 + 条号的写法是有意为之（模型基本都这么写）
const ANSWER = [
  "依据",
  "《民法典》第七百二十五条",
  "，租赁物在租赁期内所有权变动不影响租赁合同的效力，你可以继续住。\n\n",
  "另外可参考（2021）京01民终1234号",
  " 的裁判思路。",
];

const sse = (...events) =>
  events.map(([name, data]) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`).join("");

const BODY = sse(
  ["meta", { session_id: "citecheck0001", is_stub: false, title: "行内引用验收" }],
  ["sources", { items: SOURCES, notes: [] }],
  ...ANSWER.map(text => ["delta", { text }]),
  ["done", { session_id: "citecheck0001", message_id: "m1", chars: 60, sources: SOURCES.length, elapsed_ms: 900 }],
);

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
  const listeners = new Map();
  let id = 0;
  const ready = new Promise((resolve, reject) => {
    socket.addEventListener("open", () => resolve());
    socket.addEventListener("error", reject);
  });
  socket.addEventListener("message", event => {
    const message = JSON.parse(event.data);
    if (message.id) {
      const entry = pending.get(message.id);
      if (!entry) return;
      pending.delete(message.id);
      if (message.error) entry.reject(new Error(JSON.stringify(message.error)));
      else entry.resolve(message.result);
      return;
    }
    for (const handler of listeners.get(message.method) ?? []) handler(message.params);
  });
  const send = (method, params = {}) =>
    new Promise((resolve, reject) => {
      id += 1;
      pending.set(id, { resolve, reject });
      socket.send(JSON.stringify({ id, method, params }));
    });
  /** 订阅 CDP 事件：拦请求必须要它（拦截是"事件 + 主动放行"的模式，不是请求-应答） */
  const on = (method, handler) => {
    if (!listeners.has(method)) listeners.set(method, []);
    listeners.get(method).push(handler);
  };
  return { ready, send, on, close: () => socket.close() };
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

    let intercepted = 0;
    client.on("Fetch.requestPaused", params => {
      const body = Buffer.from(BODY, "utf8").toString("base64");
      intercepted += 1;
      void client.send("Fetch.fulfillRequest", {
        requestId: params.requestId,
        responseCode: 200,
        responseHeaders: [
          { name: "content-type", value: "text/event-stream; charset=utf-8" },
          { name: "cache-control", value: "no-cache" },
        ],
        body,
      });
    });
    // 只拦对话接口，其余（静态资源、bootstrap）照常走网络
    await client.send("Fetch.enable", { patterns: [{ urlPattern: "*api/bff/v3/chat*", requestStage: "Request" }] });

    const evaluate = async expression => {
      const result = await client.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
      if (result.exceptionDetails) throw new Error(result.exceptionDetails.text ?? "页面脚本报错");
      return result.result?.value;
    };

    await client.send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 1000, deviceScaleFactor: 1, mobile: false });
    await client.send("Page.navigate", { url: `${BASE}/` });
    await sleep(3000);

    await evaluate(`(() => {
      const box = document.querySelector(".v2-composer textarea");
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
      setter.call(box, "房东把房子卖了，新房东让我搬走，我该怎么办？");
      box.dispatchEvent(new Event("input", { bubbles: true }));
      return true;
    })()`);
    await sleep(200);
    await evaluate(`(() => { document.querySelector(".v2-send").click(); return true; })()`);
    await sleep(2500);

    check("对话请求被拦截并喂了固定 SSE", intercepted > 0, `${intercepted} 次`);

    const shape = await evaluate(`(() => {
      const cites = [...document.querySelectorAll(".v3-cite")];
      const refs = [...document.querySelectorAll(".v3-refs .v2-cite")];
      const prose = document.querySelector(".v3-prose");
      return {
        citeTexts: cites.map(c => c.textContent),
        refTexts: refs.map(r => r.textContent),
        prose: prose ? prose.textContent : "",
        badge: Boolean(document.querySelector(".v3-badge")),
        sourceCount: [...document.querySelectorAll(".v3-foot")].map(f => f.textContent).join(" "),
      };
    })()`);

    check(
      "正文里的简称《民法典》第七百二十五条 变成可点链接",
      shape.citeTexts.includes("《民法典》第七百二十五条"),
      shape.citeTexts.join(" | "),
    );
    check(
      "案号也变成可点链接",
      shape.citeTexts.includes("（2021）京01民终1234号"),
      "",
    );
    check("恰好两个行内链接", shape.citeTexts.length === 2, `${shape.citeTexts.length} 个`);
    check(
      "没在正文点名的第三段材料落到「参考材料」里",
      shape.refTexts.some(text => text.includes("第七百三十四条")),
      shape.refTexts.join(" | "),
    );
    check("参考材料只有一条（被点名的不重复列出）", shape.refTexts.length === 1, `${shape.refTexts.length} 条`);
    check("非离线时不显示「离线示例」徽标", !shape.badge);
    check("正文文字完整", shape.prose.includes("你可以继续住") && shape.prose.includes("的裁判思路"), `${shape.prose.length} 字`);
    check("标注了材料条数与未核验", shape.sourceCount.includes("3 条材料") && shape.sourceCount.includes("未逐条核验"), shape.sourceCount);

    const shot = await client.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
    await writeFile(path.join(OUT, "行内引用.png"), Buffer.from(shot.data, "base64"));

    // 点一下链接，看是不是真的能打开材料详情
    await evaluate(`(() => { document.querySelector(".v3-cite").click(); return true; })()`);
    await sleep(800);
    const sheet = await evaluate(`(() => {
      const el = document.querySelector(".source-sheet");
      return el ? el.textContent.replace(/\\s+/g, " ").trim() : "";
    })()`);
    check("点链接能打开材料详情", sheet.includes("第七百二十五条"), sheet.slice(0, 70));
    check("详情里能读到原文节选", sheet.includes("不影响租赁合同的效力"), "");
    check("详情里明确说了未逐条核验", sheet.includes("没有逐条核验"), "");

    const sheetShot = await client.send("Page.captureScreenshot", { format: "png" });
    await writeFile(path.join(OUT, "材料详情.png"), Buffer.from(sheetShot.data, "base64"));

    client.close();
    console.log(`\n截图：${OUT}/行内引用.png、${OUT}/材料详情.png`);
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
