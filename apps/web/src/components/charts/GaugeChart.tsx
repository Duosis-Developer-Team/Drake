"use client";

/**
 * A single ratio, drawn as a progress arc rather than a bar.
 *
 * `CapacityBar` (CategoryCharts.tsx) is right that a gauge is the wrong
 * choice for a dense list of ratios — it spends a whole panel's worth of
 * space to say one number. This component exists for the opposite case: the
 * one or two numbers a screen leads with (a hero's "how much of the picture
 * is visible", a fleet's aggregate health), where that same space cost is
 * the point — it is the thing the reader's eye should land on first.
 *
 * Colour is never the only channel: `label` renders as exact, real text
 * inside the arc, the same value the arc encodes, not an approximation of it.
 */

import { EChart } from "@/components/charts/LazyChart";
import type { EChartsOption } from "@/components/charts/options";

export function ProgressGauge({
  value,
  label,
  caption,
  color,
  trackColor,
  textColor,
  captionColor,
  size = 132,
  ariaLabel,
}: {
  /** 0-100. The arc's sweep — always the real ratio, never a stand-in. */
  value: number;
  /** Exact text drawn in the arc's center, e.g. "5 / 5" or "92%". */
  label: string;
  caption?: string;
  color: string;
  trackColor: string;
  textColor: string;
  captionColor?: string;
  size?: number;
  ariaLabel: string;
}) {
  const build = (): EChartsOption => ({
    animationDuration: 700,
    animationEasing: "cubicOut",
    series: [
      {
        type: "gauge",
        startAngle: 220,
        endAngle: -40,
        min: 0,
        max: 100,
        radius: "100%",
        pointer: { show: false },
        progress: {
          show: true,
          width: 10,
          roundCap: true,
          itemStyle: { color },
        },
        axisLine: {
          lineStyle: { width: 10, color: [[1, trackColor]] },
        },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: { show: false },
        anchor: { show: false },
        detail: {
          valueAnimation: true,
          offsetCenter: [0, caption ? "-4%" : "8%"],
          formatter: () => label,
          color: textColor,
          fontSize: Math.round(size * 0.19),
          fontWeight: 700,
          fontFamily: "inherit",
        },
        title: caption
          ? {
              offsetCenter: [0, "32%"],
              color: captionColor ?? textColor,
              fontSize: 11,
              fontFamily: "inherit",
            }
          : undefined,
        data: [{ value, name: caption ?? "" }],
      },
    ],
  });

  return (
    <EChart
      height={size}
      ariaLabel={ariaLabel}
      deps={[value, label, caption, color, trackColor, textColor]}
      build={build}
    />
  );
}
