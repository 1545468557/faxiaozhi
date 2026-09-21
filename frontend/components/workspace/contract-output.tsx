"use client";

/**
 * 合同审查的展示组件（阶段 3-4）
 *
 * 核心交互是**原文 ↔ 风险联动**：左侧原文只读、风险锚点高亮；点风险滚到原文，
 * 点高亮跳回风险。所有位置都来自后端给的 `start`/`end`，定位失败的不给错位置。
 */

import { createContext, useContext, useEffect, useRef } from "react";
import { AlertTriangle, FileText, Info } from "lucide-react";
import type { ContractRisk, SourceBrief } from "@/lib/api/types";
import {
  RISK_LEVEL_LABELS,
  riskKindText,
  segmentContract,
  type ContractSegment,
} from "@/lib/api/contract";

const AnchorContext = createContext<(riskId: string) => void>(() => undefined);

/** 立场卡（硬门）：立场未确认不得开始；选项与甲乙方名称来自后端 */
export function StanceCard({
  filename,
  parties,
  options,
  value,
  onChange,
  onSubmit,
  busy,
  error,
}: {
  filename: string;
  parties: { party_a?: string; party_b?: string };
  options: { value: string; label: string }[];
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  busy: boolean;
  error: string;
}) {
  return (
    <section className="fzx-stance" aria-label="确认审查立场">
      <div className="fzx-monitor-head">
        <h3>确认审查立场（人工门）</h3>
        <span className="fzx-pill" data-tone="await">
          未确认不得继续
        </span>
      </div>
      <p className="fzx-stance-file">
        <FileText size={15} /> {filename || "已上传的合同"}
      </p>
      {(parties.party_a || parties.party_b) && (
        <p className="fzx-mini">
          文本里识别到的当事人：甲方「{parties.party_a || "未识别"}」· 乙方「{parties.party_b || "未识别"}」
        </p>
      )}
      <div className="fzx-stance-options" role="radiogroup" aria-label="我代表哪一方">
        {options.map(option => (
          <label key={option.value} className="fzx-stance-option" data-active={value === option.value}>
            <input
              type="radio"
              name="stance"
              value={option.value}
              checked={value === option.value}
              onChange={() => onChange(option.value)}
            />
            <span>{option.label}</span>
          </label>
        ))}
      </div>
      <p className="fzx-mini">立场不同，风险结论会不同；未确认立场不得进入解析与检索。</p>
      <div className="fzx-actions">
        <button type="button" className="primary" onClick={onSubmit} disabled={!value || busy}>
          {busy ? "正在提交…" : "确认立场并开始审查"}
        </button>
      </div>
      {error && (
        <p className="fzx-note" data-tone="warn">
          {error}
        </p>
      )}
    </section>
  );
}

/** 合同原文（只读）+ 风险高亮锚点 */
export function ContractTextPanel({
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
  return (
    <div className="fzx-contract-text" aria-label="合同原文">
      <div className="fzx-contract-head">
        <b>合同原文（只读）</b>
        <span className="fzx-mini">{unlocated.length ? `${unlocated.length} 处风险未能定位到原文` : "风险位置已标出"}</span>
      </div>
      <AnchorContext.Provider value={onAnchorClick}>
        {segments.map(segment => (
          <ContractSegmentView key={`${segment.start}-${segment.end}`} segment={segment} activeRiskId={activeRiskId} />
        ))}
      </AnchorContext.Provider>
      {!segments.length && <p className="fzx-mini">合同正文为空或无法切分。</p>}
    </div>
  );
}

function ContractSegmentView({ segment, activeRiskId }: { segment: ContractSegment; activeRiskId: string | null }) {
  if (!segment.riskId) return <span>{segment.text}</span>;
  const anchor = segment.riskId;
  return <HighlightedAnchor riskId={anchor} active={activeRiskId === anchor} text={segment.text} />;
}

function HighlightedAnchor({ riskId, text, active }: { riskId: string; text: string; active: boolean }) {
  const onAnchorClick = useContext(AnchorContext);
  const node = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (active) node.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [active]);
  return (
    <button
      ref={node}
      type="button"
      className="fzx-anchor"
      data-active={active}
      data-risk={riskId}
      onClick={() => onAnchorClick(riskId)}
      title="点这里跳到右侧风险说明"
    >
      {text}
    </button>
  );
}

/** 风险清单 */
export function RiskList({
  risks,
  activeRiskId,
  onSelect,
  sources,
  onOpenSource,
}: {
  risks: ContractRisk[];
  activeRiskId: string | null;
  onSelect: (riskId: string) => void;
  sources: SourceBrief[];
  onOpenSource: (sourceId: string) => void;
}) {
  const rowRefs = useRef<Record<string, HTMLLIElement | null>>({});
  useEffect(() => {
    if (activeRiskId) rowRefs.current[activeRiskId]?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [activeRiskId]);

  if (!risks.length) {
    return (
      <div className="fzx-empty">
        <p>还没有风险条目。</p>
        <p style={{ fontSize: 13 }}>
          审查完成前不会有结果；如果模型未产出任何风险，界面会如实写「未识别到风险条款」，
          并提示「未提示风险的条款不等于没有风险」。
        </p>
      </div>
    );
  }

  return (
    <ul className="fzx-risks">
      {risks.map(risk => {
        const level = RISK_LEVEL_LABELS[risk.level] ?? risk.level;
        const verified = risk.basisStatus === "verified";
        return (
          <li
            key={risk.riskId}
            ref={node => {
              rowRefs.current[risk.riskId] = node;
            }}
            className="fzx-risk"
            data-active={activeRiskId === risk.riskId}
            data-level={risk.level}
            data-verified={verified ? "true" : "false"}
          >
            <button type="button" className="fzx-risk-head" onClick={() => onSelect(risk.riskId)}>
              <span className={`fzx-level fzx-level-${risk.level}`}>{level}风险</span>
              <span className="fzx-badge">{riskKindText(risk.kind)}</span>
              <span className="fzx-risk-clause">{risk.clauseHeading || risk.clauseId}</span>
            </button>
            <p className="fzx-risk-issue">{risk.issue}</p>
            <blockquote className="fzx-risk-anchor">原文：「{risk.anchorText}」</blockquote>
            {risk.locateOk ? null : <p className="fzx-mini">这处风险未能定位到原文（不会给你错误的位置）。</p>}
            <div className="fzx-risk-basis">
              {verified && risk.basis.length ? (
                risk.basis.map(citation => {
                  const source = sources.find(item => item.source_id === citation.source_id);
                  return (
                    <button
                      key={`${citation.source_id}-${citation.quote.slice(0, 10)}`}
                      type="button"
                      className="fzx-cite"
                      onClick={() => onOpenSource(citation.source_id)}
                    >
                      <span data-kind={source?.kind === "user_material" ? "user" : undefined}>
                        {source?.origin_text ?? "依据"}
                      </span>
                      <span className="fzx-cite-id">{citation.identifier}</span>
                      <span className="fzx-cite-status">{source?.status_text ?? "未标注核验状态"}</span>
                    </button>
                  );
                })
              ) : (
                <span className="fzx-badge bad">
                  <AlertTriangle size={13} /> {risk.basisStatus === "rejected" ? "依据未通过核验（仅作提示）" : "未找到直接依据（仅作提示）"}
                </span>
              )}
            </div>
            {risk.suggestion ? (
              <p className="fzx-risk-suggestion">
                <Info size={14} /> 建议改法：{risk.suggestion}
              </p>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
