/** Typed catalog API surface (read-only, same-origin /v1). */

export interface Provenance {
  kind: string;
  ref: string;
  revision: string;
  accepted_at: string;
}

export interface ScopeRef {
  type: string;
  ref: string;
}

export type OperationalState =
  | "not_configured"
  | "unknown"
  | "stale"
  | "degraded"
  | "ok";

export interface Project {
  id: string;
  project_key: string;
  display_name: string;
  lifecycle: "active" | "archived";
  criticality: "low" | "medium" | "high" | "critical";
  tenant_model: string;
  repository: { provider: string; owner: string; name: string; default_branch: string };
  version: number;
  scope: ScopeRef;
  source: Provenance;
  counts: { environments: number; services: number };
  as_of: string;
  owners?: { team: string; role: string }[];
  operational?: Record<string, OperationalState>;
}

export interface Environment {
  id: string;
  environment_key: string;
  runtime: "kubernetes" | "external";
  branch: string;
  criticality: string;
  namespace: string | null;
  lifecycle: string;
  cluster: { ref: string; display_name: string } | null;
  /** Typed provider identity for an external runtime; null for Kubernetes. */
  hosting_provider: string | null;
  /**
   * Concepts this runtime does not have. Distinct from a field that is
   * simply unset: an external application has no namespace, whereas a
   * Kubernetes environment without one is a real gap. Additive, so older
   * clients keep reading `cluster` and `namespace` as before.
   */
  not_applicable?: string[];
  version: number;
  scope: ScopeRef;
  source: Provenance;
  as_of: string;
  operational?: Record<string, OperationalState>;
}

export interface ServiceSummary {
  id: string;
  service_key: string;
  display_name: string;
  component: string;
  runtime: string;
  metrics_profile: string;
  lifecycle: string;
  version: number;
  scope: ScopeRef;
  source: Provenance;
}

export interface ServiceDetail extends ServiceSummary {
  workload_selector: Record<string, string>;
  health: Record<string, string>;
  operational: Record<string, OperationalState>;
  as_of: string;
}

export interface Cluster {
  id: string;
  cluster_ref: string;
  display_name: string;
  site: string;
  lifecycle: string;
  version: number;
  scope: ScopeRef;
  source: Provenance;
  operational: Record<string, OperationalState>;
  as_of: string;
  referenced_environments?: {
    project_key: string;
    environment_key: string;
    namespace: string | null;
  }[];
}

export interface IntegrationHealth {
  integration_type: string;
  scope: ScopeRef;
  configuration_state: "not_configured" | "configured";
  observed_state: OperationalState;
  last_sync_attempt_at: string | null;
  last_success_at: string | null;
  last_error_code: string | null;
  schema_version: number;
  as_of: string;
}

export interface SearchResult {
  kind: "project" | "environment" | "service" | "cluster";
  id: string;
  key: string;
  display_name: string;
  project_key: string | null;
  parent_id: string | null;
  project_id: string | null;
}

export interface CatalogContext {
  projects: number;
  environments: number;
  clusters: number;
  as_of: string;
}
