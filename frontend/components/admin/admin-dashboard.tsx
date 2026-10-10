"use client";

/**
 * 管理后台总览（2026-10-10）
 *
 * 产品边界（先说清楚，再写界面）：
 *   本页**只有聚合数字** —— 注册数、活跃数、各功能使用量、质量基线、在跑会话数。
 *   页面里没有也不该有：用户提问、材料原文、会话 id。
 *   要授予管理员，直接填手机号；名册里手机号也是打码的（没有「用户列表」这条路）。
 *
 * 权限：后端判定 owner / admin；前端只是不把入口显示给无关的人，
 * 真正的门在 /api/admin/* 上 —— 前端隐藏入口不是安全措施。
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { BarChart3, RefreshCw, ShieldCheck, UserCog } from "lucide-react";
import { authApi, type AuthUser } from "@/lib/api/auth";
import { adminApi, AdminError, type AdminOverview, type AdminStaff } from "@/lib/api/admin";

const BRANCH_LABEL: Record<string, string> = {
  research: "类案检索",
  consult: "代拟咨询",
  contract: "合同审查",
};

const ROLE_LABEL: Record<string, string> = {
  owner: "创始管理员",
  admin: "管理员",
  member: "普通成员",
};

const SOURCE_LABEL: Record<string, string> = {
  real: "真实运行",
  test: "自动化测试",
  offline: "离线演示",
  unknown: "早期数据",
};

type Screen = "loading" | "anon" | "denied" | "ready";

function pct(value: number | null | undefined): string {
  return typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "—";
}

function num(value: number | null | undefined): string {
  return typeof value === "number" ? value.toLocaleString("zh-CN") : "—";
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div style={{ minWidth: 120 }}>
      <div style={{ fontSize: 12.5, color: "var(--v2-muted)" }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 600, color: "var(--v2-ink)", lineHeight: 1.35 }}>{value}</div>
      {hint && <div style={{ fontSize: 11.5, color: "var(--v2-faint)" }}>{hint}</div>}
    </div>
  );
}

export function AdminDashboard() {
  const [screen, setScreen] = useState<Screen>("loading");
  const [me, setMe] = useState<AuthUser | null>(null);
  const [overview, setOverview] = useState<AdminOverview | null>(null);
  const [staff, setStaff] = useState<AdminStaff[]>([]);
  const [source, setSource] = useState<"real" | "all">("real");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  const [phone, setPhone] = useState("");
  const [role, setRole] = useState<"admin" | "member">("admin");

  const load = useCallback(
    async (nextSource: "real" | "all") => {
      setBusy(true);
      setError("");
      try {
        const [data, roster] = await Promise.all([
          adminApi.overview(nextSource === "all" ? "all" : "real"),
          adminApi.staff(),
        ]);
        setOverview(data);
        setStaff(roster.items);
        setScreen("ready");
      } catch (cause) {
        if (cause instanceof AdminError) {
          if (cause.status === 401) setScreen("anon");
          else if (cause.status === 403) setScreen("denied");
          else setError(cause.message);
        } else {
          setError("加载失败，请稍后再试。");
        }
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  useEffect(() => {
    let alive = true;
    (async () => {
      const user = await authApi.me().catch(() => null);
      if (!alive) return;
      setMe(user);
      if (!user) {
        setScreen("anon");
        return;
      }
      if (user.role !== "owner" && user.role !== "admin") {
        setScreen("denied");
        return;
      }
      await load("real");
    })();
    return () => {
      alive = false;
    };
  }, [load]);

  async function grant() {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await adminApi.setRole(phone.trim(), role);
      setNotice(`已把 ${result.phone_masked} 设为「${ROLE_LABEL[result.role] ?? result.role}」。`);
      setPhone("");
      const roster = await adminApi.staff();
      setStaff(roster.items);
    } catch (cause) {
      setError(cause instanceof AdminError ? cause.message : "操作失败，请稍后再试。");
    } finally {
      setBusy(false);
    }
  }

  if (screen === "loading") {
    return (
      <section className="ct-card">
        <h2 style={{ fontSize: 18, marginBottom: 8 }}>
          <BarChart3 size={16} aria-hidden="true" style={{ verticalAlign: "-2px", marginRight: 6 }} />
          管理后台
        </h2>
        <p className="ct-mini">正在检查权限与加载统计…</p>
      </section>
    );
  }

  if (screen === "anon") {
    return (
      <section className="ct-card">
        <h2 style={{ fontSize: 18, marginBottom: 8 }}>管理后台</h2>
        <p className="ct-mini">后台只对管理员开放，请先用管理员账号登录。</p>
        <div className="ct-actions">
          <Link
            href="/login?next=%2Fadmin"
            style={{
              display: "inline-flex", alignItems: "center", gap: 6, padding: "9px 16px",
              borderRadius: 9, background: "var(--v2-teal)", color: "#fff",
              textDecoration: "none", fontSize: 13.5,
            }}
          >
            去登录
          </Link>
        </div>
      </section>
    );
  }

  if (screen === "denied") {
    return (
      <section className="ct-card">
        <h2 style={{ fontSize: 18, marginBottom: 8 }}>管理后台</h2>
        <p className="ct-mini">
          当前账号（{me?.phone_masked}）不是管理员，无法查看后台。
          需要权限的话，请让创始管理员在后台把你的手机号设为管理员。
        </p>
        <div className="ct-actions">
          <Link href="/" className="ct-btn" style={{ textDecoration: "none" }}>回到首页</Link>
        </div>
      </section>
    );
  }

  const accounts = overview!.accounts;
  const usage = overview!.usage;
  const quality = overview!.quality;
  const live = overview!.live_sessions;
  const topEvents = Object.entries(usage.by_event).slice(0, 8);

  return (
    <div>
      <div className="ct-head" style={{ marginBottom: 16 }}>
        <h1 style={{ margin: 0 }}>管理后台</h1>
        <p className="ct-mini">
          这里只有<b>聚合数字</b>：不含任何用户提问、材料原文或会话内容。
          统计口径与运行记录一致，切换来源可区分「真实运行」与「测试/离线」。
        </p>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14, flexWrap: "wrap" }}>
        <div role="tablist" aria-label="统计来源" style={{ display: "inline-flex", border: "1px solid var(--v2-line)", borderRadius: 10, overflow: "hidden" }}>
          {([["real", "只看真实运行"], ["all", "全部来源"]] as const).map(([key, label]) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={source === key}
              onClick={() => { setSource(key); void load(key); }}
              style={{
                padding: "7px 14px", fontSize: 13, border: 0, cursor: "pointer", font: "inherit",
                background: source === key ? "var(--v2-teal)" : "transparent",
                color: source === key ? "#fff" : "var(--v2-muted)",
              }}
            >
              {label}
            </button>
          ))}
        </div>
        <button type="button" className="ct-btn" onClick={() => void load(source)} disabled={busy}>
          <RefreshCw size={14} aria-hidden="true" style={{ verticalAlign: "-2px", marginRight: 6 }} />
          刷新
        </button>
        <span className="ct-mini" style={{ margin: 0 }}>数据时间 {overview!.generated_at}</span>
      </div>

      {error && <p className="ct-blocked" role="alert">{error}</p>}
      {notice && <p className="ct-mini" role="status" style={{ color: "var(--v2-teal)", fontWeight: 500 }}>{notice}</p>}

      <section className="ct-card" style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 18, marginBottom: 10 }}>账号</h2>
        <div style={{ display: "flex", gap: 26, flexWrap: "wrap" }}>
          <Stat label="累计注册" value={num(accounts.total)} />
          <Stat label="近 7 天新增" value={num(accounts.new_7d)} hint={`近 30 天 ${num(accounts.new_30d)}`} />
          <Stat label="近 7 天登录过" value={num(accounts.active_7d)} />
          <Stat label="当前登录态" value={num(accounts.live_logins)} hint="未过期的登录会话" />
          <Stat label="管理员" value={num((accounts.by_role.admin ?? 0) + (accounts.by_role.owner ?? 0))} hint={`创始管理员 ${num(accounts.by_role.owner ?? 0)}`} />
        </div>
      </section>

      <section className="ct-card" style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 18, marginBottom: 10 }}>
          <BarChart3 size={16} aria-hidden="true" style={{ verticalAlign: "-2px", marginRight: 6 }} />
          使用量
        </h2>
        <div style={{ display: "flex", gap: 26, flexWrap: "wrap", marginBottom: 14 }}>
          <Stat label="事件总数" value={num(usage.events)} />
          <Stat label="在跑会话" value={num(live.total)} hint="内存态，空闲 30 分钟释放" />
          {Object.entries(live.by_branch).map(([branch, count]) => (
            <Stat key={branch} label={BRANCH_LABEL[branch] ?? branch} value={num(count)} />
          ))}
        </div>
        <p className="ct-mini" style={{ marginBottom: 6 }}>来源分布</p>
        <div style={{ display: "flex", gap: 18, flexWrap: "wrap", marginBottom: 14 }}>
          {Object.keys(usage.by_source).length === 0 && <span className="ct-mini">暂无记录</span>}
          {Object.entries(usage.by_source).map(([key, count]) => (
            <span key={key} style={{ fontSize: 13 }}>
              {SOURCE_LABEL[key] ?? key} <b>{num(count)}</b>
            </span>
          ))}
        </div>
        <p className="ct-mini" style={{ marginBottom: 6 }}>主要事件（按次数）</p>
        <div style={{ display: "flex", flexDirection: "column", gap: 6, maxWidth: 520 }}>
          {topEvents.length === 0 && <span className="ct-mini">暂无记录</span>}
          {topEvents.map(([event, count]) => (
            <div key={event} style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 13 }}>
              <span style={{ fontFamily: "ui-monospace, monospace", color: "var(--v2-ink)", flex: 1, overflowWrap: "anywhere" }}>{event}</span>
              <span style={{ color: "var(--v2-muted)" }}>{num(count)}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="ct-card" style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 18, marginBottom: 10 }}>
          <ShieldCheck size={16} aria-hidden="true" style={{ verticalAlign: "-2px", marginRight: 6 }} />
          质量基线
        </h2>
        <div style={{ display: "flex", gap: 26, flexWrap: "wrap" }}>
          <Stat
            label="引用可溯源率"
            value={pct(quality.citations.traceable_rate)}
            hint={`采纳 ${num(quality.citations.accepted)} / 驳回 ${num(quality.citations.rejected)}`}
          />
          <Stat label="耗时 p50" value={quality.latency_ms.p50 === null ? "—" : `${num(quality.latency_ms.p50)} ms`} hint={`p95 ${quality.latency_ms.p95 === null ? "—" : num(quality.latency_ms.p95) + " ms"}`} />
          <Stat label="本地资料库命中" value={pct(quality.library_hit_rate)} />
          <Stat label="双源印证占比" value={pct(quality.corroboration?.rate as number | null)} />
        </div>
      </section>

      <section className="ct-card">
        <h2 style={{ fontSize: 18, marginBottom: 8 }}>
          <UserCog size={16} aria-hidden="true" style={{ verticalAlign: "-2px", marginRight: 6 }} />
          管理员
        </h2>
        <p className="ct-mini">
          后台按手机号授予权限，不提供用户列表 —— 名册里只列管理员本人，且手机号打码。
          {me?.role !== "owner" && " 只有创始管理员能改这里的权限。"}
        </p>

        <div style={{ display: "flex", gap: 8, alignItems: "flex-end", flexWrap: "wrap", margin: "14px 0 6px" }}>
          <label style={{ display: "grid", gap: 5, fontSize: 12.5, color: "var(--v2-muted)" }}>
            手机号
            <input
              className="ct-input"
              value={phone}
              onChange={(e) => setPhone(e.target.value.replace(/\D/g, "").slice(0, 11))}
              inputMode="numeric"
              placeholder="11 位手机号"
              style={{ padding: "9px 12px", fontSize: 14, width: 190 }}
            />
          </label>
          <label style={{ display: "grid", gap: 5, fontSize: 12.5, color: "var(--v2-muted)" }}>
            设为
            <select
              className="ct-input"
              value={role}
              onChange={(e) => setRole(e.target.value as "admin" | "member")}
              style={{ padding: "9px 12px", fontSize: 14 }}
            >
              <option value="admin">管理员</option>
              <option value="member">普通成员</option>
            </select>
          </label>
          <button
            type="button"
            className="ct-btn"
            onClick={grant}
            disabled={busy || !/^1\d{10}$/.test(phone.trim()) || me?.role !== "owner"}
          >
            提交
          </button>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 10, maxWidth: 560 }}>
          {staff.map((row) => (
            <div key={row.id} style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 13.5, borderTop: "1px solid var(--v2-line)", paddingTop: 8 }}>
              <span style={{ color: "var(--v2-ink)" }}>{row.phone_masked}</span>
              <span style={{ fontSize: 12, padding: "2px 8px", borderRadius: 999, background: "var(--v2-teal)", color: "#fff" }}>
                {ROLE_LABEL[row.role] ?? row.role}
              </span>
              <span className="ct-mini" style={{ margin: 0 }}>
                {row.last_login_at ? `最近登录 ${row.last_login_at.slice(0, 10)}` : "尚未登录过"}
              </span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
