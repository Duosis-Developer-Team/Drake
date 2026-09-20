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

import { ChevronDown, Table2 } from "lucide-react";

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
import { SERIES_DASH, SERIES_TOKENS } from "@/lib/design/tokens";

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
    <ul className="flex flex-wrap items-center gap-2" data-testid="chart-legend">
      {series.map((entry) => {
        const isHidden = hidden?.has(entry.name) ?? false;
        const token = SERIES_TOKENS[entry.slot % SERIES_TOKENS.length];
        const dash = SERIES_DASH[entry.slot % SERIES_DASH.length];
        const swatch = (
          <>
            {/* The swatch carries the line's dash as well as its colour, so
                the legend still pairs with the plot in greyscale. */}
            <svg aria-hidden width="18" height="8" viewBox="0 0 18 8" className="shrink-0">
              <line
                x1="2"
                y1="4"
                x2="16"
                y2="4"
                stroke={`var(--${token})`}
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeDasharray={dash ? dash.map((part) => part * 0.6).join(" ") : undefined}
              />
            </svg>
            <span className={`truncate ${isHidden ? "line-through" : ""}`}>{entry.name}</span>
            <span data-tabular className="font-semibold text-ink">
              {formatUnit(entry.latest, unit)}
            </span>
          </>
        );
        const pill = `inline-flex h-8 min-w-0 max-w-full items-center gap-2 rounded-full border px-3 text-caption transition-[background-color,border-color,opacity] duration-[var(--duration-control)] ${
          isHidden
            ? "border-dashed border-border text-ink-muted opacity-60"
            : "border-border bg-surface-2 text-ink-secondary"
        }`;
        return (
          <li key={entry.name} className="min-w-0">
            {onToggle ? (
              <button
                type="button"
                onClick={() => onToggle(entry.name)}
                aria-pressed={!isHidden}
                className={`${pill} hover:border-border-strong hover:text-ink`}
              >
                {swatch}
              </button>
            ) : (
              <span className={pill}>{swatch}</span>
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
  errorDescription,
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
  /** Why the query failed. "Rate limited", "source unreachable" and "the
   *  request threw" send an operator to three different places, so the frame
   *  must not flatten them into one sentence. */
  errorDescription?: string;
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
      <span className="inline-flex h-5 items-center rounded-full bg-surface-2 px-2 font-medium text-ink-secondary">
        {unit}
      </span>
      {chartWindow ? (
        <span data-tabular>
          {formatUtc(chartWindow.from)} → {formatUtc(chartWindow.to)}
        </span>
      ) : null}
      {chartWindow?.stepAdjusted && chartWindow.stepSeconds ? (
        <span className="inline-flex h-5 items-center rounded-full bg-warning-soft px-2 font-medium text-warning">
          step widened to {chartWindow.stepSeconds}s
        </span>
      ) : null}
      {asOf !== undefined ? <FreshnessIndicator asOf={asOf} state={freshness} /> : null}
    </>
  );

  /* A non-plot state sits where the plot would have been, in a quiet
     placeholder of roughly the plot's height — so a panel of "not
     configured" charts still reads as a set of charts, not a list of
     sentences. */
  const placeholder = (node: React.ReactNode) => (
    <div
      className="flex items-center justify-center rounded-[1.125rem] border border-dashed border-border bg-surface-2/60 px-6 py-6"
      style={{ minHeight: Math.min(height, 200) }}
    >
      {node}
    </div>
  );

  return (
    <Panel data-testid="chart-frame" className="gap-5">
      <PanelHeader title={title} description={question} meta={meta} actions={actions} level={3} />

      {status === "ready" && freshness === "stale" ? <StaleBanner asOf={asOf} /> : null}
      {status === "ready" && partial ? <PartialBanner /> : null}

      {status === "loading" ? (
        <LoadingSkeleton variant="chart" height={height} label={`Loading ${title}`} />
      ) : null}
      {status === "denied" ? placeholder(<DeniedState compact layout="centered" />) : null}
      {status === "not-configured"
        ? placeholder(
            <NotConfiguredState compact layout="centered" description={emptyDescription} />,
          )
        : null}
      {status === "unknown" ? placeholder(<UnknownState compact layout="centered" />) : null}
      {status === "empty" || status === "no-data"
        ? placeholder(<NoDataState compact layout="centered" description={emptyDescription} />)
        : null}
      {status === "error"
        ? placeholder(
            <ErrorState
              compact
              layout="centered"
              description={errorDescription}
              correlationId={correlationId}
              onRetry={onRetry}
            />,
          )
        : null}

      {status === "ready" ? (
        <>
          {legend ?? (series ? <ChartLegend series={series} unit={unit} /> : null)}
          <div style={{ minHeight: height }}>{children}</div>
          {summary || table ? (
            <div className="flex flex-wrap items-start gap-x-4 gap-y-3 border-t border-border pt-4">
              {summary ? (
                <p
                  className="min-w-0 flex-1 basis-64 text-micro leading-5 text-ink-muted"
                  data-testid="chart-summary"
                >
                  {summary}
                </p>
              ) : null}
              {table ? (
                <details className="group ml-auto flex flex-col items-end text-micro text-ink-muted open:basis-full">
                  <summary className="inline-flex h-8 cursor-pointer list-none items-center gap-1.5 self-end rounded-full border border-border bg-surface px-3 text-caption font-medium text-ink-secondary transition-colors select-none hover:bg-surface-hover hover:text-ink [&::-webkit-details-marker]:hidden">
                    <Table2 aria-hidden className="h-3.5 w-3.5" />
                    View as table
                    <ChevronDown
                      aria-hidden
                      className="h-3.5 w-3.5 transition-transform duration-[var(--duration-control)] group-open:rotate-180"
                    />
                  </summary>
                  <div className="mt-3 max-h-64 w-full overflow-auto rounded-2xl border border-border">
                    {table}
                  </div>
                </details>
              ) : null}
            </div>
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
    <table className="w-full text-left text-caption" data-tabular>
      <thead className="sticky top-0 bg-surface-2 text-micro tracking-[0.08em] text-ink-muted uppercase">
        <tr>
          <th scope="col" className="px-4 py-2.5 font-medium">
            {categoryHeader}
          </th>
          {series.map((entry) => (
            <th key={entry.name} scope="col" className="px-4 py-2.5 text-right font-medium">
              {entry.name}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {categories.map((category, index) => (
          <tr key={category} className="border-t border-border">
            <th scope="row" className="px-4 py-2 font-normal whitespace-nowrap text-ink-secondary">
              {category}
            </th>
            {series.map((entry) => {
              const value = entry.values[index];
              return (
                <td key={entry.name} className="px-4 py-2 text-right font-medium text-ink">
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
