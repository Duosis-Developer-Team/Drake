/**
 * Typed protection surface.
 *
 * The browser renders a verdict the backend reached. It does not compare a
 * backup age to an RPO, decide whether an artifact counts, or combine the
 * two axes — that arithmetic belongs to the server, where it can be tested
 * against evidence.
 *
 * The two axes are separate on purpose: a fresh, checksummed, offsite
 * backup nobody has ever restored is `protected_unverified`, and calling
 * that "protected" is the exact comfort this screen exists to remove.
 */

import { apiGet } from "@/lib/api";

export type BackupState = "protected" | "at_risk" | "overdue" | "failed" | "unknown";
export type RecoverabilityState = "verified" | "unverified" | "failed" | "unknown";
export type OverallState =
  | "recoverable_verified"
  | "protected_unverified"
  | "at_risk"
  | "overdue"
  | "failed"
  | "unknown";

export const BACKUP_LABELS: Record<BackupState, string> = {
  protected: "Protected",
  at_risk: "At risk",
  overdue: "Overdue",
  failed: "Failed",
  unknown: "Unknown",
};

export const RECOVERABILITY_LABELS: Record<RecoverabilityState, string> = {
  verified: "Verified",
  unverified: "Never verified",
  failed: "Restore failed",
  unknown: "Unknown",
};

export const OVERALL_LABELS: Record<OverallState, string> = {
  recoverable_verified: "Recoverable (verified)",
  protected_unverified: "Protected, unverified",
  at_risk: "At risk",
  overdue: "Overdue",
  failed: "Failed",
  unknown: "Unknown",
};

/** One sentence per reason. The API also ships its own `messages`; these
 * are the short forms a table can hold. */
export const REASON_LABELS: Record<string, string> = {
  backup_overdue: "Backup overdue",
  latest_run_failed: "Last backup failed",
  artifact_missing: "No artifact observed",
  integrity_missing: "Integrity check missing",
  integrity_failed: "Integrity check failed",
  offsite_missing: "No offsite copy",
  restore_never_verified: "Never restore-tested",
  restore_verification_expired: "Restore verification expired",
  restore_failed: "Restore drill failed",
  rto_exceeded: "Restore slower than RTO",
  reporter_stale: "Reporter stale",
};

export interface ProtectionEvaluation {
  backup_state: BackupState;
  recoverability_state: RecoverabilityState;
  overall_state: OverallState;
  reasons: string[];
  last_success_at: string | null;
  last_attempt_at: string | null;
  last_restore_at: string | null;
  reporter_seen_at: string | null;
  consecutive_failures: number;
  computed_at: string | null;
}

export interface ProtectionPolicy {
  id: string;
  display_name: string;
  store_key: string;
  store_kind: string;
  provider_key: string;
  connector_key: string;
  rpo_seconds: number;
  rto_seconds: number | null;
  restore_verification_ttl_seconds: number | null;
  requires_offsite: boolean;
  requires_integrity_check: boolean;
  enabled: boolean;
  schedule_description: string | null;
  project_key: string;
  environment_key: string | null;
  project_id: string;
  environment_id: string | null;
  /** `null` until something has been evaluated — never a cheerful default. */
  evaluation: ProtectionEvaluation | null;
}

export interface ProtectionPage {
  items: ProtectionPolicy[];
  total: number;
  limit: number;
  offset: number;
}

export interface ProtectionSummary {
  total_policies: number;
  backup: Record<string, number>;
  recoverability: Record<string, number>;
  overall: Record<string, number>;
}

export interface BackupRun {
  id: string;
  provider_run_id: string;
  status: string;
  started_at: string;
  completed_at: string | null;
  duration_seconds: number | null;
  error_code: string | null;
  attempt: number;
  /** When the provider says it happened, and when Drake heard. A late
   * delivery is visible as a late delivery, not as a late backup. */
  source_event_at: string;
  ingested_at: string;
  artifact_count: number;
}

export interface RestoreDrill {
  id: string;
  drill_external_id: string;
  target_profile: string;
  result: string;
  started_at: string;
  completed_at: string | null;
  duration_seconds: number | null;
  rto_met: boolean | null;
  validations: Record<string, boolean>;
  error_code: string | null;
}

export interface ProtectionIncident {
  id: string;
  state: string;
  title: string;
  primary_reason: string;
  opened_at: string;
  resolved_at: string | null;
}

export interface ProtectionFilters {
  projectId?: string;
  environmentId?: string;
  storeKey?: string;
  connectorKey?: string;
  backupState?: BackupState;
  recoverabilityState?: RecoverabilityState;
  offsiteState?: "present" | "missing";
  window?: string;
}

export function protectionListPath(filters: ProtectionFilters): string {
  const query = new URLSearchParams();
  if (filters.projectId) query.set("project_id", filters.projectId);
  if (filters.environmentId) query.set("environment_id", filters.environmentId);
  if (filters.storeKey) query.set("store_key", filters.storeKey);
  if (filters.connectorKey) query.set("connector_key", filters.connectorKey);
  if (filters.backupState) query.set("backup_state", filters.backupState);
  if (filters.recoverabilityState)
    query.set("recoverability_state", filters.recoverabilityState);
  if (filters.offsiteState) query.set("offsite_state", filters.offsiteState);
  if (filters.window) query.set("window", filters.window);
  const suffix = query.toString() ? `?${query}` : "";
  return `/v1/protection/policies${suffix}`;
}

export async function fetchSummary(): Promise<ProtectionSummary> {
  return apiGet<ProtectionSummary>("/v1/protection/summary");
}

export async function fetchPolicies(filters: ProtectionFilters = {}): Promise<ProtectionPage> {
  return apiGet<ProtectionPage>(protectionListPath(filters));
}

export async function fetchRuns(policyId: string): Promise<BackupRun[]> {
  const body = await apiGet<{ runs: BackupRun[] }>(
    `/v1/protection/policies/${policyId}/runs`,
  );
  return body.runs;
}

export async function fetchDrills(policyId: string): Promise<RestoreDrill[]> {
  const body = await apiGet<{ drills: RestoreDrill[] }>(
    `/v1/protection/policies/${policyId}/drills`,
  );
  return body.drills;
}

export async function fetchProtectionIncidents(
  policyId: string,
): Promise<ProtectionIncident[]> {
  const body = await apiGet<{ incidents: ProtectionIncident[] }>(
    `/v1/protection/policies/${policyId}/incidents`,
  );
  return body.incidents;
}

/** "4d ago", or an honest dash. Never "just now" for a missing value. */
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

export function formatDuration(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

export function formatBytes(value: number | null): string {
  if (value === null) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let scaled = value;
  let index = 0;
  while (scaled >= 1024 && index < units.length - 1) {
    scaled /= 1024;
    index += 1;
  }
  return `${scaled.toFixed(1)} ${units[index]}`;
}

/** RPO expressed the way a policy states it, not in raw seconds. */
export function formatWindow(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds % 86400 === 0) return `${seconds / 86400}d`;
  if (seconds % 3600 === 0) return `${seconds / 3600}h`;
  return `${Math.round(seconds / 60)}m`;
}
