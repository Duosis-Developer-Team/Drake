/**
 * Typed alert-rule surface (ADR-0031).
 *
 * Every number the screen draws comes from the API, which reads the samples
 * the incident runner already stored. The browser never queries a metrics
 * provider, and a preview is the server replaying the same samples through
 * the same state machine the runner uses — the screen never guesses.
 */

import { apiGet, apiMutate } from "@/lib/api";

export type Signal =
  | "error_ratio"
  | "latency_p95_seconds"
  | "request_rate"
  | "restarts"
  | "cpu_utilization"
  | "memory_utilization"
  | "unavailable_replicas"
  | "error_logs"
  | "fatal_logs"
  | "slow_requests"
  | "slowest_route_p95_seconds";
export type Operator = ">" | "<";
export type Severity = "critical" | "warning";
export type RuleStatus = "firing" | "pending" | "ok" | "muted" | "disabled" | "no_data";

export const SIGNALS: Signal[] = [
  "error_ratio",
  "latency_p95_seconds",
  "request_rate",
  "restarts",
  "cpu_utilization",
  "memory_utilization",
  "unavailable_replicas",
  "error_logs",
  "fatal_logs",
  "slow_requests",
  "slowest_route_p95_seconds",
];

/**
 * Where a signal is measured (ADR-0034). Metrics come from the health
 * verdict; logs and traces are counted from the store over the last five
 * minutes, every minute.
 */
export type SignalSource = "metrics" | "logs" | "traces";
export const SIGNAL_SOURCE: Record<Signal, SignalSource> = {
  error_ratio: "metrics",
  latency_p95_seconds: "metrics",
  request_rate: "metrics",
  restarts: "metrics",
  cpu_utilization: "metrics",
  memory_utilization: "metrics",
  unavailable_replicas: "metrics",
  error_logs: "logs",
  fatal_logs: "logs",
  slow_requests: "traces",
  slowest_route_p95_seconds: "traces",
};
export const SIGNAL_SOURCES: SignalSource[] = ["metrics", "logs", "traces"];

/** Ratios are stored 0..1 and shown as percentages. */
export const RATIO_SIGNALS: ReadonlySet<Signal> = new Set([
  "error_ratio",
  "cpu_utilization",
  "memory_utilization",
]);

export interface RuleState {
  binding_id: string;
  state: "ok" | "pending" | "firing";
  firing_since: string | null;
  last_value: number | null;
  evaluated_at: string;
  incident_id: string | null;
  environment: string;
  service: string;
}

export interface Episode {
  binding_id: string;
  environment: string;
  service: string;
  fired_at: string;
  resolved_at: string | null;
  peak: number | null;
  incident_id?: string | null;
  minutes?: number;
}

export interface AlertRule {
  id: string;
  project_id: string;
  environment_id: string | null;
  service_id: string | null;
  name: string;
  signal: Signal;
  operator: Operator;
  threshold: number;
  for_seconds: number;
  severity: Severity;
  enabled: boolean;
  muted_until: string | null;
  version: number;
  created_at: string;
  updated_at: string;
  target: { project: string; environment: string | null; service: string | null };
  can_manage: boolean;
  states: RuleState[];
  episodes: Episode[];
  status: RuleStatus;
}

export interface RuleSummary {
  total: number;
  firing: number;
  pending: number;
  ok: number;
  muted: number;
  disabled: number;
  no_data: number;
  fired_24h: number;
}

export interface RuleList {
  as_of: string;
  rules: AlertRule[];
  summary: RuleSummary;
}

export interface SeriesPoint {
  at: string;
  value: number | null;
}

export interface BindingSeries {
  binding_id: string;
  environment: string;
  service: string;
  series: SeriesPoint[];
}

export interface RuleHistory {
  rule_id: string;
  signal: Signal;
  since: string;
  bindings: BindingSeries[];
}

export interface RuleOptions {
  signals: Signal[];
  /** How often the incident runner judges rules on this deployment. */
  evaluation_interval_seconds?: number;
  projects: {
    id: string;
    key: string;
    environments: { id: string; key: string; services: { id: string; key: string }[] }[];
  }[];
}

export interface RuleDraft {
  project_id: string;
  environment_id: string | null;
  service_id: string | null;
  signal: Signal;
  operator: Operator;
  threshold: number;
  for_seconds: number;
  severity: Severity;
}

export interface RulePreview {
  count: number;
  minutes: number;
  episodes: Episode[];
  suggestion: { threshold: number; count: number } | null;
  bindings: number;
  history_hours: number;
  series: BindingSeries | null;
}

export function fetchRules(projectId?: string): Promise<RuleList> {
  const suffix = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
  return apiGet<RuleList>(`/v1/alert-rules${suffix}`);
}

export function fetchRuleOptions(): Promise<RuleOptions> {
  return apiGet<RuleOptions>("/v1/alert-rules/options");
}

export function fetchRuleHistory(ruleId: string): Promise<RuleHistory> {
  return apiGet<RuleHistory>(`/v1/alert-rules/${encodeURIComponent(ruleId)}/history`);
}

export function previewRule(csrfToken: string, draft: RuleDraft, signal?: AbortSignal) {
  return apiMutate<RulePreview>("/v1/alert-rules/preview", { csrfToken, body: draft, signal });
}

export function createRule(csrfToken: string, draft: RuleDraft & { name: string }) {
  return apiMutate<{ id: string; version: number }>("/v1/alert-rules", {
    csrfToken,
    body: draft,
  });
}

export function updateRule(
  csrfToken: string,
  ruleId: string,
  changes: Partial<Omit<RuleDraft, "project_id" | "environment_id" | "service_id">> & {
    name?: string;
    enabled?: boolean;
    expected_version: number;
  },
) {
  return apiMutate<{ id: string; version: number }>(
    `/v1/alert-rules/${encodeURIComponent(ruleId)}`,
    { csrfToken, body: changes },
  );
}

export function muteRule(csrfToken: string, ruleId: string, minutes: number) {
  return apiMutate<{ id: string; muted_until: string | null }>(
    `/v1/alert-rules/${encodeURIComponent(ruleId)}/mute`,
    { csrfToken, body: { minutes } },
  );
}

export function deleteRule(csrfToken: string, ruleId: string) {
  return apiMutate<{ deleted: string }>(`/v1/alert-rules/${encodeURIComponent(ruleId)}`, {
    csrfToken,
    method: "DELETE",
  });
}

/** A value in the signal's own unit, for display. */
export function formatSignal(
  signal: Signal,
  value: number | null | undefined,
  number: (v: number, o?: Intl.NumberFormatOptions) => string,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (RATIO_SIGNALS.has(signal)) {
    const pct = value * 100;
    return `${number(pct, { maximumFractionDigits: pct < 100 ? 1 : 0 })}%`;
  }
  if (signal === "latency_p95_seconds" || signal === "slowest_route_p95_seconds") {
    return value >= 1
      ? `${number(value, { maximumFractionDigits: 2 })}s`
      : `${number(value * 1000, { maximumFractionDigits: 0 })}ms`;
  }
  if (signal === "request_rate") return `${number(value, { maximumFractionDigits: 1 })}/s`;
  return number(value, { maximumFractionDigits: 1 });
}

/** Whether a value breaches a rule — the same comparison the server makes. */
export function breaches(operator: Operator, threshold: number, value: number): boolean {
  return operator === "<" ? value < threshold : value > threshold;
}
