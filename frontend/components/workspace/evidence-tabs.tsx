"use client";

/**
 * 依据区 / 门禁报告 / 上传材料 三页签（阶段 3-2）
 *
 * 红线 1：依据必须标出来源与核验状态；用户材料**不经「本人已核验」不得被引用**。
 * 红线 4：门禁报告里被拦下的引用只出现在这里，不作为结论。
 * 红线 3：三种"没有结果"要分开显示（没匹配到 / 结果不足 / 接口失败）。
 */

import { useState } from "react";
import type { ConflictItem, GateReport, Gap, MaterialItem, SourceBrief, Synthesis } from "@/lib/api/types";
import { gapList, gateSummary, sourceKind, splitConclusions } from "@/lib/api/research";

export function SourceRow({ source, onOpen, onVerify, onRoleChange }: {
  source: SourceBrief;
  onOpen: () => void;
  onVerify?: () => void;
  onRoleChange?: (role: "case" | "statute" | "contract") => void;
}) {
  const kind = sourceKind(source);
  return (
    <div className="fzx-source-row">
      <div className="fzx-source-main">
        <b>{source.identifier || source.title || source.source_id}</b>
        <span className="fzx-badge" data-kind={kind === "user" ? "user" : undefined}>
          {source.origin_text}
        </span>
        <span className={`fzx-badge ${source.status === "ok" ? "ok" : "bad"}`}>{source.status_text}</span>
      </div>
      <div className="fzx-source-meta">
        {source.corroboration_text && <span>印证：{source.corroboration_text}</span>}
        {source.effective_status && <span>效力：{source.effective_status}</span>}
        {source.court && <span>{source.court}</span>}
        {source.decided_on && <span>{source.decided_on}</span>}
        {source.local_hit && <span>本地依据库命中</span>}
        {source.supplement && <span>补充来源</span>}
        {source.manual_override && <span>已人工裁决放行</span>}
        {source.superseded && <span className="bad">已被新版取代</span>}
      </div>
      <p className="fzx-source-quote">{source.quote ? `${source.quote.slice(0, 160)}…` : source.note ?? ""}</p>
      <div className="fzx-source-actions">
        <button type="button" onClick={onOpen}>
          查看来源与核验状态
        </button>
        {source.uri && (
          <a href={source.uri} target="_blank" rel="noopener noreferrer">
            打开来源链接
          </a>
        )}
        {kind === "user" && !source.user_verified && onVerify && (
          <button type="button" className="primary" onClick={onVerify}>
            本人已核验
          </button>
        )}
        {kind === "user" && onRoleChange && (
          <label className="fzx-role">
            角色
            <select
              value={source.kind}
              onChange={event => onRoleChange(event.target.value as "case" | "statute" | "contract")}
            >
              <option value="case">案例材料</option>
              <option value="statute">法条材料</option>
              <option value="contract">合同</option>
            </select>
          </label>
        )}
      </div>
    </div>
  );
}

