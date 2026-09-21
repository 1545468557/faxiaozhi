"use client";

/**
 * 类案检索 · 新界面（2026-09-20，沿用"过程收掉、只留结果"）
 *
 * 四屏：① 说你要查什么 → ② 正在查 → ③ 挑案例（人工门）→ ④ 研究报告
 *
 * 与上一代界面的差别只在"怎么摆"：**接口一条没改，业务规则一条没改**——
 * 样本确认人工门（后端强制）、引用核验、只展示通过核验的结论、导出前置条件全部沿用。
 * 界面用语不用内部词（"样本锁 / 矩阵 / 门禁覆盖率"），全部收进"想看细节"。
 *
 * 人工门在界面上的说法：不是我"确认样本"，而是**你挑哪几篇来对比**。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, ArrowRight, Download, FileText, Info, RefreshCw, Search, Upload } from "lucide-react";
import { BackendStatus, useBackend } from "@/components/backend-status";
import { EvidenceTabs } from "@/components/workspace/evidence-tabs";
import { Conclusions, DistributionNote, MatrixTable } from "@/components/workspace/research-output";
import { SnapshotFailure } from "@/components/workspace/run-monitor";
import { SourceSheet } from "@/components/workspace/source-sheet";
import { useRun } from "@/components/workspace/use-run";
import { useSessionState } from "@/components/workspace-session";
import { api, ApiError, createSession, describeError, downloadUrl, forgetSession, loadState } from "@/lib/api";
import { exportState, gapList } from "@/lib/api/common";
import {
  canConfirmSample,
  confirmPayload,
  costWarning,
  defaultSelection,
  distributionText,
  selectionSummary,
  sampleGateState,
  toggleSelection,
} from "@/lib/api/research";
import type { SessionState, SourceBrief } from "@/lib/api/types";
import { ResearchCandidates, ResearchProgress, ResearchSteps } from "./research-design";
import { PHASE_LABELS } from "@/components/workspace/run-monitor";

const reason = (cause: unknown) =>
  describeError(
    cause instanceof ApiError ? cause.code : undefined,
    cause instanceof Error ? cause.message : undefined,
  ).fallback;

const PURPOSES = ["实务参考", "课程学习", "教学备课", "学术研究"] as const;

export function ResearchStudio() {
  const [conflictJump, setConflictJump] = useState(0);
  const [activeCandidate, setActiveCandidate] = useState("");
  const [reportMode, setReportMode] = useState(false);
  const [resultTab, setResultTab] = useState("overview");
  const [topic, setTopic] = useSessionState<string>("research:topic", "");
  const [purpose, setPurpose] = useSessionState<string>("research:purpose", "实务参考");
  const [conditions, setConditions] = useSessionState<Record<string, string>>("research:conditions", {});
  const [selection, setSelection] = useSessionState<string[]>("research:selection", []);
  const [snapshot, setSnapshot] = useState<SessionState | null>(null);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshedAt, setRefreshedAt] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadRejected, setUploadRejected] = useState<{ filename: string; message?: string }[]>([]);
  const [detail, setDetail] = useState<SourceBrief | null>(null);
  const [resetting, setResetting] = useState(false);
  const sidRef = useRef<string | null>(null);
  const pollTimer = useRef<number | null>(null);
  const { bootstrap } = useBackend();

  /** 确保有会话（并发只建一次）；拿不到就明确报错，绝不静默什么都不做 */
  const sessionPromise = useRef<Promise<string | null> | null>(null);
  const ensureSession = useCallback(async (): Promise<string | null> => {
    if (sidRef.current) return sidRef.current;
    if (!sessionPromise.current) {
      sessionPromise.current = (async () => {
        try {
          const { sid, state } = await loadState("research", {
            onRecreated: () => setNotice("原会话已过期或服务已重启，已自动建立新会话。"),
          });
          sidRef.current = sid;
          setSnapshot(state);
          return sid;
        } catch (cause) {
          setNotice(reason(cause));
          return null;
        } finally {
          sessionPromise.current = null;
        }
      })();
    }
    return sessionPromise.current;
  }, []);

  const refresh = useCallback(async (): Promise<boolean> => {
    const sid = await ensureSession();
    if (!sid) return false;
    try {
      setSnapshot(await api.state(sid));
      return true;
    } catch (cause) {
      setNotice(reason(cause));
      return false;
    }
  }, [ensureSession]);

  const refreshWithFeedback = useCallback(async () => {
    setRefreshing(true);
    setRefreshedAt(null);
    const ok = await refresh();
    setRefreshing(false);
    if (ok) setRefreshedAt(new Date().toLocaleTimeString("zh-CN", { hour12: false }));
  }, [refresh]);

  const stopPolling = useCallback(() => {
    if (pollTimer.current) {
      window.clearInterval(pollTimer.current);
      pollTimer.current = null;
    }
  }, []);

  /** 有界轮询兜底（2.5 秒一次、最多 4 分钟，落定即停） */
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
          const done =
            next.status === "awaiting_checkpoint" ||
            Boolean(next.failed_step) ||
            next.sample.locked ||
            (next.matrix?.length ?? 0) > 0 ||
            next.export?.ready === true;
          if (done) {
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

  useEffect(() => {
    void (async () => {
      const sid = await ensureSession();
      if (!sid) return;
      const state = await api.state(sid).catch(() => null);
      if (!state) return;
      setSnapshot(state);
      // 换页返回 / 断线：会话还在跑且没有结果 → 轮询兜底
      if (state.run_id && state.status === "active" && !state.sample?.locked && !state.failed_step && !(state.matrix?.length > 0)) {
        pollUntilSettled();
      }
    })();
  }, [ensureSession, pollUntilSettled]);

  const start = useCallback(async () => {
    if (busy || phase === "running") return;
    if (!topic.trim()) {
      setNotice("请先写清你要查什么。");
      return;
    }
    setBusy(true);
    setNotice("");
    setDetail(null);
    setUploadRejected([]);
    setSelection([]);
    const sid = await ensureSession();
    if (!sid) {
      setBusy(false);
      return;
    }
    try {
      run.start(sid, () => api.message(sid, topic.trim(), { conditions, purpose }));
    } catch (cause) {
      setBusy(false);
      const info = describeError(
        cause instanceof ApiError ? cause.code : undefined,
        cause instanceof Error ? cause.message : undefined,
      );
      run.fail(info.code, info.fallback);
    }
  }, [busy, conditions, ensureSession, phase, purpose, run, setSelection, topic]);

  const retry = useCallback(async () => {
    const sid = await ensureSession();
    if (!sid) return;
    setBusy(true);
    run.start(sid, () => api.retry(sid));
  }, [ensureSession, run]);

  /** 人工门：你挑哪几篇来对比（后端仍然强制，未确认不得进入对比与结论） */
  const confirmSample = useCallback(async () => {
    const sid = await ensureSession();
    if (!sid || !snapshot) return;
    const candidates = snapshot.candidates ?? [];
    const payload = confirmPayload(selection, candidates);
    if (!payload.confirmed.length) {
      setNotice("请先挑至少 1 篇。");
      return;
    }
    setConfirming(true);
    setNotice("");
    run.resume();
    pollUntilSettled();
    void api.checkpoint(sid, "sample_confirm", payload).catch((cause: unknown) => {
      setConfirming(false);
      stopPolling();
      setNotice(reason(cause));
    });
  }, [ensureSession, pollUntilSettled, run, selection, snapshot, stopPolling]);

  const supplement = useCallback(async () => {
    const sid = await ensureSession();
    if (!sid) return;
    setBusy(true);
    run.resume();
    pollUntilSettled();
    void api.supplement(sid).catch((cause: unknown) => {
      setBusy(false);
      setNotice(reason(cause));
    });
  }, [ensureSession, pollUntilSettled, run]);

  const uploadFiles = useCallback(
    async (files: File[]) => {
      setUploading(true);
      setNotice("");
      const sid = await ensureSession();
      if (!sid) {
        setUploading(false);
        return;
      }
      try {
        const form = new FormData();
        files.forEach(file => form.append("files", file));
        const result = await api.upload(sid, form);
        setUploadRejected(result.rejected ?? []);
        if (result.materials?.length) setNotice(`已收录 ${result.materials.length} 个文件；点「本人已核验」后才会被引用。`);
        await refresh();
      } catch (cause) {
        setNotice(reason(cause));
      } finally {
        setUploading(false);
      }
    },
    [ensureSession, refresh],
  );

  const verifyMaterial = useCallback(
    async (sourceId: string) => {
      const sid = await ensureSession();
      if (!sid) return;
      try {
        await api.verifyMaterial(sid, sourceId);
        await refresh();
      } catch (cause) {
        setNotice(reason(cause));
      }
    },
    [ensureSession, refresh],
  );

  const resolveConflict = useCallback(
    async (sourceId: string) => {
      const sid = await ensureSession();
      if (!sid) return;
      try {
        await api.resolveConflict(sid, sourceId, "use_user_material");
        await refresh();
      } catch (cause) {
        setNotice(reason(cause));
      }
    },
    [ensureSession, refresh],
  );

  const doExport = useCallback(async () => {
    const sid = await ensureSession();
    if (!sid) return;
    setNotice("");
    try {
      const result = await api.exportResearch(sid);
      if (result.filename) {
        window.location.href = downloadUrl(result.filename);
        setNotice(`已生成：${result.filename}`);
      }
      await refresh();
    } catch (cause) {
      setNotice(reason(cause));
    }
  }, [ensureSession, refresh]);

  const resetSession = useCallback(async () => {
    setResetting(true);
    try {
      run.reset();
      setBusy(false);
      setConfirming(false);
      setReportMode(false);
      stopPolling();
      forgetSession("research");
      const sid = await createSession("research");
      sidRef.current = sid;
      setSnapshot(null);
      setSelection([]);
      setUploadRejected([]);
      setNotice("已清空：现在是一份全新的会话，请重新写你要查的问题。");
    } catch (cause) {
      setNotice(reason(cause));
    } finally {
      setResetting(false);
    }
  }, [run, setSelection, stopPolling]);

  // ------------------------------------------------------------------ 派生状态
  const candidates = snapshot?.candidates ?? [];
  const locked = Boolean(snapshot?.sample?.locked);
  const confirmedIds = snapshot?.sample?.confirmed ?? [];
  const matrix = snapshot?.matrix ?? [];
  const synthesis = snapshot?.synthesis ?? null;
  const conclusionCount = synthesis?.conclusions?.length ?? 0;
  const hasResult = matrix.length > 0 || conclusionCount > 0;
  const failure = snapshot?.failed_step ?? null;
  const exp = exportState(snapshot?.export);
  const gate = sampleGateState({
    locked,
    confirmed: confirmedIds,
    candidates,
    awaitingCheckpoint: snapshot?.status === "awaiting_checkpoint",
  });
  const selectionInfo = selectionSummary(selection, bootstrap?.limits?.min_sample_for_conclusion ?? 2);
  const gateCheck = canConfirmSample(selection);
  const warning = costWarning(selection);
  const supplementLeft = snapshot?.supplement?.remaining ?? 0;

  const started = Boolean(snapshot?.run_id) || phase !== "idle";
  const settled = Boolean(snapshot?.status === "awaiting_checkpoint") || locked || hasResult || Boolean(failure) || phase === "error" || phase === "done";
  const screen: "ask" | "searching" | "pick" | "report" = !started
    ? "ask"
    : !settled
      ? "searching"
      : locked || hasResult
        ? "report"
        : "pick";

  // 真实进度（只用后端事件的数字）
  const lastPhase = run.state.phases.at(-1);
  const progress = lastPhase && lastPhase.total > 0 ? Math.min(100, Math.round((lastPhase.index / lastPhase.total) * 100)) : 0;
  const stepText = lastPhase ? `第 ${lastPhase.index} 步 / 共 ${lastPhase.total} 步` : "";
  const currentPhaseText = PHASE_LABELS[run.state.currentPhase] ?? "";

  // 结果收尾后快照可能晚一拍：自动再取一次
  const retrieving = screen === "report" && !hasResult && !failure && locked;
  useEffect(() => {
    if (!retrieving) return;
    const timer = window.setTimeout(() => void refresh(), 1500);
    return () => window.clearTimeout(timer);
  }, [retrieving, refresh, refreshedAt]);

  useEffect(() => {
    if (!conflictJump) return;
    const target = document.getElementById("research-source-conflicts");
    target?.scrollIntoView({ block: "center", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
    target?.focus({ preventScroll: true });
  }, [conflictJump]);

  return (
    <div className={`ct-wrap rx-studio rx-screen-${screen} ${reportMode ? "rx-report-mode" : ""}`} data-result-tab={resultTab}>
      <ResearchSteps step={screen === "ask" || screen === "searching" ? 0 : screen === "pick" ? 1 : reportMode ? 3 : 2} />
      <header className="ct-head">
        <h1>{screen === "ask" ? "找相似的案子，看清不同的结果" : screen === "pick" ? "这几篇，值得放在一起看" : screen === "searching" ? "类案检索" : reportMode ? "把研究过程，整理成一份报告" : "相似的争议，差异在这里"}</h1>
        <p>{screen === "ask" ? "说说你的情况。我们一起看看，哪些事实真正影响了裁判。" : "从关键事实、裁判观点到引用出处，逐步核对，再作判断。"}</p>
      </header>

      <BackendStatus />

      {notice && (
        <p className="ct-blocked" role="status" style={{ marginBottom: 14 }}>
          <Info size={14} aria-hidden="true" />
          {notice}
        </p>
      )}

      {/* ======================================================== ① 说你要查什么 */}
      {screen === "ask" && (
        <section className="ct-card rx-intake">
          <label htmlFor="research-topic" className="rx-input-label">你想查什么？</label>
          <textarea
            id="research-topic"
            className="ct-input"
            rows={4}
            value={topic}
            placeholder="用大白话写下你的情况，例如：房东把房子卖了，新房东让我两个月内搬走，合同还有 8 个月到期。"
            aria-label="你要查什么"
            onChange={event => setTopic(event.target.value)}
          />

          <details className="ct-more" style={{ marginTop: 12 }}>
            <summary>补充条件（可以留空）</summary>
            <div className="ct-fields">
              <label>
                案由
                <input
                  value={conditions.cause ?? ""}
                  placeholder="不填＝不限"
                  onChange={event => setConditions({ ...conditions, cause: event.target.value })}
                />
              </label>
              <label>
                地域
                <input
                  value={conditions.region ?? ""}
                  placeholder="不填＝全国"
                  onChange={event => setConditions({ ...conditions, region: event.target.value })}
                />
              </label>
              <label>
                用途
                <select value={purpose} onChange={event => setPurpose(event.target.value)}>
                  {PURPOSES.map(item => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </details>

          {/* 材料为主那条路径（决策 A）：自己的材料可以一起传，未核验不会被引用 */}
          <details className="ct-more" style={{ marginTop: 12 }}>
            <summary>
              有材料？可以一起传（可选）{(snapshot?.materials ?? []).length ? ` · 已传 ${(snapshot?.materials ?? []).length} 份` : ""}
            </summary>
            <div style={{ marginTop: 12 }}>
              <label className="ct-drop" style={{ padding: "18px 16px" }}>
                <input
                  type="file"
                  accept=".docx,.pdf,.txt,.md"
                  multiple
                  disabled={uploading}
                  onChange={event => {
                    const files = Array.from(event.target.files ?? []);
                    event.target.value = "";
                    if (files.length) void uploadFiles(files);
                  }}
                />
                <Upload size={20} strokeWidth={1.5} aria-hidden="true" />
                <strong>{uploading ? "正在上传…" : "上传判决书 / 案例材料"}</strong>
                <span>docx / pdf / txt / md；原文只在本次会话内存中处理，不落盘</span>
              </label>

              {(snapshot?.materials ?? []).map(material => (
                <div className="ct-file" key={String(material.source_id)} style={{ marginTop: 10 }}>
                  <FileText size={18} aria-hidden="true" />
                  <div>
                    <strong>{String(material.filename ?? "已上传的材料")}</strong>
                    <span>
                      {String(material.role_label ?? "")} · {material.verified ? "你已核验" : "还没有核验，不会被引用"}
                    </span>
                  </div>
                  {material.verified ? (
                    <span className="ct-badge" data-tone="ok">
                      已核验
                    </span>
                  ) : (
                    <button
                      type="button"
                      className="ct-btn ct-btn-primary"
                      onClick={() => void verifyMaterial(String(material.source_id))}
                    >
                      本人已核验
                    </button>
                  )}
                </div>
              ))}

              {uploadRejected.length > 0 && (
                <p className="ct-blocked" style={{ marginTop: 10 }} role="status">
                  <AlertTriangle size={14} aria-hidden="true" />
                  以下文件没有被收录：
                  {uploadRejected.map(item => `「${item.filename}」`).join("；")}
                </p>
              )}
              <p className="ct-mini">也可以不上传材料，直接检索相关案例。</p>
            </div>
          </details>

          <div className="ct-actions">
            <button type="button" className="ct-btn ct-btn-primary" disabled={busy || !topic.trim()} onClick={() => void start()}>
              <Search size={15} aria-hidden="true" />
              开始找相似案例
            </button>

          </div>
          <div className="rx-examples">{["房东卖房后，新房东要求我搬走，但租约还有8个月。", "公司拖欠三个月工资，想查找类似劳动争议案例。", "服务合同提前解除，已支付的费用能退回多少？"].map((example, i) => <button className="ct-btn" type="button" key={example} onClick={() => { setTopic(example); document.getElementById("research-topic")?.focus(); }}><strong>{["租房纠纷", "劳动争议", "合同纠纷"][i]}</strong><span>{example}</span></button>)}</div>
          <p className="ct-mini">检索会使用北大法宝的检索额度，并产生 AI 分析费用（一次约几毛钱）。</p>
        </section>
      )}

      {/* ======================================================== ② 正在查 */}
      {screen === "searching" && (
        <section className="ct-card">
          <ResearchProgress title="正在整理值得参考的案例" progress={progress} label={currentPhaseText} stepText={stepText} />

          <p className="ct-mini">
            找完会先给你看候选案例，<b>让你确认对比哪几篇</b>；不确认不会往下做。不用盯着看，可稍后回来查看进度。
          </p>

          <div className="ct-actions">
            <button type="button" className="ct-btn ct-btn-ghost" onClick={() => void refreshWithFeedback()} disabled={refreshing}>
              <RefreshCw size={14} aria-hidden="true" />
              {refreshing ? "正在重新取…" : "刷新状态"}
            </button>
          </div>
          {refreshedAt && (
            <p className="ct-mini" role="status">
              已重新取回状态（{refreshedAt}）。如果这里仍显示进行中，说明后端确实还在跑。
            </p>
          )}
        </section>
      )}

      {/* ======================================================== ③ 挑案例 */}
      {screen === "pick" && (
        <section className="ct-card">
          {failure && !candidates.length ? (
            <>
              <h2 style={{ fontSize: 19, marginBottom: 6 }}>这次没查到，不是「没有相关规定」</h2>
              <p className="ct-lead">{snapshot?.failed_reason || "检索没能完成。"}</p>
              <p className="ct-mini">
                卡在「{failure}」。检索接口失败<b>不等于</b>没有相关规定，也不代表你的问题没有依据；
                可以重试，或者换个说法再查一次。
              </p>
              <div className="ct-actions">
                <button type="button" className="ct-btn ct-btn-primary" disabled={busy} onClick={() => void retry()}>
                  重试没做成的那一步
                </button>
                <button type="button" className="ct-btn ct-btn-ghost" onClick={() => void refreshWithFeedback()} disabled={refreshing}>
                  <RefreshCw size={14} aria-hidden="true" />
                  {refreshing ? "正在重新取…" : "刷新状态"}
                </button>
              </div>
            </>
          ) : (
            <>
              <h2 style={{ fontSize: 19, marginBottom: 4 }}>
                我找到 {candidates.length} 篇相似案例，你挑几篇来对比
              </h2>
              <p className="ct-lead">
                对比要逐篇细读，选 <b>3-5 篇最接近的</b>就够；挑得太杂，结论反而不准。
              </p>
              {snapshot?.supplement?.material_primary ? (
                <p className="ct-mini" style={{ marginBottom: 10 }}>
                  这些候选来自<b>你上传的材料</b>（材料为主路径）；材料不够时才会去法规库补充。
                </p>
              ) : null}

              <div className="rx-query"><strong>当前问题</strong><p>{snapshot?.topic || topic}</p></div>
              <ResearchCandidates candidates={candidates} sources={snapshot?.sources ?? []} selection={selection} activeId={activeCandidate} onActive={setActiveCandidate} onToggle={id => setSelection(toggleSelection(selection, id))} onOpen={setDetail} />

              <div className="ct-actions">
                <span className="ct-mini">已选 {selection.length} 篇（{selectionInfo.note}）</span>
                <button type="button" className="ct-btn" onClick={() => setSelection(defaultSelection(candidates))}>
                  选前 5 篇
                </button>
                <button type="button" className="ct-btn" onClick={() => setSelection(candidates.map(item => item.source_id))}>
                  全选
                </button>
                <button type="button" className="ct-btn" onClick={() => setSelection([])}>
                  清空
                </button>
              </div>

              {warning && (
                <p className="ct-blocked" style={{ marginTop: 10 }} role="status">
                  <AlertTriangle size={14} aria-hidden="true" />
                  {warning}
                </p>
              )}

              <div className="ct-actions">
                <button
                  type="button"
                  className="ct-btn ct-btn-primary"
                  disabled={!gateCheck.ok || confirming || busy}
                  onClick={() => void confirmSample()}
                >
                  就对比这 {selection.length} 篇
                  <ArrowRight size={15} aria-hidden="true" />
                </button>
                {!gateCheck.ok && (
                  <p className="ct-blocked" role="status">
                    <AlertTriangle size={14} aria-hidden="true" />
                    {gateCheck.reason}
                  </p>
                )}
              </div>
              <p className="ct-mini">没确认之前，系统不会拿这些材料去对比出结论。</p>
            </>
          )}
        </section>
      )}

      {/* ======================================================== ④ 研究报告 */}
      {screen === "report" && (
        <>
          <SnapshotFailure
            failedStep={snapshot?.failed_step}
            failedReason={snapshot?.failed_reason}
            canRetry={snapshot?.can_retry}
            onRetry={() => void retry()}
            retrying={busy && phase !== "running"}
          />

          {retrieving && !hasResult ? (
            <section className="ct-card">
              <ResearchProgress title="把案例放在一起，看看关键差异" progress={progress} label={currentPhaseText} stepText={stepText} />
              <p className="ct-lead">
                你已经挑好了案例，正在逐篇对比、整理结论。<b>现在还没有结论</b>，请稍等一下。
              </p>
              <div className="ct-actions">
                <button type="button" className="ct-btn ct-btn-ghost" onClick={() => void refreshWithFeedback()} disabled={refreshing}>
                  <RefreshCw size={14} aria-hidden="true" />
                  {refreshing ? "正在重新取…" : "刷新状态"}
                </button>
              </div>
            </section>
          ) : (
            <div className="rx-results">
              <div className="rx-result-toolbar"><div className="rx-tabs" aria-label="分析内容">{[["overview", "主要发现"], ["matrix", "逐案对比"], ["evidence", "依据与缺口"]].map(([id, label]) => <button type="button" className="ct-btn" key={id} aria-pressed={resultTab === id} onClick={() => { setResultTab(id); setReportMode(false); }}>{label}</button>)}</div><button className="ct-btn ct-btn-primary" type="button" onClick={() => setReportMode(!reportMode)}>{reportMode ? "返回分析" : "预览研究报告"}</button></div>
              <div className="rx-report-content">
              {reportMode && <div className="rx-report-title"><FileText size={24}/><h2>类案检索与对比报告</h2><p>{snapshot?.topic || topic}</p></div>}
              <section className="ct-card rx-summary" style={{ marginBottom: 16 }}>
                <h2 style={{ fontSize: 20, marginBottom: 8 }}>
                  {conclusionCount ? "看完这几篇，可以这样理解你的情况" : "这几篇先放在这里"}
                </h2>
                {snapshot?.supplement?.material_primary ? (
                  <p className="ct-mini" style={{ marginBottom: 6 }}>
                    这次以<b>你上传的材料为主</b>（材料为主路径：不足时才去法规库补充）。
                  </p>
                ) : null}
                <p className="ct-lead" style={{ marginBottom: 0 }}>
                  基于你确认的 {confirmedIds.length} 篇{gate.text ? `（${gate.text}）` : ""}
                  {candidates.length > confirmedIds.length ? `；另有 ${candidates.length - confirmedIds.length} 篇未纳入对比` : ""}。
                  下面每条结论都能点开看出自哪个案子。
                </p>
              </section>

              <section className="ct-card rx-conclusions" style={{ marginBottom: 16 }}>
                <h3 style={{ fontSize: 16, margin: "0 0 10px" }}>主要发现</h3>
                <Conclusions
                  synthesis={synthesis}
                  degradedTexts={snapshot?.gate_report?.degraded_texts ?? []}
                  sources={snapshot?.sources ?? []}
                  onOpenSource={sourceId => setDetail((snapshot?.sources ?? []).find(item => item.source_id === sourceId) ?? null)}
                />
              </section>

              <section className="ct-card rx-matrix" style={{ marginBottom: 16 }}>
                <h3 style={{ fontSize: 16, margin: "0 0 6px" }}>这几篇法院怎么看（对比）</h3>
                <p className="ct-mini" style={{ marginBottom: 10 }}>
                  样本口径：已确认 {confirmedIds.length} 篇 / 候选 {candidates.length} 篇。
                </p>
                <MatrixTable rows={matrix} />
                <div style={{ marginTop: 12 }}>
                  <DistributionNote distribution={snapshot?.distribution ?? null} />
                  <p className="ct-mini">{distributionText(snapshot?.distribution ?? null)}</p>
                </div>
              </section>

              {(gapList(snapshot?.gaps, (snapshot?.gate_report?.degraded_texts ?? []).map(text => ({ text }))).length ?? 0) > 0 && (
                <details className="ct-more rx-gaps" open={reportMode || resultTab === "evidence"}>
                  <summary>
                    依据缺口（{gapList(snapshot?.gaps, (snapshot?.gate_report?.degraded_texts ?? []).map(text => ({ text }))).length} 条）
                  </summary>
                  <ul className="ct-range" style={{ marginTop: 10 }}>
                    {gapList(snapshot?.gaps, (snapshot?.gate_report?.degraded_texts ?? []).map(text => ({ text }))).map(item => (
                      <li key={`${item.kind}-${item.detail}`}>
                        <b>{item.kind}</b>：{item.detail}
                      </li>
                    ))}
                  </ul>
                </details>
              )}


              <details className="ct-more rx-evidence" open={resultTab === "evidence"}>
                <summary>
                  来源与核验详情（全部候选案例 {candidates.length} 篇 · 用到的法条与核验情况 · 我上传的材料）
                </summary>
                <div style={{ marginTop: 14 }}>
                  <EvidenceTabs
                    key={conflictJump}
                    sources={snapshot?.sources ?? []}
                    materials={snapshot?.materials ?? []}
                    gate={snapshot?.gate_report ?? null}
                    gaps={snapshot?.gaps}
                    synthesis={synthesis}
                    conflicts={snapshot?.conflicts ?? []}
                    onOpenSource={sourceId => setDetail((snapshot?.sources ?? []).find(item => item.source_id === sourceId) ?? null)}
                    onVerify={sourceId => void verifyMaterial(sourceId)}
                    onRoleChange={() => undefined}
                    onResolveConflict={sourceId => void resolveConflict(sourceId)}
                    onExcludeConflict={async sourceId => {
                      if (!window.confirm("将不采用此依据，并移除所有依赖它的整条结论。剩余内容重新核验通过后才能导出，报告会注明缺口。是否继续？")) return;
                      const sid = await ensureSession();
                      if (!sid) return;
                      try { await api.resolveConflict(sid, sourceId, "exclude_source"); await refresh(); setNotice("已不采用该依据及相关结论。请查看剩余结论和导出状态。"); }
                      catch (cause) { setNotice(reason(cause)); }
                    }}
                    onUploadFiles={files => void uploadFiles(files)}
                    uploading={uploading}
                    uploadRejected={uploadRejected}
                    maxFiles={bootstrap?.materials.max_files_per_upload ?? 20}
                  />
                </div>
              </details>
              </div>
              <section className="ct-export">
                <div>
                  <h3>导出研究报告（Word）</h3>
                  <p className="ct-mini">报告包含已核验结论、对比表及案例清单。导出前请核对依据与缺口。</p>
                  {!exp.ready && (
                    <p className="ct-blocked" role="status" style={{ marginTop: 8 }}>
                      <AlertTriangle size={14} aria-hidden="true" />
                      现在还导出不了：{exp.blockers.length ? exp.blockers.map(text => text.includes("来源冲突") ? "部分来源内容不一致，请先查看冲突材料并核对。" : text).join("；") : "这次还没有产出可导出的内容。"}
                    </p>
                  )}
                </div>
                <div className="ct-actions" style={{ margin: 0 }}>
                  {(snapshot?.conflicts ?? []).some(item => !item.resolved) && <button type="button" className="ct-btn" onClick={() => { setReportMode(false); setResultTab("evidence"); setConflictJump(value => value + 1); }}>查看并处理来源冲突</button>}
                  {supplementLeft > 0 && (
                    <button type="button" className="ct-btn" disabled={busy} onClick={() => void supplement()}>
                      再找一些（还剩 {supplementLeft} 次）
                    </button>
                  )}
                  <button type="button" className="ct-btn ct-btn-primary" disabled={exp.disabled} onClick={() => void doExport()}>
                    <Download size={15} aria-hidden="true" />
                    导出报告
                  </button>
                  <button type="button" className="ct-btn ct-btn-ghost" onClick={() => void refreshWithFeedback()} disabled={refreshing}>
                    <RefreshCw size={14} aria-hidden="true" />
                    {refreshing ? "正在重新取…" : "刷新状态"}
                  </button>
                </div>
                {refreshedAt && (
                  <p className="ct-mini" role="status" style={{ width: "100%" }}>
                    已重新取回状态（{refreshedAt}）。
                  </p>
                )}
              </section>

            </div>
          )}

          <details className="ct-more">
            <summary>换一个问题 / 清空重来</summary>
            <p className="ct-mini" style={{ marginTop: 10 }}>
              会另开一份全新的空会话：这次的案例与结论都不再显示（旧的要等会话过期或服务重启才消失）。
            </p>
            <div className="ct-actions">
              <button type="button" className="ct-btn" disabled={resetting} onClick={() => void resetSession()}>
                {resetting ? "正在清空…" : "清空重来"}
              </button>
            </div>
          </details>

          <p className="ct-foot">
            <FileText size={14} aria-hidden="true" />
            以上内容由 AI 生成，仅供学习与研究参考，不构成法律意见；引用不代表已有律师审定。没有提示的问题不等于没有风险。
          </p>
        </>
      )}

      <SourceSheet source={detail} onClose={() => setDetail(null)} />
    </div>
  );
}
