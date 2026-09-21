"use client";

/**
 * 法律问答 - 对话式界面（重新设计）
 *
 * 采用简洁的对话式布局：
 * - 初始状态：居中展示，大标题 + 输入框 + 快捷功能 + 场景卡片
 * - 对话状态：用户提问后切换为对话流模式
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Download, MessageSquare, RefreshCw, Send, Paperclip, Sparkles, FileText, Scale, Search } from "lucide-react";
import { SiteHeader } from "@/components/site-header";
import { BackendStatus, useBackend } from "@/components/backend-status";
import { RunMonitor } from "@/components/workspace/run-monitor";
import { EvidenceTabs } from "@/components/workspace/evidence-tabs";
import { SourceSheet } from "@/components/workspace/source-sheet";
import { ClarifyCard, ConsultAnswerBlocks, ConsultFailure } from "@/components/workspace/consult-output";
import { useRun } from "@/components/workspace/use-run";
import { useSessionState } from "@/components/workspace-session";
import { api, ApiError, describeError, downloadUrl, loadState } from "@/lib/api";
import { answerSections, assumptionNotice, clarificationHint, clarifyState, roundsText } from "@/lib/api/consult";
import { exportState } from "@/lib/api/common";
import type { SessionState, SourceBrief } from "@/lib/api/types";

const SUGGESTIONS = [
  "房东把房子卖了，新房东让我搬走，我该怎么办？",
  "公司拖欠工资，我该怎么办，需要准备什么？",
  "买到的东西有质量问题，商家不肯退怎么办？",
];

const QUICK_ACTIONS = [
  { icon: Sparkles, label: "深度分析", desc: "全面分析法律关系" },
  { icon: FileText, label: "常用场景", desc: "快速匹配常见场景" },
  { icon: Scale, label: "快速问答", desc: "简洁直接的回答" },
];

const CATEGORIES = [
  { icon: Search, label: "法律研究" },
  { icon: FileText, label: "案例检索" },
  { icon: Scale, label: "合同审查" },
  { icon: MessageSquare, label: "文书起草" },
  { icon: Sparkles, label: "法律咨询" },
];

const SCENARIOS = [
  {
    icon: FileText,
    title: "文书起草",
    example: "帮我起草一份民间借贷起诉状（本金10万、月息1%）。",
    color: "bg-blue-50 border-blue-200",
  },
  {
    icon: Scale,
    title: "案件分析",
    example: "请做接案评估：我方是供应商，合同金额 500 万，对方...",
    color: "bg-purple-50 border-purple-200",
  },
  {
    icon: Search,
    title: "证据分析",
    example: "承包人想主张建设工程价款优先受偿权，需要哪些证据，...",
    color: "bg-green-50 border-green-200",
  },
];

export function ConsultLive() {
  const [question, setQuestion] = useSessionState<string>("consult:question", "");
  const [facts, setFacts] = useSessionState<string>("consult:facts", "");
  const [snapshot, setSnapshot] = useState<SessionState | null>(null);
  const [notice, setNotice] = useState("");
  const [detail, setDetail] = useState<SourceBrief | null>(null);
  const [busy, setBusy] = useState(false);
  const [skipping, setSkipping] = useState(false);
  const submittingFacts = useRef(false);
  const submittedRound = useRef<number | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadRejected, setUploadRejected] = useState<{ filename: string; message?: string }[]>([]);
  const [exporting, setExporting] = useState(false);
  const [hasStarted, setHasStarted] = useState(false);
  const sidRef = useRef<string | null>(null);
  const pollTimer = useRef<number | null>(null);
  const { bootstrap } = useBackend();
  const inputRef = useRef<HTMLTextAreaElement>(null);

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
          const consult = next.consult ?? {};
          if (submittedRound.current !== null && consult.awaiting && (consult.rounds ?? 0) <= submittedRound.current) return;
          setSnapshot(next);
          const settled =
            Boolean(consult.awaiting) ||
            Boolean(consult.answer) ||
            Boolean(next.failed_step) ||
            next.export?.ready === true;
          if (settled) {
            submittedRound.current = null;
            setSkipping(false);
            setBusy(false);
            stopPolling();
          }
        })
        .catch(() => undefined);
      if (Date.now() - startedAt > 180000) stopPolling();
    }, 2500);
  }, [stopPolling]);

  useEffect(() => stopPolling, [stopPolling]);

  const run = useRun({
    onSettled: () => {
      submittedRound.current = null;
      setSkipping(false);
      setBusy(false);
      stopPolling();
      void refresh();
    },
  });
  const { phase } = run.state;
  const { settle } = run;

  useEffect(() => {
    if (busy || !snapshot) return;
    if (snapshot.failed_step) settle("error");
    else if (snapshot.consult?.awaiting) settle("awaiting");
    else if (snapshot.consult?.answer) settle("done");
  }, [snapshot, busy, settle]);

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const { sid, state } = await loadState("consult", {
          onRecreated: () => alive && setNotice("原会话已过期或服务已重启，已自动建立新会话。"),
        });
        if (!alive) return;
        sidRef.current = sid;
        setSnapshot(state);
        setQuestion(previous => previous || state.consult?.facts?.[0] || state.topic || "");
        if (state.run_id && state.status === "active" && !state.consult?.awaiting && !(state.consult?.answer) && !state.failed_step) {
          setBusy(true);
          pollUntilSettled();
        }
        // 如果已有对话历史，标记为已开始
        if (state.consult?.facts?.[0] || state.consult?.answer) {
          setHasStarted(true);
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
  }, [pollUntilSettled, setQuestion]);

  const ask = useCallback(async () => {
    if (busy || phase === "running") return;
    if (!question.trim()) {
      setNotice("请先说说你想问什么。");
      return;
    }
    setBusy(true);
    setNotice("");
    setDetail(null);
    setFacts("");
    setHasStarted(true);
    try {
      const { sid } = await loadState("consult", {
        onRecreated: () => setNotice("原会话已过期或服务已重启，已自动建立新会话。"),
      });
      sidRef.current = sid;
      run.start(sid, () => api.message(sid, question.trim()));
    } catch (cause) {
      setBusy(false);
      const info = describeError(
        cause instanceof ApiError ? cause.code : undefined,
        cause instanceof Error ? cause.message : undefined,
      );
      run.fail(info.code, info.fallback);
    }
  }, [busy, phase, question, run, setFacts]);

  const submitFacts = useCallback(async (skip = false) => {
    const sid = sidRef.current;
    if (!sid || busy || submittingFacts.current) return;
    if (!skip && !facts.trim()) {
      setNotice("请先填写补充说明，也可以跳过直接看分析。");
      return;
    }
    const currentRound = Math.max(snapshot?.consult?.rounds ?? 0, run.state.clarify?.roundsUsed ?? 0);
    submittingFacts.current = true;
    submittedRound.current = currentRound;
    setBusy(true);
    setSkipping(skip);
    setNotice("");
    run.resume();
    try {
      await api.consultAnswer(sid, facts.trim(), { skipClarification: skip, expectedRound: currentRound });
      setFacts("");
      setSnapshot(previous => {
        if (!previous || (previous.consult?.rounds ?? 0) > currentRound || previous.consult?.answer) return previous;
        return { ...previous, consult: { ...previous.consult, awaiting: false } };
      });
      pollUntilSettled();
    } catch (cause) {
      submittedRound.current = null;
      setBusy(false);
      setSkipping(false);
      stopPolling();
      run.settle("awaiting");
      setNotice(
        describeError(
          cause instanceof ApiError ? cause.code : undefined,
          cause instanceof Error ? cause.message : undefined,
        ).fallback,
      );
      void refresh();
    } finally {
      submittingFacts.current = false;
    }
  }, [busy, facts, pollUntilSettled, refresh, run, setFacts, snapshot, stopPolling]);

  const retry = useCallback(async () => {
    const sid = sidRef.current;
    if (!sid || busy || !snapshot?.can_retry) return;
    setBusy(true);
    setNotice("");
    run.start(sid, async () => {
      await api.retry(sid);
      setSnapshot(previous => previous && { ...previous, failed_step: null, failed_reason: null, can_retry: false });
      pollUntilSettled();
    });
  }, [busy, pollUntilSettled, run, snapshot?.can_retry]);

  const doExport = useCallback(async () => {
    const sid = sidRef.current;
    if (!sid) return;
    setExporting(true);
    setNotice("");
    try {
      const result = await api.consultExport(sid);
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
        if (result.materials?.length) setNotice(`已收录 ${result.materials.length} 个文件；请点「本人已核验」后才会被引用。`);
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

  const consult = snapshot?.consult;
  const eventClarify = run.state.clarify;
  const clarifyRounds = {
    ...consult,
    rounds: Math.max(consult?.rounds ?? 0, eventClarify?.roundsUsed ?? 0),
    max_rounds: consult?.max_rounds ?? eventClarify?.maxRounds ?? 3,
  };
  const clarify = clarifyState(clarifyRounds, run.state.awaiting === "clarify");
  const eventIsNewer = (eventClarify?.roundsUsed ?? 0) > (consult?.rounds ?? 0);
  const clarifyQuestions = (eventIsNewer
    ? eventClarify?.questions ?? []
    : consult?.issue?.clarify_questions?.length ? consult.issue.clarify_questions : eventClarify?.questions ?? [])
    .filter(item => item.trim()).slice(0, 1);
  const normalizeQuestion = (value: string) => value.replace(/[\s\p{P}\p{S}]/gu, "");
  const currentQuestions = new Set([question, consult?.facts?.[0] ?? ""].map(normalizeQuestion));
  const suggestions = SUGGESTIONS.filter(item => !currentQuestions.has(normalizeQuestion(item)));
  const sections = answerSections(consult?.answer, consult?.passed);
  const assumptions = assumptionNotice(clarifyRounds);
  const exp = exportState(snapshot?.export);
  const running = phase === "running";
  const failed = Boolean(snapshot?.failed_step) || phase === "error";
  const rawFailureReason = snapshot?.failed_reason || run.state.error?.message || "";
  const failureReason = /结构校验|格式校验/.test(rawFailureReason)
    ? "模型返回的解答格式不完整，暂时无法显示。"
    : rawFailureReason;
  const retrying = busy && (running || failed);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void ask();
    }
  };

  const handleScenarioClick = (example: string) => {
    setQuestion(example);
    inputRef.current?.focus();
  };

  // 初始状态 - 简洁对话式界面
  if (!hasStarted && !busy && !consult?.answer && !clarify.awaiting) {
    return (
      <div className="legal-app workspace-page">
        <SiteHeader active="consult" />
        <main className="consult-hero">
          <div className="consult-hero-content">
            <h1 className="consult-hero-title">法律难题，问问小智</h1>
            <p className="consult-hero-subtitle">说说你遇到的问题，我来帮你分析</p>

            <div className="consult-input-wrapper">
              <textarea
                ref={inputRef}
                value={question}
                onChange={event => setQuestion(event.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="输入你的法律问题，我会自动匹配相关法律..."
                className="consult-main-input"
                rows={3}
              />
              <div className="consult-input-actions">
                <div className="consult-input-tools">
                  <button type="button" className="tool-btn" title="上传文件">
                    <Paperclip size={18} />
                  </button>
                  {QUICK_ACTIONS.map(action => (
                    <button key={action.label} type="button" className="tool-btn" title={action.desc}>
                      <action.icon size={18} />
                      <span>{action.label}</span>
                    </button>
                  ))}
                </div>
                <button
                  type="button"
                  className="send-btn"
                  onClick={() => void ask()}
                  disabled={!question.trim()}
                  title="发送问题"
                >
                  <Send size={20} />
                </button>
              </div>
            </div>

            <div className="consult-categories">
              {CATEGORIES.map(cat => (
                <button key={cat.label} type="button" className="category-btn">
                  <cat.icon size={16} />
                  <span>{cat.label}</span>
                </button>
              ))}
            </div>

            <div className="consult-scenarios">
              <h3 className="scenarios-title">你可以按照以下场景提问</h3>
              <div className="scenarios-grid">
                {SCENARIOS.map(scenario => (
                  <button
                    key={scenario.title}
                    type="button"
                    className={`scenario-card ${scenario.color}`}
                    onClick={() => handleScenarioClick(scenario.example)}
                  >
                    <div className="scenario-header">
                      <scenario.icon size={20} />
                      <span className="scenario-title">{scenario.title}</span>
                    </div>
                    <p className="scenario-example">{scenario.example}</p>
                  </button>
                ))}
              </div>
            </div>
          </div>
        </main>
      </div>
    );
  }

  // 对话状态 - 保留原有功能但简化布局
  return (
    <div className="legal-app workspace-page consult-page">
      <SiteHeader active="consult" />
      <main className="workbench" id="main-content">
        <div className="workbench-heading">
          <div>
            <Link className="back" href="/">
              首页 /
            </Link>
            <h1>
              法律问答
              <span>说说遇到的事，一起理清问题</span>
            </h1>
          </div>
        </div>

        <p className="consult-purpose">
          <strong>AI 辅助解答</strong>
          回答附引用依据和核验状态，方便查看原文；重要事项请结合实际情况复核。
        </p>

        <BackendStatus className="mb-4" />

        {notice && (
          <p className="fzx-note" data-tone="warn" style={{ marginTop: 12 }} role="status">
            {notice}
          </p>
        )}

        <div className="workspace-body">
          <aside className="input-panel">
            <div className="section-label">
              <span>01</span>提问
            </div>
            <label className="field-label" htmlFor="question">
              你想问什么？
            </label>
            <textarea
              id="question"
              value={question}
              onChange={event => setQuestion(event.target.value)}
              placeholder="用自己的话说说发生了什么、你有什么疑问。不用法律术语，也不用一次说全…"
              aria-describedby="consult-input-hint"
              disabled={running}
            />
            <button className="primary full" type="button" onClick={() => void ask()} disabled={busy || running || !question.trim()}>
              <MessageSquare size={17} aria-hidden="true" />
              {running ? "正在回答…" : "发送问题"}
            </button>
            <p className="muted-note" id="consult-input-hint">
              先说你知道的就好。需要了解更多情况时，我会接着问你。
            </p>
            {suggestions.length > 0 && <details className="consult-examples" open>
              <summary>试试这些问题</summary>
              <div className="fzx-suggestions">
                {suggestions.map(item => (
                  <button key={item} type="button" onClick={() => setQuestion(item)} disabled={running}>
                    {item}
                  </button>
                ))}
              </div>
            </details>}
          </aside>

          <article className="result-panel">
            <RunMonitor run={run.state.error
              ? { ...run.state, error: { ...run.state.error, message: failureReason } }
              : run.state} />

            {clarify.awaiting && !failed && (
              <ClarifyCard
                key={`${clarifyRounds.rounds}:${clarifyQuestions[0] ?? ""}`}
                roundsText={roundsText(clarifyRounds)}
                progressHint={clarificationHint(clarifyRounds)}
                questions={clarifyQuestions}
                facts={facts}
                onFactsChange={setFacts}
                onSubmit={() => void submitFacts()}
                onSkip={() => void submitFacts(true)}
                skipping={skipping}
                busy={busy}
              />
            )}

            {assumptions && (
              <p className="fzx-note" data-tone="warn">
                {assumptions}
              </p>
            )}

            <div className="result-toolbar" style={{ marginTop: 16 }}>
              <div className="section-label">
                <span>02</span>回答
              </div>
              <span className="light-badge">
                {busy || running ? "正在回答" : failed ? "回答未完成" : sections.conclusions.length ? `${sections.conclusions.length} 条结论` : sections.hasAnswer ? "无通过核验的结论" : clarify.awaiting ? "待补充信息" : "等待提问"}
              </span>
            </div>

            {failed ? (
              <ConsultFailure
                reason={failureReason}
                canRetry={Boolean(snapshot?.can_retry)}
                retrying={retrying}
                onRetry={() => void retry()}
              />
            ) : busy || running ? (
              <div className="fzx-empty" role="status">正在根据你提供的情况查找依据、整理回答，请稍候。</div>
            ) : clarify.awaiting ? (
              <div className="fzx-empty">补充上方信息后继续解答，也可以跳过直接查看。</div>
            ) : <ConsultAnswerBlocks
              answer={consult?.answer}
              passed={consult?.passed}
              sources={snapshot?.sources ?? []}
              onOpenSource={sourceId =>
                setDetail((snapshot?.sources ?? []).find(item => item.source_id === sourceId) ?? null)
              }
            />}

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
              onRoleChange={(sourceId, role) => void changeRole(sourceId, role)}
              onResolveConflict={() => undefined}
              onUploadFiles={files => void uploadFiles(files)}
              uploading={uploading}
              uploadRejected={uploadRejected}
              maxFiles={bootstrap?.materials.max_files_per_upload ?? 20}
            />

            <div className="fzx-monitor" style={{ marginTop: 14 }} aria-label="导出">
              <div className="fzx-monitor-head">
                <h3>保存回答（Word）</h3>
                <div className="fzx-actions" style={{ margin: 0 }}>
                  <button type="button" className="primary" onClick={() => void doExport()} disabled={exp.disabled || exporting}>
                    <Download size={16} aria-hidden="true" /> {exporting ? "正在导出…" : "导出回答"}
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
                <p className="fzx-note">回答已完成引用核验，可以导出保存。</p>
              )}
            </div>
          </article>
        </div>
      </main>

      <SourceSheet source={detail} onClose={() => setDetail(null)} />
    </div>
  );
}
