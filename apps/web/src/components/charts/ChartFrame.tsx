"use client";

/**
 * ChartFrame — everything around a chart that makes it readable.
 *
 * A chart on its own is a picture. The frame is what turns it into a claim
 * somebody can check, and it is mandatory rather than optional because every
 * one of these has been shipped missing at least once:
 *
 *   The title is the question the chart answers, not the metric name.
 *   The unit and the window are always visible, and the window says UTC.
 *   Freshness sits in the header, so a stale chart cannot look current.
 *   A text summary states the shape in words, for a screen reader and for
 *     anyone who does not want to read a chart.
 *   A data table holds the same numbers, which is also the relief for the two
 *     light-mode series that sit under 3:1 against white.
 *   Every non-success state renders INSTEAD of the plot, never behind it — an
 *     empty axis pair with no series reads as "zero", and it is not.
 */

import { LoadingSkeleton } from "@/components/ui/states";
import {
  DeniedState,
  ErrorState,
  NoDataState,
  NotConfiguredState,
  PartialBanner,
  StaleBanner,
  UnknownState,
} from "@/components/ui/states";
import { FreshnessIndicator } from "@/components/ui/identifiers";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { MISSING, formatUnit, formatUtc } from "@/lib/design/format";
import { SERIES_TOKENS } from "@/lib/design/tokens";

export type ChartStatus =
  | "loading"
  | "ready"
  | "empty"
  | "no-data"
  | "not-configured"
  | "unknown"
  | "denied"
  | "error";

export interface ChartSeriesSummary {
  name: string;
  /** Latest value, for the summary line and the legend. */
  latest: number | null;
  /** Index into the fixed categorical order. */
  slot: number;
}

export interface ChartWindow {
  from: string;
  to: string;
  /** Printed when the server had to widen the step. */
  stepSeconds?: number;
  stepAdjusted?: boolean;
}

/**
 * The chart's own legend.
 *
 * ECharts' built-in legend is disabled across the product in favour of this
 * one: it is real DOM, so it is reachable by keyboard and by a screen reader,
 * it carries the latest value beside each series, and it survives the chart
 * failing to render at all.
 */
