/**
 * Typed incident surface.
 *
 * The browser renders a lifecycle the backend owns. It does not decide when
 * an incident opens, when it resolves, or what its title is — there is no
 * threshold in this file, no state transition, and no title composition,
 * because a second implementation of those rules is how a screen starts
 * disagreeing with the system it is showing.
 *
 * The only mutation is acknowledge, and it sends a version and nothing
 * else: no actor (that comes from the session) and no free text.
 */

import { apiGet, apiMutate } from "@/lib/api";
import type { ServiceHealthStatus } from "@/lib/serviceHealth";

export type IncidentState = "open" | "acknowledged" | "resolved";
export type IncidentSeverity = "critical";

export interface IncidentBinding {
  id: string;
  namespace: string;
  workload_kind: string;
  workload_name: string;
  cluster_ref: string;
}

export interface IncidentSummary {
  id: string;
  state: IncidentState;
  severity: IncidentSeverity;
  title: string;
  primary_reason: string;
  opened_at: string;
  last_critical_at: string;
  acknowledged_at: string | null;
  resolved_at: string | null;
  version: number;
  project_key: string;
  environment_key: string;
  service_key: string;
  environment_service_id: string;
  binding: IncidentBinding;
  current_health: {
    status: ServiceHealthStatus;
    reasons: string[];
    last_observed_at: string | null;
  } | null;
}

export interface IncidentDetail extends IncidentSummary {
  opening_reasons: string[];
  binding_revision: number;
  resolution_source: string | null;
  acknowledged_by: { display_name: string; email: string } | null;
  project_id: string;
  environment_id: string;
  can_acknowledge: boolean;
}

export type IncidentEventType =
  | "opened"
  | "acknowledged"
  | "recovery_started"
  | "recovery_interrupted"
  | "auto_resolved";

export interface IncidentEvent {
  event_type: IncidentEventType;
  occurred_at: string;
  detail: Record<string, unknown>;
  actor: string | null;
}

export interface IncidentPage {
  items: IncidentSummary[];
  next_cursor: string | null;
  total: number;
  limit: number;
}

export interface HealthTransition {
  previous_status: ServiceHealthStatus | null;
  new_status: ServiceHealthStatus;
  reasons: string[];
  computed_at: string;
  recorded_at: string;
  binding_revision: number;
}

/** The filter vocabulary. Mirrors the server's allowlist; anything else is
 * refused there, not merely absent here. */
export const INCIDENT_STATES: IncidentState[] = ["open", "acknowledged", "resolved"];
export const OPENED_WINDOWS = ["24h", "7d", "30d"] as const;
export type OpenedWindow = (typeof OPENED_WINDOWS)[number];

export const STATE_LABELS: Record<IncidentState, string> = {
  open: "Open",
  acknowledged: "Acknowledged",
  resolved: "Resolved",
};

export const EVENT_LABELS: Record<IncidentEventType, string> = {
  opened: "Incident opened",
  acknowledged: "Acknowledged",
  recovery_started: "Recovery started",
  recovery_interrupted: "Recovery interrupted",
  auto_resolved: "Automatically resolved",
};

/** One sentence per event, so a timeline reads without a legend. */
export const EVENT_DESCRIPTIONS: Record<IncidentEventType, string> = {
  opened: "Two consecutive trustworthy critical evaluations.",
  acknowledged: "A responder confirmed they have seen this. Monitoring continues.",
  recovery_started: "One healthy evaluation. A second one resolves the incident.",
  recovery_interrupted: "The service stopped reporting healthy before recovery completed.",
  auto_resolved: "Two consecutive trustworthy healthy evaluations.",
};

export interface IncidentFilters {
  projectId?: string;
  environmentId?: string;
  environmentServiceId?: string;
  state?: IncidentState;
  severity?: IncidentSeverity;
  openedWithin?: OpenedWindow;
  cursor?: string;
}

export function incidentListPath(filters: IncidentFilters): string {
  const query = new URLSearchParams();
  if (filters.projectId) query.set("project_id", filters.projectId);
  if (filters.environmentId) query.set("environment_id", filters.environmentId);
  if (filters.environmentServiceId)
    query.set("environment_service_id", filters.environmentServiceId);
  if (filters.state) query.set("state", filters.state);
  if (filters.severity) query.set("severity", filters.severity);
  if (filters.openedWithin) query.set("opened_within", filters.openedWithin);
  if (filters.cursor) query.set("cursor", filters.cursor);
  const suffix = query.toString() ? `?${query}` : "";
  return `/v1/incidents${suffix}`;
}

export async function fetchIncidents(filters: IncidentFilters = {}): Promise<IncidentPage> {
  return apiGet<IncidentPage>(incidentListPath(filters));
}

export async function fetchIncident(incidentId: string): Promise<IncidentDetail> {
  return apiGet<IncidentDetail>(`/v1/incidents/${incidentId}`);
}

export async function fetchIncidentEvents(incidentId: string): Promise<IncidentEvent[]> {
  const body = await apiGet<{ events: IncidentEvent[] }>(`/v1/incidents/${incidentId}/events`);
  return body.events;
}

export async function acknowledgeIncident(
  csrfToken: string,
  incidentId: string,
  expectedVersion: number,
): Promise<{ id: string; state: IncidentState; version: number; changed: boolean }> {
  return apiMutate(`/v1/incidents/${incidentId}/acknowledge`, {
    csrfToken,
    body: { expected_version: expectedVersion },
  });
}

export async function fetchServiceIncidents(bindingId: string): Promise<IncidentSummary[]> {
  const body = await apiGet<{ items: IncidentSummary[] }>(
    `/v1/service-health/bindings/${bindingId}/incidents`,
  );
  return body.items;
}

export async function fetchHealthTransitions(bindingId: string): Promise<HealthTransition[]> {
  const body = await apiGet<{ transitions: HealthTransition[] }>(
    `/v1/service-health/bindings/${bindingId}/transitions`,
  );
  return body.transitions;
}

/** "1h 12m" — how long an incident has been going, or how long it ran.
 * Formatting only; the timestamps it reads are the server's. */
export function formatDuration(fromIso: string, toIso: string | null): string {
  const from = Date.parse(fromIso);
  const to = toIso ? Date.parse(toIso) : Date.now();
  if (Number.isNaN(from) || Number.isNaN(to)) return "—";
  const seconds = Math.max(0, Math.round((to - from) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ${minutes % 60}m`;
  return `${Math.floor(hours / 24)}d ${hours % 24}h`;
}
