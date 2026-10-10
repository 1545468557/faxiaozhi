import type { Metadata } from "next";
import { AppShell } from "@/components/shell/app-shell";
import { SettingsPanel } from "@/components/settings-panel";

export const metadata: Metadata = {
  title: "设置 · 法小智",
  description: "连接状态与本机数据。",
};

export default function SettingsPage() {
  return (
    <AppShell>
      <SettingsPanel />
    </AppShell>
  );
}
