import type { Metadata } from "next";
import { Suspense } from "react";
import { AppShell } from "@/components/shell/app-shell";
import { LoginView } from "@/components/login-view";

export const metadata: Metadata = {
  title: "登录 / 注册 · 法小智",
  description: "手机号 + 短信验证码登录或注册法小智账号。",
};

/** 独立登录页（2026-10-10 按用户拍板：登录/注册是独立页面，不寄宿在「我的」里）。
 *  登录成功后回到来源页（?next=，只接受站内路径）。 */
export default function LoginPage() {
  return (
    <AppShell>
      <Suspense fallback={null}>
        <LoginView />
      </Suspense>
    </AppShell>
  );
}