export function ChartLegend({
  series,
  hidden,
  onToggle,
  unit,
}: {
  series: ChartSeriesSummary[];
  hidden?: ReadonlySet<string>;
  onToggle?: (name: string) => void;
  unit: string;
}) {
  if (series.length < 2 && !onToggle) return null;
  return (
    <ul className="flex flex-wrap items-center gap-x-4 gap-y-1" data-testid="chart-legend">
      {series.map((entry) => {
        const isHidden = hidden?.has(entry.name) ?? false;
        const swatch = (
          <>
            <span
              aria-hidden
              className="h-0.5 w-4 shrink-0 rounded-full"
              style={{ background: `var(${SERIES_TOKENS[entry.slot % SERIES_TOKENS.length]})` }}
            />
            <span className={`truncate ${isHidden ? "line-through opacity-60" : ""}`}>
              {entry.name}
            </span>
            <span data-tabular className="text-ink-muted">
              {formatUnit(entry.latest, unit)}
            </span>
          </>
        );
        return (
          <li key={entry.name} className="min-w-0">
            {onToggle ? (
              <button
                type="button"
                onClick={() => onToggle(entry.name)}
                aria-pressed={!isHidden}
                className="flex min-w-0 items-center gap-1.5 rounded text-micro text-ink-secondary hover:text-ink"
              >
                {swatch}
              </button>
            ) : (
              <span className="flex min-w-0 items-center gap-1.5 text-micro text-ink-secondary">
                {swatch}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

export function ChartFrame({
  title,
  question,
  unit,
  window: chartWindow,
  status,
  asOf,
  freshness,
  partial,
  correlationId,
  onRetry,
  emptyDescription,
  summary,
  series,
  legend,
  actions,
  table,
  height = 220,
  children,
}: {
  title: React.ReactNode;
  /** One line saying what a reader can conclude from this chart. */
  question?: React.ReactNode;
  unit: string;
  window?: ChartWindow | null;
  status: ChartStatus;
  asOf?: string | null;
  freshness?: "fresh" | "stale" | "unknown" | string | null;
  partial?: boolean;
  correlationId?: string;
  onRetry?: () => void;
  emptyDescription?: string;
  /** The chart's shape in words. Rendered visibly under dense charts and
   *  always exposed to assistive technology. */
  summary?: React.ReactNode;
  series?: ChartSeriesSummary[];
  legend?: React.ReactNode;
  actions?: React.ReactNode;
  /** The same numbers as a table, behind a disclosure. */
  table?: React.ReactNode;
  height?: number;
  children: React.ReactNode;
}) {
  const meta = (
    <>
      <span>{unit}</span>
      {chartWindow ? (
        <span data-tabular>
          {formatUtc(chartWindow.from)} → {formatUtc(chartWindow.to)}
        </span>
      ) : null}
      {chartWindow?.stepAdjusted && chartWindow.stepSeconds ? (
        <span className="text-warning">step widened to {chartWindow.stepSeconds}s</span>
      ) : null}
      {asOf !== undefined ? <FreshnessIndicator asOf={asOf} state={freshness} /> : null}
    </>
  );

  return (
    <Panel data-testid="chart-frame" className="gap-2">
      <PanelHeader title={title} description={question} meta={meta} actions={actions} level={3} />

      {status === "ready" && freshness === "stale" ? <StaleBanner asOf={asOf} /> : null}
      {status === "ready" && partial ? <PartialBanner /> : null}

      {status === "loading" ? <LoadingSkeleton variant="chart" label={`Loading ${title}`} /> : null}
      {status === "denied" ? <DeniedState compact /> : null}
      {status === "not-configured" ? (
        <NotConfiguredState compact description={emptyDescription} />
      ) : null}
      {status === "unknown" ? <UnknownState compact /> : null}
      {status === "empty" || status === "no-data" ? (
        <NoDataState compact description={emptyDescription} />
      ) : null}
      {status === "error" ? (
        <ErrorState compact correlationId={correlationId} onRetry={onRetry} />
      ) : null}

      {status === "ready" ? (
        <>
          <div style={{ minHeight: height }}>{children}</div>
          {legend ?? (series ? <ChartLegend series={series} unit={unit} /> : null)}
          {summary ? (
            <p className="text-micro text-ink-muted" data-testid="chart-summary">
              {summary}
            </p>
          ) : null}
          {table ? (
            <details className="text-micro text-ink-muted">
              <summary className="cursor-pointer rounded select-none hover:text-ink">
                View as table
              </summary>
              <div className="mt-2 max-h-64 overflow-auto rounded-control border border-border">
                {table}
              </div>
            </details>
          ) : null}
        </>
      ) : null}
    </Panel>
  );
}

/**
 * The chart's numbers, as a table.
 *
 * Rendered from exactly the arrays the chart plots, so the two cannot
 * disagree. A gap in a series is a dash, never a zero.
 */
export function ChartDataTable({
  categories,
  series,
  unit,
  categoryHeader = "Time",
}: {
  categories: string[];
  series: { name: string; values: (number | null)[] }[];
  unit: string;
  categoryHeader?: string;
}) {
  return (
    <table className="w-full text-left text-micro" data-tabular>
      <thead className="bg-surface-2 text-ink-secondary">
        <tr>
          <th scope="col" className="px-2 py-1 font-medium">
            {categoryHeader}
          </th>
          {series.map((entry) => (
            <th key={entry.name} scope="col" className="px-2 py-1 text-right font-medium">
              {entry.name}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {categories.map((category, index) => (
          <tr key={category} className="border-t border-border">
            <th scope="row" className="px-2 py-1 font-normal text-ink-secondary">
              {category}
            </th>
            {series.map((entry) => {
              const value = entry.values[index];
              return (
                <td key={entry.name} className="px-2 py-1 text-right text-ink">
                  {value === null || value === undefined ? MISSING : formatUnit(value, unit)}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
