"use client";

/**
 * 合同审查 · 新界面设计稿（2026-09-20）
 *
 * 这是一份**设计稿**，不是功能：
 * - 页面上所有内容都是写死的演示数据，不发任何请求、不调用模型与法规库、不花钱；
 * - 目的是让产品经理"点着看"新界面的流程与信息层次，确认后再照着实现真功能；
 * - 页面顶部常驻"设计稿 · 演示数据"标记，避免被当成真实审查结果。
 *
 * 设计主张（与上一代界面的差别，详见交付说明）：
 * 1. 一次只做一件事 → 上传 / 确认立场 / 审查中 / 结果，四步，不在一屏里堆满；
 * 2. 「确认立场」单独一屏，直接问"你是哪一方"，说清为什么必须选；
 * 3. 风险不再用表格，改成一条一句人话的卡片；依据状态用徽标区分三种；
 * 4. 导出区从"列一堆禁用原因"改成"现在缺什么"一句话；
 * 5. 空页签（依据区 / 核验报告 / 上传材料）不再顶在首页，收进结果页折叠区。
 */

import { useEffect, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Download,
  FileText,
  Info,
  RefreshCw,
  Scale,
  ShieldAlert,
  Upload,
} from "lucide-react";
import { AppShell } from "@/components/shell/app-shell";
import "@/app/contract.css";

/** 演示合同原文（写死；三个风险锚点用 start/end 标注） */
const CONTRACT_HEAD = "办公家具采购合同（演示数据）";
const CLAUSES = [
  {
    id: "c1",
    heading: "第一条　合同标的",
    text: "甲方向乙方采购办公家具一批，具体型号、数量与单价以附件一为准。附件一未在签署时确认的，以乙方发货单为准。",
  },
  {
    id: "c2",
    heading: "第二条　付款方式",
    text: "本合同签订后三个工作日内，甲方支付合同总价 60% 作为预付款；余款在到货后 30 日内付清。乙方逾期交货超过 15 日的，甲方有权解除合同，乙方应退还已付款项并支付合同总价 30% 的违约金。",
    anchor: "甲方支付合同总价 60% 作为预付款",
  },
  {
    id: "c3",
    heading: "第三条　质量与验收",
    text: "货物到场后甲方应在 3 日内完成验收，逾期未提出异议的，视为验收合格。质保期内出现质量问题，乙方仅承担维修责任，不承担由此造成的其他损失。",
    anchor: "乙方仅承担维修责任，不承担由此造成的其他损失",
  },
];

const RISKS = [
  {
    id: "r1",
    level: "高",
    levelTone: "high",
    kind: "付款与结算",
    clauseId: "c2",
    clauseHeading: "第二条　付款方式",
    issue: "预付款比例 60% 明显偏高，且没有约定乙方不交货时的退款期限；一旦对方不履约，你方资金先出去、追回周期很长。",
    basisStatus: "verified" as const,
    basis: [
      {
        identifier: "《民法典》第五百七十七条",
        quote: "当事人一方不履行合同义务或者履行合同义务不符合约定的，应当承担继续履行、采取补救措施或者赔偿损失等违约责任。",
      },
    ],
    suggestion: "建议把预付款压到 30% 以内，并补一句：「乙方逾期超过 15 日未交货的，应在 5 个工作日内无息退还全部已付款项」。",
    confidence: "高",
  },
  {
    id: "r2",
    level: "中",
    levelTone: "medium",
    kind: "验收条款",
    clauseId: "c3",
    clauseHeading: "第三条　质量与验收",
    issue: "验收期只有 3 日、逾期视为合格，对买方偏紧；家具类问题往往要用一段时间才看得出来。",
    basisStatus: "no_basis" as const,
    basis: [],
    suggestion: "建议把验收期改为 7 个工作日，并补一句「隐蔽瑕疵不受验收期限制，可在发现后 15 日内提出」。",
    confidence: "中",
  },
  {
    id: "r3",
    level: "高",
    levelTone: "high",
    kind: "责任限制",
    clauseId: "c3",
    clauseHeading: "第三条　质量与验收",
    issue: "「只修不赔」把质量问题造成的其他损失（例如停工、返工成本）全部排除，责任分配明显失衡。",
    basisStatus: "rejected" as const,
    basis: [
      {
        identifier: "《民法典》第五百八十四条",
        quote: "……损失赔偿额应当相当于因违约所造成的损失，包括合同履行后可以获得的利益……",
      },
    ],
    suggestion: "建议删去「不承担由此造成的其他损失」，改为「并赔偿由此给甲方造成的直接损失」。",
    confidence: "中",
  },
];

