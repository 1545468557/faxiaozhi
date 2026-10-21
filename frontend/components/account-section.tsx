"use client";

/**
 * 账号区，两种形态（2026-10-10 按用户拍板拆分）：
 *
 *   variant="full"   完整登录/注册表单 —— 只出现在独立登录页 /login（app/login/page.tsx）。
 *                      登录/注册成功后回调 onAuthed，登录页用它跳回来源页面。
 *   variant="status" 只显示账号状态 —— 「我的」页用。未登录时不再内嵌表单
 *                      （表单只留一处，避免两处都能登录造成状态不同步），
 *                      显示「去登录 / 注册」引导；已登录显示手机号 + 退出。
 *
 * 为什么不做成「先试登录、失败自动转注册」：后端在「手机号未注册」时会把那条登录码
 * 消费掉（app/auth/core.py → login_with_code），自动降级会让用户白等一条短信、还得重发。
 * 所以给明确的 [登录] / [注册] 切换：注册才需要邀请码，两种码用途不同、互不通用。
 *
 * 样式全部复用 contract.css 的 .ct-* 类，强调色用 var(--v2-teal) 跟主题走。
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { KeyRound, LogOut, ShieldCheck } from "lucide-react";
import { authApi, AuthError, type AuthUser } from "@/lib/api/auth";

type Mode = "login" | "register";
type Status = "loading" | "signedOut" | "signedIn";

const ROLE_LABEL: Record<string, string> = { owner: "管理员", member: "成员" };

/**
 * 后端缺接口（部署版本不一致时会出现 `not_found` + "接口不存在。"）不该原样显示给用户：
 * 用户看不懂，也帮不上忙。这里统一换成可行动的人话；其它业务错误仍保留后端原文。
 */
function authErrorMessage(cause: unknown, fallback: string): string {
  if (!(cause instanceof AuthError)) return fallback;
  if (cause.code === "not_found" || /接口不存在|not\s*found/i.test(cause.message)) {
    return "登录服务暂时不可用，请稍后再试（站点正在更新）。";
  }
  return cause.message;
}

