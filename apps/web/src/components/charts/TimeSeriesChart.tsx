"use client";

/**
 * Time series — change over time, and change against a threshold.
 *
 * The one thing this file is careful about above everything else: a `null`
 * sample is a gap in the line, never a point at zero. A flat line along the
 * bottom of a latency chart is read as "idle", and a missing scrape is not
 * that. ECharts does this correctly for `null` values with
 * `connectNulls: false`, and the option below never sets it true.
 *
 * Threshold bands come from the caller (the API's thresholds, or configured
 * policy) and are drawn as a `markLine` with its value labelled, so the
 * reader can see both where the limit is and how far away the data is.
 *
 * Event markers — deployments, changes — are `markLine` entries on the time
 * axis and only appear when the caller has real events with real timestamps.
 */

import { useMemo, useState } from "react";

import {
  ChartDataTable,
  ChartFrame,
  ChartLegend,
  type ChartStatus,
  type ChartWindow,
} from "@/components/charts/ChartFrame";
import {
  AXIS_STYLE,
  areaGradient,
  baseOption,
  chartFontFamily,
  tooltipHeader,
  tooltipRow,
  withAlpha,
  type EChartsOption,
} from "@/components/charts/options";
import { EChart } from "@/components/charts/LazyChart";
import { formatTimeAxis, formatUnit, formatUtc } from "@/lib/design/format";
import type { Thresholds } from "@/lib/design/status";
import { SERIES_DASH, SERIES_LIMIT, SERIES_TOKENS, type Tokens } from "@/lib/design/tokens";

export interface TimeSeries {
  name: string;
  /** [epoch milliseconds, value or null]. */
  points: [number, number | null][];
}

export interface TimeMarker {
  at: number;
  label: string;
}

