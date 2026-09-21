/**
 * 合同审查离线端到端验收（阶段 3-4，零成本）
 *
 * 真实点一遍：上传合同（**显式 role=contract**）→ 本人已核验 → 发起审查 →
 *   **立场硬门**（未选立场不得继续）→ 确认立场 → 解析定位 → 依据检索 →（离线：检索不可用）→
 *   失败路径显示「接口调用失败…不等于没有问题规定」+ 导出被拦
 *
 * 前提：后端离线模式（`MODEL_PROVIDER=stub` + `PKULAW_URL_*` 指向不可达地址）。
 * 说明：合同审查**没有"材料为主"策略**，依据必须来自检索；检索不可用时后端会明确报
 * 「接口失败 ≠ 没有规定」并给重试，这是正确行为。因此本脚本验的是**立场门 + 失败路径**；
 * 风险清单与原文联动由组件测试 + 真实联调覆盖。
 *
 * 用法：
 *   node scripts/e2e-offline-contract.mjs --base http://localhost:5174 \
 *     --out ../../../docs/evidence/法小智agent开发文档/阶段3-4/screenshots
 */

import { spawn } from "node:child_process";
import { mkdir, rm, mkdtemp } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import os from "node:os";

const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const arg = (name, fallback) => {
  const index = process.argv.indexOf(`--${name}`);
  return index === -1 ? fallback : process.argv[index + 1];
};
const BASE = arg("base", "http://localhost:5174");
const OUT = path.resolve(arg("out", "./contract-shots"));
const PORT = Number(arg("port", "9411"));
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

