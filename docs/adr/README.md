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
