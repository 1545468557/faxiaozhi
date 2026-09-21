"use client";

import { useRef, type ReactNode } from "react";
import gsap from "gsap";
import { useGSAP } from "@gsap/react";

gsap.registerPlugin(useGSAP);

export function AskEntrance({ children }: { children: ReactNode }) {
  const scope = useRef<HTMLDivElement>(null);
  useGSAP(() => {
    let media: ReturnType<typeof gsap.matchMedia> | undefined;
    const play = () => {
      media?.revert();
      media = gsap.matchMedia();
      media.add("(prefers-reduced-motion: no-preference)", () => {
        const star = scope.current?.querySelector(".ask-star");
        const title = scope.current?.querySelector("h1");
        if (!star || !title) return;
        gsap.fromTo(star, { x: window.innerWidth < 720 ? 100 : 210, autoAlpha: 0 }, { x: 0, autoAlpha: 1, duration: .62, ease: "power3.out", clearProps: "transform,opacity,visibility" });
        gsap.fromTo(title, { y: 24, autoAlpha: 0 }, { y: 0, autoAlpha: 1, duration: .68, ease: "power3.out", clearProps: "transform,opacity,visibility" });
        gsap.fromTo(star.querySelector("svg"), { rotation: 24, scale: .82 }, { rotation: 0, scale: 1, duration: .62, ease: "power3.out", clearProps: "transform" });
      });
    };
    play();
    window.addEventListener("faxiaozhi:ask-entrance", play);
    return () => { window.removeEventListener("faxiaozhi:ask-entrance", play); media?.revert(); };
  }, { scope });
  return <div className="v2-home-head" ref={scope}>{children}</div>;
}
