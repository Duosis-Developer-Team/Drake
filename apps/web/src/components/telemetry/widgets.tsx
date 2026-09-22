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

import { Activity, CircleSlash, Gauge as GaugeIcon, MinusCircle } from "lucide-react";

import { TimeSeriesChart, type TimeSeries } from "@/components/charts/TimeSeriesChart";
import type { ChartStatus } from "@/components/charts/ChartFrame";
import { Sparkline } from "@/components/charts/visuals";
import { IconBubble, TileState } from "@/components/catalog/visuals";
import { FreshnessIndicator } from "@/components/ui/identifiers";
import {
  DeniedState,
  ErrorState,
  LoadingSkeleton,
  PartialBanner,
  StaleBanner,
} from "@/components/ui/states";
import { formatUnit } from "@/lib/design/format";
import { thresholdLabel, toneForThreshold, toneSpec } from "@/lib/design/status";
import { useLocale, useT, type Translator } from "@/lib/i18n";
import type { DashboardWidget, TelemetryEnvelope, TelemetrySeries } from "@/lib/telemetry";
import { formatValue, reduceEnvelope } from "@/lib/telemetry";

export type WidgetState =
  | { kind: "loading" }
  | { kind: "denied" }
  | { kind: "throttled"; correlationId?: string }
  | { kind: "unavailable"; correlationId?: string }
  | { kind: "error"; correlationId?: string }
  | { kind: "ready"; envelope: TelemetryEnvelope };

