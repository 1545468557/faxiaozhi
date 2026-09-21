"use client";

/**
 * 运行监视器（阶段 3-1）：阶段进度、工具调用、门禁摘要、降级与重试。
 * 用的是**后端真实事件**，不是本地模拟进度。
 */

import type { RunState } from "@/components/workspace/use-run";

export const PHASE_LABELS: Record<string, string> = {
  intake: "接收议题",
  retrieve: "检索候选",
  pool: "汇总候选",
  confirm: "等待确认样本",
  extract: "逐案提炼",
  matrix: "横向对比",
  synthesis: "综合结论",
  verify: "引用核验",
  assemble: "成稿",
  export: "导出",
  clarify: "补充事实",
  parse: "解析合同",
  locate: "条款定位",
  risk: "风险分级",
  report: "审查报告",
  done: "完成",
};

const label = (name: string) => PHASE_LABELS[name] ?? name;

const STATUS_TEXT: Record<RunState["phase"], { text: string; tone: "running" | "await" | "done" | "error" } | null> = {
  idle: null,
  running: { text: "进行中", tone: "running" },
  awaiting: { text: "等待你确认", tone: "await" },
  done: { text: "已完成", tone: "done" },
  error: { text: "未完成", tone: "error" },
};

export function RunMonitor({
  run,
  canRetry,
  onRetry,
  retrying,
}: {
  run: RunState;
  canRetry?: boolean;
  onRetry?: () => void;
  retrying?: boolean;
}) {
  if (run.phase === "idle" && !run.logs.length) return null;

  const status = STATUS_TEXT[run.phase];
  const total = run.phases.at(-1)?.total ?? 0;
  const index = run.phases.at(-1)?.index ?? 0;

  return (
    <section className="fzx-monitor" aria-label="处理进度">
      <div className="fzx-monitor-head">
        <h3>处理进度</h3>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {run.demoMode && <span className="fzx-pill" data-tone="error">离线模式</span>}
          {status && (
            <span className="fzx-pill" data-tone={status.tone}>
              {status.text}
              {run.phase === "running" && total ? ` · ${index}/${total}` : ""}
            </span>
          )}
        </div>
      </div>

      {run.warning && <p className="fzx-note" data-tone="warn">{run.warning}</p>}

      {run.phases.length > 0 && (
        <ul className="fzx-steps">
          {run.phases.map((item, position) => {
            const state = position === run.phases.length - 1 ? "active" : "done";
            return (
              <li key={`${item.name}-${position}`} data-state={state}>
                {label(item.name)}
              </li>
            );
          })}
        </ul>
      )}

      {run.logs.length > 0 && (
        <details className="fzx-processing-records">
          <summary>查看处理记录</summary>
          <ul className="fzx-log">
            {run.logs.slice(-12).map((line, position) => (
              <li key={`${line.at}-${position}`} data-tone={line.tone ?? "info"}>
                <span>{line.label}</span>
                <span>{line.text}</span>
              </li>
            ))}
          </ul>
        </details>
      )}

      {run.gate && (
        <p className="fzx-note">
          引用核验：通过 {run.gate.accepted} 条 · 拦截 {run.gate.rejected} 条 · 覆盖率{" "}
          {Math.round(run.gate.coverage * 100)}%
          {run.gate.gaps.length > 0 && ` · 依据缺口 ${run.gate.gaps.length} 处`}
          {(run.gate.rejected > 0 || run.gate.guardHits.length > 0) &&
            "（被拦下的引用不作为结论展示，已归入依据缺口）"}
        </p>
      )}

      {run.degraded.length > 0 && (
        <div className="fzx-note" data-tone="warn">
          <strong>降级记录</strong>
          <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
            {run.degraded.map((item, position) => (
              <li key={`${item.step}-${position}`}>
                {label(item.step)}：{item.text}
                {item.retryable ? "（可重试）" : ""}
              </li>
            ))}
          </ul>
        </div>
      )}

      {run.error && (
        <p className="fzx-note" data-tone="error" role="alert">
          {run.error.message}
          {/* 后端文案已含「不等于…」时不重复追加，避免两种口径并存 */}
          {run.error.code === "mcp_unavailable" && !/不等于/.test(run.error.message) && " 这不等于「没有相关规定」。"}
        </p>
      )}

      {(onRetry || run.phase === "running") && (
        <div className="fzx-actions">
          {onRetry && run.phase === "error" && canRetry && (
            <button type="button" className="primary" onClick={onRetry} disabled={retrying}>
              {retrying ? "重试中…" : "重试未完成的步骤"}
            </button>
          )}
        </div>
      )}
    </section>
  );
}

export function CandidateList({
  items,
  onOpen,
}: {
  items: {
    source_id: string;
    identifier: string;
    title: string;
    court: string;
    decided_on: string;
    origin_text: string;
    status: string;
    user_verified: boolean;
    corroboration: string;
    supplement: boolean;
  }[];
  onOpen?: (sourceId: string) => void;
}) {
  if (!items.length) {
    return (
      <div className="fzx-empty">
        <p>还没有候选材料。</p>
        <p style={{ fontSize: 13 }}>检索完成后，可在此查看候选材料。若未找到匹配材料，可调整议题或检索范围后重试。</p>
      </div>
    );
  }

  return (
    <ul className="fzx-candidates">
      {items.map(item => (
        <li key={item.source_id} className="fzx-candidate">
          <h4>{item.title || item.identifier || "未命名材料"}</h4>
          <div className="fzx-candidate-meta">
            <span data-kind={item.origin_text.includes("用户材料") ? "user" : undefined}>{item.origin_text}</span>
            {item.identifier && <span>{item.identifier}</span>}
            {item.court && <span>{item.court}</span>}
            {item.decided_on && <span>{item.decided_on}</span>}
            {item.supplement && <span>补充检索</span>}
            {item.status && item.status !== "ok" && <span data-kind="status">{item.status}</span>}
            {item.user_verified ? <span>用户已核验</span> : null}
          </div>
          {onOpen && (
            <div>
              <button type="button" className="fzx-text-button" onClick={() => onOpen(item.source_id)}>
                查看来源与核验状态
              </button>
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}

/**
 * 从**服务端快照**渲染失败与重试（阶段 3-4 实测补充）。
 *
 * 为什么需要它：SSE 是主通道，但刷新页面、换页返回、断线时事件可能收不到；
 * 此时界面必须仍能从快照里看出"这次没跑完、原因是什么、能不能重试"，
 * 而不是一片空白（否则用户会以为什么都没发生）。
 */
export function SnapshotFailure({
  failedStep,
  failedReason,
  canRetry,
  onRetry,
  retrying,
}: {
  failedStep?: string | null;
  failedReason?: string | null;
  canRetry?: boolean;
  onRetry?: () => void;
  retrying?: boolean;
}) {
  if (!failedStep) return null;
  const reason = failedReason ?? "";
  const mcpWording = /接口调用失败|不等于/.test(reason);
  return (
    <div className="fzx-note" data-tone="error" role="alert">
      <strong>本次未完成：{failedStep}</strong>
      {reason ? <p style={{ margin: "6px 0 0" }}>{reason}</p> : null}
      {mcpWording && <p style={{ margin: "6px 0 0" }}>注意：检索接口失败**不等于**「没有相关规定」。</p>}
      {canRetry && onRetry && (
        <div className="fzx-actions" style={{ marginTop: 10 }}>
          <button type="button" className="primary" onClick={onRetry} disabled={retrying}>
            {retrying ? "重试中…" : "重试未完成的步骤"}
          </button>
        </div>
      )}
    </div>
  );
}
