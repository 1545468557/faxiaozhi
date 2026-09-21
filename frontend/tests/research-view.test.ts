/**
 * 类案研究的界面判定（阶段 3-2）
 *
 * 这些测试锁的是**红线**，不是实现细节：
 * - 人工门（未确认样本不能进下一步）；
 * - 红线 4（被拦下的引用不进结论，降级为「依据缺口」）；
 * - 导出未就绪时禁用并把原因原文列出；
 * - 来源必须能标出来（用户材料 / 法宝 / 本地依据库）。
 */

import { describe, expect, it } from "vitest";
import {
  COST_WARNING_THRESHOLD,
  DEFAULT_PRESELECT,
  canConfirmSample,
  confirmPayload,
  costWarning,
  defaultSelection,
  distributionText,
  exportState,
  gapList,
  gateSummary,
  matrixView,
  resolveCitations,
  sampleGateState,
  selectableCandidates,
  selectionSummary,
  sourceKind,
  splitConclusions,
  toggleSelection,
} from "@/lib/api/research";
import type { Candidate, Conclusion, GateReport, SourceBrief, Synthesis } from "@/lib/api/types";

const candidate = (id: string, extra: Partial<Candidate> = {}): Candidate => ({
  source_id: id,
  identifier: `（2023）示例民终${id}号`,
  title: `示例案例 ${id}`,
  court: "示例法院",
  level: "",
  region: "",
  decided_on: "2023-05-01",
  status: "ok",
  origin: "mcp",
  origin_text: "法宝",
  supplement: false,
  user_verified: false,
  identifier_missing: false,
  corroboration: "dual",
  ...extra,
});

const candidates = Array.from({ length: 12 }, (_, index) => candidate(`s${index + 1}`));

describe("候选池勾选", () => {
  it("默认预选前 5 篇（契约要求）", () => {
    const selection = defaultSelection(candidates);
    expect(selection).toHaveLength(DEFAULT_PRESELECT);
    expect(selection[0]).toBe("s1");
  });

  it("已被新版取代的候选不可勾选", () => {
    const list = [candidate("s1"), { ...candidate("s2"), superseded: true } as Candidate];
    expect(selectableCandidates(list).map(item => item.source_id)).toEqual(["s1"]);
    expect(defaultSelection(list)).toEqual(["s1"]);
  });

  it("勾选与取消勾选", () => {
    expect(toggleSelection([], "s1")).toEqual(["s1"]);
    expect(toggleSelection(["s1", "s2"], "s1")).toEqual(["s2"]);
  });

  it("少于 2 篇时给出「不会有综合结论」的提示，但允许确认", () => {
    const summary = selectionSummary(["s1"]);
    expect(summary.enough).toBe(false);
    expect(summary.note).toContain("不会生成综合结论");
    expect(canConfirmSample(["s1"]).ok).toBe(true);
  });

  it("超过 10 篇给花费提醒", () => {
    const many = Array.from({ length: COST_WARNING_THRESHOLD + 1 }, (_, index) => `s${index}`);
    expect(costWarning(many)).toContain("费用与耗时都会上升");
    expect(costWarning(["s1", "s2"])).toBeNull();
  });
});

describe("人工门：确认样本", () => {
  it("一篇都没勾时不允许确认，并说明原因", () => {
    const gate = canConfirmSample([]);
    expect(gate.ok).toBe(false);
    expect(gate.reason).toContain("请先勾选");
  });

  it("确认请求体：勾选的进 confirmed，其余进 excluded", () => {
    const payload = confirmPayload(["s2", "s3"], candidates.slice(0, 4));
    expect(payload.confirmed).toEqual(["s2", "s3"]);
    expect(payload.excluded).toEqual(["s1", "s4"]);
  });

  it("确认请求体不会包含非候选的来源（防伪造）", () => {
    const payload = confirmPayload(["s9", "s404"], candidates.slice(0, 3));
    expect(payload.confirmed).toEqual([]);
    expect(payload.excluded).toEqual(["s1", "s2", "s3"]);
  });

  it("等待确认时状态是「等待你确认」，不是加载中", () => {
    const state = sampleGateState({ locked: false, confirmed: [], candidates, awaitingCheckpoint: true });
    expect(state.status).toBe("awaiting");
    expect(state.text).toContain("等待你确认");
  });

  it("已确认后状态为 locked 并显示篇数", () => {
    const state = sampleGateState({ locked: true, confirmed: ["s1", "s2"], candidates, awaitingCheckpoint: false });
    expect(state.status).toBe("locked");
    expect(state.text).toContain("2 篇");
  });

  it("还没有候选时是「尚未开始检索」而不是报错", () => {
    const state = sampleGateState({ locked: false, confirmed: [], candidates: [], awaitingCheckpoint: false });
    expect(state.status).toBe("idle");
  });
});

