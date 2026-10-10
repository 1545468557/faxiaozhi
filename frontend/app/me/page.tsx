import type { Metadata } from "next";
import { AppShell } from "@/components/shell/app-shell";
import { MineDashboard } from "@/components/mine/mine-dashboard";

export const metadata: Metadata = {
  title: "我的 · 法小智",
  description: "本机工作台：会话、上传的材料、导出的文件、使用情况。",
};

/** 「我的」：账号状态 + 本机工作台（2026-10-10 登录/注册表单拆到独立页 /login，这里只留状态卡）。 */
export default function MePage() {
  return (
    <AppShell>
      <MineDashboard />
    </AppShell>
  );
}
