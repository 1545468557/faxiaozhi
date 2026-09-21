import type { Metadata } from "next";
import { AppShell } from "@/components/shell/app-shell";
import { MineDashboard } from "@/components/mine/mine-dashboard";

export const metadata: Metadata = {
  title: "我的 · 法小智",
  description: "本机工作台：会话、上传的材料、导出的文件、使用情况。",
};

/** 「我的」（迭代 3 追加）：现在没有账号体系，所以呈现本机范围内的真实数据。 */
export default function MePage() {
  return (
    <AppShell>
      <MineDashboard />
    </AppShell>
  );
}
