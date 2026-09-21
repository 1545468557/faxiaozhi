/**
 * 类案研究的界面判定逻辑（阶段 3-2）
 *
 * 这里只放**纯函数**：所有"能不能确认""哪些结论不许展示""导出为什么不能点"都由
 * 后端数据推导，界面组件只负责渲染。好处有两个：
 * 1. 五条红线可以直接用单测逐条锁住（不依赖浏览器）；
 * 2. 组件里不会散落判定分支，改口径只改一处。
 */

import type {
  Candidate,
  Conclusion,
  Distribution,
  GateReport,
  MatrixRow,
  SourceBrief,
  Synthesis,
} from "./types";

/** 契约要求：候选池默认预选前 5 篇 */
export const DEFAULT_PRESELECT = 5;
/** 契约要求：超过 10 篇要给花费提醒 */
export const COST_WARNING_THRESHOLD = 10;

/** 只允许勾选"还没被取代"的候选（后端已排除 superseded，这里做双保险） */
export function selectableCandidates(candidates: Candidate[]): Candidate[] {
  return candidates.filter(item => !(item as Candidate & { superseded?: boolean }).superseded);
}

export function defaultSelection(candidates: Candidate[]): string[] {
  return selectableCandidates(candidates)
    .slice(0, DEFAULT_PRESELECT)
    .map(item => item.source_id);
}

export function toggleSelection(selection: string[], sourceId: string): string[] {
  return selection.includes(sourceId)
    ? selection.filter(id => id !== sourceId)
    : [...selection, sourceId];
}

/** 勾选状态提示：数量 + 综合结论最低样本数（后端 min_sample_for_conclusion，默认 2） */
export function selectionSummary(selection: string[], minSample = 2) {
  const count = selection.length;
  return {
    count,
    minSample,
    enough: count >= minSample,
    /** 综合结论至少需要 minSample 篇；只选 1 篇时后端只给单案要素 */
    note:
      count === 0
        ? "请至少勾选 1 篇候选材料。"
        : count < minSample
          ? `已选 ${count} 篇：可以确认，但少于 ${minSample} 篇不会生成综合结论。`
          : `已选 ${count} 篇。`,
  };
}

/** 花费提醒：勾选过多时提示可能产生更多检索/模型调用 */
export function costWarning(selection: string[]): string | null {
  if (selection.length <= COST_WARNING_THRESHOLD) return null;
  return `已勾选 ${selection.length} 篇（超过 ${COST_WARNING_THRESHOLD} 篇）：逐案提炼会消耗更多模型调用，费用与耗时都会上升。`;
}

/** 人工门 1：未勾选任何候选时**不允许**确认（按钮禁用 + 说明原因） */
export function canConfirmSample(selection: string[]): { ok: boolean; reason: string } {
  if (selection.length === 0) {
    return { ok: false, reason: "请先勾选至少 1 篇候选材料，才能确认样本。" };
  }
  return { ok: true, reason: "" };
}

/** 确认样本的请求体：confirmed = 勾选的，excluded = 候选里没勾的（都不是"排除样本残留"） */
export function confirmPayload(
  selection: string[],
  candidates: Candidate[],
): { confirmed: string[]; excluded: string[] } {
  const selectable = selectableCandidates(candidates);
  const chosen = new Set(selection);
  return {
    confirmed: selectable.filter(item => chosen.has(item.source_id)).map(item => item.source_id),
    excluded: selectable.filter(item => !chosen.has(item.source_id)).map(item => item.source_id),
  };
}

/** 样本门当前状态：等还是不等（awaiting_checkpoint 不能显示成"加载中"） */
export function sampleGateState(params: {
  locked: boolean;
  confirmed: string[];
  candidates: Candidate[];
  awaitingCheckpoint: boolean;
}) {
  const { locked, confirmed, candidates, awaitingCheckpoint } = params;
  if (locked) {
    return { status: "locked" as const, text: `样本已确认（${confirmed.length} 篇）` };
  }
  if (awaitingCheckpoint || candidates.length > 0) {
    return {
      status: "awaiting" as const,
      text: candidates.length
        ? `等待你确认样本（候选 ${candidates.length} 篇）`
        : "等待检索返回候选材料",
    };
  }
  return { status: "idle" as const, text: "尚未开始检索" };
}

/**
 * 红线 4：只展示通过核验的结论。
 * 后端把被降级的结论原文放在 `gate_report.degraded_texts` 里；这里据此过滤。
 */
export function splitConclusions(
  synthesis: Synthesis | null,
  gate: GateReport | null,
): { shown: Conclusion[]; degraded: Conclusion[] } {
  const all = Array.isArray(synthesis?.conclusions) ? synthesis!.conclusions! : [];
  const degradedTexts = new Set(gate?.degraded_texts ?? []);
  const shown: Conclusion[] = [];
  const degraded: Conclusion[] = [];
  for (const item of all) {
    if (degradedTexts.has(item.text)) degraded.push(item);
    else shown.push(item);
  }
  return { shown, degraded };
}

/** 结论的引用 → 依据池里的来源（用于点开看来源与核验状态） */
export function resolveCitations(conclusion: Conclusion, sources: SourceBrief[]): SourceBrief[] {
  const ids = new Set([
    ...(conclusion.citation_source_ids ?? []),
    ...(conclusion.citations ?? []).map(item => item.source_id),
  ]);
  return sources.filter(item => ids.has(item.source_id));
}

/** 门禁报告摘要（数字 + 被拦下的规则明细，供「门禁报告」页签） */
export function gateSummary(gate: GateReport | null) {
  if (!gate) {
    return {
      ready: false as const,
      text: "尚未生成门禁报告。",
      accepted: 0,
      rejected: 0,
      guardHits: 0,
      coverage: 0,
      rules: [] as string[],
      details: [] as GateReport["details"],
    };
  }
  const rules = [...new Set((gate.details ?? []).map(item => item.rule))].sort();
  return {
    ready: true as const,
    accepted: gate.accepted,
    rejected: gate.rejected,
    guardHits: gate.guard_hits,
    coverage: gate.coverage,
    rules,
    text:
      `通过引用 ${gate.accepted} 条 · 拦截 ${gate.rejected} 条 · 越界表述 ${gate.guard_hits} 处 · ` +
      `证据覆盖率 ${Math.round((gate.coverage ?? 0) * 100)}%`,
    /** 被拦下的引用只能进「依据缺口」，不得当结论展示 */
    details: gate.details ?? [],
  };
}

/** 矩阵视图：列顺序固定，来源列放最后（宽表要能横向滚动） */
export function matrixView(rows: MatrixRow[]) {
  const columns = rows.length ? Object.keys(rows[0]) : [];
  return {
    columns,
    rows,
    /** 表格在窄屏要允许横向滚动，不能被内容撑破 */
    widthClass: "fzx-table-wrap",
  };
}

export function distributionText(distribution: Distribution | Record<string, never> | null): string {
  const text = (distribution as Distribution | null)?.text;
  return typeof text === "string" && text ? text : "";
}

// 导出前置条件与依据缺口是研究/咨询共用的判定，实现在 common.ts（这里转出，调用方不用改）
export { exportState, gapList } from "./common";

/** 来源归类：用户材料 / 法宝 / 本地依据库 / 补充来源（红线 1：必须标出来源） */
export function sourceKind(item: SourceBrief): "user" | "mcp" | "local" | "fixture" {
  if (item.kind === "user_material" || item.origin === "user") return "user";
  if (item.origin === "local") return "local";
  if (item.origin === "fixture") return "fixture";
  return "mcp";
}
