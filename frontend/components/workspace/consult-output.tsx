"use client";

/**
 * 咨询的两块展示组件（阶段 3-3）
 * 抽出来是为了能用 DOM 断言直接锁住口径：追问卡（轮数）+ 四块解答（只展示通过核验的结论）。
 */

import type { ConsultAnswerSnapshot, SourceBrief } from "@/lib/api/types";
import { answerSections } from "@/lib/api/consult";
import { useId, useRef, useState } from "react";

/** 服务失败与法律依据不足是不同状态；失败时保留同会话重试入口。 */
export function ConsultFailure({
  reason,
  canRetry,
  retrying,
  onRetry,
}: {
  reason: string;
  canRetry: boolean;
  retrying: boolean;
  onRetry: () => void;
}) {
  return (
    <section className="fzx-note" data-tone="error" role="alert" aria-label="回答未完成">
      <strong>回答暂未生成</strong>
      <p>{reason || "本次处理未完成，请稍后重试。"}</p>
      <p>这是处理失败，不代表你的问题没有相关法律依据。</p>
      {canRetry ? (
        <>
          <p>本次会话中已提交的问题、补充事实和已取得的依据会继续使用，无需重新回答。</p>
          <div className="fzx-actions">
            <button className="primary" type="button" onClick={onRetry} disabled={retrying}>
              {retrying ? "正在重新生成…" : "重试回答"}
            </button>
          </div>
        </>
      ) : (
        <p>当前暂不能继续重试，请查看以上原因；服务恢复后可重新提问。</p>
      )}
    </section>
  );
}

export function ClarifyCard({
  roundsText,
  questions,
  facts,
  onFactsChange,
  onSubmit,
  busy,
  onSkip,
  skipping = false,
  progressHint,
}: {
  roundsText: string;
  questions: string[];
  facts: string;
  onFactsChange: (value: string) => void;
  onSubmit: () => void;
  busy: boolean;
  onSkip?: () => void;
  skipping?: boolean;
  progressHint?: string;
}) {
  const fieldId = useId();
  const input = useRef<HTMLTextAreaElement>(null);
  const [error, setError] = useState("");
  const question = questions.find(item => item.trim())?.trim() ?? "请补充你认为重要的情况。";
  const pending = busy || skipping;
  const example = /何时|什么时候|时间|日期|多久|哪天/.test(question)
    ? "例如：大约两周前，具体日期记不清了。"
    : /购买渠道|购买平台|网购|实体店|哪里买|在哪.*买/.test(question)
      ? "例如：在网上下单购买，订单记录还在。"
    : /通知|联系/.test(question)
      ? "例如：对方在微信里告诉我的，聊天记录还在。"
      : /是否|有没有|有无|吗[？?]?$/.test(question)
        ? "可以回答“有”“没有”或“不确定”，再说说你记得的情况。"
        : "例如：我记得……，其他细节暂时不清楚。";
  const submit = () => {
    if (pending) return;
    if (!facts.trim()) {
      setError(onSkip ? "请先补充当前问题；不清楚时可以写“不清楚”，或选择跳过。" : "请先补充当前问题；不清楚时可以写“不清楚”。");
      input.current?.focus();
      return;
    }
    setError("");
    onSubmit();
  };

  return (
    <section className="fzx-clarify" aria-label="补充情况">
      <div className="fzx-monitor-head fzx-clarify-heading">
        <h3>补充一个关键信息</h3>
        <span className="fzx-pill" data-tone="await">
          {roundsText}
        </span>
      </div>
      {progressHint && <p className="fzx-mini fzx-clarify-progress">{progressHint}</p>}
      <p className="fzx-clarify-question">{question}</p>
      <label className="field-label" htmlFor={fieldId}>
        你的回答
      </label>
      <p className="fzx-mini fzx-clarify-guidance" id={`${fieldId}-hint`}>
        只需回答上面这一个问题；不记得的部分可以直接说明。示例仅供参考，请按真实情况填写。
      </p>
      <textarea
        ref={input}
        id={fieldId}
        value={facts}
        onChange={event => {
          setError("");
          onFactsChange(event.target.value);
        }}
        placeholder={example}
        aria-describedby={`${fieldId}-hint${error ? ` ${fieldId}-error` : ""}`}
        aria-invalid={Boolean(error)}
        disabled={pending}
      />
      {error && <p id={`${fieldId}-error`} className="fzx-clarify-error" role="alert">{error}</p>}
      <p className="fzx-mini fzx-clarify-reassurance">
        {onSkip ? "补充越详细，回答越有针对性；也可以跳过，先看基于现有信息的回答。" : "补充越详细，回答越有针对性；不清楚的地方也可以直接说明。"}
      </p>
      <div className="fzx-actions fzx-clarify-actions">
        <button type="button" className="primary" onClick={submit} disabled={pending}>
          {busy && !skipping ? "正在提交…" : "提交补充说明"}
        </button>
        {onSkip && (
          <button
            type="button"
            className="secondary"
            onClick={() => {
              setError("");
              onSkip();
            }}
            disabled={pending}
          >
            {skipping ? "正在继续分析…" : "跳过，直接看分析"}
          </button>
        )}
      </div>
      <p className="fzx-mini">本次输入仅在当前会话中处理，服务重启后需重新提交。请及时保存需要的内容。</p>
    </section>
  );
}

