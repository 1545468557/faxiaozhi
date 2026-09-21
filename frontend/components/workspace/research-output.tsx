"use client";

/**
 * 研究产物：对比矩阵、观点分布、带引用的结论（阶段 3-2）
 *
 * 红线 1：每条引用都能点开看**来源与核验状态**。
 * 红线 4：被拦下的引用**不进结论**（由 lib/api/research.ts 过滤），改列入「依据缺口」。
 */

import type { Conclusion, Distribution, MatrixRow, SourceBrief, Synthesis } from "@/lib/api/types";
import { distributionText, matrixView, resolveCitations, splitConclusions } from "@/lib/api/research";

/** 对比矩阵：列由后端给（含代码写入的「来源」列），窄屏横向滚动、不被内容撑破 */
export function MatrixTable({ rows }: { rows: MatrixRow[] }) {
  const view = matrixView(rows);
  if (!rows.length) {
    return (
      <div className="fzx-empty">
        <p>还没有对比矩阵。</p>
        <p style={{ fontSize: 13 }}>矩阵由后端在样本确认之后用代码计算，只包含已确认的样本。</p>
      </div>
    );
  }
  return (
    <div className="fzx-table-wrap">
      <table className="fzx-table">
        <thead>
          <tr>
            {view.columns.map(column => (
              <th key={column} scope="col">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={String(row.case_id ?? index)}>
              {view.columns.map(column => (
                <td key={column} data-column={column}>
                  {column === "来源" ? <span className="fzx-badge">{String(row[column] ?? "")}</span> : String(row[column] ?? "")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function DistributionNote({ distribution }: { distribution: Distribution | Record<string, never> | null }) {
  const text = distributionText(distribution);
  if (!text) return null;
  return (
    <p className="fzx-note">
      <strong>观点分布：</strong>
      {text}
    </p>
  );
}

export function Conclusions({
  synthesis,
  degradedTexts,
  sources,
  onOpenSource,
}: {
  synthesis: Synthesis | null;
  degradedTexts: string[];
  sources: SourceBrief[];
  onOpenSource: (sourceId: string) => void;
}) {
  const gate = degradedTexts.length ? { degraded_texts: degradedTexts } : null;
  const { shown } = splitConclusions(synthesis, gate as never);

  if (!shown.length) {
    return (
      <div className="fzx-empty">
        <p>还没有综合结论。</p>
        <p style={{ fontSize: 13 }}>
          确认样本后，后端会生成逐条带引用的结论；引用未通过核验的结论不会显示在这里（会进入「依据缺口」）。
        </p>
      </div>
    );
  }

  return (
    <ol className="fzx-conclusions">
      {shown.map((item: Conclusion, index) => (
        <li key={`${item.text}-${index}`} className="fzx-conclusion">
          <p className="fzx-conclusion-text">{item.text}</p>
          <div className="fzx-cites">
            {resolveCitations(item, sources).map(source => (
              <button key={source.source_id} type="button" className="fzx-cite" onClick={() => onOpenSource(source.source_id)}>
                <span data-kind={source.kind === "user_material" ? "user" : undefined}>{source.origin_text}</span>
                <span className="fzx-cite-id">{source.identifier || source.title || source.source_id}</span>
                <span className="fzx-cite-status">{source.status_text}</span>
              </button>
            ))}
          </div>
          {item.differences?.length ? <p className="fzx-mini">差异：{item.differences.join("；")}</p> : null}
          {item.similarities?.length ? <p className="fzx-mini">共性：{item.similarities.join("；")}</p> : null}
        </li>
      ))}
    </ol>
  );
}
