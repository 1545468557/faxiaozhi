"use client";

/**
 * 运行状态机（阶段 3-1）：把后端 SSE 的 11 种事件归约成界面状态。
 *
 * 设计口径（手册 §9「状态不是一个转圈图标」）：
 * - `awaiting_checkpoint` / 追问 是**独立状态**，不能显示成"加载中"；
 * - 五种"没有结果"的来源要能区分（状态文案由后端给，前端只负责不混淆）；
 * - 断线不等于失败：把快照拉回来重建即可。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { describeError, subscribeEvents, type RunEvent } from "@/lib/api";

export type RunPhase = "idle" | "running" | "awaiting" | "done" | "error";

export type LogLine = { at: number; label: string; text: string; tone?: "info" | "warn" | "error" };

export type RunState = {
  phase: RunPhase;
  /** 后端阶段名（phase 事件） */
  currentPhase: string;
  phases: { name: string; index: number; total: number }[];
  logs: LogLine[];
  demoMode: boolean;
  warning: string;
  mcpAvailable: boolean | null;
  /** 门禁摘要 */
  gate: { accepted: number; rejected: number; coverage: number; gaps: string[]; guardHits: string[] } | null;
  /** 等待人工确认的类型 */
  awaiting: "sample_confirm" | "stance_confirm" | "clarify" | null;
  /** 咨询追问的最新一批问题（事件里就带着，界面不必等快照刷新） */
  clarify: { questions: string[]; roundsUsed: number; maxRounds: number; remaining: number } | null;
  /** 人工门的原始载荷（合同立场门要用：甲乙方名称 + 选项都来自后端） */
  checkpoint: { kind: string; payload: Record<string, unknown> } | null;
  /** 出现过的降级 / 失败 */
  degraded: { step: string; text: string; retryable: boolean }[];
  error: { code: string; message: string; retryable: boolean } | null;
  startedAt: number | null;
};

const EMPTY: RunState = {
  phase: "idle",
  currentPhase: "",
  phases: [],
  logs: [],
  demoMode: false,
  warning: "",
  mcpAvailable: null,
  gate: null,
  awaiting: null,
  clarify: null,
  checkpoint: null,
  degraded: [],
  error: null,
  startedAt: null,
};

const MAX_LOGS = 60;

/** 日志行工厂：避免对象字面量把 tone 推断成 string */
const line = (label: string, text: string, tone: LogLine["tone"] = "info"): LogLine => ({
  at: Date.now(),
  label,
  text,
  tone,
});

const str = (value: unknown, fallback = ""): string =>
  value === null || value === undefined ? fallback : String(value);

export type SettledPhase = "done" | "awaiting" | "error";

/**
 * @param options.onSettled 运行收尾（完成 / 等待人工确认 / 失败）时回调。
 *   由事件回调触发，**不在 effect 里做 setState**（避免级联渲染，且收尾动作语义上属于外部事件）。
 */
