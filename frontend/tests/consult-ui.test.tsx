/**
 * 咨询界面组件行为（阶段 3-3）
 *
 * 用 fixture 数据渲染，锁住：
 * - 追问卡一次呈现一个问题，空输入就地提示，允许跳过，忙碌时防止重复操作；
 * - 四块解答都在，且**只展示通过核验的结论**（未通过的不出现在 DOM 里）；
 * - 没有可引用依据时如实说明，不编造。
 */

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ClarifyCard, ConsultAnswerBlocks } from "@/components/workspace/consult-output";
import type { SourceBrief } from "@/lib/api/types";

const source = (extra: Partial<SourceBrief> = {}): SourceBrief =>
  ({
    source_id: "s1",
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
    ...extra,
  }) as SourceBrief;

describe("追问卡", () => {
  it("显示轮数与动态进度提示，只呈现首个非空问题", () => {
    render(
      <ClarifyCard
        roundsText="第 2 轮 / 最多 3 轮"
        progressHint="信息足够时会提前结束补充。"
        questions={["  ", " 涉案金额是多少？ ", "双方是否有书面约定？"]}
        facts=""
        onFactsChange={() => undefined}
        onSubmit={() => undefined}
        busy={false}
      />,
    );
    expect(screen.getByRole("heading", { name: "补充一个关键信息" })).toBeTruthy();
    expect(screen.getByText("第 2 轮 / 最多 3 轮")).toBeTruthy();
    expect(screen.getByText("信息足够时会提前结束补充。")).toBeTruthy();
    expect(screen.getByText("涉案金额是多少？")).toBeTruthy();
    expect(screen.queryByText("双方是否有书面约定？")).toBeNull();
  });

  it("空输入仍可点提交，就地报错并聚焦输入，不调用提交；输入后清除错误", () => {
    const onSubmit = vi.fn();
    const onChange = vi.fn();
    render(
      <ClarifyCard roundsText="第 1 轮 / 最多 3 轮" questions={["x"]} facts="  " onFactsChange={onChange} onSubmit={onSubmit} busy={false} onSkip={() => undefined} />,
    );
    const submit = screen.getByRole("button", { name: "提交补充说明" });
    const input = screen.getByLabelText("你的回答");
    expect(submit.hasAttribute("disabled")).toBe(false);
    fireEvent.click(submit);
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toContain("请先补充当前问题");
    expect(input.getAttribute("aria-invalid")).toBe("true");
    expect(document.activeElement).toBe(input);
    fireEvent.change(input, { target: { value: "不清楚" } });
    expect(onChange).toHaveBeenCalledWith("不清楚");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(input.getAttribute("aria-invalid")).toBe("false");
  });

  it("填写后可以提交，并回调输入变化", () => {
    const onChange = vi.fn();
    const onSubmit = vi.fn();
    render(
      <ClarifyCard roundsText="第 1 轮 / 最多 3 轮" questions={["x"]} facts="金额 8000 元，2024 年 3 月退租" onFactsChange={onChange} onSubmit={onSubmit} busy={false} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "提交补充说明" }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
    fireEvent.change(screen.getByLabelText("你的回答"), { target: { value: "新事实" } });
    expect(onChange).toHaveBeenCalledWith("新事实");
  });

  it("无需填写即可跳过，只调用跳过处理，事实携带交由父层负责", () => {
    const onSkip = vi.fn();
    const onSubmit = vi.fn();
    render(<ClarifyCard roundsText="第 1 轮 / 最多 3 轮" questions={["x"]} facts="" onFactsChange={() => undefined} onSubmit={onSubmit} onSkip={onSkip} busy={false} />);
    const skip = screen.getByRole("button", { name: "跳过，直接看分析" });
    expect(skip.hasAttribute("disabled")).toBe(false);
    fireEvent.click(skip);
    expect(onSkip).toHaveBeenCalledExactlyOnceWith();
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it.each([
    { busy: true, skipping: false },
    { busy: false, skipping: true },
  ])("处理中锁定输入与两个操作，避免重复提交（%o）", ({ busy, skipping }) => {
    const onSkip = vi.fn();
    const onSubmit = vi.fn();
    render(<ClarifyCard roundsText="第 1 轮 / 最多 3 轮" questions={["x"]} facts="已有事实" onFactsChange={() => undefined} onSubmit={onSubmit} onSkip={onSkip} busy={busy} skipping={skipping} />);
    expect(screen.getByLabelText("你的回答").hasAttribute("disabled")).toBe(true);
    const buttons = screen.getAllByRole("button");
    expect(buttons).toHaveLength(2);
    for (const button of buttons) {
      expect(button.hasAttribute("disabled")).toBe(true);
      fireEvent.click(button);
    }
    expect(onSubmit).not.toHaveBeenCalled();
    expect(onSkip).not.toHaveBeenCalled();
    expect(screen.getByText(skipping ? "正在继续分析…" : "正在提交…")).toBeTruthy();
  });

  it("给出当前题的输入引导，并保留跳过提示与隐私限制", () => {
    render(<ClarifyCard roundsText="第 1 轮 / 最多 3 轮" questions={["大概是什么时候收到通知的？"]} facts="" onFactsChange={() => undefined} onSubmit={() => undefined} busy={false} onSkip={() => undefined} />);
    expect(screen.getByPlaceholderText("例如：大约两周前，具体日期记不清了。")).toBeTruthy();
    expect(screen.getByText(/只需回答上面这一个问题/)).toBeTruthy();
    expect(screen.getByText(/也可以跳过，先看基于现有信息的回答/)).toBeTruthy();
    expect(screen.getByText(/仅在当前会话中处理/)).toBeTruthy();
  });

  it("隐私提示必须在", () => {
    render(<ClarifyCard roundsText="第 1 轮 / 最多 3 轮" questions={["x"]} facts="" onFactsChange={() => undefined} onSubmit={() => undefined} busy={false} />);
    expect(screen.getByText(/仅在当前会话中处理/)).toBeTruthy();
  });
});

describe("四块解答", () => {
  const answer = {
    conclusions: [
      { text: "可以主张返还押金。", citations: [{ source_id: "s1", identifier: "《民法典》第七百三十三条", quote: "承租人……" }] },
      { text: "你一定能赢。", citations: [{ source_id: "s1", identifier: "x", quote: "bad" }] },
    ],
    uncertainties: ["押金是否已被部分抵扣尚不清楚。"],
    next_steps: ["整理交接单与聊天记录。"],
  };

  it("四块标题都在", () => {
    render(<ConsultAnswerBlocks answer={answer} passed={["可以主张返还押金。"]} sources={[source()]} onOpenSource={() => undefined} />);
    expect(screen.getByText("一、结论")).toBeTruthy();
    expect(screen.getByText("二、法律依据（1 条）")).toBeTruthy();
    expect(screen.getByText("三、还需确认的情况与风险")).toBeTruthy();
    expect(screen.getByText("四、下一步建议")).toBeTruthy();
  });

  it("只展示通过核验的结论（未通过的完全不在 DOM 里）", () => {
    render(<ConsultAnswerBlocks answer={answer} passed={["可以主张返还押金。"]} sources={[source()]} onOpenSource={() => undefined} />);
    expect(screen.getByText("可以主张返还押金。")).toBeTruthy();
    expect(screen.queryByText("你一定能赢。")).toBeNull();
  });

  it("结论为空时明确说明原因，不留白", () => {
    render(<ConsultAnswerBlocks answer={answer} passed={[]} sources={[source()]} onOpenSource={() => undefined} />);
    expect(screen.getByText(/没有通过引用核验的结论/)).toBeTruthy();
  });

  it("引用按钮能把来源信息带出来（红线 1）", () => {
    render(<ConsultAnswerBlocks answer={answer} passed={["可以主张返还押金。"]} sources={[source()]} onOpenSource={() => undefined} />);
    expect(screen.getAllByText("法宝").length).toBeGreaterThan(0);
    expect(screen.getAllByText("已取得完整原文").length).toBeGreaterThan(0);
  });

  it("点引用会回调（用于打开来源侧栏）", () => {
    const onOpen = vi.fn();
    render(<ConsultAnswerBlocks answer={answer} passed={["可以主张返还押金。"]} sources={[source()]} onOpenSource={onOpen} />);
    const buttons = screen.getAllByRole("button");
    fireEvent.click(buttons[0]);
    expect(onOpen).toHaveBeenCalledWith("s1");
  });

  it("没有任何解答时显示等待说明", () => {
    render(<ConsultAnswerBlocks answer={undefined} passed={undefined} sources={[]} onOpenSource={() => undefined} />);
    expect(screen.getByText(/从你的问题开始/)).toBeTruthy();
  });

  it("不确定性与下一步为空时如实说明，不显示空列表", () => {
    render(<ConsultAnswerBlocks answer={{ conclusions: [] }} passed={[]} sources={[]} onOpenSource={() => undefined} />);
    expect(screen.getByText(/未列出其他待确认事项/)).toBeTruthy();
    expect(screen.getByText(/未给出额外建议/)).toBeTruthy();
  });
});
