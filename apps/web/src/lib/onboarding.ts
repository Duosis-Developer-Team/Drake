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

import { apiGet, apiMutate } from "@/lib/api";

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
 * that read only the first three keep working.
 *
 * The four extended counters are `number | null`, and the `null` is load
 * bearing. Apply receipts written before migration 0020 never recorded
 * them, so a replay of one of those reports "not recorded" rather than a
 * zero that would read as a measurement. Do NOT normalise it to 0 — that
 * turns "we do not know" into "nothing happened", which is the exact
 * confusion the backend went to the trouble of avoiding. Render it as
 * unknown, or omit the counter. */
export interface ApplyResult {
  outcome: "applied" | "unchanged" | "failed";
  project_id: string | null;
  created_entities: number;
  linked_entities: number;
  unchanged_entities: number;
  no_change_count: number;
  metadata_updated: number | null;
  slo_definitions_created: number | null;
  slo_definitions_updated: number | null;
  bindings_created: number | null;
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

/** A repository an operator may start an onboarding on. */
export interface RepositoryCandidate {
  id: string;
  full_name: string;
  default_branch: string;
  onboarding_state: string;
  access_state: string;
  security_gate: string | null;
  active_session_id: string | null;
  startable: boolean;
  reason_code: string | null;
}

export interface RepositoryCandidatePage {
  items: RepositoryCandidate[];
  next_cursor: string | null;
}

/** Why a repository cannot be started, in words an operator can act on. */
export const CANDIDATE_BLOCKERS: Record<string, string> = {
  security_gate_open: "Closed by a manual security review. Ask a platform owner to clear it.",
  repository_unavailable:
    "Drake cannot reach this repository — it is archived, disabled, or the installation lost access.",
  repository_not_ready:
    "Drake has not finished projecting this repository. Reconcile it first, then try again.",
  session_in_progress: "An onboarding is already open for this repository.",
};

/**
 * Bounded server codes, mapped to sentences a person can act on.
 *
 * The server never sends provider text, and neither does this: every entry
 * is Drake's own wording for a code Drake defined. An unmapped code falls
 * back to the server's message, which is also Drake's.
 */
export const ERROR_GUIDANCE: Record<string, string> = {
  security_gate_open: "This repository is closed by a manual security review.",
  provider_unavailable: "GitHub could not be reached. Nothing was changed.",
  permission_missing: "The GitHub App installation is missing a read permission it needs.",
  version_conflict: "This session changed while you were looking at it. Reloaded.",
  plan_stale: "The repository moved after this plan was reviewed. Analyse again.",
  plan_blocked: "This plan has conflicts that must be resolved before it can be approved.",
  plan_integrity_mismatch:
    "This plan no longer matches what was approved. Analyse and review it again.",
  idempotency_key_reused: "That request was already used for a different plan version.",
  invalid_session_state: "This action is not available from the session's current state.",
  analysis_required: "Analyse the repository first.",
  gitops_disabled: "Repository writes are disabled. No branch or pull request is created.",
  legacy_onboarding_retired:
    "That path is retired. Onboarding now runs through a reviewed plan.",
  already_imported: "This session has already been applied.",
  already_approved: "This plan version has already been approved.",
  plan_not_found: "That plan version no longer exists. Reloaded.",
  plan_superseded: "A newer analysis replaced this plan. Review the current one.",
};

/** Codes that mean the client's copy of the session is out of date. */
export const REFETCH_CODES = new Set([
  "version_conflict",
  "plan_stale",
  "plan_superseded",
  "plan_not_found",
  "invalid_session_state",
  "plan_integrity_mismatch",
  "already_imported",
  "already_approved",
]);

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

/**
 * A counter the server may not have recorded.
 *
 * `null` means an apply receipt written before migration 0020 replayed, and
 * those never stored the extended counters. Rendering it as 0 would turn
 * "we do not know" into "nothing happened".
 */
export function counterLabel(value: number | null): string {
  return value === null ? "Not recorded" : String(value);
}

// ---------------------------------------------------------------------------
// mutations
// ---------------------------------------------------------------------------

export async function fetchRepositoryCandidates(
  options: { search?: string; limit?: number; cursor?: string } = {},
): Promise<RepositoryCandidatePage> {
  const query = new URLSearchParams();
  if (options.search) query.set("search", options.search);
  if (options.limit) query.set("limit", String(options.limit));
  if (options.cursor) query.set("cursor", options.cursor);
  const suffix = query.toString();
  return apiGet<RepositoryCandidatePage>(
    `/v1/onboarding/repositories${suffix ? `?${suffix}` : ""}`,
  );
}

/** One repository's startability, target-specific and server-decided. */
export async function fetchRepositoryCandidate(
  repositoryId: string,
): Promise<RepositoryCandidate> {
  return apiGet<RepositoryCandidate>(`/v1/onboarding/repositories/${repositoryId}`);
}

export async function fetchSession(sessionId: string): Promise<OnboardingSession> {
  return apiGet<OnboardingSession>(`/v1/onboarding/sessions/${sessionId}`);
}

export async function createSession(
  csrfToken: string,
  repositoryId: string,
): Promise<{ session_id: string; created: boolean }> {
  return apiMutate<{ session_id: string; created: boolean }>("/v1/onboarding/sessions", {
    csrfToken,
    body: { repository_id: repositoryId },
  });
}

export async function analyzeSession(
  csrfToken: string,
  sessionId: string,
): Promise<Record<string, unknown>> {
  return apiMutate<Record<string, unknown>>(
    `/v1/onboarding/sessions/${sessionId}/analyze`,
    { csrfToken },
  );
}

export async function approvePlan(
  csrfToken: string,
  sessionId: string,
  planVersion: number,
  expectedVersion: number,
): Promise<Record<string, unknown>> {
  return apiMutate<Record<string, unknown>>(`/v1/onboarding/sessions/${sessionId}/approve`, {
    csrfToken,
    body: { plan_version: planVersion, expected_version: expectedVersion },
  });
}

/**
 * Apply an approved plan.
 *
 * The key is the caller's, and travels twice: in the `Idempotency-Key`
 * header and in the body. The server binds its apply receipt to the body
 * value, so the two must be the same value or a retry would be recorded
 * under a key nobody sent. A caller that retries MUST pass the same key it
 * used the first time — see `useApplyKey` in the session screen.
 */
export async function applyPlan(
  csrfToken: string,
  sessionId: string,
  planVersion: number,
  idempotencyKey: string,
): Promise<ApplyResult> {
  return apiMutate<ApplyResult>(`/v1/onboarding/sessions/${sessionId}/apply`, {
    csrfToken,
    idempotencyKey,
    body: { plan_version: planVersion, idempotency_key: idempotencyKey },
  });
}

export async function cancelSession(
  csrfToken: string,
  sessionId: string,
  expectedVersion: number,
): Promise<{ session_id: string; state: string }> {
  return apiMutate<{ session_id: string; state: string }>(
    `/v1/onboarding/sessions/${sessionId}/cancel`,
    { csrfToken, body: { expected_version: expectedVersion } },
  );
}

export async function requestGitOps(
  csrfToken: string,
  sessionId: string,
): Promise<{ id: string; state: string; created: boolean }> {
  return apiMutate<{ id: string; state: string; created: boolean }>(
    `/v1/onboarding/sessions/${sessionId}/gitops-request`,
    { csrfToken, body: {} },
  );
}

/**
 * Where the generated manifest lives.
 *
 * A path, not a fetch: the file is downloaded by navigation so the browser
 * never holds the bytes, and the page never renders them. A manifest is a
 * file to commit, not markup to put in a document.
 */
export function manifestDraftPath(sessionId: string): string {
  return `/v1/onboarding/sessions/${sessionId}/manifest-draft`;
}
