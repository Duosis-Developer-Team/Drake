"use client";

/**
 * A live CSS media-query match.
 *
 * Used where a screen's actual DOM/tab order must change with viewport width
 * — not just its visual position — since a CSS `order` utility reorders
 * what a reader sees but never what Tab reaches. Safe wherever
 * `window.matchMedia` may not exist: SSR, and this repo's jsdom test
 * environment does not implement it. Mirrors the same guard
 * `lib/theme.ts`'s `watchSystemTheme` uses, and defaults to `false`
 * (the desktop order) in that case rather than throwing.
 */
import { useEffect, useState } from "react";

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
    return window.matchMedia(query).matches;
  });

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const mql = window.matchMedia(query);
    const handler = (event: MediaQueryListEvent) => setMatches(event.matches);
    setMatches(mql.matches);
    mql.addEventListener("change", handler);
    return () => mql.removeEventListener("change", handler);
  }, [query]);

  return matches;
}
