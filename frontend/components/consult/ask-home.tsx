"use client";

/**
 * 法律问答（首页）
 *
 * 空态：居中标题 + 大输入框 + 快捷条 + 场景卡片 + 下面三个入口。
 * 发出第一句后进入对话态：你说的话在右、小智在左；追问是模型自己在回答里问出来的，
 * 用户照常打字即可 —— 没有"回答追问"这种单独动作。
 *
 * 依据不再单列一个引用区：正文里出现的法规名/案名**直接就是链接**，点开看原文。
 * 正文没链上的材料在气泡末尾以「参考材料」列出，避免检索回来的东西被藏起来。
 *
 * 口径：场景卡片只把示例问题填进输入框，不自动发送 —— 避免误触发一次计费检索。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  Sparkles,
  Send,
  Square,
} from "lucide-react";
import { AskEntrance } from "@/components/consult/ask-entrance";
import { useSessionState } from "@/components/workspace-session";
import { splitByCitations } from "@/components/consult/citations";
import { SourceSheet } from "@/components/consult/source-sheet";
import { useAsk, type AnswerTurn, type AskTurn } from "@/components/consult/use-ask";
import type { V3Source } from "@/lib/api/v3";

// 2026-09-20 产品经理指示：类案检索与合同审查已经放进左侧导航，
// **首页不再重复放入口**（原来那两张卡片已删除）；首页只留问答框与场景示例。

const SCENES = [
  { title: "租房", text: "押金不退、房东提前收房", ask: "房东把房子卖了，新房东让我搬走，我该怎么办？" },
  { title: "劳动", text: "拖欠工资、违法解除", ask: "公司拖欠我三个月工资，我该怎么办，需要准备什么？" },
  { title: "消费", text: "退货纠纷、预付卡", ask: "买到的东西有质量问题，商家不肯退怎么办？" },
  { title: "合同", text: "条款风险、违约责任", ask: "签合同前有哪些条款要特别留意，哪些属于明显不利？" },
];

export function AskHome() {
  const [draft, setDraft] = useSessionState<string>("consult:draft", "");
  const [detail, setDetail] = useState<V3Source | null>(null);
  const chat = useAsk();
  const input = useRef<HTMLTextAreaElement>(null);
  const scenes = useRef<HTMLDivElement>(null);

  // 流式回答时把光标留在输入框，用户想接着说不用自己去点
  useEffect(() => {
    if (chat.running) input.current?.focus();
  }, [chat.running]);

  const submit = useCallback(() => {
    const text = draft.trim();
    if (!text || chat.running) return;
    setDraft("");
    void chat.send(text);
  }, [chat, draft, setDraft]);

  const placeholder = chat.started
    ? "继续说，或者接着问下一个问题…"
    : "说说发生了什么…";

  const composer = (
    <div className="v2-composer">
      <textarea
        ref={input}
        value={draft}
        onChange={event => setDraft(event.target.value)}
        placeholder={placeholder}
        rows={2}
        aria-label="你的问题"
        onKeyDown={event => {
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault();
            submit();
          }
        }}
      />
      <div className="v2-composer-row">
        <span className="v2-composer-hint">
          {chat.running ? "正在生成，可以随时停止" : "Enter 发送，Shift + Enter 换行"}
        </span>
        {chat.running ? (
          <button className="v2-send" type="button" onClick={() => chat.stop()}>
            <Square size={12} aria-hidden="true" />
            停止
          </button>
        ) : (
          <button className="v2-send" type="button" onClick={submit} disabled={!draft.trim()}>
            <Send size={15} aria-hidden="true" />
            发送
          </button>
        )}
      </div>
    </div>
  );

  return (
    <>
      {chat.started ? (
        <div className="v2-page">
          <div className="v2-thread">
            {chat.stub && (
              <p className="v2-alert" role="status">
                <strong>当前是离线示例模式</strong>
                后端跑的是占位模型，回答不是真实推理结果，也<b>没有</b>检索法规库。要看真实回答请让后端以正常模式启动。
              </p>
            )}

            {chat.turns.map(turn =>
              // 气泡等第一个字到了才出现，避免先冒一个空的
              turn.role === "ai" && turn.kind === "answer" && turn.streaming && !turn.text ? null : (
                <Turn key={turn.id} turn={turn} onOpenSource={setDetail} />
              ),
            )}

            {chat.thinking && (
              <div className="v2-turn" data-role="ai">
                <span className="v2-who">小智</span>
                <div className="v2-bubble" role="status" aria-live="polite">
                  <span className="v2-thinking">
                    <i /> <i /> <i />
                    正在查找依据、组织说法…
                  </span>
                </div>
              </div>
            )}
          </div>

          {chat.notice && (
            <p className="v2-alert" role="status" style={{ maxWidth: 780, margin: "16px auto 0" }}>
              {chat.notice}
            </p>
          )}

          <div className="v2-turn" data-role="ai" style={{ maxWidth: 780, margin: "18px auto 0" }}>
            <div className="v2-ask-actions">
              <button
                className="v2-ghost"
                type="button"
                onClick={() => {
                  chat.reset();
                  setDraft("");
                }}
              >
                换个问题
              </button>
              <span className="v2-note" style={{ alignSelf: "center" }}>
                换问题会开一段新对话，刚才那段可以在「我的」里找回。
              </span>
            </div>
          </div>

          <div className="v2-dock">
            <div className="v2-dock-inner">
              {composer}
              <p className="v2-note" style={{ marginTop: 10 }}>
                {chat.disclosure}
              </p>
            </div>
          </div>
        </div>
      ) : (
        <div className="v2-home">
          <AskEntrance><div className="ask-star"><Sparkles size={36} strokeWidth={1.5} aria-hidden="true" /></div><h1>有什么法律问题，聊聊看</h1><p>说说你遇到的事，不用准备专业术语。</p></AskEntrance>

            {chat.stub && (
              <p className="v2-alert" role="status">
                <strong>当前是离线示例模式</strong>
                后端跑的是占位模型，回答不是真实推理结果，也<b>没有</b>检索法规库。要看真实回答请让后端以正常模式启动。
              </p>
            )}

          {composer}

          {chat.notice && (
            <p className="v2-alert" role="status" style={{ marginBottom: 18 }}>
              {chat.notice}
            </p>
          )}

          <div className="v2-section-label" ref={scenes} id="scenes">
            按场景提问
          </div>
          <div className="v2-scenes">
            {SCENES.map(scene => (
              <button
                key={scene.title}
                className="v2-scene"
                type="button"
                onClick={() => {
                  setDraft(scene.ask);
                  input.current?.focus();
                }}
              >
                <strong>{scene.title}</strong>
                <span>{scene.text}</span>
                <em>
                  问一句 <ArrowRight size={12} aria-hidden="true" />
                </em>
              </button>
            ))}
          </div>

          <p className="v2-note" style={{ marginTop: 26 }}>
            {chat.disclosure}
          </p>
        </div>
      )}

      <SourceSheet source={detail} onClose={() => setDetail(null)} />
    </>
  );
}

function Turn({ turn, onOpenSource }: { turn: AskTurn; onOpenSource: (source: V3Source) => void }) {
  if (turn.role === "user") {
    return (
      <div className="v2-turn" data-role="user">
        <span className="v2-who">你</span>
        <div className="v2-bubble">{turn.text}</div>
      </div>
    );
  }

  if (turn.kind === "note") {
    return (
      <div className="v2-turn" data-role="ai">
        <span className="v2-who">小智</span>
        <div className="v2-note-box">{turn.text}</div>
      </div>
    );
  }

  if (turn.kind === "error") {
    return (
      <div className="v2-turn" data-role="ai">
        <span className="v2-who">小智</span>
        <div className="v2-alert" role="alert">
          <strong>这次没有生成回答</strong>
          {turn.reason || "本次处理未完成，请稍后重试。"}
          <br />
          这是处理失败，<b>不代表你的问题没有相关法律依据</b>。可以直接再发一次。
        </div>
      </div>
    );
  }

  return (
    <div className="v2-turn" data-role="ai">
      <span className="v2-who">小智</span>
      <div className="v2-bubble">
        <Answer turn={turn} onOpenSource={onOpenSource} />
      </div>
    </div>
  );
}

function Answer({ turn, onOpenSource }: { turn: AnswerTurn; onOpenSource: (source: V3Source) => void }) {
  const parts = splitByCitations(turn.text, turn.sources);
  const linked = new Set(parts.map(part => part.index).filter((index): index is number => index !== undefined));
  const rest = turn.sources.map((source, index) => ({ source, index })).filter(item => !linked.has(item.index));

  return (
    <>
      <div className="v3-prose">
        {parts.map((part, index) =>
          part.index === undefined ? (
            <span key={index}>{part.text}</span>
          ) : (
            <button
              key={index}
              className="v3-cite"
              type="button"
              title="点击查看检索到的材料原文"
              onClick={() => onOpenSource(turn.sources[part.index as number])}
            >
              {part.text}
            </button>
          ),
        )}
        {turn.streaming && <span className="v3-caret" aria-hidden="true" />}
      </div>

      {rest.length > 0 && (
        <div className="v3-refs">
          <span className="v2-note">参考材料（未在正文中点名）：</span>
          {rest.map(item => (
            <button
              key={item.index}
              className="v2-cite"
              type="button"
              onClick={() => onOpenSource(item.source)}
            >
              <b>{item.source.identifier || item.source.title || "来源"}</b>
              <span>{item.source.kind === "case" ? "判例" : "法规"}</span>
            </button>
          ))}
        </div>
      )}

      <div className="v3-foot">
        {turn.stub && <span className="v3-badge">离线示例</span>}
        {turn.sources.length > 0 && <span className="v2-note">检索到 {turn.sources.length} 条材料，未逐条核验</span>}
        {turn.elapsedMs ? <span className="v2-note">{(turn.elapsedMs / 1000).toFixed(1)} 秒</span> : null}
        {turn.interrupted === "stopped" && <span className="v2-note">已停止生成，这段话没有存进会话。</span>}
        {turn.interrupted === "broken" && (
          <span className="v2-note">生成中断了，这段回答可能不完整，刷新后只会显示已保存的部分。</span>
        )}
      </div>
    </>
  );
}
