/**
 * 合同审查的界面判定（阶段 3-4）
 *
 * 锁住三条口径：
 * - 立场门（未确认不得开始，选项来自后端）；
 * - 原文 ↔ 风险联动的切分（定位失败的不给错位置）；
 * - 依据未通过核验的风险只能作「提示性风险」。
 */

import { describe, expect, it } from "vitest";
import {
  canSubmitStance,
  clauseList,
  anchorFor,
  offsetsSane,
  riskKindText,
  riskLevelText,
  riskSummary,
  segmentContract,
  sortRisks,
  splitRisks,
  stanceGate,
} from "@/lib/api/contract";
import type { ContractRisk, ContractSnapshot } from "@/lib/api/types";

const risk = (id: string, over: Partial<ContractRisk> = {}): ContractRisk => ({
  riskId: id,
  kind: "commercial",
  level: "medium",
  clauseId: "c1",
  clauseHeading: "第三条 押金返还",
  anchorText: "押金全部不予退还",
  start: 0,
  end: 8,
  locateOk: true,
  issue: "该约定对乙方不利。",
  basis: [],
  basisStatus: "verified",
  suggestion: "建议改为按实际损失结算（须经律师审定）",
  confidence: "medium",
  ...over,
});

describe("立场门（硬门）", () => {
  it("立场未确认时是等待状态，选项来自后端事件载荷", () => {
    const gate = stanceGate(undefined, {
      payload: {
        filename: "房屋租赁合同.docx",
        parties: { party_a: "张三", party_b: "李四" },
        options: [
          { value: "party_a", label: "甲方" },
          { value: "party_b", label: "乙方" },
          { value: "neutral", label: "中立" },
        ],
      },
    });
    expect(gate.awaiting).toBe(true);
    expect(gate.confirmed).toBe(false);
    expect(gate.filename).toBe("房屋租赁合同.docx");
    expect(gate.parties).toEqual({ party_a: "张三", party_b: "李四" });
    expect(gate.options.map(item => item.value)).toEqual(["party_a", "party_b", "neutral"]);
  });

  it("后端没给选项时用兜底标签，不空白", () => {
    const gate = stanceGate(undefined, null);
    expect(gate.options).toHaveLength(3);
    expect(gate.options.some(item => item.label.includes("甲方"))).toBe(true);
  });

  it("已确认立场后 waiting=false 并显示标签", () => {
    const gate = stanceGate({ stance: "party_b", stanceLabel: "乙方" } as Partial<ContractSnapshot>, null);
    expect(gate.awaiting).toBe(false);
    expect(gate.confirmed).toBe(true);
    expect(gate.label).toBe("乙方");
  });

  it("没选立场不能提交（按钮禁用 + 原因）", () => {
    const gate = canSubmitStance("");
    expect(gate.ok).toBe(false);
    expect(gate.reason).toContain("请先选择你代表哪一方");
  });

  it("选了立场可以提交", () => {
    expect(canSubmitStance("party_a").ok).toBe(true);
  });
});

describe("风险清单排序与分级", () => {
  it("等级按 高 → 中 → 低 排序", () => {
    const rows = [risk("r1", { level: "low" }), risk("r2", { level: "high" }), risk("r3", { level: "medium" })];
    expect(sortRisks(rows).map(item => item.riskId)).toEqual(["r2", "r3", "r1"]);
  });

  it("同等级里定位成功的排前面（不能定位的先看结论）", () => {
    const rows = [
      risk("r1", { locateOk: false, start: -1, end: -1 }),
      risk("r2", { locateOk: true }),
    ];
    expect(sortRisks(rows).map(item => item.riskId)).toEqual(["r2", "r1"]);
  });

  it("等级与类型有中文标签，未知值原样显示", () => {
    expect(riskLevelText("high")).toBe("高");
    expect(riskKindText("wording")).toBe("措辞不清");
    expect(riskLevelText("weird")).toBe("weird");
  });
});

