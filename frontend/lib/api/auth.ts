/**
 * 账号客户端（迭代 2-1/2-4）
 *
 * 登录态用 **HttpOnly Cookie** 保存：前端 JS 读不到它（这是有意的），
 * 所以"我是否已登录"只能问后端 `GET /api/auth/me`。
 */

import { BFF } from "./client";

export type AuthUser = { id: number; username: string; role: "owner" | "member"; expires_at?: string };

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${BFF}${path}`, {
    ...init,
    headers: { "content-type": "application/json", accept: "application/json", ...(init.headers ?? {}) },
  });
  const payload = (await response.json().catch(() => null)) as (T & { error?: { code: string; message: string } }) | null;
  if (!response.ok) {
    throw new AuthError(payload?.error?.code ?? "request_failed", payload?.error?.message ?? "请求失败。", response.status);
  }
  return payload as T;
}

export class AuthError extends Error {
  constructor(
    public code: string,
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

export const authApi = {
  /** 当前登录态；未登录返回 null（不抛错） */
  async me(): Promise<AuthUser | null> {
    try {
      const data = await call<{ user: AuthUser }>("/auth/me");
      return data.user;
    } catch (cause) {
      if (cause instanceof AuthError && cause.status === 401) return null;
      throw cause;
    }
  },
  register: (inviteCode: string, username: string, password: string) =>
    call<{ user: AuthUser }>("/auth/register", {
      method: "POST",
      body: JSON.stringify({ invite_code: inviteCode, username, password }),
    }),
  login: (username: string, password: string) =>
    call<{ user: AuthUser }>("/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  logout: () => call<{ ok: boolean }>("/auth/logout", { method: "POST" }),
  changePassword: (oldPassword: string, newPassword: string) =>
    call<{ ok: boolean }>("/auth/password", {
      method: "POST",
      body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
    }),
  invites: () => call<{ items: { id: number; note: string; created_at: string; expires_at: string; used_at: string | null }[] }>("/admin/invites"),
  createInvite: (note: string, expiresDays = 30) =>
    call<{ code: string; expires_at: string }>("/admin/invites", { method: "POST", body: JSON.stringify({ note, expires_days: expiresDays }) }),
};
