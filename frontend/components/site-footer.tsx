import Link from "next/link";

export function SiteFooter() {
  return <footer className="fs-footer">
    <div className="fs-footer-main"><div><Link className="fs-footer-brand" href="/">法小智</Link><p>用平常的话提问，逐步理清法律问题。</p></div><nav aria-label="页脚导航"><Link href="/research">类案研究</Link><Link href="/consult">法律问答</Link><Link href="/contract">合同审查</Link><Link href="/about">关于法小智</Link></nav></div>
    <div className="fs-footer-bottom"><span>© {new Date().getFullYear()} 法小智</span><span>AI 辅助梳理与研究，使用前请核对依据及其适用情况。</span></div>
  </footer>;
}
