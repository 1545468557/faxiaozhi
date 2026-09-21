import type { Metadata } from "next";
import { ConsultLive } from "@/components/consult-live";

export const metadata: Metadata = { title: "法律问答 · 法小智", description: "用自己的话描述法律问题，无需准备专业术语。逐步补充关键信息，查看解答、引用依据、待核实事项与下一步建议。" };

export default function ConsultPage() {
  return <ConsultLive />;
}
