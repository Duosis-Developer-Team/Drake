/**
 * Typed telemetry surface. Queries go ONLY to the Drake API: template key +
 * opaque scope id + range. No metric names, label names, or query fragments
 * exist in this contract, and no provider endpoint is ever known here.
 */

import { apiGet, apiMutate } from "@/lib/api";

export type RangePreset = "1h" | "24h" | "7d";

export const RANGE_PRESETS: { key: RangePreset; label: string; seconds: number; step: number }[] =
  [
    { key: "1h", label: "Last 1h", seconds: 3600, step: 60 },
    { key: "24h", label: "Last 24h", seconds: 86400, step: 300 },
    { key: "7d", label: "Last 7d", seconds: 604800, step: 1800 },
  ];

/** Invalid/unknown values fall back to the safe default (24h). */
export function parseRangePreset(raw: string | null): RangePreset {
  return (RANGE_PRESETS.some((preset) => preset.key === raw) ? raw : "24h") as RangePreset;
}

export type DataState = "ok" | "empty" | "stale" | "not_configured";
export type CacheState = "fresh_hit" | "miss" | "stale" | "none";

export interface TelemetrySeries {
  labels: Record<string, string>;
  points: [number, number | null][];
}

export interface TelemetryEnvelope {
  template_key: string;
  template_version: number;
  metric_key: string;
  scope: { type: string; ref: string };
  unit: string;
  result_type: "timeseries";
  series: TelemetrySeries[];
  range: {
    from: string;
    to: string;
    requested_step_seconds: number;
    effective_step_seconds: number;
    step_adjusted: boolean;
  };
  data_state: DataState;
  cache_state: CacheState;
  partial: boolean;
  warnings: string[];
  source_type: string;
  as_of: string;
  correlation_id: string;
}

export interface DashboardWidget {
  key: string;
  title: string;
  display: "kpi" | "timeseries" | "status";
  queryTemplateKey: string;
  reducer: "latest" | "avg" | "max" | "sum";
  unit: string;
  thresholds?: { warn: number; critical: number; direction: "above" | "below" };
  requiredProfile?: string;
  emptyBehavior: "show_empty" | "show_zero_as_zero";
  accessibleSummary?: string;
  drilldownRoute?: string;
}

export interface DashboardSection {
  key: string;
  title: string;
  widgets: DashboardWidget[];
}

export interface DashboardDefinition {
  key: string;
  version: number;
  title: string;
  profiles: string[];
  scopeTypes: string[];
  summary: string;
  sections: DashboardSection[];
}

export async function fetchDashboard(templateKey: string): Promise<DashboardDefinition> {
  const body = await apiGet<{ dashboard: DashboardDefinition }>(
    `/v1/dashboard-templates/${templateKey}`,
  );
  return body.dashboard;
}

export async function queryTelemetry(
  csrfToken: string,
  input: {
    templateKey: string;
    scopeType: "environment" | "service" | "cluster";
    scopeId: string;
    preset: RangePreset;
  },
): Promise<TelemetryEnvelope> {
  const preset = RANGE_PRESETS.find((entry) => entry.key === input.preset) ?? RANGE_PRESETS[1];
  const to = new Date();
  const from = new Date(to.getTime() - preset.seconds * 1000);
  return apiMutate<TelemetryEnvelope>("/v1/telemetry/query", {
    csrfToken,
    body: {
      template_key: input.templateKey,
      scope: { type: input.scopeType, id: input.scopeId },
      range: {
        from: from.toISOString(),
        to: to.toISOString(),
        step_seconds: preset.step,
      },
      parameters: {},
    },
  });
}

/** Unit-aware display formatting (tabular, honest about magnitude). */
export function formatValue(value: number | null, unit: string): string {
  if (value === null || Number.isNaN(value)) return "—";
  switch (unit) {
    case "requests_per_second":
      return `${value.toFixed(2)} req/s`;
    case "ratio":
      return `${(value * 100).toFixed(2)}%`;
    case "seconds":
      return value < 1 ? `${(value * 1000).toFixed(0)} ms` : `${value.toFixed(2)} s`;
    case "bytes": {
      const units = ["B", "KiB", "MiB", "GiB", "TiB"];
      let scaled = value;
      let index = 0;
      while (scaled >= 1024 && index < units.length - 1) {
        scaled /= 1024;
        index += 1;
      }
      return `${scaled.toFixed(1)} ${units[index]}`;
    }
    case "cores":
      return `${value.toFixed(2)} cores`;
    case "restarts":
      return `${Math.round(value)}`;
    default:
      return value.toFixed(2);
  }
}

/** Reduce a series over time, then combine across series (sum). */
export function reduceEnvelope(
  envelope: TelemetryEnvelope,
  reducer: DashboardWidget["reducer"],
): number | null {
  const perSeries = envelope.series
    .map((series) => {
      const values = series.points
        .map((point) => point[1])
        .filter((point): point is number => point !== null);
      if (values.length === 0) return null;
      switch (reducer) {
        case "latest":
          return values[values.length - 1];
        case "avg":
          return values.reduce((total, item) => total + item, 0) / values.length;
        case "max":
          return Math.max(...values);
        case "sum":
          return values.reduce((total, item) => total + item, 0);
      }
    })
    .filter((value): value is number => value !== null);
  if (perSeries.length === 0) return null;
  return perSeries.reduce((total, item) => total + item, 0);
}
