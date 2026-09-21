"use client";

/**
 * 法规查找（迭代 3）
 *
 * 一句话：**一个框，直接查某条法条**。支持"法规名 + 条号"（577 / 第577条 / 第五百七十七条，
 * 以及「第X条之一」），也支持关键词检索。
 *
 * 三条口径（与后端一致，写死）：
 * 1. **默认只出「现行有效」**——本库有 686 条已修改/已废止/失效，默认混入会让用户引用废止条文，
 *    所以默认过滤；用户显式勾选"包含旧版本"后才出现，并把状态标黄/标红；
 * 2. 每条结果必须能**一键复制引用**（带法规名、条号、效力状态）；
 * 3. **不显示"数据截至时间"**（产品经理 2026-09-21 明确要求去掉）。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Copy, FileText, Info, Search, X } from "lucide-react";
import { statutesApi, citationOf, type StatuteDetail, type StatuteHit, type StatutesStatus } from "@/lib/api/statutes";

const STATUS_TONE: Record<string, "ok" | "warn" | "bad"> = {
  valid: "ok",
  amended: "warn",
  pending: "warn",
  na: "warn",
  repealed: "bad",
  invalid: "bad",
  unknown: "warn",
};

export function StatuteSearch() {
  const [query, setQuery] = useState("");
  const [includeInvalid, setIncludeInvalid] = useState(false);
  const [items, setItems] = useState<StatuteHit[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [searched, setSearched] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const [status, setStatus] = useState<StatutesStatus | null>(null);
  const [detail, setDetail] = useState<StatuteDetail | null>(null);
  /** 正在打开哪一条的全文（按钮给"正在打开…"反馈，避免"点了没反应"的错觉） */
  const [openingId, setOpeningId] = useState<string | null>(null);
  const detailRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    void statutesApi.status().then(setStatus).catch(() => undefined);
  }, []);

  const run = useCallback(
    async (text: string, withInvalid: boolean) => {
      const trimmed = text.trim();
      if (trimmed.length < 2) {
        setNotice("至少输入两个字，例如「民法典 577」或「押金 退还」。");
        return;
      }
      setBusy(true);
      setNotice("");
      setSearched(true);
      try {
        const result = await statutesApi.search(trimmed, { includeInvalid: withInvalid, limit: 10 });
        setItems(result.items);
        setTotal(result.total);
        if (result.items.length === 0) setNotice("未找到匹配条文，请检查法规名称和条号，例如“民法典34”或“民法典第三十四条”。也可能是当前法规库尚未收录，或被有效性筛选排除。");
      } catch (cause) {
        setItems([]);
        setTotal(null);
        setNotice(cause instanceof Error ? cause.message : "检索失败，请稍后再试。");
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  /**
   * 打开某份法规的全文。
   *
   * 2026-09-21 实测踩到：详情原来渲染在**结果列表下面**，点完内容其实已经出来了，
   * 但在屏幕外（视口 674px、页面高 1383px、滚动位置 0）→ 用户看到的是"点了没反应"。
   * 现在：① 按钮显示"正在打开…"；② 打开后**滚到全文**；③ 全文区块渲染在结果列表之前。
   */
  const openDetail = useCallback(async (hit: StatuteHit) => {
    setOpeningId(hit.bbbs);
    setNotice("");
    try {
      setDetail(await statutesApi.detail(hit.bbbs));
      window.setTimeout(() => detailRef.current?.scrollIntoView({ block: "start", behavior: "smooth" }), 60);
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "打不开这份法规。");
    } finally {
      setOpeningId(null);
    }
  }, []);

  const copy = async (hit: StatuteHit) => {
    const text = citationOf(hit);
    try {
      await navigator.clipboard.writeText(text);
      setCopied(text);
      window.setTimeout(() => setCopied(null), 1500);
    } catch {
      setNotice("浏览器不允许自动复制，请手动选中条文复制。");
    }
  };

  return (
    <div className="ct-wrap">
      <header className="ct-head">
        <h1>法规查找</h1>
        <p>
          查法律法规原文。支持直接查某一条：输入「法规名 + 条号」，例如
          <b> 民法典 577</b>、<b>刑法 第一百二十条之一</b>；也可以只输关键词，例如「押金 退还」。
        </p>
      </header>

      <section className="ct-card">
        <form
          className="ct-actions"
          style={{ marginTop: 0, alignItems: "stretch" }}
          onSubmit={event => {
            event.preventDefault();
            void run(query, includeInvalid);
          }}
        >
          <input
            className="ct-input"
            style={{ flex: 1, minWidth: 220 }}
            value={query}
            placeholder="民法典 577 / 押金 退还 / 格式条款 无效"
            aria-label="查法规或条号"
            onChange={event => setQuery(event.target.value)}
          />
          <button type="submit" className="ct-btn ct-btn-primary" disabled={busy}>
            <Search size={15} aria-hidden="true" />
            {busy ? "正在查…" : "查一下"}
          </button>
        </form>

        <div className="ct-actions" style={{ marginTop: 10 }}>
          <label className="ct-mini" style={{ display: "inline-flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={includeInvalid}
              onChange={event => {
                setIncludeInvalid(event.target.checked);
                if (searched) void run(query, event.target.checked);
              }}
            />
            包含已修改 / 已废止的旧条文
          </label>
          {status && (
            <span className="ct-mini">
              本地法规库：{status.statutes} 篇 · {status.articles} 条条文 · 默认只出「现行有效」（另有{" "}
              {(status.by_status["已被修改"] ?? 0) + (status.by_status["已废止"] ?? 0) + (status.by_status["已失效"] ?? 0)} 条旧版本）
            </span>
          )}
        </div>

        {notice && (
          <p className="ct-blocked" role="status" style={{ marginTop: 12 }}>
            <Info size={14} aria-hidden="true" />
            {notice}
          </p>
        )}
        {copied && (
          <p className="ct-mini" role="status" style={{ marginTop: 10 }}>
            已复制引用：{copied}
          </p>
        )}
      </section>

      {detail && (
        <section className="ct-card" style={{ marginTop: 16 }} ref={detailRef}>
          <div className="ct-actions" style={{ marginTop: 0, justifyContent: "space-between" }}>
            <h2 style={{ fontSize: 18, margin: 0 }}>
              《{detail.title}》
              <span className="ct-badge" data-tone={STATUS_TONE[detail.status_code] ?? "warn"} style={{ marginLeft: 8 }}>
                {detail.status_text}
              </span>
            </h2>
            <button type="button" className="ct-btn ct-btn-ghost" onClick={() => setDetail(null)}>
              <X size={14} aria-hidden="true" />
              收起
            </button>
          </div>
          <p className="ct-mini" style={{ margin: "8px 0 12px" }}>
            {[detail.category, detail.organ, detail.publish_date && `公布 ${detail.publish_date}`, detail.effective_date && `施行 ${detail.effective_date}`, `共 ${detail.article_count} 条`]
              .filter(Boolean)
              .join(" · ")}
          </p>
          <div className="ct-text" style={{ maxHeight: 460 }}>
            {detail.articles.map(article => (
              <p key={article.no}>
                <b>{article.no}</b>
                {"　"}
                {article.text.replace(article.no, "").trimStart()}
              </p>
            ))}
          </div>
        </section>
      )}

      {searched && items.length > 0 && (
        <section className="ct-card" style={{ marginTop: 16 }}>
          <h2 style={{ fontSize: 18, marginBottom: 10 }}>找到 {total} 条</h2>
          <ul className="ct-risks" style={{ maxHeight: "none" }}>
            {items.map(item => (
              <li key={`${item.title}-${item.no}`} style={{ marginBottom: 10 }}>
                <div className="ct-risk-body" style={{ paddingTop: 14 }}>
                  <p className="ct-risk-issue" style={{ fontSize: 15, fontWeight: 600, marginBottom: 8 }}>
                    《{item.title}》{item.no}
                    <span className="ct-badge" data-tone={STATUS_TONE[item.status_code] ?? "warn"} style={{ marginLeft: 8 }}>
                      {item.status_text}
                    </span>
                  </p>
                  <p className="ct-mini" style={{ marginBottom: 8 }}>
                    {[item.organ, item.publish_date && `公布 ${item.publish_date}`, item.effective_date && `施行 ${item.effective_date}`]
                      .filter(Boolean)
                      .join(" · ")}
                  </p>
                  <p className="ct-quote" style={{ whiteSpace: "pre-wrap" }}>
                    {item.text}
                  </p>
                  <div className="ct-actions" style={{ marginTop: 10 }}>
                    <button type="button" className="ct-btn" onClick={() => void copy(item)}>
                      <Copy size={14} aria-hidden="true" />
                      复制引用
                    </button>
                    <button
                      type="button"
                      className="ct-btn ct-btn-ghost"
                      disabled={openingId === item.bbbs}
                      onClick={() => void openDetail(item)}
                    >
                      <FileText size={14} aria-hidden="true" />
                      {openingId === item.bbbs ? "正在打开…" : "看这份法规全文"}
                    </button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      <p className="ct-foot">
        <AlertTriangle size={14} aria-hidden="true" />
        条文来自本地法规库，仅供检索与引用核对；具体适用请由执业律师结合案情判断。
      </p>
    </div>
  );
}
