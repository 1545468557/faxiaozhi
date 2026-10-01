/**
 * 账号客户端（2026-09-27 改造：手机号 + 验证码 / 密码）
 *
 * 登录态用 **HttpOnly Cookie** 保存：前端 JS 读不到它（这是有意的），
 * 所以"我是否已登录"只能问后端 `GET /api/auth/me`。
 *
 * 账号标识是**手机号**；用户名登录已移除。
 */

import { BFF } from "./client";

export type AuthUser = {
  id: number;
  /** 完整手机号（只出现在自己的账号页） */
  phone: string;
  /** 打码手机号（138****8888），用于导航等处显示 */
  phone_masked: string;
  role: "owner" | "member";
  /** 注册时是否设过密码。false ⇒ 只能走验证码登录 */
  has_password: boolean;
  expires_at?: string;
};

/** 验证码用途。注册的码不能拿来登录，反之亦然。 */
export type CodePurpose = "register" | "login" | "change_password";

/** 发码结果。**后端不会回传验证码本身** —— 界面也不该假设拿得到。 */
export type SentCode = {
  phone_masked: string;
  expires_in: number;
  /** 多久之后才能再发（秒）。界面倒计时用这个值，别自己写死 60。 */
  retry_after: number;
  channel: string;
  /** true 表示当前是模拟发码（验证码写在服务端日志与 data/sms-outbox.jsonl） */
  mock: boolean;
};

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

  /** 发验证码。注册 / 登录 / 改密码各有各的用途码。 */
  sendCode: (phone: string, purpose: CodePurpose) =>
    call<SentCode>("/auth/code", { method: "POST", body: JSON.stringify({ phone, purpose }) }),

  /** 邀请码 + 手机号 + 验证码注册；`password` 可选（收不到短信时的备用登录方式）。 */
  register: (inviteCode: string, phone: string, code: string, password?: string) =>
    call<{ user: AuthUser }>("/auth/register", {
      method: "POST",
      body: JSON.stringify({ invite_code: inviteCode, phone, code, password: password ?? "" }),
    }),

  /** 验证码登录（主路径） */
  loginWithCode: (phone: string, code: string) =>
    call<{ user: AuthUser }>("/auth/login", { method: "POST", body: JSON.stringify({ phone, code }) }),

  /** 密码登录（辅路径，仅对注册时设过密码的账号可用） */
  loginWithPassword: (phone: string, password: string) =>
    call<{ user: AuthUser }>("/auth/login", { method: "POST", body: JSON.stringify({ phone, password }) }),

  logout: () => call<{ ok: boolean }>("/auth/logout", { method: "POST" }),

  /**
   * 改密码 / 首次设密码。
   * 给 `oldPassword` 是常规改密；给 `code`（purpose=change_password）是
   * 给「注册时没设密码」的账号首次设密码用的。
   */
  changePassword: (newPassword: string, proof: { oldPassword?: string; code?: string }) =>
    call<{ ok: boolean }>("/auth/password", {
      method: "POST",
      body: JSON.stringify({
        new_password: newPassword,
        old_password: proof.oldPassword ?? "",
        code: proof.code ?? "",
      }),
    }),

  invites: () =>
    call<{ items: { id: number; note: string; created_at: string; expires_at: string; used_at: string | null }[] }>(
      "/admin/invites",
    ),
  createInvite: (note: string, expiresDays = 30) =>
    call<{ code: string; expires_at: string }>("/admin/invites", {
      method: "POST",
      body: JSON.stringify({ note, expires_days: expiresDays }),
    }),
};
