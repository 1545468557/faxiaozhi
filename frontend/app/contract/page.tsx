import type { Metadata } from "next";
import { AppShell } from "@/components/shell/app-shell";
import { ContractStudio } from "@/components/contract/contract-studio";

export const metadata: Metadata = {
  title: "合同审查 · 法小智",
  description: "上传一份合同，逐条挑出对你不利的地方：风险分级、原文定位、依据与建议改法，可导出审查报告。",
};

/**
 * 合同审查（迭代 1-1：新界面）
 *
 * 2026-09-20 起本页使用新界面 `ContractStudio`（四步走：上传 → 立场 → 审查中 → 结果）。
 * 上一代页面组件 `components/contract-live.tsx` 保留未删，如需回退把上面的引用换回去即可。
 * 接口与业务规则未改：仍走 8010 的 /contract/review 与 /contract/export（经 BFF 代理）。
 */
export default function ContractPage() {
  return (
    <AppShell>
      <ContractStudio />
    </AppShell>
  );
}
