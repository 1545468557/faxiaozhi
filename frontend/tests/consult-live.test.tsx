/** 咨询整页回归：真实组件与 useRun，网络全部离线替身。 */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConsultLive } from "@/components/consult-live";
import { WorkspaceSessionProvider } from "@/components/workspace-session";
import type { SessionState } from "@/lib/api/types";
import type { StreamHandlers } from "@/lib/api/sse";

const network = vi.hoisted(() => ({
  loadState: vi.fn(),
  state: vi.fn(),
  retry: vi.fn(),
  message: vi.fn(),
  consultAnswer: vi.fn(),
  subscribeEvents: vi.fn(),
}));

vi.mock("@/lib/api", async original => {
  const actual = await original<typeof import("@/lib/api")>();
  return {
    ...actual,
    loadState: network.loadState,
    subscribeEvents: network.subscribeEvents,
    api: { ...actual.api, ...network },
  };
});
vi.mock("@/components/site-header", () => ({ SiteHeader: () => null }));
vi.mock("@/components/backend-status", () => ({ BackendStatus: () => null, useBackend: () => ({ bootstrap: null }) }));
vi.mock("@/components/workspace/evidence-tabs", () => ({ EvidenceTabs: () => null }));
vi.mock("@/components/workspace/source-sheet", () => ({ SourceSheet: () => null }));

function snapshot(overrides: Partial<SessionState> = {}): SessionState {
  return {
    session_id: "consult-kept-session",
    branch: "consult",
    topic: "新房东让我搬走",
    conditions: {},
    phase: "解答",
    status: "active",
    run_id: "consult-existing-run",
    queued: false,
    first_response_ms: null,
    total_ms: null,
    candidates: [],
    sample: { locked: false, confirmed: [], excluded: [] },
    cases: [],
    matrix: [],
    distribution: {},
    synthesis: null,
    limitations: [],
    gate_report: null,
    expression_hits: [],
    gaps: [],
    sources: [],
    materials: [],
    conflicts: [],
    supplement: { rounds_used: 0, max_rounds: 0, material_primary: false, remaining: 0 },
    export: { ready: false, blockers: ["咨询尚未完成。"] },
    consult: { facts: ["新房东让我搬走", "合同还有一年", "已经入住"], rounds: 3, max_rounds: 3 },
    contract: {},
    degradations: [],
    degradation_summary: {},
    failed_step: null,
    failed_reason: null,
    retry_count: 0,
    can_retry: false,
    ...overrides,
  };
}

function show(state: SessionState) {
  network.loadState.mockResolvedValue({ sid: state.session_id, state });
  network.state.mockResolvedValue(state);
  return render(<WorkspaceSessionProvider><ConsultLive /></WorkspaceSessionProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  network.subscribeEvents.mockReturnValue(vi.fn());
  network.retry.mockResolvedValue({ run_id: "consult-existing-run", status: "started" });
  network.message.mockResolvedValue({ run_id: "consult-existing-run", status: "started" });
  network.consultAnswer.mockResolvedValue({ run_id: "consult-next-run", status: "started" });
});

afterEach(() => vi.useRealTimers());

