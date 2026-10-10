"use client";

/**
 * 登录页主体：完整登录/注册表单 + 登录成功后跳回来源页。
 *
 * ?next= 只接受站内路径（必须以 / 开头且不是 //），防开放重定向。
 * 账号表单本体在 AccountSection(variant="full")；登录/注册成功时它回调 onAuthed，
 * 这里 replace 到 next —— 用 replace 而不是 push，登录页不会留在历史记录里。
 */

import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AccountSection } from "@/components/account-section";

function LoginViewInner() {
  const router = useRouter();
  const params = useSearchParams();
  const raw = params.get("next") ?? "/";
  const next = raw.startsWith("/") && !raw.startsWith("//") ? raw : "/";

  return (
    <div style={{ maxWidth: 640, margin: "0 auto", padding: "32px 20px 64px" }}>
      <h1 style={{ fontSize: 26, margin: "0 0 8px" }}>登录 / 注册</h1>
      <p className="ct-mini" style={{ marginBottom: 20 }}>
        用手机号 + 短信验证码登录；新账号注册需要邀请码（内测阶段）。
        登录完成后会自动回到你刚才的页面。
      </p>
      <AccountSection variant="full" onAuthed={() => router.replace(next)} />
    </div>
  );
}

export function LoginView() {
  return (
    <Suspense fallback={null}>
      <LoginViewInner />
    </Suspense>
  );
}
