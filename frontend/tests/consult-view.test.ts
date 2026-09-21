/**
 * 法律咨询的界面判定（阶段 3-3）
 *
 * 锁住三件事：
 * - 追问：轮数用**后端计数**，文案必须是「第 N 轮 / 最多 M 轮」；
 * - 解答四块：结论 / 引用 / 不确定与风险 / 建议的下一步；
 * - 红线 4：只展示 `consult.passed` 里的结论。
 */

import { describe, expect, it } from "vitest";
import {
  answerSections,
  assumptionNotice,
  clarificationHint,
  clarifyState,
  consultExportBlockers,
  resolveConsultCitations,
  roundsText,
  splitAnswer,
} from "@/lib/api/consult";
import type { SourceBrief } from "@/lib/api/types";
import type { ConsultAnswer, ConsultState } from "@/lib/api/consult";

const answer: ConsultAnswer = {
  conclusions: [
    { text: "可以主张返还押金。", citation_source_ids: ["s1"], citations: [{ source_id: "s1", identifier: "《民法典》第七百三十三条", quote: "承租人……" }] },
    { text: "一定能赢，全国支持率 87%。", citation_source_ids: ["s1"], citations: [{ source_id: "s1", identifier: "x", quote: "bad" }] },
  ],
  uncertainties: ["押金是否已被部分抵扣的事实尚不清楚。"],
  next_steps: ["整理交接单与聊天记录。", "向当地法律援助中心咨询。"],
  insufficient: "",
};

describe("追问进度（轮数由代码计）", () => {
  it("没追问过时说明上限", () => {
    expect(roundsText({ rounds: 0, max_rounds: 3 })).toContain("最多可追问 3 轮");
  });

  it("追问中显示第几轮与剩余轮数", () => {
    const text = roundsText({ rounds: 2, max_rounds: 3 });
    expect(text).toContain("第 2 轮 / 最多 3 轮");
    expect(clarificationHint({ rounds: 2, max_rounds: 3 })).toContain("最多再补充 1 轮");
  });

  it("缺字段时按后端默认上限 3 轮显示，不显示 NaN", () => {
    expect(roundsText({})).toContain("最多可追问 3 轮");
    expect(roundsText(undefined)).not.toContain("NaN");
  });

  it("awaiting=true 时给出追问卡（问题来自后端）", () => {
    const consult: ConsultState = {
      awaiting: true,
      rounds: 1,
      max_rounds: 3,
      issue: { clarify_questions: ["金额是多少？", "有没有书面约定？"] },
    };
    const state = clarifyState(consult, false);
    expect(state.awaiting).toBe(true);
    expect(state.questions).toEqual(["金额是多少？"]);
    expect(state.text).toContain("第 1 轮");
  });

  it("SSE 说在追问时也显示追问卡（两种来源任一即可）", () => {
    expect(clarifyState({ rounds: 1, max_rounds: 3 }, true).awaiting).toBe(true);
  });

  it("没有追问需求时不显示追问卡", () => {
    expect(clarifyState({ rounds: 0, max_rounds: 3, issue: {} }, false).awaiting).toBe(false);
  });

  it("空问题被过滤，不出现空白项", () => {
    const state = clarifyState({ awaiting: true, issue: { clarify_questions: ["", "有效问题"] } }, false);
    expect(state.questions).toEqual(["有效问题"]);
  });

  it("追问到上限时提示基于假设作答，并列出假设", () => {
    const notice = assumptionNotice({
      rounds: 3,
      max_rounds: 3,
      issue: { assumptions: ["假设双方没有书面约定", "假设押金已全额支付"] },
    });
    expect(notice).toContain("以下回答基于这些尚待确认的情况：");
    expect(notice).toContain("假设双方没有书面约定");
  });

  it("没到上限或没有假设就不提示", () => {
    expect(assumptionNotice({ rounds: 1, max_rounds: 3, issue: { assumptions: ["x"] } })).toBeNull();
    expect(assumptionNotice({ rounds: 3, max_rounds: 3, issue: { assumptions: [] } })).toBeNull();
  });
});

describe("动态追问与跳过说明", () => {
  it("最后一轮不再暗示还要补充，也不预先承诺固定总轮次", () => {
    expect(clarificationHint({ rounds: 3, max_rounds: 3 })).toContain("最后一轮");
    expect(clarificationHint({ rounds: 1, max_rounds: 3 })).toContain("信息够用就会直接解答");
    expect(roundsText({ rounds: 1, max_rounds: 3 })).not.toContain("共 3 轮");
  });

  it("用户提前跳过时照样披露未知信息，不谎称达到上限", () => {
    const notice = assumptionNotice({ rounds: 1, max_rounds: 3, skipped_clarification: true, assumptions: ["购买渠道尚未确认"] });
    expect(notice).toContain("你已跳过补充");
    expect(notice).toContain("购买渠道尚未确认");
    expect(notice).not.toContain("上限");
    expect(assumptionNotice({ skipped_clarification: true })).toContain("部分情况尚未确认");
  });
});

