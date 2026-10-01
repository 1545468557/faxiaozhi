import { act, cleanup, fireEvent, render as renderView, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AppShell } from "@/components/shell/app-shell";
import { WorkspaceSessionProvider } from "@/components/workspace-session";
import type { ReactNode } from "react";
const render = (children: ReactNode) => renderView(<WorkspaceSessionProvider>{children}</WorkspaceSessionProvider>);
const api = vi.hoisted(() => ({ sessions: vi.fn(), pathname: "/" }));
vi.mock("next/navigation", () => ({ usePathname: () => api.pathname }));
vi.mock("@/lib/api/v3", () => ({ sessions: api.sessions }));
beforeEach(() => { api.pathname = "/"; api.sessions.mockResolvedValue({ sessions: [] }); });
afterEach(() => { cleanup(); vi.clearAllMocks(); });
it("keeps all five real routes and expands/collapses the rail", async () => {
  render(<AppShell><p>正文</p></AppShell>);
  await screen.findByRole("link", { name: "设置" });
  for (const [name, path] of [["法律问答", "/"], ["类案检索", "/research"], ["合同审查", "/contract"], ["法规查找", "/statutes"], ["我的", "/me"]]) {
    expect(screen.getByRole("link", { name }).getAttribute("href")).toBe(path);
  }
  fireEvent.click(screen.getByRole("button", { name: "展开导航" }));
  expect(screen.getByRole("button", { name: "收起导航", expanded: true })).toBeTruthy();
  fireEvent.keyDown(window, { key: "Escape" });
  expect(screen.getByRole("button", { name: "展开导航" }).getAttribute("aria-expanded")).toBe("false");
});
it("loads real history on demand and uses actual session IDs", async () => {
  api.sessions.mockResolvedValue({ sessions: [{ session_id: "test-42", title: "测试对话", updated_at: 1 }] });
  render(<AppShell><p>正文</p></AppShell>);
  await screen.findByRole("link", { name: "设置" });
  expect(api.sessions).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "历史对话" }));
  const link = await screen.findByRole("link", { name: /测试对话/ });
  expect(link.getAttribute("href")).toBe("/?session=test-42");
  expect(screen.queryByRole("link", { name: "新建对话" })).toBeNull();
  expect(screen.queryByRole("button", { name: "历史对话" })).toBeNull();
  expect(screen.getAllByText("历史对话")).toHaveLength(1);
  fireEvent.click(screen.getAllByRole("button", { name: "关闭历史对话" }).at(-1)!);
  expect(screen.getByRole("button", { name: "历史对话" })).toBeTruthy();
});
it("shows a recoverable history error without fake rows", async () => {
  api.sessions.mockRejectedValue(new Error("offline"));
  render(<AppShell><p>正文</p></AppShell>);
  await screen.findByRole("link", { name: "设置" });
  fireEvent.click(screen.getByRole("button", { name: "历史对话" }));
  await screen.findByText("历史对话暂时加载失败，请关闭后重新打开。");
});
it("preserves expanded navigation across module clicks and shell remounts", async () => {
  const view = renderView(<WorkspaceSessionProvider><AppShell key="home"><p>首页</p></AppShell></WorkspaceSessionProvider>);
  await screen.findByRole("link", { name: "设置" });
  fireEvent.click(screen.getByRole("button", { name: "展开导航" }));
  const moduleLink = screen.getByRole("link", { name: "合同审查" });
  moduleLink.addEventListener("click", event => event.preventDefault());
  fireEvent.click(moduleLink);
  expect(screen.getByRole("button", { name: "收起导航", expanded: true })).toBeTruthy();
  view.rerender(<WorkspaceSessionProvider><AppShell key="contract"><p>合同页</p></AppShell></WorkspaceSessionProvider>);
  await screen.findByRole("link", { name: "设置" });
  expect(screen.getByRole("button", { name: "收起导航", expanded: true })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "收起导航", expanded: true }));
  view.rerender(<WorkspaceSessionProvider><AppShell key="research"><p>研究页</p></AppShell></WorkspaceSessionProvider>);
  await screen.findByRole("link", { name: "设置" });
  expect(screen.getByRole("button", { name: "展开导航", expanded: false })).toBeTruthy();
});

it.each(["/", "/research", "/contract", "/statutes", "/me", "/settings"])("shows %s without a login screen", pathname => {
  api.pathname = pathname;
  render(<AppShell><p>模块正文</p></AppShell>);
  expect(screen.getByText("模块正文")).toBeTruthy();
  expect(screen.queryByText("请先登录")).toBeNull();
  expect(screen.getByRole("link", { name: "设置" })).toBeTruthy();
});

it.each(["/research", "/contract", "/statutes", "/me", "/settings"])("does not show the question history header on %s", async pathname => {
  api.pathname = pathname;
  const view = render(<AppShell>模块正文</AppShell>);
  await screen.findByRole("link", { name: "设置" });
  expect(screen.queryByRole("button", { name: "历史对话" })).toBeNull();
  expect(view.container.querySelector(".nav-topbar .nav-brand")?.textContent).toBe("法小智");
  expect(api.sessions).not.toHaveBeenCalled();
});

it("hides an open question-history panel when the route changes", async () => {
  const view = render(<AppShell>首页</AppShell>);
  await screen.findByRole("link", { name: "设置" });
  fireEvent.click(screen.getByRole("button", { name: "历史对话" }));
  await screen.findByText("还没有历史对话。可以先聊一个问题。");
  api.pathname = "/research";
  await act(async () => { view.rerender(<WorkspaceSessionProvider><AppShell>研究页</AppShell></WorkspaceSessionProvider>); });
  expect(view.container.querySelector(".nav-history")).toBeNull();
  expect(view.container.querySelector(".nav-topbar .nav-brand")?.textContent).toBe("法小智");
});
