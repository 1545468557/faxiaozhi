/**
 * 界面组件行为（阶段 3-2）
 *
 * 用 fixture 数据渲染组件，锁住三条红线在**真实 DOM** 上的表现：
 * - 人工门真的挡住（按钮禁用 + 说明原因）；
 * - 被拦下的引用不出现在结论区；
 * - 每条依据都标出来源，用户材料未核验时不能被引用（给的是"本人已核验"按钮）。
 */

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { CandidatePool } from "@/components/workspace/candidate-pool";
import { Conclusions, MatrixTable } from "@/components/workspace/research-output";
import { EvidenceTabs } from "@/components/workspace/evidence-tabs";
import type { Candidate, GateReport, MaterialItem, SourceBrief, Synthesis } from "@/lib/api/types";

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

const source = (extra: Partial<SourceBrief> = {}): SourceBrief =>
  ({
    source_id: "s1",
    kind: "case",
    identifier: "（2023）示例民终1号",
    title: "示例案例",
    court: "示例法院",
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
    quote: "本院认为……",
    synthetic: false,
    ...extra,
  }) as SourceBrief;

describe("候选池与人工门", () => {
  const candidates = [candidate("s1"), candidate("s2"), candidate("s3")];

  it("一篇都没勾时「确认样本」按钮禁用，并说明原因", () => {
    render(
      <CandidatePool
        candidates={candidates}
        selection={[]}
        onSelectionChange={() => undefined}
        onConfirm={() => undefined}
        confirming={false}
        locked={false}
        confirmedIds={[]}
      />,
    );
    const button = screen.getByRole("button", { name: "确认样本" });
    expect(button.hasAttribute("disabled")).toBe(true);
    expect(screen.getByText(/请先勾选至少 1 篇候选材料/)).toBeTruthy();
  });

  it("勾选后按钮可用", () => {
    render(
      <CandidatePool
        candidates={candidates}
        selection={["s1"]}
        onSelectionChange={() => undefined}
        onConfirm={() => undefined}
        confirming={false}
        locked={false}
        confirmedIds={[]}
      />,
    );
    expect(screen.getByRole("button", { name: "确认样本" }).hasAttribute("disabled")).toBe(false);
  });

  it("每条候选都标出来源（红线 1）", () => {
    render(
      <CandidatePool
        candidates={[candidate("s1"), candidate("s2", { origin: "user", origin_text: "用户材料" })]}
        selection={[]}
        onSelectionChange={() => undefined}
        onConfirm={() => undefined}
        confirming={false}
        locked={false}
        confirmedIds={[]}
      />,
    );
    expect(screen.getAllByText("法宝").length).toBeGreaterThan(0);
    expect(screen.getAllByText("用户材料").length).toBeGreaterThan(0);
  });

  it("点「全不选」会把选择清空；「恢复默认」只选前 5 篇", () => {
    const onChange = vi.fn();
    render(
      <CandidatePool
        candidates={[candidate("s1"), candidate("s2")]}
        selection={["s1", "s2"]}
        onSelectionChange={onChange}
        onConfirm={() => undefined}
        confirming={false}
        locked={false}
        confirmedIds={[]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "全不选" }));
    expect(onChange).toHaveBeenCalledWith([]);
    fireEvent.click(screen.getByRole("button", { name: /恢复默认/ }));
    expect(onChange).toHaveBeenCalledWith(["s1", "s2"]);
  });

  it("样本已确认后不再提供勾选，只显示已确认的篇数", () => {
    render(
      <CandidatePool
        candidates={candidates}
        selection={[]}
        onSelectionChange={() => undefined}
        onConfirm={() => undefined}
        confirming={false}
        locked
        confirmedIds={["s1", "s2"]}
      />,
    );
    expect(screen.queryByRole("button", { name: "确认样本" })).toBeNull();
    expect(screen.getByText(/样本已确认/)).toBeTruthy();
  });

  it("候选为空时说明原因，不用示例数据补齐", () => {
    render(
      <CandidatePool
        candidates={[]}
        selection={[]}
        onSelectionChange={() => undefined}
        onConfirm={() => undefined}
        confirming={false}
        locked={false}
        confirmedIds={[]}
      />,
    );
    expect(screen.getByText(/候选池目前是空的/)).toBeTruthy();
  });
});

describe("红线 4：结论区只出现通过核验的结论", () => {
  const synthesis: Synthesis = {
    conclusions: [
      { text: "买受人可主张减少价款。", citation_source_ids: ["s1"], citations: [{ source_id: "s1", identifier: "A", quote: "q" }] },
      { text: "买受人可主张三倍赔偿。", citation_source_ids: ["s1"], citations: [{ source_id: "s1", identifier: "A", quote: "bad" }] },
    ],
  };

  it("被降级的结论不出现在结论区", () => {
    render(
      <Conclusions
        synthesis={synthesis}
        degradedTexts={["买受人可主张三倍赔偿。"]}
        sources={[source()]}
        onOpenSource={() => undefined}
      />,
    );
    expect(screen.getByText("买受人可主张减少价款。")).toBeTruthy();
    expect(screen.queryByText("买受人可主张三倍赔偿。")).toBeNull();
  });

  it("引用可点开看来源（键盘可达的按钮）", () => {
    const onOpen = vi.fn();
    render(
      <Conclusions
        synthesis={synthesis}
        degradedTexts={[]}
        sources={[source()]}
        onOpenSource={onOpen}
      />,
    );
    const cites = screen.getAllByRole("button");
    expect(cites.length).toBeGreaterThan(1);
    fireEvent.click(cites[cites.length - 1]);
    expect(onOpen).toHaveBeenCalled();
  });

  it("没有结论时说明会怎么产生，不显示占位结论", () => {
    render(<Conclusions synthesis={null} degradedTexts={[]} sources={[]} onOpenSource={() => undefined} />);
    expect(screen.getByText(/还没有综合结论/)).toBeTruthy();
  });
});

