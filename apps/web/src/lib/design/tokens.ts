/**
 * The design tokens, as values.
 *
 * Screens use Tailwind classes and never touch this file. It exists for the
 * one consumer that cannot: ECharts builds a plain options object and needs
 * real colours, not class names.
 *
 * `globals.css` is the source of truth. At runtime `readTokens` resolves the
 * live custom properties off `<html>`, so a theme change is picked up without
 * anything here being edited. The literals below are the fallback for SSR and
 * for jsdom, where no stylesheet is applied — and `tokens.test.ts` parses
 * `globals.css` and fails if a literal here has drifted from it.
 */

export const TOKEN_NAMES = [
  "canvas",
  "surface-1",
  "surface-2",
  "surface-3",
  "surface-elevated",
  "surface-hover",
  "surface-selected",
  "border-subtle",
  "border-strong",
  "text-primary",
  "text-secondary",
  "text-muted",
  "text-inverse",
  "brand",
  "brand-hover",
  "brand-active",
  "brand-soft",
  "brand-accent",
  "focus-ring",
  "chart-grid",
  "chart-axis",
  "chart-tooltip",
  "chart-tooltip-text",
  "status-success",
  "status-info",
  "status-warning",
  "status-critical",
  "status-neutral",
  "status-unknown",
  "status-stale",
  "sidebar-canvas",
  "sidebar-surface-hover",
  "sidebar-surface-selected",
  "sidebar-border",
  "sidebar-text",
  "sidebar-text-muted",
  "sidebar-active-rail",
  "series-1",
  "series-2",
  "series-3",
  "series-4",
  "series-5",
  "series-6",
] as const;

export type TokenName = (typeof TOKEN_NAMES)[number];
export type Tokens = Record<TokenName, string>;
export type ThemeMode = "light" | "dark";


export const LIGHT_TOKENS: Tokens = {
  canvas: "#f6f4ef",
  "surface-1": "#ffffff",
  "surface-2": "#f3f0ea",
  "surface-3": "#ebe7df",
  "surface-elevated": "#ffffff",
  "surface-hover": "#f5f2ec",
  "surface-selected": "#ebe7e0",
  "border-subtle": "#e2ddd3",
  "border-strong": "#7a7484",
  "text-primary": "#17151c",
  "text-secondary": "#46414f",
  "text-muted": "#5f5969",
  "text-inverse": "#ffffff",
  brand: "#3f57e0",
  "brand-hover": "#3249cc",
  "brand-active": "#2a3fb8",
  "brand-soft": "#e9ecfc",
  "brand-accent": "#3f57e0",
  "focus-ring": "#3f57e0",
  "chart-grid": "#ebe7df",
  "chart-axis": "#6b6575",
  "chart-tooltip": "#17151c",
  "chart-tooltip-text": "#f6f4ef",
  "status-success": "#1b7446",
  "status-info": "#3f57e0",
  "status-warning": "#93580a",
  "status-critical": "#c0352b",
  "status-neutral": "#5f5969",
  "status-unknown": "#5d5a66",
  "status-stale": "#7646c0",
  "sidebar-canvas": "#efece5",
  "sidebar-surface-hover": "#e6e2da",
  "sidebar-surface-selected": "#e1ddd4",
  "sidebar-border": "#e0dbd1",
  "sidebar-text": "#17151c",
  "sidebar-text-muted": "#57525f",
  "sidebar-active-rail": "#3f57e0",
  "series-1": "#3f57e0",
  "series-2": "#b4842a",
  "series-3": "#1c8c9e",
  "series-4": "#c4467e",
  "series-5": "#7a5ae0",
  "series-6": "#d06a2c",
};

export const DARK_TOKENS: Tokens = {
  canvas: "#0b0a10",
  "surface-1": "#13111a",
  "surface-2": "#1a1722",
  "surface-3": "#221e2b",
  "surface-elevated": "#2a2534",
  "surface-hover": "#1c1925",
  "surface-selected": "#272231",
  "border-subtle": "#2a2633",
  "border-strong": "#7a7386",
  "text-primary": "#f2eee6",
  "text-secondary": "#cbc5d3",
  "text-muted": "#a8a2b3",
  "text-inverse": "#0b0a10",
  brand: "#8197ff",
  "brand-hover": "#98aaff",
  "brand-active": "#6d84f2",
  "brand-soft": "#1c1f3b",
  "brand-accent": "#8197ff",
  "focus-ring": "#8197ff",
  "chart-grid": "#24202d",
  "chart-axis": "#a8a2b3",
  "chart-tooltip": "#211d2a",
  "chart-tooltip-text": "#f2eee6",
  "status-success": "#4cc38a",
  "status-info": "#8fa1ff",
  "status-warning": "#f2b04b",
  "status-critical": "#f26d63",
  "status-neutral": "#a8a2b3",
  "status-unknown": "#a29dab",
  "status-stale": "#b996ec",
  "sidebar-canvas": "#0e0c14",
  "sidebar-surface-hover": "#1a1722",
  "sidebar-surface-selected": "#221e2b",
  "sidebar-border": "#1f1c27",
  "sidebar-text": "#f2eee6",
  "sidebar-text-muted": "#a8a2b3",
  "sidebar-active-rail": "#8197ff",
  "series-1": "#8197ff",
  "series-2": "#e9c46a",
  "series-3": "#4fc1d3",
  "series-4": "#e57fb0",
  "series-5": "#a58cf7",
  "series-6": "#f59068",
};

/** Fixed assignment order. A seventh series folds into "other", never a
 *  generated hue, and the order never depends on rank — a filter that drops
 *  a series must not repaint the ones that remain. */
export const SERIES_TOKENS = [
  "series-1",
  "series-2",
  "series-3",
  "series-4",
  "series-5",
  "series-6",
] as const satisfies readonly TokenName[];

export const SERIES_LIMIT = SERIES_TOKENS.length;

/**
 * Dash patterns paired with the series order.
 *
 * Two of the light-mode series sit just under 3:1 against white, and the
 * worst colour-blind pair separation in the set is dE 9.2 — comfortable, but
 * not a reason to make colour the only channel. Line charts carry the pattern
 * as well, so the series are distinguishable in greyscale, in print, and to a
 * reader who cannot separate the hues.
 */
export const SERIES_DASH: readonly (number[] | undefined)[] = [
  undefined,
  [6, 3],
  [2, 3],
  [9, 3, 2, 3],
  [1, 3],
  [12, 4],
];

export function themeTokens(mode: ThemeMode): Tokens {
  return mode === "dark" ? DARK_TOKENS : LIGHT_TOKENS;
}

/**
 * Live token values for the applied theme.
 *
 * Reads the computed custom properties so the stylesheet stays the single
 * source of truth; falls back to the literals when there is no document or
 * when the property resolves empty (jsdom applies no stylesheet).
 */
export function readTokens(mode: ThemeMode, element?: Element | null): Tokens {
  const fallback = themeTokens(mode);
  if (typeof window === "undefined" || typeof getComputedStyle !== "function") return fallback;
  const target = element ?? document.documentElement;
  const computed = getComputedStyle(target);
  const resolved = {} as Tokens;
  for (const name of TOKEN_NAMES) {
    const value = computed.getPropertyValue(`--${name}`).trim();
    resolved[name] = value || fallback[name];
  }
  return resolved;
}
