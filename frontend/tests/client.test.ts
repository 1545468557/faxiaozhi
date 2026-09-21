/**
 * API 客户端（阶段 3-1）
 *
 * 断言：错误结构解析、后端未启动的兜底、multipart 不被改写 content-type、
 * 下载链接编码、以及「组件里不允许裸 fetch」的集中式入口。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, BFF, downloadUrl } from "@/lib/api/client";

const ok = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });

const fail = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

let calls: { url: string; init: RequestInit }[] = [];

beforeEach(() => {
  calls = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
    calls.push({ url, init });
    return ok({ fine: true });
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("请求封装", () => {
  it("所有请求都打到同源 BFF 前缀", async () => {
    await api.bootstrap();
    expect(calls[0].url.startsWith(`${BFF}/`)).toBe(true);
  });

  it("默认不缓存", async () => {
    await api.bootstrap();
    expect(calls[0].init.cache).toBe("no-store");
  });

  it("JSON 请求带 content-type", async () => {
    await api.createSession("research");
    expect((calls[0].init.headers as Record<string, string>)["content-type"]).toBe("application/json");
    expect(calls[0].init.body).toBe(JSON.stringify({ branch: "research" }));
  });

  it("会话相关路径带上 session id", async () => {
    await api.state("s_abc");
    expect(calls[0].url).toBe(`${BFF}/session/s_abc/state`);
  });

  it("session id 做 URL 编码", async () => {
    await api.state("s/1 2");
    expect(calls[0].url).toBe(`${BFF}/session/s%2F1%202/state`);
  });

  it("咨询回答用 facts 字段", async () => {
    await api.consultAnswer("s_1", "补充事实");
    expect(JSON.parse(String(calls[0].init.body))).toEqual({ facts: "补充事实" });
  });

  it("跳过追问使用独立控制参数，保留草稿和当前轮次", async () => {
    await api.consultAnswer("s_1", "大约两周前", { skipClarification: true, expectedRound: 1 });
    expect(JSON.parse(String(calls[0].init.body))).toEqual({ facts: "大约两周前", skip_clarification: true, expected_round: 1 });
  });

  it("确认样本用 checkpoint + kind + payload", async () => {
    await api.checkpoint("s_1", "sample_confirm", { confirmed: ["a"], excluded: [] });
    expect(JSON.parse(String(calls[0].init.body))).toEqual({
      kind: "sample_confirm",
      payload: { confirmed: ["a"], excluded: [] },
    });
  });

  it("确认立场用 stance_confirm", async () => {
    await api.checkpoint("s_1", "stance_confirm", { stance: "party_b" });
    expect(JSON.parse(String(calls[0].init.body)).kind).toBe("stance_confirm");
  });

  it("冲突裁决带 source_id 与 decision", async () => {
    await api.resolveConflict("s_1", "src_9", "use_user_material");
    expect(JSON.parse(String(calls[0].init.body))).toEqual({
      source_id: "src_9",
      decision: "use_user_material",
    });
  });

  it("改角色只允许 case / statute（与后端一致）", async () => {
    await api.setMaterialRole("s_1", "src_1", "statute");
    expect(JSON.parse(String(calls[0].init.body))).toEqual({ role: "statute" });
  });
});

describe("multipart 上传", () => {
  it("FormData 直传，不手写 content-type（否则 boundary 会坏）", async () => {
    const form = new FormData();
    form.append("role", "case");
    form.append("files", new File(["正文"], "样例.txt", { type: "text/plain" }));
    await api.upload("s_1", form);
    const headers = (calls[0].init.headers ?? {}) as Record<string, string>;
    expect(headers["content-type"]).toBeUndefined();
    expect(calls[0].init.body).toBe(form);
  });

  it("上传路径正确", async () => {
    await api.upload("s_1", new FormData());
    expect(calls[0].url).toBe(`${BFF}/session/s_1/upload`);
  });
});

describe("错误解析（底线 3：统一错误结构）", () => {
  it("后端错误体转成 ApiError，保留 code/message/detail", async () => {
    vi.stubGlobal("fetch", async () =>
      fail(409, { error: { code: "export_blocked", message: "存在未通过核验的引用。", detail: { reasons: ["a"] } } }),
    );
    await expect(api.exportResearch("s_1")).rejects.toMatchObject({
      code: "export_blocked",
      status: 409,
      message: "存在未通过核验的引用。",
      detail: { reasons: ["a"] },
    });
  });

  it("导出被拦时不可重试（要先去解决门禁）", async () => {
    vi.stubGlobal("fetch", async () => fail(409, { error: { code: "export_blocked", message: "x" } }));
    try {
      await api.exportResearch("s_1");
      throw new Error("应当抛错");
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).info.retryable).toBe(false);
    }
  });

  it("后端没启动时给 backend_unreachable 且可重试", async () => {
    vi.stubGlobal("fetch", async () => {
      throw new TypeError("fetch failed");
    });
    await expect(api.bootstrap()).rejects.toMatchObject({ code: "backend_unreachable" });
  });

  it("非 JSON 错误体不崩，降级为 internal_error", async () => {
    vi.stubGlobal("fetch", async () => new Response("<html>500</html>", { status: 500 }));
    await expect(api.bootstrap()).rejects.toMatchObject({ code: "internal_error", status: 500 });
  });

  it("200 但空体视为契约异常，不当成功", async () => {
    vi.stubGlobal("fetch", async () => new Response("", { status: 200 }));
    await expect(api.bootstrap()).rejects.toMatchObject({ code: "internal_error" });
  });

  it("403 无错误体时按本机限制处理", async () => {
    vi.stubGlobal("fetch", async () => new Response("", { status: 403 }));
    await expect(api.bootstrap()).rejects.toMatchObject({ code: "backend_blocked" });
  });

  it("错误对象保留 HTTP 状态码，便于界面区分", async () => {
    vi.stubGlobal("fetch", async () => fail(429, { error: { code: "concurrency_queued" } }));
    await expect(api.createSession("research")).rejects.toMatchObject({ status: 429 });
  });
});

describe("Word 下载地址", () => {
  it("走代理并做编码", () => {
    expect(downloadUrl("法小智_研究报告 2026.docx")).toBe(
      `${BFF}/exports/${encodeURIComponent("法小智_研究报告 2026.docx")}`,
    );
  });

  it("带路径时只取文件名，防目录穿越", () => {
    expect(downloadUrl("/etc/passwd")).toBe(`${BFF}/exports/passwd`);
    expect(downloadUrl("..\\..\\secret.docx")).toBe(`${BFF}/exports/secret.docx`);
  });
});
