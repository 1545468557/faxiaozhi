/**
 * 合同审查的界面判定（阶段 3-4）
 *
 * 三条口径全部在这里落地（组件只渲染）：
 * - **立场硬门**：立场未确认不得开始；选项与甲乙方名称来自后端 `checkpoint` 事件；
 * - **原文 ↔ 风险联动**：由后端给的 `start`/`end` 偏移切分原文（定位失败的不给错位置）；
 * - **有依据 vs 提示性**：`basisStatus` 为 rejected 的风险**不得**当作"有依据的风险"输出。
 */

import type { ContractRisk, ContractSnapshot, ContractClause } from "./types";

export const RISK_LEVEL_LABELS: Record<string, string> = { high: "高", medium: "中", low: "低" };
export const RISK_KIND_LABELS: Record<string, string> = {
  illegal: "违法",
  commercial: "商业不利",
  wording: "措辞不清",
};
const LEVEL_ORDER: Record<string, number> = { high: 0, medium: 1, low: 2 };

export const STANCE_FALLBACK_LABELS: Record<string, string> = {
  party_a: "甲方（委托方 / 买方 / 出租方一侧）",
  party_b: "乙方（受托方 / 卖方 / 承租方一侧）",
  neutral: "中立第三方（同时看双方风险）",
};

export function riskLevelText(level: string): string {
  return RISK_LEVEL_LABELS[level] ?? level;
}

export function riskKindText(kind: string): string {
  return RISK_KIND_LABELS[kind] ?? kind;
}

/** 立场门：未确认立场 → 不许开始（红线 2） */
export function stanceGate(
  contract: Partial<ContractSnapshot> | undefined,
  checkpoint: { payload?: Record<string, unknown> } | null,
): {
  awaiting: boolean;
  confirmed: boolean;
  label: string;
  parties: { party_a?: string; party_b?: string };
  options: { value: string; label: string }[];
  filename: string;
} {
  const payload = (checkpoint?.payload ?? {}) as {
    parties?: { party_a?: string; party_b?: string };
    options?: { value: string; label: string }[];
    filename?: string;
  };
  const stance = contract?.stance ?? "";
  const options = payload.options?.length
    ? payload.options
    : Object.entries(STANCE_FALLBACK_LABELS).map(([value, label]) => ({ value, label }));
  return {
    awaiting: !stance,
    confirmed: Boolean(stance),
    label: stance ? (contract?.stanceLabel || STANCE_FALLBACK_LABELS[stance] || stance) : "",
    parties: { ...(payload.parties ?? {}), ...(contract?.parties ?? {}) },
    options,
    filename: payload.filename || contract?.filename || "",
  };
}

/** 想选中立但还没选时，按钮必须禁用（不给默认值） */
export function canSubmitStance(value: string): { ok: boolean; reason: string } {
  if (!value) return { ok: false, reason: "请先选择你代表哪一方，才能开始审查。" };
  return { ok: true, reason: "" };
}

/** 风险清单：按等级排序（高 → 中 → 低），定位失败的排后面 */
export function sortRisks(risks: ContractRisk[]): ContractRisk[] {
  return [...risks].sort((a, b) => {
    const level = (LEVEL_ORDER[a.level] ?? 9) - (LEVEL_ORDER[b.level] ?? 9);
    if (level !== 0) return level;
    return Number(b.locateOk ? 1 : 0) - Number(a.locateOk ? 1 : 0);
  });
}

/**
 * 红线 4：依据未通过核验（`basisStatus === "rejected"`）的风险**不能**作为"有依据的风险"展示，
 * 只能降级成「提示性风险」；`no_basis` 同样只作提示。
 */
export function splitRisks(risks: ContractRisk[]): {
  based: ContractRisk[];
  provisional: ContractRisk[];
} {
  const based: ContractRisk[] = [];
  const provisional: ContractRisk[] = [];
  for (const risk of risks) {
    if (risk.basisStatus === "verified") based.push(risk);
    else provisional.push(risk);
  }
  return { based, provisional };
}

/** 风险数量摘要（用后端 counts，界面不自己数，避免口径漂移） */
export function riskSummary(contract: Partial<ContractSnapshot> | undefined) {
  const counts = contract?.counts ?? {};
  const byLevel = (counts.by_level ?? {}) as Record<string, number>;
  const total = Number(counts.total ?? 0);
  const parts = (["high", "medium", "low"] as const)
    .map(level => (byLevel[level] ? `${byLevel[level]} 项${RISK_LEVEL_LABELS[level]}风险` : ""))
    .filter(Boolean);
  return {
    total,
    text: total ? `${total} 项待关注条款（${parts.join(" · ") || "未分级"}）` : "未识别到风险条款",
    /** 各等级条数：口径同样来自后端 counts，界面不自己数 */
    byLevel: {
      high: Number(byLevel.high ?? 0),
      medium: Number(byLevel.medium ?? 0),
      low: Number(byLevel.low ?? 0),
    },
    verified: Number(counts.verified ?? 0),
    noBasis: Number(counts.no_basis ?? 0),
    locateFailed: Number(counts.locate_failed ?? 0),
  };
}

/**
 * 原文 ↔ 风险联动：把合同原文按风险的 `start`/`end` 切成片段序列。
 * 定位失败（`locateOk=false` / 偏移为 -1）的风险**不参与高亮**，也不给错位置。
 */
export type ContractSegment = { text: string; riskId?: string; start: number; end: number };

export function segmentContract(
  text: string,
  risks: ContractRisk[],
): { segments: ContractSegment[]; unlocated: ContractRisk[] } {
  const body = text ?? "";
  const spans: { riskId: string; start: number; end: number }[] = [];
  const unlocated: ContractRisk[] = [];
  for (const risk of risks) {
    const start = Number(risk.start ?? -1);
    const end = Number(risk.end ?? -1);
    if (!risk.locateOk || start < 0 || end <= start || end > body.length) {
      unlocated.push(risk);
      continue;
    }
    spans.push({ riskId: risk.riskId, start, end });
  }
  spans.sort((a, b) => a.start - b.start);

  const segments: ContractSegment[] = [];
  let cursor = 0;
  for (const span of spans) {
    if (span.start < cursor) continue; // 重叠的高亮只保留第一个，避免片段错乱
    if (span.start > cursor) segments.push({ text: body.slice(cursor, span.start), start: cursor, end: span.start });
    segments.push({ text: body.slice(span.start, span.end), riskId: span.riskId, start: span.start, end: span.end });
    cursor = span.end;
  }
  if (cursor < body.length) segments.push({ text: body.slice(cursor), start: cursor, end: body.length });
  return { segments, unlocated };
}

/** 点风险 → 找它对应的原文片段（用于滚动与高亮） */
export function anchorFor(risk: ContractRisk, segments: ContractSegment[]): ContractSegment | null {
  return segments.find(segment => segment.riskId === risk.riskId) ?? null;
}

/** 条款列表（后端切块结果，含行号与偏移） */
export function clauseList(contract: Partial<ContractSnapshot> | undefined): ContractClause[] {
  return contract?.clauses ?? [];
}

/** 客户端侧的长度对不上等异常要能被发现（不静默给出错位置） */
export function offsetsSane(text: string, risks: ContractRisk[]): boolean {
  return risks.every(risk => {
    if (!risk.locateOk) return true;
    const start = Number(risk.start ?? -1);
    const end = Number(risk.end ?? -1);
    return start >= 0 && end <= text.length && end > start;
  });
}