export function TimeSeriesChart({
  title,
  question,
  unit,
  series,
  window: chartWindow,
  status,
  asOf,
  freshness,
  partial,
  correlationId,
  onRetry,
  thresholds,
  markers,
  area = false,
  height = 220,
  emptyDescription,
  errorDescription,
  actions,
  deterministic,
}: {
  title: React.ReactNode;
  question?: React.ReactNode;
  unit: string;
  series: TimeSeries[];
  window?: ChartWindow | null;
  status: ChartStatus;
  asOf?: string | null;
  freshness?: string | null;
  partial?: boolean;
  correlationId?: string;
  onRetry?: () => void;
  thresholds?: Thresholds | null;
  markers?: TimeMarker[];
  area?: boolean;
  height?: number;
  emptyDescription?: string;
  errorDescription?: string;
  actions?: React.ReactNode;
  deterministic?: boolean;
}) {
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set());

  const shown = useMemo(() => series.slice(0, SERIES_LIMIT), [series]);
  const visible = useMemo(
    () => shown.filter((entry) => !hidden.has(entry.name)),
    [shown, hidden],
  );

  const windowSeconds = useMemo(() => {
    const all = shown.flatMap((entry) => entry.points.map(([ts]) => ts));
    if (all.length < 2) return 3600;
    return (Math.max(...all) - Math.min(...all)) / 1000;
  }, [shown]);

  const summaries = useMemo(
    () =>
      shown.map((entry, index) => {
        const last = [...entry.points].reverse().find(([, value]) => value !== null);
        return { name: entry.name, latest: last?.[1] ?? null, slot: index };
      }),
    [shown],
  );

  const gaps = useMemo(
    () => shown.reduce((total, entry) => total + entry.points.filter(([, v]) => v === null).length, 0),
    [shown],
  );

  const build = useMemo(
    () =>
      (tokens: Tokens, animate: boolean): EChartsOption => {
        const base = baseOption(tokens, animate);
        const axis = AXIS_STYLE(tokens);
        const colorOf = (slot: number) => tokens[SERIES_TOKENS[slot % SERIES_TOKENS.length]];
        // A soft wash under the lines. Strong under a lone series, a whisper
        // under a few, and none once the washes would stack into mud.
        const washStrength =
          visible.length === 1 ? (area ? 0.28 : 0.2) : visible.length <= 3 ? 0.08 : 0;
        const pill = (color: string) => ({
          color,
          backgroundColor: tokens["surface-1"],
          borderColor: withAlpha(color, 0.45),
          borderWidth: 1,
          borderRadius: 999,
          padding: [2, 7],
          fontSize: 10,
          fontWeight: 600 as const,
          fontFamily: chartFontFamily(),
        });
        return {
          ...base,
          grid: { left: 4, right: 16, top: markers?.length ? 30 : 16, bottom: 2, containLabel: true },
          tooltip: {
            ...(base.tooltip as object),
            trigger: "axis",
            formatter: (params: unknown) => {
              const list = Array.isArray(params) ? params : [params];
              const first = list[0] as { value?: [number, number | null] } | undefined;
              const stamp = first?.value?.[0];
              const rows = list
                .map((item) => {
                  const point = item as {
                    seriesName?: string;
                    value?: [number, number | null];
                    color?: string;
                  };
                  const value = point.value?.[1];
                  return tooltipRow(
                    tokens,
                    point.color ?? tokens["series-1"],
                    point.seriesName ?? "",
                    formatUnit(value ?? null, unit),
                  );
                })
                .join("");
              return `${stamp ? tooltipHeader(tokens, formatUtc(new Date(stamp))) : ""}${rows}`;
            },
          },
          xAxis: {
            type: "time",
            ...axis,
            boundaryGap: false,
            splitLine: { show: false },
            axisLabel: {
              ...axis.axisLabel,
              formatter: (value: number) => formatTimeAxis(value, windowSeconds),
            },
          },
          yAxis: {
            type: "value",
            ...axis,
            axisLine: { show: false },
            splitNumber: 4,
            axisLabel: {
              ...axis.axisLabel,
              formatter: (value: number) => formatUnit(value, unit, { compact: true }),
            },
          },
          series: [
            ...visible.map((entry) => {
              const slot = shown.findIndex((candidate) => candidate.name === entry.name);
              // Resolved value, not `var(--series-n)`: the canvas renderer
              // cannot read custom properties, and an unparsed colour falls
              // back to ECharts' own palette — which is how four series end
              // up sharing one line colour.
              const color = colorOf(slot);
              const dash = SERIES_DASH[slot % SERIES_DASH.length];
              return {
                name: entry.name,
                type: "line" as const,
                data: entry.points,
                // Smoothed, but monotone along x: a curve that overshoots a
                // sample would draw a value that was never measured.
                smooth: 0.35,
                smoothMonotone: "x" as const,
                showSymbol: false,
                symbol: "circle",
                symbolSize: 9,
                // A null sample stays a hole in the line.
                connectNulls: false,
                lineStyle: {
                  width: visible.length > 3 ? 2 : 2.5,
                  color,
                  type: dash ?? "solid",
                  cap: "round" as const,
                  join: "round" as const,
                },
                itemStyle: { color, borderColor: tokens["surface-1"], borderWidth: 2 },
                areaStyle:
                  washStrength > 0
                    ? { color: areaGradient(color, washStrength), opacity: 1 }
                    : undefined,
                emphasis: {
                  focus: "series" as const,
                  lineStyle: { width: 3 },
                  itemStyle: { borderColor: tokens["surface-1"], borderWidth: 2.5 },
                },
                blur: { lineStyle: { opacity: 0.25 }, areaStyle: { opacity: 0.1 } },
                markLine:
                  slot === 0 && (thresholds || markers?.length)
                    ? {
                        silent: true,
                        symbol: "none",
                        animation: animate,
                        label: {
                          color: tokens["text-muted"],
                          fontSize: 10,
                          position: "insideEndTop" as const,
                        },
                        data: [
                          ...(thresholds
                            ? [
                                {
                                  yAxis: thresholds.warn,
                                  lineStyle: {
                                    color: tokens["status-warning"],
                                    type: [5, 4],
                                    width: 1.25,
                                    opacity: 0.9,
                                  },
                                  label: {
                                    ...pill(tokens["status-warning"]),
                                    position: "insideEndTop" as const,
                                    formatter: `warn ${formatUnit(thresholds.warn, unit)}`,
                                  },
                                },
                                {
                                  yAxis: thresholds.critical,
                                  lineStyle: {
                                    color: tokens["status-critical"],
                                    type: [5, 4],
                                    width: 1.25,
                                    opacity: 0.9,
                                  },
                                  label: {
                                    ...pill(tokens["status-critical"]),
                                    position: "insideEndTop" as const,
                                    formatter: `critical ${formatUnit(thresholds.critical, unit)}`,
                                  },
                                },
                              ]
                            : []),
                          ...(markers ?? []).map((marker) => ({
                            xAxis: marker.at,
                            lineStyle: {
                              color: tokens["status-info"],
                              type: [2, 3],
                              width: 1.5,
                            },
                            label: {
                              ...pill(tokens["status-info"]),
                              position: "end" as const,
                              formatter: marker.label,
                            },
                          })),
                        ],
                      }
                    : undefined,
              };
            }),
          ],
        };
      },
    [visible, shown, unit, area, thresholds, markers, windowSeconds],
  );

  const categories = useMemo(
    () => (shown[0]?.points ?? []).map(([ts]) => formatUtc(new Date(ts))),
    [shown],
  );

  const summaryText = useMemo(() => {
    if (shown.length === 0) return null;
    const parts = summaries.map(
      (entry) => `${entry.name} latest ${formatUnit(entry.latest, unit)}`,
    );
    const gapNote = gaps > 0 ? `; ${gaps} missing sample${gaps === 1 ? "" : "s"} shown as gaps` : "";
    const extra =
      series.length > shown.length ? `; ${series.length - shown.length} further series not drawn` : "";
    return `${shown.length} series. ${parts.join(", ")}${gapNote}${extra}.`;
  }, [summaries, shown, series.length, unit, gaps]);

  return (
    <ChartFrame
      title={title}
      question={question}
      unit={unit}
      window={chartWindow}
      status={status}
      asOf={asOf}
      freshness={freshness}
      partial={partial}
      correlationId={correlationId}
      onRetry={onRetry}
      emptyDescription={emptyDescription}
      errorDescription={errorDescription}
      height={height}
      actions={actions}
      summary={summaryText}
      legend={
        <ChartLegend
          series={summaries}
          unit={unit}
          hidden={hidden}
          onToggle={(name) =>
            setHidden((previous) => {
              const next = new Set(previous);
              if (next.has(name)) next.delete(name);
              // The last visible series cannot be hidden: an empty plot area
              // reads as "no data", which would be a lie.
              else if (next.size < shown.length - 1) next.add(name);
              return next;
            })
          }
        />
      }
      table={
        <ChartDataTable
          categories={categories}
          series={shown.map((entry) => ({
            name: entry.name,
            values: entry.points.map(([, value]) => value),
          }))}
          unit={unit}
        />
      }
    >
      <EChart
        build={build}
        deps={[build]}
        height={height}
        deterministic={deterministic}
        ariaLabel={`${typeof title === "string" ? title : "Time series"}. ${summaryText ?? ""}`}
      />
    </ChartFrame>
  );
}
