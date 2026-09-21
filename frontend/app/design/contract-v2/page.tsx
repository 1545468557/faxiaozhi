"use client";

/**
 * 合同审查 · 重做版设计（2026-09-20，产品经理定方向）
 *
 * 产品经理原话：「A. 找坑 + 给改法 + 出报告 → 我照这个把界面重做，**过程全部收掉，只留结果**」。
 *
 * 所以这份设计只保留三屏：
 *   ① 一屏搞定：交合同 + 说你代表谁 → 按「开始审查」
 *   ② 等待：一句话 + 一条细进度 + "已经找到 N 条"，**不出现任何技术词**
 *   ③ 结果：问题清单（这条哪里有问题 / 为什么对你不利 / 法律上怎么说 / 建议怎么改）+ 导出报告
 *
 * 被收起来的东西（放进"想看细节"里，默认不展开）：拆条、检索、分级、核验过程、依据列表、上传材料。
 *
 * 本页是**设计稿**：演示数据，不调后端、不花钱。三个按钮切换三屏。
 */

import { useEffect, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Download,
  FileText,
  Info,
  Upload,
} from "lucide-react";
import { AppShell } from "@/components/shell/app-shell";
import "@/app/contract.css";

type Screen = "input" | "waiting" | "result";

/** 演示数据：挑的都是这份合成示例合同里真实存在的坑 */
const ISSUES = [
  {
    level: "高",
    title: "押金能不能退，完全由对方说了算",
    original: "租赁终止后，出租人可根据实际情况决定是否退还押金，无须说明理由。",
    why: "这条等于把押金交给对方随意处置：退不退、退多少都不用给理由，你事后很难追。",
    law: "《民法典》第五百零九条（当事人应当按照约定全面履行义务）",
    fix: "建议改为：租赁终止并完成交接后 7 日内，出租人应退还剩余押金；如扣除费用，应提供书面明细和凭证。",
  },
  {
    level: "高",
    title: "提前退租：换锁、收回房子、钱全不退",
    original: "乙方提前退租的，甲方有权直接更换门锁并收回房屋，已付租金与押金全部不予退还。",
    why: "对方可以直接换锁把你挡在门外，而且钱一分不退、还不赔你的损失——处理方式明显过重。",
    law: "《民法典》第七百三十一条（租赁物危及安全时承租人可解除合同）",
    fix: "建议改为：乙方提前退租应提前 30 日书面通知并支付一个月租金作为违约金；甲方应通过协商或诉讼收回房屋，不得擅自换锁或处置乙方物品。",
  },
  {
    level: "中",
    title: "全部维修费用都由你承担",
    original: "房屋及其附属设施的全部维修费用由乙方承担。",
    why: "房屋本身的老化、管道、结构问题通常该由房东负责；写「全部」会把不该你掏的钱也算到你头上。",
    law: "《民法典》第七百一十二条（出租人应当履行租赁物的维修义务）",
    fix: "建议改为：因承租人使用不当造成的损坏由承租人维修；房屋主体结构、管道及自然老化部分由出租人负责维修。",
  },
  {
    level: "中",
    title: "验收只有 3 天，过期就不能再提问题",
    original: "乙方应在入住后三日内完成对房屋及设施的验收，逾期未提出异议的，视为验收合格。",
    why: "3 天太短，很多问题（漏水、空调、热水器）要住进去才发现的；一过期你就失去了主张的权利。",
    law: "《民法典》第七百零八条（出租人应当保证租赁物符合约定用途）",
    fix: "建议改为：验收期为 7 个工作日；隐蔽瑕疵不受验收期限制，可在发现后 15 日内提出。",
  },
  {
    level: "中",
    title: "一次性付全年租金，逾期还有高价违约金",
    original: "乙方应于签订本合同时一次性支付全年租金……逾期支付任何款项的，按日千分之五计收违约金。",
    why: "钱先全给对方，对方出问题你就很被动；日千分之五折算年化远超常见标准，属于偏高的违约金。",
    law: "《民法典》第五百八十五条（约定违约金过分高于损失的可以请求适当减少）",
    fix: "建议改为：租金按月支付，月末前 5 日付清；违约金调整为按日万分之一，且总额不超过欠付金额的 10%。",
  },
];

