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
import { AXIS_STYLE, EChart, baseOption, type EChartsOption } from "@/components/charts/echarts";
import { formatUnit } from "@/lib/design/format";
import type { StatusTone } from "@/lib/design/status";
import { toneSpec } from "@/lib/design/status";
import { SERIES_TOKENS, type Tokens } from "@/lib/design/tokens";

export interface Category {
  name: string;
  value: number;
  /** Semantic colour when the category IS a state (health, severity). */
  tone?: StatusTone;
  href?: string;
}

const OTHER = "other";

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
                  color: entry.tone
                    ? `var(${toneSpec(entry.tone).token})`
                    : entry.name.startsWith(OTHER)
                      ? tokens["text-muted"]
                      : `var(${SERIES_TOKENS[0]})`,
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

/**
 * One horizontal stacked bar: a composition, in a single row.
 *
 * Used where a donut would be reached for. It reads left-to-right like the
 * rest of the page, stacks into far less vertical space, and puts the counts
 * in a legend where they can be read exactly rather than estimated by angle.
 */
export function CompositionBar({
  segments,
  unit = "count",
  label,
}: {
  segments: { name: string; value: number; tone?: StatusTone; slot?: number }[];
  unit?: string;
  label: string;
}) {
  const total = segments.reduce((sum, entry) => sum + entry.value, 0);
  if (total === 0) {
    return (
      <p className="text-caption text-ink-muted" data-testid="composition-empty">
        Nothing to break down — every bucket is zero.
      </p>
    );
  }
  return (
    <div data-testid="composition-bar">
      <div
        role="img"
        aria-label={`${label}: ${segments
          .filter((entry) => entry.value > 0)
          .map((entry) => `${entry.name} ${entry.value}`)
          .join(", ")}`}
        className="flex h-2.5 w-full gap-0.5 overflow-hidden rounded-full bg-surface-3"
      >
        {segments
          .filter((entry) => entry.value > 0)
          .map((entry, index) => (
            <span
              key={entry.name}
              style={{
                width: `${(entry.value / total) * 100}%`,
                background: entry.tone
                  ? `var(${toneSpec(entry.tone).token})`
                  : `var(${SERIES_TOKENS[(entry.slot ?? index) % SERIES_TOKENS.length]})`,
              }}
            />
          ))}
      </div>
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
        {segments.map((entry, index) => (
          <li
            key={entry.name}
            className="flex items-center gap-1.5 text-micro text-ink-secondary"
          >
            <span
              aria-hidden
              className="h-2 w-2 shrink-0 rounded-full"
              style={{
                background: entry.tone
                  ? `var(${toneSpec(entry.tone).token})`
                  : `var(${SERIES_TOKENS[(entry.slot ?? index) % SERIES_TOKENS.length]})`,
              }}
            />
            {entry.name}
            <span data-tabular className="text-ink">
              {formatUnit(entry.value, unit)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * A single ratio against its capacity.
 *
 * Text first, bar second: the number is the answer and the bar is the
 * context. The threshold marks are drawn on the track so "how close are we"
 * is answerable without reading the axis.
 */
export function CapacityBar({
  label,
  used,
  total,
  unit,
  tone = "neutral",
  thresholdRatios,
}: {
  label: string;
  used: number | null;
  total: number | null;
  unit: string;
  tone?: StatusTone;
  /** Where to draw the warn/critical ticks, as fractions of the total. */
  thresholdRatios?: { warn?: number; critical?: number };
}) {
  const known = used !== null && total !== null && total > 0;
  const ratio = known ? Math.min(1, used / total) : 0;
  const spec = toneSpec(tone);
  return (
    <div data-testid="capacity-bar">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-caption text-ink-secondary">{label}</span>
        <span data-tabular className="text-caption text-ink">
          {known ? (
            <>
              {formatUnit(used, unit)} <span className="text-ink-muted">of {formatUnit(total, unit)}</span>
            </>
          ) : (
            <span className="text-ink-muted">not reported</span>
          )}
        </span>
      </div>
      <div className="relative mt-1 h-2 w-full overflow-hidden rounded-full bg-surface-3">
        {known ? (
          <span
            className={`block h-full rounded-full ${spec.dot}`}
            style={{ width: `${ratio * 100}%` }}
          />
        ) : null}
        {thresholdRatios?.warn ? (
          <span
            aria-hidden
            className="absolute top-0 h-full w-px bg-warning"
            style={{ left: `${thresholdRatios.warn * 100}%` }}
          />
        ) : null}
        {thresholdRatios?.critical ? (
          <span
            aria-hidden
            className="absolute top-0 h-full w-px bg-critical"
            style={{ left: `${thresholdRatios.critical * 100}%` }}
          />
        ) : null}
      </div>
    </div>
  );
}
