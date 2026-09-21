import type { Metadata } from "next";
import Link from "next/link";
import { ArrowRight, BookOpen, Fingerprint, ShieldCheck } from "lucide-react";
import { SiteHeader } from "@/components/site-header";
import { SiteFooter } from "@/components/site-footer";
import { MotionMain } from "@/components/motion-main";

export const metadata: Metadata = { title: "关于法小智 · 法律 AI 助手", description: "法小智主要服务律师与法务，也欢迎任何有法律问题的人。无需法律术语，从日常提问到依据检索、引用核验与专业研究。" };
const principles = [
  { icon: BookOpen, title: "依据来源可追溯", text: "区分检索所得材料、提供的材料与 AI 分析。保留来源和原文线索，便于复核引用及其适用条件。" },
  { icon: ShieldCheck, title: "核验边界明确", text: "呈现引用核验状态、材料缺口与观点分歧。核验结果用于判断引用是否有据，法律适用仍需结合案件事实复核。" },
  { icon: Fingerprint, title: "关键选择由你确认", text: "研究中比较哪些案例、合同审查代表哪一方，都由你确认。工具协助检索与整理，涉及具体行动时仍需结合完整事实审慎判断。" },
];

export default function AboutPage() {
  return <div className="fh-home fh-about"><SiteHeader active="about" /><MotionMain>
    <section className="fh-about-hero fh-section"><p className="fh-intro">关于法小智</p><h1>从平常的话开始，<br />让问题逐步清晰。</h1><p>法小智主要服务律师与法务，也欢迎任何有法律问题的人。<br />无需法律术语，也不用先整理完整案情；关键信息不足时，会进一步询问。无论是初步了解问题，还是开展专业研究，都可以从自己的描述开始。</p></section>
    <section className="fh-section fh-about-principles" aria-labelledby="about-principles-title"><h2 id="about-principles-title">我们坚持的三个原则</h2><div>{principles.map(principle => <article key={principle.title}><principle.icon size={28} strokeWidth={1.5} aria-hidden="true" /><h3>{principle.title}</h3><p>{principle.text}</p></article>)}</div></section>
    <section className="fh-about-version" aria-labelledby="about-version-title"><div className="fh-section fh-about-version-layout"><div><h2 id="about-version-title">三种方式，帮助你理清问题</h2><p>从日常提问到专业研究，保留依据，也说明不确定之处。</p></div><div className="fh-version-list">
      <article><div><h3>类案研究</h3><span>研究备忘</span></div><p>围绕争议问题检索案例，由你确认研究样本，再对比事实与裁判观点。分析附来源与引用核验状态，可导出研究备忘，供后续论证和复核使用。</p><Link href="/research">进入类案研究<ArrowRight size={16} aria-hidden="true" /></Link></article>
      <article><div><h3>法律问答</h3><span>解答与依据</span></div><p>用自己的话说说遇到的事和疑问，关键信息不足时再逐步补充。结合检索到的法条与案例，查看解答、引用依据、待核实事项和下一步建议，也可导出备忘。</p><Link href="/consult">去提问<ArrowRight size={16} aria-hidden="true" /></Link></article>
      <article><div><h3>合同审查</h3><span>审查报告</span></div><p>上传合同、核对文本并确定审查立场，对照条款原文查看分级风险及相关依据。保留需进一步核实的事项，整理修改建议并导出合同审查报告。</p><Link href="/contract">进入合同审查<ArrowRight size={16} aria-hidden="true" /></Link></article>
    </div></div></section>
    <section className="fh-section fh-about-end"><h2>从你关心的问题开始。</h2><p>不必先准备专业表达，把发生的事说清楚就是第一步。</p><Link className="fh-button" href="/research">查找相关案例<ArrowRight size={18} aria-hidden="true" /></Link></section>
  </MotionMain><SiteFooter /></div>;
}
