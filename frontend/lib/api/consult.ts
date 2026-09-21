/**
 * 法律咨询的界面判定（阶段 3-3）
 *
 * 规则全部来自后端（`state.consult`），前端只做呈现判定：
 * - 追问：**轮数由代码计数**，界面显示「第 N 轮 / 最多 M 轮」，到上限才基于假设作答；
 * - 解答四块：结论 / 引用 / 不确定与风险 / 建议的下一步；
 * - **只展示通过核验的结论**（`consult.passed`），未通过的不当结论展示。
 */

import type { SourceBrief } from "./types";

export type ConsultIssue = {
  legal_relation?: string;
  disputes?: string[];
  given_facts?: string[];
  missing_facts?: string[];
  possible_claims?: string[];
  need_clarify?: boolean;
  clarify_questions?: string[];
  assumptions?: string[];
};

export type ConsultConclusion = {
  text: string;
  citation_source_ids?: string[];
  citations?: { source_id: string; identifier: string; quote: string }[];
};

export type ConsultAnswer = {
  conclusions?: ConsultConclusion[];
  uncertainties?: string[];
  next_steps?: string[];
  insufficient?: string;
};

export type ConsultState = {
  facts?: string[];
  asked?: string[];
  rounds?: number;
  max_rounds?: number;
  status?: string;
  awaiting?: boolean;
  skipped_clarification?: boolean;
  issue?: ConsultIssue;
  assumptions?: string[];
  answer?: ConsultAnswer;
  passed?: string[];
};

/** 追问进度文案：轮数由后端计数，界面不许自己估 */
export function roundsText(consult: ConsultState | undefined): string {
  const used = consult?.rounds ?? 0;
  const max = consult?.max_rounds ?? 3;
  if (!used) return `最多可追问 ${max} 轮，还没有追问过。`;
  return `第 ${used} 轮 / 最多 ${max} 轮`;
}

/** 动态追问不能预先承诺固定总轮数；显示上限与可能提前结束。 */
export function clarificationHint(consult: ConsultState | undefined): string {
  const remaining = Math.max(0, (consult?.max_rounds ?? 3) - (consult?.rounds ?? 0));
  return remaining
    ? `回答后最多再补充 ${remaining} 轮；信息够用就会直接解答。`
    : "这是最后一轮补充，回答后将根据已有信息给出解答。";
}

/** 是否需要展示追问卡（awaiting=true 才是真的要用户回答） */
export function clarifyState(
  consult: ConsultState | undefined,
  runAwaiting: boolean,
): { awaiting: boolean; questions: string[]; text: string } {
  const awaiting = Boolean(consult?.awaiting) || runAwaiting;
  const questions = (consult?.issue?.clarify_questions ?? []).filter(item => item.trim()).slice(0, 1);
  return { awaiting, questions, text: roundsText(consult) };
}

/** 追问到上限 → 基于假设作答，假设必须写明（不许闷着猜） */
export function assumptionNotice(consult: ConsultState | undefined): string | null {
  const rounds = consult?.rounds ?? 0;
  const max = consult?.max_rounds ?? 3;
  const assumptions = (consult?.assumptions ?? consult?.issue?.assumptions ?? []).filter(Boolean);
  if (consult?.skipped_clarification) {
    const missing = assumptions.map(item => item.replace(/^尚未确认（若补充事实已说明则以补充为准）：/u, ""));
    return "你已跳过补充，以下回答基于现有信息。"
      + (missing.length ? `尚待确认（已补充的情况以你的说明为准）：${missing.join("；")}` : "部分情况尚未确认，回答可能需要随补充信息调整。");
  }
  if (rounds < max || !assumptions.length) return null;
  return `以下回答基于这些尚待确认的情况：${assumptions.join("；")}`;
}

/**
 * 只展示通过门禁的结论（红线 4）。
 * 后端把通过的那批结论原文放在 `consult.passed` 里。
 */
export function splitAnswer(
  answer: ConsultAnswer | undefined,
  passed: string[] | undefined,
): { shown: ConsultConclusion[]; degraded: ConsultConclusion[] } {
  const all = answer?.conclusions ?? [];
  const passedSet = new Set(passed ?? []);
  const shown: ConsultConclusion[] = [];
  const degraded: ConsultConclusion[] = [];
  for (const item of all) {
    if (passedSet.has(item.text)) shown.push(item);
    else degraded.push(item);
  }
  return { shown, degraded };
}

/** 解答四块（结论 / 引用 / 不确定与风险 / 建议的下一步） */
export function answerSections(answer: ConsultAnswer | undefined, passed: string[] | undefined) {
  const { shown, degraded } = splitAnswer(answer, passed);
  const citations: { identifier: string; quote: string; sourceId: string }[] = [];
  for (const item of shown) {
    for (const citation of item.citations ?? []) {
      citations.push({
        identifier: citation.identifier || citation.source_id,
        quote: citation.quote || "",
        sourceId: citation.source_id,
      });
    }
  }
  return {
    conclusions: shown,
    degraded,
    citations,
    uncertainties: (answer?.uncertainties ?? []).filter(item => String(item).trim()),
    nextSteps: (answer?.next_steps ?? []).filter(item => String(item).trim()),
    insufficient: (answer?.insufficient ?? "").trim(),
    hasAnswer: Boolean(answer),
    /** 结论区是否为空：空的时候必须解释原因，而不是留白 */
    empty: shown.length === 0,
  };
}

/** 引用能映射回依据池（点开看来源与核验状态） */
export function resolveConsultCitations(
  citations: { sourceId: string }[],
  sources: SourceBrief[],
): SourceBrief[] {
  const ids = new Set(citations.map(item => item.sourceId));
  return sources.filter(item => ids.has(item.source_id));
}

/** 咨询出口状态：没解答 / 引用没过 / 有冲突 / 越界表述都会拦住导出 */
export function consultExportBlockers(blockers: string[] | undefined): string[] {
  return blockers ?? [];
}
