/**
 * 合同审查新界面 · 结果区与红线（迭代 1-1）
 *
 * 用 fixture 数据渲染真实的 DOM，锁住五件事：
 * 1. 依据状态必须分开显示（已核验 / 未找到直接依据 / 依据未通过核验只能作提示）；
 * 2. 导出没就绪时按钮禁用，并把原因**原文**列出来；
 * 3. 原文 ↔ 风险双向联动；
 * 4. 定位失败的风险**绝不高亮**（宁可不标，也不给错位置）；
 * 5. "未提示风险的条款不等于没有风险" 常驻。
 */

import { beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { ContractResult } from "@/components/contract/contract-result";
import { exportState } from "@/lib/api/common";
import { riskSummary, sortRisks } from "@/lib/api/contract";
import type { ContractRisk, ContractSnapshot, SourceBrief } from "@/lib/api/types";

beforeAll(() => {
  // jsdom 不实现 scrollIntoView
  Element.prototype.scrollIntoView = vi.fn();
});

const source = (extra: Partial<SourceBrief> = {}): SourceBrief =>
  ({
    source_id: "s1",
    kind: "statute",
    identifier: "《民法典》第五百七十七条",
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
    quote: "当事人一方不履行合同义务…",
    synthetic: false,
    ...extra,
  }) as SourceBrief;

const risk = (id: string, over: Partial<ContractRisk> = {}): ContractRisk => ({
  riskId: id,
  kind: "commercial",
  level: "high",
  clauseId: "c1",
  clauseHeading: "第二条 押金",
  anchorText: "押金全部不予退还",
  start: 0,
  end: 8,
  locateOk: true,
  issue: "押金全额不退，缺少扣除依据与返还期限。",
  basis: [{ source_id: "s1", identifier: "《民法典》第五百七十七条", quote: "当事人一方不履行合同义务…" }],
  basisStatus: "verified",
  suggestion: "建议改为按实际损失结算。",
  confidence: "medium",
  ...over,
});

const TEXT = "第一条 押金押金全部不予退还，出租人无须说明理由。第二条 维修费用由承租人承担。";

function renderResult(
  risks: ContractRisk[],
  options: {
    exportReady?: boolean;
    blockers?: string[];
    failedStep?: string | null;
    /** 风险卡是受控的：要看到某条的正文，需把它设为"当前选中" */
    activeRiskId?: string | null;
  } = {},
) {
  const sorted = sortRisks(risks);
  const contract: ContractSnapshot = {
    filename: "房屋租赁合同.docx",
    text: TEXT,
    stance: "support",
    stanceLabel: "甲方（出租方一侧）",
    clauses_count: 2,
    risks: sorted,
    counts: {
      total: sorted.length,
      by_level: { high: 1, medium: 0, low: 0 },
      verified: sorted.filter(item => item.basisStatus === "verified").length,
      no_basis: sorted.filter(item => item.basisStatus === "no_basis").length,
      locate_failed: sorted.filter(item => !item.locateOk).length,
    },
    status: "reviewed",
  };
  const onSelectRisk = vi.fn();
  const onExport = vi.fn();
  render(
    <ContractResult
      contract={contract}
      risks={sorted}
      activeRiskId={options.activeRiskId ?? null}
      onSelectRisk={onSelectRisk}
      summary={riskSummary(contract)}
      sources={[source()]}
      stanceLabel="甲方（出租方一侧）"
      exportInfo={exportState({
        ready: options.exportReady === true,
        blockers: options.blockers ?? [],
      })}
      onExport={onExport}
      exporting={false}
      onRefresh={vi.fn()}
      refreshing={false}
      refreshedAt={null}
      onRetry={vi.fn()}
      canRetry={false}
      retrying={false}
      truncated={[]}
      materials={[]}
      gateReport={null}
      gaps={[]}
      conflicts={[]}
      onVerify={vi.fn()}
      uploadFiles={vi.fn()}
      uploading={false}
      uploadRejected={[]}
      maxFiles={20}
      failedStep={options.failedStep ?? null}
    />,
  );
  return { onSelectRisk, onExport };
}

describe("合同审查结果区", () => {
  it("三种依据状态分别显示，未通过核验的只能作提示", () => {
    renderResult([
      risk("r1"),
      risk("r2", { level: "medium", basisStatus: "no_basis", basis: [], anchorText: "维修费用由承租人承担" }),
      risk("r3", { level: "low", basisStatus: "rejected", anchorText: "押金全部不予退还" }),
    ]);

    expect(screen.getByText("有法律依据（已核对原文）")).toBeTruthy();
    expect(screen.getByText("没找到直接条文 · 只作提醒")).toBeTruthy();
    expect(screen.getByText("引用的条文没对上 · 只作提醒")).toBeTruthy();
  });

  it("导出没就绪时按钮禁用，并把原因原文列出；就绪时可点", () => {
    const blocker = "引用核验尚未完成，暂时无法导出。";
    renderResult([risk("r1")], { blockers: [blocker] });
    const button = screen.getByRole("button", { name: /导出报告/ }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(screen.getByText(new RegExp(blocker.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")))).toBeTruthy();
  });

  it("导出就绪时按钮可点", () => {
    renderResult([risk("r1")], { exportReady: true });
    const button = screen.getByRole("button", { name: /导出报告/ }) as HTMLButtonElement;
    expect(button.disabled).toBe(false);
  });

  it("点风险头 → 回调选中该条；被选中时左侧原文锚点高亮", () => {
    const { onSelectRisk } = renderResult([risk("r1")]);
    // 点风险头（正文要"被选中"才渲染，所以点标题行的徽标）
    fireEvent.click(screen.getByText("有法律依据（已核对原文）"));
    expect(onSelectRisk).toHaveBeenCalledWith("r1");
  });

  it("选中的风险：左侧对应原文锚点高亮；点锚点回选该条", () => {
    const { onSelectRisk } = renderResult([risk("r1")], { activeRiskId: "r1" });
    const anchors = document.querySelectorAll(".ct-anchor");
    expect(anchors.length).toBe(1);
    expect(anchors[0].getAttribute("data-active")).toBe("true");
    fireEvent.click(anchors[0]);
    expect(onSelectRisk).toHaveBeenCalledWith("r1");
  });

  it("依据未通过核验的风险：正文里必须写明只作提示、不作结论", () => {
    renderResult([risk("r3", { level: "low", basisStatus: "rejected" })], { activeRiskId: "r3" });
    expect(screen.getByText(/只作提醒，不作结论/)).toBeTruthy();
  });

  it("定位失败的风险绝不高亮，并如实说明", () => {
    renderResult([risk("r1", { locateOk: false, start: -1, end: -1 })], { activeRiskId: "r1" });
    expect(document.querySelectorAll(".ct-anchor").length).toBe(0);
    expect(screen.getAllByText(/没能定位到原文/).length).toBeGreaterThan(0);
    // 这条已经展开（受控），直接看说明；不再按标题文字点击（标题与正文都可能出现同一句话）
    expect(screen.getByText(/不会在原文里标黄/)).toBeTruthy();
  });

  it("常驻提示：未提示风险的条款不等于没有风险", () => {
    renderResult([risk("r1")], { activeRiskId: "r1" });
    expect(screen.getByText(/没有提示问题的条款不等于没有风险/)).toBeTruthy();
    expect(screen.getByText(/建议这样改 · 可编辑，须经律师审定/)).toBeTruthy();
  });

  it("这次没跑完时，如实写明失败在哪一步", () => {
    renderResult([risk("r1")], { failedStep: "依据检索" });
    expect(screen.getByText(/卡在「依据检索」/)).toBeTruthy();
  });

  it("风险清单按等级排序：高风险排前", () => {
    renderResult([
      risk("low", { level: "low", anchorText: "押金全部不予退还" }),
      risk("high", { level: "high", anchorText: "维修费用由承租人承担" }),
    ]);
    const list = document.querySelector(".ct-risks")!;
    const levels = within(list as HTMLElement)
      .getAllByText(/^(高|中|低)$/)
      .map(node => node.textContent);
    expect(levels[0]).toBe("高");
  });
});

it("exports edited suggestions and acceptance without changing evidence status", () => {
  const {onExport} = renderResult([risk("r1", {basisStatus:"rejected"})],{activeRiskId:"r1",exportReady:true});
  fireEvent.change(screen.getByLabelText(/建议这样改 · 可编辑/),{target:{value:"人工改为验收后付款"}});
  fireEvent.click(screen.getByRole("button",{name:"采纳这条建议"}));
  expect(screen.getByText("引用的条文没对上 · 只作提醒")).toBeTruthy();
  fireEvent.click(screen.getByRole("button",{name:"导出报告"}));
  expect(onExport).toHaveBeenCalledWith({r1:{status:"accept",suggestion:"人工改为验收后付款"}});
  fireEvent.click(screen.getByRole("button",{name:"撤销处理"}));
  fireEvent.click(screen.getByRole("button",{name:"导出报告"}));
  expect(onExport).toHaveBeenLastCalledWith({r1:{status:"pending",suggestion:"人工改为验收后付款"}});
});
