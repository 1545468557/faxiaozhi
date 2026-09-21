"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { ArrowUpRight, Menu, Scale, X } from "lucide-react";
import { useInterfaceMotion } from "@/components/use-interface-motion";

export type SitePage = "home" | "research" | "consult" | "contract" | "about";
const pages: { id: SitePage; href: string; label: string }[] = [
  { id: "home", href: "/", label: "首页" },
  { id: "research", href: "/research", label: "类案研究" },
  { id: "consult", href: "/consult", label: "法律问答" },
  { id: "contract", href: "/contract", label: "合同审查" },
  { id: "about", href: "/about", label: "关于法小智" },
];

export function SiteHeader({ active = "home" }: { active?: SitePage }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuButton = useRef<HTMLButtonElement>(null);
  const header = useRef<HTMLElement>(null);
  useInterfaceMotion(header, ".fs-mobile-nav a", String(menuOpen), true);
  return <header ref={header} className="fs-header" onKeyDown={event => {
    if (event.key === "Escape" && menuOpen) { setMenuOpen(false); menuButton.current?.focus(); }
  }}>
    <a className="fs-skip" href="#main-content">跳至主要内容</a>
    <div className="fs-header-inner">
      <Link className="fs-brand" href="/" aria-label="法小智首页" onClick={() => setMenuOpen(false)}><span className="fs-brand-icon"><Scale size={25} strokeWidth={1.6} aria-hidden="true" /></span><span>法小智</span></Link>
      <nav className="fs-desktop-nav" aria-label="主导航">{pages.map(page => <Link key={page.id} href={page.href} aria-current={active === page.id ? "page" : undefined}>{page.label}</Link>)}</nav>
      <div className="fs-header-actions">
        <Link className="fs-start" href="/research" onClick={() => setMenuOpen(false)}>开始研究<ArrowUpRight size={17} aria-hidden="true" /></Link>
        <button ref={menuButton} className="fs-menu-button" type="button" aria-label={menuOpen ? "关闭导航" : "打开导航"} aria-expanded={menuOpen} aria-controls="site-mobile-nav" onClick={() => setMenuOpen(!menuOpen)}>{menuOpen ? <X size={24} aria-hidden="true" /> : <Menu size={24} aria-hidden="true" />}</button>
      </div>
    </div>
    {menuOpen && <nav id="site-mobile-nav" className="fs-mobile-nav" aria-label="手机导航">{pages.map(page => <Link key={page.id} href={page.href} aria-current={active === page.id ? "page" : undefined} onClick={() => setMenuOpen(false)}>{page.label}<ArrowUpRight size={16} aria-hidden="true" /></Link>)}</nav>}
  </header>;
}