export function AccountSection({ variant = "full", onAuthed }: { variant?: "full" | "status"; onAuthed?: (user: AuthUser) => void } = {}) {
  const [status, setStatus] = useState<Status>("loading");
  const [user, setUser] = useState<AuthUser | null>(null);

  const [mode, setMode] = useState<Mode>("login");
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [invite, setInvite] = useState("");
  const [password, setPassword] = useState("");

  const [sent, setSent] = useState<{ phone_masked: string; mock: boolean; channel: string } | null>(null);
  const [cooldown, setCooldown] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [hint, setHint] = useState("");

  useEffect(() => {
    let alive = true;
    authApi.me()
      .then((u) => { if (!alive) return; setUser(u); setStatus(u ? "signedIn" : "signedOut"); })
      .catch(() => { if (alive) setStatus("signedOut"); });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = setTimeout(() => setCooldown((c) => c - 1), 1000);
    return () => clearTimeout(timer);
  }, [cooldown]);

  const phoneOk = /^1\d{10}$/.test(phone.trim());
  const codeOk = /^\d{6}$/.test(code.trim());
  const canSend = phoneOk && cooldown <= 0 && !busy;
  const canSubmit = phoneOk && codeOk && !busy && (mode === "login" || invite.trim().length > 0);

  function switchMode(next: Mode) {
    if (next === mode) return;
    setMode(next);
    setCode(""); setSent(null); setError(""); setHint("");
  }

  async function sendCode() {
    setError(""); setHint(""); setBusy(true);
    try {
      const res = await authApi.sendCode(phone.trim(), mode === "register" ? "register" : "login");
      setSent({ phone_masked: res.phone_masked, mock: res.mock, channel: res.channel });
      setCooldown(res.retry_after);
    } catch (cause) {
      setError(authErrorMessage(cause, "验证码发送失败，请稍后再试。"));
    } finally {
      setBusy(false);
    }
  }

  async function submit() {
    setError(""); setHint(""); setBusy(true);
    try {
      const result = mode === "login"
        ? await authApi.loginWithCode(phone.trim(), code.trim())
        : await authApi.register(invite.trim(), phone.trim(), code.trim(), password.trim() || undefined);
      setUser(result.user);
      setStatus("signedIn");
      window.dispatchEvent(new Event("faxiaozhi:auth-changed"));
      onAuthed?.(result.user);
    } catch (cause) {
      if (!(cause instanceof AuthError)) { setError("请求失败，请稍后再试。"); return; }
      setError(authErrorMessage(cause, "请求失败，请稍后再试。"));
      if (mode === "login" && cause.code === "auth_failed") {
        setHint("这个手机号还没注册。请切到「注册」、填邀请码，并重新发一条验证码（刚那条登录码已被作废）。");
      }
      if (mode === "register" && cause.code === "phone_taken") {
        setHint("这个手机号已经注册过了，切到「登录」即可。");
      }
    } finally {
      setBusy(false);
    }
  }

  async function logout() {
    setBusy(true);
    try {
      await authApi.logout();
    } finally {
      setUser(null);
      setStatus("signedOut");
      setPhone(""); setCode(""); setInvite(""); setPassword(""); setSent(null); setError(""); setHint("");
      setBusy(false);
      window.dispatchEvent(new Event("faxiaozhi:auth-changed"));
    }
  }

  if (status === "loading") {
    return (
      <section className="ct-card">
        <h2 style={{ fontSize: 18, marginBottom: 8 }}><KeyRound size={16} aria-hidden="true" style={{ verticalAlign: "-2px", marginRight: 6 }} />账号</h2>
        <p className="ct-mini">正在检查登录状态…</p>
      </section>
    );
  }

  if (status === "signedIn" && user) {
    return (
      <section className="ct-card">
        <h2 style={{ fontSize: 18, marginBottom: 8 }}><KeyRound size={16} aria-hidden="true" style={{ verticalAlign: "-2px", marginRight: 6 }} />账号</h2>
        <p className="ct-mini">已登录，会话有效期 7 天。登录后你的对话与账号关联，换浏览器需要重新登录。</p>
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 10, margin: "14px 0 4px" }}>
          <span style={{ fontSize: 15, fontWeight: 500, color: "var(--v2-ink)" }}>{user.phone_masked}</span>
          <span style={{ fontSize: 12, padding: "3px 9px", borderRadius: 999, background: "var(--v2-teal)", color: "#fff" }}>
            {ROLE_LABEL[user.role] ?? user.role}
          </span>
          {user.has_password && (
            <span style={{ fontSize: 12, color: "var(--v2-faint)", display: "inline-flex", alignItems: "center", gap: 4 }}>
              <ShieldCheck size={13} aria-hidden="true" /> 已设密码
            </span>
          )}
        </div>
        <div className="ct-actions">
          {/* 后台入口只给 owner/admin 显示（2026-10-10）。这不是安全措施 ——
              真正的门在 /api/admin/* 上；这里只是别把入口摆在无关的人眼前。 */}
          {(user.role === "owner" || user.role === "admin") && (
            <Link
              href="/admin"
              style={{
                display: "inline-flex", alignItems: "center", gap: 6,
                padding: "9px 16px", borderRadius: 9,
                border: "1px solid var(--v2-line)", background: "#fff",
                color: "var(--v2-ink)", textDecoration: "none", fontSize: 13.5,
              }}
            >
              <ShieldCheck size={14} aria-hidden="true" />
              管理后台
            </Link>
          )}
          <button type="button" className="ct-btn" onClick={logout} disabled={busy}>
            <LogOut size={14} aria-hidden="true" style={{ verticalAlign: "-2px", marginRight: 6 }} />
            退出登录
          </button>
        </div>
      </section>
    );
  }

  // 「我的」页用的状态卡：未登录时不内嵌表单（表单只留 /login 一处），引导去登录页
  if (variant === "status") {
    return (
      <section className="ct-card">
        <h2 style={{ fontSize: 18, marginBottom: 8 }}><KeyRound size={16} aria-hidden="true" style={{ verticalAlign: "-2px", marginRight: 6 }} />账号</h2>
        <p className="ct-mini">登录后，你的对话与账号关联；换浏览器需要重新登录。</p>
        <div className="ct-actions">
          <Link
            href="/login?next=%2Fme"
            style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              padding: "9px 16px", borderRadius: 9,
              background: "var(--v2-teal)", color: "#fff",
              textDecoration: "none", fontSize: 13.5,
            }}
          >
            去登录 / 注册
          </Link>
        </div>
      </section>
    );
  }

  return (
    <section className="ct-card">
      <h2 style={{ fontSize: 18, marginBottom: 8 }}><KeyRound size={16} aria-hidden="true" style={{ verticalAlign: "-2px", marginRight: 6 }} />账号</h2>
      <p className="ct-mini">
        {mode === "login"
          ? "用手机号 + 短信验证码登录已有账号。"
          : "新账号需要邀请码（内测阶段）。注册时可以顺便设一个密码，收不到短信时用密码登录。"}
      </p>

      <div role="tablist" aria-label="登录或注册" style={{ display: "inline-flex", gap: 0, marginTop: 14, border: "1px solid var(--v2-line)", borderRadius: 10, overflow: "hidden" }}>
        {(["login", "register"] as Mode[]).map((m) => (
          <button
            key={m}
            type="button"
            role="tab"
            aria-selected={mode === m}
            onClick={() => switchMode(m)}
            style={{
              padding: "8px 18px",
              fontSize: 13.5,
              border: 0,
              cursor: "pointer",
              font: "inherit",
              background: mode === m ? "var(--v2-teal)" : "transparent",
              color: mode === m ? "#fff" : "var(--v2-muted)",
            }}
          >
            {m === "login" ? "登录" : "注册"}
          </button>
        ))}
      </div>

      <div style={{ display: "grid", gap: 10, marginTop: 14, maxWidth: 420 }}>
        <label style={{ display: "grid", gap: 5, fontSize: 12.5, color: "var(--v2-muted)" }}>
          手机号
          <input
            className="ct-input"
            value={phone}
            onChange={(e) => setPhone(e.target.value.replace(/\D/g, "").slice(0, 11))}
            inputMode="numeric"
            autoComplete="tel"
            placeholder="11 位手机号"
            style={{ padding: "10px 12px", fontSize: 14 }}
          />
        </label>

        <label style={{ display: "grid", gap: 5, fontSize: 12.5, color: "var(--v2-muted)" }}>
          短信验证码
          <span style={{ display: "flex", gap: 8 }}>
            <input
              className="ct-input"
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
              inputMode="numeric"
              autoComplete="one-time-code"
              placeholder="6 位验证码"
              style={{ flex: 1, padding: "10px 12px", fontSize: 14 }}
            />
            <button type="button" className="ct-btn" onClick={sendCode} disabled={!canSend} style={{ whiteSpace: "nowrap" }}>
              {cooldown > 0 ? `${cooldown}s 后重发` : "发送验证码"}
            </button>
          </span>
        </label>

        {mode === "register" && (
          <label style={{ display: "grid", gap: 5, fontSize: 12.5, color: "var(--v2-muted)" }}>
            邀请码（内测阶段必填）
            <input
              className="ct-input"
              value={invite}
              onChange={(e) => setInvite(e.target.value.trim())}
              placeholder="邀请码"
              style={{ padding: "10px 12px", fontSize: 14 }}
            />
          </label>
        )}

        {mode === "register" && (
          <label style={{ display: "grid", gap: 5, fontSize: 12.5, color: "var(--v2-muted)" }}>
            设置密码（可选）
            <input
              className="ct-input"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              type="password"
              autoComplete="new-password"
              placeholder="至少 8 位；收不到短信时的备用登录方式"
              style={{ padding: "10px 12px", fontSize: 14 }}
            />
          </label>
        )}

        {sent?.mock && (
          <p className="ct-mini" style={{ color: "var(--v2-muted)", background: "var(--color-background-secondary, #f4f4f6)", padding: "9px 11px", borderRadius: 9 }}>
            当前是<b>模拟发码</b>：短信没有真的发出，验证码写在服务端日志与 <code>data/sms-outbox.jsonl</code>。
            本地开发这样就能测全流程；上线前必须切到真实短信通道。
          </p>
        )}
        {sent && !sent.mock && (
          <p className="ct-mini">验证码已发送至 {sent.phone_masked}（通道 {sent.channel}），5 分钟内有效。</p>
        )}

        {error && (
          <p className="ct-blocked" role="alert" style={{ margin: 0 }}>
            {error}
          </p>
        )}
        {hint && (
          <p className="ct-mini" role="status" style={{ color: "var(--v2-teal)", fontWeight: 500 }}>{hint}</p>
        )}

        <div className="ct-actions" style={{ marginTop: 6 }}>
          <button type="button" className="ct-btn" onClick={submit} disabled={!canSubmit}>
            {mode === "login" ? "登录" : "注册并登录"}
          </button>
        </div>
      </div>
    </section>
  );
}
