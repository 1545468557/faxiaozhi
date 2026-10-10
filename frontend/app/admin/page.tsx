import type { Metadata } from "next";
import { AppShell } from "@/components/shell/app-shell";
import { AdminDashboard } from "@/components/admin/admin-dashboard";

export const metadata: Metadata = {
  title: "管理后台 · 法小智",
  description: "运营总览：注册与活跃、功能使用量、质量基线。只有聚合数字，不含用户内容。",
};

/** 管理后台（2026-10-10）：只有 owner / admin 打得开，权限判定在后端。 */
export default function AdminPage() {
  return (
    <AppShell>
      <AdminDashboard />
    </AppShell>
  );
}