describe("红线 4：只展示通过核验的结论", () => {
  it("未通过核验的结论不出现在结论区", () => {
    const { shown, degraded } = splitAnswer(answer, ["可以主张返还押金。"]);
    expect(shown.map(item => item.text)).toEqual(["可以主张返还押金。"]);
    expect(degraded.map(item => item.text)).toEqual(["一定能赢，全国支持率 87%。"]);
  });

  it("passed 为空时结论区为空（且必须解释原因，不是留白）", () => {
    const sections = answerSections(answer, []);
    expect(sections.empty).toBe(true);
    expect(sections.conclusions).toHaveLength(0);
    expect(sections.degraded).toHaveLength(2);
  });

  it("后端没给 passed 字段时按空处理，不误展示", () => {
    expect(splitAnswer(answer, undefined).shown).toHaveLength(0);
  });
});

describe("四块解答", () => {
  const sections = answerSections(answer, ["可以主张返还押金。"]);

  it("引用是从通过的结论里汇总出来的", () => {
    expect(sections.citations).toHaveLength(1);
    expect(sections.citations[0].identifier).toContain("民法典");
  });

  it("不确定与风险来自 uncertainties", () => {
    expect(sections.uncertainties[0]).toContain("押金是否已被部分抵扣");
  });

  it("建议的下一步来自 next_steps", () => {
    expect(sections.nextSteps).toHaveLength(2);
  });

  it("insufficient 会并入「不确定与风险」并优先展示", () => {
    const withInsufficient = answerSections(
      { ...answer, insufficient: "本次未检索到足够依据，不能据此判断。" },
      ["可以主张返还押金。"],
    );
    expect(withInsufficient.insufficient).toContain("未检索到足够依据");
  });

  it("完全没有解答时不显示四块（hasAnswer=false）", () => {
    expect(answerSections(undefined, undefined).hasAnswer).toBe(false);
  });

  it("空字符串项被过滤，不渲染空行", () => {
    const cleaned = answerSections({ conclusions: [], uncertainties: ["", "  "], next_steps: [""] }, []);
    expect(cleaned.uncertainties).toEqual([]);
    expect(cleaned.nextSteps).toEqual([]);
  });
});

describe("引用回溯", () => {
  const source = (id: string): SourceBrief =>
    ({
      source_id: id,
      kind: "statute",
      identifier: "《民法典》第七百三十三条",
      title: "",
      court: "",
      level: "",
      region: "",
      decided_on: "",
      status: "ok",
      origin: "mcp",
      origin_text: "法宝",
      supplement: false,
      user_verified: false,
      identifier_missing: false,
      corroboration: "dual",
      status_text: "已取得完整原文",
      effective_status: "现行有效",
      uri: null,
      local_hit: false,
      corroboration_text: "双源一致（法宝 + 本地依据库）",
      superseded: false,
      manual_override: false,
      locator: {},
      quote: "承租人……",
      synthetic: false,
    }) as SourceBrief;

  it("能按 source_id 映射回依据池", () => {
    const resolved = resolveConsultCitations([{ sourceId: "s1" }], [source("s1"), source("s2")]);
    expect(resolved.map(item => item.source_id)).toEqual(["s1"]);
  });

  it("找不到来源时不虚构，返回空列表", () => {
    expect(resolveConsultCitations([{ sourceId: "nope" }], [source("s1")])).toEqual([]);
  });
});

describe("咨询导出被拦的原因（原样展示）", () => {
  it("没有解答时后端会给出原因", () => {
    expect(consultExportBlockers(["尚未生成咨询解答，无法导出。"])).toEqual(["尚未生成咨询解答，无法导出。"]);
  });

  it("引用未通过核验时原样列出", () => {
    const blockers = ["存在 1 条未通过核验的引用（规则 R5）：引用原文与来源不一致，无法导出。"];
    expect(consultExportBlockers(blockers)[0]).toContain("R5");
  });

  it("没有原因时返回空数组（界面据此提示「暂时没有可导出的内容」）", () => {
    expect(consultExportBlockers(undefined)).toEqual([]);
  });
});
