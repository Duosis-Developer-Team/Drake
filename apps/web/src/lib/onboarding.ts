/**
 * Typed onboarding surface.
 *
 * The browser renders a plan the backend built. It does not reconcile a
 * manifest against the catalog, decide whether an item blocks, compute a
 * plan digest, or judge whether a commit is still current — that reasoning
 * belongs to the server, where it can be tested and where a manifest is
 * treated as intent rather than as truth.
 *
 * Two things this module deliberately cannot express:
 *
 * - a repository URL, an owner/name pair, a branch, a file path or a
 *   manifest body. A repository is chosen from Drake's own projection by
 *   its row id; everything else is server-composed.
 * - a plan item. The UI shows what the server proposed and sends back a
 *   plan VERSION, never a set of edits.
 */

import { apiGet } from "@/lib/api";

export type SessionState =
  | "draft"
  | "discovery_pending"
  | "analyzing"
  | "needs_review"
  | "ready"
  | "approved"
  | "applying"
  | "imported"
  | "failed"
  | "cancelled"
  | "not_configured"
  | "stale"
  | "provider_unavailable";

export type PlanAction =
  | "create"
  | "link"
  | "update_metadata"
  | "no_change"
  | "conflict"
  | "unmapped"
  | "unsupported";

export type GitOpsState = "pending" | "active" | "failed" | "stale" | "cancelled";

export const SESSION_LABELS: Record<SessionState, string> = {
  draft: "Draft",
  discovery_pending: "Discovery pending",
  analyzing: "Analysing",
  needs_review: "Needs review",
  ready: "Ready to approve",
  approved: "Approved",
  applying: "Applying",
  imported: "Imported",
  failed: "Failed",
  cancelled: "Cancelled",
  not_configured: "Not configured",
  // A review of a commit is not a review of its successor.
  stale: "Stale",
  provider_unavailable: "GitHub unavailable",
};

export const ACTION_LABELS: Record<PlanAction, string> = {
  create: "Create",
  link: "Link to existing",
  update_metadata: "Update metadata",
  no_change: "No change",
  conflict: "Conflict",
  unmapped: "Unmapped",
  unsupported: "Unsupported",
};

export const GITOPS_LABELS: Record<GitOpsState, string> = {
  // Not "open": Drake has not created it yet, so nothing is open.
  pending: "Pending at GitHub",
  active: "Pull request open",
  failed: "Failed",
  stale: "Base commit moved",
  cancelled: "Cancelled",
};

/** Plan entity kinds. `workload_binding` is its own kind since migration
 * 0019 — it used to borrow `service` with a flag in `detail`, which made two
 * different decisions read as one. */
export type PlanEntityKind =
  | "project"
  | "environment"
  | "service"
  | "owner_team"
  | "repository"
  | "cluster_binding"
  | "namespace_binding"
  | "metric_profile"
  | "slo_profile"
  | "deployment_source"
  | "workload_binding";

/** One field's before and after, canonical on both sides. Shown so an
 * approval is informed rather than a list of field names. */
export interface FieldChange {
  before: unknown;
  after: unknown;
}

export interface PlanItem {
  entity_kind: PlanEntityKind;
  action: PlanAction;
  item_key: string;
  proposed_name: string | null;
  existing_entity_id: string | null;
  existing_name: string | null;
  reason_code: string | null;
  reason: string;
  detail: Record<string, unknown>;
  /** The canonical values apply will execute — bound to the plan digest. */
  payload: Record<string, unknown>;
  changes: Record<string, FieldChange>;
  blocking: boolean;
  /** False for an item that is shown but changes nothing; `detail`
   * carries a bounded reason code saying why. */
  materialized: boolean;
}

export interface Plan {
  id: string;
  plan_version: number;
  state: string;
  /** The exact commit this plan describes. */
  commit_sha: string;
  manifest_digest: string | null;
  analyzer_version: number;
  plan_digest: string;
  blocking_items: number;
  total_items: number;
  created_at: string;
  applicable: boolean;
}

