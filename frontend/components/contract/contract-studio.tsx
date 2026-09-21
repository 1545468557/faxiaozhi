"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Info, RefreshCw } from "lucide-react";
import { BackendStatus, useBackend } from "@/components/backend-status";
import { ContractResult } from "@/components/contract/contract-result";
import { ContractProgressVisual } from "./contract-progress";
import { ContractIntake } from "./contract-intake";
import { PHASE_LABELS } from "@/components/workspace/run-monitor";
import { useRun } from "@/components/workspace/use-run";
import { useSessionState } from "@/components/workspace-session";
import { api, ApiError, createSession, describeError, downloadUrl, forgetSession, loadState } from "@/lib/api";
import { exportState } from "@/lib/api/common";
import { STANCE_FALLBACK_LABELS, riskSummary, sortRisks, stanceGate } from "@/lib/api/contract";
import type { MaterialItem, SessionState } from "@/lib/api/types";

const reason = (cause: unknown) =>
  describeError(
    cause instanceof ApiError ? cause.code : undefined,
    cause instanceof Error ? cause.message : undefined,
  ).fallback;

export function ContractStudio() {
  const [stanceDraft, setStanceDraft] = useSessionState<string>("contract:stance", "");
  const [snapshot, setSnapshot] = useState<SessionState | null>(null);
  const [requirements, setRequirements] = useSessionState<string>("contract:requirements", "");
  const [reportMode, setReportMode] = useState(false);
  const [notice, setNotice] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadRejected, setUploadRejected] = useState<{ filename: string; message?: string }[]>([]);
  const [exporting, setExporting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [activeRiskId, setActiveRiskId] = useState<string | null>(null);
  const [confirmingReset, setConfirmingReset] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshedAt, setRefreshedAt] = useState<string | null>(null);
  /** 后端没认我们提前选的立场时的兜底：让用户从后端给的选项里重选 */
  const [stanceRejected, setStanceRejected] = useState(false);
  const sidRef = useRef<string | null>(null);
  const pollTimer = useRef<number | null>(null);
  const stanceSubmitted = useRef(false);
  const { bootstrap } = useBackend();

  /** 重新取一次后端状态；返回是否成功（供"刷新状态"按钮给反馈用） */
  const refresh = useCallback(async (): Promise<boolean> => {
    const sid = sidRef.current;
    if (!sid) return false;
    try {
      setSnapshot(await api.state(sid));
      return true;
    } catch (cause) {
      setNotice(reason(cause));
      return false;
    }
  }, []);

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

  /** 有界轮询兜底：事件流断开或漏事件时靠快照追平（落定即停） */
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

  /**
   * 确保有可用会话（**并发只建一次**）。
   *
   * 为什么必须这样：2026-09-20 实测发现——刚打开页面就交合同，此时会话还没建好，
   * 而旧的写法是 `if (!sid) return;` **静默什么都不做**，用户看到的就是"点了没反应"。
   * 现在：谁需要会话就 await 它；建不出来就明确报错，不装没事。
   */
  const sessionPromise = useRef<Promise<string | null> | null>(null);
  const ensureSession = useCallback(async (): Promise<string | null> => {
    if (sidRef.current) return sidRef.current;
    if (!sessionPromise.current) {
      sessionPromise.current = (async () => {
        try {
          const { sid, state } = await loadState("contract", {
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

  useEffect(() => {
    void ensureSession();
  }, [ensureSession]);

  const uploadContract = useCallback(
    async (files: File[]) => {
      if (files.length !== 1) { setNotice("一次请上传一份合同。"); return; }
      setUploading(true);
      setNotice("");
      const sid = await ensureSession();
      if (!sid) {
        setUploading(false);
        setNotice("会话还没准备好，刚才那份文件没有交上去，请再试一次。");
        return;
      }
      try {
        const form = new FormData();
        form.append("role", "contract");
        files.forEach(file => form.append("files", file));
        const result = await api.upload(sid, form);
        setUploadRejected(result.rejected ?? []);
        if (result.materials?.length) setNotice("合同已收到。确认内容没问题后勾选「本人已核验」，再开始审查。");
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

  /** 开始审查：后端会停在立场门；我们随即把用户提前选好的立场提交上去 */
  const startReview = useCallback(async () => {
    if (busy || phase === "running") return;
    const sid = await ensureSession();
    if (!sid) return;
    setBusy(true);
    setNotice("");
    setActiveRiskId(null);
    setStanceRejected(false);
    stanceSubmitted.current = false;
    try {
      run.start(sid, () => api.contractReview(sid, { requirements }));
    } catch (cause) {
      setBusy(false);
      const info = describeError(
        cause instanceof ApiError ? cause.code : undefined,
        cause instanceof Error ? cause.message : undefined,
      );
      run.fail(info.code, info.fallback);
    }
  }, [busy, ensureSession, phase, run, requirements]);

  /** 立场门到达：提交用户已选的值（人工门仍由后端强制；不认就回退成重选） */
  useEffect(() => {
    if (run.state.awaiting !== "stance_confirm" || stanceSubmitted.current) return;
    const sid = sidRef.current;
    if (!sid || !stanceDraft) return;
    stanceSubmitted.current = true;
    setBusy(true);
    run.resume();
    pollUntilSettled();
    void api.checkpoint(sid, "stance_confirm", { stance: stanceDraft }).catch((cause: unknown) => {
      stanceSubmitted.current = false;
      setBusy(false);
      stopPolling();
      setStanceRejected(true);
      setNotice(reason(cause));
    });
  }, [run, stanceDraft, pollUntilSettled, stopPolling]);

  /** 兜底重选（后端给的选项为准） */
  const submitStanceFromGate = useCallback(
    async (value: string) => {
      const sid = sidRef.current;
      if (!sid) return;
      setStanceDraft(value);
      setBusy(true);
      setNotice("");
      run.resume();
      pollUntilSettled();
      void api.checkpoint(sid, "stance_confirm", { stance: value }).catch((cause: unknown) => {
        setBusy(false);
        stopPolling();
        setNotice(reason(cause));
      });
      setStanceRejected(false);
    },
    [pollUntilSettled, run, setStanceDraft, stopPolling],
  );

  const retry = useCallback(async () => {
    const sid = await ensureSession();
    if (!sid) return;
    setBusy(true);
    run.start(sid, () => api.retry(sid));
  }, [ensureSession, run]);

  const doExport = useCallback(async (review: Record<string, {status: string; suggestion: string}> = {}) => {
    const sid = await ensureSession();
    if (!sid) return;
    setExporting(true);
    setNotice("");
    try {
      const result = await api.contractExport(sid, { review });
      if (result.filename) {
        window.location.href = downloadUrl(result.filename);
        setNotice(`已生成：${result.filename}`);
      }
      await refresh();
    } catch (cause) {
      setNotice(reason(cause));
    } finally {
      setExporting(false);
    }
  }, [ensureSession, refresh]);

  /** 清空重来：另开一个空会话（后端没有删除材料的接口，这是如实可用的替代办法） */
  const resetSession = useCallback(async () => {
    setResetting(true);
    try {
      run.reset();
      setBusy(false);
      stopPolling();
      forgetSession("contract");
      const sid = await createSession("contract");
      sidRef.current = sid;
      setSnapshot(null);
      setActiveRiskId(null);
      setStanceDraft("");
      setRequirements("");
      setReportMode(false);
      setUploadRejected([]);
      setStanceRejected(false);
      stanceSubmitted.current = false;
      setNotice("已清空：现在是一份全新的会话，请重新上传合同。");
    } catch (cause) {
      setNotice(reason(cause));
    } finally {
      setResetting(false);
      setConfirmingReset(false);
    }
  }, [run, setStanceDraft, setRequirements, stopPolling]);

  // ------------------------------------------------------------------ 派生状态
  const contract = snapshot?.contract;
  const materials = (snapshot?.materials ?? []) as MaterialItem[];
  const material = materials.find(item => !item.superseded) ?? materials[0];
  const liveMaterials = materials.filter(item => !item.superseded);
  const supersededCount = materials.filter(item => item.superseded).length;
  const gate = stanceGate(contract, run.state.checkpoint);
  const risks = sortRisks(contract?.risks ?? []);
  const summary = riskSummary(contract);
  const exp = exportState(snapshot?.export);
  const running = phase === "running" || phase === "awaiting";
  const finished = Boolean(contract?.stance && (contract?.status === "reviewed" || contract?.status === "insufficient"));
  const truncated = (snapshot?.limitations ?? []).filter(item => /截断|未审查|上限/.test(item));

  const started = Boolean(snapshot?.run_id) || phase !== "idle";
  const settled = finished || Boolean(snapshot?.failed_step) || phase === "error" || phase === "done";
  /**
   * 结果是否**真的取回来了**：跑完（status 落定）或明确失败，才算有结论。
   * 2026-09-20 实测踩到：运行刚结束、快照还没取回时，界面会先显示"没有挑出对你不利的地方"——
   * 那是瞎说（我们其实还不知道）。现在这种情况显示"正在取回结果…"。
   */
  const resultReady = Boolean(
    contract?.status === "reviewed" || contract?.status === "insufficient" || snapshot?.failed_step,
  );
  const screen: "input" | "waiting" | "retrieving" | "result" = !started
    ? "input"
    : !settled
      ? "waiting"
      : resultReady
        ? "result"
        : "retrieving";

  // 真实进度：只用后端给的事件数据，没有就说没有
  const lastPhase = run.state.phases.at(-1);
  const progress = lastPhase && lastPhase.total > 0 ? Math.min(100, Math.round((lastPhase.index / lastPhase.total) * 100)) : 0;
  const stepText = lastPhase ? `第 ${lastPhase.index} 步 / 共 ${lastPhase.total} 步` : "";
  const currentPhaseText = PHASE_LABELS[run.state.currentPhase] ?? "";

  const canStart = Boolean(material?.verified) && Boolean(stanceDraft) && liveMaterials.length === 1 && !busy && !running;

  /** 运行已收尾但结果还没取回来：自动再取一次（只取到就停） */
  useEffect(() => {
    if (screen !== "retrieving") return;
    const timer = window.setTimeout(() => void refresh(), 1500);
    return () => window.clearTimeout(timer);
  }, [screen, refresh, refreshedAt]);


  return (
    <div className="ct-wrap cx-studio">
      <header className="ct-head" hidden={screen === "waiting"}>
        <span className="cx-eyebrow">合同审查</span><h1>{screen === "input" ? material ? "先确认，这次站在哪一方看" : "把合同交给我，一起看清风险" : reportMode ? "把审查意见，整理成一份清楚的报告" : screen === "result" ? "对照合同，逐条斟酌修改意见" : "正在梳理合同里的关键约定"}</h1><p>从合同原文出发，看清风险，留下可追溯的修改意见。</p>
      </header>

      <ol className="cx-steps" aria-label="审查步骤">{["放入合同", "确认要求", "审查内容", "生成报告"].map((label, index) => { const current = screen === "input" ? material ? 1 : 0 : reportMode ? 3 : 2; return <li key={label} data-state={index === current ? "current" : index < current ? "done" : "todo"} aria-current={index === current ? "step" : undefined}><b>{index < current ? "✓" : index + 1}</b>{label}</li>; })}</ol>
      <BackendStatus />

      {notice && (
        <p className="ct-blocked" role="status" style={{ marginBottom: 14 }}>
          <Info size={14} aria-hidden="true" />
          {notice}
        </p>
      )}

      {screen === "input" && <ContractIntake material={material} uploading={uploading} stance={stanceDraft} onStance={setStanceDraft} onUpload={files=>void uploadContract(files)} onVerify={id=>void verifyMaterial(id)} onReset={()=>setConfirmingReset(true)} onStart={()=>void startReview()} canStart={canStart} requirements={requirements} onRequirements={setRequirements}/>}
      {screen === "input" && liveMaterials.length > 1 && <p className="ct-blocked">当前有多份合同，请更换合同并重新上传一份后再审查。</p>}
      {screen === "input" && supersededCount > 0 && <p className="ct-mini">{supersededCount} 份旧版本已被替代。</p>}
      {uploadRejected.map((item,index)=><p className="ct-blocked" role="alert" key={index}>「{item.filename}」未收录：{item.message||"请检查文件格式与大小。"}</p>)}
      {confirmingReset && <section className="ct-more"><p>将开启新的合同会话，当前结果不再显示。请先导出需要保留的报告。</p><div className="ct-actions"><button className="ct-btn ct-btn-primary" disabled={resetting} onClick={()=>void resetSession()}>确认更换，重新上传</button><button className="ct-btn" onClick={()=>setConfirmingReset(false)}>取消</button></div></section>}

      {/* ============================================================ ② 正在读 */}
      {screen === "waiting" && (
        <section className="ct-card cx-waiting">
          <ContractProgressVisual progress={lastPhase ? progress : undefined} phaseLabel={currentPhaseText} stepText={stepText}/>

          {/* 兜底：后端没认我们提前选的立场时，从这里重选（后端给的选项为准） */}
          {stanceRejected && (
            <div className="ct-card" style={{ marginTop: 14, background: "#fdf8ec" }}>
              <p className="ct-mini" style={{ marginBottom: 8 }}>
                这次没能用你选的立场（{stanceDraft ? STANCE_FALLBACK_LABELS[stanceDraft] ?? stanceDraft : "未选"}）。
                请从下面重新选一次：
              </p>
              <div className="ct-actions" style={{ marginTop: 0 }}>
                {gate.options.map(option => (
                  <button
                    key={option.value}
                    type="button"
                    className="ct-btn"
                    disabled={busy}
                    onClick={() => void submitStanceFromGate(option.value)}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="ct-actions">
            <button type="button" className="ct-btn ct-btn-ghost" onClick={() => void refreshWithFeedback()} disabled={refreshing}>
              <RefreshCw size={14} aria-hidden="true" />
              {refreshing ? "正在重新取…" : "刷新状态"}
            </button>
            {running && (
              <button
                type="button"
                className="ct-btn ct-btn-ghost"
                onClick={() => {
                  run.stop();
                  setNotice("已不再等待这次结果。后台仍会跑完，稍后点「刷新状态」或重新进入本页可取回结果。");
                }}
              >
                不再等待（后台会继续跑完）
              </button>
            )}
          </div>
          {refreshedAt && (
            <p className="ct-mini" role="status" style={{ marginTop: 10 }}>
              已重新取回状态（{refreshedAt}）。如果这里仍显示进行中，说明后端确实还在跑。
            </p>
          )}
        </section>
      )}

      {/* ========================================================= ②b 取回结果中 */}
      {screen === "retrieving" && (
        <section className="ct-card">
          <h2 style={{ fontSize: 19, marginBottom: 6 }}>正在取回这次的结果…</h2>
          <p className="ct-lead">
            这次审查已经跑完，正在把结果读回来。<b>现在还没有结论</b>，请稍等一下——不要当成「没有问题」。
          </p>
          <div className="ct-actions">
            <button type="button" className="ct-btn ct-btn-ghost" onClick={() => void refreshWithFeedback()} disabled={refreshing}>
              <RefreshCw size={14} aria-hidden="true" />
              {refreshing ? "正在重新取…" : "刷新状态"}
            </button>
          </div>
          {refreshedAt && (
            <p className="ct-mini" role="status">
              已重新取回状态（{refreshedAt}）。
            </p>
          )}
        </section>
      )}

      {/* ============================================================ ③ 问题清单 */}
      {screen === "result" && (
        <ContractResult
          contract={contract}
          risks={risks}
          activeRiskId={activeRiskId}
          onSelectRisk={setActiveRiskId}
          summary={summary}
          sources={snapshot?.sources ?? []}
          stanceLabel={gate.label}
          exportInfo={exp}
          onExport={review => void doExport(review)}
          reportMode={reportMode}
          onReportMode={setReportMode}
          reviewKey={`${snapshot?.session_id ?? ""}:${snapshot?.run_id ?? ""}`}
          exporting={exporting}
          onRefresh={() => void refreshWithFeedback()}
          refreshing={refreshing}
          refreshedAt={refreshedAt}
          truncated={truncated}
          materials={snapshot?.materials ?? []}
          gateReport={snapshot?.gate_report ?? null}
          gaps={snapshot?.gaps}
          conflicts={snapshot?.conflicts ?? []}
          onVerify={sourceId => void verifyMaterial(sourceId)}
          uploadFiles={files => void uploadContract(files)}
          uploading={uploading}
          uploadRejected={uploadRejected}
          maxFiles={bootstrap?.materials.max_files_per_upload ?? 20}
          failedStep={snapshot?.failed_step}
          onRetry={() => void retry()}
          canRetry={snapshot?.can_retry}
          retrying={busy && phase !== "running"}
        />
      )}

      {/* 结果页也要能换一份合同 */}
      {screen === "result" && (
        <details className="ct-more">
          <summary>换一份合同 / 清空重来</summary>
          <p className="ct-mini" style={{ marginTop: 10 }}>
            点下面会另开一份全新的空会话：当前这份合同和这次的结果都不再显示（旧的那份要等会话过期或服务重启才消失）。
          </p>
          <div className="ct-actions">
            <button type="button" className="ct-btn" disabled={resetting} onClick={() => void resetSession()}>
              {resetting ? "正在清空…" : "清空重来"}
            </button>
          </div>
        </details>
      )}
    </div>
  );
}
