"use client";

/**
 * 合同审查 · 「审查中」这一屏的新设计（2026-09-20）
 *
 * 这份是**设计稿**：演示数据、不调后端、不花钱。三个按钮切换三种情况：
 *  ① 正在跑  ② 已经跑完  ③ 没跑完（失败）
 *
 * 为什么重画（产品经理原话：「审查中还是存在一定问题」）：
 *  - 现在这一屏会在运行已经结束/失败时仍然显示「正在提交，等待后端第一条进度」——
 *    等于界面说假话；
 *  - 信息太薄：等 40 秒的时候看不出做到哪、产出了什么。
 * 新设计的规矩：**永远只说实话**——在跑就说在跑（带第几步、已用多久、已产出什么），
 * 跑完就说跑完，失败就说失败并给重试，没有实时进度就明确写"下面是最后的状态"。
 */

import { useEffect, useState } from "react";
import { AlertTriangle, ArrowRight, CheckCircle2, Clock, RefreshCw, RotateCcw, Square } from "lucide-react";
import { AppShell } from "@/components/shell/app-shell";
import "@/app/contract.css";

type Mode = "running" | "done" | "failed";

const STAGES = ["读懂合同、切成条款", "按你的立场找依据", "逐条分级风险", "核验引用的原文"];

const STAGE_DETAIL: Record<string, string[]> = {
  "读懂合同、切成条款": ["识别出 7 条主要条款", "第 2 条、第 6 条条款较密，已单独切块"],
  "按你的立场找依据": ["已取回 4 条依据", "正在比对《民法典》合同编相关条文"],
  "逐条分级风险": ["还没开始", "会按高 / 中 / 低分级，并标出对哪一方不利"],
  "核验引用的原文": ["还没开始", "逐字比对引用是否与原文一致，对不上的只作提示"],
};

export default function ContractProgressDesignPage() {
  const [mode, setMode] = useState<Mode>("running");

  // 方便评审与截图：?mode=done 直接打开"已经跑完"那一种（仅本设计稿使用）
  useEffect(() => {
    const wanted = new URLSearchParams(window.location.search).get("mode");
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 只在挂载时按地址栏参数初始化一次
    if (wanted === "done" || wanted === "failed" || wanted === "running") setMode(wanted);
  }, []);

  const head =
    mode === "running" ? (
      <>
        <Clock size={16} aria-hidden="true" />
        <b>正在审查：按你的立场找依据</b> · 第 2 步 / 共 4 步 · 已用 <b>18 秒</b>
      </>
    ) : mode === "done" ? (
      <>
        <CheckCircle2 size={16} aria-hidden="true" />
        <b>这次审查已经完成</b> · 用时 <b>41 秒</b> · 一共出 <b>3 条风险</b>
      </>
    ) : (
      <>
        <AlertTriangle size={16} aria-hidden="true" />
        <b>这次没跑完：卡在「按你的立场找依据」</b> · 前面已经做出的部分都保留着
      </>
    );

  const stageState = (index: number) =>
    mode === "done" ? "done" : mode === "running" ? (index < 1 ? "done" : index === 1 ? "running" : "todo") : index < 1 ? "done" : index === 1 ? "failed" : "todo";

  return (
    <AppShell>
      <div className="ct-wrap">
        <p className="ct-draft" role="note">
          <AlertTriangle size={14} aria-hidden="true" />
          这是<strong>设计稿</strong>：演示数据，不是真结果。点上面三个按钮看三种情况——<strong>正在跑 / 已经跑完 / 没跑完</strong>。
        </p>

        <div className="ct-actions" style={{ marginBottom: 18 }}>
          {(
            [
              ["running", "① 正在跑"],
              ["done", "② 已经跑完"],
              ["failed", "③ 没跑完（出错）"],
            ] as [Mode, string][]
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              className={mode === value ? "ct-btn ct-btn-primary" : "ct-btn"}
              onClick={() => setMode(value)}
            >
              {label}
            </button>
          ))}
        </div>

        <section className="ct-card">
          <h2>第三步　审查进行中</h2>

          {/* 顶部一句实话：只说你现在的真实处境 */}
          <p className="ct-status" data-tone={mode}>
            {head}
          </p>

          {mode === "failed" && (
            <p className="ct-blocked" style={{ marginTop: 6 }}>
              <AlertTriangle size={14} aria-hidden="true" />
              依据检索接口调用失败，本次未获得可核验依据。<b>这不等于「没有问题规定」</b>，也不代表这份合同没问题。
            </p>
          )}

          {/* 已经产出什么：等待时看得见东西在长出来 */}
          <div className="ct-yield">
            <div>
              <span>已切分条款</span>
              <b>{mode === "running" ? 7 : 7} 条</b>
            </div>
            <div>
              <span>已取回依据</span>
              <b>{mode === "running" ? 4 : mode === "done" ? 6 : 0} 条</b>
            </div>
            <div>
              <span>已出风险</span>
              <b>{mode === "done" ? 3 : 0} 条</b>
            </div>
          </div>

          {/* 四段进度：每段都写清已完成 / 进行中 / 未开始 / 卡住了 */}
          <ul className="ct-stages">
            {STAGES.map((name, index) => {
              const state = stageState(index);
              return (
                <li key={name} data-state={state}>
                  <span className="ct-stage-mark" aria-hidden="true">
                    {state === "done" ? <CheckCircle2 size={15} /> : index + 1}
                  </span>
                  <div>
                    <strong>{name}</strong>
                    <em>
                      {state === "done" && "已完成"}
                      {state === "running" && "正在做"}
                      {state === "todo" && "还没开始"}
                      {state === "failed" && "没做成"}
                    </em>
                    {STAGE_DETAIL[name].map(line => (
                      <small key={line}>{line}</small>
                    ))}
                  </div>
                </li>
              );
            })}
          </ul>

          <div className="ct-actions">
            {mode === "running" && (
              <>
                <button type="button" className="ct-btn ct-btn-ghost">
                  <Square size={14} aria-hidden="true" />
                  不再等待（后台会继续跑完）
                </button>
                <button type="button" className="ct-btn ct-btn-ghost">
                  <RefreshCw size={14} aria-hidden="true" />
                  刷新状态
                </button>
              </>
            )}
            {mode === "done" && (
              <>
                <button type="button" className="ct-btn ct-btn-primary">
                  去看结果
                  <ArrowRight size={15} aria-hidden="true" />
                </button>
                <button type="button" className="ct-btn ct-btn-ghost">
                  <RefreshCw size={14} aria-hidden="true" />
                  刷新状态
                </button>
              </>
            )}
            {mode === "failed" && (
              <>
                <button type="button" className="ct-btn ct-btn-primary">
                  <RotateCcw size={15} aria-hidden="true" />
                  重试没做成的那一步
                </button>
                <button type="button" className="ct-btn ct-btn-ghost">
                  <RefreshCw size={14} aria-hidden="true" />
                  刷新状态
                </button>
              </>
            )}
          </div>

          <p className="ct-mini" style={{ marginTop: 12 }}>
            {mode === "running"
              ? "这几行进度来自后端的真实事件，不是装饰动画；点「刷新状态」会显示取回的时间。"
              : "这次运行已经结束，下面是它最后的状态——不会再显示「正在提交」这类含糊话。"}
          </p>
        </section>
      </div>
    </AppShell>
  );
}
