import { AppShell } from "@/components/shell/app-shell";
import { AskHome } from "@/components/consult/ask-home";

export const metadata = {
  title: "法律问答 · 法小智",
  description: "用日常语言说清楚你遇到的事，拿到带依据的回答，以及可点开核对原文的法条与判例。",
};

export default function Home() {
  return (
    <AppShell>
      <AskHome />
    </AppShell>
  );
}
