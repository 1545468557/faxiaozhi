"use client";

/** 来源详情侧栏（阶段 3-2）：把「这一条到底来自哪、核验到什么程度」讲清楚 */

import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import type { SourceBrief } from "@/lib/api/types";
import { sourceKind } from "@/lib/api/research";

export function SourceSheet({ source, onClose }: { source: SourceBrief | null; onClose: () => void }) {
  return (
    <Sheet open={Boolean(source)} onOpenChange={open => !open && onClose()}>
      <SheetContent className="source-sheet">
        <SheetHeader>
          <SheetTitle>{source?.title || source?.identifier || "来源"}</SheetTitle>
          <SheetDescription>
            {source?.origin_text} · {source?.status_text}
          </SheetDescription>
        </SheetHeader>
        {source && (
          <div className="sheet-body">
            <p>标识：{source.identifier || "未识别到标识"}</p>
            <p>印证：{source.corroboration_text}</p>
            <p>效力状态：{source.effective_status || "—"}</p>
            {source.court ? <p>法院：{source.court}</p> : null}
            {source.decided_on ? <p>裁判日期：{source.decided_on}</p> : null}
            {source.manual_override ? <p>已由人工裁决放行（以用户材料为准）。</p> : null}
            {source.superseded ? <p>已被同标识的新版本取代，不参与对比与引用。</p> : null}
            {sourceKind(source) === "user" && !source.user_verified ? (
              <div className="boundary">这条材料尚未点「本人已核验」，因此不会被引用。</div>
            ) : null}
            {source.uri && (
              <a className="text-button" href={source.uri} target="_blank" rel="noopener noreferrer">
                打开法宝来源链接
              </a>
            )}
            {source.quote ? <blockquote className="real-quote">{source.quote}</blockquote> : null}
            {source.note && <div className="boundary">{source.note}</div>}
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
