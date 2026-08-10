/**
 * Typed service-health surface.
 *
 * The browser never decides what "healthy" means. It receives a status, a
 * list of reason codes and per-section numbers, and renders them. There is
 * no threshold in this file and no arithmetic on a status, because a second
 * implementation of the health rules is exactly how two screens start
 * disagreeing about the same service.
 *
 * Nor is there any way to ask a free-form question: a chart names a
 * `signal` from the binding's preset and a `range` from a fixed list. There
 * is no field for a selector, a label matcher, or a query.
 */

import { apiGet, apiMutate } from "@/lib/api";

/** Every status the API may report. `unknown`, `stale` and `not_configured`
 * are first-class — none of them is a synonym for healthy. */
export type ServiceHealthStatus =
  | "healthy"
  | "degraded"
  | "critical"
  | "unknown"
  | "stale"
  | "not_configured";

/** How one signal turned out. Five outcomes, deliberately not four. */
export type SignalState = "ok" | "empty" | "failed" | "stale" | "not_configured" | "not_collected";

export interface SignalValue {
  value: number | null;
  state: SignalState;
  newest_sample_at: string | null;
  from_cache: boolean;
}

export interface SectionResult {
  status: ServiceHealthStatus;
  reasons: string[];
  [key: string]: unknown;
}

export interface AvailabilitySection extends SectionResult {
  desired_replicas: number | null;
  ready_replicas: number | null;
  ready_ratio?: number | null;
  scaled_to_zero?: boolean;
}

export interface StabilitySection extends SectionResult {
  restarts_in_window: number | null;
  crash_looping: boolean;
  oom_killed: boolean;
}

export interface ResourcesSection extends SectionResult {
  cpu_cores_used: number | null;
  cpu_limit_cores: number | null;
  cpu_utilization: number | null;
  memory_bytes_used: number | null;
  memory_limit_bytes: number | null;
  memory_utilization: number | null;
  cpu_throttled_ratio: number | null;
  limits_configured?: boolean;
}

export interface ApplicationSection extends SectionResult {
  metrics_present: boolean;
  request_rate: number | null;
  error_ratio: number | null;
  latency_p95_seconds: number | null;
}

export interface BindingSummary {
  id: string;
  lifecycle: string;
  resolved: boolean;
  resolved_at: string | null;
  revision: number;
  namespace: string;
  workload_kind: string;
  workload_name: string;
  cluster_ref: string;
  cluster_id: string;
  preset_key: string;
  health_policy_key: string;
  project_key: string;
  environment_key: string;
  service_key: string;
  environment_service_id: string;
  datasource_configured: boolean;
}

export interface ServiceHealth {
  status: ServiceHealthStatus;
  computed_at: string;
  newest_sample_at: string | null;
  freshness_age_seconds: number | null;
  availability: AvailabilitySection;
  stability: StabilitySection;
  resources: ResourcesSection;
  application: ApplicationSection;
  reasons: string[];
  messages: string[];
  missing_signals: string[];
  partial: boolean;
  policy_key: string;
  binding_id: string | null;
  served_from_last_good: boolean;
  cached: boolean;
  /** Present only on last-good responses. */
  served_at?: string;
  age_seconds?: number | null;
  binding: BindingSummary;
}

export interface ServiceHealthRow {
  environment_service_id: string;
  project_id: string;
  project_key: string;
  environment_id: string;
  environment_key: string;
  service_key: string;
  display_name: string | null;
  component: string | null;
  binding: {
    id: string;
    lifecycle: string;
    namespace: string;
    workload_kind: string;
    workload_name: string;
    resolved: boolean;
    preset_key: string;
    health_policy_key: string;
    revision: number;
    cluster: { cluster_ref: string; id: string };
  } | null;
  health: {
    status: ServiceHealthStatus;
    computed_at: string;
    newest_sample_at: string | null;
    freshness_age_seconds: number | null;
    partial: boolean;
    served_from_last_good: boolean;
    reasons: string[];
    availability: Partial<AvailabilitySection>;
    stability: Partial<StabilitySection>;
    resources: Partial<ResourcesSection>;
  };
}

