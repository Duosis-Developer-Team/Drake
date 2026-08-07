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
