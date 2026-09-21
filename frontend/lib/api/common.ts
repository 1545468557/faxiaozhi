/**
 * 研究 / 咨询共用的展示判定（阶段 3-3 从 research.ts 抽出来）
 */

import type { ExportState, Gap } from "./types";

/** 导出按钮：保留每条阻断原因，仅将已知内部用语转为工作界面文案。 */
export function exportState(state: ExportState | undefined) {
  const labels: Record<string, string> = {
    "尚未生成咨询解答，无法导出。": "尚未生成回答，暂时无法导出。",
    "引用尚未经过门禁核验，无法导出。": "引用核验尚未完成，暂时无法导出。",
  };
  const blockers = (state?.blockers ?? []).map(reason => labels[reason] ?? reason);
  const ready = state?.ready === true && blockers.length === 0;
  return {
    ready,
    disabled: !ready,
    blockers,
    text: ready ? "可以导出。" : "暂时不能导出：",
  };
}

/** 依据缺口合并（后端 gaps + 被降级的结论） */
export function gapList(gaps: Gap[] | undefined, degraded: { text: string }[]): { kind: string; detail: string }[] {
  const fromServer = (gaps ?? []).map(item => ({ kind: item.kind || "缺口", detail: item.detail || "" }));
  const fromDegraded = degraded.map(item => ({
    kind: "证明力不足的结论",
    detail: `${item.text}（引用未通过核验，已降级为依据缺口，不作为结论展示）`,
  }));
  return [...fromServer, ...fromDegraded];
}
