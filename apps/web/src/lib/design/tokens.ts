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
  canvas: "#f4f6f5",
  "surface-1": "#ffffff",
  "surface-2": "#f3f7f5",
  "surface-3": "#ebf0ed",
  "surface-elevated": "#ffffff",
  "surface-hover": "#eff4f1",
  "surface-selected": "#e1f1e8",
  "border-subtle": "#d3ddd8",
  "border-strong": "#7c8e85",
  "text-primary": "#0c2019",
  "text-secondary": "#48584f",
  "text-muted": "#59695f",
  "text-inverse": "#ffffff",
  brand: "#074630",
  "brand-hover": "#0a5c3f",
  "brand-active": "#05341f",
  "brand-soft": "#e1f1e8",
  "brand-accent": "#02a242",
  "focus-ring": "#00843a",
  "chart-grid": "#e7edea",
  "chart-axis": "#59695f",
  "chart-tooltip": "#0c2019",
  "chart-tooltip-text": "#f7f9f9",
  "status-success": "#067647",
  "status-info": "#175cd3",
  "status-warning": "#b54708",
  "status-critical": "#b42318",
  "status-neutral": "#4e5b64",
  "status-unknown": "#566273",
  "status-stale": "#8a6410",
  "series-1": "#2a78d6",
  "series-2": "#eb6834",
  "series-3": "#1baf7a",
  "series-4": "#4a3aa7",
  "series-5": "#e87ba4",
  "series-6": "#b58100",
};

export const DARK_TOKENS: Tokens = {
  canvas: "#032821",
  "surface-1": "#06342b",
  "surface-2": "#0a3e33",
  "surface-3": "#0e4a3c",
  "surface-elevated": "#0a3e33",
  "surface-hover": "#0f4a3d",
  "surface-selected": "#0e4535",
  "border-subtle": "#18543f",
  "border-strong": "#3e8c71",
  "text-primary": "#f7f9f9",
  "text-secondary": "#a9c0b6",
  "text-muted": "#9cb6ab",
  "text-inverse": "#062018",
  brand: "#0ecd65",
  "brand-hover": "#35dd84",
  "brand-active": "#0aa954",
  "brand-soft": "#0c4531",
  "brand-accent": "#0ecd65",
  "focus-ring": "#10cd68",
  "chart-grid": "#0f4034",
  "chart-axis": "#9cb6ab",
  "chart-tooltip": "#0e4a3c",
  "chart-tooltip-text": "#f7f9f9",
  "status-success": "#3ccb7f",
  "status-info": "#63a5fa",
  "status-warning": "#fdb022",
  "status-critical": "#f97066",
  "status-neutral": "#9aa4b2",
  "status-unknown": "#a3aebd",
  "status-stale": "#d9a441",
  "series-1": "#3987e5",
  "series-2": "#d95926",
  "series-3": "#199e70",
  "series-4": "#9085e9",
  "series-5": "#d55181",
  "series-6": "#c98500",
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