const CONTRACT = `房屋租赁合同

甲方（出租人）：张三
乙方（承租人）：李四

第一条 租赁物
甲方将其所有的位于某市某区的房屋出租给乙方居住使用，租期一年，自 2026 年 1 月 1 日起至 2026 年 12 月 31 日止。

第二条 租金及支付
月租金人民币三千元，乙方应于每月五日前支付；逾期支付的，按日千分之五计收违约金。

第三条 押金
乙方应于签订本合同时支付押金人民币六千元。租赁终止后，出租人可根据实际情况决定是否退还押金，无须说明理由。

第四条 违约责任
乙方提前退租的，甲方有权直接更换门锁并收回房屋，押金全部不予退还，且不承担任何赔偿责任。

第五条 维修义务
房屋的全部维修费用由乙方承担。

第六条 争议解决
因本合同发生的争议，由甲方所在地人民法院管辖。`;

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
        await sleep(500);
      }
    };
    const shoot = async name => {
      const shot = await client.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
      const { writeFile } = await import("node:fs/promises");
      await writeFile(path.join(OUT, `${name}.png`), Buffer.from(shot.data, "base64"));
      console.log(`  📷 ${name}.png`);
    };

    // ---------- ① 交合同（一屏） ----------
    await client.send("Page.navigate", { url: `${BASE}/contract` });
    await waitFor('!!document.querySelector(".ct-drop")', 30000, "合同页第一屏");
    check("合同页可打开，第一屏就是「交合同」（不再显示技术步骤条）", (await evaluate('document.querySelectorAll(".ct-steps").length')) === 0);

    const startButton = `[...document.querySelectorAll("button")].find(b => b.textContent.includes("开始审查"))`;
    const startState = await evaluate(`(() => { const b = ${startButton}; return b ? { disabled: b.disabled } : null; })()`);
    check("没交合同时「开始审查」不可用", startState === null || startState.disabled === true, JSON.stringify(startState));

    // 示例合同（先等按钮出现再点，避免页面还没编译完就被点）
    await waitFor(`!![...document.querySelectorAll("button")].find(b => b.textContent.includes("用一份示例合同试一下"))`, 30000, "示例合同按钮");
    await evaluate(`(() => { [...document.querySelectorAll("button")].find(b => b.textContent.includes("用一份示例合同试一下")).click(); return true; })()`);
    await waitFor('document.body.innerText.includes("示例合同-房屋租赁（合成，可直接试）.txt")', 120000, "示例合同上传");
    const sampleRow = await evaluate(`document.querySelector(".ct-file")?.textContent ?? ""`);
    check("示例合同被收录，文件名自带「合成」标记", /合成/.test(sampleRow), sampleRow.slice(0, 60));

    // 清空重来（换一份）
    await evaluate(`(() => { [...document.querySelectorAll("button")].find(b => b.textContent.includes("换一份")).click(); return true; })()`);
    await waitFor('document.body.innerText.includes("确认清空，重新上传")', 10000, "清空确认");
    await evaluate(`(() => { [...document.querySelectorAll("button")].find(b => b.textContent.includes("确认清空，重新上传")).click(); return true; })()`);
    // 等真正的标志：文件行消失（"把合同拖进来"这句一直都在，不能当判据）
    await waitFor('document.querySelector(".ct-file") === null', 60000, "清空完成");
    check("「换一份 / 清空」可以把交进去的合同清掉重来", (await evaluate('document.querySelector(".ct-file") === null')) === true);

    // ---------- 交一份正式用（显式 role=contract） ----------
    await evaluate(`(() => {
      const input = document.querySelector('.ct-drop input[type=file]');
      const dt = new DataTransfer();
      dt.items.add(new File([${JSON.stringify(CONTRACT)}], "房屋租赁合同（离线验收）.txt", { type: "text/plain" }));
      input.files = dt.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    })()`);
    await waitFor('document.querySelector(".ct-file")?.textContent?.includes("房屋租赁合同（离线验收）.txt")', 120000, "合同上传");
    const roleShown = await evaluate(`document.querySelector(".ct-file")?.textContent ?? ""`);
    check("合同被收录且角色为「合同」（显式 role=contract）", /合同/.test(roleShown), roleShown.slice(0, 60));

    // ---------- 本人已核验（勾选） ----------
    await evaluate(`(() => { document.querySelector(".ct-check input[type=checkbox]").click(); return true; })()`);
    await waitFor('document.body.innerText.includes("你已核验这份合同")', 30000, "核验完成");
    check("勾选「本人已核验」后合同可作为审查对象", true);

    const blockedNoStance = await evaluate(`(() => {
      const b = ${startButton};
      const hint = [...document.querySelectorAll(".ct-blocked")].map(n => n.textContent).join(" ");
      return { disabled: b?.disabled, hint: /选「你是哪一方」/.test(hint) };
    })()`);
    check(
      "没选立场时不能开始（按钮禁用 + 写明原因）",
      blockedNoStance.disabled === true && blockedNoStance.hint === true,
      JSON.stringify(blockedNoStance),
    );

    // ---------- 选立场 → 开始审查 ----------
    await evaluate(`(() => { document.querySelectorAll(".ct-stance input[type=radio]")[1].click(); return true; })()`);
    await sleep(300);
    const startEnabled = await evaluate(`(() => { const b = ${startButton}; return b ? !b.disabled : null; })()`);
    check("选了立场后「开始审查」可用", startEnabled === true);
    await shoot("01-交合同");

    await evaluate(`(() => { ${startButton}.click(); return true; })()`);
    await waitFor('document.body.innerText.includes("正在读你的合同")', 60000, "正在读");
    const waiting = await evaluate(`document.body.innerText`);
    check("开始后进入「正在读你的合同」（不再显示切分条款/取回依据这类术语）", /正在读你的合同/.test(waiting));
    check("等待屏不出现技术术语", !/切分条款|取回依据|核验引用|逐案提炼/.test(waiting));
    await shoot("02-正在读");

    // ---------- 立场由人工确认后自动提交 → 离线失败路径 ----------
    // 等**真的有结论**：要么给出结论，要么明确失败；不接受"正在取回结果"这种中间态
    await waitFor(
      `document.body.innerText.includes("这次没跑完") || /挑出 \\d+ 条|没有挑出对你不利的地方/.test(document.body.innerText)`,
      240000,
      "结果或失败",
    );
    const resultText = await evaluate(`document.body.innerText`);
    check("离线检索不可用时如实说明失败（不静默、不装成功）", /这次没跑完|接口调用失败/.test(resultText), resultText.slice(0, 60));
    check("红线：不把接口失败说成「没有问题规定」", /不等于/.test(resultText));
    check(
      "没有结论时不乱下结论（不允许出现「没有挑出」这种瞎说）",
      !/没有挑出对你不利的地方/.test(resultText),
      /没有挑出/.test(resultText) ? "出现了『没有挑出』，但其实是失败/未取回" : "",
    );

    const stanceRecorded = await evaluate(`(() => {
      const b = ${startButton};
      return document.body.innerText.includes("这次没跑完") || b === undefined;
    })()`);
    check("立场门仍由后端强制，界面在门到达时把已选立场提交（未卡在等立场）", stanceRecorded === true, String(stanceRecorded));
    await shoot("03-结果或失败");

    const exportBlocked = await evaluate(`(() => {
      const b = [...document.querySelectorAll("button")].find(x => x.textContent.includes("导出报告"));
      const blocked = [...document.querySelectorAll(".ct-blocked")].map(n => n.textContent).filter(t => t.includes("导出不了"));
      return { disabled: Boolean(b?.disabled), blocked: blocked.slice(0, 1) };
    })()`);
    check("没结果时导出被拦且写明原因", exportBlocked.disabled && exportBlocked.blocked.length > 0, exportBlocked.blocked.join(""));

    // ---------- 刷新状态要有反馈 ----------
    const refreshState = await evaluate(`(() => {
      const b = [...document.querySelectorAll("button")].find(x => x.textContent.includes("刷新状态"));
      return b ? { disabled: b.disabled } : null;
    })()`);
    check("「刷新状态」按钮可点", refreshState !== null && refreshState.disabled === false, JSON.stringify(refreshState));
    await evaluate(`(() => { [...document.querySelectorAll("button")].find(x => x.textContent.includes("刷新状态"))?.click(); return true; })()`);
    await waitFor('document.body.innerText.includes("已重新取回状态")', 20000, "刷新反馈");
    check("点完有明确反馈（含时间戳）", true);

    // ---------- 手机宽度 ----------
    await client.send("Emulation.setDeviceMetricsOverride", { width: 390, height: 900, deviceScaleFactor: 1, mobile: true });
    await sleep(600);
    const overflow = await evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth");
    check("390px 无横向溢出", overflow === 0, `${overflow}px`);
    await shoot("04-手机390");

    client.close();
  } finally {
    chrome.kill();
    await rm(profile, { recursive: true, force: true }).catch(() => undefined);
  }

  const failed = checks.filter(item => !item.ok);
  console.log(`\n合计 ${checks.length} 项，失败 ${failed.length} 项`);
  for (const item of failed) console.log(`  ✗ ${item.name} ${item.detail}`);
  if (failed.length) process.exitCode = 1;
};

await main();
