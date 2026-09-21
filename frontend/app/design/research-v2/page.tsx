"use client";

/**
 * 类案检索 · 新设计（2026-09-20，沿用"过程收掉、只留结果"的口径）
 *
 * 四屏：
 *   ① 说你要查什么 → ② 正在查（一句话 + 细进度）→ ③ 挑案例（人工门：你确认对比哪几篇）
 *   → ④ 研究报告（结论 + 对比 + 依据 + 导出 Word）
 *
 * 为什么第③屏不能省：后端有一条硬门——**没有人工确认的样本，不许进入对比与结论**
 * （PRD 2.4 ADR-4）。但它在界面上的说法不是"样本确认门"这种内部词，而是：
 * "我找到 12 篇，对比要逐篇读，你挑 3-5 篇最接近的"。
 *
 * 本页是**设计稿**：演示数据，不调后端、不花钱。
 */

import { useEffect, useState } from "react";
import { AlertTriangle, ArrowRight, Download, FileText, Info, Search } from "lucide-react";
import { AppShell } from "@/components/shell/app-shell";
import "@/app/contract.css";

type Screen = "ask" | "searching" | "pick" | "report";

const CANDIDATES = [
  ["（2023）京02民终1145号", "北京市第二中级人民法院", "2023-04-12", "房东出售房屋后要求承租人限期搬离，法院认为买卖不破租赁，承租人可以继续使用至租期届满。"],
  ["（2022）沪01民终8823号", "上海市第一中级人民法院", "2022-11-08", "新房东主张未办理租赁登记不予认可租赁关系，法院认定登记与否不影响租赁合同效力。"],
  ["（2022）粤03民终6671号", "广东省深圳市中级人民法院", "2022-09-26", "房东以自用为由要求解除租赁，法院认为不符合法定解除条件，驳回其请求。"],
  ["（2021）苏05民终3312号", "江苏省苏州市中级人民法院", "2021-12-15", "双方约定押金一律不退，法院认定该条款加重承租人责任，仅支持扣除实际损失部分。"],
  ["（2023）浙01民终2207号", "浙江省杭州市中级人民法院", "2023-06-03", "承租人提前退租，法院认为出租人换锁并扣留全部押金的做法超出必要范围。"],
  ["（2021）川01民终5590号", "四川省成都市中级人民法院", "2021-08-19", "合同约定维修费用全部由承租人承担，法院认定房屋主体维修应由出租人负担。"],
];

const MATRIX = {
  columns: ["结果", "押金怎么处理", "能否继续租住"],
  rows: [
    ["（2023）京02民终1145号", "支持承租人继续租住", "押金全额退还", "可以，至租期届满"],
    ["（2022）沪01民终8823号", "支持承租人继续租住", "押金全额退还", "可以"],
    ["（2022）粤03民终6671号", "驳回房东解除请求", "押金全额退还", "可以"],
    ["（2021）苏05民终3312号", "部分支持", "只能扣实际损失", "未涉及"],
  ],
};

const CONCLUSIONS = [
  {
    text: "房东把房子卖了，不影响你已经签的租约：新房东原则上不能以“房子换主人了”为理由让你提前搬走，你可以按合同住到租期届满。",
    cites: ["（2023）京02民终1145号", "（2022）沪01民终8823号", "（2022）粤03民终6671号"],
  },
  {
    text: "合同里写“押金一律不退”这类条款，法院通常不会照单支持，一般只允许房东扣掉实际损失的部分。",
    cites: ["（2021）苏05民终3312号", "（2023）浙01民终2207号"],
  },
  {
    text: "“维修费用全部由承租人承担”的约定，多数法院会区分：房屋主体和自然老化由房东负责，使用不当造成的损坏才由承租人承担。",
    cites: ["（2021）川01民终5590号"],
  },
];

