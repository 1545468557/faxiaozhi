/**
 * 类案研究离线端到端验收（阶段 3-2 起）
 *
 * 真实点一遍完整闭环，**零成本**：
 *   上传 2 份案例材料 → 本人已核验 → 开始研究 → 候选池 → 确认样本（人工门）→
 *   对比矩阵 → 带引用的结论 → 门禁报告 → 导出 Word → 下载文件校验
 *
 * 前提：后端处于**离线模式**（`MODEL_PROVIDER=stub`），且 `PKULAW_URL_*` 指向不可达地址。
 * 这样「材料为主」策略会让本次检索调用为 0（材料 ≥ 最小样本量时不调法宝），
 * 模型走 stub：它的引用取自材料原文首句，因此能真实跑出「引用通过」的链路。
 *
 * 用法：
 *   node scripts/e2e-offline-research.mjs --base http://localhost:5174 \
 *     --out ../../../docs/evidence/法小智agent开发文档/阶段3-2/screenshots
 */

import { spawn } from "node:child_process";
import { mkdir, rm, readdir, stat, mkdtemp } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import os from "node:os";

const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const arg = (name, fallback) => {
  const index = process.argv.indexOf(`--${name}`);
  return index === -1 ? fallback : process.argv[index + 1];
};
const BASE = arg("base", "http://localhost:5174");
const OUT = path.resolve(arg("out", "./e2e-shots"));
const PORT = Number(arg("port", "9355"));
const DOWNLOADS = "/tmp/fzx-e2e-downloads";
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

const CASE_A = `民事判决书
（2023）苏01民终1234号
本院认为，承租人提前退租后，押金是否返还应当结合合同约定、实际损失与交接情况综合认定。
出租人主张押金全部不予返还，但未提供实际损失的证据，该项主张缺乏事实依据。
房屋已完成交接，承租人不存在损坏房屋设施的情形。
判决如下：一、出租人于本判决生效之日起十日内返还押金六千元；二、驳回出租人其他请求。`;

