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

/** The obsidian rail. Identical in `LIGHT_TOKENS` and `DARK_TOKENS` — see
 *  `globals.css`'s file header for why the sidebar does not flip with theme. */
const SIDEBAR_TOKENS: Pick<
  Tokens,
  | "sidebar-canvas"
  | "sidebar-surface-hover"
  | "sidebar-surface-selected"
  | "sidebar-border"
  | "sidebar-text"
  | "sidebar-text-muted"
  | "sidebar-active-rail"
> = {
  "sidebar-canvas": "#181818",
  "sidebar-surface-hover": "#202020",
  "sidebar-surface-selected": "#252525",
  "sidebar-border": "#292929",
  "sidebar-text": "#f1f1f1",
  "sidebar-text-muted": "#818181",
  "sidebar-active-rail": "#f2cf55",
};

export const LIGHT_TOKENS: Tokens = {
  canvas: "#ececec",
  "surface-1": "#f9f9f9",
  "surface-2": "#f1f1f1",
  "surface-3": "#e9e9e9",
  "surface-elevated": "#ffffff",
  "surface-hover": "#f4f4f4",
  "surface-selected": "#e7e7e7",
  "border-subtle": "#d8d8d8",
  "border-strong": "#868686",
  "text-primary": "#161616",
  "text-secondary": "#646464",
  "text-muted": "#686868",
  "text-inverse": "#f1f1f1",
  brand: "#161616",
  "brand-hover": "#2a2a2a",
  "brand-active": "#000000",
  "brand-soft": "#f1f1f1",
  "brand-accent": "#a3820d",
  "focus-ring": "#a3820d",
  "chart-grid": "#dfdfdf",
  "chart-axis": "#646464",
  "chart-tooltip": "#181818",
  "chart-tooltip-text": "#f1f1f1",
  "status-success": "#2270d8",
  "status-info": "#5165e6",
  "status-warning": "#9c671c",
  "status-critical": "#df2121",
  "status-neutral": "#727272",
  "status-unknown": "#6e7278",
  "status-stale": "#8957d4",
  ...SIDEBAR_TOKENS,
  "series-1": "#2a78d6",
  "series-2": "#eb6834",
  "series-3": "#1c8fd6",
  "series-4": "#4a3aa7",
  "series-5": "#e87ba4",
  "series-6": "#b58100",
};

export const DARK_TOKENS: Tokens = {
  canvas: "#101010",
  "surface-1": "#161616",
  "surface-2": "#262626",
  "surface-3": "#2e2e2e",
  "surface-elevated": "#1f1f1f",
  "surface-hover": "#1b1b1b",
  "surface-selected": "#333333",
  "border-subtle": "#313131",
  "border-strong": "#656565",
  "text-primary": "#f1f1f1",
  "text-secondary": "#b9b9b9",
  "text-muted": "#969696",
  "text-inverse": "#161616",
  brand: "#f1f1f1",
  "brand-hover": "#ffffff",
  "brand-active": "#d7d7d7",
  "brand-soft": "#262626",
  "brand-accent": "#f2cf55",
  "focus-ring": "#f2cf55",
  "chart-grid": "#232323",
  "chart-axis": "#b9b9b9",
  "chart-tooltip": "#181818",
  "chart-tooltip-text": "#f1f1f1",
  "status-success": "#3c82e0",
  "status-info": "#6376e9",
  "status-warning": "#d9922e",
  "status-critical": "#e85d5d",
  "status-neutral": "#8c8c8c",
  "status-unknown": "#7c8187",
  "status-stale": "#9568d8",
  ...SIDEBAR_TOKENS,
  "series-1": "#5998e5",
  "series-2": "#f59068",
  "series-3": "#43acec",
  "series-4": "#6553cb",
  "series-5": "#ef8fb3",
  "series-6": "#f2ad00",
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