const STEPS = [
  { id: 1, label: "上传合同" },
  { id: 2, label: "确认立场" },
  { id: 3, label: "审查中" },
  { id: 4, label: "看结果并导出" },
];

const BASIS_BADGE = {
  verified: { text: "有依据 · 已核验", tone: "ok" },
  no_basis: { text: "未找到直接依据 · 仅作提示", tone: "warn" },
  rejected: { text: "依据未通过核验 · 仅作提示", tone: "warn" },
};

export default function ContractDesignPage() {
  const [step, setStep] = useState(1);
  const [uploaded, setUploaded] = useState(false);
  const [verified, setVerified] = useState(false);
  const [stance, setStance] = useState("");
  const [activeRisk, setActiveRisk] = useState<string | null>(null);

  // 方便评审：?step=4 可直接看结果态；?step=3 看进度态。仅本设计稿使用。
  // 这里是"读一次外部来源（地址栏）→ 初始化状态"，不是派生渲染状态，故允许在挂载时写一次。
  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    const wanted = Number(query.get("step"));
    if (!Number.isFinite(wanted) || wanted < 1 || wanted > 4) return;
    if (wanted >= 2) {
      // 只在挂载时按地址栏参数初始化一次（外部来源 → 状态），不是派生渲染状态
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setUploaded(true);
      setVerified(true);
    }
    if (wanted >= 3) setStance(query.get("stance") || "a");
    setStep(wanted);
  }, []);

  const canStart = uploaded && verified;
  const canStance = stance !== "";

  return (
    <AppShell>
      <div className="ct-wrap">
        <p className="ct-draft" role="note">
          <Info size={14} aria-hidden="true" />
          这是<strong>设计稿</strong>：内容全是写死的演示数据，不是真实审查结果，也不调用任何模型与法规库。看完请在对话里告诉我要改哪里。
        </p>

        <header className="ct-head">
          <h1>合同审查</h1>
          <p>上传一份合同，逐条挑出对你不利的地方，并标明依据。</p>
        </header>

        <ol className="ct-steps" aria-label="流程">
          {STEPS.map(item => (
            <li key={item.id} data-state={step === item.id ? "current" : step > item.id ? "done" : "todo"}>
              <button type="button" onClick={() => setStep(item.id)}>
                <span className="ct-step-no">{step > item.id ? <CheckCircle2 size={14} /> : item.id}</span>
                {item.label}
              </button>
            </li>
          ))}
        </ol>

        {/* ------------------------------------------------ 第一步：上传 */}
        {step === 1 && (
          <section className="ct-card">
            <h2>第一步　上传合同</h2>
            <p className="ct-lead">支持 docx / pdf / txt / md，一次一份（V1）。合同原文只在本次会话内存中处理，不落盘、不入日志。</p>

            {!uploaded ? (
              <div className="ct-drop">
                <Upload size={26} strokeWidth={1.5} aria-hidden="true" />
                <strong>把合同拖进来，或点这里选择文件</strong>
                <span>单份不超过 20 MB</span>
                <button type="button" className="ct-btn ct-btn-primary" onClick={() => setUploaded(true)}>
                  选择文件（演示：点一下就当已上传）
                </button>
              </div>
            ) : (
              <>
                <div className="ct-file">
                  <FileText size={18} aria-hidden="true" />
                  <div>
                    <strong>办公家具采购合同.docx</strong>
                    <span>约 1,860 字 · 识别为「合同」 · 甲乙方：甲方「示例科技有限公司」／乙方「示例家具有限公司」</span>
                  </div>
                  <button type="button" className="ct-btn ct-btn-ghost" onClick={() => setUploaded(false)}>
                    换一份
                  </button>
                </div>
                <p className="ct-mini">识别不对？可以在这里改成「合同 / 案例材料 / 法条」，改错会直接影响后面按什么标准审查。</p>

                <label className="ct-check">
                  <input type="checkbox" checked={verified} onChange={event => setVerified(event.target.checked)} />
                  <span>
                    本人已核验这份合同
                    <em>未核验时不会把合同内容当作依据引用，也不能开始审查。</em>
                  </span>
                </label>

                <div className="ct-actions">
                  <button
                    type="button"
                    className="ct-btn ct-btn-primary"
                    disabled={!canStart}
                    onClick={() => setStep(2)}
                  >
                    开始审查
                    <ArrowRight size={15} aria-hidden="true" />
                  </button>
                  {!canStart && (
                    <p className="ct-blocked" role="status">
                      <ShieldAlert size={14} aria-hidden="true" />
                      {!uploaded ? "还没有上传合同。" : "请先勾选「本人已核验这份合同」。"}
                    </p>
                  )}
                </div>
              </>
            )}
          </section>
        )}

        {/* -------------------------------------------- 第二步：立场硬门 */}
        {step === 2 && (
          <section className="ct-card">
            <h2>
              第二步　确认你代表哪一方
              <span className="ct-lock">必选，不选不能继续</span>
            </h2>
            <p className="ct-lead">
              同一份合同，站在甲方和站在乙方看，风险正好相反。请先告诉系统你为谁审。
            </p>
            <p className="ct-parties">
              合同里识别到的当事人：甲方「示例科技有限公司」 · 乙方「示例家具有限公司」
            </p>

            <div className="ct-stance" role="radiogroup" aria-label="我代表哪一方">
              {[
                { value: "a", label: "代表甲方", hint: "示例科技有限公司（采购方）" },
                { value: "b", label: "代表乙方", hint: "示例家具有限公司（供货方）" },
                { value: "neutral", label: "中立审阅", hint: "不站在任何一方，只看条款是否均衡" },
              ].map(option => (
                <label key={option.value} className="ct-stance-item" data-active={stance === option.value}>
                  <input
                    type="radio"
                    name="stance"
                    checked={stance === option.value}
                    onChange={() => setStance(option.value)}
                  />
                  <span>
                    <strong>{option.label}</strong>
                    <em>{option.hint}</em>
                  </span>
                </label>
              ))}
            </div>

            <div className="ct-actions">
              <button
                type="button"
                className="ct-btn ct-btn-primary"
                disabled={!canStance}
                onClick={() => setStep(3)}
              >
                确认立场并开始审查
              </button>
              {!canStance && (
                <p className="ct-blocked" role="status">
                  <ShieldAlert size={14} aria-hidden="true" />
                  还没有选立场，不能开始审查（立场错了，风险结论会整份反着来）。
                </p>
              )}
            </div>
          </section>
        )}

        {/* ------------------------------------------------ 第三步：进度 */}
        {step === 3 && (
          <section className="ct-card">
            <h2>第三步　正在审查（演示）</h2>
            <ul className="ct-progress">
              {[
                { name: "读懂合同、切成条款", state: "done", note: "识别出 3 条主条款" },
                { name: "按立场找依据", state: "done", note: "查法规库 4 次，取回 6 条依据" },
                { name: "逐条分级风险", state: "running", note: "已出 3 条，正在继续" },
                { name: "核验引用的原文", state: "todo", note: "还没开始" },
              ].map(item => (
                <li key={item.name} data-state={item.state}>
                  <span className="ct-dot" aria-hidden="true" />
                  <strong>{item.name}</strong>
                  <em>{item.note}</em>
                </li>
              ))}
            </ul>
            <div className="ct-actions">
              <button type="button" className="ct-btn ct-btn-primary" onClick={() => setStep(4)}>
                看看结果（演示：直接跳到结果）
              </button>
              <button type="button" className="ct-btn ct-btn-ghost">
                <RefreshCw size={14} aria-hidden="true" />
                停下来
              </button>
            </div>
          </section>
        )}

        {/* ------------------------------------------------ 第四步：结果 */}
        {step === 4 && (
          <>
            <div className="ct-summary">
              <div>
                <strong>共 3 条风险</strong>
                <span>
                  高 <b data-tone="high">2</b> · 中 <b data-tone="medium">1</b> · 低 0
                </span>
              </div>
              <div>
                <strong>其中 1 条有已核验依据</strong>
                <span>另 2 条只作提示（1 条未找到直接依据、1 条依据未通过核验）</span>
              </div>
              <p className="ct-summary-note">
                <Info size={14} aria-hidden="true" />
                没有提示风险的条款，不等于没有风险；结论须由经办人员复核。
              </p>
            </div>

            <div className="ct-result">
              <section className="ct-pane" aria-label="合同原文">
                <header>
                  <h3>{CONTRACT_HEAD}</h3>
                  <span className="ct-mini">只读原文 · 黄色是风险位置，点它看右侧对应条目</span>
                </header>
                <div className="ct-text">
                  {CLAUSES.map(clause => (
                    <p key={clause.id}>
                      <span className="ct-clause-head" data-active={activeRisk && RISKS.find(r => r.id === activeRisk)?.clauseId === clause.id}>
                        {clause.heading}
                      </span>
                      <br />
                      {clause.anchor ? (
                        <>
                          {clause.text.split(clause.anchor)[0]}
                          <mark
                            data-active={RISKS.filter(r => r.clauseId === clause.id).some(r => r.id === activeRisk)}
                            onClick={() => {
                              const hit = RISKS.find(r => r.clauseId === clause.id);
                              if (hit) setActiveRisk(hit.id);
                            }}
                          >
                            {clause.anchor}
                          </mark>
                          {clause.text.split(clause.anchor)[1]}
                        </>
                      ) : (
                        clause.text
                      )}
                    </p>
                  ))}
                </div>
              </section>

              <section className="ct-pane" aria-label="风险清单">
                <header>
                  <h3>风险清单（3）</h3>
                  <span className="ct-mini">点一条，左侧原文会跳到对应位置</span>
                </header>
                <ul className="ct-risks">
                  {RISKS.map(risk => {
                    const badge = BASIS_BADGE[risk.basisStatus];
                    const open = activeRisk === risk.id;
                    return (
                      <li key={risk.id} data-active={open}>
                        <button type="button" className="ct-risk-head" onClick={() => setActiveRisk(open ? null : risk.id)}>
                          <span className="ct-risk-level" data-tone={risk.levelTone}>
                            {risk.level}
                          </span>
                          <span className="ct-risk-title">
                            <strong>{risk.kind}</strong>
                            <em>{risk.clauseHeading}</em>
                          </span>
                          <span className="ct-badge" data-tone={badge.tone}>
                            {badge.text}
                          </span>
                        </button>

                        {open && (
                          <div className="ct-risk-body">
                            <p className="ct-risk-issue">{risk.issue}</p>

                            {risk.basis.length > 0 && (
                              <div className="ct-basis">
                                <span className="ct-mini">依据</span>
                                {risk.basis.map(item => (
                                  <p key={item.identifier}>
                                    <strong>{item.identifier}</strong>
                                    <em>“{item.quote}”</em>
                                  </p>
                                ))}
                                {risk.basisStatus === "rejected" && (
                                  <p className="ct-blocked">
                                    <AlertTriangle size={14} aria-hidden="true" />
                                    这条依据的原文没能逐字对上，所以只作提示，不作结论。
                                  </p>
                                )}
                              </div>
                            )}

                            <div className="ct-suggest">
                              <span className="ct-mini">建议改法（须经律师审定）</span>
                              <p>{risk.suggestion}</p>
                            </div>
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </section>
            </div>

            <section className="ct-export">
              <div>
                <h3>导出审查报告（Word）</h3>
                <p className="ct-mini">报告包含：合同基本信息、风险清单与依据、未通过核验的提示项、复核提示。</p>
              </div>
              <button type="button" className="ct-btn ct-btn-primary">
                <Download size={15} aria-hidden="true" />
                导出审查报告
              </button>
            </section>

            <details className="ct-more">
              <summary>依据区（6）· 核验报告 · 上传材料（1）</summary>
              <p className="ct-mini">
                这三块内容不顶在首页，需要时展开看。上一代界面把它们并排摆在上方，占了半屏、又大多是空的。
              </p>
            </details>
          </>
        )}

        <p className="ct-foot">
          <Scale size={14} aria-hidden="true" />
          本页为设计稿，演示数据；真实功能仍走已验证的合同审查链路。以上内容不构成法律意见，涉及实际权益请由执业律师复核。
        </p>
      </div>
    </AppShell>
  );
}