export default function ContractV2DesignPage() {
  const [screen, setScreen] = useState<Screen>("input");
  const [stance, setStance] = useState("party_b");

  useEffect(() => {
    const wanted = new URLSearchParams(window.location.search).get("screen");
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 只在挂载时按地址栏参数初始化一次
    if (wanted === "waiting" || wanted === "result" || wanted === "input") setScreen(wanted);
  }, []);

  return (
    <AppShell>
      <div className="ct-wrap">
        <p className="ct-draft" role="note">
          <AlertTriangle size={14} aria-hidden="true" />
          这是<strong>设计稿</strong>：演示数据。点上面三个按钮看三屏——<strong>交合同 / 正在读 / 问题清单</strong>。
        </p>

        <div className="ct-actions" style={{ marginBottom: 18 }}>
          {(
            [
              ["input", "① 交合同"],
              ["waiting", "② 正在读"],
              ["result", "③ 问题清单"],
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

        {/* ------------------------------------------------------ 第一屏 */}
        {screen === "input" && (
          <section className="ct-card">
            <h1 style={{ margin: "0 0 6px", fontSize: 22 }}>合同审查</h1>
            <p className="ct-lead">把合同交给我，我替你挑出对你不利的地方，并告诉你怎么改。</p>

            <label className="ct-drop">
              <input type="file" readOnly />
              <Upload size={26} strokeWidth={1.5} aria-hidden="true" />
              <strong>把合同拖进来，或点这里选文件</strong>
              <span>docx / pdf / txt / md，一次一份</span>
            </label>
            <p className="ct-mini">
              手上没有合同？
              <button type="button" className="ct-btn ct-btn-ghost" style={{ marginLeft: 8, padding: "6px 12px", fontSize: 13 }}>
                用一份示例合同试一下
              </button>
            </p>

            <div className="ct-parties" style={{ marginTop: 18 }}>
              <b>你是哪一方？</b>同一条，对甲方好对乙方就坏，方向正好相反。
            </div>
            <div className="ct-stance" role="radiogroup" aria-label="我是哪一方">
              {[
                ["party_a", "我是甲方（出租方 / 买方）"],
                ["party_b", "我是乙方（承租方 / 卖方）"],
                ["neutral", "我不站边，只想看看哪里不均衡"],
              ].map(([value, label]) => (
                <label key={value} className="ct-stance-item" data-active={stance === value}>
                  <input
                    type="radio"
                    name="stance"
                    checked={stance === value}
                    onChange={() => setStance(value)}
                  />
                  <span>
                    <strong>{label}</strong>
                  </span>
                </label>
              ))}
            </div>

            <div className="ct-actions">
              <button type="button" className="ct-btn ct-btn-primary" onClick={() => setScreen("waiting")}>
                开始审查
                <ArrowRight size={15} aria-hidden="true" />
              </button>
            </div>
            <p className="ct-mini">合同原文只在本次会话内存中处理，不落盘、不进日志。</p>
          </section>
        )}

        {/* ------------------------------------------------------ 第二屏 */}
        {screen === "waiting" && (
          <section className="ct-card">
            <h2 style={{ fontSize: 19, marginBottom: 4 }}>正在读你的合同…</h2>
            <p className="ct-lead" style={{ marginBottom: 14 }}>
              正在逐条看有没有对你不利的地方，读完就给你清单。大概 40 秒。
            </p>

            <div className="ct-bar" aria-hidden="true">
              <span style={{ width: "62%" }} />
            </div>

            <p className="ct-status" data-tone="running" style={{ marginTop: 14 }}>
              <CheckCircle2 size={16} aria-hidden="true" />
              已经找到 <b>3</b> 条对你不利的地方（还在继续看）
            </p>

            <div className="ct-actions">
              <button type="button" className="ct-btn ct-btn-ghost" onClick={() => setScreen("result")}>
                先看看已经找到的（演示：直接跳到清单）
              </button>
              <button type="button" className="ct-btn ct-btn-ghost">
                先去忙别的（好了会留在这里）
              </button>
            </div>

            <p className="ct-mini" style={{ marginTop: 12 }}>
              等的时候不用盯着看；离开这个页面也没关系，回来还能看到。
            </p>
          </section>
        )}

        {/* ------------------------------------------------------ 第三屏 */}
        {screen === "result" && (
          <>
            <section className="ct-card" style={{ marginBottom: 16 }}>
              <h2 style={{ fontSize: 20, marginBottom: 8 }}>挑出 5 条对你不利的地方</h2>
              <p className="ct-lead" style={{ marginBottom: 0 }}>
                其中有 <b style={{ color: "#c0392b" }}>2 条风险较高</b>，建议签之前先改掉。每条都给了可以直接抄的改法。
              </p>
            </section>

            <ul className="ct-risks" style={{ maxHeight: "none" }}>
              {ISSUES.map((issue, index) => (
                <li key={issue.title} data-active="false" style={{ marginBottom: 10 }}>
                  <div className="ct-risk-body" style={{ paddingTop: 14 }}>
                    <p className="ct-risk-issue" style={{ fontSize: 15, fontWeight: 600, marginBottom: 8 }}>
                      <span
                        className="ct-risk-level"
                        data-tone={issue.level === "高" ? "high" : "medium"}
                        style={{ display: "inline-block", marginRight: 8, verticalAlign: "middle" }}
                      >
                        {issue.level}
                      </span>
                      <span style={{ marginLeft: 2 }}>
                        {index + 1}. {issue.title}
                      </span>
                    </p>

                    <p className="ct-quote" style={{ marginBottom: 10 }}>
                      合同里写的是：“{issue.original}”
                    </p>

                    <p style={{ margin: "0 0 10px", fontSize: 13.5, lineHeight: 1.9 }}>
                      <b>为什么对你不利：</b>
                      {issue.why}
                    </p>

                    <div className="ct-basis" style={{ marginBottom: 10 }}>
                      <span className="ct-mini">法律上怎么说</span>
                      <p>
                        <button type="button" className="ct-cite">
                          <FileText size={13} aria-hidden="true" />
                          <strong>{issue.law}</strong>
                          <em>点开看原文</em>
                        </button>
                      </p>
                    </div>

                    <div className="ct-suggest">
                      <span className="ct-mini">建议这样改（可以直接抄给对方）</span>
                      <p>{issue.fix}</p>
                    </div>
                  </div>
                </li>
              ))}
            </ul>

            <section className="ct-export" style={{ marginTop: 14 }}>
              <div>
                <h3>导出报告（Word）</h3>
                <p className="ct-mini">报告里是上面这份清单，可以直接发给对方或律师。</p>
              </div>
              <button type="button" className="ct-btn ct-btn-primary">
                <Download size={15} aria-hidden="true" />
                导出报告
              </button>
            </section>

            <details className="ct-more">
              <summary>想看细节（怎么读的、引用了哪些法条原文、上传的材料）</summary>
              <p className="ct-mini">
                这些默认收起来：拆条、找法条、分级、逐字核对引用的过程，以及用到的全部依据清单。
                谁需要谁再展开。
              </p>
            </details>

            <p className="ct-foot">
              <Info size={14} aria-hidden="true" />
              没有提示问题的条款不等于没有风险；建议改法须经律师审定。以上内容不构成法律意见。
            </p>
          </>
        )}
      </div>
    </AppShell>
  );
}
