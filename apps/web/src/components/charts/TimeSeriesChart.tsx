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
import { AXIS_STYLE, baseOption, type EChartsOption } from "@/components/charts/echarts";
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
        return {
          ...base,
          grid: { left: 8, right: 16, top: 12, bottom: 4, containLabel: true },
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
                  return `<div style="display:flex;gap:8px;align-items:center;justify-content:space-between">
<span style="display:inline-flex;gap:6px;align-items:center"><span style="width:8px;height:2px;border-radius:2px;background:${point.color}"></span>${point.seriesName ?? ""}</span>
<span style="font-variant-numeric:tabular-nums">${formatUnit(value ?? null, unit)}</span></div>`;
                })
                .join("");
              return `<div style="font-variant-numeric:tabular-nums;margin-bottom:4px;opacity:.8">${
                stamp ? formatUtc(new Date(stamp)) : ""
              }</div>${rows}`;
            },
          },
          xAxis: {
            type: "time",
            ...axis,
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
              const color = tokens[SERIES_TOKENS[slot % SERIES_TOKENS.length]];
              const dash = SERIES_DASH[slot % SERIES_DASH.length];
              return {
                name: entry.name,
                type: "line" as const,
                data: entry.points,
                showSymbol: false,
                symbol: "circle",
                symbolSize: 6,
                // A null sample stays a hole in the line.
                connectNulls: false,
                lineStyle: { width: 2, color, type: dash ?? "solid" },
                itemStyle: { color },
                areaStyle: area && visible.length === 1 ? { opacity: 0.12, color } : undefined,
                emphasis: { focus: "series" as const },
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
                                    type: "dashed" as const,
                                  },
                                  label: { formatter: `warn ${formatUnit(thresholds.warn, unit)}` },
                                },
                                {
                                  yAxis: thresholds.critical,
                                  lineStyle: {
                                    color: tokens["status-critical"],
                                    type: "dashed" as const,
                                  },
                                  label: {
                                    formatter: `critical ${formatUnit(thresholds.critical, unit)}`,
                                  },
                                },
                              ]
                            : []),
                          ...(markers ?? []).map((marker) => ({
                            xAxis: marker.at,
                            lineStyle: { color: tokens["status-info"], type: "solid" as const },
                            label: { formatter: marker.label, rotate: 90 },
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
