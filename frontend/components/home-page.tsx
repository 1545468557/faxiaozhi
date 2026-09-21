"use client";

import { LegalHome } from "@/components/legal-home";
import { useWorkspaceNavigation } from "@/components/workspace-session";

export function HomePage() {
  const { openWorkspace } = useWorkspaceNavigation();
  return <LegalHome onEnter={openWorkspace} />;
}
