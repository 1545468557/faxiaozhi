"use client";

/**
 * 类案检索与研究（阶段 3-2：完整闭环）
 *
 * 流程：议题（+条件）→ 检索 → **确认样本（人工门）** → 对比矩阵 → 带引用的结论
 *       → 依据区 / 门禁报告 / 上传材料 → 来源冲突人工裁决 →（补充检索）→ 导出 Word
 *
 * 三条底线在这一层体现：
 * - 门没确认就不继续（按钮禁用 + 说明原因）；
 * - 界面上所有依据都带来源与核验状态；
 * - 导出未就绪时禁用并**原文列出原因**。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Download, FileText, RefreshCw, Search, Upload } from "lucide-react";
import { SiteHeader } from "@/components/site-header";
import { BackendStatus, useBackend } from "@/components/backend-status";
import { RunMonitor, SnapshotFailure } from "@/components/workspace/run-monitor";
import { CandidatePool } from "@/components/workspace/candidate-pool";
import { Conclusions, DistributionNote, MatrixTable } from "@/components/workspace/research-output";
import { EvidenceTabs } from "@/components/workspace/evidence-tabs";
import { SourceSheet } from "@/components/workspace/source-sheet";
import { useRun } from "@/components/workspace/use-run";
import { useSessionState } from "@/components/workspace-session";
import { api, ApiError, describeError, downloadUrl, loadState } from "@/lib/api";
import { exportState, sampleGateState } from "@/lib/api/research";
import type { SessionState, SourceBrief } from "@/lib/api/types";

const PURPOSES = ["课程学习", "教学备课", "学术研究", "实务参考"] as const;

export function ResearchLive() {
  const [purpose, setPurpose] = useSessionState<string>("research:purpose", "课程学习");
  const [topic, setTopic] = useSessionState<string>("research:topic", "");
  const [conditions, setConditions] = useSessionState<Record<string, string>>("research:conditions", {});
  const [selection, setSelection] = useSessionState<string[]>("research:selection", []);
  const [snapshot, setSnapshot] = useState<SessionState | null>(null);
  const [notice, setNotice] = useState("");
  const [detail, setDetail] = useState<SourceBrief | null>(null);
  const [exporting, setExporting] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadRejected, setUploadRejected] = useState<{ filename: string; message?: string }[]>([]);
  const [busy, setBusy] = useState(false);
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

  /**
   * 兜底轮询（手册 §10.3）：SSE 是主通道，但换页返回、网络抖动、或服务端旧流未及时释放时
   * 事件可能到不了。这里起一个**有界**轮询（2.5 秒一次、最多 4 分钟），一旦状态落定就停。
   */
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
          const settled =
            next.status === "awaiting_checkpoint" ||
            Boolean(next.failed_step) ||
            next.sample.locked ||
            (next.matrix?.length ?? 0) > 0 ||
            next.export?.ready === true;
          if (settled) {
            setBusy(false);
            setConfirming(false);
            stopPolling();
          }
        })
        .catch(() => undefined);
      if (Date.now() - startedAt > 240000) stopPolling();
    }, 2500);
  }, [stopPolling]);

  useEffect(() => stopPolling, [stopPolling]);

  const run = useRun({
    onSettled: () => {
      setBusy(false);
      setConfirming(false);
      stopPolling();
      void refresh();
    },
  });
  const { phase } = run.state;

  // 首次进入：把会话拉回来（刷新后仍能恢复；会话丢了会静默重建）
  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const { sid, state } = await loadState("research", {
          onRecreated: () => alive && setNotice("原会话已过期或服务已重启，已自动建立新会话。"),
        });
        if (!alive) return;
        sidRef.current = sid;
        setSnapshot(state);
        // 换页返回 / 断线重连：会话还在跑且没有结果 → 用轮询兜底，不重开事件流
        if (state.run_id && state.status === "active" && !state.sample?.locked && !state.failed_step && !(state.matrix?.length > 0)) {
          pollUntilSettled();
        }
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
  }, [pollUntilSettled]);

  const start = useCallback(async () => {
    if (busy || phase === "running") return;
    if (!topic.trim()) {
      setNotice("请先输入要研究的议题。");
      return;
    }
    setBusy(true);
    setNotice("");
    setDetail(null);
    setUploadRejected([]);
    setSelection([]);
    try {
      const { sid } = await loadState("research", {
        onRecreated: () => setNotice("原会话已过期或服务已重启，已自动建立新会话。"),
      });
      sidRef.current = sid;
      run.start(sid, () => api.message(sid, topic.trim(), { conditions, purpose }));
    } catch (cause) {
      setBusy(false);
      const info = describeError(
        cause instanceof ApiError ? cause.code : undefined,
        cause instanceof Error ? cause.message : undefined,
      );
      run.fail(info.code, info.fallback);
    }
  }, [busy, conditions, phase, purpose, run, setSelection, topic]);

  const retry = useCallback(async () => {
    const sid = sidRef.current;
    if (!sid) return;
    setBusy(true);
    run.start(sid, () => api.retry(sid));
  }, [run]);

  /** 人工门 1：确认样本（后端会在这里挂起，确认后继续提炼 → 矩阵 → 结论） */
  const confirmSample = useCallback(async () => {
    const sid = sidRef.current;
    if (!sid || !snapshot) return;
    const confirmed = selection.filter(id => snapshot.candidates.some(item => item.source_id === id));
    if (!confirmed.length) {
      setNotice("请先勾选至少 1 篇候选材料。");
      return;
    }
    const excluded = snapshot.candidates
      .map(item => item.source_id)
      .filter(id => !confirmed.includes(id));
    setConfirming(true);
    setNotice("");
    // 人工门不开启新运行：**沿用同一条事件流**，只把状态改回进行中（避免旧流抢事件）
    run.resume();
    pollUntilSettled();
    void api
      .checkpoint(sid, "sample_confirm", { confirmed, excluded })
      .catch((cause: unknown) => {
        setConfirming(false);
        stopPolling();
        const info = describeError(
          cause instanceof ApiError ? cause.code : undefined,
          cause instanceof Error ? cause.message : undefined,
        );
        setNotice(info.fallback);
      });
  }, [pollUntilSettled, run, selection, snapshot, stopPolling]);

  const supplement = useCallback(async () => {
    const sid = sidRef.current;
    if (!sid) return;
    setBusy(true);
    run.resume();
    pollUntilSettled();
    void api.supplement(sid).catch((cause: unknown) => {
      setBusy(false);
      const info = describeError(
        cause instanceof ApiError ? cause.code : undefined,
        cause instanceof Error ? cause.message : undefined,
      );
      setNotice(info.fallback);
    });
  }, [pollUntilSettled, run]);

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

  const changeRole = useCallback(
    async (sourceId: string, role: "case" | "statute" | "contract") => {
      const sid = sidRef.current;
      if (!sid) return;
      try {
        await api.setMaterialRole(sid, sourceId, role);
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

  const resolveConflict = useCallback(
    async (sourceId: string) => {
      const sid = sidRef.current;
      if (!sid) return;
      try {
        await api.resolveConflict(sid, sourceId, "use_user_material");
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

  const uploadFiles = useCallback(
    async (files: File[]) => {
      const sid = sidRef.current;
      if (!sid) return;
      setUploading(true);
      setNotice("");
      try {
        const form = new FormData();
        files.forEach(file => form.append("files", file));
        const result = await api.upload(sid, form);
        setUploadRejected(result.rejected ?? []);
        if (result.materials?.length) {
          setNotice(`已收录 ${result.materials.length} 个文件；请点「本人已核验」后才会被引用。`);
        }
        await refresh();
      } catch (cause) {
        const info = describeError(
          cause instanceof ApiError ? cause.code : undefined,
          cause instanceof Error ? cause.message : undefined,
        );
        setNotice(info.fallback);
      } finally {
        setUploading(false);
      }
    },
    [refresh],
  );

  const doExport = useCallback(async () => {
    const sid = sidRef.current;
    if (!sid) return;
    setExporting(true);
    setNotice("");
    try {
      const result = await api.exportResearch(sid);
      if (result.filename) {
        window.location.href = downloadUrl(result.filename);
        setNotice(`已生成：${result.filename}`);
      }
      await refresh();
    } catch (cause) {
      const info = describeError(
        cause instanceof ApiError ? cause.code : undefined,
        cause instanceof Error ? cause.message : undefined,
      );
      setNotice(info.fallback);
    } finally {
      setExporting(false);
    }
  }, [refresh]);

  const candidates = snapshot?.candidates ?? [];
  const locked = snapshot?.sample?.locked ?? false;
  const confirmedIds = snapshot?.sample?.confirmed ?? [];
  const awaiting = run.state.awaiting === "sample_confirm" || (Boolean(candidates.length) && !locked && phase !== "running");
  const gate = sampleGateState({ locked, confirmed: confirmedIds, candidates, awaitingCheckpoint: awaiting });
  const exp = exportState(snapshot?.export);
  const running = phase === "running";
  const supplementLeft = snapshot?.supplement?.remaining ?? 0;

  return (
    <div className="legal-app workspace-page">
      <SiteHeader active="research" />
      <main className="workbench" id="main-content">
        <div className="workbench-heading">
          <div>
            <Link className="back" href="/">
              首页 /
            </Link>
            <h1>
              类案检索与研究
              <span>议题 → 检索 → 确认样本 → 对比 → 结论</span>
            </h1>
          </div>
          <label className="purpose">
            研究用途
            <select value={purpose} onChange={event => setPurpose(event.target.value)} disabled={running}>
              {PURPOSES.map(item => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
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
              <span>01</span>定义研究范围
            </div>
            <label className="field-label" htmlFor="topic">
              事实 / 争议焦点
            </label>
            <textarea
              id="topic"
              value={topic}
              onChange={event => setTopic(event.target.value)}
              placeholder="描述事实和希望研究的法律问题…"
              disabled={running}
            />
            <div className="filter-row">
              <label>
                案由
                <input
                  value={conditions.cause ?? ""}
                  onChange={event => setConditions({ ...conditions, cause: event.target.value })}
                  placeholder="如：房屋租赁合同纠纷"
                  disabled={running}
                />
              </label>
              <label>
                地域
                <input
                  value={conditions.region ?? ""}
                  onChange={event => setConditions({ ...conditions, region: event.target.value })}
                  placeholder="如：江苏"
                  disabled={running}
                />
              </label>
            </div>
            <button className="primary full" type="button" onClick={() => void start()} disabled={busy || running}>
              <Search size={17} />
              {running ? "正在检索…" : "开始研究"}
            </button>
            <button
              className="secondary full"
              type="button"
              onClick={() => void supplement()}
              disabled={running || !candidates.length || supplementLeft <= 0}
            >
              <Upload size={16} /> 补充检索（剩 {supplementLeft} 次）
            </button>
            <p className="muted-note">
              发起检索将使用北大法宝检索额度，并产生 AI 分析费用。
            </p>
            <p className="muted-note">
              样本状态：<b>{gate.text}</b>
            </p>
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

            <div className="result-toolbar" style={{ marginTop: 16 }}>
              <div className="section-label">
                <span>02</span>候选材料与人工确认
              </div>
              <span className="light-badge">{locked ? "样本已确认" : `${candidates.length} 篇候选`}</span>
            </div>

            <CandidatePool
              candidates={candidates}
              selection={selection}
              onSelectionChange={setSelection}
              onConfirm={() => void confirmSample()}
              confirming={confirming || (running && Boolean(confirming))}
              locked={locked}
              confirmedIds={confirmedIds}
            />

            <div className="result-toolbar" style={{ marginTop: 16 }}>
              <div className="section-label">
                <span>03</span>横向对比与综合结论
              </div>
              <span className="light-badge">{locked ? "已生成" : "等待样本确认"}</span>
            </div>

            <MatrixTable rows={snapshot?.matrix ?? []} />
            <DistributionNote distribution={snapshot?.distribution ?? null} />

            <Conclusions
              synthesis={snapshot?.synthesis ?? null}
              degradedTexts={snapshot?.gate_report?.degraded_texts ?? []}
              sources={snapshot?.sources ?? []}
              onOpenSource={sourceId =>
                setDetail((snapshot?.sources ?? []).find(item => item.source_id === sourceId) ?? null)
              }
            />

            {(snapshot?.limitations?.length ?? 0) > 0 && (
              <div className="fzx-note" data-tone="warn">
                <strong>本次限制</strong>
                <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                  {snapshot?.limitations.map(item => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            )}

            <EvidenceTabs
              sources={snapshot?.sources ?? []}
              materials={snapshot?.materials ?? []}
              gate={snapshot?.gate_report ?? null}
              gaps={snapshot?.gaps}
              synthesis={snapshot?.synthesis ?? null}
              conflicts={snapshot?.conflicts ?? []}
              onOpenSource={sourceId =>
                setDetail((snapshot?.sources ?? []).find(item => item.source_id === sourceId) ?? null)
              }
              onVerify={sourceId => void verifyMaterial(sourceId)}
              onRoleChange={(sourceId, role) => void changeRole(sourceId, role)}
              onResolveConflict={sourceId => void resolveConflict(sourceId)}
              onUploadFiles={files => void uploadFiles(files)}
              uploading={uploading}
              uploadRejected={uploadRejected}
              maxFiles={bootstrap?.materials.max_files_per_upload ?? 20}
            />

            <div className="fzx-monitor" style={{ marginTop: 14 }} aria-label="导出">
              <div className="fzx-monitor-head">
                <h3>研究备忘录（Word）</h3>
                <div className="fzx-actions" style={{ margin: 0 }}>
                  <button type="button" className="primary" onClick={() => void doExport()} disabled={exp.disabled || exporting}>
                    <Download size={16} /> {exporting ? "正在导出…" : "导出 Word"}
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
                <p className="fzx-note">
                  <FileText size={14} /> 引用核验已完成，可导出研究备忘录。
                </p>
              )}
            </div>
          </article>
        </div>
      </main>

      <SourceSheet source={detail} onClose={() => setDetail(null)} />
    </div>
  );
}
