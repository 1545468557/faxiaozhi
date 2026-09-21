/**
 * v3 后端客户端（重写版，挂在 8011）
 *
 * 与旧 `lib/api/*` 的关系：**互不影响**。旧接口打到 `/api/bff/**`（8010），
 * 这里全部打到 `/api/bff/v3/**`（8011）。等旧 `app/` 退役后，把 `lib/api/*` 整体删掉即可。
 *
 * 三条约定（照 `app_v3/README.md` 的契约写，不自己造字段）：
 * 1. `POST /api/chat` 是 **SSE**：`meta → sources? → note? → delta×N → done|error`；
 *    `meta` 必到且是首帧；`done` / `error` 收到即关流。
 * 2. 检索结果（`sources`）**只是参考材料，未经核验**，界面不许写"已核验"。
 * 3. `error` 是"这次没做成"，**不等于"没有相关规定"** —— 文案照后端给的写。
 */

import { BFF } from "./client";

export const V3 = `${BFF}/v3`;

export type V3Source = {
  kind: string;
  title: string;
  identifier: string;
  court: string;
  decided_on: string;
  uri: string;
  origin_text: string;
  quote: string;
};

export type V3Message = {
  id: string;
  session_id: string;
  role: "user" | "assistant";
  content: string;
  sources: V3Source[];
  created_at: number;
  seq: number;
};

export type V3SessionBrief = {
  session_id: string;
  title: string;
  created_at: number;
  updated_at: number;
  message_count?: number;
};

export type V3Bootstrap = {
  agent: { name: string; id: string };
  /** 只报配置状态，不真的调模型（见 `app_v3/model.py: ping`） */
  model: { provider: string; is_stub: boolean; model_id: string; key_present: boolean };
  retrieval: { enabled: boolean; configured: boolean; kinds: string[]; verified: false };
  disclosure: string;
};

export type V3Meta = { session_id: string; is_stub: boolean; title: string };
export type V3Done = {
  session_id: string;
  message_id: string;
  chars: number;
  sources: number;
  elapsed_ms: number;
};

export type V3Event =
  | { name: "meta"; data: V3Meta }
  | { name: "sources"; data: { items: V3Source[]; notes: string[] } }
  | { name: "note"; data: { text: string } }
  | { name: "delta"; data: { text: string } }
  | { name: "done"; data: V3Done }
  | { name: "error"; data: { code: string; message: string } };

const KNOWN = new Set(["meta", "sources", "note", "delta", "done", "error"]);

/** 后端错误体：v3 自造的 `{error:{...}}`，以及 FastAPI HTTPException 的 `{detail:{error:{...}}}` */
type ErrorBody = { error?: { code?: string; message?: string }; detail?: { error?: { code?: string; message?: string } } };

async function failure(response: Response): Promise<{ code: string; message: string }> {
  let code = `http_${response.status}`;
  let message = "";
  try {
    const body = (await response.json()) as ErrorBody;
    const inner = body?.error ?? body?.detail?.error;
    if (inner?.code) code = inner.code;
    if (inner?.message) message = inner.message;
  } catch {
    /* 后端没给 JSON（多半是代理/网关层的错误页），用兜底文案 */
  }
  if (!message) message = response.status === 404 ? "找不到这个会话。" : "请求未成功，请稍后重试。";
  return { code, message };
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${V3}${path}`, {
      headers: { accept: "application/json" },
      cache: "no-store",
      signal,
    });
  } catch {
    if (signal?.aborted) throw new Error("aborted");
    throw Object.assign(new Error("连不上本机后端服务。"), { code: "backend_unreachable" });
  }
  if (!response.ok) {
    const info = await failure(response);
    throw Object.assign(new Error(info.message), { code: info.code, status: response.status });
  }
  return (await response.json()) as T;
}

export function health(signal?: AbortSignal) {
  return getJson<{ ok: boolean; is_stub: boolean }>("/health", signal);
}

export function bootstrap(signal?: AbortSignal) {
  return getJson<V3Bootstrap>("/bootstrap", signal);
}

export function timeline(sid: string, signal?: AbortSignal) {
  return getJson<{ session_id: string; title: string; messages: V3Message[] }>(
    `/chat/${encodeURIComponent(sid)}`,
    signal,
  );
}

export function sessions(signal?: AbortSignal) {
  return getJson<{ sessions: V3SessionBrief[] }>("/sessions", signal);
}

export async function deleteSession(sid: string, signal?: AbortSignal) {
  const response = await fetch(`${V3}/session/${encodeURIComponent(sid)}`, {
    method: "DELETE",
    headers: { accept: "application/json" },
    cache: "no-store",
    signal,
  });
  if (!response.ok) {
    const info = await failure(response);
    throw Object.assign(new Error(info.message), { code: info.code, status: response.status });
  }
  return (await response.json()) as { deleted: boolean };
}

export type ChatHandlers = {
  onEvent: (event: V3Event) => void;
  /** 收流后一定会调用一次，用于收尾（成功 / 失败 / 断开 / 主动中止） */
  onClosed?: (reason: "done" | "error" | "network" | "aborted") => void;
};

/**
 * 发一句话，流式收回答。返回「停止」函数（组件卸载时必须调用）。
 *
 * 不用原生 EventSource：POST 带 JSON 体、且需要能主动关流。
 */
export function chat(
  body: { session_id?: string; message: string; use_retrieval?: boolean },
  handlers: ChatHandlers,
): () => void {
  const controller = new AbortController();
  let finished = false;

  const finish = (reason: "done" | "error" | "network" | "aborted") => {
    if (finished) return;
    finished = true;
    handlers.onClosed?.(reason);
  };

  (async () => {
    let response: Response;
    try {
      response = await fetch(`${V3}/chat`, {
        method: "POST",
        headers: { "content-type": "application/json", accept: "text/event-stream" },
        body: JSON.stringify(body),
        cache: "no-store",
        signal: controller.signal,
      });
    } catch {
      finish(controller.signal.aborted ? "aborted" : "network");
      return;
    }

    // 后端在开始流之前就失败（例如 500）：没有 SSE 可读，转成一次 error 事件
    if (!response.ok || !response.body) {
      const info = await failure(response);
      handlers.onEvent({ name: "error", data: info });
      finish("error");
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        let boundary = buffer.indexOf("\n\n");
        while (boundary !== -1) {
          const chunk = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          boundary = buffer.indexOf("\n\n");

          if (!chunk || chunk.startsWith(":")) continue;

          let name = "message";
          const dataLines: string[] = [];
          for (const line of chunk.split("\n")) {
            if (line.startsWith("event:")) name = line.slice(6).trim();
            else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
          }
          if (!dataLines.length || !KNOWN.has(name)) continue;

          let data: Record<string, unknown> = {};
          try {
            data = JSON.parse(dataLines.join("\n")) as Record<string, unknown>;
          } catch {
            continue;
          }

          handlers.onEvent({ name, data } as V3Event);
          if (name === "done" || name === "error") {
            await reader.cancel().catch(() => undefined);
            finish(name);
            return;
          }
        }
      }
      // 流在 done/error 之前就断了：可能是后端崩了，也可能是网络
      finish("network");
    } catch {
      finish(controller.signal.aborted ? "aborted" : "network");
    }
  })();

  return () => {
    controller.abort();
    finish("aborted");
  };
}
