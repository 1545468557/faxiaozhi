"use client";

/**
 * 合同审查 · 结果（重做版：只留结果）
 *
 * 产品经理定的方向（2026-09-20）：「找坑 + 给改法 + 出报告，**过程全部收掉，只留结果**」。
 * 所以这一屏只做三件事：
 *   1. 先说结论（挑出几条、其中几条风险较高）；
 *   2. 每条问题给四块——**合同里原话 / 为什么对你不利 / 法律上怎么说 / 建议这样改**；
 *   3. 底部一个导出按钮。
 * 拆条、找法条、逐字核对、全部依据清单**默认折叠**在最后。
 *
 * 判定逻辑仍全部沿用 lib/api 里的既有函数：三种依据状态、未通过核验只能作提示、
 * 定位失败绝不高亮、导出未就绪时禁用并原文列出原因。本文件不发明业务规则。
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Download, FileText, Info, RefreshCw } from "lucide-react";
import { ContractReport, reviewLabel, type ReviewNotes } from "./contract-report";
import { EvidenceTabs } from "@/components/workspace/evidence-tabs";
import { SourceSheet } from "@/components/workspace/source-sheet";
import { RISK_LEVEL_LABELS, riskKindText, segmentContract } from "@/lib/api/contract";
import type { exportState } from "@/lib/api/common";
import type {
  ConflictItem,
  ContractRisk,
  ContractSnapshot,
  GateReport,
  Gap,
  MaterialItem,
  SourceBrief,
} from "@/lib/api/types";

type ExportState = ReturnType<typeof exportState>;
type RiskSummary = {
  total: number;
  text: string;
  byLevel: { high: number; medium: number; low: number };
  verified: number;
  noBasis: number;
  locateFailed: number;
};

const BASIS_BADGE: Record<string, { text: string; tone: "ok" | "warn" }> = {
  verified: { text: "有法律依据（已核对原文）", tone: "ok" },
  no_basis: { text: "没找到直接条文 · 只作提醒", tone: "warn" },
  rejected: { text: "引用的条文没对上 · 只作提醒", tone: "warn" },
  pending: { text: "正在核对依据", tone: "warn" },
};

export function ContractResult({
  contract,
  risks,
  activeRiskId,
  onSelectRisk,
  summary,
  sources,
  stanceLabel,
  exportInfo,
  onExport,
  exporting,
  reportMode = false, onReportMode, reviewKey,
  onRefresh,
  refreshing,
  refreshedAt,
  truncated,
  materials,
  gateReport,
  gaps,
  conflicts,
  onVerify,
  uploadFiles,
  uploading,
  uploadRejected,
  maxFiles,
  failedStep,
  onRetry,
  canRetry,
  retrying,
}: {
  contract: ContractSnapshot | undefined;
  risks: ContractRisk[];
  activeRiskId: string | null;
  onSelectRisk: (riskId: string | null) => void;
  summary: RiskSummary;
  sources: SourceBrief[];
  stanceLabel: string;
  exportInfo: ExportState;
  onExport: (notes?: ReviewNotes) => void;
  reportMode?: boolean;
  onReportMode?: (value: boolean) => void;
  reviewKey?: string;
  exporting: boolean;
  onRefresh: () => void;
  refreshing: boolean;
  refreshedAt: string | null;
  truncated: string[];
  materials: MaterialItem[];
  gateReport: GateReport | null;
  gaps: Gap[] | undefined;
  conflicts: ConflictItem[];
  onVerify: (sourceId: string) => void;
  uploadFiles: (files: File[]) => void;
  uploading: boolean;
  uploadRejected: { filename: string; message?: string }[];
  maxFiles: number;
  failedStep: string | null | undefined;
  onRetry: () => void;
  canRetry: boolean | undefined;
  retrying: boolean;
}) {
  const [draft, setDraft] = useState<{key?:string; notes:ReviewNotes}>({key:reviewKey,notes:{}});
  const notes = useMemo(() => draft.key === reviewKey ? draft.notes : {}, [draft, reviewKey]);
  const [filter,setFilter] = useState("all");
  const updateNote = (risk:ContractRisk, status:string, suggestion:string) => setDraft({key:reviewKey,notes:{...notes,[risk.riskId]:{status,suggestion}}});
  useEffect(()=>{if(!Object.keys(notes).length)return;const warn=(e:BeforeUnloadEvent)=>{e.preventDefault();};window.addEventListener("beforeunload",warn);return()=>window.removeEventListener("beforeunload",warn);},[notes]);
  const [detail, setDetail] = useState<SourceBrief | null>(null);
  const openSource = (sourceId: string) => setDetail(sources.find(item => item.source_id === sourceId) ?? null);
  const visibleRisks = risks.filter(r => filter === "all" || filter === "high" && r.level === "high" || filter === "pending" && (!notes[r.riskId] || notes[r.riskId].status === "pending"));
  const selected = visibleRisks.find(r => r.riskId === activeRiskId) ?? visibleRisks[0];
  const selectedId = selected?.riskId ?? null;
  const handled = risks.filter(r => ["accept", "skip"].includes(notes[r.riskId]?.status)).length;
  const high = summary.byLevel.high ?? 0;
  const provisional = risks.length - summary.verified;

  const exportPanel = (<section className="ct-export">
        <div>
          <h3>导出报告（Word）</h3>
          <p className="ct-mini">包含系统审查清单、处理状态与人工修改意见，请复核后使用。</p>
          {!exportInfo.ready && (
            <p className="ct-blocked" role="status" style={{ marginTop: 8 }}>
              <AlertTriangle size={14} aria-hidden="true" />
              现在还导出不了：{exportInfo.blockers.length ? exportInfo.blockers.join("；") : "这次没有产出可导出的内容。"}
            </p>
          )}
        </div>
        <div className="ct-actions" style={{ margin: 0 }}>
          {failedStep && canRetry && (
            <button type="button" className="ct-btn" onClick={onRetry} disabled={retrying}>
              {retrying ? "正在重试…" : "重试没做成的那一步"}
            </button>
          )}
          <button
            type="button"
            className="ct-btn ct-btn-primary"
            onClick={()=>onExport(notes)}
            disabled={exportInfo.disabled || exporting}
          >
            <Download size={15} aria-hidden="true" />
            {exporting ? "正在导出…" : "导出报告"}
          </button>
          <button type="button" className="ct-btn ct-btn-ghost" onClick={onRefresh} disabled={refreshing}>
            <RefreshCw size={14} aria-hidden="true" />
            {refreshing ? "正在重新取…" : "刷新状态"}
          </button>
        </div>
        {refreshedAt && (
          <p className="ct-mini" role="status" style={{ width: "100%" }}>
            已重新取回状态（{refreshedAt}）。
          </p>
        )}
      </section>);

  return (
    <>
      {/* ---------------------------------------------------- 结论：先说结果 */}
      <section className="cx-review-summary"><div>
        <h2 style={{ fontSize: 20, marginBottom: 8 }}>
          {failedStep && !summary.total
            ? "这次没跑完，没能给出结论"
            : summary.total
              ? `挑出 ${summary.total} 条对你不利的地方`
              : "没有挑出对你不利的地方"}
        </h2>
        <p className="ct-lead" >
          {failedStep && !summary.total ? (
            <>
              这次卡在「{failedStep}」，<b>所以这不代表合同没问题</b>。已经找出来的部分列在下面；
              可以点「重试没做成的那一步」把它补完。
            </>
          ) : summary.total ? (
            <>
              {high > 0 ? (
                <>
                  其中有 <b style={{ color: "#c0392b" }}>{high} 条风险较高</b>，建议签之前先改掉。
                </>
              ) : (
                <>没有高风险的条目，但仍有 {summary.total - high} 条值得在签字前确认。</>
              )}
              {provisional > 0 && <> 其中 {provisional} 条缺可靠依据，只作提醒。</>}
              {stanceLabel && <>（审查立场：{stanceLabel}）</>}
            </>
          ) : (
            <>
              可能是合同里确实没有明显问题，也可能是这次没看全。没有提示问题的条款不等于没有风险，
              签字前请再自己过一遍，或请律师复核。
            </>
          )}
        </p>

        {failedStep && (
          <p className="ct-blocked" role="status">
            <AlertTriangle size={14} aria-hidden="true" />
            这次没跑完（卡在「{failedStep}」）。已经找出来的部分都列在下面，可以直接看，也可以重试补完。
          </p>
        )}

        {truncated.length > 0 && (
          <div className="ct-more" style={{ marginTop: 10 }}>
            <strong style={{ fontSize: 13.5 }}>这次看的不完整（范围限制）</strong>
            <ul className="ct-range">
              {truncated.map(item => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        )}
      </div><div className="cx-risk-counts"><span data-level="high"><b>{high}</b>高关注</span><span data-level="medium"><b>{summary.byLevel.medium}</b>中关注</span><span><b>{summary.byLevel.low}</b>低关注</span></div></section>

      <div className="cx-bottom"><p className="ct-mini">修改意见为当前页面草稿，请导出报告保存；刷新或切换模块可能丢失草稿。</p><button className="ct-btn ct-btn-primary" onClick={()=>onReportMode?.(!reportMode)}>{reportMode ? "返回审查意见" : "整理审查报告"}</button></div>
      {reportMode ? <div className="cx-report-layout"><ContractReport contract={contract} risks={risks} notes={notes} stanceLabel={stanceLabel} truncated={truncated}/><aside className="cx-report-sidebar">{exportPanel}</aside></div> : <div className="cx-review-grid"><section className="ct-pane cx-contract-paper"><header><h3><FileText size={18}/> 合同原文</h3><span>{contract?.clauses_count ?? contract?.clauses?.length ?? 0} 个条款</span></header><ContractText text={contract?.text??""} risks={risks} activeRiskId={selectedId} onAnchorClick={onSelectRisk}/></section><section>
      <div className="cx-filters">{[["all","全部"],["high","高风险"],["pending","待处理"]].map(([v,t])=><button key={v} className="ct-btn" aria-pressed={filter===v} onClick={()=>setFilter(v)}>{t} {v === "all" ? risks.length : v === "high" ? high : risks.length-handled}</button>)}<span>已处理 {handled} / {risks.length}</span></div>
      {/* ---------------------------------------------------- 每条问题：四块 */}
      <ul className="ct-risks" style={{ maxHeight: "none" }}>
        {visibleRisks.map((risk, index) => {
          const badge = BASIS_BADGE[risk.basisStatus] ?? BASIS_BADGE.pending;
          const open = selectedId === risk.riskId;
          return (
            <li key={risk.riskId} data-active={open} >
              <button type="button" className="ct-risk-head" onClick={() => onSelectRisk(risk.riskId)} aria-pressed={open}>
                <span className="ct-risk-level" data-tone={risk.level}>
                  {RISK_LEVEL_LABELS[risk.level] ?? risk.level}
                </span>
                <span className="ct-risk-title">
                  <strong>
                    {risk.issue || risk.clauseHeading || `${index+1}. ${riskKindText(risk.kind)}`}
                  </strong>

                </span>
                <span className="ct-badge" data-tone={badge.tone}>
                  <span>{badge.text}</span>
                </span><small className="cx-row-state">{reviewLabel(notes[risk.riskId]?.status)}</small>
              </button>
            </li>
          );
        })}
      </ul>
      {!visibleRisks.length && <p className="ct-card">当前筛选下没有待关注事项，可切换“全部”查看。</p>}
      {selected && (() => { const risk = selected; return (

                <article className="cx-selected-risk"><div className="ct-risk-body"><span className="ct-mini">{risk.clauseHeading || risk.clauseId} · {riskKindText(risk.kind)}</span><h2>{risk.issue || "条款修改建议"}</h2><p className="ct-mini">依据状态：{BASIS_BADGE[risk.basisStatus]?.text ?? "正在核对依据"}</p>
                  <p className="ct-quote">合同里写的是：“{risk.anchorText || "（未能取到原文片段）"}”</p>

                  <p className="ct-why">
                    <b>为什么对你不利：</b>
                    {risk.issue || "（这条没有给出说明）"}
                  </p>

                  <div className="ct-basis">
                    <span className="ct-mini">法律上怎么说</span>
                    {risk.basis.length ? (
                      risk.basis.map(citation => {
                        const source = sources.find(item => item.source_id === citation.source_id);
                        return (
                          <p key={`${citation.source_id}-${citation.identifier}`}>
                            <button type="button" className="ct-cite" onClick={() => openSource(citation.source_id)}>
                              <FileText size={13} aria-hidden="true" />
                              <strong>{citation.identifier || source?.origin_text || "依据"}</strong>
                              <em>{source?.status_text || "未标注核验状态"} · 点开看原文</em>
                            </button>
                          </p>
                        );
                      })
                    ) : (
                      <p className="ct-mini">没找到直接对应的条文，所以这条只作提醒，不当结论。</p>
                    )}
                    {risk.basisStatus === "rejected" && (
                      <p className="ct-blocked">
                        <AlertTriangle size={14} aria-hidden="true" />
                        引用的条文原文没能逐字对上，因此只作提醒，不作结论。
                      </p>
                    )}
                  </div>

                  <div className="ct-suggest"><label htmlFor={`suggestion-${risk.riskId}`}>建议这样改 · 可编辑，须经律师审定</label><textarea id={`suggestion-${risk.riskId}`} className="ct-input" rows={5} maxLength={10000} value={notes[risk.riskId]?.suggestion ?? risk.suggestion ?? ""} onChange={e=>updateNote(risk,notes[risk.riskId]?.status??"pending",e.target.value)}/><div className="ct-actions"><button className="ct-btn ct-btn-primary" disabled={!(notes[risk.riskId]?.suggestion??risk.suggestion??"").trim()} onClick={()=>updateNote(risk,"accept",notes[risk.riskId]?.suggestion??risk.suggestion)}>采纳这条建议</button><button className="ct-btn" onClick={()=>updateNote(risk,"skip",notes[risk.riskId]?.suggestion??risk.suggestion)}>暂不采纳</button>{notes[risk.riskId]?.status && notes[risk.riskId].status!=="pending" && <button className="ct-btn" onClick={()=>updateNote(risk,"pending",notes[risk.riskId].suggestion)}>撤销处理</button>}</div><p className="ct-mini">人工意见会写入报告，不自动修改原合同，也不改变依据核验状态。</p></div>

                  {!risk.locateOk && (
                    <p className="ct-mini">这一条没能在合同原文里定位到位置，所以不会在原文里标黄（宁可不标，也不给错位置）。</p>
                  )}

                </div></article>
              ); })()}

      </section></div>}

      {/* ---------------------------------------------------- 导出：只留这一处 */}
      {!reportMode && exportPanel}

      {/* ---------------------------------------------------- 细节：默认折叠 */}
      <details className="ct-more">
        <summary>
          想看细节（用到了哪些依据 · 核验情况 · 上传的材料）
        </summary>
        <div style={{ marginTop: 14 }}>
          <EvidenceTabs
            sources={sources}
            materials={materials}
            gate={gateReport}
            gaps={gaps}
            synthesis={null}
            conflicts={conflicts}
            onOpenSource={openSource}
            onVerify={onVerify}
            onRoleChange={() => undefined}
            onResolveConflict={() => undefined}
            onUploadFiles={uploadFiles}
            uploading={uploading}
            uploadRejected={uploadRejected}
            maxFiles={maxFiles}
          />
        </div>
      </details>

      <p className="ct-foot">
        <Info size={14} aria-hidden="true" />
        没有提示问题的条款不等于没有风险；建议改法须经律师审定。以上内容不构成法律意见，涉及实际权益请由执业律师复核。
      </p>

      <SourceSheet source={detail} onClose={() => setDetail(null)} />
    </>
  );
}

