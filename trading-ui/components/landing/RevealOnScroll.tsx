"use client";

import { type CSSProperties, type ElementType, type ReactNode, useEffect, useRef, useState } from "react";

type RevealOnScrollProps = {
  children: ReactNode;
  className?: string;
  delayMs?: number;
  as?: ElementType;
  id?: string;
};

export default function RevealOnScroll({
  children,
  className = "",
  delayMs = 0,
  as: Component = "div",
  id,
}: RevealOnScrollProps) {
  const ref = useRef<HTMLElement | null>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setVisible(true);
            observer.disconnect();
            break;
          }
        }
      },
      { threshold: 0.15, rootMargin: "0px 0px -8% 0px" }
    );

    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const style: CSSProperties = { transitionDelay: `${Math.max(0, delayMs)}ms` };
  const classes = `reveal ${visible ? "is-visible" : ""} ${className}`.trim();

  return (
    <Component ref={ref as never} id={id} className={classes} style={style}>
      {children}
    </Component>
  );
}