export interface ServiceHealthPage {
  items: ServiceHealthRow[];
  total: number;
  limit: number;
  offset: number;
}

export interface MetricSummary {
  binding: BindingSummary;
  status: ServiceHealthStatus;
  computed_at: string;
  partial: boolean;
  missing_signals: string[];
  readable_signals: string[];
  metrics: {
    availability: { desired_replicas: SignalValue; ready_replicas: SignalValue };
    stability: { restarts: SignalValue };
    resources: {
      cpu_usage: SignalValue;
      cpu_limit: SignalValue;
      cpu_utilization: number | null;
      memory_usage: SignalValue;
      memory_limit: SignalValue;
      memory_utilization: number | null;
      cpu_throttling: SignalValue;
    };
    application: {
      request_rate: SignalValue;
      error_ratio: SignalValue;
      latency_p95: SignalValue;
    };
    freshness: SignalValue;
  };
}

export interface HealthSeries {
  signal: string;
  unit: string | null;
  series: { labels: Record<string, string>; points: [number, number | null][] }[];
  series_truncated: boolean;
  range: Record<string, unknown> | null;
  data_range?: Record<string, unknown> | null;
  data_state: "ok" | "empty" | "stale" | "not_configured";
  cache_state: string;
  partial: boolean;
  warnings: string[];
  as_of: string | null;
  range_key: string;
  binding: BindingSummary;
}

export interface BindingOptions {
  clusters: { id: string; cluster_ref: string; display_name: string | null }[];
  namespaces: string[];
  workloads: { kind: string; name: string; last_seen_at: string | null }[];
  workload_kinds: string[];
  datasource: {
    configured: boolean;
    integration_type: string;
    configuration_state: string;
    observed_state: string;
    last_success_at: string | null;
  } | null;
  presets: {
    key: string;
    title: string;
    description: string;
    signals: string[];
    includes_application_signals: boolean;
  }[];
  policies: { key: string; title: string }[];
  ranges: string[];
}

/** The only windows a chart may request. Mirrors the server's fixed set;
 * anything else is refused there, not merely absent here. */
export const SERIES_RANGES = ["15m", "1h", "6h", "24h"] as const;
export type SeriesRange = (typeof SERIES_RANGES)[number];

export function parseSeriesRange(raw: string | null): SeriesRange {
  return (SERIES_RANGES as readonly string[]).includes(raw ?? "")
    ? (raw as SeriesRange)
    : "1h";
}

/** Human labels for reason codes. The code is the contract; this is only
 * the wording, and the API ships its own `messages` alongside. */
export const REASON_LABELS: Record<string, string> = {
  no_binding: "Not bound to a workload",
  binding_disabled: "Binding disabled",
  binding_unresolved: "Workload not seen in inventory",
  datasource_not_configured: "No telemetry datasource",
  datasource_unavailable: "Datasource unreachable",
  telemetry_stale: "Telemetry is stale",
  query_failed: "A telemetry query failed",
  partial_result: "Partial result",
  no_ready_replicas: "No replicas ready",
  partial_availability: "Fewer replicas ready than desired",
  rollout_incomplete: "Rollout incomplete",
  restart_spike: "Restart spike",
  oom_killed: "OOM killed",
  crash_loop: "Crash loop",
  cpu_pressure: "CPU pressure",
  memory_pressure: "Memory pressure",
  cpu_throttling: "CPU throttling",
  high_error_rate: "High error rate",
  high_latency: "High latency",
  application_metrics_missing: "No application metrics published",
};

