import type { Metadata } from "next";
import "./globals.css";
import "./legal.css";
import "./legibility.css";
import "./real-research.css";
import "./home.css";
import "./workspace.css";
import "./interactions.css";
import "./status.css";
import "./shell.css";
import "./contract.css";
import "./research-design.css";
import { WorkspaceSessionProvider } from "@/components/workspace-session";

export const metadata: Metadata = {
  title: "法小智 · 法律 AI 助手",
  description: "用平常的话描述遇到的事，拿到带依据的回答：法律问答、案例查询、法规查找与合同审查。",
  other: {
    "codex-preview": "development",
  },
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body className="antialiased"><WorkspaceSessionProvider>{children}</WorkspaceSessionProvider></body>
    </html>
  );
}
