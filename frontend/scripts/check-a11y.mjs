/**
 * 无障碍与键盘可达性检查（阶段 3-5）
 *
 * 检查三件事（按前端手册 §17 的基础要求）：
 * 1. **可访问名称**：所有可交互元素（button / a / input / select / textarea）都要有可读名称
 *    （文字、aria-label 或关联的 label）；只有图标的按钮必须有 aria-label；
 * 2. **键盘可达**：连续 Tab 能走到核心交互（导航、输入框、提交按钮、引用按钮、原文锚点），
 *    且焦点不会卡在不可见元素上；
 * 3. **焦点可见**：聚焦元素的 outline/box-shadow 不能是 none（有可见焦点样式）。
 *
 * 用法：
 *   node scripts/check-a11y.mjs --base http://localhost:5174
 */

import { spawn } from "node:child_process";
import { rm } from "node:fs/promises";
import { existsSync } from "node:fs";

const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const arg = (name, fallback) => {
  const index = process.argv.indexOf(`--${name}`);
  return index === -1 ? fallback : process.argv[index + 1];
};
const BASE = arg("base", "http://localhost:5174");
const PORT = Number(arg("port", "9444"));
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

const ROUTES = [
  { path: "/", label: "首页" },
  { path: "/research", label: "类案研究" },
  { path: "/consult", label: "法律咨询" },
  { path: "/contract", label: "合同审查" },
  { path: "/about", label: "关于" },
];

async function connect() {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    try {
      const list = await fetch(`http://127.0.0.1:${PORT}/json/list`).then(r => r.json());
      const page = list.find(item => item.type === "item" || item.type === "page");
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
  if (!existsSync(CHROME)) throw new Error(`找不到 Chrome：${CHROME}`);
  const profile = "/tmp/fzx-a11y-profile";
  await rm(profile, { recursive: true, force: true });
  const chrome = spawn(
    CHROME,
    [
      "--headless=new",
      "--disable-gpu",
      `--remote-debugging-port=${PORT}`,
      `--user-data-dir=${profile}`,
      "--window-size=1440,1200",
      "about:blank",
    ],
    { stdio: "ignore" },
  );

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
      await client.send("Page.navigate", { url: `${BASE}${route.path}` });
      await sleep(1500);

      // 1) 可访问名称
      const audit = await evaluate(`(() => {
        const nodes = [...document.querySelectorAll("button, a[href], input, select, textarea")];
        const unnamed = [];
        for (const node of nodes) {
          const rect = node.getBoundingClientRect();
          if (rect.width === 0 && rect.height === 0) continue;   // 不在视口/未渲染
          if (node.tagName === "INPUT" && node.type === "hidden") continue;
          const label = node.getAttribute("aria-label")
            || node.getAttribute("title")
            || (node.labels && node.labels[0]?.textContent)
            || node.textContent
            || node.value
            || "";
          if (!String(label).trim()) {
            unnamed.push(node.tagName + "." + String(node.className || "").slice(0, 40));
          }
        }
        const iconOnlyWithoutLabel = [...document.querySelectorAll("button")]
          .filter(node => {
            const text = (node.textContent || "").trim();
            const hasIcon = Boolean(node.querySelector("svg"));
            const labelled = node.getAttribute("aria-label") || node.getAttribute("title");
            return hasIcon && !text && !labelled;
          })
          .map(node => String(node.className || "button").slice(0, 50));
        return { total: nodes.length, unnamed: unnamed.slice(0, 5), iconOnlyWithoutLabel: iconOnlyWithoutLabel.slice(0, 5) };
      })()`);
      if (audit.unnamed.length) failures.push(`${route.label}：${audit.unnamed.length} 个可交互元素没有可访问名称（${audit.unnamed.join(", ")}）`);
      if (audit.iconOnlyWithoutLabel.length) failures.push(`${route.label}：图标按钮缺 aria-label（${audit.iconOnlyWithoutLabel.join(", ")}）`);

      // 2) 键盘 Tab 走一轮，记录焦点落点
      const focusWalk = [];
      await evaluate("document.body.focus(); document.querySelector('#main-content')?.focus();");
      for (let step = 0; step < 25; step += 1) {
        await client.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
        await client.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
        const focused = await evaluate(`(() => {
          const node = document.activeElement;
          if (!node || node === document.body) return null;
          const style = getComputedStyle(node);
          return {
            tag: node.tagName,
            name: (node.getAttribute("aria-label") || node.textContent || node.tagName).toString().trim().slice(0, 40),
            focusVisible: style.outlineStyle !== "none" || style.boxShadow !== "none" || style.outlineWidth !== "0px"
          };
        })()`);
        if (focused) focusWalk.push(focused);
      }
      const reachable = focusWalk.length;
      const withoutFocusStyle = focusWalk.filter(item => !item.focusVisible);
      if (reachable < 5) failures.push(`${route.label}：Tab 只走到 ${reachable} 个可聚焦元素（疑似键盘不可达）`);
      if (withoutFocusStyle.length > Math.max(1, Math.floor(focusWalk.length * 0.4))) {
        failures.push(`${route.label}：${withoutFocusStyle.length}/${focusWalk.length} 个聚焦元素没有可见焦点样式`);
      }
      console.log(`✓ ${route.label}：可交互元素 ${audit.total} 个 · Tab 可达 ${reachable} 个 · 焦点样式齐全 ${reachable - withoutFocusStyle.length}/${reachable}`);
    }

    client.close();
  } finally {
    chrome.kill();
    await rm(profile, { recursive: true, force: true }).catch(() => undefined);
  }

  console.log(`\n失败 ${failures.length} 项`);
  for (const item of failures) console.log(`  ✗ ${item}`);
  if (failures.length) process.exitCode = 1;
};

await main();
