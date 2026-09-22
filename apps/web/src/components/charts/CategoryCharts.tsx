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
import { useLocale, useT } from "@/lib/i18n";

export interface Category {
  name: string;
  value: number;
  /** Semantic colour when the category IS a state (health, severity). */
  tone?: StatusTone;
  href?: string;
}

/** A plotted row: a category, or the fold of everything past `limit`. */
type Row = Category & { other?: boolean };

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
  const t = useT("ui");
  const { locale } = useLocale();
  const rows = useMemo((): Row[] => {
    const sorted = [...categories].sort((a, b) => b.value - a.value);
    if (sorted.length <= limit) return sorted;
    const head = sorted.slice(0, limit - 1);
    const tail = sorted.slice(limit - 1);
    return [
      ...head,
      {
        name: t("chart.other", { count: tail.length }),
        value: tail.reduce((total, entry) => total + entry.value, 0),
        other: true,
      },
    ];
  }, [categories, limit, t]);

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
                {},
                locale,
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
                    : entry.other
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
                  formatUnit(params.value, unit, { compact: true }, locale),
              },
            },
          ],
        };
      },
    [plotted, unit, locale],
  );

  const total = rows.reduce((sum, entry) => sum + entry.value, 0);
  const summary = t("chart.categorySummary", {
    count: rows.length,
    total: formatUnit(total, unit, {}, locale),
    largest: rows[0]
      ? t("chart.largestAt", { name: rows[0].name, value: formatUnit(rows[0].value, unit, {}, locale) })
      : t("chart.largestNone"),
  });

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
          categoryHeader={t("chart.categoryColumn")}
          categories={rows.map((entry) => entry.name)}
          series={[{ name: t("chart.countColumn"), values: rows.map((entry) => entry.value) }]}
          unit={unit}
        />
      }
    >
      <EChart
        build={build}
        deps={[build]}
        height={resolvedHeight}
        deterministic={deterministic}
        ariaLabel={`${typeof title === "string" ? title : t("chart.distribution")}. ${summary}`}
      />
    </ChartFrame>
  );
}

export { CapacityBar, CompositionBar } from "@/components/charts/InlineBars";
