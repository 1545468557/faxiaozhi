"use client";

import { useRef, type ReactNode } from "react";
import { useInterfaceMotion } from "@/components/use-interface-motion";

export function MotionMain({ children }: { children: ReactNode }) {
  const main = useRef<HTMLElement>(null);
  useInterfaceMotion(main, ".fh-hero > .fh-intro, .fh-hero > h1, .fh-hero-description, .fh-composer, .fh-examples, .fh-about-hero > *", "entry");
  return <main ref={main} id="main-content" tabIndex={-1}>{children}</main>;
}
