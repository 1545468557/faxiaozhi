"use client";

/**
 * 顶栏右侧的账号入口（「法小智」品牌旁）。
 *
 * 2026-10-10 按用户要求：登录/注册入口必须在「法小智」旁边一眼可见，
 * 只放在「我的」页里等于没有入口 —— 用户不会为了登录先去翻「我的」。
 *
 * 这里只做入口，表单本体在独立登录页 /login（2026-10-10 按用户拍板从「我的」里拆出来）：
 *   未登录 → 「登录 / 注册」按钮，点击去 /login?next=当前页，登录完自动回来；
 *   已登录 → 打码手机号，点击去 /me（那里有账号状态与退出）。
 *
 * 登录 / 退出时 account-section 会 dispatch "faxiaozhi:auth-changed"，
 * 这里监听它即时刷新，不用刷新页面。
 *
 * 样式用 CSS 变量（var(--v2-teal)）跟主题走；本文件不在主题快照里，
 * 不写硬编码品牌色，避免切主题后这里颜色不跟着变。
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { authApi, type AuthUser } from "@/lib/api/auth";

export function TopbarAuth() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [ready, setReady] = useState(false);
  const pathname = usePathname();

  useEffect(() => {
    let alive = true;
    const check = () =>
      authApi.me()
        .then((u) => { if (alive) { setUser(u); setReady(true); } })
        .catch(() => { if (alive) setReady(true); });
    void check();
    window.addEventListener("faxiaozhi:auth-changed", check);
    return () => { alive = false; window.removeEventListener("faxiaozhi:auth-changed", check); };
  }, []);

  // 未查完前给个固定宽度的占位，避免顶栏高度跳动
  if (!ready) return <span style={{ display: "inline-block", width: 68 }} aria-hidden="true" />;

  if (user) {
    return (
      <Link
        href="/me"
        aria-label={`已登录 ${user.phone_masked}，点此管理账号`}
        title="账号与退出"
        style={{
          fontSize: 13, padding: "6px 13px", borderRadius: 999,
          border: "1px solid var(--v2-line)", background: "#fff",
          color: "var(--v2-ink)", textDecoration: "none", whiteSpace: "nowrap",
        }}
      >
        {user.phone_masked}
      </Link>
    );
  }

  return (
    <Link
      href={`/login?next=${encodeURIComponent(pathname || "/")}`}
      aria-label="登录或注册"
      title="登录 / 注册"
      style={{
        fontSize: 13, padding: "6px 14px", borderRadius: 999,
        background: "var(--v2-teal)", color: "#fff",
        textDecoration: "none", whiteSpace: "nowrap",
      }}
    >
      登录 / 注册
    </Link>
  );
}
