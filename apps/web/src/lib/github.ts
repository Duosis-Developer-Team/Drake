/** Typed GitHub integration surface (read-only, same-origin /v1).
 *
 * The browser never sees a private key, JWT, installation token, or
 * webhook secret: the API does not return them, and nothing here models
 * them. Only readiness flags and observed metadata cross this boundary.
 */

export type OnboardingState =
  | "discovered"
  | "validating"
  | "ready"
  | "blocked"
  | "degraded"
  | "disabled";

export type PolicyVerdict = "pass" | "warn" | "fail" | "unknown";
export type AccessState = "accessible" | "suspended" | "removed";

export interface GitHubStatus {
  configuration_state: "configured" | "not_configured";
  missing_operator_inputs: string[];
  installations: number;
  repositories: number;
  blocked_repositories: number;
  supported_events: string[];
  policy_profiles: string[];
  as_of: string;
}

export interface GitHubInstallation {
  id: string;
  external_id: number;
  account_login: string;
  account_type: string;
  app_slug: string;
  repository_selection: string;
  granted_permissions: Record<string, string>;
  subscribed_events: string[];
  state: "active" | "suspended" | "deleted";
  suspended_at: string | null;
  last_reconciled_at: string | null;
  last_error_code: string | null;
}

export interface GitHubRepository {
  id: string;
  provider: string;
  external_id: number;
  owner_login: string;
  name: string;
  full_name: string;
  private: boolean;
  visibility: string;
  archived: boolean;
  disabled: boolean;
  default_branch: string;
  onboarding_state: OnboardingState;
  state_reason: string | null;
  security_gate: string | null;
  security_gate_reason: string;
  access_state: AccessState;
  last_reconciled_at: string | null;
  last_policy_evaluated_at: string | null;
  last_error_code: string | null;
  installation_external_id: number;
  /** Outstanding installation-level work: this view is not yet complete. */
  pending_reconciliation?: boolean;
  as_of: string;
}

export interface PolicyRuleResult {
  rule_id: string;
  title: string;
  verdict: PolicyVerdict;
  severity: "low" | "medium" | "high" | "critical";
  expected: string;
  observed: string;
  blocking: boolean;
  remediation: string;
  evidence: Record<string, unknown>;
}

export interface PolicySnapshot {
  repository_id: string;
  state: "evaluated" | "never_evaluated";
  id?: string;
  profile?: string;
  overall?: PolicyVerdict;
  blocking_count?: number;
  unknown_count?: number;
  results: PolicyRuleResult[];
  evidence_digest?: string;
  dry_run?: boolean;
  evaluated_at?: string;
  as_of: string;
}

/** How long before a reconciliation is presented as stale. */
export const RECONCILE_STALE_SECONDS = 24 * 60 * 60;

export function isStale(timestamp: string | null, now: number = Date.now()): boolean {
  if (!timestamp) return true;
  const parsed = Date.parse(timestamp);
  if (Number.isNaN(parsed)) return true;
  return now - parsed > RECONCILE_STALE_SECONDS * 1000;
}

// --- catalog onboarding (Sprint 5B) --------------------------------------

/**
 * Catalog onboarding is a separate axis from `onboarding_state`, which
 * describes the GitHub projection. A repository can be perfectly
 * reconciled and simply not onboarded into the Drake catalog yet.
 */
export type OnboardingDraftState =
  | "not_started"
  | "scanning"
  | "needs_input"
  | "invalid"
  | "ready_to_import"
  | "imported"
  | "failed";

/** Where the manifest came from. Only `repository` may be imported. */
export type ManifestSource = "none" | "repository" | "operator_draft";

export interface ManifestFinding {
  path: string;
  rule: string;
  message: string;
}

export interface DiscoveredFile {
  path: string;
  size: number;
  sha256: string;
}

export interface Detection {
  kind: string;
  value: string;
  evidence: string;
  confidence: string;
}

export interface DiscoverySummary {
  commit_sha?: string;
  default_branch?: string;
  files?: DiscoveredFile[];
  detections?: Detection[];
  manifest_found?: boolean;
  truncated?: boolean;
  provider_calls?: number;
  total_bytes?: number;
}

export interface OperatorInput {
  field: string;
  reason: string;
}

export interface OnboardingDraft {
  repository_id: string;
  state: OnboardingDraftState;
  commit_sha: string;
  manifest_source: ManifestSource;
  manifest_digest?: string;
  findings: ManifestFinding[];
  discovery: DiscoverySummary;
  draft_manifest?: string | null;
  reason_code?: string | null;
  accepted_project_id?: string | null;
  accepted_at?: string | null;
  scanned_at?: string | null;
  revision?: number;
  operator_inputs_required: OperatorInput[];
  /** The single field the Import action keys off. */
  importable: boolean;
  as_of: string;
}

export interface ManifestValidation {
  repository_id: string;
  valid: boolean;
  findings: ManifestFinding[];
  importable: boolean;
  next_step: string;
  as_of: string;
}

/*
 * The Sprint 5B onboarding path helpers used to live here — scan, validate,
 * download, import. Those endpoints answer 410 now, and keeping typed
 * builders for them would be an invitation to call one: a path helper reads
 * as a supported route.
 *
 * Onboarding is `@/lib/onboarding`, which drives the reviewed session flow.
 */

/** Human-readable reason a repository cannot be onboarded right now. */
export function blockedReason(repository: GitHubRepository): string | null {
  if (repository.security_gate) return "A manual security gate is open on this repository.";
  if (repository.access_state !== "accessible")
    return "Drake does not currently have access to this repository.";
  if (repository.onboarding_state !== "ready")
    return "Reconcile the repository first so Drake has a complete picture of it.";
  return null;
}