export interface Analysis {
  id: string;
  commit_sha: string;
  analyzer_version: number;
  status: "complete" | "partial" | "failed";
  /** A budget stop. An incomplete picture is never a green light. */
  truncated: boolean;
  manifest_found: boolean;
  files_read: number;
  bytes_read: number;
  provider_calls: number;
  error_code: string | null;
  analyzed_at: string;
}

export interface Finding {
  finding_type: string;
  /** A path inside the repository. Never the content at it. */
  safe_path: string;
  confidence: "high" | "medium" | "low";
  evidence_kind: string;
  proposed_target: string | null;
  review_reason: string | null;
}

export interface GitOpsRequest {
  id: string;
  state: GitOpsState;
  branch_name: string;
  file_path: string;
  base_commit_sha: string;
  provider_pr_number: number | null;
  error_code: string | null;
  created_at: string;
  version: number;
}

export interface OnboardingSession {
  id: string;
  state: SessionState;
  reason_code: string | null;
  reason: string;
  analyzed_commit_sha: string | null;
  analyzed_at: string | null;
  approved_at: string | null;
  approved_plan_version: number | null;
  imported_project_id: string | null;
  imported_project_key: string | null;
  imported_at: string | null;
  version: number;
  created_at: string;
  repository: {
    id: string;
    owner: string;
    name: string;
    full_name: string;
    default_branch: string;
    security_gate: string | null;
  };
  plan: {
    plan_version: number;
    state: string;
    blocking_items: number;
    total_items: number;
    plan_digest: string;
    commit_sha: string;
  } | null;
  can_manage?: boolean;
  can_apply?: boolean;
  can_gitops?: boolean;
  gitops_requests?: GitOpsRequest[];
}

export interface GitHubStatus {
  configuration_state: "configured" | "not_configured";
  /** Which reference is absent — never its name and never its value. */
  missing_operator_inputs: string[];
  gitops_pr_enabled: boolean;
  can_manage: boolean;
  can_apply: boolean;
  can_gitops: boolean;
  sessions: number;
  needs_review: number;
  ready: number;
  imported: number;
  stale: number;
  provider_unavailable: number;
  analyses: number;
  analyses_truncated: number;
  analyses_failed: number;
  last_analyzed_at: string | null;
  gitops_pending: number;
  gitops_active: number;
  gitops_failed: number;
}

/** What an apply actually committed. Counters are additive; older clients
 * that read only the first three keep working. */
export interface ApplyResult {
  outcome: "applied" | "unchanged" | "failed";
  project_id: string | null;
  created_entities: number;
  linked_entities: number;
  unchanged_entities: number;
  no_change_count: number;
  metadata_updated: number;
  slo_definitions_created: number;
  slo_definitions_updated: number;
  bindings_created: number;
}

export interface SessionPage {
  items: OnboardingSession[];
  total: number;
  limit: number;
  offset: number;
}

export const MISSING_INPUT_LABELS: Record<string, string> = {
  feature_disabled: "The GitHub App integration is switched off.",
  app_identity: "No GitHub App identity is configured.",
  private_key_reference: "No private key reference is configured.",
  webhook_secret_reference: "No webhook secret reference is configured.",
};

export async function fetchStatus(): Promise<GitHubStatus> {
  return apiGet<GitHubStatus>("/v1/onboarding/github/status");
}

export async function fetchPlan(
  sessionId: string,
): Promise<{ plan: Plan | null; items: PlanItem[] }> {
  return apiGet<{ plan: Plan | null; items: PlanItem[] }>(
    `/v1/onboarding/sessions/${sessionId}/plan`,
  );
}

/** The wizard steps, in order. Named here so the UI and its test agree. */
export const WIZARD_STEPS = [
  "Integration status",
  "Repository",
  "Safe discovery",
  "Detected structure",
  "Review",
  "Approval",
  "Result",
] as const;

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

export function shortSha(value: string | null): string {
  return value ? value.slice(0, 12) : "—";
}
