/**
 * 集中式 API 层（阶段 3-1，手册 §12.1）
 *
 * 所有请求都打到**同源** `/api/bff/**`，由 Next route handler 转发到后端。
 * 浏览器**永远不直连**后端（后端无 CORS 头；且后端地址与错误原文收口在一处）。
 *
 * 规则：
 * - 不自己重试计费请求（底线 5）——`retryable` 只用于显示「重试」按钮，由用户点；
 * - 错误统一解析后端 `{error:{code,message}}`（底线 3），解析不出才用本地兜底；
 * - 组件里不允许出现裸 `fetch`。
 */

import { describeError, type ErrorInfo } from "./errors";
import type { Bootstrap, Module, SessionState } from "./types";

export const BFF = "/api/bff";

export class ApiError extends Error {
  code: string;
  status: number;
  detail?: Record<string, unknown>;
  info: ErrorInfo;

  constructor(code: string, message: string, status = 0, detail?: Record<string, unknown>) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.detail = detail;
    this.info = describeError(code, message);
  }
}

type RequestOptions = {
  method?: string;
  body?: unknown;
  form?: FormData;
  signal?: AbortSignal;
  timeoutMs?: number;
};

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, form, signal, timeoutMs = 30000 } = options;
  const controller = new AbortController();
  const timer = timeoutMs > 0 ? setTimeout(() => controller.abort(), timeoutMs) : null;
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort);

  const headers: Record<string, string> = { accept: "application/json" };
  if (body !== undefined) headers["content-type"] = "application/json";

  let response: Response;
  try {
    response = await fetch(`${BFF}${path}`, {
      method,
      headers,
      // FormData 不能手写 content-type（boundary 会坏）
      body: form ?? (body === undefined ? undefined : JSON.stringify(body)),
      cache: "no-store",
      signal: controller.signal,
    });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new ApiError("backend_unreachable", describeError("backend_unreachable").fallback, 0);
  } finally {
    if (timer) clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }

  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }

  if (!response.ok) {
    const error = (payload as { error?: { code?: string; message?: string; detail?: Record<string, unknown> } })
      ?.error;
    const code = error?.code ?? (response.status === 403 ? "backend_blocked" : "internal_error");
    throw new ApiError(code, error?.message ?? `请求失败（HTTP ${response.status}）`, response.status, error?.detail);
  }

  if (payload === null) {
    // 只读接口返回空体属于契约异常，但不能静默当成功
    throw new ApiError("internal_error", "后端返回内容无法解析。", response.status);
  }
  return payload as T;
}

const enc = encodeURIComponent;

export const api = {
  // ---------------- 全局 ----------------
  bootstrap: () => request<Bootstrap>("/bootstrap"),
  library: () => request<Record<string, unknown>>("/library"),
  metrics: (source = "real") => request<Record<string, unknown>>(`/metrics?source=${enc(source)}`),

  // ---------------- 会话 ----------------
  createSession: (branch: Module) => request<{ session_id: string; created_at: string }>("/session", { method: "POST", body: { branch } }),
  state: (sid: string) => request<SessionState>(`/session/${enc(sid)}/state`),
  evidence: (sid: string) => request<Record<string, unknown>>(`/session/${enc(sid)}/evidence`),
  degradation: (sid: string) => request<Record<string, unknown>>(`/session/${enc(sid)}/degradation`),

  // ---------------- 研究 / 咨询 ----------------
  message: (sid: string, text: string, extra: Record<string, unknown> = {}) =>
    request<{ run_id: string; status: string }>(`/session/${enc(sid)}/message`, {
      method: "POST",
      body: { text, ...extra },
    }),
  consultAnswer: (sid: string, facts: string, options: { skipClarification?: boolean; expectedRound?: number } = {}) =>
    request<{ run_id: string; status: string }>(`/session/${enc(sid)}/consult/answer`, {
      method: "POST",
      body: { facts, skip_clarification: options.skipClarification, expected_round: options.expectedRound },
    }),
  consultExport: (sid: string) => request<ExportResult>(`/session/${enc(sid)}/consult/export`, { method: "POST", body: {} }),

  // ---------------- 人工门与流程控制 ----------------
  checkpoint: (sid: string, kind: "sample_confirm" | "stance_confirm", payload: Record<string, unknown>) =>
    request<Record<string, unknown>>(`/session/${enc(sid)}/checkpoint`, { method: "POST", body: { kind, payload } }),
  supplement: (sid: string) => request<Record<string, unknown>>(`/session/${enc(sid)}/supplement`, { method: "POST", body: {} }),
  resolveConflict: (sid: string, sourceId: string, decision: "use_user_material" | "exclude_source") =>
    request<Record<string, unknown>>(`/session/${enc(sid)}/conflict/resolve`, {
      method: "POST",
      body: { source_id: sourceId, decision },
    }),
  retry: (sid: string) => request<{ run_id: string; status: string }>(`/session/${enc(sid)}/retry`, { method: "POST", body: {} }),

  // ---------------- 材料 ----------------
  upload: (sid: string, form: FormData) =>
    request<{ materials: Record<string, unknown>[]; rejected: { filename: string; message?: string }[] }>(
      `/session/${enc(sid)}/upload`,
      { method: "POST", form, timeoutMs: 120000 },
    ),
  materials: (sid: string) => request<Record<string, unknown>[]>(`/session/${enc(sid)}/materials`),
  verifyMaterial: (sid: string, sourceId: string) =>
    request<Record<string, unknown>>(`/session/${enc(sid)}/material/${enc(sourceId)}/verify`, { method: "POST", body: {} }),
  setMaterialRole: (sid: string, sourceId: string, role: "case" | "statute" | "contract") =>
    request<Record<string, unknown>>(`/session/${enc(sid)}/material/${enc(sourceId)}/role`, { method: "POST", body: { role } }),

  // ---------------- 合同 ----------------
  contractReview: (sid: string, body: { requirements?: string } = {}) => request<{ run_id: string; status: string }>(`/session/${enc(sid)}/contract/review`, { method: "POST", body }),
  contractExport: (sid: string, body: { review?: Record<string, {status: string; suggestion: string}> } = {}) => request<ExportResult>(`/session/${enc(sid)}/contract/export`, { method: "POST", body }),

  // ---------------- 导出 ----------------
  exportResearch: (sid: string) => request<ExportResult>(`/session/${enc(sid)}/export`, { method: "POST", body: {} }),
};

export type ExportResult = {
  ok: boolean;
  filename: string | null;
  download_url: string | null;
  detail?: string;
};

/** Word 下载地址（走代理，文件名做 URL 编码） */
export function downloadUrl(filename: string): string {
  const name = filename.split(/[\\/]/).pop() ?? filename;
  return `${BFF}/exports/${encodeURIComponent(name)}`;
}