describe("红线 4：被拦下的引用不进结论", () => {
  const synthesis: Synthesis = {
    conclusions: [
      { text: "买受人可主张减少价款。", citation_source_ids: ["s1"], citations: [{ source_id: "s1", identifier: "A", quote: "q" }] },
      { text: "买受人可主张三倍赔偿。", citation_source_ids: ["s1"], citations: [{ source_id: "s1", identifier: "A", quote: "bad" }] },
    ],
  };
  const gate: GateReport = {
    accepted: 1,
    rejected: 1,
    guard_hits: 0,
    coverage: 0.5,
    demo_mode: false,
    details: [{ rule: "R5", reason: "引用原文与来源不一致", identifier: "A", quote: "bad" }],
    gaps: ["结论「买受人可主张三倍赔偿。…」存在未通过核验的引用"],
    degraded_texts: ["买受人可主张三倍赔偿。"],
  };

  it("只有通过核验的结论出现在结论区", () => {
    const { shown, degraded } = splitConclusions(synthesis, gate);
    expect(shown.map(item => item.text)).toEqual(["买受人可主张减少价款。"]);
    expect(degraded.map(item => item.text)).toEqual(["买受人可主张三倍赔偿。"]);
  });

  it("被降级的结论进入「依据缺口」，并写明原因", () => {
    const { degraded } = splitConclusions(synthesis, gate);
    const gaps = gapList(gate.gaps.map(detail => ({ kind: "citation", detail })), degraded);
    expect(gaps).toHaveLength(2);
    expect(gaps[1].kind).toBe("证明力不足的结论");
    expect(gaps[1].detail).toContain("不作为结论展示");
  });

  it("没有门禁报告时不做过滤（此时导出本来也被拦）", () => {
    const { shown, degraded } = splitConclusions(synthesis, null);
    expect(shown).toHaveLength(2);
    expect(degraded).toHaveLength(0);
  });

  it("后端未提供 degraded_texts 时按空处理，不误删结论", () => {
    const partial = { ...gate, degraded_texts: undefined as unknown as string[] };
    expect(splitConclusions(synthesis, partial).shown).toHaveLength(2);
  });
});

describe("门禁报告摘要", () => {
  it("未生成报告时如实说明", () => {
    expect(gateSummary(null).ready).toBe(false);
    expect(gateSummary(null).text).toContain("尚未生成");
  });

  it("有报告时给出数字、规则集合与覆盖率百分比", () => {
    const summary = gateSummary({
      accepted: 8,
      rejected: 2,
      guard_hits: 1,
      coverage: 0.8,
      demo_mode: false,
      details: [
        { rule: "R5", reason: "x" },
        { rule: "R2", reason: "y" },
      ],
      gaps: [],
      degraded_texts: [],
    });
    expect(summary.accepted).toBe(8);
    expect(summary.rules).toEqual(["R2", "R5"]);
    expect(summary.text).toContain("80%");
  });

  it("被拦明细保留规则与原因，供界面展示", () => {
    const summary = gateSummary({
      accepted: 1,
      rejected: 1,
      guard_hits: 0,
      coverage: 1,
      demo_mode: false,
      details: [{ rule: "R6", reason: "越权依据", identifier: "法条A" }],
      gaps: [],
      degraded_texts: [],
    });
    expect(summary.details[0].rule).toBe("R6");
  });
});

describe("导出前置条件", () => {
  it("未就绪时禁用并原文列出原因", () => {
    const state = exportState({ ready: false, blockers: ["尚未生成对比矩阵（样本可能未确认）", "引用尚未经过门禁核验，无法导出。"] });
    expect(state.disabled).toBe(true);
    expect(state.blockers).toHaveLength(2);
    expect(state.text).toContain("暂时不能导出");
  });

  it("ready=true 但仍有 blocker 时按未就绪处理（宁可拒绝）", () => {
    expect(exportState({ ready: true, blockers: ["x"] }).disabled).toBe(true);
  });

  it("就绪时可导出", () => {
    expect(exportState({ ready: true, blockers: [] }).disabled).toBe(false);
  });

  it("缺字段时默认不可导出", () => {
    expect(exportState(undefined).disabled).toBe(true);
  });
});

describe("矩阵与观点分布", () => {
  it("列顺序固定，来源列在最后", () => {
    const view = matrixView([
      { case_id: "s1", identifier: "A", facts: "f", stance: "support", 来源: "用户材料" },
    ]);
    expect(view.columns.at(-1)).toBe("来源");
  });

  it("没有矩阵时不报错", () => {
    expect(matrixView([]).columns).toEqual([]);
  });

  it("观点分布文案由后端给（含分母声明），前端不改写", () => {
    const text = "本次确认样本 3 篇中，支持 2 篇、相反 1 篇、其他 0 篇、未能判定 0 篇。";
    expect(distributionText({ counts: { support: 2, oppose: 1, other: 0, unknown: 0 }, denominator: 3, labels: {}, text })).toBe(text);
    expect(distributionText({})).toBe("");
  });
});

describe("来源标注（红线 1）", () => {
  const source = (extra: Partial<SourceBrief>): SourceBrief =>
    ({
      source_id: "s1",
      kind: "case",
      identifier: "A",
      title: "标题",
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
      quote: "",
      synthetic: false,
      ...extra,
    }) as SourceBrief;

  it("用户材料单独归类", () => {
    expect(sourceKind(source({ kind: "user_material", origin: "user" }))).toBe("user");
  });

  it("法宝与本地依据库区分开", () => {
    expect(sourceKind(source({ origin: "mcp" }))).toBe("mcp");
    expect(sourceKind(source({ origin: "local", local_hit: true }))).toBe("local");
  });

  it("演示夹具单独标注（不能和真实来源混在一起）", () => {
    expect(sourceKind(source({ origin: "fixture", synthetic: true }))).toBe("fixture");
  });

  it("结论的引用能映射回依据池", () => {
    const conclusion: Conclusion = {
      text: "x",
      citation_source_ids: ["s1"],
      citations: [{ source_id: "s2", identifier: "B", quote: "q" }],
    };
    const resolved = resolveCitations(conclusion, [source({ source_id: "s1" }), source({ source_id: "s2" }), source({ source_id: "s3" })]);
    expect(resolved.map(item => item.source_id)).toEqual(["s1", "s2"]);
  });
});
