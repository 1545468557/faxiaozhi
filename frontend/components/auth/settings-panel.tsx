"use client";

/**
 * 设置（迭代 2-4）：只放**真能用**的东西——账号信息 · 改密码 · 退出登录 ·
 * 生成邀请码（管理员）· 清空本机数据。没做的（注销账号）不摆假按钮，末尾如实说明。
 */

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Info, KeyRound, LogOut, Ticket, Trash2 } from "lucide-react";
import { authApi, AuthError, type AuthUser } from "@/lib/api/auth";
import { useSessionState } from "@/components/workspace-session";
import { clearLocalData } from "@/lib/api/mine";
import { BackendStatus } from "@/components/backend-status";

export function SettingsPanel() {
  const router = useRouter();
  const [, setCurrentUser] = useSessionState<AuthUser | null | undefined>("auth:current-user", undefined);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [notice, setNotice] = useState("");
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [invite, setInvite] = useState<{ code: string; expires_at: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setUser(await authApi.me());
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 挂载时读取登录态
    void load();
  }, [load]);

  const onChangePassword = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setNotice("");
    try {
      await authApi.changePassword(oldPassword, newPassword);
      setOldPassword("");
      setNewPassword("");
      setNotice("密码已修改：其它设备上的登录已失效，这台继续有效。");
    } catch (cause) {
      setNotice(cause instanceof AuthError ? cause.message : "改密码没成功。");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="ct-wrap">
      <header className="ct-head">
        <h1>设置</h1>
        <p>账号、连接状态、本机数据与版本说明。</p>
      </header>
      <BackendStatus />

      {notice && (
        <p className="ct-blocked" role="status" style={{ marginBottom: 14 }}>
          <Info size={14} aria-hidden="true" />
          {notice}
        </p>
      )}

      <section className="ct-card" style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 18, marginBottom: 8 }}>账号</h2>
        <p className="ct-mini">
          {user ? (
            <>
              已登录：<b>{user.username}</b>
              {user.role === "owner" ? "（管理员）" : ""}
            </>
          ) : (
            "当前未登录。"
          )}
        </p>
        <div className="ct-actions">
          <button
            type="button"
            className="ct-btn"
            disabled={busy || !user}
            onClick={async () => {
              setBusy(true);
              try {
                await authApi.logout();
                setCurrentUser(null);
                router.push("/login");
                router.refresh();
              } finally {
                setBusy(false);
              }
            }}
          >
            <LogOut size={14} aria-hidden="true" />
            退出登录
          </button>
        </div>
      </section>

      <section className="ct-card" style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 18, marginBottom: 8 }}>
          <KeyRound size={16} aria-hidden="true" style={{ verticalAlign: "-2px", marginRight: 6 }} />
          改密码
        </h2>
        <form onSubmit={onChangePassword} style={{ display: "grid", gap: 10 }}>
          <input className="ct-input" type="password" value={oldPassword} onChange={e => setOldPassword(e.target.value)} placeholder="原密码" autoComplete="current-password" required />
          <input className="ct-input" type="password" value={newPassword} onChange={e => setNewPassword(e.target.value)} placeholder="新密码（至少 8 位）" autoComplete="new-password" required />
          <div className="ct-actions" style={{ marginTop: 0 }}>
            <button type="submit" className="ct-btn ct-btn-primary" disabled={busy || !user}>
              改密码
            </button>
          </div>
        </form>
        <p className="ct-mini">改完后其它设备上的登录会失效，这台继续有效。</p>
      </section>

      {user?.role === "owner" && (
        <section className="ct-card" style={{ marginBottom: 16 }}>
          <h2 style={{ fontSize: 18, marginBottom: 8 }}>
            <Ticket size={16} aria-hidden="true" style={{ verticalAlign: "-2px", marginRight: 6 }} />
            邀请码（管理员）
          </h2>
          <p className="ct-mini">生成一次性邀请码发给同事；每个码只能用一次。</p>
          <div className="ct-actions">
            <button
              type="button"
              className="ct-btn ct-btn-primary"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                setNotice("");
                try {
                  setInvite(await authApi.createInvite("同事试用"));
                } catch (cause) {
                  setNotice(cause instanceof AuthError ? cause.message : "生成邀请码失败。");
                } finally {
                  setBusy(false);
                }
              }}
            >
              生成一个邀请码
            </button>
          </div>
          {invite && (
            <p className="ct-mini" role="status" style={{ marginTop: 10 }}>
              新邀请码：<b>{invite.code}</b>（有效期至 {invite.expires_at.slice(0, 10)}）—— 只显示这一次，刷新后不再显示。
            </p>
          )}
        </section>
      )}

      <section className="ct-card" style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 18, marginBottom: 8 }}>
          <Trash2 size={16} aria-hidden="true" style={{ verticalAlign: "-2px", marginRight: 6 }} />
          本机数据
        </h2>
        <p className="ct-mini">清掉浏览器里保存的会话与草稿（三条链路 + 首页问答）。不动服务器上的导出文件。</p>
        <div className="ct-actions">
          <button type="button" className="ct-btn" onClick={() => setNotice(`已清空本机保存的 ${clearLocalData()} 项数据。`)}>
            清空本机数据
          </button>
        </div>
      </section>

      <section className="ct-card">
        <h2 style={{ fontSize: 18, marginBottom: 8 }}>还没做的（如实说明）</h2>
        <ul className="ct-range">
          <li><b>注销账号</b>：接口还没做（会真删历史与导出，需二次确认），下一步补</li>
          <li><b>材料长期保存</b>：口径仍是「原文不落盘」，要长期保存得先解决加密与合规</li>
        </ul>
      </section>
    </div>
  );
}
