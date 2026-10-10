import type { Metadata } from "next";
import { AppShell } from "@/components/shell/app-shell";
import { StatuteSearch } from "@/components/statutes/statute-search";
import "./statutes.css";

export const metadata: Metadata = {
  title: "法规查找 · 法小智",
  description: "查法律法规原文，支持精准查条文与自然语言智能检索。",
};

/** 法规查找（迭代 3）：本地法规库，默认只出「现行有效」条文。 */
export default function StatutesPage() {
  return (
    <AppShell>
      <StatuteSearch />
    </AppShell>
  );
}
