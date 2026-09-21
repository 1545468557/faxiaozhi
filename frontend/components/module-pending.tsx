"use client";

/**
 * 未接线模块的占位（阶段 3-1）
 *
 * 这里**故意什么都不展示**：法律咨询与合同审查的真实链路分别在本阶段的 3-3 / 3-4 接入。
 * 按产品红线（AGENTS.md 底线 10、交接契约红线 1/4），在真实链路可用之前，
 * 界面上**不放示例回答、不放固定风险、不放罐头文案**——因为那些内容会看起来像法律意见。
 */

import Link from "next/link";
import { ArrowLeft, Search } from "lucide-react";
import { SiteHeader, type SitePage } from "@/components/site-header";
import { BackendStatus } from "@/components/backend-status";

export function ModulePending({ page, title, summary }: { page: SitePage; title: string; summary: string }) {
  return (
    <div className="legal-app workspace-page">
      <SiteHeader active={page} />
      <main id="main-content" className="fzx-pending">
        <p className="fh-intro">{title}</p>
        <h1>{title}的真实分析链路尚未接入</h1>
        <p>{summary}</p>
        <ul>
          <li>后端能力已经做好并通过验证，当前缺的是把这个页面接到它上面（正在做）。</li>
          <li>在接好之前，这里不会展示任何示例结论、固定风险或罐头回答，也不会编造法条与案号。</li>
          <li>需要立刻可用的功能是「类案研究」——它已经跑在真实链路上。</li>
        </ul>
        <BackendStatus />
        <p>
          <Link className="fzx-back" href="/research">
            <Search size={15} style={{ display: "inline", verticalAlign: "-2px" }} /> 去用已接入的类案研究
          </Link>
        </p>
        <p>
          <Link className="fzx-back" href="/">
            <ArrowLeft size={15} style={{ display: "inline", verticalAlign: "-2px" }} /> 返回首页
          </Link>
        </p>
      </main>
    </div>
  );
}
