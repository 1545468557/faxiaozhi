import type { Metadata } from "next";
import { AppShell } from "@/components/shell/app-shell";
import { SettingsPanel } from "@/components/auth/settings-panel";

export const metadata: Metadata = {
  title: "设置 · 法小智",
  description: "账号、连接状态、本机数据与版本说明。",
};

export default function SettingsPage() {
  return (
    <AppShell>
      <SettingsPanel />
    </AppShell>
  );
}
