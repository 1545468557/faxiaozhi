"use client";

/**
 * v2 外壳：左侧导航 + 内容区。
 *
 * 2026-09-20 产品经理指示：
 *  1 **删除案例分析与案例查询**（两个占位页已删）；
 *  2 **把合同审查、类案检索放进左侧导航**；
 *  3 **「法规查找」「我的」加回来**（此前我擅自从导航撤下，产品经理明确要求恢复）。
 * 所以导航共五项：法律问答 / 类案检索 / 合同审查 / 法规查找 / 我的。
 *
 * 每个一级页都有独立网址，点导航换页，刷新后仍在原页。
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { BookOpen, FileText, LogIn, Sparkles, PanelLeft, Search, Settings, User, Clock, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { authApi, type AuthUser } from "@/lib/api/auth";
import { sessions, type V3SessionBrief } from "@/lib/api/v3";
import { useSessionState } from "@/components/workspace-session";

const NAV = [
  { href: "/", label: "法律问答", icon: Sparkles },
  { href: "/research", label: "类案检索", icon: Search },
  { href: "/contract", label: "合同审查", icon: FileText },
  { href: "/statutes", label: "法规查找", icon: BookOpen },
  { href: "/me", label: "我的", icon: User },
];

export function AppShell({ children }: { children: ReactNode }) {
  const [expanded, setExpanded] = useSessionState("navigation:expanded", false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyRows, setHistoryRows] = useState<V3SessionBrief[]>([]);
  const [historyStatus, setHistoryStatus] = useState("");
  const pathname = usePathname() || "/";
  const isAskPage = pathname === "/";
  const isActive = (href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href));

  /**
   * 登录态：Cookie 是 HttpOnly（前端读不到），所以只能问后端 `/api/auth/me`。
   * `undefined` = 还没问到（先不判），`null` = 没登录。
   */
  const [user, setUser] = useSessionState<AuthUser | null | undefined>("auth:current-user", undefined);
  useEffect(() => {
    let alive = true;
    void authApi
      .me()
      .then(found => alive && setUser(found))
      .catch(() => { if (alive) setUser(previous => previous === undefined ? null : previous); });
    return () => {
      alive = false;
    };
  }, [pathname, setUser]);

  useEffect(() => {
    if (!isAskPage || !historyOpen || !user) return;
    const controller = new AbortController();
    void sessions(controller.signal).then(data => {
      if (controller.signal.aborted) return;
      setHistoryRows(data.sessions);
      setHistoryStatus(data.sessions.length ? "" : "还没有历史对话。可以先聊一个问题。");
    }).catch(() => {
      if (!controller.signal.aborted) setHistoryStatus("历史对话暂时加载失败，请关闭后重新打开。");
    });
    return () => controller.abort();
  }, [isAskPage, historyOpen, user]);

  useEffect(() => {
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setExpanded(false); setHistoryOpen(false); }
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [setExpanded]);

  // 未登录：功能页一律挡住（**权限判断在后端**，这里只是不给入口；直接调接口同样会被拒）
  if (user === null) {
    return (
      <div className="v2-app" style={{ gridTemplateColumns: "minmax(0, 1fr)" }}>
        <main className="v2-main" id="main-content" style={{ display: "grid", placeItems: "center", minHeight: "100dvh" }}>
          <div className="ct-card" style={{ maxWidth: 460 }}>
            <h1 style={{ fontSize: 20, margin: "0 0 8px" }}>请先登录</h1>
            <p className="ct-mini" style={{ marginBottom: 14 }}>
              法小智现在需要账号才能使用：你问过的、研究过的、审过的都会记在你自己名下，别人看不到。
              还没有账号就用邀请码注册。
            </p>
            <Link className="ct-btn ct-btn-primary" href="/login">
              <LogIn size={15} aria-hidden="true" />
              去登录 / 注册
            </Link>
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="v2-app v2-nav-shell" data-expanded={expanded}>
      <a className="v2-skip" href="#main-content">跳至主要内容</a>
      {expanded && <button className="nav-mobile-shade" aria-label="收起导航" onClick={() => setExpanded(false)} />}
      <aside className="nav-rail" aria-label="功能导航">
        <button className="nav-rail-toggle" aria-label={expanded ? "收起导航" : "展开导航"} aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
          <PanelLeft size={24} strokeWidth={1.7} /><span className="nav-label">法小智</span>
        </button>
        <div className="nav-rule" />
        <nav className="nav-items" aria-label="主导航">
          {NAV.map(item => {
            const Icon = item.icon;
            return <Link key={item.href} href={item.href} aria-label={item.label} title={item.label} aria-current={isActive(item.href) ? "page" : undefined}
              onClick={() => {
                if (item.href === "/" && pathname === "/") window.dispatchEvent(new Event("faxiaozhi:ask-entrance"));
              }}>
              <Icon size={24} strokeWidth={1.7} aria-hidden="true" /><span className="nav-label">{item.label}</span>
            </Link>;
          })}
        </nav>
        <div className="nav-account">
          {user ? <>
            <Link href="/settings" aria-label="设置" title="设置"><Settings size={24} strokeWidth={1.7} /><span className="nav-label">设置</span></Link>
            <Link href="/me" aria-label="我的账号" title={user.username}><span className="nav-avatar">{user.username.slice(0, 1)}</span><span className="nav-label">{user.username}</span></Link>
          </> : <div className="nav-account-loading" role="status" aria-label="正在确认账号"><span /><span /></div>}
        </div>
      </aside>
      {isAskPage && historyOpen && <>
        <button className="nav-history-shade" aria-label="关闭历史对话" onClick={() => setHistoryOpen(false)} />
        <aside className="nav-history" aria-label="历史对话">
          <div className="nav-history-head"><Clock size={22} />历史对话<button aria-label="关闭历史对话" onClick={() => setHistoryOpen(false)}><X size={18} /></button></div>
          <div className="nav-history-list">
            {historyStatus && <p role="status">{historyStatus}</p>}
            {historyRows.map(row => <a key={row.session_id} href={`/?session=${encodeURIComponent(row.session_id)}`}>
              <span>{row.title || "未命名对话"}</span><small>{new Date(row.updated_at * 1000).toLocaleDateString("zh-CN")}</small>
            </a>)}
          </div>
          <p className="nav-history-note">当前展示本机历史记录，账号间的数据隔离仍在完善。</p>
        </aside>
      </>}
      <main className="v2-main nav-main" id="main-content">
        <header className="nav-topbar">
          {isAskPage && (historyOpen ? <span className="nav-current-module">{NAV.find(item => isActive(item.href))?.label || "法小智"}</span> : <button aria-expanded={false} onClick={() => {setHistoryStatus("正在加载…"); setHistoryRows([]); setHistoryOpen(true);}}><Clock size={20} />历史对话</button>)}
          <span className="nav-brand">法小智</span>
        </header>
        {children}
      </main>
    </div>
  );
}
