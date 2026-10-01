// @vitest-environment node
/**
 * BFF 来源白名单（2026-09-27 放开局域网/域名访问时补的守卫）
 *
 * 为什么必须钉住：
 * 改造前 BFF 只放行 localhost —— 这是「本机部署」的安全默认，**不能因为要开放访问就悄悄改掉**。
 * 所以这组用例要同时钉住两件事：
 *   1. **默认不变**：不设 `ALLOWED_HOSTS` 时，非本机来源必须照样被拒；
 *   2. **按需放开**：设了之后，只有列表内的来源放行。
 *
 * 判据说明：放行 ≠ 成功。放行的请求会真的去连后端，这里把 `fetch` 打桩成「连不上」，
 * 于是放行 → 502 backend_unreachable，被拒 → 403 permission_denied。
 * 两者 code 不同，正好用来区分「白名单过没过」。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { forward } from "@/lib/server/bff";

/** 构造 forward 真正会用到的那几个字段（不依赖 undici 对 host 头的处理）。 */
function fakeRequest(host: string, origin?: string) {
  return {
    url: `http://${host}/api/bff/health`,
    method: "GET",
    body: null,
    headers: {
      get: (key: string) => {
        if (key === "host") return host;
        if (key === "origin") return origin ?? null;
        return null;
      },
    },
  } as unknown as Request;
}

async function probe(host: string, origin?: string): Promise<string> {
  const res = await forward(fakeRequest(host, origin), "http://127.0.0.1:8010", ["health"]);
  const body = (await res.json()) as { error?: { code?: string } };
  return body.error?.code ?? "ok";
}

beforeEach(() => {
  delete process.env.ALLOWED_HOSTS;
  // 让「放行」的请求快速失败，从而与「被拒」区分开，不去碰真实端口。
  vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("no backend"))));
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete process.env.ALLOWED_HOSTS;
});

describe("默认（不设 ALLOWED_HOSTS）—— 行为必须与改造前一致", () => {
  it("本机来源放行（localhost / 127.0.0.1 / [::1]）", async () => {
    expect(await probe("localhost:5174")).toBe("backend_unreachable");
    expect(await probe("127.0.0.1:5174")).toBe("backend_unreachable");
    expect(await probe("[::1]:5174")).toBe("backend_unreachable");
  });

  it("局域网 IP 照样被拒", async () => {
    expect(await probe("192.168.129.53:5174")).toBe("permission_denied");
  });

  it("任意外部域名被拒", async () => {
    expect(await probe("faxiaozhi.example.com")).toBe("permission_denied");
    expect(await probe("evil.example.com")).toBe("permission_denied");
  });

  it("Host 是本机但 Origin 不是 → 仍然拒（挡跨站发起）", async () => {
    expect(await probe("localhost:5174", "https://evil.example.com")).toBe("permission_denied");
  });

  it("没有 Host 头（直连/测试）→ 与改造前一致，放行", async () => {
    expect(await probe("")).toBe("backend_unreachable");
  });
});

describe("ALLOWED_HOSTS 精确主机名", () => {
  it("只放行列出的域名", async () => {
    process.env.ALLOWED_HOSTS = "faxiaozhi.example.com";
    expect(await probe("faxiaozhi.example.com")).toBe("backend_unreachable");
    expect(await probe("other.example.com")).toBe("permission_denied");
  });

  it("大小写与空格不影响判定", async () => {
    process.env.ALLOWED_HOSTS = "  Faxiaozhi.Example.COM  ";
    expect(await probe("faxiaozhi.example.com")).toBe("backend_unreachable");
  });

  it("域名放行后，Origin 也要在列表内", async () => {
    process.env.ALLOWED_HOSTS = "faxiaozhi.example.com";
    expect(await probe("faxiaozhi.example.com", "https://faxiaozhi.example.com")).toBe(
      "backend_unreachable",
    );
    expect(await probe("faxiaozhi.example.com", "https://evil.example.com")).toBe(
      "permission_denied",
    );
  });
});

describe("ALLOWED_HOSTS 前缀通配与多值", () => {
  it("192.168.129.* 覆盖同网段（DHCP 换 IP 也不用改）", async () => {
    process.env.ALLOWED_HOSTS = "192.168.129.*";
    expect(await probe("192.168.129.53:5174")).toBe("backend_unreachable");
    expect(await probe("192.168.129.99:5174")).toBe("backend_unreachable");
    expect(await probe("10.0.0.1:5174")).toBe("permission_denied");
    // 前缀不能跨段匹配
    expect(await probe("192.168.1299.1")).toBe("permission_denied");
  });

  it("逗号分隔的多个值都生效", async () => {
    process.env.ALLOWED_HOSTS = "faxiaozhi.example.com,192.168.1.*,localhost";
    expect(await probe("faxiaozhi.example.com")).toBe("backend_unreachable");
    expect(await probe("192.168.1.7:5174")).toBe("backend_unreachable");
    expect(await probe("localhost:5174")).toBe("backend_unreachable");
    expect(await probe("evil.example.com")).toBe("permission_denied");
  });

  it("空的 / 只有逗号的值不误放行", async () => {
    process.env.ALLOWED_HOSTS = " , ,, ";
    expect(await probe("evil.example.com")).toBe("permission_denied");
    expect(await probe("localhost:5174")).toBe("backend_unreachable");
  });
});

describe("ALLOWED_HOSTS=* 逃生舱", () => {
  it("任意来源放行（只在隧道/反代后面用）", async () => {
    process.env.ALLOWED_HOSTS = "*";
    expect(await probe("anything.example.com")).toBe("backend_unreachable");
    expect(await probe("192.168.1.1:5174")).toBe("backend_unreachable");
  });
});

it("passes the guest cookie through an SSE response", async () => {
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("data: ok\n\n", {
    headers: { "content-type": "text/event-stream", "set-cookie": "fzx_guest=guest-token; Path=/; HttpOnly" },
  }))));
  const response = await forward(fakeRequest("localhost:5174"), "http://127.0.0.1:8011", ["api", "chat"]);
  expect(response.headers.get("set-cookie")).toContain("fzx_guest=guest-token");
  expect(response.headers.get("content-type")).toContain("text/event-stream");
});
