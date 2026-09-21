/**
 * 会话生命周期（阶段 3-1）
 *
 * 断言：sid 存 sessionStorage（刷新可恢复、关标签页即清）、会话丢失时静默重开一次、
 * 以及「内容不落盘、重启后无法继续」这类错误能被识别出来。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createSession, forgetSession, isRestartRequired, loadState, readSessionId } from "@/lib/api/session";
import { ApiError } from "@/lib/api/client";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

beforeEach(() => {
  window.sessionStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("会话标识存放位置", () => {
  it("建会话后写入 sessionStorage（刷新仍在、关掉标签页即清）", async () => {
    vi.stubGlobal("fetch", async () => json({ session_id: "s_new" }));
    const sid = await createSession("research");
    expect(sid).toBe("s_new");
    expect(readSessionId("research")).toBe("s_new");
  });

  it("不同模块的会话互不覆盖", async () => {
    vi.stubGlobal("fetch", async (_url: string, init: RequestInit = {}) => {
      const branch = JSON.parse(String(init.body ?? "{}")).branch;
      return json({ session_id: branch === "consult" ? "s_consult" : "s_research" });
    });
    await createSession("research");
    await createSession("consult");
    expect(readSessionId("research")).toBe("s_research");
    expect(readSessionId("consult")).toBe("s_consult");
  });

  it("不写 localStorage（材料与问题正文都不留痕）", async () => {
    vi.stubGlobal("fetch", async () => json({ session_id: "s_new" }));
    await createSession("research");
    expect(window.localStorage.length).toBe(0);
  });

  it("可以主动忘记会话", async () => {
    vi.stubGlobal("fetch", async () => json({ session_id: "s_new" }));
    await createSession("research");
    forgetSession("research");
    expect(readSessionId("research")).toBeNull();
  });
});

describe("loadState 的恢复逻辑", () => {
  it("没有会话时先建再取快照", async () => {
    vi.stubGlobal("fetch", async (url: string) =>
      url.endsWith("/api/bff/session") ? json({ session_id: "s_fresh" }) : json({ phase: "idle" }),
    );
    const { sid, state } = await loadState("research");
    expect(sid).toBe("s_fresh");
    expect(state.phase).toBe("idle");
  });

  it("已有会话时直接复用", async () => {
    window.sessionStorage.setItem("fzx:sid:research", "s_keep");
    vi.stubGlobal("fetch", async () => json({ phase: "done" }));
    const { sid } = await loadState("research");
    expect(sid).toBe("s_keep");
  });

  it("会话过期（404 session_not_found）时静默重开一次并回调说明", async () => {
    window.sessionStorage.setItem("fzx:sid:research", "s_dead");
    const onRecreated = vi.fn();
    let stateCalls = 0;
    vi.stubGlobal("fetch", async (url: string) => {
      if (url.includes("s_dead")) return json({ error: { code: "session_not_found", message: "会话不存在。" } }, 404);
      if (url.endsWith("/api/bff/session")) return json({ session_id: "s_again" });
      stateCalls += 1;
      return json({ phase: "idle", session_id: "s_again" });
    });
    const { sid } = await loadState("research", { onRecreated });
    expect(sid).toBe("s_again");
    expect(onRecreated).toHaveBeenCalledTimes(1);
    expect(readSessionId("research")).toBe("s_again");
    expect(stateCalls).toBeGreaterThan(0);
  });

  it("只重开一次：新会话仍失败时把错误抛出去，不无限重试", async () => {
    vi.stubGlobal("fetch", async (url: string) =>
      url.endsWith("/api/bff/session") ? json({ session_id: "s_again" }) : json({ error: { code: "session_not_found" } }, 404),
    );
    await expect(loadState("research")).rejects.toBeInstanceOf(ApiError);
  });

  it("后端连不上时如实抛出，不假装成功", async () => {
    vi.stubGlobal("fetch", async () => {
      throw new TypeError("fetch failed");
    });
    await expect(loadState("research")).rejects.toMatchObject({ code: "backend_unreachable" });
  });

  it("忽略传入的 sid 之外的状态：以服务端快照为准", async () => {
    vi.stubGlobal("fetch", async () => json({ phase: "运行中", session_id: "s_x" }));
    const { state } = await loadState("research", { sid: "s_x" });
    expect(state.phase).toBe("运行中");
  });
});

describe("不可恢复错误识别", () => {
  it("材料类不可恢复错误要提示重新发起", () => {
    expect(isRestartRequired(new ApiError("material_not_recoverable", "x", 409))).toBe(true);
  });

  it("咨询与合同类同样识别", () => {
    expect(isRestartRequired(new ApiError("consult_not_recoverable", "x", 409))).toBe(true);
    expect(isRestartRequired(new ApiError("contract_not_recoverable", "x", 409))).toBe(true);
  });

  it("普通错误不误判", () => {
    expect(isRestartRequired(new ApiError("export_blocked", "x", 409))).toBe(false);
    expect(isRestartRequired(new Error("随便一个错误"))).toBe(false);
  });
});
