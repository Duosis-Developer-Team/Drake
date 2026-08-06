/** Typed cluster inventory API surface (read-only, same-origin /v1).
 *
 * The browser only ever sees these read endpoints: agent enrollment,
 * certificates, and the internal ingest listener are never reachable or
 * referenced from web code.
 */

export type AgentStatus =
  | "not_configured"
  | "enrolled"
  | "connected"
  | "disconnected"
  | "revoked";

export type InventoryState =
  | "not_configured"
  | "empty"
  | "reconciling"
  | "fresh"
  | "stale"
  | "reconcile_required";

export type ResourceHealth = "healthy" | "degraded" | "unhealthy" | "unknown";

export interface AgentObservation {
  status: AgentStatus;
  agent_version?: string | null;
  last_heartbeat_at?: string | null;
  certificate_not_after?: string | null;
  certificate_expiry_warning?: boolean;
}

export interface InventoryFreshness {
  state: InventoryState;
  last_reconcile_at?: string | null;
  last_event_at?: string | null;
  active_resources?: number;
  missing_resources?: number;
}

export interface HealthRollup {
  total: number;
  healthy: number;
  degraded: number;
  unhealthy: number;
  unknown: number;
}

export interface PodRollup extends HealthRollup {
  crashloop: number;
  oom_killed: number;
  restarts: number;
}

export interface InventorySummary {
  cluster_id: string;
  agent: AgentObservation;
  inventory: InventoryFreshness;
  nodes: HealthRollup;
  namespaces: HealthRollup;
  pods: PodRollup;
  workloads: HealthRollup;
  persistent_volume_claims: HealthRollup;
  by_kind: Record<string, HealthRollup>;
  as_of: string;
}

export interface InventoryResourceRow {
  id: string;
  api_group: string;
  api_version: string;
  kind: string;
  namespace: string | null;
  name: string;
  health: ResourceHealth;
  health_reasons: string[];
  lifecycle: "active" | "missing";
  observed_at: string;
  last_seen_at: string;
  status_summary: Record<string, string | number | boolean | null>;
}

export interface InventoryResourceList {
  cluster_id: string;
  resources: InventoryResourceRow[];
  next_cursor: string | null;
  inventory: InventoryFreshness;
  as_of: string;
}

export interface InventoryResourceDetail extends InventoryResourceRow {
  cluster_id: string;
  uid: string;
  resource_version: string;
  labels: Record<string, string>;
  annotations: Record<string, string>;
  owners: { kind: string; name: string; uid: string }[];
  spec_summary: Record<string, string | number | boolean | null>;
  conditions: { type: string; status: string; reason?: string; message?: string }[];
  first_seen_at: string;
  provenance: { source: string; last_snapshot_id: string | null };
  inventory: InventoryFreshness;
  as_of: string;
}

/** Core collection allowlist offered by the kind filter. Optional
 * monitoring CRD kinds are deliberately NOT named in browser source (the
 * provider-guard forbids provider vocabulary here); they remain fully
 * browsable through the data-driven by-kind links on the cluster page. */
export const INVENTORY_KINDS = [
  "Namespace",
  "Node",
  "Pod",
  "Service",
  "EndpointSlice",
  "Deployment",
  "ReplicaSet",
  "StatefulSet",
  "DaemonSet",
  "Job",
  "CronJob",
  "PersistentVolumeClaim",
  "PersistentVolume",
  "StorageClass",
  "HorizontalPodAutoscaler",
  "PodDisruptionBudget",
  "ResourceQuota",
  "LimitRange",
  "Event",
] as const;
