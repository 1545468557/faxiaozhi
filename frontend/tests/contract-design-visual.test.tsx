/**
 * 合同审查新界面 · 结果区与红线（迭代 1-1）
 *
 * 用 fixture 数据渲染真实的 DOM，锁住五件事：
 * 1. 依据状态必须分开显示（已核验 / 未找到直接依据 / 依据未通过核验只能作提示）；
 * 2. 导出没就绪时按钮禁用，并把原因**原文**列出来；
 * 3. 原文 ↔ 风险双向联动；
 * 4. 定位失败的风险**绝不高亮**（宁可不标，也不给错位置）；
 * 5. "未提示风险的条款不等于没有风险" 常驻。
 */

import { beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { ContractResult } from "@/components/contract/contract-result";
import { exportState } from "@/lib/api/common";
import { riskSummary, sortRisks } from "@/lib/api/contract";
import type { ContractRisk, ContractSnapshot, SourceBrief } from "@/lib/api/types";

beforeAll(() => {
  // jsdom 不实现 scrollIntoView
  Element.prototype.scrollIntoView = vi.fn();
});

const source = (extra: Partial<SourceBrief> = {}): SourceBrief =>
  ({
    source_id: "s1",
    kind: "statute",
    identifier: "《民法典》第五百七十七条",
    title: "",
    court: "",
    level: "",
    region: "",
    decided_on: "",
    status: "ok",
    origin: "mcp",
    origin_text: "法宝",
    supplement: false,
    user_verified: false,
    identifier_missing: false,
    corroboration: "dual",
    status_text: "已取得完整原文",
    effective_status: "现行有效",
    uri: null,
    local_hit: false,
    corroboration_text: "双源一致（法宝 + 本地依据库）",
    superseded: false,
    manual_override: false,
    locator: {},
    quote: "当事人一方不履行合同义务…",
    synthetic: false,
    ...extra,
  }) as SourceBrief;

const risk = (id: string, over: Partial<ContractRisk> = {}): ContractRisk => ({
  riskId: id,
  kind: "commercial",
  level: "high",
  clauseId: "c1",
  clauseHeading: "第二条 押金",
  anchorText: "押金全部不予退还",
  start: 0,
  end: 8,
  locateOk: true,
  issue: "押金全额不退，缺少扣除依据与返还期限。",
  basis: [{ source_id: "s1", identifier: "《民法典》第五百七十七条", quote: "当事人一方不履行合同义务…" }],
  basisStatus: "verified",
  suggestion: "建议改为按实际损失结算。",
  confidence: "medium",
  ...over,
});

const TEXT = "房屋租赁合同\n甲方（出租方）：示例甲方　乙方（承租方）：示例乙方\n第一条 房屋与租期\n租赁期限为一年，双方按约定办理交接。\n第二条 押金\n押金全部不予退还，出租人无须说明理由。\n第三条 维修\n房屋维修费用由承租人承担。";

function renderResult(
  risks: ContractRisk[],
  options: {
    exportReady?: boolean;
    blockers?: string[];
    failedStep?: string | null;
    /** 风险卡是受控的：要看到某条的正文，需把它设为"当前选中" */
    activeRiskId?: string | null;
  } = {},
) {
  const sorted = sortRisks(risks);
  const contract: ContractSnapshot = {
    filename: "房屋租赁合同.docx",
    text: TEXT,
    stance: "support",
    stanceLabel: "甲方（出租方一侧）",
    clauses_count: 2,
    risks: sorted,
    counts: {
      total: sorted.length,
      by_level: { high: 1, medium: 0, low: 0 },
      verified: sorted.filter(item => item.basisStatus === "verified").length,
      no_basis: sorted.filter(item => item.basisStatus === "no_basis").length,
      locate_failed: sorted.filter(item => !item.locateOk).length,
    },
    status: "reviewed",
  };
  const onSelectRisk = vi.fn();
  const onExport = vi.fn();
  render(
    <ContractResult
      contract={contract}
      risks={sorted}
      activeRiskId={options.activeRiskId ?? null}
      onSelectRisk={onSelectRisk}
      summary={riskSummary(contract)}
      sources={[source()]}
      stanceLabel="甲方（出租方一侧）"
      exportInfo={exportState({
        ready: options.exportReady === true,
        blockers: options.blockers ?? [],
      })}
      onExport={onExport}
      exporting={false}
      onRefresh={vi.fn()}
      refreshing={false}
      refreshedAt={null}
      onRetry={vi.fn()}
      canRetry={false}
      retrying={false}
      truncated={[]}
      materials={[]}
      gateReport={null}
      gaps={[]}
      conflicts={[]}
      onVerify={vi.fn()}
      uploadFiles={vi.fn()}
      uploading={false}
      uploadRejected={[]}
      maxFiles={20}
      failedStep={options.failedStep ?? null}
    />,
  );
  return { onSelectRisk, onExport };
}


import { mkdirSync, writeFileSync, readFileSync } from "node:fs";
import { ContractProgressVisual } from "@/components/contract/contract-progress";
const save = (name:string) => {
 if(!process.env.CONTRACT_VISUAL_OUTPUT)return;
 const root=process.env.CONTRACT_VISUAL_OUTPUT;
 mkdirSync(root,{recursive:true});
 const css=readFileSync("app/contract.css","utf8");
 writeFileSync(`${root}/${name}.html`, `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>*{box-sizing:border-box}body{margin:0;font:14px/1.65 -apple-system,"PingFang SC",sans-serif;color:#253936;background:#f5f7f6;--v2-line:#dce5e1;--v2-ink:#173d37;--v2-teal:#17675c;--v2-teal-deep:#17675c;--v2-surface:#f5f7f6}button,textarea{font:inherit}button{cursor:pointer}p{margin:0} ${css}</style><body><div style="padding:12px;text-align:center;background:#edf2ef">离线视觉验收 · 使用正式 React 组件与正式 CSS，数据为测试夹具</div><main class="ct-wrap cx-studio">${document.body.innerHTML}</main></body></html>`);
};
it("default selection uses separate opinion panel and paper paragraphs",()=>{
 renderResult([risk("r1",{start:TEXT.indexOf("押金全部"),end:TEXT.indexOf("押金全部")+8}),risk("r2",{issue:"维修责任范围未明确",level:"medium",start:TEXT.indexOf("房屋维修"),end:TEXT.indexOf("房屋维修")+13,anchorText:"房屋维修费用由承租人承担。",basisStatus:"no_basis",basis:[]})]);
 expect(document.querySelectorAll(".cx-selected-risk")).toHaveLength(1);
 expect(document.querySelector(".ct-risks .ct-risk-body")).toBeNull();
 expect(document.querySelectorAll(".cx-paper-paragraph").length).toBeGreaterThan(2);
 save("result");
});
it("waiting visual shows actual phase count without inventing a percentage",()=>{
 render(<section className="ct-card cx-waiting"><ContractProgressVisual phaseLabel="查找相关依据" stepText="第 3 步 / 共 5 步" progress={60}/></section>);
 expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("60");
 expect(document.querySelector(".cx-scan")).toBeTruthy();
 save("waiting");
});
