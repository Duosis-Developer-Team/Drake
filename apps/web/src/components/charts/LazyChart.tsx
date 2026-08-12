"use client";

/**
 * The seam that keeps ECharts out of routes that do not draw.
 *
 * `ChartFrame` — the panel, the header, the legend, the states, the table
 * fallback — is ordinary React and stays in the route chunk. Only the canvas
 * is behind this dynamic import, so a screen whose charts are all "not
 * configured", denied or still loading never downloads the chart engine at
 * all, and a screen with no charts never references it.
 *
 * `ssr: false` because ECharts measures a real element to size its canvas.
 * There is nothing to prerender, and the skeleton it falls back to is the
 * same one the loading state uses, so no layout shifts when it arrives.
 */

import dynamic from "next/dynamic";

import { LoadingSkeleton } from "@/components/ui/states";

export const EChart = dynamic(
  () => import("@/components/charts/echarts").then((module) => module.EChart),
  {
    ssr: false,
    loading: () => <LoadingSkeleton variant="chart" label="Loading chart" />,
  },
);