describe("矩阵", () => {
  it("渲染后端给的列（含「来源」列）", () => {
    render(
      <MatrixTable
        rows={[{ case_id: "s1", identifier: "A", facts: "已完成交接", 来源: "用户材料" }]}
      />,
    );
    const table = screen.getByRole("table");
    expect(within(table).getByText("来源")).toBeTruthy();
    expect(within(table).getByText("用户材料")).toBeTruthy();
  });

  it("没有矩阵时说明由后端计算", () => {
    render(<MatrixTable rows={[]} />);
    expect(screen.getByText(/还没有对比矩阵/)).toBeTruthy();
  });
});

describe("依据区 / 核验报告 / 上传材料", () => {
  const gate: GateReport = {
    accepted: 1,
    rejected: 1,
    guard_hits: 1,
    coverage: 0.5,
    demo_mode: false,
    details: [{ rule: "R5", reason: "引用原文与来源不一致", identifier: "示例案例", quote: "bad" }],
    gaps: ["结论存在未通过核验的引用"],
    degraded_texts: ["被降级的结论文本"],
  };
  const materials: MaterialItem[] = [
    {
      material_id: "m1",
      source_id: "user_m1",
      filename: "我的判决书.txt",
      role: "case",
      role_label: "案例材料",
      role_source: "detected",
      identifier: "（2023）苏01民终1234号",
      identifier_source: "text",
      identifier_missing: false,
      chars: 1200,
      verified: false,
      status: "pending_review",
      version_label: "v1",
      superseded: false,
    },
  ];

  const renderTabs = (props: Partial<Parameters<typeof EvidenceTabs>[0]> = {}) =>
    render(
      <EvidenceTabs
        sources={[source({ source_id: "s1" }), source({ source_id: "s2", kind: "user_material", origin: "user", origin_text: "用户材料", user_verified: false })]}
        materials={materials}
        gate={gate}
        gaps={[{ kind: "citation", detail: "有结论未提供引用" }]}
        synthesis={{ conclusions: [{ text: "被降级的结论文本", citation_source_ids: ["s1"] }] }}
        conflicts={[]}
        onOpenSource={() => undefined}
        onVerify={() => undefined}
        onRoleChange={() => undefined}
        onResolveConflict={() => undefined}
        onUploadFiles={() => undefined}
        uploading={false}
        uploadRejected={[]}
        maxFiles={20}
        {...props}
      />,
    );

  it("依据区列出来源与核验状态", () => {
    renderTabs();
    expect(screen.getAllByText(/已取得完整原文/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/双源一致/).length).toBeGreaterThan(0);
  });

  it("用户材料未核验时给「本人已核验」按钮，点击会上报", () => {
    const onVerify = vi.fn();
    renderTabs({ onVerify });
    fireEvent.click(screen.getAllByRole("button", { name: "本人已核验" })[0]);
    expect(onVerify).toHaveBeenCalledWith("s2");
  });

  it("门禁页签展示被拦下的规则与依据缺口（含被降级结论）", () => {
    renderTabs();
    fireEvent.click(screen.getByRole("tab", { name: /核验报告/ }));
    expect(screen.getByText("R5")).toBeTruthy();
    expect(screen.getByText(/引用原文与来源不一致/)).toBeTruthy();
    expect(screen.getAllByText(/不作为结论展示/).length).toBeGreaterThan(0);
  });

  it("来源冲突未裁决时给出裁决按钮", () => {
    const onResolve = vi.fn();
    renderTabs({
      conflicts: [{ source_id: "s2", identifier: "（2023）苏01民终1234号", status_text: "与法宝原文不一致" }],
      onResolveConflict: onResolve,
    });
    fireEvent.click(screen.getByRole("button", { name: "以我上传的材料为准" }));
    expect(onResolve).toHaveBeenCalledWith("s2");
  });

  it("材料页签展示待核验与自动识别信息", () => {
    renderTabs();
    fireEvent.click(screen.getByRole("tab", { name: /上传材料/ }));
    expect(screen.getByText("我的判决书.txt")).toBeTruthy();
    expect(screen.getByText("待核验")).toBeTruthy();
    expect(screen.getByText(/自动识别/)).toBeTruthy();
  });

  it("上传失败的文件逐条列出原因（部分失败不阻断）", () => {
    renderTabs({ uploadRejected: [{ filename: "坏文件.pdf", message: "不支持的文件类型" }] });
    fireEvent.click(screen.getByRole("tab", { name: /上传材料/ }));
    expect(screen.getByText(/坏文件.pdf/)).toBeTruthy();
    expect(screen.getByText(/不支持的文件类型/)).toBeTruthy();
  });
});
