"use client";

import type { RefObject } from "react";
import { gsap } from "gsap";
import { useGSAP } from "@gsap/react";

gsap.registerPlugin(useGSAP);

// Nothing is hidden in CSS: server-rendered content stays usable before JS,
// when motion is reduced, and when a component unmounts mid-animation.
export function useInterfaceMotion(
  scope: RefObject<HTMLElement | null>,
  selector: string,
  changeKey: string,
  compact = false,
) {
  useGSAP(() => {
    const root = scope.current;
    if (!root || !selector) return;
    const media = gsap.matchMedia();
    media.add("(prefers-reduced-motion: no-preference)", () => {
      const targets = Array.from(root.querySelectorAll<HTMLElement>(selector)).filter(element =>
        element.getClientRects().length > 0 && !element.contains(document.activeElement),
      );
      if (!targets.length) return;
      gsap.from(targets, {
        y: compact ? 9 : 20,
        autoAlpha: 0,
        duration: compact ? 0.3 : 0.52,
        stagger: { each: compact ? 0.035 : 0.065, amount: compact ? 0.12 : 0.26 },
        ease: "power2.out",
        clearProps: "transform,opacity,visibility",
      });
    }, root);
    return () => media.revert();
  }, { scope, dependencies: [selector, changeKey, compact], revertOnUpdate: true });
}
