import type { Metadata } from "next";
import { AppShell } from "@/components/shell/app-shell";
import { ResearchStudio } from "@/components/research/research-studio";

export const metadata: Metadata = {
  title: "类案检索 · 法小智",
  description: "说清你的情况，找到相似的判例，看看法院都是怎么判的，并给出可以拿去用的结论与研究报告。",
};

/**
 * 类案检索（迭代 1-2：新界面）
 *
 * 2026-09-20 起本页使用新界面 `ResearchStudio`（四屏：说你要查什么 → 正在查 → 挑案例 → 研究报告）。
 * 上一代页面组件 `components/research-live.tsx` 保留未删，如需回退把上面的引用换回去即可。
 * 接口与业务规则未改：仍走后端 8010 的 /message、/checkpoint(sample_confirm)、/supplement、/export。
 */
export default function ResearchPage() {
  return (
    <AppShell>
      <ResearchStudio />
    </AppShell>
  );
}
