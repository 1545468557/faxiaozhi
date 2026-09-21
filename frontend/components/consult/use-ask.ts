"use client";

/**
 * 法律问答的对话状态（v3）
 *
 * v3 后端不是"一次运行一段"，就是**一段一直聊下去的对话**：
 * - 没有追问轮数、没有 clarify 状态机、没有引用核验门禁；
 * - 问法上的"追问"由模型自己在回答里问出来，用户照常打字即可；
 * - 回答流式吐字（`delta` 事件），边收边显示。
 *
 * 因此这个 hook 比 v2 那份简单得多：它只管
 * ① 新建/复用会话 id（localStorage）；② 发一句话并接住 SSE；③ 刷新后把时间线拉回来。
 *
 * 两条不许松的口径：
 * - `error` 是"这次没做成"，**不等于"你的问题没有相关规定"**，文案照后端给的写；
 * - 检索回来的材料**未经核验**，界面不许写"已核验"。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  bootstrap as fetchBootstrap,
  chat as chatStream,
  timeline as fetchTimeline,
  type V3Bootstrap,
  type V3Event,
  type V3Source,
} from "@/lib/api/v3";

const SID_KEY = "faxiaozhi:v3:sid";

export type AnswerTurn = {
  id: string;
  role: "ai";
  kind: "answer";
  text: string;
  sources: V3Source[];
  streaming: boolean;
  /** 后端的 `is_stub`：界面必须标明这是离线示例，不能看着像真实回答 */
  stub: boolean;
  elapsedMs?: number;
  /** 客户端主动停止 / 连接断了 */
  interrupted?: "stopped" | "broken";
};

export type AskTurn =
  | { id: string; role: "user"; text: string }
  | { id: string; role: "ai"; kind: "note"; text: string }
  | { id: string; role: "ai"; kind: "error"; code: string; reason: string }
  | AnswerTurn;

