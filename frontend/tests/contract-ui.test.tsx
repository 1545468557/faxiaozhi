/**
 * 合同界面组件行为（阶段 3-4）
 *
 * 重点是**原文 ↔ 风险联动**：点风险 → 对应原文片段高亮；点高亮片段 → 回选风险。
 * jsdom 没有 scrollIntoView，这里补一个空实现（只验证调用与状态，不验证视觉滚动）。
 */

import { beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ContractTextPanel, RiskList, StanceCard } from "@/components/workspace/contract-output";
import type { ContractRisk, SourceBrief } from "@/lib/api/types";

beforeAll(() => {
  // jsdom 不实现 scrollIntoView
  Element.prototype.scrollIntoView = vi.fn();
});

const source = (): SourceBrief =>
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
  }) as SourceBrief;

const risk = (id: string, over: Partial<ContractRisk> = {}): ContractRisk => ({
  riskId: id,
  kind: "commercial",
  level: "high",
  clauseId: "c2",
  clauseHeading: "第二条 押金",
  anchorText: "押金全部不予退还",
  start: 0,
  end: 8,
  locateOk: true,
  issue: "该约定缺少扣除依据与返还期限。",
  basis: [{ source_id: "s1", identifier: "《民法典》第五百七十七条", quote: "当事人一方不履行合同义务…" }],
  basisStatus: "verified",
  suggestion: "建议改为按实际损失结算（须经律师审定）",
  confidence: "medium",
  ...over,
});

describe("立场卡", () => {
  const props = {
    filename: "房屋租赁合同.docx",
    parties: { party_a: "张三", party_b: "李四" },
    options: [
      { value: "party_a", label: "甲方（出租方一侧）" },
      { value: "party_b", label: "乙方（承租方一侧）" },
      { value: "neutral", label: "中立第三方" },
    ],
    busy: false,
    error: "",
  };

  it("显示文件名、当事人与选项，并标明「未确认不得继续」", () => {
    render(<StanceCard {...props} value="" onChange={() => undefined} onSubmit={() => undefined} />);
    expect(screen.getByText(/房屋租赁合同.docx/)).toBeTruthy();
    expect(screen.getByText(/张三/)).toBeTruthy();
    expect(screen.getByText("未确认不得继续")).toBeTruthy();
    expect(screen.getByLabelText("我代表哪一方")).toBeTruthy();
  });

  it("没选立场时「确认立场并开始审查」禁用", () => {
    render(<StanceCard {...props} value="" onChange={() => undefined} onSubmit={() => undefined} />);
    expect(screen.getByRole("button", { name: "确认立场并开始审查" }).hasAttribute("disabled")).toBe(true);
  });

  it("选了立场后可以提交并回调", () => {
    const onSubmit = vi.fn();
    render(<StanceCard {...props} value="party_b" onChange={() => undefined} onSubmit={onSubmit} />);
    const button = screen.getByRole("button", { name: "确认立场并开始审查" });
    expect(button.hasAttribute("disabled")).toBe(false);
    fireEvent.click(button);
    expect(onSubmit).toHaveBeenCalled();
  });

  it("切换选项会回调（不给默认值，必须用户自己选）", () => {
    const onChange = vi.fn();
    render(<StanceCard {...props} value="" onChange={onChange} onSubmit={() => undefined} />);
    fireEvent.click(screen.getByRole("radio", { name: /中立第三方/ }));
    expect(onChange).toHaveBeenCalledWith("neutral");
  });

  it("后端说明「立场不同结论不同」必须可见", () => {
    render(<StanceCard {...props} value="" onChange={() => undefined} onSubmit={() => undefined} />);
    expect(screen.getByText(/立场不同，风险结论会不同/)).toBeTruthy();
  });
});