/** 合同原文：风险位置做成可点高亮；定位失败的（偏移 -1）不参与高亮，绝不标错位置 */
function ContractText({
  text,
  risks,
  activeRiskId,
  onAnchorClick,
}: {
  text: string;
  risks: ContractRisk[];
  activeRiskId: string | null;
  onAnchorClick: (riskId: string) => void;
}) {
  const { segments, unlocated } = segmentContract(text, risks);
  if (!segments.length) {
    return (
      <div className="ct-text">
        <p className="ct-mini">合同正文为空，或暂时取不回来（原文只在当前会话内存中保留）。</p>
      </div>
    );
  }
  return (
    <div className="ct-text">
      {unlocated.length > 0 && (
        <p className="ct-mini" style={{ marginBottom: 12 }}>
          {unlocated.length} 条问题没能定位到原文，所以下面不会标黄（宁可不标，也不给错位置）。
        </p>
      )}
      {Array.from(text.matchAll(/[^\n]+(?:\n|$)/g)).map((match, index) => {
        const start = match.index ?? 0;
        const end = start + match[0].length;
        const parts = segments.filter(segment => segment.end > start && segment.start < end);
        const highlighted = parts.find(segment => segment.riskId);
        const isTitle = index === 0 && match[0].trim().length < 70;
        const content = parts.map(segment => {
          const from = Math.max(start, segment.start), to = Math.min(end, segment.end);
          const value = text.slice(from, to);
          return segment.riskId ? <Anchor key={`${from}-${to}`} riskId={segment.riskId} text={value} active={activeRiskId === segment.riskId} onClick={onAnchorClick}/> : <span key={`${from}-${to}`}>{value}</span>;
        });
        return isTitle ? <h2 className="cx-paper-title" key={start}>{content}</h2> : <p key={start} className="cx-paper-paragraph" data-flagged={Boolean(highlighted)} data-active={Boolean(highlighted && highlighted.riskId === activeRiskId)} data-heading={/^第[一二三四五六七八九十百零0-9]+[条章节]/.test(match[0].trim())}>{content}</p>;
      })}
    </div>
  );
}

function Anchor({
  riskId,
  text,
  active,
  onClick,
}: {
  riskId: string;
  text: string;
  active: boolean;
  onClick: (riskId: string) => void;
}) {
  const node = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!active || !node.current) return;
    const container = node.current.closest(".ct-text");
    if (container) {
      const top = node.current.getBoundingClientRect().top - container.getBoundingClientRect().top + container.scrollTop;
      container.scrollTo?.({top: Math.max(0, top-container.clientHeight/3), behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth"});
    }
  }, [active]);
  return (
    <button
      ref={node}
      type="button"
      className="ct-anchor"
      data-active={active}
      data-risk={riskId}
      onClick={() => onClick(riskId)}
      title="点这里跳到上面那条问题说明"
    >
      {text}
    </button>
  );
}