function seriesName(series: TelemetrySeries, index: number, t: Translator<"ui">): string {
  const parts = Object.entries(series.labels).map(([key, value]) => `${key}=${value}`);
  return parts.length > 0 ? parts.join(" ") : t("widget.seriesN", { n: index + 1 });
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
 * Whether a timeseries widget has a real series to draw.
 *
 * Only then does it earn a full-width chart; every other answer — loading,
 * empty, not configured, failed — is a sentence, and a sentence belongs in a
 * tile beside its siblings rather than in a 1000px-wide empty frame. Stale
 * data IS drawable, and keeps its chart and its stale banner.
 */
export function hasDrawableSeries(state: WidgetState): boolean {
  return chartStatus(state) === "ready";
}

/**
 * The words for each failure.
 *
 * Separate from `chartStatus` because the chart frame only has one error
 * state, and these three reasons send an operator to three different places.
 */
function failureDescription(state: WidgetState, t: Translator<"ui">): string | undefined {
  switch (state.kind) {
    case "throttled":
      return t("widget.throttled");
    case "unavailable":
      return t("widget.unavailable");
    case "error":
      return t("widget.error");
    default:
      return undefined;
  }
}

/** A formatted measurement split into its number and its unit, so the number
 *  can be set large and the unit beside it rather than wrapping under it. */
function splitMeasure(text: string): { number: string; unit: string } {
  const match = /^([-+]?[\d.,]+)\s*(.*)$/.exec(text);
  return match ? { number: match[1], unit: match[2] } : { number: text, unit: "" };
}

/** The tile frame shared by KPI, status and not-yet-drawable chart widgets. */
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
  const t = useT("ui");
  const correlationId = "correlationId" in state ? state.correlationId : undefined;
  return (
    <section
      data-testid={`widget-${widget.key}`}
      aria-label={widget.title}
      className="flex h-full min-w-0 flex-col gap-4 rounded-[1.5rem] border border-border bg-surface p-6 shadow-panel"
    >
      <div className="min-w-0">
        <h3 className="truncate text-body font-semibold text-ink" title={widget.title}>
          {widget.title}
        </h3>
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-micro text-ink-muted">
          {/* The unit id from the dashboard definition, as data: it is not copy. */}
          <span>{widget.unit.replace(/_/g, " ")}</span>
          {state.kind === "ready" ? (
            <FreshnessIndicator
              asOf={state.envelope.as_of}
              state={state.envelope.data_state === "stale" ? "stale" : "fresh"}
            />
          ) : null}
          {meta}
        </div>
      </div>
      <div className="flex min-w-0 flex-1 flex-col justify-end">
        {state.kind === "loading" ? <LoadingSkeleton rows={2} label={widget.title} /> : null}
        {state.kind === "denied" ? <DeniedState compact /> : null}
        {state.kind === "throttled" || state.kind === "unavailable" || state.kind === "error" ? (
          <ErrorState
            compact
            title={state.kind === "throttled" ? t("widget.throttledTitle") : undefined}
            description={failureDescription(state, t)}
            correlationId={correlationId}
            onRetry={onRetry}
          />
        ) : null}
        {state.kind === "ready" ? (
          <>
            {state.envelope.data_state === "stale" ? (
              <div className="mb-3">
                <StaleBanner
                  asOf={state.envelope.as_of}
                  description={
                    state.envelope.data_range
                      ? t("widget.staleRange", {
                          from: state.envelope.data_range.from,
                          to: state.envelope.data_range.to,
                        })
                      : undefined
                  }
                  source={state.envelope.source_type}
                />
              </div>
            ) : null}
            {state.envelope.partial ? (
              <div className="mb-3">
                <PartialBanner />
              </div>
            ) : null}
            {state.envelope.data_state === "not_configured" ? (
              <TileState
                icon={CircleSlash}
                testId="state-not-configured"
                title={t("widget.notConfiguredTitle")}
                description={t("widget.notConfigured")}
              />
            ) : state.envelope.data_state === "empty" || state.envelope.series.length === 0 ? (
              <TileState
                icon={MinusCircle}
                testId="state-no-data"
                title={t("widget.noDataTitle")}
                description={t("widget.noData")}
              />
            ) : (
              children(state.envelope)
            )}
          </>
        ) : null}
      </div>
    </section>
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
  const t = useT("ui");
  const { locale } = useLocale();
  return (
    <WidgetShell
      widget={widget}
      state={state}
      onRetry={onRetry}
    >
      {(envelope) => {
        const value = reduceEnvelope(envelope, widget.reducer);
        const thresholds = widget.thresholds ?? null;
        const tone = toneForThreshold(value, thresholds);
        const spec = toneSpec(tone, locale);
        const ToneIcon = spec.icon;
        const missing = value === null || Number.isNaN(value);
        const { number, unit } = splitMeasure(formatUnit(value, widget.unit, {}, locale));
        // The series is already here — the shape of the window costs nothing
        // and answers "is this rising" before the reader opens the chart.
        const shape = envelope.series[0]?.points.map(([, sample]) => sample) ?? [];
        return (
          <div data-testid="stat" className="flex min-w-0 flex-col gap-3">
            <p className="flex min-w-0 flex-wrap items-baseline gap-x-1.5">
              <span
                data-tabular
                className={`text-[2.25rem] leading-none font-semibold tracking-[-0.03em] ${
                  missing ? "text-ink-muted" : "text-ink"
                }`}
              >
                {number}
              </span>
              {unit ? <span className="text-body font-medium text-ink-muted">{unit}</span> : null}
            </p>
            <p className="-mt-1 text-micro text-ink-muted">
              {t("widget.reducerOverWindow", {
                reducer: t.dyn("widget.reducer", widget.reducer, widget.reducer),
              })}
            </p>
            {shape.length > 1 ? (
              <div className="min-w-0 [&_svg]:h-9 [&_svg]:w-full">
                <Sparkline
                  points={shape}
                  width={240}
                  height={36}
                  label={t("widget.overWindow", { title: widget.title })}
                  tone={tone}
                />
              </div>
            ) : null}
            {missing ? (
              <p className="text-micro text-ink-muted">{t("widget.noUsableSample")}</p>
            ) : thresholds ? (
              <span
                className={`inline-flex items-center gap-1.5 self-start rounded-full px-2.5 py-1 text-micro font-medium ${spec.chip}`}
              >
                <ToneIcon aria-hidden className="h-3 w-3" />
                {thresholdLabel(tone, true, locale)}
              </span>
            ) : (
              <span className="self-start rounded-full bg-surface-2 px-2.5 py-1 text-micro text-ink-muted">
                {thresholdLabel(tone, false, locale)}
              </span>
            )}
            <p className="sr-only">
              {widget.accessibleSummary ?? widget.title}: {formatValue(value, widget.unit, locale)}
              {widget.thresholds
                ? `, ${t("widget.thresholdVerdict", { tone: toneForThreshold(value, widget.thresholds) })}`
                : ""}
            </p>
          </div>
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
  const t = useT("ui");
  const common = useT("common");
  return (
    <WidgetShell widget={widget} state={state} onRetry={onRetry}>
      {(envelope) => {
        const value = reduceEnvelope(envelope, "latest");
        const unknown = value === null;
        const up = !unknown && value >= 1;
        const tone = unknown ? "unknown" : up ? "success" : "critical";
        return (
          <div className="flex items-center gap-3">
            <IconBubble icon={up ? Activity : GaugeIcon} tone={tone} size="large" />
            <span className="min-w-0">
              <span className={`block text-[1.125rem] leading-6 font-semibold ${toneSpec(tone).text}`}>
                {unknown ? common("state.unknown") : up ? t("widget.scraped") : t("widget.notScraped")}
              </span>
              <span className="block text-caption text-ink-muted">
                {unknown ? t("widget.unknownDetail") : t("widget.scrapedDetail")}
              </span>
            </span>
          </div>
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
  const t = useT("ui");
  const envelope = state.kind === "ready" ? state.envelope : null;

  const series: TimeSeries[] = (envelope?.series ?? []).map((entry, index) => ({
    name: seriesName(entry, index, t),
    // The API speaks epoch seconds; the chart's time axis is milliseconds.
    points: entry.points.map(([ts, value]) => [ts * 1000, value] as [number, number | null]),
  }));

  if (!hasDrawableSeries(state)) {
    // Nothing to plot: the same tile every other widget uses, with the
    // honest reason in it. `children` is never reached for these states.
    return (
      <WidgetShell widget={widget} state={state} onRetry={onRetry}>
        {() => null}
      </WidgetShell>
    );
  }

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
        area={series.length === 1}
        height={240}
        correlationId={"correlationId" in state ? state.correlationId : undefined}
        onRetry={onRetry}
        emptyDescription={
          state.kind === "ready" && state.envelope.data_state === "not_configured"
            ? t("widget.notConfiguredScope")
            : undefined
        }
        errorDescription={failureDescription(state, t)}
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