export function useRun(options: { onSettled?: (phase: SettledPhase) => void } = {}) {
  const [state, setState] = useState<RunState>(EMPTY);
  const stopRef = useRef<(() => void) | null>(null);
  const { onSettled } = options;

  const stop = useCallback(() => {
    stopRef.current?.();
    stopRef.current = null;
  }, []);

  useEffect(() => stop, [stop]);

  const push = useCallback((line: Omit<LogLine, "at">) => {
    setState(previous => ({ ...previous, logs: [...previous.logs, { ...line, at: Date.now() }].slice(-MAX_LOGS) }));
  }, []);

  const handleEvent = useCallback(
    (event: RunEvent) => {
      const { name, data } = event;
      setState(previous => {
        switch (name) {
          case "meta":
            return {
              ...previous,
              demoMode: Boolean(data.demo_mode),
              warning: str(data.warning),
              mcpAvailable: data.mcp_available === null ? null : Boolean(data.mcp_available),
            };
          case "phase":
            return {
              ...previous,
              currentPhase: str(data.name),
              phases: [
                ...previous.phases.filter(item => item.name !== str(data.name)),
                { name: str(data.name), index: Number(data.index ?? 0), total: Number(data.total ?? 0) },
              ],
            };
          case "progress":
            return {
              ...previous,
              logs: [...previous.logs, line(str(data.phase, "进度"), str(data.detail, ""))].slice(-MAX_LOGS),
            };
          case "tool":
            return {
              ...previous,
              logs: [
                ...previous.logs,
                line(
                  str(data.tool, "工具"),
                  `${str(data.status_text, str(data.status))}${
                    data.sources_returned ? ` · 返回 ${data.sources_returned} 条` : ""
                  }${data.attempts ? ` · 第 ${data.attempts} 次` : ""}`,
                  str(data.status) === "ok" ? "info" : "warn",
                ),
              ].slice(-MAX_LOGS),
            };
          case "gate":
            return {
              ...previous,
              gate: {
                accepted: Number(data.accepted ?? 0),
                rejected: Array.isArray(data.rejected) ? data.rejected.length : 0,
                coverage: Number(data.coverage ?? 0),
                gaps: Array.isArray(data.gaps) ? data.gaps.map(item => str(item)) : [],
                guardHits: Array.isArray(data.guard_hits) ? data.guard_hits.map(item => str(item)) : [],
              },
            };
          case "degrade":
            return {
              ...previous,
              degraded: [
                ...previous.degraded,
                {
                  step: str(data.step, str(data.tool, "步骤")),
                  text: str(data.status_text, str(data.error_kind, "降级")),
                  retryable: Boolean(data.retryable),
                },
              ],
            };
          case "retry":
            return {
              ...previous,
              logs: [
                ...previous.logs,
                line("重试", `${str(data.step, "步骤")} 第 ${str(data.attempt, "?")} / ${str(data.max_attempts, "?")} 次`, "warn"),
              ].slice(-MAX_LOGS),
            };
          case "checkpoint_reached":
            onSettled?.("awaiting");
            return {
              ...previous,
              phase: "awaiting",
              awaiting: str(data.kind) === "stance_confirm" ? "stance_confirm" : "sample_confirm",
              checkpoint: {
                kind: str(data.kind),
                payload: (data.payload ?? {}) as Record<string, unknown>,
              },
            };
          case "clarify":
            onSettled?.("awaiting");
            return {
              ...previous,
              phase: "awaiting",
              awaiting: "clarify",
              clarify: {
                questions: Array.isArray(data.questions) ? data.questions.map(item => str(item)) : [],
                roundsUsed: Number(data.rounds_used ?? 0),
                maxRounds: Number(data.max_rounds ?? 3),
                remaining: Number(data.remaining ?? 0),
              },
            };
          case "conflict":
            return {
              ...previous,
              logs: [
                ...previous.logs,
                line("来源冲突", str(data.status_text, "检测到来源冲突，需要人工裁决。"), "warn"),
              ].slice(-MAX_LOGS),
            };
          case "done":
            onSettled?.("done");
            return { ...previous, phase: "done", awaiting: null };
          case "error": {
            const error = (data.error ?? {}) as { code?: string; message?: string };
            const info = describeError(error.code, error.message);
            onSettled?.("error");
            return {
              ...previous,
              phase: "error",
              awaiting: null,
              error: { code: info.code, message: info.fallback, retryable: info.retryable },
            };
          }
          default:
            return previous;
        }
      });
    },
    [onSettled],
  );

  /** 订阅事件流并触发动作（先订阅后触发，避免漏掉首批事件） */
  const start = useCallback(
    (sid: string, trigger: () => Promise<unknown>) => {
      stop();
      setState({ ...EMPTY, phase: "running", startedAt: Date.now() });
      stopRef.current = subscribeEvents(sid, {
        onEvent: handleEvent,
        onClosed: reason => {
          setState(previous => {
            if (reason === "network" && previous.phase === "running") {
              onSettled?.("done");
              return {
                ...previous,
                phase: "done",
                logs: [...previous.logs, line("连接", "实时进度连接中断，已改用最新快照恢复。", "warn")].slice(-MAX_LOGS),
              };
            }
            if (reason === "error" && previous.phase === "running") {
              // 404 等：会话或运行已不可用，按可重试的中断处理（真实原因在 state 里）
              onSettled?.("error");
              return {
                ...previous,
                phase: "error",
                error: {
                  code: "stream_interrupted",
                  message: describeError("stream_interrupted").fallback,
                  retryable: true,
                },
              };
            }
            return previous;
          });
        },
      });
      void trigger().catch((cause: unknown) => {
        const info = describeError(
          cause && typeof cause === "object" && "code" in cause ? String((cause as { code: string }).code) : undefined,
          cause instanceof Error ? cause.message : undefined,
        );
        setState(previous => ({
          ...previous,
          phase: "error",
          error: { code: info.code, message: info.fallback, retryable: info.retryable },
        }));
        onSettled?.("error");
      });
    },
    [handleEvent, onSettled, stop],
  );

  /**
   * 人工门 / 补充检索等**不开启新运行**的动作：只把状态改回"进行中"，
   * **保持原来的事件流**。
   *
   * 为什么不能重开流：后端的事件队列是**每个会话一个**，而一个断开的旧流在服务端
   * 不一定立刻结束，它会继续把事件从队列里取走 → 新流只能拿到 `meta`，之后一片空白
   * （阶段 3-2 实测踩到：确认样本后界面一直停在"进行中"）。所以一次运行只订阅一次流。
   */
  const resume = useCallback(() => {
    setState(previous => ({ ...previous, phase: "running", awaiting: null, error: null }));
  }, []);

  /** 断线或刷新后，按服务端快照同步终态；不触发请求，也不重复 onSettled。 */
  const settle = useCallback((phase: SettledPhase) => {
    setState(previous => ({
      ...previous,
      phase,
      awaiting: phase === "awaiting" ? previous.awaiting : null,
      clarify: phase === "awaiting" ? previous.clarify : null,
      error: phase === "error" ? previous.error : null,
    }));
  }, []);

  /** 当前是否还挂着事件流（据此判断要不要靠轮询兜底） */
  const hasStream = useCallback(() => Boolean(stopRef.current), []);

  const fail = useCallback((code: string, message: string) => {
    const info = describeError(code, message);
    setState(previous => ({
      ...previous,
      phase: "error",
      error: { code: info.code, message: info.fallback, retryable: info.retryable },
    }));
  }, []);

  const reset = useCallback(() => {
    stop();
    setState(EMPTY);
  }, [stop]);

  return { state, start, stop, resume, settle, hasStream, reset, fail, push };
}
