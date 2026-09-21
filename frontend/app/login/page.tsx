import type { Metadata } from "next";
import { AuthPanel } from "@/components/auth/auth-panel";

export const metadata: Metadata = {
  title: "登录 · 法小智",
  description: "用账号登录，之后问过的、研究过的、审过的都会记在你自己名下。",
};

/** 登录 / 注册（迭代 2-4）。故意不使用 AppShell：未登录时不该看到功能导航。 */
export default function LoginPage() {
  return (
    <main style={{ minHeight: "100dvh", background: "#f5f8f6" }}>
      <AuthPanel />
    </main>
  );
}
