# Sprint 0 — Discovery Report

**Status:** Complete
**Scope:** Repository/foundation discovery and Sprint 0 execution baseline.
This report is public-safe: it contains no internal topology, host details,
credentials, or non-public operational information.

## 1. Product baseline

Drake is an internal observability and operations control plane that unifies:

- Kubernetes cluster/workload health and inventory,
- application golden signals (traffic, errors, latency, saturation),
- PostgreSQL health, capacity, and growth,
- tenant plan/entitlement/usage snapshots with measurement honesty,
- backup, offsite, integrity, and restore-drill evidence,
- deployment (commit → image digest → rollout) correlation,
- alert-to-incident projection,

behind one project catalog and a purpose-built UI. The authoritative product
and architecture dossier is maintained internally; its machine-validatable
contracts (project manifest, tenant snapshot, backup events) are published in
[`packages/contracts`](../../packages/contracts/) and its foundational
decisions as public-safe ADRs in [`docs/adr`](../adr/).

## 2. Non-negotiable decisions applied in Sprint 0

1. Control plane, not a telemetry backend (ADR-0001).
2. FastAPI modular monolith; no early microservices (ADR-0002, ADR-0012).
3. Browser talks only to the Drake API; enforced by a static guard test
   (ADR-0003).
4. Query mediation via templates/budgets — no free-form PromQL (ADR-0004).
5. Read-only, secretless, outbound-only cluster agent (ADR-0005).
6. Static-only repository analysis for onboarding (ADR-0006).
7. Versioned `.drake/project.yaml` manifest contract (ADR-0007).
8. Tenant measurement honesty: method + confidence + as-of (ADR-0008).
9. Backup evidence is never presented as recoverability (ADR-0009).
10. Deny-by-default scoped RBAC; server-side enforcement (ADR-0010).
11. Unknown/stale/partial semantics are first-class UI states (ADR-0011).

## 3. Toolchain baseline

| Tool | Pinned/used in Sprint 0 |
|---|---|
| Node.js | 24 (LTS) via `.nvmrc` + `engines`; CI uses the same file |
| pnpm | 10.13.1 (`packageManager` field) |
| Python | 3.13.x via `.python-version`; managed by uv |
| uv | workspace with `apps/api`, `apps/worker` members |
| Go | 1.26.x (`apps/cluster-agent/go.mod`) |
| PostgreSQL / Redis | 16 / 7 via the local Compose stack |
| Docker | local-only Compose stack, loopback-bound ports |

Development host note: local verification ran on macOS/arm64 with Node 23
available system-wide while the project pins Node 24 for CI; this mismatch is
recorded as a known limitation in the test report.

## 4. What Sprint 0 delivered

- Monorepo layout: `apps/{web,api,worker,cluster-agent}`,
  `packages/contracts`, `deploy/local`, `docs/{adr,sprints}`, `tests/`.
- Contracts package: three JSON Schemas, fictional positive fixtures,
  per-rule negative fixtures, two-layer validator (schema + content policy),
  `drake-validate` CLI, metric-label policy guard.
- API foundation: health live/ready with per-dependency truth, correlation
  ID middleware, typed error envelope, JSON logging with redaction,
  deny-by-default CORS, Alembic baseline, database-enforced append-only
  audit table with validated writer.
- Worker foundation: strict job envelope (idempotency, retry, dead-letter,
  bounded credential-free payload), Redis queue with typed unavailability,
  decision-owning runner.
- Web foundation: premium shell (dark-first, responsive), honest data-state
  primitives, provenance footer contract, provider-access guard.
- Agent foundation: validated config, redacting logger, loopback liveness,
  outbound transport/enrollment stubs, collector registry that rejects
  forbidden resource kinds. No Kubernetes client in Sprint 0.
- Local environment: Compose stack (localhost-only), data-safe `make down`,
  explicitly destructive `make destroy-local-data` with non-local refusal.
- CI: SHA-pinned actions, digest-pinned scanners, no secrets, read-only
  permissions, unit + integration + security gates.

## 5. Explicitly out of Sprint 0 (not started, by design)

Kubernetes discovery/connections, Prometheus/Thanos/Alertmanager
deployment or querying, GitHub App integration, OIDC sign-in, tenant
adapters, backup connectors, real dashboards/KPIs, any deployment.

## 6. Operator inputs still required (Sprint 1+ gates)

- Corporate OIDC (Entra ID) tenant + app registration for the identity sprint.
- GitHub App creation/installation for the onboarding sprint.
- Cluster/namespace matrix, ingress/TLS, object storage, notification
  channels, RPO/RTO policy for their respective sprints.
- Target-runtime decisions recorded in the internal dossier remain open
  decision gates and are not repeated here.
