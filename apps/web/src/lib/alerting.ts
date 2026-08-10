/**
 * Typed alerting and SLO surface.
 *
 * The browser renders verdicts the backend reached. It does not decide
 * whether an alert is a page, compute a burn rate, compare an error budget
 * to a threshold, or turn a status into a priority — all of that arithmetic
 * belongs to the server, where it can be tested against evidence and where
 * a threshold is a reviewed contract rather than a constant in a bundle.
 *
 * Two things this module deliberately cannot express:
 *
 * - a PromQL expression, a matcher, or an Alertmanager address. There is no
 *   type here that carries one, so no screen can send one.
 * - a percentage as a stored value. Everything is a ratio; a percentage is
 *   formatted at the last moment, once, in `formatRatio`.
 */

import { apiGet } from "@/lib/api";

export type AlertStatus = "firing" | "resolved";
export type Severity = "critical" | "high" | "medium" | "info" | "unknown";
export type Priority = "P1" | "P2" | "P3" | "P4";
export type MappingState = "mapped" | "unmapped" | "ambiguous";
export type SilenceState =
  | "pending"
  | "active"
  | "expired"
  | "failed"
  | "cancel_pending"
  | "cancelled";
export type SloStatus =
  | "healthy"
  | "warning"
  | "critical"
  | "exhausted"
  | "insufficient_data"
  | "stale"
  | "query_failed"
  | "not_configured";

export const SEVERITY_LABELS: Record<Severity, string> = {
  critical: "Critical",
  high: "High",
  medium: "Medium",
  info: "Info",
  unknown: "Unrecognised severity",
};

export const PRIORITY_LABELS: Record<Priority, string> = {
  P1: "P1",
  P2: "P2",
  P3: "P3",
  P4: "P4",
};

export const MAPPING_LABELS: Record<MappingState, string> = {
  mapped: "Mapped",
  // Not "error": Drake received the alert and kept it. What it could not do
  // is place it in the catalog, which is a different fact.
  unmapped: "Unmapped",
  ambiguous: "Ambiguous",
};

export const MAPPING_EXPLANATIONS: Record<string, string> = {
  project_mismatch: "The alert named a project this Alertmanager is not registered for.",
  environment_unknown: "The environment label does not match any environment in this project.",
  service_unknown: "The service label does not match any service in this project.",
  cluster_unknown: "The cluster label does not match any cluster Drake knows about.",
  environment_ambiguous:
    "This service exists in more than one environment and the alert named none of them.",
};

export const SLO_LABELS: Record<SloStatus, string> = {
  healthy: "Healthy",
  warning: "Warning",
  critical: "Critical",
  exhausted: "Budget exhausted",
  // Not "healthy" and not "0%": nothing was measured.
  insufficient_data: "Insufficient data",
  stale: "Stale",
  query_failed: "Query failed",
  not_configured: "Not configured",
};

export const SLO_EXPLANATIONS: Record<SloStatus, string> = {
  healthy: "Within objective, and not burning budget at a rate worth acting on.",
  warning: "Burning error budget faster than the objective allows.",
  critical: "Burning error budget fast enough to exhaust it soon.",
  exhausted: "The error budget for this window is spent.",
  insufficient_data:
    "No requests were observed in this window, so compliance cannot be computed. " +
    "This is not the same as a perfect score.",
  stale: "The last measurement is older than this SLO's freshness limit.",
  query_failed:
    "Drake could not read the SLI. This says nothing about the service — only that " +
    "the measurement did not happen.",
  not_configured: "No SLI is mapped to this SLO yet, so nothing is being measured.",
};

export const SILENCE_LABELS: Record<SilenceState, string> = {
  // Requested and audited, but Alertmanager has not confirmed it. It is
  // suppressing nothing yet, and the label says so.
  pending: "Pending at Alertmanager",
  active: "Active",
  expired: "Expired",
  failed: "Failed",
  cancel_pending: "Cancelling",
  cancelled: "Cancelled",
};

export interface AlertIncidentRef {
  id: string;
  state: string;
  severity: string;
  priority: string | null;
  title: string;
  acknowledged_at: string | null;
  assigned_at: string | null;
}

export interface AlertInstance {
  id: string;
  fingerprint_prefix: string;
  alert_name: string;
  status: AlertStatus;
  severity: Severity;
  priority: Priority;
  mapping_state: MappingState;
  mapping_error_code: string | null;
  owner_team: string | null;
  slo_key: string | null;
  /** A KEY into a reviewed registry, never a URL. */
  runbook_key: string | null;
  starts_at: string;
  ends_at: string | null;
  last_seen_at: string;
  source_event_at: string;
  /** When Drake heard it, as distinct from when it happened. */
  ingested_at: string;
  resolved_at: string | null;
  labels: Record<string, string>;
  annotations: Record<string, string>;
  occurrence: number;
  silenced: boolean;
  inhibited: boolean;
  namespace: string | null;
  version: number;
  project_key: string | null;
  environment_key: string | null;
  service_key: string | null;
  cluster_ref: string | null;
  incident: AlertIncidentRef | null;
}