describe("咨询整页的失败与恢复", () => {
  it("格式失败显示重试而不是无依据，沿用原会话，不重新提交问题", async () => {
    const failed = snapshot({
      failed_step: "解答",
      failed_reason: "模型输出未通过结构校验：$.conclusions[0] 缺少必填字段：text",
      can_retry: true,
      consult: { ...snapshot().consult, status: "failed" },
    });
    show(failed);

    const retry = await screen.findByRole("button", { name: "重试回答" });
    expect(screen.getByText("回答暂未生成")).toBeTruthy();
    expect(screen.getByText("模型返回的解答格式不完整，暂时无法显示。")).toBeTruthy();
    expect(screen.queryByText(/\$\.conclusions/)).toBeNull();
    expect(screen.getByText(/本次会话中已提交的问题、补充事实和已取得的依据会继续使用/)).toBeTruthy();
    expect((screen.getByLabelText("你想问什么？") as HTMLTextAreaElement).value).toBe("新房东让我搬走");
    expect(screen.queryByText("无通过核验的结论")).toBeNull();
    expect(screen.queryByText("二、法律依据（0 条）")).toBeNull();
    expect(screen.queryByText("已完成")).toBeNull();
    fireEvent.click(retry);
    fireEvent.click(retry);

    await waitFor(() => expect(network.retry).toHaveBeenCalledTimes(1));
    expect(network.retry).toHaveBeenCalledWith("consult-kept-session");
    expect(network.message).not.toHaveBeenCalled();
    expect(network.consultAnswer).not.toHaveBeenCalled();

    const answered = snapshot({
      consult: { ...failed.consult, status: "answered", answer: { conclusions: [{ text: "仅展示已核验结论" }] }, passed: ["仅展示已核验结论"] },
    });
    network.state.mockResolvedValue(answered);
    const handlers = network.subscribeEvents.mock.calls[0][1] as StreamHandlers;
    await act(async () => handlers.onEvent({ name: "done", data: {} }));
    expect(await screen.findByText("仅展示已核验结论")).toBeTruthy();
    expect(screen.queryByText("回答暂未生成")).toBeNull();
  });

  it("重试请求失败后解除忙碌，可再次点击同会话重试", async () => {
    network.retry.mockRejectedValueOnce(new Error("服务暂时不可用"));
    show(snapshot({ failed_step: "解答", failed_reason: "输出格式校验失败", can_retry: true }));
    fireEvent.click(await screen.findByRole("button", { name: "重试回答" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "重试回答" }).hasAttribute("disabled")).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "重试回答" }));
    await waitFor(() => expect(network.retry).toHaveBeenCalledTimes(2));
  });

  it("实时格式错误事件也呈现失败，并从快照取得同会话重试权限", async () => {
    show(snapshot({ run_id: null, consult: {} }));
    await waitFor(() => expect((screen.getByLabelText("你想问什么？") as HTMLTextAreaElement).value).toBe("新房东让我搬走"));
    fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
    await waitFor(() => expect(network.message).toHaveBeenCalledWith("consult-kept-session", "新房东让我搬走"));
    network.state.mockResolvedValue(snapshot({ failed_step: "解答", failed_reason: "模型输出格式校验失败", can_retry: true }));
    const handlers = network.subscribeEvents.mock.calls[0][1] as StreamHandlers;
    await act(async () => handlers.onEvent({ name: "error", data: { error: { code: "schema_invalid", message: "模型输出格式校验失败" } } }));
    expect(await screen.findByRole("button", { name: "重试回答" })).toBeTruthy();
    expect(screen.queryByText("已完成")).toBeNull();
    expect(screen.queryByText("二、法律依据（0 条）")).toBeNull();
  });

  it("后端不允许重试时不提供可点击的重试按钮", async () => {
    show(snapshot({ failed_step: "解答", failed_reason: "重试次数已达上限", can_retry: false }));
    expect(await screen.findByText("回答暂未生成")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "重试回答" })).toBeNull();
  });

  it("刷新后从快照恢复追问入口，提交补充后轮询完成也结束进行中", async () => {
    const awaiting = snapshot({ consult: { rounds: 2, max_rounds: 3, awaiting: true, issue: { clarify_questions: ["合同何时到期？"] } } });
    show(awaiting);
    expect(await screen.findByText("合同何时到期？")).toBeTruthy();
    const facts = screen.getByLabelText("你的回答");
    fireEvent.change(facts, { target: { value: "明年到期" } });

    vi.useFakeTimers();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "提交补充说明" })));
    expect(network.consultAnswer).toHaveBeenCalledWith("consult-kept-session", "明年到期", { skipClarification: false, expectedRound: 2 });
    expect(network.subscribeEvents).not.toHaveBeenCalled();

    network.state.mockResolvedValue(snapshot({ consult: { rounds: 2, answer: { conclusions: [{ text: "轮询恢复的解答" }] }, passed: ["轮询恢复的解答"] } }));
    await act(async () => vi.advanceTimersByTimeAsync(2500));
    expect(screen.getByText("轮询恢复的解答")).toBeTruthy();
    expect(screen.queryByText("正在回答")).toBeNull();
    expect(screen.queryByText("进行中")).toBeNull();
    expect(screen.queryByLabelText("你的回答")).toBeNull();
  });

  it("达到追问上限后显示后端采用的假设，仍只展示通过核验的结论", async () => {
    show(snapshot({
      consult: {
        rounds: 3,
        max_rounds: 3,
        assumptions: ["假设租赁关系仍有效"],
        issue: { assumptions: ["识别阶段旧假设"] },
        answer: { conclusions: [{ text: "通过核验的内容" }, { text: "未通过核验的内容" }] },
        passed: ["通过核验的内容"],
      },
    }));
    expect(await screen.findByText(/以下回答基于这些尚待确认的情况：假设租赁关系仍有效/)).toBeTruthy();
    expect(screen.queryByText(/识别阶段旧假设/)).toBeNull();
    expect(screen.getByText("通过核验的内容")).toBeTruthy();
    expect(screen.queryByText("未通过核验的内容")).toBeNull();
  });

  it("兼容只有 issue.assumptions 的快照，页面不丢失假设说明", async () => {
    show(snapshot({ consult: { rounds: 3, max_rounds: 3, issue: { assumptions: ["假设书面合同尚未到期"] }, answer: { conclusions: [] } } }));
    expect(await screen.findByText(/以下回答基于这些尚待确认的情况：假设书面合同尚未到期/)).toBeTruthy();
  });

  it("真正的引用未通过仍展示依据不足，不误变成服务失败", async () => {
    show(snapshot({ consult: { status: "insufficient", answer: { conclusions: [{ text: "被门禁拒绝的结论" }] }, passed: [] } }));
    expect(await screen.findByText("无通过核验的结论")).toBeTruthy();
    expect(screen.queryByText("被门禁拒绝的结论")).toBeNull();
    expect(screen.queryByText("回答暂未生成")).toBeNull();
  });
});


