/**
 * 管理后台客户端（2026-10-10）
 *
 * 边界（产品决定，写在最前面）：后台**只看聚合数字** ——
 * 注册数、活跃数、各功能使用量、质量基线、在跑会话数。
 * 没有「用户列表」这条路径：要授予管理员，直接填手机号；名册里手机号也是打码的。
 *
 * 权限由后端判定（owner / admin），前端只是**不把入口显示给无关的人**，
 * 真正的门在 `/api/admin/*` 上，不依赖这里。
 */

import { BFF } from "./client";

export type AdminAccounts = {
  total: number;
  by_role: Record<string, number>;
  by_status: Record<string, number>;
  new_7d: number;
  new_30d: number;
  active_7d: number;
  /** 未过期的登录会话数 ≈「当前有多少人处于登录态」 */
  live_logins: number;
};

export type AdminOverview = {
  accounts: AdminAccounts;
  usage: {
    events: number;
    by_source: Record<string, number>;
    by_event: Record<string, number>;
    by_status: Record<string, number>;
  };
  quality: {
    citations: { accepted: number; rejected: number; traceable_rate: number | null };
    latency_ms: { count: number; p50: number | null; p95: number | null; max: number | null };
    library_hit_rate: number | null;
    corroboration: Record<string, number | null>;
  };
  live_sessions: { by_branch: Record<string, number>; total: number };
  source_filter: string | null;
  generated_at: string;
};

export type AdminStaff = {
  id: number;
  phone_masked: string;
  role: "owner" | "admin";
  status: string;
  created_at: string;
  last_login_at: string | null;
};

export class AdminError extends Error {
  constructor(
    public code: string,
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${BFF}${path}`, {
    ...init,
    headers: { "content-type": "application/json", accept: "application/json", ...(init.headers ?? {}) },
  });
  const payload = (await response.json().catch(() => null)) as (T & { error?: { code: string; message: string } }) | null;
  if (!response.ok) {
    throw new AdminError(payload?.error?.code ?? "request_failed", payload?.error?.message ?? "请求失败。", response.status);
  }
  return payload as T;
}

export const adminApi = {
  /** 总览。`source` 缺省是 real（真实运行）；传 "all" 看全部来源。 */
  overview: (source?: "real" | "test" | "offline" | "all") =>
    call<AdminOverview>(`/admin/overview${source ? `?source=${source}` : ""}`),

  /** 管理员名册（只含 owner / admin，手机号打码）。 */
  staff: () => call<{ items: AdminStaff[] }>("/admin/staff"),

  /** 按手机号授予 / 收回管理员（仅 owner 可用）。 */
  setRole: (phone: string, role: "admin" | "member") =>
    call<{ phone_masked: string; role: string }>("/admin/staff", {
      method: "POST",
      body: JSON.stringify({ phone, role }),
    }),
};
