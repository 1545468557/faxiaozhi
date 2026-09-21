/**
 * SSE 客户端（阶段 3-1）
 *
 * 后端：`GET /api/session/{sid}/events`（经 BFF 透传）。
 * - 首帧必是 `meta`；`done` / `error` 是收尾事件，收到即关流；
 * - `: ping` 心跳行要忽略（后端 15 秒一次，防代理掐断）；
 * - 断线不算失败：调用方拿 `GET state` 全量快照重建即可（手册 §10.4 / §11.4）。
 *
 * 不用原生 EventSource：它不能手动关流、不能带自定义头，也不便统一错误处理。
 */

import { BFF } from "./client";
import type { EventName, RunEvent } from "./types";

const KNOWN: EventName[] = [
  "meta",
  "phase",
  "progress",
  "tool",
  "gate",
  "degrade",
  "checkpoint_reached",
  "clarify",
  "conflict",
  "retry",
  "done",
  "error",
];

function isKnown(name: string): name is EventName {
  return (KNOWN as string[]).includes(name);
}

export type StreamHandlers = {
  onEvent: (event: RunEvent) => void;
  onClosed?: (reason: "done" | "error" | "network" | "aborted") => void;
};

/**
 * 订阅会话事件流。返回一个「停止」函数（组件卸载时必须调用）。
 */
export function subscribeEvents(sid: string, handlers: StreamHandlers): () => void {
  const controller = new AbortController();
  let finished = false;

  const finish = (reason: "done" | "error" | "network" | "aborted") => {
    if (finished) return;
    finished = true;
    handlers.onClosed?.(reason);
  };

  (async () => {
    let response: Response;
    try {
      response = await fetch(`${BFF}/session/${encodeURIComponent(sid)}/events`, {
        headers: { accept: "text/event-stream" },
        cache: "no-store",
        signal: controller.signal,
      });
    } catch {
      finish(controller.signal.aborted ? "aborted" : "network");
      return;
    }

    if (!response.ok || !response.body) {
      finish(response.status === 404 ? "error" : "network");
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        let boundary = buffer.indexOf("\n\n");
        while (boundary !== -1) {
          const chunk = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          boundary = buffer.indexOf("\n\n");

          // 心跳与注释行直接忽略
          if (!chunk || chunk.startsWith(":")) continue;

          let name = "message";
          const dataLines: string[] = [];
          for (const line of chunk.split("\n")) {
            if (line.startsWith("event:")) name = line.slice(6).trim();
            else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
          }
          if (!dataLines.length) continue;

          let data: Record<string, unknown> = {};
          try {
            data = JSON.parse(dataLines.join("\n")) as Record<string, unknown>;
          } catch {
            data = { raw: dataLines.join("\n") };
          }

          if (!isKnown(name)) continue;
          const event: RunEvent = { name, data };
          handlers.onEvent(event);
          if (name === "done" || name === "error") {
            await reader.cancel().catch(() => undefined);
            finish(name);
            return;
          }
        }
      }
      finish("network");
    } catch {
      finish(controller.signal.aborted ? "aborted" : "network");
    }
  })();

  return () => {
    controller.abort();
    finish("aborted");
  };
}
