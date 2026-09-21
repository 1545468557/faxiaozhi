"use client";

/**
 * 候选池与「确认样本」人工门（阶段 3-2）
 *
 * 红线 2：这个门必须**真的挡住** —— 一篇都没勾时「确认样本」按钮禁用并说明原因，
 * 不允许默认帮用户选中后直接进下一步。
 * 红线 1：每条候选都必须显示来源（用户材料 / 法宝 / 本地依据库 / 补充来源）。
 */

import { Checkbox } from "@/components/ui/checkbox";
import type { Candidate } from "@/lib/api/types";
import {
  canConfirmSample,
  costWarning,
  selectionSummary,
  toggleSelection,
  defaultSelection,
  selectableCandidates,
} from "@/lib/api/research";

export function CandidatePool({
  candidates,
  selection,
  onSelectionChange,
  onConfirm,
  confirming,
  locked,
  confirmedIds,
}: {
  candidates: Candidate[];
  selection: string[];
  onSelectionChange: (next: string[]) => void;
  onConfirm: () => void;
  confirming: boolean;
  locked: boolean;
  confirmedIds: string[];
}) {
  const selectable = selectableCandidates(candidates);
  const summary = selectionSummary(selection, 2);
  const gate = canConfirmSample(selection);
  const warning = costWarning(selection);

  if (!candidates.length) {
    return (
      <div className="fzx-empty">
        <p>候选池目前是空的。</p>
        <p style={{ fontSize: 13 }}>
          检索完成后，后端会把候选材料放在这里；如果确实没有匹配，这里会显示「没匹配到」和放宽条件的建议，
          而不是留白，也不会用示例数据补齐。
        </p>
      </div>
    );
  }

  return (
    <div className="fzx-pool">
      <div className="fzx-pool-head">
        <span className="fzx-pool-count">
          候选 {selectable.length} 篇{locked ? "（样本已确认，仅供查看）" : `，已勾选 ${summary.count} 篇`}
        </span>
        {!locked && (
          <span className="fzx-pool-actions">
            <button type="button" onClick={() => onSelectionChange(selectable.map(item => item.source_id))}>
              全选
            </button>
            <button type="button" onClick={() => onSelectionChange([])}>
              全不选
            </button>
            <button type="button" onClick={() => onSelectionChange(defaultSelection(candidates))}>
              恢复默认（前 {Math.min(5, selectable.length)} 篇）
            </button>
          </span>
        )}
      </div>

      <ul className="fzx-candidates">
        {candidates.map(item => {
          const checked = locked ? confirmedIds.includes(item.source_id) : selection.includes(item.source_id);
          return (
            <li key={item.source_id} className="fzx-candidate" data-locked={locked ? "true" : undefined}>
              <div className="fzx-candidate-row">
                {!locked && (
                  <Checkbox
                    aria-label={`选择 ${item.title || item.identifier}`}
                    checked={checked}
                    onCheckedChange={() => onSelectionChange(toggleSelection(selection, item.source_id))}
                  />
                )}
                <div>
                  <h4>{item.title || item.identifier || "未命名材料"}</h4>
                  <div className="fzx-candidate-meta">
                    <span data-kind={item.origin_text.includes("用户材料") ? "user" : undefined}>{item.origin_text}</span>
                    {item.identifier && <span>{item.identifier}</span>}
                    {item.court && <span>{item.court}</span>}
                    {item.decided_on && <span>{item.decided_on}</span>}
                    {item.supplement && <span>补充来源</span>}
                    {item.identifier_missing && <span data-kind="status">未识别到案号</span>}
                    {item.user_verified ? <span data-kind="user">已核验</span> : null}
                  </div>
                </div>
              </div>
            </li>
          );
        })}
      </ul>

      {warning && (
        <p className="fzx-note" data-tone="warn">
          {warning}
        </p>
      )}

      {!locked && (
        <div className="fzx-gate">
          <div>
            <strong>确认样本（人工门）</strong>
            <p className="fzx-gate-note">{summary.note} 未确认前不会生成对比矩阵，也不会给出结论。</p>
          </div>
          <button type="button" className="primary" onClick={onConfirm} disabled={!gate.ok || confirming} title={gate.reason}>
            {confirming ? "确认中…" : "确认样本"}
          </button>
        </div>
      )}
      {!locked && !gate.ok && <p className="fzx-note" data-tone="warn">{gate.reason}</p>}
    </div>
  );
}