const CASE_B = `民事判决书
（2024）苏05民终5678号
本院认为，承租人提前退租确实构成违约，但约定的违约责任应当以实际损失为基础予以衡量。
出租人已在一个月内将房屋重新出租，空置损失有限，合同约定的三个月租金违约金明显过高。
当事人主张约定违约金过高的，人民法院可以根据实际损失予以适当减少。
判决如下：出租人返还押金中的四千元，其余部分不予支持。`;

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
    const waitFor = async (expression, timeoutMs = 30000, label = expression.slice(0, 60)) => {
      const deadline = Date.now() + timeoutMs;
      for (;;) {
        if (await evaluate(expression)) return true;
        if (Date.now() > deadline) throw new Error(`等待超时：${label}`);
        await sleep(400);
      }
    };
    const shoot = async name => {
      const shot = await client.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
      const { writeFile } = await import("node:fs/promises");
      await writeFile(path.join(OUT, `${name}.png`), Buffer.from(shot.data, "base64"));
      console.log(`  📷 ${name}.png`);
    };

    // ---------------- 1. 打开研究页，确认后端连接 ----------------
    await client.send("Page.navigate", { url: `${BASE}/research` });
    await waitFor('!!document.querySelector(".fzx-status")', 30000, "后端状态条");
    const status = await evaluate('document.querySelector(".fzx-status")?.textContent?.trim()');
    check("后端状态条可见", Boolean(status), status?.slice(0, 90));
    check("离线模式被显式标注", /离线模式|离线演示/.test(status ?? ""));

    // ---------------- 2. 说清要查什么 + 上传两份案例材料（新界面：第一屏）----------------
    await waitFor('!!document.querySelector(".ct-input")', 30000, "议题输入框");
    await evaluate(`(() => {
      const box = document.querySelector(".ct-input");
      const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
      setter.call(box, "房东把房子卖了，新房东让我提前搬走，押金也不退，法院一般怎么判？");
      box.dispatchEvent(new Event("input", { bubbles: true }));
      return true;
    })()`);
    await waitFor(`(() => { const b = [...document.querySelectorAll("button")].find(x => x.textContent.includes("开始找相似案例")); return b && !b.disabled; })()`, 15000, "开始按钮可用");
    check("写清要查什么之后，「开始找相似案例」可用", true);

    await evaluate(`(() => { [...document.querySelectorAll(".ct-more summary")].find(t => t.textContent.includes("有材料"))?.click(); return true; })()`);
    await waitFor('!!document.querySelector(".ct-drop input[type=file]")', 15000, "上传控件");
    await evaluate(`(() => {
      const input = document.querySelector(".ct-drop input[type=file]");
      const dt = new DataTransfer();
      dt.items.add(new File([${JSON.stringify(CASE_A)}], "样例判决书A.txt", { type: "text/plain" }));
      dt.items.add(new File([${JSON.stringify(CASE_B)}], "样例判决书B.txt", { type: "text/plain" }));
      input.files = dt.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    })()`);
    await waitFor('document.querySelectorAll(".ct-file").length >= 2', 90000, "材料收录");
    check("两份材料被收录（经代理 multipart）", true);

    // ---------------- 3. 本人已核验（未核验不得被引用）----------------
    await waitFor('[...document.querySelectorAll("button")].filter(b => b.textContent.trim() === "本人已核验").length >= 2', 20000, "核验按钮");
    const beforeVerify = await evaluate('[...document.querySelectorAll("button")].filter(b => b.textContent.trim() === "本人已核验").length');
    check("未核验材料给出「本人已核验」按钮", beforeVerify >= 2, `${beforeVerify} 个`);
    await evaluate(`(() => {
      [...document.querySelectorAll("button")].filter(b => b.textContent.trim() === "本人已核验").forEach(b => b.click());
      return true;
    })()`);
    await waitFor('document.querySelectorAll(".ct-badge[data-tone=ok]").length >= 2', 40000, "核验完成");
    check("点击后材料变为已核验", true);

    // ---------------- 4. 开始找相似案例 ----------------
    await evaluate(`(() => {
      const button = [...document.querySelectorAll("button")].find(b => b.textContent.includes("开始找相似案例"));
      button.click();
      return true;
    })()`);

    // ---------------- 5. 人工门：候选池 + 确认样本 ----------------
    await waitFor('!!document.querySelector(".ct-cands input[type=checkbox]")', 180000, "候选池");
    const poolCount = await evaluate('document.querySelectorAll(".ct-cands input[type=checkbox]").length');
    check("候选池出现（材料为主，未调用法宝）", poolCount >= 2, `${poolCount} 篇`);
    const primaryCheck = await evaluate(`(async () => {
      const sid = sessionStorage.getItem("fzx:sid:research");
      const res = await fetch("/api/bff/session/" + sid + "/state");
      const data = await res.json();
      return { primary: Boolean(data.supplement?.material_primary), shown: /材料为主/.test(document.body.innerText) };
    })()`);
    check(
      "「材料为主」的标注与后端口径一致（不冒充检索结果，也不漏标）",
      primaryCheck.primary === primaryCheck.shown,
      JSON.stringify(primaryCheck),
    );

    const gateDisabled = await evaluate(`(() => {
      const button = [...document.querySelectorAll("button")].find(b => b.textContent.includes("就对比"));
      return button ? button.disabled : null;
    })()`);
    check("没挑够案例时「就对比这几篇」被禁用（人工门真的挡住）", gateDisabled === true, String(gateDisabled));
    const gateReason = await evaluate('document.body.innerText.includes("至少") || document.body.innerText.includes("先挑")');
    check("禁用原因写在界面上", Boolean(gateReason));
    await shoot("01-确认样本门-未勾选时禁用");

    await evaluate(`(() => {
      const button = [...document.querySelectorAll("button")].find(b => b.textContent.trim() === "全选");
      button.click();
      return true;
    })()`);
    await sleep(300);
    const gateEnabled = await evaluate(`(() => {
      const button = [...document.querySelectorAll("button")].find(b => b.textContent.includes("就对比"));
      return button ? !button.disabled : null;
    })()`);
    check("挑够之后按钮可用", gateEnabled === true, String(gateEnabled));
    await shoot("02-确认样本门-已勾选");

    await evaluate(`(() => {
      const button = [...document.querySelectorAll("button")].find(b => b.textContent.includes("就对比"));
      button.click();
      return true;
    })()`);

    // ---------------- 6. 矩阵 / 结论 / 门禁 ----------------
    await waitFor('document.querySelectorAll(".fzx-table tbody tr").length > 0', 120000, "对比矩阵");
    const matrixRows = await evaluate('document.querySelectorAll(".fzx-table tbody tr").length');
    const matrixHasSource = await evaluate('[...document.querySelectorAll(".fzx-table th")].some(th => th.textContent === "来源")');
    check("对比矩阵生成", matrixRows >= 2, `${matrixRows} 行`);
    check("矩阵含「来源」列（红线 1）", Boolean(matrixHasSource));

    const distribution = await evaluate('[...document.querySelectorAll(".fzx-note")].map(n => n.textContent).find(t => t.includes("观点分布")) ?? ""');
    check("观点分布带分母声明", /本次确认样本\s*\d+\s*篇中/.test(distribution), distribution.slice(0, 60));

    await waitFor('!!document.querySelector(".fzx-conclusions") || !!document.querySelector(".fzx-empty")', 120000, "结论区");
    const conclusionCount = await evaluate('document.querySelectorAll(".fzx-conclusion").length');
    const citeCount = await evaluate('document.querySelectorAll(".fzx-cite").length');
    check("综合结论已生成", conclusionCount >= 1, `${conclusionCount} 条`);
    check("每条结论都能点开引用", citeCount >= conclusionCount, `${citeCount} 个引用`);

    await evaluate(`(() => {
      // 细节默认折叠：先展开，再点「核验报告」页签（页签内容是后端真实数据）
      [...document.querySelectorAll(".ct-more summary")].find(t => t.textContent.includes("想看细节"))?.click();
      return true;
    })()`);
    await sleep(400);
    const tabClicked = await evaluate(`(() => {
      const tab = [...document.querySelectorAll(".fzx-tab-nav button")].find(b => b.textContent.includes("核验报告") || b.textContent.includes("门禁报告"));
      if (!tab) return "找不到核验报告页签";
      tab.click();
      return "ok";
    })()`);
    if (tabClicked !== "ok") throw new Error(tabClicked);
    await sleep(400);
    const gateBody = await evaluate('document.querySelector(".fzx-tab-body")?.textContent ?? ""');
    check("门禁报告显示通过/拦截/覆盖率", /通过引用\s*\d+\s*条/.test(gateBody), gateBody.slice(0, 80));
    await shoot("03-门禁报告与依据缺口");

    // ---------------- 7. 红线 4：被拦下的结论不进结论区 ----------------
    const degraded = await evaluate(`(() => {
      const texts = [...document.querySelectorAll(".fzx-conclusion-text")].map(n => n.textContent.trim());
      const gaps = document.querySelector(".fzx-tab-body")?.textContent ?? "";
      const shown = texts.length;
      const rejectedShown = texts.filter(t => gaps.includes(t) && /未通过核验/.test(gaps)).length;
      return { shown, rejectedShown };
    })()`);
    check("结论区的结论没有被降级的那些", degraded.rejectedShown === 0, JSON.stringify(degraded));

    // ---------------- 8. 导出 Word（真实下载）----------------
    await evaluate(`(() => {
      const tab = [...document.querySelectorAll(".fzx-tab-nav button")].find(b => b.textContent.includes("依据区"));
      tab.click();
      return true;
    })()`);
    await sleep(300);
    const exportButton = await evaluate(`(() => {
      const button = [...document.querySelectorAll("button")].find(b => b.textContent.includes("导出报告"));
      return {
        disabled: button ? button.disabled : null,
        blockers: [...document.querySelectorAll(".ct-blocked, .fzx-note")].map(n => n.textContent).filter(t => t.includes("导出不了") || t.includes("不能导出")),
      };
    })()`);
    check("有「导出报告」按钮", exportButton.disabled !== null, JSON.stringify(exportButton));
    await shoot("04-研究结果与导出");

    if (exportButton.disabled) {
      check("导出被拦并且原因原文列出（门禁未通过时）", exportButton.blockers.length > 0, JSON.stringify(exportButton.blockers).slice(0, 120));
    } else {
      await evaluate(`(() => {
        const button = [...document.querySelectorAll("button")].find(b => b.textContent.includes("导出报告"));
        button.click();
        return true;
      })()`);
      await sleep(4000);
      const files = (await readdir(DOWNLOADS)).filter(name => name.endsWith(".docx"));
      let size = 0;
      if (files.length) size = (await stat(path.join(DOWNLOADS, files[0]))).size;
      check("导出 Word 成功并真的下载（门禁通过时）", files.length > 0 && size > 5000, `${files[0] ?? "无文件"} / ${size} 字节`);
      await shoot("05-导出完成");
    }

    // ---------------- 9. 手机宽度 ----------------
    await client.send("Emulation.setDeviceMetricsOverride", { width: 390, height: 900, deviceScaleFactor: 1, mobile: true });
    await sleep(600);
    const overflow = await evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth");
    check("390px 无横向溢出", overflow === 0, `${overflow}px`);
    await shoot("06-手机390-研究结果");

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
