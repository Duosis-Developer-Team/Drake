"use client";

/**
 * Dashboard widgets.
 *
 * These render a `TelemetryEnvelope` — a real answer from the Drake API with
 * its own data state attached — and the whole job here is to not lose any of
 * that on the way to the screen:
 *
 *   `stale` is never painted as current. It carries a banner and the instant
 *   the values were actually measured, above the numbers rather than below.
 *
 *   `empty`, `not_configured`, `denied`, `throttled` and `unavailable` are
 *   five different answers and render as five different things. A throttled
 *   query in particular is not an outage — saying "telemetry unavailable"
 *   when the truth is "you asked too fast" sends people to look at the wrong
 *   system.
 *
 *   A `null` sample is a gap. It is never plotted at zero, and the chart says
 *   how many gaps it drew.
 *
 * Nothing here invents a comparison period, so nothing here shows a delta:
 * the query returns one window, and a percentage change against a window that
 * was never fetched would be fabricated.
 */

import { TimeSeriesChart, type TimeSeries } from "@/components/charts/TimeSeriesChart";
import type { ChartStatus } from "@/components/charts/ChartFrame";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { Stat } from "@/components/ui/Stat";
import { HealthIndicator } from "@/components/ui/StatusBadge";
import { FreshnessIndicator } from "@/components/ui/identifiers";
import {
  DeniedState,
  ErrorState,
  LoadingSkeleton,
  NoDataState,
  NotConfiguredState,
  PartialBanner,
  StaleBanner,
} from "@/components/ui/states";
import { toneForThreshold } from "@/lib/design/status";
import type { DashboardWidget, TelemetryEnvelope, TelemetrySeries } from "@/lib/telemetry";
import { formatValue, reduceEnvelope } from "@/lib/telemetry";

export type WidgetState =
  | { kind: "loading" }
  | { kind: "denied" }
  | { kind: "throttled"; correlationId?: string }
  | { kind: "unavailable"; correlationId?: string }
  | { kind: "error"; correlationId?: string }
  | { kind: "ready"; envelope: TelemetryEnvelope };

function seriesName(series: TelemetrySeries, index: number): string {
  const parts = Object.entries(series.labels).map(([key, value]) => `${key}=${value}`);
  return parts.length > 0 ? parts.join(" ") : `series ${index + 1}`;
}

/** Envelope data state → the chart frame's status vocabulary. */
function chartStatus(state: WidgetState): ChartStatus {
  switch (state.kind) {
    case "loading":
      return "loading";
    case "denied":
      return "denied";
    case "throttled":
    case "unavailable":
    case "error":
      return "error";
    case "ready":
      if (state.envelope.data_state === "not_configured") return "not-configured";
      if (state.envelope.data_state === "empty" || state.envelope.series.length === 0) {
        return "no-data";
      }
      return "ready";
  }
}

/**
 * The words for each failure.
 *
 * Separate from `chartStatus` because the chart frame only has one error
 * state, and these three reasons send an operator to three different places.
 */
function failureDescription(state: WidgetState): string | undefined {
  switch (state.kind) {
    case "throttled":
      return "The query budget for this scope is exhausted. This is a rate limit, not an outage — it clears within seconds.";
    case "unavailable":
      return "The telemetry source could not be reached. Nothing is known about this window.";
    case "error":
      return "The query did not complete. This is not the same as an empty result.";
    default:
      return undefined;
  }
}