export function EvidenceTabs({
  sources,
  materials,
  gate,
  gaps,
  synthesis,
  conflicts,
  onOpenSource,
  onVerify,
  onRoleChange,
  onResolveConflict,
  onExcludeConflict,
  onUploadFiles,
  uploading,
  uploadRejected,
  maxFiles,
}: {
  sources: SourceBrief[];
  materials: MaterialItem[];
  gate: GateReport | null;
  gaps: Gap[] | undefined;
  synthesis: Synthesis | null;
  conflicts: ConflictItem[];
  onOpenSource: (sourceId: string) => void;
  onVerify: (sourceId: string) => void;
  onRoleChange: (sourceId: string, role: "case" | "statute" | "contract") => void;
  onResolveConflict: (sourceId: string) => void;
  onExcludeConflict?: (sourceId: string) => void;
  onUploadFiles: (files: File[]) => void;
  uploading: boolean;
  uploadRejected: { filename: string; message?: string }[];
  maxFiles: number;
}) {
  const [tab, setTab] = useState<"sources" | "gate" | "materials">("sources");
  const summary = gateSummary(gate);
  const { degraded } = splitConclusions(synthesis, gate);
  const gapRows = gapList(gaps, degraded);

  return (
    <section className="fzx-tabs" aria-label="依据与核验">
      <div className="fzx-tab-nav" role="tablist">
        <button type="button" role="tab" aria-selected={tab === "sources"} data-active={tab === "sources"} onClick={() => setTab("sources")}>
          依据区（{sources.length}）
        </button>
        <button type="button" role="tab" aria-selected={tab === "gate"} data-active={tab === "gate"} onClick={() => setTab("gate")}>
          核验报告{summary.ready ? `（未通过 ${summary.rejected}）` : ""}
        </button>
        <button type="button" role="tab" aria-selected={tab === "materials"} data-active={tab === "materials"} onClick={() => setTab("materials")}>
          上传材料（{materials.length}）
        </button>
      </div>

      {tab === "sources" && (
        <div className="fzx-tab-body">
          {sources.length ? (
            sources.map(source => (
              <SourceRow
                key={source.source_id}
                source={source}
                onOpen={() => onOpenSource(source.source_id)}
                onVerify={() => onVerify(source.source_id)}
                onRoleChange={role => onRoleChange(source.source_id, role)}
              />
            ))
          ) : (
            <p className="fzx-empty">依据区为空：本次还没有取得可核验的来源。</p>
          )}
          {conflicts.length > 0 && (
            <div className="fzx-conflicts" id="research-source-conflicts" tabIndex={-1}>
              <strong>来源冲突（需人工裁决）</strong>
              {conflicts.map(item => (
                <div key={item.source_id} className="fzx-conflict">
                  <span>{item.identifier || item.source_id}</span>
                  <span className="fzx-badge bad">来源内容待核对</span>
                  {item.resolved ? (
                    <span className="fzx-badge ok">{item.decision === "exclude_source" ? "已不采用，相关结论已移除" : "已裁决放行"}</span>
                  ) : sources.some(source => source.source_id === item.source_id && sourceKind(source) === "user") ? (
                    <button type="button" className="primary" onClick={() => onResolveConflict(item.source_id)}>
                      以我上传的材料为准
                    </button>
                  ) : <span className="fzx-mini">需要核对来源差异，不能按用户上传材料直接放行。</span>}
                  <button type="button" onClick={() => onOpenSource(item.source_id)}>查看冲突材料</button>
                  {!item.resolved && onExcludeConflict && sources.some(s => s.source_id === item.source_id && s.kind === "statute") && <button type="button" onClick={() => onExcludeConflict(item.source_id)}>不采用此依据及相关结论</button>}
                </div>
              ))}
              <p className="fzx-mini">未裁决前该来源不能参与引用，导出也会被拦住。</p>
            </div>
          )}
        </div>
      )}

      {tab === "gate" && (
        <div className="fzx-tab-body">
          <p className="fzx-note">{summary.text}</p>
          {!summary.ready && <p className="fzx-mini">引用核验在生成结论之后进行；现在还没有报告。</p>}
          {summary.ready && summary.details.length > 0 && (
            <>
              <strong>被拦下的引用（不作为结论展示）</strong>
              {summary.details.map((item, index) => (
                <div key={`${item.rule}-${index}`} className="fzx-reject">
                  <span className="fzx-badge bad">{item.rule}</span>
                  {item.reason}
                  {item.identifier ? `（${item.identifier}）` : ""}
                  {item.quote ? <span className="fzx-mini">原文摘录：{item.quote}</span> : null}
                </div>
              ))}
            </>
          )}
          <strong>依据缺口</strong>
          {gapRows.length ? (
            gapRows.map((item, index) => (
              <div key={`${item.kind}-${index}`} className="fzx-reject" data-kind={item.kind}>
                <span className="fzx-badge">{item.kind}</span>
                {item.detail}
              </div>
            ))
          ) : (
            <p className="fzx-mini">暂无缺口记录。</p>
          )}
        </div>
      )}

      {tab === "materials" && (
        <div className="fzx-tab-body">
          <label className="fzx-upload">
            <input
              type="file"
              multiple
              accept=".docx,.pdf,.txt,.md"
              disabled={uploading}
              onChange={event => {
                const files = Array.from(event.target.files ?? []);
                event.target.value = "";
                if (files.length) onUploadFiles(files);
              }}
            />
            <span>{uploading ? "正在上传与解析…" : `选择材料文件（最多 ${maxFiles} 个，支持 docx / pdf / txt / md）`}</span>
          </label>
          <p className="fzx-mini">
            材料原文仅用于当前会话；经「本人已核验」确认后才可被引用。单个文件上传失败不影响其他文件。
          </p>
          {uploadRejected.length > 0 && (
            <div className="fzx-note" data-tone="warn">
              <strong>以下文件未收录：</strong>
              <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                {uploadRejected.map(item => (
                  <li key={item.filename}>
                    {item.filename}：{item.message ?? "格式或大小不符合要求"}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {materials.length ? (
            materials.map(item => (
              <div key={item.material_id} className="fzx-source-row">
                <div className="fzx-source-main">
                  <b>{item.filename}</b>
                  <span className="fzx-badge">{item.role_label}</span>
                  {item.verified ? <span className="fzx-badge ok">已核验</span> : item.superseded ? <span className="fzx-badge">已被取代</span> : <span className="fzx-badge bad">待核验</span>}
                  {item.identifier_missing && <span className="fzx-badge bad">未识别到案号</span>}
                </div>
                <div className="fzx-source-meta">
                  <span>标识：{item.identifier || "—"}（{item.identifier_source === "text" ? "正文抽取" : "文件名"}）</span>
                  <span>{item.chars} 字</span>
                  {item.version_label && <span>{item.version_label}</span>}
                  <span>角色来源：{item.role_source === "manual" ? "手动指定" : item.role_source === "detected" ? "自动识别" : "默认"}</span>
                </div>
                {item.superseded && <p className="fzx-mini">已被新版取代，不参与对比与引用。</p>}
                {item.fail_reason && <p className="fzx-mini">失败原因：{item.fail_reason}</p>}
              </div>
            ))
          ) : (
            <p className="fzx-mini">尚未上传材料。上传后可以自动识别案例 / 法条 / 合同，也可以手动改角色。</p>
          )}
        </div>
      )}
    </section>
  );
}