/** Human labels for signal names, so a missing-signal list reads as prose. */
export const SIGNAL_LABELS: Record<string, string> = {
  desired_replicas: "Desired replicas",
  ready_replicas: "Ready replicas",
  restarts: "Restarts",
  cpu_usage: "CPU usage",
  cpu_limit: "CPU limit",
  memory_usage: "Memory usage",
  memory_limit: "Memory limit",
  cpu_throttling: "CPU throttling",
  request_rate: "Request rate",
  error_ratio: "Error ratio",
  latency_p95: "Latency (p95)",
  freshness: "Scrape freshness",
  "availability.replicas": "Replica counts",
  "stability.restarts": "Restart counts",
  "resources.usage": "Resource usage",
  "resources.limits": "Resource limits",
  "application.golden_signals": "Application golden signals",
};

export async function fetchServiceHealthList(
  params: { environmentId?: string; projectId?: string } = {},
): Promise<ServiceHealthPage> {
  const query = new URLSearchParams();
  if (params.environmentId) query.set("environment_id", params.environmentId);
  if (params.projectId) query.set("project_id", params.projectId);
  const suffix = query.toString() ? `?${query}` : "";
  return apiGet<ServiceHealthPage>(`/v1/service-health/services${suffix}`);
}

export async function fetchBindingOptions(params: {
  environmentServiceId?: string;
  clusterId?: string;
  namespace?: string;
}): Promise<BindingOptions> {
  const query = new URLSearchParams();
  if (params.environmentServiceId)
    query.set("environment_service_id", params.environmentServiceId);
  if (params.clusterId) query.set("cluster_id", params.clusterId);
  if (params.namespace) query.set("namespace", params.namespace);
  const suffix = query.toString() ? `?${query}` : "";
  return apiGet<BindingOptions>(`/v1/service-health/binding-options${suffix}`);
}

export async function createBinding(
  csrfToken: string,
  body: {
    environment_service_id: string;
    cluster_id: string;
    namespace: string;
    workload_kind: string;
    workload_name: string;
    preset_key: string;
    health_policy_key: string;
  },
): Promise<{ id: string; revision: number; resolved: boolean }> {
  return apiMutate("/v1/service-health/bindings", { csrfToken, body });
}

export async function updateBinding(
  csrfToken: string,
  bindingId: string,
  body: { preset_key: string; health_policy_key: string; expected_revision: number },
): Promise<{ id: string; revision: number; changed: boolean }> {
  return apiMutate(`/v1/service-health/bindings/${bindingId}`, { csrfToken, body });
}

export async function setBindingLifecycle(
  csrfToken: string,
  bindingId: string,
  lifecycle: "active" | "disabled",
  expectedRevision: number,
): Promise<{ id: string; revision: number; lifecycle: string; changed: boolean }> {
  return apiMutate(`/v1/service-health/bindings/${bindingId}/lifecycle`, {
    csrfToken,
    body: { lifecycle, expected_revision: expectedRevision },
  });
}

export async function resolveBinding(
  csrfToken: string,
  bindingId: string,
): Promise<{ resolved: boolean; resource_uid: string | null }> {
  return apiMutate(`/v1/service-health/bindings/${bindingId}/resolve`, { csrfToken });
}

/** Formatting only — never a judgement. `null` renders as an em dash so an
 * absent measurement can never be mistaken for a measured zero. */
export function formatSignal(value: number | null, unit: string): string {
  if (value === null || Number.isNaN(value)) return "—";
  switch (unit) {
    case "ratio":
      return `${(value * 100).toFixed(1)}%`;
    case "cores":
      return `${value.toFixed(2)} cores`;
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
    case "seconds":
      return value < 1 ? `${(value * 1000).toFixed(0)} ms` : `${value.toFixed(2)} s`;
    case "requests_per_second":
      return `${value.toFixed(2)} req/s`;
    case "count":
      return `${Math.round(value)}`;
    default:
      return value.toFixed(2);
  }
}

/** "4m ago", or an honest dash when nothing has been measured. */
export function formatAge(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const whole = Math.max(0, Math.round(seconds));
  if (whole < 60) return `${whole}s ago`;
  if (whole < 3600) return `${Math.round(whole / 60)}m ago`;
  if (whole < 86400) return `${Math.round(whole / 3600)}h ago`;
  return `${Math.round(whole / 86400)}d ago`;
}
