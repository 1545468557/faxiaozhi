"use client";

/**
 * 合同审查（阶段 3-4）
 *
 * 流程：上传合同（角色=合同）→ **本人已核验** → 发起审查 →
 *      **确认审查立场（人工门，硬门）** → 解析定位 → 依据检索 → 风险分级 → 引用核验
 *      → **原文 ↔ 风险联动** → 导出《合同审查报告》
 *
 * 三条红线：
 * - 立场没确认不能开始（按钮禁用 + 说明原因）；
 * - 每条风险都显示依据或"未找到直接依据"，并标"须经律师审定"；
 * - 未通过核验的依据只能作「提示性风险」，不作有依据的风险输出。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Download, FileSearch, RefreshCw, Upload } from "lucide-react";
import { SiteHeader } from "@/components/site-header";
import { BackendStatus, useBackend } from "@/components/backend-status";
import { RunMonitor, SnapshotFailure } from "@/components/workspace/run-monitor";
import { ContractTextPanel, RiskList, StanceCard } from "@/components/workspace/contract-output";
import { EvidenceTabs } from "@/components/workspace/evidence-tabs";
import { SourceSheet } from "@/components/workspace/source-sheet";
import { useRun } from "@/components/workspace/use-run";
import { useSessionState } from "@/components/workspace-session";
import { api, ApiError, describeError, downloadUrl, loadState } from "@/lib/api";
import { canSubmitStance, riskSummary, sortRisks, splitRisks, stanceGate } from "@/lib/api/contract";
import { exportState } from "@/lib/api/common";
import type { SessionState, SourceBrief } from "@/lib/api/types";

export function ContractLive() {
  const [stanceDraft, setStanceDraft] = useSessionState<string>("contract:stance", "");
  const [snapshot, setSnapshot] = useState<SessionState | null>(null);
  const [notice, setNotice] = useState("");
  const [detail, setDetail] = useState<SourceBrief | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadRejected, setUploadRejected] = useState<{ filename: string; message?: string }[]>([]);
  const [exporting, setExporting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [activeRiskId, setActiveRiskId] = useState<string | null>(null);
  const sidRef = useRef<string | null>(null);
  const pollTimer = useRef<number | null>(null);
  const { bootstrap } = useBackend();

  const refresh = useCallback(async () => {
    const sid = sidRef.current;
    if (!sid) return;
    try {
      setSnapshot(await api.state(sid));
    } catch (cause) {
      setNotice(
        describeError(
          cause instanceof ApiError ? cause.code : undefined,
          cause instanceof Error ? cause.message : undefined,
        ).fallback,
      );
    }
  }, []);

  const stopPolling = useCallback(() => {
    if (pollTimer.current) {
      window.clearInterval(pollTimer.current);
      pollTimer.current = null;
    }
  }, []);

  const pollUntilSettled = useCallback(() => {
    if (pollTimer.current) return;
    const startedAt = Date.now();
    pollTimer.current = window.setInterval(() => {
      const sid = sidRef.current;
      if (!sid) return stopPolling();
      void api
        .state(sid)
        .then(next => {
          setSnapshot(next);
          const contract = next.contract ?? {};
          const settled =
            Boolean(contract.stance && contract.status && contract.status !== "awaiting_stance") ||
            Boolean(next.failed_step) ||
            next.export?.ready === true;
          if (settled) {
            setBusy(false);
            stopPolling();
          }
        })
        .catch(() => undefined);
      if (Date.now() - startedAt > 420000) stopPolling();
    }, 3000);
  }, [stopPolling]);

  useEffect(() => stopPolling, [stopPolling]);

  const run = useRun({
    onSettled: () => {
      setBusy(false);
      stopPolling();
      void refresh();
    },
  });
  const { phase } = run.state;

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const { sid, state } = await loadState("contract", {
          onRecreated: () => alive && setNotice("原会话已过期或服务已重启，已自动建立新会话。"),
        });
        if (!alive) return;
        sidRef.current = sid;
        setSnapshot(state);
      } catch (cause) {
        if (!alive) return;
        setNotice(
          describeError(
            cause instanceof ApiError ? cause.code : undefined,
            cause instanceof Error ? cause.message : undefined,
          ).fallback,
        );
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  /** 上传合同：**显式指定 role=contract**，避免被识别成案例（3-1 记录的契约差异已在后端修掉） */
  const uploadContract = useCallback(
    async (files: File[]) => {
      const sid = sidRef.current;
      if (!sid) return;
      setUploading(true);
      setNotice("");
      try {
        const form = new FormData();
        form.append("role", "contract");
        files.forEach(file => form.append("files", file));
        const result = await api.upload(sid, form);
        setUploadRejected(result.rejected ?? []);
        if (result.materials?.length) {
          setNotice(`已收录合同：${result.materials.length} 个文件。请先点「本人已核验」，再发起审查。`);
        }
        await refresh();
      } catch (cause) {
        setNotice(
          describeError(cause instanceof ApiError ? cause.code : undefined, cause instanceof Error ? cause.message : undefined)
            .fallback,
        );
      } finally {
        setUploading(false);
      }
    },
    [refresh],
  );

  const verifyMaterial = useCallback(
    async (sourceId: string) => {
      const sid = sidRef.current;
      if (!sid) return;
      try {
        await api.verifyMaterial(sid, sourceId);
        await refresh();
      } catch (cause) {
        setNotice(
          describeError(cause instanceof ApiError ? cause.code : undefined, cause instanceof Error ? cause.message : undefined)
            .fallback,
        );
      }
    },
    [refresh],
  );

  /** 发起审查：这一步会停在立场门 */
  const startReview = useCallback(async () => {
    const sid = sidRef.current;
    if (!sid || busy || phase === "running") return;
    setBusy(true);
    setNotice("");
    setActiveRiskId(null);
    try {
      run.start(sid, () => api.contractReview(sid));
    } catch (cause) {
      setBusy(false);
      const info = describeError(
        cause instanceof ApiError ? cause.code : undefined,
        cause instanceof Error ? cause.message : undefined,
      );
      run.fail(info.code, info.fallback);
    }
  }, [busy, phase, run]);

  /** 确认立场：不开启新运行（沿用同一条事件流 + 轮询兜底） */
  const submitStance = useCallback(async () => {
    const sid = sidRef.current;
    if (!sid) return;
    const gate = canSubmitStance(stanceDraft);
    if (!gate.ok) {
      setNotice(gate.reason);
      return;
    }
    setBusy(true);
    setNotice("");
    run.resume();
    pollUntilSettled();
    void api
      .checkpoint(sid, "stance_confirm", { stance: stanceDraft })
      .catch((cause: unknown) => {
        setBusy(false);
        stopPolling();
        setNotice(
          describeError(
            cause instanceof ApiError ? cause.code : undefined,
            cause instanceof Error ? cause.message : undefined,
          ).fallback,
        );
      });
  }, [pollUntilSettled, run, stanceDraft, stopPolling]);

  const retry = useCallback(async () => {
    const sid = sidRef.current;
    if (!sid) return;
    setBusy(true);
    run.start(sid, () => api.retry(sid));
  }, [run]);

  const doExport = useCallback(async () => {
    const sid = sidRef.current;
    if (!sid) return;
    setExporting(true);
    setNotice("");
    try {
      const result = await api.contractExport(sid);
      if (result.filename) {
        window.location.href = downloadUrl(result.filename);
        setNotice(`已生成：${result.filename}`);
      }
      await refresh();
    } catch (cause) {
      setNotice(
        describeError(cause instanceof ApiError ? cause.code : undefined, cause instanceof Error ? cause.message : undefined)
          .fallback,
      );
    } finally {
      setExporting(false);
    }
  }, [refresh]);

  const contract = snapshot?.contract;
  const gate = stanceGate(contract, run.state.checkpoint);
  const material = (snapshot?.materials ?? [])[0];
  const risks = sortRisks(contract?.risks ?? []);
  const { provisional } = splitRisks(risks);
  const summary = riskSummary(contract);
  const exp = exportState(snapshot?.export);
  const running = phase === "running";
  const finished = Boolean(contract?.stance && (contract?.status === "reviewed" || contract?.status === "insufficient"));
  const truncated = (snapshot?.limitations ?? []).filter(item => /截断|未审查|上限/.test(item));

  return (
    <div className="legal-app workspace-page">
      <SiteHeader active="contract" />
      <main className="workbench" id="main-content">
        <div className="workbench-heading">
          <div>
            <Link className="back" href="/">
              首页 /
            </Link>
            <h1>
              合同审查
              <span>上传合同 → 确认立场 → 原文定位 → 风险分级 → 审查报告</span>
            </h1>
          </div>
          {gate.confirmed && <span className="light-badge">立场：{gate.label}</span>}
        </div>

        <BackendStatus className="mb-4" />

        {notice && (
          <p className="fzx-note" data-tone="warn" style={{ marginTop: 12 }} role="status">
            {notice}
          </p>
        )}

        <div className="workspace-body">
          <aside className="input-panel">
            <div className="section-label">
              <span>01</span>上传合同并核验
            </div>
            <label className="fzx-upload">
              <input
                type="file"
                accept=".docx,.pdf,.txt,.md"
                disabled={uploading}
                onChange={event => {
                  const files = Array.from(event.target.files ?? []);
                  event.target.value = "";
                  if (files.length) void uploadContract(files);
                }}
              />
              <span>
                <Upload size={15} /> {uploading ? "正在上传与解析…" : "选择合同文件（docx / pdf / txt / md）"}
              </span>
            </label>
            {material ? (
              <div className="fzx-source-row">
                <div className="fzx-source-main">
                  <b>{String(material.filename ?? "")}</b>
                  <span className="fzx-badge">{String(material.role_label ?? "")}</span>
                  {material.verified ? (
                    <span className="fzx-badge ok">已核验</span>
                  ) : (
                    <span className="fzx-badge bad">待核验</span>
                  )}
                </div>
                <div className="fzx-source-actions">
                  {!material.verified && (
                    <button type="button" className="primary" onClick={() => void verifyMaterial(String(material.source_id))}>
                      本人已核验
                    </button>
                  )}
                </div>
                {!material.verified && <p className="fzx-mini">未核验的合同不能作为审查对象。</p>}
              </div>
            ) : (
              <p className="fzx-mini">还没有上传合同。合同原文只在当前会话内存中处理，不落盘。</p>
            )}
            <button
              className="primary full"
              type="button"
              onClick={() => void startReview()}
              disabled={busy || running || !material?.verified}
            >
              <FileSearch size={17} /> {running ? "审查中…" : "发起合同审查"}
            </button>
            {!material?.verified && <p className="fzx-mini">先上传合同并点「本人已核验」，才能发起审查。</p>}
            <p className="muted-note">一次审查一份合同（V1）；立场不同，风险结论不同。</p>
          </aside>

          <article className="result-panel">
            <RunMonitor
              run={run.state}
              canRetry={snapshot?.can_retry}
              onRetry={() => void retry()}
            />

            <SnapshotFailure
              failedStep={snapshot?.failed_step}
              failedReason={snapshot?.failed_reason}
              canRetry={snapshot?.can_retry}
              onRetry={() => void retry()}
              retrying={busy && phase !== "running"}
            />

            {gate.awaiting && run.state.awaiting === "stance_confirm" && (
              <StanceCard
                filename={gate.filename}
                parties={gate.parties}
                options={gate.options}
                value={stanceDraft}
                onChange={setStanceDraft}
                onSubmit={() => void submitStance()}
                busy={busy}
                error={notice}
              />
            )}

            {finished && (
              <>
                <div className="result-toolbar" style={{ marginTop: 16 }}>
                  <div className="section-label">
                    <span>02</span>风险与原文定位
                  </div>
                  <span className="light-badge">{summary.text}</span>
                </div>

                <div className="fzx-contract-layout">
                  <ContractTextPanel
                    text={contract?.text ?? ""}
                    risks={risks}
                    activeRiskId={activeRiskId}
                    onAnchorClick={riskId => setActiveRiskId(riskId)}
                  />
                  <div className="fzx-risk-panel">
                    {summary.verified + summary.noBasis ? (
                      <p className="fzx-mini">
                        有依据风险 {summary.verified} 条 · 提示性风险 {summary.noBasis + provisional.filter(item => item.basisStatus === "rejected").length} 条
                        {summary.locateFailed ? ` · ${summary.locateFailed} 条未能定位到原文` : ""}
                      </p>
                    ) : null}
                    <RiskList
                      risks={risks}
                      activeRiskId={activeRiskId}
                      onSelect={riskId => setActiveRiskId(riskId)}
                      sources={snapshot?.sources ?? []}
                      onOpenSource={sourceId =>
                        setDetail((snapshot?.sources ?? []).find(item => item.source_id === sourceId) ?? null)
                      }
                    />
                  </div>
                </div>

                {!risks.length && (
                  <p className="fzx-note" data-tone="warn">
                    未提示风险的条款不等于没有风险：本次分析受已提供材料与审查范围限制，请由经办人员结合完整合同复核。
                  </p>
                )}
                <p className="fzx-note">未提示风险的条款不等于没有风险；建议改法须经律师审定。</p>

                {truncated.length > 0 && (
                  <div className="fzx-note" data-tone="warn">
                    <strong>本次审查的范围限制</strong>
                    <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                      {truncated.map(item => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </>
            )}

            <EvidenceTabs
              sources={snapshot?.sources ?? []}
              materials={snapshot?.materials ?? []}
              gate={snapshot?.gate_report ?? null}
              gaps={snapshot?.gaps}
              synthesis={null}
              conflicts={snapshot?.conflicts ?? []}
              onOpenSource={sourceId =>
                setDetail((snapshot?.sources ?? []).find(item => item.source_id === sourceId) ?? null)
              }
              onVerify={sourceId => void verifyMaterial(sourceId)}
              onRoleChange={() => undefined}
              onResolveConflict={() => undefined}
              onUploadFiles={files => void uploadContract(files)}
              uploading={uploading}
              uploadRejected={uploadRejected}
              maxFiles={bootstrap?.materials.max_files_per_upload ?? 20}
            />

            <div className="fzx-monitor" style={{ marginTop: 14 }} aria-label="导出">
              <div className="fzx-monitor-head">
                <h3>合同审查报告（Word）</h3>
                <div className="fzx-actions" style={{ margin: 0 }}>
                  <button type="button" className="primary" onClick={() => void doExport()} disabled={exp.disabled || exporting}>
                    <Download size={16} /> {exporting ? "正在导出…" : "导出审查报告"}
                  </button>
                  <button type="button" onClick={() => void refresh()}>
                    <RefreshCw size={15} /> 刷新状态
                  </button>
                </div>
              </div>
              {exp.disabled ? (
                <div className="fzx-note" data-tone="warn">
                  <strong>{exp.text}</strong>
                  <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                    {exp.blockers.length ? exp.blockers.map(item => <li key={item}>{item}</li>) : <li>暂时没有可导出的内容。</li>}
                  </ul>
                </div>
              ) : (
                <p className="fzx-note">引用核验已完成，可导出审查报告供复核与整理。</p>
              )}
            </div>
          </article>
        </div>
      </main>

      <SourceSheet source={detail} onClose={() => setDetail(null)} />
    </div>
  );
}
