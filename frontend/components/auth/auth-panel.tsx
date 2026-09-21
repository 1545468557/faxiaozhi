"use client";

/**
 * 登录 / 注册（迭代 2-4）
 *
 * 口径：邀请码 + 用户名 + 密码；登录态是 **HttpOnly Cookie**（前端读不到，只能问 `/api/auth/me`）。
 * 失败提示一律用后端给的话（不区分"用户不存在/密码错"，防枚举）。
 */

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { KeyRound, LogIn, UserPlus } from "lucide-react";
import { useSessionState } from "@/components/workspace-session";
import { authApi, AuthError, type AuthUser } from "@/lib/api/auth";

export function AuthPanel({ next = "/" }: { next?: string }) {
  const router = useRouter();
  const [, setCurrentUser] = useSessionState<AuthUser | null | undefined>("auth:current-user", undefined);
  const [mode, setMode] = useState<"login" | "register">("login");
  const [inviteCode, setInviteCode] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submitRef = useRef<HTMLButtonElement | null>(null);

  const focusSubmit = () => {
    window.setTimeout(() => submitRef.current?.scrollIntoView({ block: "center", behavior: "smooth" }), 60);
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = mode === "register"
        ? await authApi.register(inviteCode.trim(), username.trim(), password)
        : await authApi.login(username.trim(), password);
      setCurrentUser(result.user);
      router.push(next);
      router.refresh();
    } catch (cause) {
      setError(cause instanceof AuthError ? cause.message : "操作没成功，请稍后再试。");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="ct-wrap" style={{ maxWidth: 520, padding: "22px 20px 40px" }}>
      <header className="ct-head" style={{ marginBottom: 4 }}>
        <h1 style={{ fontSize: 20, marginBottom: 2 }}>
          <KeyRound size={18} aria-hidden="true" style={{ verticalAlign: "-3px", marginRight: 6 }} />
          法小智 · {mode === "login" ? "登录" : "注册"}
        </h1>
        <p style={{ fontSize: 13 }}>
          {mode === "login" ? "登录后，问过的、研究过的、审过的都记在你自己名下。" : "用邀请码注册（内部试用，邀请码由管理员生成）。"}
        </p>
      </header>

      <section className="ct-card">
        <div className="ct-actions" style={{ marginTop: 0 }}>
          <button type="button" className={mode === "login" ? "ct-btn ct-btn-primary" : "ct-btn"} onClick={() => { setMode("login"); setError(""); }}>
            <LogIn size={15} aria-hidden="true" />
            登录
          </button>
          <button type="button" className={mode === "register" ? "ct-btn ct-btn-primary" : "ct-btn"} onClick={() => { setMode("register"); setError(""); focusSubmit(); }}>
            <UserPlus size={15} aria-hidden="true" />
            用邀请码注册
          </button>
        </div>

        <form onSubmit={submit} style={{ marginTop: 16, display: "grid", gap: 10 }}>
          {mode === "register" && (
            <input
              className="ct-input"
              value={inviteCode} onChange={e => setInviteCode(e.target.value)} placeholder="邀请码（fzx-…）"
              autoComplete="off"
              required
            />
          )}
          <input className="ct-input" value={username} onChange={e => setUsername(e.target.value)} placeholder="用户名（3-32 位字母数字下划线短横线）" autoComplete="username" required />
          <input
            className="ct-input"
            type="password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            placeholder={mode === "register" ? "密码（至少 8 位）" : "密码"}
            autoComplete={mode === "register" ? "new-password" : "current-password"}
            required
          />
          {error && (
            <p className="ct-blocked" role="alert">
              {error}
            </p>
          )}
          <div className="ct-actions" style={{ marginTop: 4 }}>
            <button ref={submitRef} type="submit" className="ct-btn ct-btn-primary" disabled={busy} style={{ minWidth: 140 }}>
              {busy ? "正在处理…" : mode === "login" ? "登录" : "注册并登录"}
            </button>
          </div>
        </form>

        <p className="ct-mini" style={{ marginTop: 12 }}>
          填完直接按 <b>回车（Enter）</b> 也能提交。登录状态保持 7 天；密码不会被明文保存；邀请码只能用一次。
        </p>
      </section>
    </div>
  );
}