export function ConsultAnswerBlocks({
  answer,
  passed,
  sources,
  onOpenSource,
}: {
  answer: ConsultAnswerSnapshot | undefined;
  passed: string[] | undefined;
  sources: SourceBrief[];
  onOpenSource: (sourceId: string) => void;
}) {
  const sections = answerSections(answer, passed);

  if (!sections.hasAnswer) {
    return (
      <div className="fzx-empty">
        <p>从你的问题开始</p>
        <p style={{ fontSize: 13 }}>
          说说你遇到的事，或直接问一个法律问题。我会根据你提供的情况整理回答；信息不够时，会请你补充。
          回答中的法律依据可以点开查看原文。
        </p>
      </div>
    );
  }

  return (
    <div className="fzx-blocks">
      <section className="fzx-block" aria-label="结论">
        <h4>一、结论</h4>
        {sections.conclusions.length ? (
          <ol className="fzx-conclusions">
            {sections.conclusions.map((item, index) => (
              <li key={`${item.text}-${index}`} className="fzx-conclusion">
                <p className="fzx-conclusion-text">{item.text}</p>
                <div className="fzx-cites">
                  {(item.citations ?? []).map(citation => {
                    const source = sources.find(entry => entry.source_id === citation.source_id);
                    return (
                      <button
                        key={`${citation.source_id}-${citation.quote.slice(0, 12)}`}
                        type="button"
                        className="fzx-cite"
                        onClick={() => onOpenSource(citation.source_id)}
                      >
                        <span data-kind={source?.kind === "user_material" ? "user" : undefined}>
                          {source?.origin_text ?? "来源"}
                        </span>
                        <span className="fzx-cite-id">{citation.identifier || citation.source_id}</span>
                        <span className="fzx-cite-status">{source?.status_text ?? "未标注核验状态"}</span>
                      </button>
                    );
                  })}
                </div>
              </li>
            ))}
          </ol>
        ) : (
          <p className="fzx-mini">
            本次没有通过引用核验的结论，因此不做结论展示（未通过核验的内容不作为结论）。
          </p>
        )}
      </section>

      <section className="fzx-block" aria-label="法律依据">
        <h4>二、法律依据（{sections.citations.length} 条）</h4>
        {sections.citations.length ? (
          <ul className="fzx-cite-list">
            {sections.citations.map(citation => {
              const source = sources.find(entry => entry.source_id === citation.sourceId);
              return (
                <li key={`${citation.sourceId}-${citation.quote.slice(0, 10)}`}>
                  <button type="button" className="fzx-cite" onClick={() => onOpenSource(citation.sourceId)}>
                    <span data-kind={source?.kind === "user_material" ? "user" : undefined}>
                      {source?.origin_text ?? "来源"}
                    </span>
                    <span className="fzx-cite-id">{citation.identifier}</span>
                    <span className="fzx-cite-status">{source?.status_text ?? "未标注核验状态"}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="fzx-mini">本次没有可引用的依据。</p>
        )}
      </section>

      <section className="fzx-block" aria-label="还需确认的情况与风险">
        <h4>三、还需确认的情况与风险</h4>
        {sections.uncertainties.length || sections.insufficient ? (
          <ul>
            {sections.insufficient && <li>{sections.insufficient}</li>}
            {sections.uncertainties.map(item => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        ) : (
          <p className="fzx-mini">本次回答未列出其他待确认事项，仍需结合实际情况核对。</p>
        )}
      </section>

      <section className="fzx-block" aria-label="下一步建议">
        <h4>四、下一步建议</h4>
        {sections.nextSteps.length ? (
          <ul>
            {sections.nextSteps.map(item => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        ) : (
          <p className="fzx-mini">本次回答未给出额外建议。</p>
        )}
      </section>
    </div>
  );
}