export interface AlertDetail extends AlertInstance {
  can_silence: boolean;
  silences: SilenceRequest[];
}

export interface AlertEvent {
  event_type: string;
  status: AlertStatus;
  occurrence: number;
  source_event_at: string;
  received_at: string;
  detail: Record<string, unknown>;
}

export interface AlertSummary {
  firing: number;
  p1: number;
  p2: number;
  silenced: number;
  unmapped: number;
  with_incident: number;
}

export interface BurnRate {
  name: string;
  factor: number;
  long_window_seconds: number;
  short_window_seconds: number;
  severity: string;
  long_burn_rate: number | null;
  short_burn_rate: number | null;
  /** True only when BOTH windows exceed the factor. */
  active: boolean;
}

export interface SloEvaluation {
  status: SloStatus;
  data_quality: string;
  compliance_ratio: number | null;
  error_budget_total: number | null;
  error_budget_consumed: number | null;
  /** May be negative. The UI does not hide that. */
  error_budget_remaining: number | null;
  burn_rates: BurnRate[];
  evaluated_for: string;
  window_start: string;
  window_end: string;
  freshness_seconds: number | null;
  error_code: string | null;
  sample_count: number;
  /** The objective this evaluation was judged against. */
  objective_ratio: number;
  definition_version: number;
}

export interface Slo {
  id: string;
  slo_key: string;
  display_name: string;
  indicator: "availability" | "latency";
  objective_ratio: number;
  window_seconds: number;
  threshold_profile_key: string | null;
  burn_profile_key: string;
  enabled: boolean;
  version: number;
  project_key: string;
  environment_key: string | null;
  service_key: string | null;
  measurement: string;
  evaluation: SloEvaluation | null;
}

export interface SloContext {
  deployments: {
    id: string;
    observed_at: string;
    rollout_state: string;
    evidence_state: string;
    generation: number;
  }[];
  incidents: {
    id: string;
    state: string;
    severity: string;
    priority: string | null;
    title: string;
    opened_at: string;
  }[];
  alerts: {
    id: string;
    alert_name: string;
    status: string;
    priority: string;
    last_seen_at: string;
  }[];
  correlation_note: string;
}

export interface SloDetail extends Slo {
  context: SloContext | null;
}

export interface SilenceRequest {
  id: string;
  state: SilenceState;
  reason_code: string;
  reason_note: string | null;
  requested_seconds: number;
  requested_at: string;
  starts_at: string | null;
  ends_at: string | null;
  error_code: string | null;
  version: number;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface AlertingFilters {
  alert_statuses: AlertStatus[];
  severities: Severity[];
  priorities: Priority[];
  mapping_states: MappingState[];
  slo_states: SloStatus[];
  silence_states: SilenceState[];
  indicators: string[];
  windows: string[];
  silence_reasons: { key: string; label: string }[];
}

/** The list path. Every parameter is an allowlisted value the API re-checks. */
export function alertListPath(params: {
  status?: string;
  severity?: string;
  priority?: string;
  mapping_state?: string;
  window?: string;
}): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value) search.set(key, value);
  }
  const query = search.toString();
  return query ? `/v1/alerts?${query}` : "/v1/alerts";
}

export function sloListPath(params: { indicator?: string; status?: string }): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value) search.set(key, value);
  }
  const query = search.toString();
  return query ? `/v1/slo?${query}` : "/v1/slo";
}

export async function fetchAlertEvents(alertId: string): Promise<AlertEvent[]> {
  const body = await apiGet<{ events: AlertEvent[] }>(`/v1/alerts/${alertId}/events`);
  return body.events;
}

/**
 * A ratio as a percentage, formatted once and only here.
 *
 * `null` renders as a dash rather than 0%, because "not measured" and
 * "zero" are different answers and this is the last place they could be
 * confused.
 */
export function formatRatio(value: number | null, digits = 3): string {
  if (value === null || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

/** Error budget remaining, sign preserved. 180% burned reads as −80%. */
export function formatBudget(value: number | null): string {
  if (value === null || Number.isNaN(value)) return "—";
  const percent = value * 100;
  return `${percent > 0 ? "" : ""}${percent.toFixed(1)}%`;
}

export function formatBurn(value: number | null): string {
  if (value === null || Number.isNaN(value)) return "—";
  return `${value.toFixed(2)}×`;
}

export function formatWindow(seconds: number): string {
  if (seconds % 86400 === 0) return `${seconds / 86400}d`;
  if (seconds % 3600 === 0) return `${seconds / 3600}h`;
  return `${Math.round(seconds / 60)}m`;
}

/** "4m ago", or an honest dash. Never "just now" for a missing value. */
export function formatAge(iso: string | null): string {
  if (!iso) return "—";
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "—";
  const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}

export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)}h`;
  return `${(seconds / 86400).toFixed(1)}d`;
}
