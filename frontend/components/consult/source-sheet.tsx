"use client";

/**
 * 来源详情（v3）
 *
 * 与旧的 `components/workspace/source-sheet.tsx` 分开写，是因为**事实不同**：
 * 旧版来源带「核验状态 / 效力状态 / 印证程度」，v3 不做核验，这些字段根本不存在。
 * 复用旧组件就必须编造这几个值 —— 那比多写 30 行糟得多。
 */

import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import type { V3Source } from "@/lib/api/v3";

export function SourceSheet({ source, onClose }: { source: V3Source | null; onClose: () => void }) {
  return (
    <Sheet open={Boolean(source)} onOpenChange={open => !open && onClose()}>
      <SheetContent className="source-sheet">
        <SheetHeader>
          <SheetTitle>{source?.title || source?.identifier || "来源"}</SheetTitle>
          <SheetDescription>{source?.kind === "case" ? "判例" : "法规"} · 来自法规库检索</SheetDescription>
        </SheetHeader>
        {source && (
          <div className="sheet-body">
            <p>标识：{source.identifier || "未识别到标识"}</p>
            {source.court ? <p>法院：{source.court}</p> : null}
            {source.decided_on ? <p>裁判日期：{source.decided_on}</p> : null}
            {source.uri ? (
              <a className="text-button" href={source.uri} target="_blank" rel="noopener noreferrer">
                打开法宝来源链接
              </a>
            ) : null}
            {source.quote ? <blockquote className="real-quote">{source.quote}</blockquote> : null}
            <div className="boundary">
              这条材料是检索回来的原始结果，<b>没有逐条核验</b>。判断前请点上面的链接核对原文；与你的材料冲突时，以原文为准。
            </div>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
