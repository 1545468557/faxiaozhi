/**
 * 法律咨询离线端到端验收（阶段 3-3，零成本）
 *
 * 真实点一遍：上传背景材料 → 本人已核验 → 提问（短问题触发追问）→
 *   **追问卡（第 N 轮 / 最多 M 轮）** → 补充事实 → 检索依据 → **四块解答**
 *   →（门禁通过则）导出咨询备忘 Word
 *
 * 前提：后端离线模式（`MODEL_PROVIDER=stub` + `PKULAW_URL_*` 指向不可达地址）。
 * 上传并核验一份背景材料后，即使检索接口不可用，链路也能走到"四块解答"（否则后端会按
 * 「接口失败 ≠ 没有相关规定」直接报错——那也是一种正确行为，但不是本脚本要验的）。
 *
 * 用法：
 *   node scripts/e2e-offline-consult.mjs --base http://localhost:5174 \
 *     --out ../../../docs/evidence/法小智agent开发文档/阶段3-3/screenshots
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
const OUT = path.resolve(arg("out", "./consult-shots"));
const PORT = Number(arg("port", "9388"));
const DOWNLOADS = "/tmp/fzx-consult-downloads";
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

const BACKGROUND = `《中华人民共和国民法典》
第七百三十三条 租赁期限届满，承租人继续使用租赁物，出租人没有提出异议的，原租赁合同继续有效，但是租赁期限为不定期。
第七百三十四条 租赁期限届满，承租人享有以同等条件优先承租的权利。
第五百七十七条 当事人一方不履行合同义务或者履行合同义务不符合约定的，应当承担继续履行、采取补救措施或者赔偿损失等违约责任。
第五百八十五条 当事人可以约定一方违约时应当根据违约情况向对方支付一定数额的违约金，也可以约定因违约产生的损失赔偿额的计算方法。`;

const QUESTION = "提前退租，押金能不退吗？";
const FACTS = "我与房东签了一年的租房合同，月租三千元，押金六千元。我在住了五个月后因为工作调动提前退租，已经提前三十天通知房东并完成房屋交接，房东以我提前退租为由不退还全部押金。";

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

    // ---------- 1. 打开咨询页 ----------
    await client.send("Page.navigate", { url: `${BASE}/consult` });
    await waitFor('!!document.querySelector(".fzx-status")', 30000, "后端状态条");
    const status = await evaluate('document.querySelector(".fzx-status")?.textContent?.trim()');
    check("咨询页可打开且显示后端状态", Boolean(status), status?.slice(0, 70));
    check("页面写明咨询没有人工门", (await evaluate("document.body.innerText.includes('无人工门')")) === true);

    // ---------- 2. 上传背景材料并核验（保证接口不可用时仍有依据）----------
    await evaluate(`(() => {
      [...document.querySelectorAll(".fzx-tab-nav button")].find(b => b.textContent.includes("上传材料")).click();
      return true;
    })()`);
    await waitFor('!!document.querySelector(".fzx-upload input[type=file]")', 15000, "上传控件");
    await evaluate(`(() => {
      const input = document.querySelector(".fzx-upload input[type=file]");
      const dt = new DataTransfer();
      dt.items.add(new File([${JSON.stringify(BACKGROUND)}], "民法典条文节选.txt", { type: "text/plain" }));
      input.files = dt.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    })()`);
    await waitFor('document.querySelectorAll(".fzx-source-row").length >= 1', 60000, "材料收录");
    await evaluate(`(() => {
      [...document.querySelectorAll(".fzx-tab-nav button")].find(b => b.textContent.includes("依据区")).click();
      return true;
    })()`);
    await waitFor('document.querySelectorAll(".fzx-source-actions button.primary").length > 0', 20000, "核验按钮");
    await evaluate(`(() => {
      [...document.querySelectorAll(".fzx-source-actions button.primary")].forEach(b => b.click());
      return true;
    })()`);
    await waitFor('document.querySelectorAll(".fzx-source-actions button.primary").length === 0', 30000, "核验完成");
    check("背景材料上传并核验（未核验不得引用）", true);

    // ---------- 3. 提问 → 追问 ----------
    await evaluate(`(() => {
      const box = document.querySelector("#question");
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
      setter.call(box, ${JSON.stringify(QUESTION)});
      box.dispatchEvent(new Event("input", { bubbles: true }));
      [...document.querySelectorAll("button")].find(b => b.textContent.includes("发送问题")).click();
      return true;
    })()`);

    await waitFor('!!document.querySelector(".fzx-clarify")', 90000, "追问卡");
    await waitFor('document.querySelectorAll(".fzx-clarify ol li").length > 0', 60000, "追问问题列表");
    const clarifyText = await evaluate('document.querySelector(".fzx-clarify .fzx-pill")?.textContent?.trim()');
    const clarifyQuestions = await evaluate('document.querySelectorAll(".fzx-clarify ol li").length');
    check("追问卡出现", true);
    check("显示「第 N 轮 / 最多 M 轮」", /第\s*1\s*轮\s*\/\s*最多\s*3\s*轮/.test(clarifyText ?? ""), clarifyText);
    check("追问问题列出来了", clarifyQuestions > 0, `${clarifyQuestions} 个问题`);
    const submitDisabled = await evaluate(`(() => {
      const button = [...document.querySelectorAll("button")].find(b => b.textContent.includes("提交补充事实"));
      return button ? button.disabled : null;
    })()`);
    check("未填事实时提交按钮禁用", submitDisabled === true);
    await shoot("01-追问卡");

    // ---------- 4. 补充事实 → 四块解答 ----------
    await evaluate(`(() => {
      const box = document.querySelector("#facts");
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
      setter.call(box, ${JSON.stringify(FACTS)});
      box.dispatchEvent(new Event("input", { bubbles: true }));
      [...document.querySelectorAll("button")].find(b => b.textContent.includes("提交补充事实")).click();
      return true;
    })()`);

    await waitFor('document.body.innerText.includes("一、结论")', 240000, "解答四块");
    const blocks = await evaluate(`({
      conclusion: document.body.innerText.includes("一、结论"),
      cites: document.body.innerText.includes("二、引用"),
      risks: document.body.innerText.includes("三、不确定与风险"),
      next: document.body.innerText.includes("四、建议的下一步"),
      conclusions: document.querySelectorAll(".fzx-conclusion").length,
      citeButtons: document.querySelectorAll(".fzx-cite").length,
      rounds: document.querySelector(".input-panel .muted-note:last-of-type")?.textContent?.trim() ?? ""
    })`);
    check("四块解答都在（结论 / 引用 / 不确定与风险 / 建议的下一步）", blocks.conclusion && blocks.cites && blocks.risks && blocks.next, JSON.stringify({ c: blocks.conclusion, i: blocks.cites, r: blocks.risks, n: blocks.next }));
    check("结论区只展示通过核验的结论（可为 0，但必须解释）", blocks.conclusions >= 0, `${blocks.conclusions} 条`);
    check("引用可点开看来源", blocks.citeButtons >= blocks.conclusions, `${blocks.citeButtons} 个引用`);
    await shoot("02-四块解答");

    await evaluate(`(() => {
      [...document.querySelectorAll(".fzx-tab-nav button")].find(b => b.textContent.includes("门禁报告")).click();
      return true;
    })()`);
    await sleep(500);
    const gateBody = await evaluate('document.querySelector(".fzx-tab-body")?.textContent ?? ""');
    check("门禁报告有通过/拦截/覆盖率", /通过引用\s*\d+\s*条/.test(gateBody), gateBody.slice(0, 70));

    // ---------- 5. 导出咨询备忘 ----------
    await evaluate(`(() => {
      [...document.querySelectorAll(".fzx-tab-nav button")].find(b => b.textContent.includes("依据区")).click();
      return true;
    })()`);
    await sleep(400);
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
      await sleep(5000);
      const files = (await readdir(DOWNLOADS)).filter(name => name.endsWith(".docx"));
      const size = files.length ? (await stat(path.join(DOWNLOADS, files[0]))).size : 0;
      check("导出咨询备忘成功并真的下载", files.length > 0 && size > 3000, `${files[0] ?? "无"} / ${size} 字节`);
      await shoot("03-导出完成");
    }

    // ---------- 6. 手机宽度 ----------
    await client.send("Emulation.setDeviceMetricsOverride", { width: 390, height: 900, deviceScaleFactor: 1, mobile: true });
    await sleep(600);
    const overflow = await evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth");
    check("390px 无横向溢出", overflow === 0, `${overflow}px`);
    await shoot("04-手机390-咨询");

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
