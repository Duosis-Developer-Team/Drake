"use client";

/**
 * The non-time charts: sorted bar, stacked composition, and capacity.
 *
 * `SortedBarChart` is the answer to "how is this distributed" for anything
 * with more than about five categories — resource kinds, namespaces, top
 * consumers. Not a pie: a reader cannot rank fourteen wedges, and Drake's
 * distributions are long-tailed. Everything past `limit` is folded into a
 * single labelled "other" row rather than dropped, so the bars still add up.
 *
 * `StackedBarChart` is for composition where the parts genuinely make a whole
 * and the categories are few — severity across a handful of levels, health
 * across four buckets. Segments carry a 2px surface gap so adjacent fills do
 * not blend into one another.
 *
 * `CapacityBar` is a single ratio, drawn as a bar with its threshold marked.
 * Not a gauge: a gauge spends a quarter of a panel saying one number.
 */

import { useMemo } from "react";

import {
  ChartDataTable,
  ChartFrame,
  type ChartStatus,
} from "@/components/charts/ChartFrame";
import { AXIS_STYLE, baseOption, type EChartsOption } from "@/components/charts/options";
import { EChart } from "@/components/charts/LazyChart";
import { formatUnit } from "@/lib/design/format";
import type { StatusTone } from "@/lib/design/status";
import { toneSpec } from "@/lib/design/status";
import { SERIES_TOKENS, type TokenName, type Tokens } from "@/lib/design/tokens";

export interface Category {
  name: string;
  value: number;
  /** Semantic colour when the category IS a state (health, severity). */
  tone?: StatusTone;
  href?: string;
}

const OTHER = "other";

/** `--status-warning` -> `status-warning`, so a tone can index the token map. */
function tokenName(cssVariable: string): TokenName {
  return cssVariable.replace(/^--/, "") as TokenName;
}

export function SortedBarChart({
  title,
  question,
  unit,
  categories,
  status,
  asOf,
  freshness,
  correlationId,
  onRetry,
  emptyDescription,
  limit = 12,
  height,
  deterministic,
}: {
  title: React.ReactNode;
  question?: React.ReactNode;
  unit: string;
  categories: Category[];
  status: ChartStatus;
  asOf?: string | null;
  freshness?: string | null;
  correlationId?: string;
  onRetry?: () => void;
  emptyDescription?: string;
  limit?: number;
  height?: number;
  deterministic?: boolean;
}) {
  const rows = useMemo(() => {
    const sorted = [...categories].sort((a, b) => b.value - a.value);
    if (sorted.length <= limit) return sorted;
    const head = sorted.slice(0, limit - 1);
    const tail = sorted.slice(limit - 1);
    return [
      ...head,
      {
        name: `${OTHER} (${tail.length} more)`,
        value: tail.reduce((total, entry) => total + entry.value, 0),
      },
    ];
  }, [categories, limit]);

  // Horizontal bars: category names are words, and words fit along a y axis.
  const plotted = useMemo(() => [...rows].reverse(), [rows]);
  const resolvedHeight = height ?? Math.max(140, plotted.length * 26 + 24);

  const build = useMemo(
    () =>
      (tokens: Tokens, animate: boolean): EChartsOption => {
        const base = baseOption(tokens, animate);
        const axis = AXIS_STYLE(tokens);
        return {
          ...base,
          grid: { left: 4, right: 48, top: 4, bottom: 4, containLabel: true },
          tooltip: {
            ...(base.tooltip as object),
            trigger: "item",
            formatter: (params: unknown) => {
              const item = params as { name?: string; value?: number };
              return `${item.name}<br/><span style="font-variant-numeric:tabular-nums">${formatUnit(
                item.value ?? null,
                unit,
              )}</span>`;
            },
          },
          xAxis: { type: "value", ...axis, axisLine: { show: false }, axisLabel: { show: false }, splitLine: { show: false } },
          yAxis: {
            type: "category",
            data: plotted.map((entry) => entry.name),
            ...axis,
            splitLine: { show: false },
            axisLabel: { ...axis.axisLabel, width: 140, overflow: "truncate" },
          },
          series: [
            {
              type: "bar",
              data: plotted.map((entry) => ({
                value: entry.value,
                itemStyle: {
                  // Resolved values: the canvas renderer cannot read CSS
                  // custom properties.
                  color: entry.tone
                    ? tokens[tokenName(toneSpec(entry.tone).token)]
                    : entry.name.startsWith(OTHER)
                      ? tokens["text-muted"]
                      : tokens[SERIES_TOKENS[0]],
                  borderRadius: [0, 4, 4, 0],
                },
              })),
              barMaxWidth: 14,
              label: {
                show: true,
                position: "right",
                color: tokens["text-secondary"],
                fontSize: 11,
                formatter: (params: { value: number }) =>
                  formatUnit(params.value, unit, { compact: true }),
              },
            },
          ],
        };
      },
    [plotted, unit],
  );

  const total = rows.reduce((sum, entry) => sum + entry.value, 0);
  const summary = `${rows.length} categories, ${formatUnit(total, unit)} total. Largest: ${
    rows[0] ? `${rows[0].name} at ${formatUnit(rows[0].value, unit)}` : "none"
  }.`;

  return (
    <ChartFrame
      title={title}
      question={question}
      unit={unit}
      status={status}
      asOf={asOf}
      freshness={freshness}
      correlationId={correlationId}
      onRetry={onRetry}
      emptyDescription={emptyDescription}
      height={resolvedHeight}
      summary={summary}
      table={
        <ChartDataTable
          categoryHeader="Category"
          categories={rows.map((entry) => entry.name)}
          series={[{ name: "Count", values: rows.map((entry) => entry.value) }]}
          unit={unit}
        />
      }
    >
      <EChart
        build={build}
        deps={[build]}
        height={resolvedHeight}
        deterministic={deterministic}
        ariaLabel={`${typeof title === "string" ? title : "Distribution"}. ${summary}`}
      />
    </ChartFrame>
  );
}

export { CapacityBar, CompositionBar } from "@/components/charts/InlineBars";