export default function ResearchV2DesignPage() {
  const [screen, setScreen] = useState<Screen>("ask");
  const [picked, setPicked] = useState<string[]>([CANDIDATES[0][0], CANDIDATES[1][0], CANDIDATES[2][0]]);

  useEffect(() => {
    const wanted = new URLSearchParams(window.location.search).get("screen");
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 只在挂载时按地址栏参数初始化一次
    if (wanted === "ask" || wanted === "searching" || wanted === "pick" || wanted === "report") setScreen(wanted);
  }, []);

  const toggle = (id: string) =>
    setPicked(current => (current.includes(id) ? current.filter(item => item !== id) : [...current, id]));

  return (
    <AppShell>
      <div className="ct-wrap">
        <p className="ct-draft" role="note">
          <AlertTriangle size={14} aria-hidden="true" />
          这是<strong>设计稿</strong>：案例、案号、判决内容都是编的演示数据。点上面四个按钮看四屏。
        </p>

        <div className="ct-actions" style={{ marginBottom: 18 }}>
          {(
            [
              ["ask", "① 说你要查什么"],
              ["searching", "② 正在查"],
              ["pick", "③ 挑案例"],
              ["report", "④ 研究报告"],
            ] as [Screen, string][]
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              className={screen === value ? "ct-btn ct-btn-primary" : "ct-btn"}
              onClick={() => setScreen(value)}
            >
              {label}
            </button>
          ))}
        </div>

        {/* -------------------------------------------------------- ① 说你要查什么 */}
        {screen === "ask" && (
          <section className="ct-card">
            <h1 style={{ margin: "0 0 6px", fontSize: 22 }}>类案检索</h1>
            <p className="ct-lead">
              说清楚你的情况，我去找相似的案子，看看法院都是怎么判的，最后给你一份可以拿去用的结论。
            </p>

            <textarea
              className="ct-input"
              rows={4}
              defaultValue="房东把房子卖了，新房东让我两个月内搬走。合同还有 8 个月到期，押金也一直没退。"
              aria-label="你要查什么"
            />

            <details className="ct-more" style={{ marginTop: 12 }}>
              <summary>补充条件（可以留空）</summary>
              <div className="ct-fields">
                <label>
                  案由
                  <input defaultValue="房屋租赁合同纠纷" />
                </label>
                <label>
                  地域
                  <input placeholder="不填＝全国" />
                </label>
                <label>
                  时间范围
                  <input placeholder="不填＝近三年" />
                </label>
              </div>
            </details>

            <div className="ct-actions">
              <button type="button" className="ct-btn ct-btn-primary" onClick={() => setScreen("searching")}>
                <Search size={15} aria-hidden="true" />
                开始找相似案例
              </button>
            </div>
            <p className="ct-mini">检索会使用北大法宝的检索额度，并产生 AI 分析费用（一次约几毛钱）。</p>
          </section>
        )}

        {/* -------------------------------------------------------- ② 正在查 */}
        {screen === "searching" && (
          <section className="ct-card">
            <h2 style={{ fontSize: 19, marginBottom: 4 }}>正在找相似的案子…</h2>
            <p className="ct-lead" style={{ marginBottom: 14 }}>
              先去法规库找一批相似的判决，再逐篇读。大概 1 分钟。
            </p>

            <div className="ct-bar" aria-hidden="true">
              <span style={{ width: "48%" }} />
            </div>

            <div className="ct-yield">
              <div>
                <span>已找到相似案例</span>
                <b>12 篇</b>
              </div>
              <div>
                <span>已读完</span>
                <b>—</b>
              </div>
              <div>
                <span>下一步</span>
                <b>让你挑几篇</b>
              </div>
            </div>

            <p className="ct-mini">
              找完会先给你看候选案例，让你确认对比哪几篇；不确认不会往下做。不用盯着看，离开也没关系。
            </p>

            <div className="ct-actions">
              <button type="button" className="ct-btn ct-btn-ghost" onClick={() => setScreen("pick")}>
                先看找到的（演示：直接跳到下一屏）
              </button>
            </div>
          </section>
        )}

        {/* -------------------------------------------------------- ③ 挑案例（人工门） */}
        {screen === "pick" && (
          <section className="ct-card">
            <h2 style={{ fontSize: 19, marginBottom: 4 }}>我找到 12 篇相似案例，你挑几篇来对比</h2>
            <p className="ct-lead">
              对比要逐篇细读，选 <b>3-5 篇最接近的</b>就够；挑得太杂，结论反而不准。
            </p>

            <ul className="ct-cands">
              {CANDIDATES.map(([id, court, date, summary]) => (
                <li key={id} data-active={picked.includes(id)}>
                  <label className="ct-cand">
                    <input type="checkbox" checked={picked.includes(id)} onChange={() => toggle(id)} />
                    <span>
                      <strong>{id}</strong>
                      <em>
                        {court} · {date}
                      </em>
                      <small>{summary}</small>
                    </span>
                  </label>
                </li>
              ))}
            </ul>

            <div className="ct-actions">
              <span className="ct-mini">已选 {picked.length} 篇</span>
              <button type="button" className="ct-btn" onClick={() => setPicked(CANDIDATES.map(item => item[0]))}>
                全选
              </button>
              <button type="button" className="ct-btn" onClick={() => setPicked([])}>
                清空
              </button>
            </div>

            <div className="ct-actions">
              <button
                type="button"
                className="ct-btn ct-btn-primary"
                disabled={picked.length < 2}
                onClick={() => setScreen("report")}
              >
                就对比这 {picked.length} 篇
                <ArrowRight size={15} aria-hidden="true" />
              </button>
              {picked.length < 2 && (
                <p className="ct-blocked" role="status">
                  <AlertTriangle size={14} aria-hidden="true" />
                  至少要选 2 篇才能对比（只选 1 篇得不出结论）。
                </p>
              )}
            </div>
          </section>
        )}

        {/* -------------------------------------------------------- ④ 研究报告 */}
        {screen === "report" && (
          <>
            <section className="ct-card" style={{ marginBottom: 16 }}>
              <h2 style={{ fontSize: 20, marginBottom: 8 }}>看完这几篇，可以这样理解你的情况</h2>
              <p className="ct-lead" style={{ marginBottom: 0 }}>
                基于你勾选的 4 篇相似案例（另有 8 篇未纳入对比）。下面每条结论都能点开看出自哪个案子。
              </p>
            </section>

            <section className="ct-card" style={{ marginBottom: 16 }}>
              <h3 style={{ fontSize: 16, margin: "0 0 10px" }}>结论</h3>
              <ul style={{ margin: 0, paddingLeft: 18, display: "grid", gap: 12 }}>
                {CONCLUSIONS.map(item => (
                  <li key={item.text} style={{ fontSize: 13.5, lineHeight: 1.95 }}>
                    {item.text}
                    <span className="ct-cites">
                      {item.cites.map(cite => (
                        <button key={cite} type="button" className="ct-cite" style={{ width: "auto", display: "inline-flex", marginLeft: 6 }}>
                          <FileText size={12} aria-hidden="true" />
                          <strong>{cite}</strong>
                        </button>
                      ))}
                    </span>
                  </li>
                ))}
              </ul>
            </section>

            <section className="ct-card" style={{ marginBottom: 16 }}>
              <h3 style={{ fontSize: 16, margin: "0 0 6px" }}>这几篇法院怎么看（对比）</h3>
              <p className="ct-mini" style={{ marginBottom: 10 }}>
                4 篇里有 3 篇支持承租人继续租住；押金问题上有 1 篇只支持扣除实际损失。
              </p>
              <div className="ct-table-wrap">
                <table className="ct-table">
                  <thead>
                    <tr>
                      <th>案例</th>
                      {MATRIX.columns.map(column => (
                        <th key={column}>{column}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {MATRIX.rows.map(row => (
                      <tr key={row[0]}>
                        {row.map((cell, index) => (
                          <td key={`${row[0]}-${index}`}>{cell}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="ct-export">
              <div>
                <h3>导出研究报告（Word）</h3>
                <p className="ct-mini">报告里是上面的结论、对比表和用到的案例清单，可以直接拿去用或给律师看。</p>
              </div>
              <button type="button" className="ct-btn ct-btn-primary">
                <Download size={15} aria-hidden="true" />
                导出报告
              </button>
            </section>

            <details className="ct-more">
              <summary>想看细节（用到的全部案例与法条 · 核验情况 · 我上传的材料）</summary>
              <p className="ct-mini">
                默认收起来：检索到的全部 12 篇候选、每条的来源与核验状态、引用逐字核对的结果、上传的材料清单。
              </p>
            </details>

            <p className="ct-foot">
              <Info size={14} aria-hidden="true" />
              以上内容由 AI 生成，仅供学习与研究参考，不构成法律意见；引用不代表已有律师审定。
            </p>
          </>
        )}
      </div>
    </AppShell>
  );
}
