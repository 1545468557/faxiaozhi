/**
 * 合同审查**真实链路**联调（会花钱：≈¥0.30–0.60 / 次）
 *
 * 真实点一遍：上传合同（role=contract）→ 本人已核验 → 发起审查 → **立场硬门** →
 *   选立场 → 真法宝检索（3 次法条 + 1 次案例）→ 风险分级 → 引用逐字核验 →
 *   **原文 ↔ 风险联动**（点风险 → 原文高亮；点高亮 → 回选风险）→ 导出《合同审查报告》
 *
 * 运行前：后端必须是真实模式（脚本自检，是 stub 直接退出），且产品经理已同意花费。
 * 注意：合同用的是**自写的合成合同**（D4 硬门禁：真实合同/案件材料不得进第三方模型）。
 *
 * 用法：
 *   node scripts/e2e-real-contract.mjs --base http://localhost:5174 \
 *     --out ../../../docs/evidence/法小智agent开发文档/阶段3-4/screenshots-real \
 *     --runs ../runs [--stance party_b]
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
const OUT = path.resolve(arg("out", "./contract-real-shots"));
const RUNS = path.resolve(arg("runs", "./runs"));
const PORT = Number(arg("port", "9422"));
const DOWNLOADS = "/tmp/fzx-contract-real-downloads";
const STANCE_INDEX = Number(arg("stance-index", "1")); // 0=甲方 1=乙方 2=中立
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const PRICE_IN_PER_M = 2.0;
const PRICE_OUT_PER_M = 8.0;
const cost = usage =>
  (Number(usage?.in ?? 0) / 1e6) * PRICE_IN_PER_M + (Number(usage?.out ?? 0) / 1e6) * PRICE_OUT_PER_M;

/** 自写的合成房屋租赁合同（含明显对乙方不利的条款，用于验证风险识别） */
const CONTRACT = `房屋租赁合同

甲方（出租人）：张三
乙方（承租人）：李四

第一条 租赁物
甲方将其所有的位于某市某区某路 1 号的房屋出租给乙方居住使用，建筑面积约 60 平方米。租期一年，自 2026 年 1 月 1 日起至 2026 年 12 月 31 日止。

第二条 租金及支付
月租金人民币三千元，乙方应于每月五日前支付；逾期支付的，每逾期一日按应付租金的千分之五计收违约金。甲方有权在乙方逾期支付后直接更换门锁并收回房屋，且无需另行通知。

第三条 押金
乙方应于签订本合同时支付押金人民币六千元。租赁期限届满或合同解除后，出租人可根据房屋的实际情况自行决定是否退还押金，无须说明理由，亦无须提供任何扣除明细。

第四条 维修义务
房屋及其附属设施的全部维修费用由乙方承担，包括因房屋本身结构缺陷、给排水管道老化以及墙体渗水所产生的维修费用。

第五条 违约责任
乙方提前退租的，甲方有权没收全部押金，并要求乙方另行支付相当于三个月租金的违约金；因甲方原因导致合同无法履行的，甲方仅退还剩余租金，不承担其他赔偿责任。

第六条 免责条款
乙方在租赁期间因房屋内设施故障、漏电、燃气泄漏等造成的人身伤害或财产损失，甲方不承担任何责任。

第七条 争议解决
因本合同发生的争议，双方应友好协商解决；协商不成的，由甲方所在地人民法院管辖。本合同最终解释权归甲方所有。

甲方：张三
乙方：李四
签署日期：2026 年 1 月 1 日`;

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
  console.log(`后端：${bootstrap.model.model_id}（真实模式）· 合成合同（D4：真实合同不进第三方模型）`);

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
      "--window-size=1440,1600",
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
        await sleep(1000);
      }
    };
    const shoot = async name => {
      const shot = await client.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
      const { writeFile } = await import("node:fs/promises");
      await writeFile(path.join(OUT, `${name}.png`), Buffer.from(shot.data, "base64"));
      console.log(`  📷 ${name}.png`);
    };

    // ---------- 上传 + 核验 ----------
    await client.send("Page.navigate", { url: `${BASE}/contract` });
    await waitFor('!!document.querySelector(".fzx-status")', 30000, "后端状态条");
    check("后端为真实模式", !/离线模式/.test(await evaluate('document.querySelector(".fzx-status")?.textContent ?? ""')));

    await evaluate(`(() => {
      const input = document.querySelector('.fzx-upload input[type=file]');
      const dt = new DataTransfer();
      dt.items.add(new File([${JSON.stringify(CONTRACT)}], "房屋租赁合同（合成样例）.txt", { type: "text/plain" }));
      input.files = dt.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    })()`);
    await waitFor(
      `document.querySelector(".fzx-source-row .fzx-badge.ok")?.textContent?.trim() === "已核验" ||
       [...document.querySelectorAll("button")].some(b => b.textContent.trim() === "本人已核验")`,
      60000,
      "合同上传与核验按钮",
    );
    const verifyButton = await evaluate(
      `[...document.querySelectorAll("button")].find(b => b.textContent.trim() === "本人已核验") ? true : false`,
    );
    if (verifyButton) {
      await evaluate(`(() => {
        [...document.querySelectorAll("button")].find(b => b.textContent.trim() === "本人已核验").click();
        return true;
      })()`);
      await waitFor(
        `document.querySelector(".fzx-source-row .fzx-badge.ok")?.textContent?.trim() === "已核验"`,
        60000,
        "核验完成",
      );
    }
    check("合成合同上传并核验通过", true);

    // ---------- 发起审查 → 立场门 ----------
    await evaluate(`(() => {
      [...document.querySelectorAll("button")].find(b => b.textContent.includes("发起合同审查")).click();
      return true;
    })()`);
    await waitFor('!!document.querySelector(".fzx-stance")', 180000, "立场卡");

    const stanceInfo = await evaluate(`(() => {
      const card = document.querySelector(".fzx-stance");
      const radios = [...document.querySelectorAll(".fzx-stance input[type=radio]")];
      const button = [...document.querySelectorAll("button")].find(b => b.textContent.includes("确认立场并开始审查"));
      return {
        text: card?.textContent ?? "",
        options: radios.length,
        disabled: Boolean(button?.disabled)
      };
    })()`);
    check("立场硬门出现且未选时按钮禁用", stanceInfo.disabled === true, `${stanceInfo.options} 个选项`);
    check("立场卡显示识别到的甲乙方名称", /张三|李四/.test(stanceInfo.text), stanceInfo.text.replace(/\s+/g, " ").slice(0, 90));
    await shoot("01-真实立场门-识别到甲乙方");

    await evaluate(`(() => {
      const radios = [...document.querySelectorAll(".fzx-stance input[type=radio]")];
      const target = radios[${STANCE_INDEX}] ?? radios[0];
      target.click();
      const label = target.closest("label")?.textContent?.trim();
      return label;
    })()`).then(label => console.log(`  选择的立场：${label}`));
    await sleep(500);
    await evaluate(`(() => {
      [...document.querySelectorAll("button")].find(b => b.textContent.includes("确认立场并开始审查")).click();
      return true;
    })()`);

    // ---------- 风险结果（真实检索 + 提炼，给足时间）----------
    await waitFor('document.querySelectorAll(".fzx-risk").length > 0', 600000, "风险清单");
    const risks = await evaluate(`(() => {
      const rows = [...document.querySelectorAll(".fzx-risk")];
      return {
        count: rows.length,
        levels: rows.map(r => r.getAttribute("data-level")),
        verified: rows.filter(r => r.getAttribute("data-verified") === "true").length,
        firstIssue: rows[0]?.querySelector(".fzx-risk-issue")?.textContent?.trim().slice(0, 80),
        firstAnchor: rows[0]?.querySelector(".fzx-risk-anchor")?.textContent?.trim().slice(0, 60),
        levelsText: [...document.querySelectorAll(".fzx-risk .fzx-level")].map(n => n.textContent.trim()),
        summary: document.querySelector(".result-toolbar .light-badge")?.textContent?.trim()
      };
    })()`);
    check("真实风险清单生成", risks.count > 0, `${risks.count} 条 · ${risks.summary}`);
    check("每条风险都带等级与类型", risks.levelsText.length === risks.count, risks.levelsText.slice(0, 5).join("/"));
    check("风险带原文摘录", Boolean(risks.firstAnchor), risks.firstAnchor ?? "");
    console.log(`  首条风险：${risks.firstIssue ?? ""}`);
    console.log(`  其中依据通过核验：${risks.verified} 条`);
    await shoot("02-真实风险清单与原文联动");

    // ---------- 原文 ↔ 风险联动 ----------
    const anchors = await evaluate('document.querySelectorAll(".fzx-anchor").length');
    check("原文里出现风险锚点（可高亮）", anchors > 0, `${anchors} 处`);

    // 点第一条风险 → 对应锚点应变成 active
    await evaluate(`(() => {
      document.querySelector(".fzx-risk")?.querySelector(".fzx-risk-head")?.click();
      return true;
    })()`);
    await sleep(800);
    const linked = await evaluate(`(() => {
      const active = document.querySelector(".fzx-anchor[data-active='true']");
      const activeRisk = document.querySelector(".fzx-risk[data-active='true']");
      return {
        anchorActive: Boolean(active),
        anchorText: active?.textContent?.trim().slice(0, 40) ?? "",
        riskActive: Boolean(activeRisk),
        riskId: active?.getAttribute("data-risk") ?? ""
      };
    })()`);
    check("点风险 → 原文对应位置高亮", linked.anchorActive && linked.riskActive, `高亮原文「${linked.anchorText}」`);

    const backLink = await evaluate(`(() => {
      const anchor = document.querySelector(".fzx-anchor[data-active='true']");
      if (!anchor) return null;
      const riskId = anchor.getAttribute("data-risk");
      // 先取消选中，再点原文锚点，验证能否回选风险
      document.querySelectorAll(".fzx-risk-head")[1]?.click();
      anchor.click();
      return riskId;
    })()`);
    await sleep(600);
    const backLinked = await evaluate(`(() => {
      const active = document.querySelector(".fzx-risk[data-active='true']");
      return { active: Boolean(active), hasAnchor: Boolean(active?.querySelector(".fzx-risk-anchor")) };
    })()`);
    check("点原文高亮 → 回选对应风险", Boolean(backLink) && backLinked.active && backLinked.hasAnchor, String(backLink));
    await shoot("03-真实原文联动");

    // ---------- 门禁 + 导出 ----------
    await evaluate(`(() => {
      [...document.querySelectorAll(".fzx-tab-nav button")].find(b => b.textContent.includes("门禁报告")).click();
      return true;
    })()`);
    await sleep(700);
    const gateBody = await evaluate('document.querySelector(".fzx-tab-body")?.textContent ?? ""');
    const coverage = /证据覆盖率\s*(\d+)%/.exec(gateBody)?.[1];
    check("引用逐字核验完成", coverage !== undefined, `覆盖率 ${coverage}%`);
    const verifiedPercent = risks.count ? Math.round((risks.verified / risks.count) * 100) : 0;
    console.log(`  风险依据核验通过率：${verifiedPercent}%（${risks.verified}/${risks.count}）`);

    await evaluate(`(() => {
      [...document.querySelectorAll(".fzx-tab-nav button")].find(b => b.textContent.includes("依据区")).click();
      return true;
    })()`);
    await sleep(500);
    const exportState = await evaluate(`(() => {
      const button = [...document.querySelectorAll("button")].find(b => b.textContent.includes("导出审查报告"));
      const blockers = [...document.querySelectorAll(".fzx-note")].map(n => n.textContent).filter(t => t.includes("不能导出"));
      return { disabled: Boolean(button?.disabled), blockers: blockers.slice(0, 3) };
    })()`);
    if (exportState.disabled) {
      check("导出被拦时原因原文可见", exportState.blockers.length > 0, exportState.blockers.join(" / ").slice(0, 140));
    } else {
      await evaluate(`(() => {
        [...document.querySelectorAll("button")].find(b => b.textContent.includes("导出审查报告")).click();
        return true;
      })()`);
      await sleep(7000);
      const files = (await readdir(DOWNLOADS)).filter(name => name.endsWith(".docx"));
      const size = files.length ? (await stat(path.join(DOWNLOADS, files[0]))).size : 0;
      check("导出《合同审查报告》成功并真的下载", files.length > 0 && size > 5000, `${files[0] ?? "无"} / ${size} 字节`);
      await shoot("04-真实导出完成");
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
    const stats = await Promise.all(
      files.map(async file => ({ file: path.join(RUNS, file), mtime: (await stat(path.join(RUNS, file))).mtimeMs })),
    );
    stats.sort((a, b) => b.mtime - a.mtime);
    for (const item of stats.slice(0, 3)) {
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
