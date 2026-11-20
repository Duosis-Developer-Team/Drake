"use client";

/**
 * The detail page's table of contents: one pill per section, sticky at the
 * top while the page scrolls, marking the section in view. A section that
 * has something wrong carries a count, so the reader knows where to go
 * before scrolling there.
 *
 * Nothing here decides what is wrong; counts come from the page, which
 * reads them from the API. A section with no count says nothing — never a
 * green "0 problems" that would read as a verdict.
 */

import { useEffect, useState } from "react";

import { toneSpec, type StatusTone } from "@/lib/design/status";

export interface SectionNavItem {
  id: string;
  label: string;
  /** Shown only when present; the page passes it only for a real count. */
  badge?: string;
  tone?: StatusTone;
}

export function SectionNav({ items, label }: { items: SectionNavItem[]; label: string }) {
  const [active, setActive] = useState<string | null>(items[0]?.id ?? null);

  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const visible = new Map<string, number>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) visible.set(entry.target.id, entry.boundingClientRect.top);
          else visible.delete(entry.target.id);
        }
        // The topmost section still on screen is the one being read.
        const top = [...visible.entries()].sort((a, b) => a[1] - b[1])[0];
        if (top) setActive(top[0]);
      },
      { rootMargin: "-96px 0px -55% 0px" },
    );
    for (const item of items) {
      const element = document.getElementById(item.id);
      if (element) observer.observe(element);
    }
    return () => observer.disconnect();
  }, [items]);

  return (
    <nav
      aria-label={label}
      data-testid="section-nav"
      className="sticky top-3 z-20 -mx-1 flex max-w-full flex-wrap gap-1.5 self-start rounded-[0.625rem] bg-surface p-1.5 shadow-panel"
    >
      {items.map((item) => {
        const current = active === item.id;
        return (
          <a
            key={item.id}
            href={`#${item.id}`}
            aria-current={current ? "location" : undefined}
            className={`inline-flex items-center gap-2 rounded-[0.7rem] px-3.5 py-1.5 text-caption transition-colors ${
              current ? "bg-surface-2 font-medium text-ink" : "text-ink-secondary hover:bg-surface-hover"
            }`}
          >
            {item.label}
            {item.badge ? (
              <span
                data-tabular
                className={`rounded-full px-1.5 py-px text-micro font-medium ${toneSpec(item.tone ?? "critical").chip}`}
              >
                {item.badge}
              </span>
            ) : null}
          </a>
        );
      })}
    </nav>
  );
}
