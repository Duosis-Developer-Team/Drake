"use client";

/**
 * The ECharts adapter.
 *
 * One place owns the lifecycle, so no screen has to get it right:
 *
 *   Registration is explicit. `echarts/core` plus exactly the chart and
 *   component modules Drake draws — importing `echarts` whole would pull the
 *   map, gauge, graph, tree and calendar renderers into a bundle that never
 *   draws one.
 *
 *   The instance is disposed on unmount and the ResizeObserver disconnected
 *   with it. An ECharts instance holds a canvas, a raf loop and its own
 *   listeners; leaking one per route change is how a monitoring tab left open
 *   overnight ends up owning a gigabyte.
 *
 *   Resize is observed on the container, not on `window`. The sidebar
 *   collapsing changes the chart's width without the window resizing at all.
 *
 *   Theme comes from the live CSS custom properties, and the chart re-themes
 *   when the `dark` class flips — without refetching, and without the caller
 *   knowing a theme exists.
 *
 *   Animation is off under `prefers-reduced-motion` and off when
 *   `deterministic` is set, which is what makes a screenshot comparable.
 *
 * The option BUILDERS live in `./options`, which imports ECharts only as a
 * type. This module is the sole value-importer of the engine, and the only
 * way in is the dynamic import in `LazyChart` — so a screen that frames a
 * chart without rendering one never downloads it.
 */

import * as echarts from "echarts/core";
import { BarChart, CustomChart, HeatmapChart, LineChart, PieChart } from "echarts/charts";
import {
  DatasetComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TooltipComponent,
  VisualMapComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";

import { readTokens, type ThemeMode, type Tokens } from "@/lib/design/tokens";
import type { EChartsOption } from "@/components/charts/options";

echarts.use([
  BarChart,
  CustomChart,
  HeatmapChart,
  LineChart,
  PieChart,
  DatasetComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TooltipComponent,
  VisualMapComponent,
  CanvasRenderer,
]);

function currentMode(): ThemeMode {
  if (typeof document === "undefined") return "light";
  return document.documentElement.classList.contains("dark") ? "dark" : "light";
}

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * Mount an ECharts instance and keep it in sync.
 *
 * `build` receives the resolved tokens and whether animation is allowed, and
 * returns the option. It is called again on theme change and on every
 * dependency change, so it must be a pure function of its inputs.
 */
export function EChart({
  build,
  deps,
  height,
  ariaLabel,
  deterministic = false,
  onReady,
  className = "",
}: {
  build: (tokens: Tokens, animate: boolean) => EChartsOption;
  /** Values `build` closes over. Changing any of them re-renders the option. */
  deps: readonly unknown[];
  height: number;
  /** What the chart shows, in a sentence. Never omitted. */
  ariaLabel: string;
  /** Disables animation regardless of motion preference, for screenshots. */
  deterministic?: boolean;
  onReady?: (instance: echarts.ECharts) => void;
  className?: string;
}) {
  const container = useRef<HTMLDivElement | null>(null);
  const instance = useRef<echarts.ECharts | null>(null);
  const buildRef = useRef(build);
  buildRef.current = build;
  const onReadyRef = useRef(onReady);
  onReadyRef.current = onReady;

  // Mount, resize, dispose. Runs once: the option is applied separately so a
  // data change never tears down the canvas.
  useEffect(() => {
    const node = container.current;
    if (!node) return;
    const chart = echarts.init(node, undefined, { renderer: "canvas" });
    instance.current = chart;
    onReadyRef.current?.(chart);

    const observer = new ResizeObserver(() => {
      // A panel animating open reports width 0 for a frame; resizing to it
      // makes ECharts drop the canvas and it never comes back.
      if (node.clientWidth > 0 && node.clientHeight > 0) chart.resize();
    });
    observer.observe(node);

    return () => {
      observer.disconnect();
      chart.dispose();
      instance.current = null;
    };
  }, []);

  // Option, and re-themeing when the `dark` class flips.
  useEffect(() => {
    const chart = instance.current;
    if (!chart) return;

    const apply = () => {
      if (instance.current !== chart || chart.isDisposed()) return;
      const animate = !deterministic && !prefersReducedMotion();
      chart.setOption(buildRef.current(readTokens(currentMode()), animate), {
        notMerge: true,
        lazyUpdate: false,
      });
    };
    apply();

    const observer = new MutationObserver(apply);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });
    return () => observer.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deterministic, ...deps]);

  return (
    <div
      ref={container}
      role="img"
      aria-label={ariaLabel}
      data-testid="echart"
      style={{ height }}
      className={`w-full ${className}`}
    />
  );
}
