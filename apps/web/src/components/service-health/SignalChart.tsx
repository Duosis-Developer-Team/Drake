"use client";

/**
 * One signal over a bounded window.
 *
 * The caller picks a signal name and a range key; both are validated by the
 * API against the binding's preset and a fixed list of windows. Nothing
 * here can widen either, which is why this component takes no query, no
 * step and no label filter.
 *
 * Gaps stay gaps. A null point breaks the line instead of being drawn at
 * zero, because a flat line at the bottom of a chart is read as "idle" and
 * a missing sample is not that.
 */

import { useCallback, useEffect, useState } from "react";

import { DataState } from "@/components/state/DataState";
import { ApiError, apiGet } from "@/lib/api";
import {
  SERIES_RANGES,
  SIGNAL_LABELS,
  formatSignal,
  type HealthSeries,
  type SeriesRange,
} from "@/lib/serviceHealth";

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string; correlationId?: string }
  | { kind: "ready"; data: HealthSeries };

export function RangeSelector({
  value,
  onChange,
}: {
  value: SeriesRange;
  onChange: (next: SeriesRange) => void;
}) {
  return (
    <div
      role="group"
      aria-label="Time range"
      className="inline-flex items-center gap-0.5 rounded-lg border border-border p-0.5"
    >
      {SERIES_RANGES.map((range) => (
        <button
          key={range}
          type="button"
          onClick={() => onChange(range)}
          aria-pressed={value === range}
          className={`rounded-md px-2 py-1 text-xs font-medium transition-colors ${
            value === range
              ? "bg-accent text-white"
              : "text-ink-secondary hover:bg-surface-sunken"
          }`}
        >
          {range}
        </button>
      ))}
    </div>
  );
}

function Plot({ data }: { data: HealthSeries }) {
  const points = data.series.flatMap((series) => series.points);
  const values = points
    .map(([, value]) => value)
    .filter((value): value is number => value !== null);
  if (values.length === 0) return null;

  const timestamps = points.map(([ts]) => ts);
  const minTime = Math.min(...timestamps);
  const maxTime = Math.max(...timestamps);
  const minValue = Math.min(...values, 0);
  const maxValue = Math.max(...values);
  const spanTime = maxTime - minTime || 1;
  const spanValue = maxValue - minValue || 1;

  const x = (ts: number) => ((ts - minTime) / spanTime) * 100;
  const y = (value: number) => 40 - ((value - minValue) / spanValue) * 36 - 2;

  return (
    <svg
      viewBox="0 0 100 40"
      preserveAspectRatio="none"
      role="img"
      aria-label={`${SIGNAL_LABELS[data.signal] ?? data.signal} over the last ${data.range_key}`}
      className="h-28 w-full"
      data-testid="signal-chart"
    >
      {data.series.map((series, index) => {
        // Each run of consecutive non-null points is its own path, so a gap
        // in the data is a gap in the line rather than a straight segment
        // drawn across it.
        const runs: string[] = [];
        let current: string[] = [];
        for (const [ts, value] of series.points) {
          if (value === null) {
            if (current.length > 1) runs.push(current.join(" "));
            current = [];
            continue;
          }
          current.push(`${current.length === 0 ? "M" : "L"}${x(ts)},${y(value)}`);
        }
        if (current.length > 1) runs.push(current.join(" "));
        return runs.map((path, runIndex) => (
          <path
            key={`${index}-${runIndex}`}
            d={path}
            fill="none"
            stroke="currentColor"
            strokeWidth="0.8"
            vectorEffect="non-scaling-stroke"
            className="text-accent"
          />
        ));
      })}
    </svg>
  );
}

export function SignalChart({
  bindingId,
  signal,
  unit,
  range,
}: {
  bindingId: string;
  signal: string;
  unit: string;
  range: SeriesRange;
}) {
  const [state, setState] = useState<State>({ kind: "loading" });

  const load = useCallback(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    apiGet<HealthSeries>(
      `/v1/service-health/bindings/${bindingId}/series?signal=${signal}&range=${range}`,
    )
      .then((data) => {
        if (!cancelled) setState({ kind: "ready", data });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        if (error instanceof ApiError) {
          setState({
            kind: "error",
            message: error.message,
            correlationId: error.correlationId,
          });
        } else {
          setState({ kind: "error", message: "request failed" });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [bindingId, signal, range]);

  useEffect(() => load(), [load]);

  if (state.kind === "loading") return <DataState kind="loading" />;
  if (state.kind === "error") {
    return <DataState kind="error" description={state.message} onRetry={load} />;
  }

  const { data } = state;
  if (data.data_state === "not_configured") {
    return <DataState kind="not-configured" description="No telemetry datasource is configured." />;
  }
  if (data.series.length === 0 || data.data_state === "empty") {
    return (
      <DataState
        kind="no-data"
        description={`The datasource returned no samples for the last ${data.range_key}.`}
      />
    );
  }

  const latest = data.series
    .flatMap((series) => series.points)
    .filter(([, value]) => value !== null)
    .sort((a, b) => a[0] - b[0])
    .at(-1);

  return (
    <div className="space-y-2">
      {data.data_state === "stale" ? (
        <DataState
          kind="stale"
          description="Showing the last successful reading; the datasource has not refreshed in time."
          lastSuccessAt={data.as_of ?? undefined}
        />
      ) : null}
      {data.series_truncated ? (
        <DataState
          kind="partial"
          description="Only the first 12 series are shown; this chart does not cover every one."
        />
      ) : null}
      <Plot data={data} />
      <p className="text-[11px] text-ink-muted">
        Latest: <span className="font-mono">{formatSignal(latest?.[1] ?? null, unit)}</span>
      </p>
    </div>
  );
}