/** The non-success frame shared by the KPI and status widgets. */
function WidgetShell({
  widget,
  state,
  onRetry,
  meta,
  children,
}: {
  widget: DashboardWidget;
  state: WidgetState;
  onRetry: () => void;
  meta?: React.ReactNode;
  children: (envelope: TelemetryEnvelope) => React.ReactNode;
}) {
  const correlationId = "correlationId" in state ? state.correlationId : undefined;
  return (
    <Panel data-testid={`widget-${widget.key}`} aria-label={widget.title} className="gap-2">
      <PanelHeader
        title={widget.title}
        level={3}
        meta={
          <>
            <span>{widget.unit.replace(/_/g, " ")}</span>
            {state.kind === "ready" ? (
              <FreshnessIndicator
                asOf={state.envelope.as_of}
                state={state.envelope.data_state === "stale" ? "stale" : "fresh"}
              />
            ) : null}
            {meta}
          </>
        }
      />
      {state.kind === "loading" ? <LoadingSkeleton rows={2} label={widget.title} /> : null}
      {state.kind === "denied" ? <DeniedState compact /> : null}
      {state.kind === "throttled" || state.kind === "unavailable" || state.kind === "error" ? (
        <ErrorState
          compact
          title={state.kind === "throttled" ? "Query limit reached" : undefined}
          description={failureDescription(state)}
          correlationId={correlationId}
          onRetry={onRetry}
        />
      ) : null}
      {state.kind === "ready" ? (
        <>
          {state.envelope.data_state === "stale" ? (
            <StaleBanner
              asOf={state.envelope.as_of}
              description={
                state.envelope.data_range
                  ? `These values cover ${state.envelope.data_range.from} to ${state.envelope.data_range.to}, not the window you asked for.`
                  : undefined
              }
              source={state.envelope.source_type}
            />
          ) : null}
          {state.envelope.partial ? <PartialBanner /> : null}
          {state.envelope.data_state === "not_configured" ? (
            <NotConfiguredState
              compact
              description="No telemetry source is configured for this scope."
            />
          ) : state.envelope.data_state === "empty" || state.envelope.series.length === 0 ? (
            <NoDataState compact />
          ) : (
            children(state.envelope)
          )}
        </>
      ) : null}
    </Panel>
  );
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
    <WidgetShell
      widget={widget}
      state={state}
      onRetry={onRetry}
      meta={<span>{widget.reducer} over the window</span>}
    >
      {(envelope) => {
        const value = reduceEnvelope(envelope, widget.reducer);
        return (
          <>
            <Stat
              bare
              label={<span className="sr-only">{widget.title}</span>}
              value={value}
              unit={widget.unit}
              thresholds={widget.thresholds ?? null}
              size="large"
              missingReason="The source returned no usable sample in this window."
            />
            <p className="sr-only">
              {widget.accessibleSummary ?? widget.title}: {formatValue(value, widget.unit)}
              {widget.thresholds
                ? `, ${toneForThreshold(value, widget.thresholds)} against its threshold`
                : ""}
            </p>
          </>
        );
      }}
    </WidgetShell>
  );
}

/**
 * A binary state that is really ternary.
 *
 * "Being scraped" / "not being scraped" / unknown. The third one is the
 * reason this is not a boolean: a missing `up` sample means Drake does not
 * know whether the target is being scraped, and rendering that as "not being
 * scraped" would report a monitoring gap as a service outage.
 */
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
    <WidgetShell widget={widget} state={state} onRetry={onRetry}>
      {(envelope) => {
        const value = reduceEnvelope(envelope, "latest");
        const unknown = value === null;
        const up = !unknown && value >= 1;
        return (
          <HealthIndicator
            status={unknown ? "unknown" : up ? "success" : "critical"}
            label={unknown ? "Unknown" : up ? "Being scraped" : "Not being scraped"}
            detail={
              unknown
                ? "No sample arrived, so Drake cannot say whether this target is scraped."
                : undefined
            }
          />
        );
      }}
    </WidgetShell>
  );
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
  const envelope = state.kind === "ready" ? state.envelope : null;

  const series: TimeSeries[] = (envelope?.series ?? []).map((entry, index) => ({
    name: seriesName(entry, index),
    // The API speaks epoch seconds; the chart's time axis is milliseconds.
    points: entry.points.map(([ts, value]) => [ts * 1000, value] as [number, number | null]),
  }));

  return (
    <div data-testid={`widget-${widget.key}`}>
      <TimeSeriesChart
        title={widget.title}
        question={widget.accessibleSummary}
        unit={widget.unit}
        series={series}
        status={chartStatus(state)}
        asOf={envelope?.as_of}
        freshness={envelope?.data_state === "stale" ? "stale" : "fresh"}
        partial={envelope?.partial}
        thresholds={widget.thresholds ?? null}
        correlationId={"correlationId" in state ? state.correlationId : undefined}
        onRetry={onRetry}
        emptyDescription={
          state.kind === "ready" && state.envelope.data_state === "not_configured"
            ? "No telemetry source is configured for this scope."
            : failureDescription(state)
        }
        window={
          envelope
            ? {
                from: envelope.range.from,
                to: envelope.range.to,
                stepSeconds: envelope.range.effective_step_seconds,
                stepAdjusted: envelope.range.step_adjusted,
              }
            : null
        }
      />
    </div>
  );
}