describe("逐轮补充与快速回答", () => {
  const waiting = () => snapshot({ consult: { rounds: 1, max_rounds: 3, awaiting: true, issue: { clarify_questions: ["什么时候购买的？", "在哪个平台购买？", "存在什么质量问题？"] } } });

  it("只显示当前关键问题，补充后从新快照决定下一问", async () => {
    show(waiting());
    expect(await screen.findByText("什么时候购买的？")).toBeTruthy();
    expect(screen.queryByText("在哪个平台购买？")).toBeNull();
    expect(screen.getByText("第 1 轮 / 最多 3 轮")).toBeTruthy();
    expect(screen.getByText(/最多再补充 2 轮/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("你的回答"), { target: { value: "两周前" } });
    vi.useFakeTimers();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "提交补充说明" })));
    network.state.mockResolvedValue(snapshot({ consult: { rounds: 2, max_rounds: 3, awaiting: true, issue: { clarify_questions: ["商家是否承认质量问题？"] } } }));
    await act(async () => vi.advanceTimersByTimeAsync(2500));
    expect(screen.getByText("商家是否承认质量问题？")).toBeTruthy();
    expect(screen.queryByText("什么时候购买的？")).toBeNull();
    expect(screen.queryByText("在哪个平台购买？")).toBeNull();
    expect(screen.getByText(/最多再补充 1 轮/)).toBeTruthy();
    expect((screen.getByLabelText("你的回答") as HTMLTextAreaElement).value).toBe("");
  });

  it("空输入也可跳过，重复点击只提交一次且不把跳过当成新问题", async () => {
    network.consultAnswer.mockReturnValue(new Promise(() => undefined));
    show(waiting());
    const skip = await screen.findByRole("button", { name: "跳过，直接看分析" });
    fireEvent.click(skip);
    fireEvent.click(skip);
    expect(network.consultAnswer).toHaveBeenCalledTimes(1);
    expect(network.consultAnswer).toHaveBeenCalledWith("consult-kept-session", "", { skipClarification: true, expectedRound: 1 });
    expect(network.message).not.toHaveBeenCalled();
    expect((screen.getByLabelText("你的回答") as HTMLTextAreaElement).disabled).toBe(true);
  });

  it("跳过携带已填内容，失败后草稿保留且可以再次操作", async () => {
    network.consultAnswer.mockRejectedValueOnce(new Error("服务暂时不可用"));
    show(waiting());
    await screen.findByText("什么时候购买的？");
    fireEvent.change(screen.getByLabelText("你的回答"), { target: { value: "大约两周前" } });
    fireEvent.click(screen.getByRole("button", { name: "跳过，直接看分析" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "跳过，直接看分析" }).hasAttribute("disabled")).toBe(false));
    expect(network.consultAnswer).toHaveBeenCalledWith("consult-kept-session", "大约两周前", { skipClarification: true, expectedRound: 1 });
    expect((screen.getByLabelText("你的回答") as HTMLTextAreaElement).value).toBe("大约两周前");
  });

  it("跳过后的结果提示信息限制，仍过滤未核验结论", async () => {
    show(snapshot({ consult: { rounds: 1, max_rounds: 3, skipped_clarification: true, assumptions: ["购买时间尚未确认"], answer: { conclusions: [{ text: "可信结论" }, { text: "未核验内容" }] }, passed: ["可信结论"] } }));
    expect(await screen.findByText(/你已跳过补充/)).toBeTruthy();
    expect(screen.getByText("可信结论")).toBeTruthy();
    expect(screen.queryByText("未核验内容")).toBeNull();
    expect(screen.queryByRole("button", { name: "跳过，直接看分析" })).toBeNull();
  });

  it("已选择的示例不再重复推荐，标点与空白差异也可识别", async () => {
    show(snapshot({ topic: "买到的东西有质量问题 商家不肯退怎么办", consult: {}, run_id: null }));
    await waitFor(() => expect((screen.getByLabelText("你想问什么？") as HTMLTextAreaElement).value).toContain("质量问题"));
    expect(screen.queryByRole("button", { name: "买到的东西有质量问题，商家不肯退怎么办？" })).toBeNull();
    expect(screen.getByRole("button", { name: "公司拖欠工资，我该怎么办，需要准备什么？" })).toBeTruthy();
  });
});
