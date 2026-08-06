"use client";

/**
 * Generic dashboard widgets: KPI, time series (inline SVG — no chart
 * library, zero bundle cost), and status. Every widget distinguishes
 * loading / empty / zero / stale / partial / not-configured / denied /
 * unavailable, shows unit-aware tabular numbers, and carries a
 * screen-reader summary plus a table fallback for the chart.
 */

import { AlertTriangle, CheckCircle2, HelpCircle, RefreshCw } from "lucide-react";

import type {
  DashboardWidget,
  TelemetryEnvelope,
  TelemetrySeries,
} from "@/lib/telemetry";
import { formatValue, reduceEnvelope } from "@/lib/telemetry";

export type WidgetState =
  | { kind: "loading" }
  | { kind: "denied" }
  | { kind: "unavailable"; correlationId?: string }
  | { kind: "error"; correlationId?: string }
  | { kind: "ready"; envelope: TelemetryEnvelope };

function StateBadge({ label, tone }: { label: string; tone: "warn" | "info" | "muted" }) {
  const toneClass =
    tone === "warn"
      ? "border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400"
      : tone === "info"
        ? "border-sky-500/40 bg-sky-500/10 text-sky-600 dark:text-sky-400"
        : "border-border bg-surface-sunken text-ink-muted";
  return (
    <span
      className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${toneClass}`}
    >
      {label}
    </span>
  );
}

function EnvelopeBadges({ envelope }: { envelope: TelemetryEnvelope }) {
  return (
    <span className="flex items-center gap-1">
      {envelope.data_state === "stale" ? <StateBadge label="stale" tone="warn" /> : null}
      {envelope.partial ? <StateBadge label="partial" tone="warn" /> : null}
      {envelope.range.step_adjusted ? <StateBadge label="step adjusted" tone="muted" /> : null}
    </span>
  );
}

function WidgetFrame({
  widget,
  state,
  onRetry,
  children,
}: {
  widget: DashboardWidget;
  state: WidgetState;
  onRetry: () => void;
  children?: React.ReactNode;
}) {
  return (
    <section
      aria-label={widget.title}
      data-testid={`widget-${widget.key}`}
      className="flex min-h-28 flex-col rounded-xl border border-border bg-surface p-4"
    >
      <div className="mb-2 flex items-center justify-between gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-muted">
          {widget.title}
        </h3>
        {state.kind === "ready" ? <EnvelopeBadges envelope={state.envelope} /> : null}
      </div>
      {state.kind === "loading" ? (
        <div aria-hidden className="flex-1 animate-pulse rounded-lg bg-surface-sunken" />
      ) : null}
      {state.kind === "denied" ? (
        <p className="text-sm text-ink-muted">You do not have access to this signal.</p>
      ) : null}
      {state.kind === "unavailable" || state.kind === "error" ? (
        <div className="space-y-2 text-sm">
          <p className="text-ink-secondary">
            {state.kind === "unavailable"
              ? "Telemetry source unavailable."
              : "Could not load this signal."}
          </p>
          {state.correlationId ? (
            <p className="font-mono text-[11px] text-ink-muted">ref: {state.correlationId}</p>
          ) : null}
          <button
            type="button"
            onClick={onRetry}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-xs font-medium text-ink hover:bg-surface-sunken"
          >
            <RefreshCw className="h-3 w-3" aria-hidden /> Retry
          </button>
        </div>
      ) : null}
      {state.kind === "ready" ? children : null}
    </section>
  );
}

function honestEmpty(envelope: TelemetryEnvelope): string | null {
  if (envelope.data_state === "not_configured") return "Telemetry source not configured.";
  if (envelope.data_state === "empty" || envelope.series.length === 0) {
    return "No data in the selected range.";
  }
  return null;
}

function thresholdTone(
  value: number | null,
  thresholds: DashboardWidget["thresholds"],
): { icon: React.ReactNode; label: string } {
  if (value === null || !thresholds) {
    return {
      icon: <HelpCircle className="h-4 w-4 text-ink-muted" aria-hidden />,
      label: "no threshold",
    };
  }
  const breachedCritical =
    thresholds.direction === "above" ? value >= thresholds.critical : value <= thresholds.critical;
  const breachedWarn =
    thresholds.direction === "above" ? value >= thresholds.warn : value <= thresholds.warn;
  if (breachedCritical) {
    return {
      icon: <AlertTriangle className="h-4 w-4 text-rose-500" aria-hidden />,
      label: "critical",
    };
  }
  if (breachedWarn) {
    return {
      icon: <AlertTriangle className="h-4 w-4 text-amber-500" aria-hidden />,
      label: "warning",
    };
  }
  return {
    icon: <CheckCircle2 className="h-4 w-4 text-emerald-500" aria-hidden />,
    label: "within threshold",
  };
}

export function KpiWidget({
  widget,
  state,
  onRetry,
}: {
  widget: DashboardWidget;
  state: WidgetState;
  onRetry: () => void;
}) {
  return (
    <WidgetFrame widget={widget} state={state} onRetry={onRetry}>
      {state.kind === "ready"
        ? (() => {
            const empty = honestEmpty(state.envelope);
            if (empty) return <p className="text-sm italic text-ink-muted">{empty}</p>;
            const value = reduceEnvelope(state.envelope, widget.reducer);
            const tone = thresholdTone(value, widget.thresholds);
            return (
              <div>
                <p className="flex items-center gap-2">
                  <span className="text-2xl font-semibold tabular-nums text-ink">
                    {formatValue(value, widget.unit)}
                  </span>
                  {widget.thresholds ? (
                    <span className="flex items-center gap-1 text-[11px] text-ink-muted">
                      {tone.icon}
                      {tone.label}
                    </span>
                  ) : null}
                </p>
                <p className="sr-only">
                  {widget.accessibleSummary ?? widget.title}: {formatValue(value, widget.unit)}
                  {widget.thresholds ? `, ${tone.label}` : ""}
                </p>
                <p className="mt-1 text-[11px] text-ink-muted">
                  as of {new Date(state.envelope.as_of).toLocaleTimeString()}
                </p>
              </div>
            );
          })()
        : null}
    </WidgetFrame>
  );
}

export function StatusWidget({
  widget,
  state,
  onRetry,
}: {
  widget: DashboardWidget;
  state: WidgetState;
  onRetry: () => void;
}) {
  return (
    <WidgetFrame widget={widget} state={state} onRetry={onRetry}>
      {state.kind === "ready"
        ? (() => {
            const empty = honestEmpty(state.envelope);
            if (empty) return <p className="text-sm italic text-ink-muted">{empty}</p>;
            const value = reduceEnvelope(state.envelope, "latest");
            const up = value !== null && value >= 1;
            const unknown = value === null;
            return (
              <p className="flex items-center gap-2 text-sm text-ink">
                {unknown ? (
                  <HelpCircle className="h-4 w-4 text-ink-muted" aria-hidden />
                ) : up ? (
                  <CheckCircle2 className="h-4 w-4 text-emerald-500" aria-hidden />
                ) : (
                  <AlertTriangle className="h-4 w-4 text-rose-500" aria-hidden />
                )}
                <span>{unknown ? "Unknown" : up ? "Being scraped" : "Not being scraped"}</span>
                {state.envelope.data_state === "stale" ? (
                  <span className="text-[11px] text-ink-muted">
                    (as of {new Date(state.envelope.as_of).toLocaleTimeString()})
                  </span>
                ) : null}
              </p>
            );
          })()
        : null}
    </WidgetFrame>
  );
}

const SERIES_STYLES = [
  { dash: "", width: 2 },
  { dash: "6 3", width: 2 },
  { dash: "2 3", width: 2 },
  { dash: "8 3 2 3", width: 2 },
];

function seriesName(series: TelemetrySeries, index: number): string {
  const parts = Object.entries(series.labels).map(([key, value]) => `${key}=${value}`);
  return parts.length > 0 ? parts.join(" ") : `series ${index + 1}`;
}

export function TimeseriesWidget({
  widget,
  state,
  onRetry,
}: {
  widget: DashboardWidget;
  state: WidgetState;
  onRetry: () => void;
}) {
  return (
    <WidgetFrame widget={widget} state={state} onRetry={onRetry}>
      {state.kind === "ready"
        ? (() => {
            const envelope = state.envelope;
            const empty = honestEmpty(envelope);
            if (empty) return <p className="text-sm italic text-ink-muted">{empty}</p>;
            return <Chart widget={widget} envelope={envelope} />;
          })()
        : null}
    </WidgetFrame>
  );
}

function Chart({ widget, envelope }: { widget: DashboardWidget; envelope: TelemetryEnvelope }) {
  const width = 560;
  const height = 140;
  const shown = envelope.series.slice(0, SERIES_STYLES.length);

  const allValues = shown.flatMap((series) =>
    series.points.map((point) => point[1]).filter((value): value is number => value !== null),
  );
  const allTimes = shown.flatMap((series) => series.points.map((point) => point[0]));
  const min = Math.min(0, ...allValues);
  const max = Math.max(...allValues, min + 1e-9);
  const t0 = Math.min(...allTimes);
  const t1 = Math.max(...allTimes, t0 + 1);

  const x = (ts: number) => ((ts - t0) / (t1 - t0)) * (width - 8) + 4;
  const y = (value: number) => height - 12 - ((value - min) / (max - min)) * (height - 24);

  const paths = shown.map((series) => {
    const segments: string[] = [];
    let current: string[] = [];
    for (const [ts, value] of series.points) {
      if (value === null) {
        if (current.length > 0) segments.push(current.join(" "));
        current = []; // null = honest gap, never drawn as zero
      } else {
        current.push(`${current.length === 0 ? "M" : "L"}${x(ts).toFixed(1)},${y(value).toFixed(1)}`);
      }
    }
    if (current.length > 0) segments.push(current.join(" "));
    return segments.join(" ");
  });

  const latest = reduceEnvelope(envelope, "latest");
  const summary =
    `${widget.accessibleSummary ?? widget.title}: ${shown.length} series, ` +
    `latest total ${formatValue(latest, widget.unit)}, range ${envelope.range.from} to ` +
    `${envelope.range.to}${envelope.data_state === "stale" ? " (stale data)" : ""}`;

  return (
    <figure className="space-y-2">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={summary}
        className="w-full text-accent motion-reduce:transition-none"
        preserveAspectRatio="none"
      >
        <line
          x1="4"
          y1={height - 12}
          x2={width - 4}
          y2={height - 12}
          stroke="currentColor"
          strokeOpacity="0.15"
        />
        {paths.map((d, index) => (
          <path
            key={shown[index] ? seriesName(shown[index], index) : index}
            d={d}
            fill="none"
            stroke="currentColor"
            strokeWidth={SERIES_STYLES[index].width}
            strokeDasharray={SERIES_STYLES[index].dash}
            strokeOpacity={1 - index * 0.18}
            vectorEffect="non-scaling-stroke"
          />
        ))}
      </svg>
      {shown.length > 1 ? (
        <ul className="flex flex-wrap gap-3 text-[11px] text-ink-muted">
          {shown.map((series, index) => (
            <li key={seriesName(series, index)} className="flex items-center gap-1.5">
              <svg viewBox="0 0 24 4" className="h-1 w-6 text-accent" aria-hidden>
                <line
                  x1="0"
                  y1="2"
                  x2="24"
                  y2="2"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeDasharray={SERIES_STYLES[index].dash}
                  strokeOpacity={1 - index * 0.18}
                />
              </svg>
              {seriesName(series, index)}
            </li>
          ))}
        </ul>
      ) : null}
      {envelope.series.length > shown.length ? (
        <p className="text-[11px] text-ink-muted">
          {envelope.series.length - shown.length} more series not drawn.
        </p>
      ) : null}
      <figcaption className="sr-only">{summary}</figcaption>
      <details className="text-xs text-ink-muted">
        <summary className="cursor-pointer select-none">Data table</summary>
        <div className="mt-2 max-h-48 overflow-auto">
          <table className="w-full text-left text-[11px] tabular-nums">
            <thead>
              <tr>
                <th className="pr-2 font-medium">Time</th>
                {shown.map((series, index) => (
                  <th key={seriesName(series, index)} className="pr-2 font-medium">
                    {seriesName(series, index)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(shown[0]?.points ?? []).map(([ts], rowIndex) => (
                <tr key={ts}>
                  <td className="pr-2">{new Date(ts * 1000).toLocaleTimeString()}</td>
                  {shown.map((series, seriesIndex) => (
                    <td key={`${seriesName(series, seriesIndex)}-${ts}`} className="pr-2">
                      {formatValue(series.points[rowIndex]?.[1] ?? null, widget.unit)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}
