"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { ArrowRight, ArrowUpRight, Check, FileSearch, MessageSquare, Search, ShieldCheck } from "lucide-react";
import { SiteHeader } from "@/components/site-header";
import { SiteFooter } from "@/components/site-footer";
import { MotionMain } from "@/components/motion-main";
import { BackendStatus } from "@/components/backend-status";

export type HomeEntry = { module: "research" | "consult" | "contract"; query?: string };
type Props = { onEnter: (entry: HomeEntry) => void };

const examples = [
  { text: "房东把房子卖了，新房东让我搬走怎么办？", mode: "consult" },
  { text: "公司拖欠工资，我该准备什么材料？", mode: "consult" },
] as const;

const tools = [
  {
    title: "类案研究",
    href: "/research",
    icon: Search,
    description: "查找相关案例，选择需要比较的材料，对照事实与裁判观点，整理研究备忘。",
    action: "进入类案研究",
    label: "案例检索与对比",
  },
  {
    title: "法律问答",
    href: "/consult",
    icon: MessageSquare,
    description: "用自己的话描述问题，逐步补充关键信息，查看相关依据与下一步建议。",
    action: "去提问",
    label: "梳理问题与依据",
  },
  {
    title: "合同审查",
    href: "/contract",
    icon: FileSearch,
    description: "上传合同，说明你代表哪一方，对照原文查看风险与修改建议，导出审查报告。",
    action: "进入合同审查",
    label: "条款定位与审查",
  },
] as const;

export function LegalHome({ onEnter }: Props) {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<"research" | "consult">("consult");
  const input = useRef<HTMLTextAreaElement>(null);
  const start = () => {
    if (query.trim()) onEnter({ module: mode, query: query.trim() });
  };

  return (
    <div className="fh-home">
      <SiteHeader active="home" />
      <MotionMain>
        <section className="fh-hero" aria-labelledby="home-title">
          <p className="fh-intro">法小智 · 法律 AI 助手</p>
          <h1 id="home-title">
            说说你的问题，<br />一起找到依据。
          </h1>
          <p className="fh-hero-description">
            用平常的话说清遇到的事，<br />从法律问答、案例检索到合同审查，帮你逐步理清思路。
          </p>
          <form
            className="fh-composer"
            onSubmit={event => {
              event.preventDefault();
              start();
            }}
          >
            <div className="fh-composer-tabs" role="group" aria-label="选择你需要的帮助">
              <button type="button" className={mode === "research" ? "is-active" : ""} aria-pressed={mode === "research"} onClick={() => setMode("research")}>
                <Search size={17} aria-hidden="true" />类案检索
              </button>
              <button type="button" className={mode === "consult" ? "is-active" : ""} aria-pressed={mode === "consult"} onClick={() => setMode("consult")}>
                <MessageSquare size={17} aria-hidden="true" />法律问答
              </button>
            </div>
            <label htmlFor="home-question">你想了解什么？</label>
            <textarea
              ref={input}
              id="home-question"
              rows={2}
              maxLength={4000}
              value={query}
              onChange={event => setQuery(event.target.value)}
              placeholder="说说发生了什么、你有什么疑问，不用先整理成专业问题…"
              onKeyDown={event => {
                if (event.key === "Enter" && (event.metaKey || event.ctrlKey) && !event.nativeEvent.isComposing) {
                  event.preventDefault();
                  start();
                }
              }}
              aria-describedby="home-question-hint"
            />
            <div className="fh-composer-bottom">
              <p id="home-question-hint">
                <ShieldCheck size={16} aria-hidden="true" />
                {mode === "research" ? "找到相关案例，再对照事实与裁判观点" : "不用准备法律术语，关键信息可以逐步补充"}
              </p>
              <button className="fh-button" type="submit" disabled={!query.trim()}>
                {mode === "research" ? "去检索" : "去提问"}
                <ArrowRight size={18} aria-hidden="true" />
              </button>
            </div>
          </form>
          <div className="fh-examples">
            <span>可以这样问</span>
            {examples.map(example => (
              <button
                key={example.text}
                onClick={() => {
                  setQuery(example.text);
                  setMode(example.mode);
                  input.current?.focus();
                }}
              >
                {example.text}
                <ArrowUpRight size={15} aria-hidden="true" />
              </button>
            ))}
          </div>
          <BackendStatus className="mt-5" />
        </section>

        <section className="fh-tools fh-section" aria-labelledby="tools-title">
          <div className="fh-section-heading">
            <h2 id="tools-title">从你的问题，找到合适的帮助。</h2>
            <p>了解法律问题、查找案例或审阅合同，都可以从这里开始。</p>
          </div>
          <div className="fh-tool-grid">
            {tools.map(tool => (
              <Link className="fh-tool" href={tool.href} key={tool.href}>
                <div className="fh-tool-top">
                  <tool.icon size={26} strokeWidth={1.5} aria-hidden="true" />
                  <span>{tool.label}</span>
                </div>
                <h3>{tool.title}</h3>
                <p>{tool.description}</p>
                <span className="fh-tool-action">
                  {tool.action}
                  <ArrowUpRight size={18} aria-hidden="true" />
                </span>
              </Link>
            ))}
          </div>
        </section>

        <section className="fh-research-section" aria-labelledby="research-title">
          <div className="fh-section fh-research-layout">
            <div className="fh-research-copy">
              <h2 id="research-title">
                不止找到案例，<br />更看清关键差异。
              </h2>
              <p>看起来相似的事情，可能有不同的关键事实。对照案例中的事实、裁判观点及适用条件，帮助你理解差异，也支持进一步的专业研究。</p>
              <ul>
                <li><Check size={18} aria-hidden="true" /><span>每条依据都标出来源与核验状态</span></li>
                <li><Check size={18} aria-hidden="true" /><span>由你选择需要比较的案例</span></li>
                <li><Check size={18} aria-hidden="true" /><span>区分可引用依据与待核实材料，说明还缺哪些信息</span></li>
              </ul>
              <Link className="fh-text-link" href="/research">
                进入类案研究<ArrowRight size={18} aria-hidden="true" />
              </Link>
            </div>
            <article className="fh-research-preview" aria-label="类案研究的研究路径">
              <div className="fh-preview-bar">
                <span><Search size={18} aria-hidden="true" />研究工作台</span>
                <span className="fh-demo-tag">研究流程</span>
              </div>
              <div className="fh-preview-content">
                <p className="fh-preview-topic">研究路径</p>
                <h3>描述问题 → 查找材料 → 选择案例 → 对比分析 → 研究备忘</h3>
                <div className="fh-preview-note">
                  <ShieldCheck size={18} aria-hidden="true" />
                  <p>
                    <strong>确定研究范围，保留引用依据</strong>
                    <span>选择与你的问题相关的案例，再比较事实与裁判观点；阅读分析时，可返回原文核查引用与适用条件。</span>
                  </p>
                </div>
                <p className="fh-preview-disclosure">流程示意；具体研究范围和结果以选定材料为准。</p>
              </div>
            </article>
          </div>
        </section>

        <section className="fh-section fh-principle-strip" aria-labelledby="principle-title">
          <div>
            <h2 id="principle-title">依据可核查，判断由你掌握。</h2>
            <p>区分来源材料与 AI 分析，标明核验状态，也说明哪些信息还需要确认。</p>
          </div>
          <Link className="fh-text-link" href="/about">
            了解法小智的产品原则<ArrowUpRight size={18} aria-hidden="true" />
          </Link>
        </section>
      </MotionMain>
      <SiteFooter />
    </div>
  );
}
