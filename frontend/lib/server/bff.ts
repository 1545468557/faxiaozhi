/**
 * BFF 代理（阶段 3-1）
 *
 * 浏览器只访问同源 `/api/bff/**`，由这些 route handler 转发到后端（默认 `http://127.0.0.1:8010`）。
 * 每个接口一个显式文件（见 app/api/bff/**），共享本文件的转发实现。
 *
 * 注意：本项目运行器是 **vinext + Vite + Cloudflare Workers**（不是 `next dev`），
 * 实测它**不支持 catch-all 路由**（`[...path]` 会 404），因此用显式路由 —— 也更贴近
 * 《阶段 3 技术开发文档》§7.2 的接口表。
 *
 * 四个必须守住的技术点：
 * 1. **SSE 真流式**：`text/event-stream` 的响应体原样透传，不 `await text()`，并关掉缓冲；
 * 2. **错误结构不改写**：后端 `{error:{code,message}}` 原样返回，前端靠 code 决定文案与重试；
 * 3. **multipart / 下载**：不手写 content-type（boundary 会坏）；`Content-Disposition` 要透传；
 * 4. **只允许本机来源**：后端只监听 127.0.0.1，代理层也不对外开口子。
 */

const BASE = (process.env.BACKEND_BASE_URL ?? "http://127.0.0.1:8010").replace(/\/+$/, "");

/**
 * v3 后端（重写版）在 **8011**，与旧后端 8010 并存。
 * 新接口挂在 `/api/bff/v3/**`，转发到 v3；旧接口不动。
 * 等 v3 验收通过、旧 `app/` 退役后，把 BACKEND_BASE_URL 指到 8011 即可收口。
 */
const V3_BASE = (process.env.V3_BACKEND_BASE_URL ?? "http://127.0.0.1:8011").replace(/\/+$/, "");

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);

function isLocalRequest(request: Request): boolean {
  const host = request.headers.get("host") ?? "";
  const hostname = host.startsWith("[") ? host.slice(0, host.indexOf("]") + 1) : host.split(":")[0];
  if (hostname && !LOCAL_HOSTS.has(hostname)) return false;
  const origin = request.headers.get("origin");
  if (!origin) return true;
  try {
    return LOCAL_HOSTS.has(new URL(origin).hostname);
  } catch {
    return false;
  }
}

const errorJson = (code: string, message: string, status: number) =>
  Response.json(
    { error: { code, message } },
    { status, headers: { "cache-control": "no-store" } },
  );

export async function forward(request: Request, base: string, segments: string[]): Promise<Response> {
  if (!isLocalRequest(request)) {
    return errorJson("permission_denied", "当前只允许在本机使用。", 403);
  }

  const incoming = new URL(request.url);
  const target = `${base}/${segments.map(encodeURIComponent).join("/")}${incoming.search}`;

  const headers = new Headers();
  // cookie 必须转发（登录态用的 HttpOnly Cookie；前端 JS 读不到，只能靠代理带过去）
  for (const key of ["content-type", "accept", "range", "if-none-match", "cookie"]) {
    const value = request.headers.get(key);
    if (value) headers.set(key, value);
  }

  const method = request.method.toUpperCase();
  const hasBody = method !== "GET" && method !== "HEAD";

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method,
      headers,
      // 流式转发请求体（multipart 大文件不落内存）；Node 下需要 duplex
      body: hasBody ? request.body : undefined,
      duplex: hasBody ? "half" : undefined,
      cache: "no-store",
      redirect: "manual",
    } as RequestInit & { duplex?: "half" });
  } catch {
    // 后端没启动 / 端口不对：给出可执行的提示，不回显内部细节
    const port = (() => {
      try {
        return new URL(base).port || "80";
      } catch {
        return base;
      }
    })();
    return errorJson("backend_unreachable", `连不上本机后端服务。请确认后端已启动（127.0.0.1:${port}）。`, 502);
  }

  const contentType = upstream.headers.get("content-type") ?? "application/json; charset=utf-8";
  const out = new Headers();

  if (contentType.includes("text/event-stream")) {
    out.set("content-type", "text/event-stream; charset=utf-8");
    out.set("cache-control", "no-cache, no-transform");
    out.set("connection", "keep-alive");
    out.set("x-accel-buffering", "no");
    return new Response(upstream.body, { status: upstream.status, headers: out });
  }

  out.set("cache-control", "no-store");
  out.set("content-type", contentType);
  // 下发登录 Cookie（HttpOnly 由后端设置，代理只负责原样透传，不改属性）
  const setCookies = typeof upstream.headers.getSetCookie === "function" ? upstream.headers.getSetCookie() : [];
  if (setCookies.length) {
    for (const value of setCookies) out.append("set-cookie", value);
  } else {
    const single = upstream.headers.get("set-cookie");
    if (single) out.append("set-cookie", single);
  }
  const disposition = upstream.headers.get("content-disposition");
  if (disposition) out.set("content-disposition", disposition);

  return new Response(await upstream.arrayBuffer(), { status: upstream.status, headers: out });
}

/** 针对某个后端生成 route handler 工厂 */
function handlersOn(base: string) {
  return {
    /** `build(params)` 给出转发到后端的路径段 */
    route<P extends Record<string, string>>(build: (params: P) => string[]) {
      return async (request: Request, context: { params: Promise<P> }): Promise<Response> => {
        const params = await context.params;
        return forward(request, base, build(params));
      };
    },
    /** 无参数路由 */
    flat(...segments: string[]) {
      return async (request: Request): Promise<Response> => forward(request, base, segments);
    },
  };
}

/** 旧后端（8010） */
export const legacy = handlersOn(BASE);

/** v3 后端（8011）。新接口一律用它 */
export const v3 = handlersOn(V3_BASE);

export const route = legacy.route;
export const flat = legacy.flat;

export function proxy(request: Request, segments: string[]): Promise<Response> {
  return forward(request, BASE, segments);
}
