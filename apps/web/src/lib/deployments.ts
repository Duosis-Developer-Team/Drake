/**
 * Typed deployment surface.
 *
 * The browser renders states the backend decided. It does not evaluate a
 * rollout, grade evidence, or build a workflow link — the API composes any
 * link from configured parts, so nothing here can point somewhere Drake
 * did not choose.
 */

import { apiGet } from "@/lib/api";

export type RolloutState =
  | "pending"
  | "progressing"
  | "healthy"
  | "degraded"
  | "failed"
  | "stalled"
  | "unknown";

export type EvidenceState = "verified" | "partial" | "unverified" | "conflict";
export type ComparisonVerdict = "improved" | "stable" | "regressed" | "insufficient_data";

export const ROLLOUT_LABELS: Record<RolloutState, string> = {
  pending: "Pending",
  progressing: "Progressing",
  healthy: "Healthy",
  degraded: "Degraded",
  failed: "Failed",
  stalled: "Stalled",
  unknown: "Unknown",
};

export const EVIDENCE_LABELS: Record<EvidenceState, string> = {
  verified: "Verified",
  partial: "Partial evidence",
  unverified: "Unverified",
  conflict: "Conflicting evidence",
};

/** What each evidence grade actually means, so nobody has to guess. */
export const EVIDENCE_DESCRIPTIONS: Record<EvidenceState, string> = {
  verified:
    "Commit, workflow run and image digest all line up with the running workload.",
  partial:
    "Some of the chain was observed, but it does not close end to end.",
  unverified:
    "Only a mutable image tag was seen. It may well be correct; Drake has no evidence for it.",
  conflict:
    "The workload declares one image digest and the node pulled another. Drake does not pick a side.",
};

export const VERDICT_LABELS: Record<ComparisonVerdict, string> = {
  improved: "Improved",
  stable: "Stable",
  regressed: "Regressed",
  insufficient_data: "Not enough data",
};

export interface DeploymentImage {
  name: string | null;
  image: string | null;
  digest: string | null;
  short_digest: string | null;
}

export interface DeploymentRow {
  id: string;
  namespace: string;
  workload_kind: string;
  workload_name: string;
  revision: number;
  observed_generation: number | null;
  images: DeploymentImage[];
  primary_image: string | null;
  primary_digest: string | null;
  short_digest: string | null;
  commit_sha: string | null;
  short_commit: string | null;
  workflow: {
    provider: string | null;
    repository: string | null;
    run_id: string | null;
    /** Composed by the server from a configured base, or null. */
    run_url: string | null;
  };
  evidence_state: EvidenceState;
  evidence_detail: Record<string, unknown>;
  rollout_state: RolloutState;
  rollout_reason: string | null;
  replicas: {
    desired: number | null;
    ready: number | null;
    updated: number | null;
    available: number | null;
  };
  rollout_started_at: string;
  rollout_completed_at: string | null;
  last_seen_at: string;
  cluster: { cluster_ref: string; id: string };
  project_key: string | null;
  environment_key: string | null;
  service_key: string | null;
  environment_service_id: string | null;
  binding_id: string | null;
  previous_revision_id: string | null;
  health_comparison: {
    verdict: ComparisonVerdict;
    incident_count: number;
    signals?: Record<string, SignalComparison>;
    missing_signals?: string[];
    before?: { from: string; to: string };
    after?: { from: string; to: string };
    computed_at?: string;
  } | null;
}

export interface SignalComparison {
  before: number | null;
  after: number | null;
  delta: number | null;
  direction: "improved" | "regressed" | "stable" | "unknown";
  lower_is_better: boolean;
}

export interface DeploymentPage {
  items: DeploymentRow[];
  next_cursor: string | null;
  total: number;
  limit: number;
}

export interface RevisionEntry {
  id: string;
  revision: number;
  rollout_state: RolloutState;
  evidence_state: EvidenceState;
  short_digest: string | null;
  short_commit: string | null;
  rollout_started_at: string;
  rollout_completed_at: string | null;
}

export interface RelatedIncident {
  id: string;
  state: string;
  severity: string;
  title: string;
  primary_reason: string;
  opened_at: string;
}

export interface DeploymentFilters {
  projectId?: string;
  environmentId?: string;
  environmentServiceId?: string;
  clusterId?: string;
  workloadKind?: string;
  rolloutState?: RolloutState;
  evidenceState?: EvidenceState;
  startedWithin?: string;
}

export function deploymentListPath(filters: DeploymentFilters): string {
  const query = new URLSearchParams();
  if (filters.projectId) query.set("project_id", filters.projectId);
  if (filters.environmentId) query.set("environment_id", filters.environmentId);
  if (filters.environmentServiceId)
    query.set("environment_service_id", filters.environmentServiceId);
  if (filters.clusterId) query.set("cluster_id", filters.clusterId);
  if (filters.workloadKind) query.set("workload_kind", filters.workloadKind);
  if (filters.rolloutState) query.set("rollout_state", filters.rolloutState);
  if (filters.evidenceState) query.set("evidence_state", filters.evidenceState);
  if (filters.startedWithin) query.set("started_within", filters.startedWithin);
  const suffix = query.toString() ? `?${query}` : "";
  return `/v1/deployments${suffix}`;
}

export async function fetchDeployments(
  filters: DeploymentFilters = {},
): Promise<DeploymentPage> {
  return apiGet<DeploymentPage>(deploymentListPath(filters));
}

export async function fetchRevisions(deploymentId: string): Promise<RevisionEntry[]> {
  const body = await apiGet<{ revisions: RevisionEntry[] }>(
    `/v1/deployments/${deploymentId}/revisions`,
  );
  return body.revisions;
}

export async function fetchRelatedIncidents(
  deploymentId: string,
): Promise<RelatedIncident[]> {
  const body = await apiGet<{ incidents: RelatedIncident[]; correlation_only: boolean }>(
    `/v1/deployments/${deploymentId}/incidents`,
  );
  return body.incidents;
}

/** Formatting only. `null` renders as a dash, never as zero. */
export function formatSignal(value: number | null): string {
  if (value === null || Number.isNaN(value)) return "—";
  if (Math.abs(value) >= 100) return value.toFixed(0);
  if (Math.abs(value) >= 1) return value.toFixed(2);
  return value.toFixed(4);
}

/** "12m" — how long a rollout took, or has been running. */
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
  return `${Math.floor(hours / 24)}d`;
}