describe("风险清单", () => {
  it("有依据的风险展示依据按钮（可点开来源）", () => {
    const onOpen = vi.fn();
    render(
      <RiskList risks={[risk("r1")]} activeRiskId={null} onSelect={() => undefined} sources={[source()]} onOpenSource={onOpen} />,
    );
    expect(screen.getByText("高风险")).toBeTruthy();
    expect(screen.getByText("商业不利")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /民法典/ }));
    expect(onOpen).toHaveBeenCalledWith("s1");
  });

  it("依据未通过核验 → 只能作「提示性风险」", () => {
    render(
      <RiskList
        risks={[risk("r1", { basisStatus: "rejected" })]}
        activeRiskId={null}
        onSelect={() => undefined}
        sources={[source()]}
        onOpenSource={() => undefined}
      />,
    );
    expect(screen.getByText(/依据未通过核验（仅作提示）/)).toBeTruthy();
  });

  it("没有直接依据 → 如实写「未找到直接依据」", () => {
    render(
      <RiskList
        risks={[risk("r1", { basisStatus: "no_basis", basis: [] })]}
        activeRiskId={null}
        onSelect={() => undefined}
        sources={[]}
        onOpenSource={() => undefined}
      />,
    );
    expect(screen.getByText(/未找到直接依据（仅作提示）/)).toBeTruthy();
  });

  it("建议改法必须带「须经律师审定」", () => {
    render(
      <RiskList risks={[risk("r1")]} activeRiskId={null} onSelect={() => undefined} sources={[source()]} onOpenSource={() => undefined} />,
    );
    expect(screen.getByText(/须经律师审定/)).toBeTruthy();
  });

  it("定位失败的风险明确说明，不给错位置", () => {
    render(
      <RiskList
        risks={[risk("r1", { locateOk: false, start: -1, end: -1 })]}
        activeRiskId={null}
        onSelect={() => undefined}
        sources={[source()]}
        onOpenSource={() => undefined}
      />,
    );
    expect(screen.getByText(/未能定位到原文/)).toBeTruthy();
  });

  it("点风险会回调选中", () => {
    const onSelect = vi.fn();
    render(
      <RiskList risks={[risk("r1")]} activeRiskId={null} onSelect={onSelect} sources={[source()]} onOpenSource={() => undefined} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /高风险/ }));
    expect(onSelect).toHaveBeenCalledWith("r1");
  });

  it("没有风险条目时说明会怎么产生，不显示占位结论", () => {
    render(<RiskList risks={[]} activeRiskId={null} onSelect={() => undefined} sources={[]} onOpenSource={() => undefined} />);
    expect(screen.getByText(/还没有风险条目/)).toBeTruthy();
  });
});

describe("原文 ↔ 风险联动", () => {
  const text = "第二条 押金\n押金全部不予退还，出租人无须说明理由。";

  it("原文按风险偏移高亮，非风险部分为普通文本", () => {
    const start = text.indexOf("押金全部不予退还");
    render(
      <ContractTextPanel
        text={text}
        risks={[risk("r1", { start, end: start + 8 })]}
        activeRiskId={null}
        onAnchorClick={() => undefined}
      />,
    );
    const anchor = screen.getByRole("button", { name: "押金全部不予退还" });
    expect(anchor).toBeTruthy();
    expect(anchor.getAttribute("data-risk")).toBe("r1");
  });

  it("点原文高亮 → 回选风险", () => {
    const onClick = vi.fn();
    const start = text.indexOf("押金全部不予退还");
    render(
      <ContractTextPanel
        text={text}
        risks={[risk("r1", { start, end: start + 8 })]}
        activeRiskId={null}
        onAnchorClick={onClick}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "押金全部不予退还" }));
    expect(onClick).toHaveBeenCalledWith("r1");
  });

  it("选中风险时对应锚点标记为 active", () => {
    const start = text.indexOf("押金全部不予退还");
    render(
      <ContractTextPanel
        text={text}
        risks={[risk("r1", { start, end: start + 8 })]}
        activeRiskId="r1"
        onAnchorClick={() => undefined}
      />,
    );
    expect(screen.getByRole("button", { name: "押金全部不予退还" }).getAttribute("data-active")).toBe("true");
  });

  it("未能定位的风险在原文面板上如实提示", () => {
    render(
      <ContractTextPanel
        text={text}
        risks={[risk("r1", { locateOk: false, start: -1, end: -1 })]}
        activeRiskId={null}
        onAnchorClick={() => undefined}
      />,
    );
    expect(screen.getByText(/1 处风险未能定位到原文/)).toBeTruthy();
    // 原文本身不能丢
    expect(screen.getByLabelText("合同原文").textContent).toContain("出租人无须说明理由");
  });
});
