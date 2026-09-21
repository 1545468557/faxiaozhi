import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ResearchCandidates, ResearchProgress } from "@/components/research/research-design";
import type { Candidate } from "@/lib/api/types";
afterEach(cleanup);
const candidates = ["A", "B"].map(source_id => ({source_id, title:`案例 ${source_id}`, identifier:`案号 ${source_id}`, court:"测试法院", decided_on:"2025-01-01", origin_text:"测试来源"} as Candidate));
describe("研究工作台真实数据展示", () => {
 it("查看详情不改变确认样本，只有勾选触发选择", () => {
  const onActive=vi.fn(), onToggle=vi.fn();
  render(<ResearchCandidates candidates={candidates} sources={[]} selection={["A"]} activeId="A" onActive={onActive} onToggle={onToggle} onOpen={vi.fn()}/>);
  fireEvent.click(screen.getByRole("button",{name:/案例 B/}));
  expect(onActive).toHaveBeenCalledWith("B"); expect(onToggle).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("checkbox",{name:"选择 案号 B"})); expect(onToggle).toHaveBeenCalledWith("B");
 });
 it("缺少摘要时明确说明，不虚构相似性", () => {
  render(<ResearchCandidates candidates={candidates} sources={[]} selection={[]} activeId="removed" onActive={vi.fn()} onToggle={vi.fn()} onOpen={vi.fn()}/>);
  expect(screen.getByText(/当前候选信息未提供可展示的摘要/)).toBeTruthy();
  expect(screen.queryByText(/相似度/)).toBeNull();
  expect(screen.getByRole("heading",{level:2}).textContent).toBe("案例 A");
 });
 it("没有后端步骤数时用等待动画，不显示虚构百分比", () => {
  const {container}=render(<ResearchProgress title="正在检索" progress={0} label="" stepText=""/>);
  expect(container.querySelector(".rx-indeterminate")).toBeTruthy();
  expect(screen.getByText("正在等待检索进度")).toBeTruthy();
  expect(container.textContent).not.toContain("%");
 });
});
