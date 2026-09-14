/**
 * The shared shape of every Drake chart option.
 *
 * Deliberately in its own module with a TYPE-ONLY import of ECharts. The
 * option builders are needed by every chart component, but the engine is
 * needed only when a canvas actually renders — and a value import here would
 * pull ~250 kB into the route chunk of every screen that merely *frames* a
 * chart, defeating the dynamic import in `LazyChart`.
 *
 * Axis, grid, tooltip and legend styling live here rather than in each chart,
 * so a tooltip on the overview and a tooltip on a service detail are the same
 * object.
 */

import type { EChartsCoreOption } from "echarts/core";

import type { Tokens } from "@/lib/design/tokens";

export type EChartsOption = EChartsCoreOption;

const FONT_FALLBACK =
  'Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';

/**
 * The real font stack, resolved.
 *
 * The canvas renderer cannot read `var(--font-inter)`: a font string with a
 * custom property in it is invalid, and the canvas silently falls back to
 * 10px sans-serif — which is why axis labels used to look like a different
 * product from the DOM around them. The body's computed family is the
 * resolved next/font name, so the canvas and the page finally agree.
 */
export function chartFontFamily(): string {
  if (typeof document === "undefined" || typeof getComputedStyle !== "function") {
    return FONT_FALLBACK;
  }
  const family = getComputedStyle(document.body).fontFamily;
  return family && !family.includes("var(") ? family : FONT_FALLBACK;
}

/**
 * A resolved colour at an opacity, for gradients and soft fills.
 *
 * Tokens resolve to hex; anything else (a colour the stylesheet wrote in
 * another notation) is returned untouched rather than guessed at.
 */
export function withAlpha(color: string, alpha: number): string {
  const hex = color.trim().replace("#", "");
  const full =
    hex.length === 3
      ? hex
          .split("")
          .map((c) => c + c)
          .join("")
      : hex;
  if (!/^[0-9a-f]{6}$/i.test(full)) return color;
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16));
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/** A soft vertical wash under a line: the series colour fading to nothing. */
export function areaGradient(color: string, strength: number) {
  return {
    type: "linear" as const,
    x: 0,
    y: 0,
    x2: 0,
    y2: 1,
    colorStops: [
      { offset: 0, color: withAlpha(color, strength) },
      { offset: 0.75, color: withAlpha(color, strength * 0.25) },
      { offset: 1, color: withAlpha(color, 0) },
    ],
  };
}

/** HTML-escape a label before it goes into a tooltip. Series names come from
 *  the API (pod names, route labels) and are not markup. */
export function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** The tooltip card's header line — the instant or category being read. */
export function tooltipHeader(tokens: Tokens, text: string): string {
  return `<div style="font-size:11px;line-height:16px;color:${tokens["text-muted"]};font-variant-numeric:tabular-nums;margin-bottom:6px;letter-spacing:0.01em">${escapeHtml(
    text,
  )}</div>`;
}

/** One row of the tooltip card: a colour dot, the series name, the value. */
export function tooltipRow(tokens: Tokens, color: string, name: string, value: string): string {
  return `<div style="display:flex;align-items:center;justify-content:space-between;gap:20px;min-width:140px;line-height:22px">
<span style="display:inline-flex;align-items:center;gap:8px;min-width:0;color:${tokens["text-secondary"]};font-size:12px">
<span style="width:8px;height:8px;border-radius:9999px;background:${color};box-shadow:0 0 0 3px ${withAlpha(color, 0.18)};flex-shrink:0"></span>
<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:200px">${escapeHtml(name)}</span></span>
<span style="font-variant-numeric:tabular-nums;font-weight:600;font-size:12px;color:${tokens["text-primary"]}">${escapeHtml(value)}</span></div>`;
}

export function baseOption(tokens: Tokens, animate: boolean): EChartsOption {
  const fontFamily = chartFontFamily();
  return {
    animation: animate,
    animationDuration: 420,
    animationDurationUpdate: 240,
    animationEasing: "cubicOut",
    textStyle: {
      fontFamily,
      fontSize: 11,
      color: tokens["text-secondary"],
    },
    grid: { left: 8, right: 12, top: 12, bottom: 4, containLabel: true },
    legend: {
      // Off everywhere: the frame renders a real DOM legend instead, which is
      // reachable by keyboard, carries each series' latest value, and survives
      // the canvas failing to render at all.
      show: false,
    },
    tooltip: {
      // Confined to the chart box so it cannot spill past the viewport on a
      // narrow screen or inside a scrolled panel.
      confine: true,
      appendToBody: false,
      // A card, not a black box: the surface, a hairline and the overlay
      // shadow, so it reads as part of the same product in both themes.
      backgroundColor: tokens["surface-1"],
      borderColor: tokens["border-subtle"],
      borderWidth: 1,
      padding: [10, 12],
      textStyle: { color: tokens["text-primary"], fontSize: 12, fontFamily },
      extraCssText:
        "box-shadow: var(--shadow-overlay); border-radius: 14px; backdrop-filter: blur(6px);",
      transitionDuration: 0.15,
      axisPointer: {
        type: "line",
        lineStyle: { color: tokens["border-strong"], width: 1, type: [3, 3] },
        crossStyle: { color: tokens["border-strong"] },
        label: {
          backgroundColor: tokens["surface-1"],
          color: tokens["text-primary"],
          borderColor: tokens["border-subtle"],
          borderWidth: 1,
          borderRadius: 6,
          fontFamily,
        },
      },
    },
  };
}

export const AXIS_STYLE = (tokens: Tokens) => ({
  axisLine: { show: true, lineStyle: { color: tokens["border-subtle"] } },
  axisTick: { show: false },
  axisLabel: {
    color: tokens["chart-axis"],
    fontSize: 11,
    fontFamily: chartFontFamily(),
    hideOverlap: true,
    margin: 12,
  },
  splitLine: {
    show: true,
    lineStyle: { color: tokens["chart-grid"], type: [4, 4] as number[], width: 1 },
  },
});