describe("红线 4：依据未通过核验的风险只作提示", () => {
  it("只有 basisStatus=verified 才算有依据", () => {
    const { based, provisional } = splitRisks([
      risk("r1", { basisStatus: "verified" }),
      risk("r2", { basisStatus: "rejected" }),
      risk("r3", { basisStatus: "no_basis" }),
      risk("r4", { basisStatus: "pending" }),
    ]);
    expect(based.map(item => item.riskId)).toEqual(["r1"]);
    expect(provisional.map(item => item.riskId)).toEqual(["r2", "r3", "r4"]);
  });

  it("数量摘要用后端 counts（不自己数），并给出分级文案", () => {
    const summary = riskSummary({
      counts: { total: 3, by_level: { high: 2, medium: 1, low: 0 }, verified: 2, no_basis: 1, locate_failed: 1 },
    } as Partial<ContractSnapshot>);
    expect(summary.total).toBe(3);
    expect(summary.text).toContain("3 项待关注条款");
    expect(summary.text).toContain("2 项高风险");
    expect(summary.locateFailed).toBe(1);
  });

  it("没有风险时如实说明，不写「没风险」", () => {
    const summary = riskSummary({ counts: { total: 0 } } as Partial<ContractSnapshot>);
    expect(summary.text).toBe("未识别到风险条款");
    expect(summary.text).not.toContain("没有风险");
  });
});

describe("原文 ↔ 风险联动", () => {
  const text = "第一条 租赁期限\n租期一年。\n第二条 押金\n押金全部不予退还，出租人无须说明理由。";

  it("按 start/end 切分原文，风险片段带 riskId", () => {
    const start = text.indexOf("押金全部不予退还");
    const { segments } = segmentContract(text, [
      risk("r1", { start, end: start + "押金全部不予退还".length }),
    ]);
    const highlighted = segments.filter(item => item.riskId);
    expect(highlighted).toHaveLength(1);
    expect(highlighted[0].text).toBe("押金全部不予退还");
    // 切分不能丢字
    expect(segments.map(item => item.text).join("")).toBe(text);
  });

  it("多个风险按位置切分且不重叠错乱", () => {
    const a = text.indexOf("租期一年");
    const b = text.indexOf("押金全部不予退还");
    const { segments } = segmentContract(text, [
      risk("r2", { start: b, end: b + 4 }),
      risk("r1", { start: a, end: a + 4 }),
    ]);
    expect(segments.map(item => item.text).join("")).toBe(text);
    const ids = segments.filter(item => item.riskId).map(item => item.riskId);
    expect(ids).toEqual(["r1", "r2"]);
  });

  it("定位失败的风险不参与高亮，单独列出来", () => {
    const { segments, unlocated } = segmentContract(text, [
      risk("r1", { locateOk: false, start: -1, end: -1 }),
      risk("r2", { start: 0, end: 3 }),
    ]);
    expect(unlocated.map(item => item.riskId)).toEqual(["r1"]);
    expect(segments.filter(item => item.riskId).map(item => item.riskId)).toEqual(["r2"]);
  });

  it("偏移越界（超过原文长度）也不能给出错误位置", () => {
    const { unlocated } = segmentContract(text, [risk("r1", { start: 5, end: 99999 })]);
    expect(unlocated).toHaveLength(1);
  });

  it("offsetsSane 能发现越界偏移（用于自查）", () => {
    expect(offsetsSane(text, [risk("r1", { start: 0, end: 3 })])).toBe(true);
    expect(offsetsSane(text, [risk("r1", { start: 0, end: 99999 })])).toBe(false);
  });

  it("点风险能拿到对应片段（用于滚动定位）", () => {
    const start = text.indexOf("租期一年");
    const { segments } = segmentContract(text, [risk("r1", { start, end: start + 4 })]);
    expect(anchorFor(risk("r1"), segments)?.start).toBe(start);
    expect(anchorFor(risk("r9"), segments)).toBeNull();
  });

  it("原文为空时不报错", () => {
    const { segments } = segmentContract("", [risk("r1")]);
    expect(segments).toEqual([]);
  });
});

describe("条款与截断提示", () => {
  it("条款列表来自后端切块结果", () => {
    const clauses = clauseList({
      clauses: [{ clause_id: "c1", heading: "第一条", text: "…", summary: "…", start: 0, end: 10, line: 1 }],
    } as Partial<ContractSnapshot>);
    expect(clauses).toHaveLength(1);
    expect(clauses[0].line).toBe(1);
  });

  it("缺字段时返回空数组", () => {
    expect(clauseList(undefined)).toEqual([]);
  });
});
