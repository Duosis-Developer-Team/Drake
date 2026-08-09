# Architecture Decision Records

Foundational decisions for Drake. Each ADR is deliberately public-safe: it
records the decision and its rationale without internal topology, inventory,
or operational details.

| ADR | Decision |
|---|---|
| [ADR-0001](ADR-0001-control-plane-not-telemetry-backend.md) | Drake is a control plane, not a telemetry backend |
| [ADR-0002](ADR-0002-fastapi-modular-monolith.md) | FastAPI modular monolith |
| [ADR-0003](ADR-0003-browser-api-boundary.md) | The browser talks only to the Drake API |
| [ADR-0004](ADR-0004-query-broker.md) | Query Broker with templates and budgets |
| [ADR-0005](ADR-0005-read-only-cluster-agent.md) | Read-only cluster agent |
| [ADR-0006](ADR-0006-static-repository-analysis.md) | Static-only GitHub repository analysis |
| [ADR-0007](ADR-0007-project-manifest-contract.md) | Versioned project manifest contract |
| [ADR-0008](ADR-0008-tenant-measurement-honesty.md) | Tenant measurement honesty |
| [ADR-0009](ADR-0009-backup-vs-recoverability.md) | Backup evidence ≠ recoverability |
| [ADR-0010](ADR-0010-deny-by-default-rbac.md) | Deny-by-default scoped RBAC |
| [ADR-0011](ADR-0011-state-semantics.md) | Unknown/stale/partial state semantics |
| [ADR-0012](ADR-0012-no-early-microservices.md) | No early microservices |
| [ADR-0013](ADR-0013-auth-session-architecture.md) | Authentication and session architecture |
| [ADR-0014](ADR-0014-catalog-authority-and-scope-topology.md) | Catalog authority and scope topology |
| [ADR-0015](ADR-0015-metrics-registry-and-query-broker-runtime.md) | Metrics registry and Query Broker runtime |
| [ADR-0016](ADR-0016-agent-enrollment-and-mtls-trust-boundary.md) | Agent enrollment and the mTLS trust boundary |
| [ADR-0017](ADR-0017-snapshot-watch-ordering-and-atomic-projection.md) | Snapshot/watch ordering and atomic inventory projection |
| [ADR-0018](ADR-0018-inventory-allowlist-bounded-metadata-and-health.md) | Inventory allowlist, bounded metadata, and health derivation |
| [ADR-0019](ADR-0019-github-app-identity-and-webhook-trust-boundary.md) | GitHub App identity and the webhook trust boundary |
| [ADR-0020](ADR-0020-repository-onboarding-and-policy-evaluation.md) | Repository onboarding and read-only policy evaluation |
| [ADR-0021](ADR-0021-production-edge-contract.md) | Production edge contract: one origin, /v1 direct to the API |
| [ADR-0022](ADR-0022-service-health.md) | Service health computed from stored service→workload bindings |
| [ADR-0023](ADR-0023-incident-lifecycle.md) | Incident lifecycle driven by consecutive trustworthy health verdicts |
| [ADR-0024](ADR-0024-notification-delivery.md) | Notification routing and reliable, deduplicated delivery |
| [ADR-0025](ADR-0025-authoritative-onboarding-and-plan-apply-parity.md) | One authoritative onboarding path; the approved plan is the instruction set |