export function useAsk() {
  const [turns, setTurns] = useState<AskTurn[]>([]);
  const [notice, setNotice] = useState("");
  const [running, setRunning] = useState(false);
  const [started, setStarted] = useState(false);
  const [stub, setStub] = useState(false);
  const [info, setInfo] = useState<V3Bootstrap | null>(null);
  const [restored, setRestored] = useState(false);

  const sidRef = useRef<string | null>(null);
  const stopRef = useRef<(() => void) | null>(null);
  const seq = useRef(0);
  /** 正在流式写入的那条 ai 轮次 id；为空表示还没开这条气泡 */
  const liveRef = useRef<string | null>(null);
  const busyRef = useRef(false);
  const booted = useRef(false);
  const stubRef = useRef(false);
  /** 本条消息检索到的材料：`sources` 事件可能先于第一个字到，先存着，等气泡建出来再挂上去 */
  const pending = useRef<V3Source[]>([]);

  const nextId = () => `t${++seq.current}`;

  const push = useCallback((turn: AskTurn) => setTurns(previous => [...previous, turn]), []);

  const updateAnswer = useCallback((id: string, patch: (turn: AnswerTurn) => AnswerTurn) => {
    setTurns(previous =>
      previous.map(turn => (turn.id === id && turn.role === "ai" && turn.kind === "answer" ? patch(turn) : turn)),
    );
  }, []);

  const readSid = () => {
    try {
      const url = new URL(window.location.href);
      const selected = url.searchParams.get("session");
      if (selected) {
        url.searchParams.delete("session");
        window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
      }
      if (selected === "new") { window.localStorage.removeItem(SID_KEY); return null; }
      return selected || window.localStorage.getItem(SID_KEY);
    } catch {
      return null;
    }
  };

  const writeSid = (sid: string | null) => {
    try {
      if (sid) window.localStorage.setItem(SID_KEY, sid);
      else window.localStorage.removeItem(SID_KEY);
    } catch {
      /* 隐私模式下写不进 localStorage：不影响本次对话，只是刷新后接不上 */
    }
  };

  // 首屏：能力状态 + 把本会话已有的对话拉回来
  useEffect(() => {
    if (booted.current) return;
    booted.current = true;
    let alive = true;

    void (async () => {
      try {
        const it = await fetchBootstrap();
        if (!alive) return;
        setInfo(it);
        stubRef.current = Boolean(it.model.is_stub);
        setStub(Boolean(it.model.is_stub));
      } catch {
        if (alive) {
          setNotice("连不上本机后端服务（127.0.0.1:8011）。请先启动 v3 后端，再刷新页面。");
        }
      }

      const sid = readSid();
      if (sid) {
        try {
          const data = await fetchTimeline(sid);
          if (!alive) return;
          sidRef.current = sid;
          writeSid(sid);
          if (data.messages.length) {
            setTurns(data.messages.map(message => toTurn(message, nextId)));
            setStarted(true);
          }
        } catch {
          // 会话不存在（换了库 / 被删了）：丢掉这个 id，下次发送会自动新建
          writeSid(null);
        }
      }
      if (alive) setRestored(true);
    })();

    return () => {
      alive = false;
    };
  }, []);

  // 卸载时把还在跑的流停掉（后端那边会跟着取消，不会留下半截回答）
  useEffect(() => () => stopRef.current?.(), []);

  /** 发一句话。同一会话连发就是多轮，不需要额外的"回答追问"动作 */
  const send = useCallback(
    (raw: string) => {
      const text = raw.trim();
      if (!text || busyRef.current) return;

      setStarted(true);
      setNotice("");
      push({ id: nextId(), role: "user", text });
      busyRef.current = true;
      setRunning(true);
      liveRef.current = null;
      pending.current = [];

      /** 懒建气泡：第一个 delta / sources 到了才出现，失败时不会留下空气泡 */
      const ensureAnswer = (): string => {
        if (liveRef.current) return liveRef.current;
        const id = nextId();
        liveRef.current = id;
        setTurns(previous => [
          ...previous,
          {
            id,
            role: "ai",
            kind: "answer",
            text: "",
            sources: pending.current,
            streaming: true,
            stub: stubRef.current,
          },
        ]);
        return id;
      };

      return new Promise<void>(resolve => {
        stopRef.current = chatStream(
          { session_id: sidRef.current ?? undefined, message: text },
          {
            onEvent: (event: V3Event) => {
              if (event.name === "meta") {
                sidRef.current = event.data.session_id;
                writeSid(event.data.session_id);
                stubRef.current = Boolean(event.data.is_stub);
                setStub(Boolean(event.data.is_stub));
                return;
              }

              if (event.name === "sources") {
                // 材料先到、字后到：只存起来，等气泡建出来时一起挂上（避免先冒一个空气泡）
                pending.current = event.data.items ?? [];
                return;
              }

              if (event.name === "note") {
                // 检索没成功之类：单独一条说明，不混进回答里
                push({ id: nextId(), role: "ai", kind: "note", text: event.data.text });
                return;
              }

              if (event.name === "delta") {
                const id = ensureAnswer();
                updateAnswer(id, turn => ({ ...turn, text: turn.text + event.data.text }));
                return;
              }

              if (event.name === "done") {
                const id = liveRef.current;
                if (id) {
                  updateAnswer(id, turn => ({ ...turn, streaming: false, elapsedMs: event.data.elapsed_ms }));
                }
                return;
              }

              // error：这次没做成，不等于没有相关规定 —— 如实说，并保留已收到的部分
              const id = liveRef.current;
              if (id) {
                updateAnswer(id, turn => ({ ...turn, streaming: false, interrupted: "broken" }));
              }
              push({ id: nextId(), role: "ai", kind: "error", code: event.data.code, reason: event.data.message });
            },
            onClosed: reason => {
              const id = liveRef.current;
              // 只有"主动停止"和"连接断了"才算中断。
              // 注意：正常收尾也会回调到这里（reason === "done"），别把它当成中断 —— 之前就误报过一次。
              if (id && (reason === "aborted" || reason === "network")) {
                // 主动停止 / 连接断开：这段话**没有存到会话里**，得说清楚，免得刷新后对不上
                updateAnswer(id, turn => ({
                  ...turn,
                  streaming: false,
                  interrupted: reason === "aborted" ? "stopped" : "broken",
                }));
              }
              liveRef.current = null;
              stopRef.current = null;
              busyRef.current = false;
              setRunning(false);
              resolve();
            },
          },
        );
      });
    },
    [push, updateAnswer],
  );

  const stop = useCallback(() => {
    stopRef.current?.();
  }, []);

  /** 换个问题：只是本地开一段新对话；原来那段仍在会话库里，可在「我的」里找回 */
  const reset = useCallback(() => {
    stopRef.current?.();
    stopRef.current = null;
    liveRef.current = null;
    busyRef.current = false;
    pending.current = [];
    sidRef.current = null;
    writeSid(null);
    setTurns([]);
    setStarted(false);
    setRunning(false);
    setNotice("");
  }, [stop]);

  // 一句话都还没吐出来时，用占位提示代替空气泡
  const thinking = running && !turns.some(turn => turn.role === "ai" && turn.kind === "answer" && turn.streaming && turn.text);

  return {
    turns,
    notice,
    started,
    running,
    thinking,
    restored,
    stub,
    info,
    disclosure: info?.disclosure ?? "以上内容由 AI 生成，仅供学习与研究参考，不构成正式法律意见。",
    retrieval: info?.retrieval ?? null,
    send,
    stop,
    reset,
  };
}

/** 把库里的一条消息还原成对话轮次（刷新 / 换页回来时用） */
function toTurn(
  message: { role: string; content: string; sources?: V3Source[]; id: string },
  nextId: () => string,
): AskTurn {
  if (message.role === "user") return { id: nextId(), role: "user", text: message.content };
  return {
    id: nextId(),
    role: "ai",
    kind: "answer",
    text: message.content,
    sources: message.sources ?? [],
    streaming: false,
    stub: false,
  };
}
