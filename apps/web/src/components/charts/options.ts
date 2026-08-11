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

export function baseOption(tokens: Tokens, animate: boolean): EChartsOption {
  return {
    animation: animate,
    animationDuration: 240,
    animationEasing: "cubicOut",
    textStyle: {
      fontFamily:
        'var(--font-inter), ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
      fontSize: 11,
      color: tokens["text-secondary"],
    },
    grid: { left: 8, right: 12, top: 8, bottom: 4, containLabel: true },
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
      backgroundColor: tokens["chart-tooltip"],
      borderColor: tokens["border-strong"],
      borderWidth: 1,
      padding: [6, 8],
      textStyle: { color: tokens["chart-tooltip-text"], fontSize: 11 },
      extraCssText: "box-shadow: var(--shadow-overlay); border-radius: 8px;",
      axisPointer: {
        type: "line",
        lineStyle: { color: tokens["border-strong"], width: 1, type: "dashed" },
        crossStyle: { color: tokens["border-strong"] },
        label: { backgroundColor: tokens["chart-tooltip"], color: tokens["chart-tooltip-text"] },
      },
    },
  };
}

export const AXIS_STYLE = (tokens: Tokens) => ({
  axisLine: { show: true, lineStyle: { color: tokens["border-subtle"] } },
  axisTick: { show: false },
  axisLabel: { color: tokens["chart-axis"], fontSize: 11, hideOverlap: true },
  splitLine: { show: true, lineStyle: { color: tokens["chart-grid"], type: "solid" as const } },
});
