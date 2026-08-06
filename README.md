# Drake

Drake is an internal observability and operations control plane. It unifies
Kubernetes workload health, application golden signals, PostgreSQL health and
capacity, tenant plan/entitlement/usage snapshots, backup & restore evidence,
deployment correlation, and alert/incident projection into a single project
catalog with a purpose-built UI.

Drake is **not** a Grafana clone and **not** a telemetry backend. It builds on
mature data planes (Prometheus, Alertmanager, OpenTelemetry, and optionally
Thanos/Loki/Tempo) and owns the business context layer on top of them.

## Architecture at a glance

| Component | Technology | Role |
|---|---|---|
| `apps/web` | Next.js (App Router), TypeScript, Tailwind | UI. Talks only to the Drake API |
| `apps/api` | Python, FastAPI (modular monolith) | Control plane API, RBAC, audit, query broker |
| `apps/worker` | Python, Redis | Idempotent background jobs (sync, snapshots, reconciliation) |
| `apps/cluster-agent` | Go | Read-only Kubernetes inventory agent (outbound-only) |
| `packages/contracts` | TypeScript, JSON Schema | Versioned machine-validated contracts (`.drake/project.yaml`, snapshots, events) |

Non-negotiable boundaries:

- The browser never connects to Prometheus, Thanos, Alertmanager, or the
  Kubernetes API. The web app has exactly one data source: the Drake API.
- The cluster agent is read-only (`get`/`list`/`watch` only), has no access to
  Secrets, and never receives `exec`/`attach`/`portforward` permissions.
- Tenant, plan, and entitlement facts come from authoritative application
  snapshots — never guessed, never silently converted to zero/healthy.
- Backup artifact existence is never presented as restore success.

See [docs/adr/](docs/adr/) for the architecture decision records.

## Repository layout

```text
apps/
  web/            Next.js UI
  api/            FastAPI control plane
  worker/         Background job runner
  cluster-agent/  Go read-only Kubernetes agent
packages/
  contracts/      JSON Schemas, fixtures, validator CLI
deploy/
  local/          Local-only Docker Compose stack
docs/
  adr/            Architecture decision records
  sprints/        Sprint reports
tests/            Cross-cutting test assets
```

## Getting started

Prerequisites: Node.js (see `.nvmrc`), pnpm, Python 3.13 + [uv](https://docs.astral.sh/uv/),
Go (see `apps/cluster-agent/go.mod`), Docker.

```bash
pnpm install          # JS/TS workspaces
uv sync               # Python workspaces (api, worker)
make up               # local PostgreSQL 16 + Redis 7 (localhost only)
make test             # run all test suites
make down             # stop local stack (keeps data)
```

See the `Makefile` for the full task list. All development happens against the
local stack; nothing in this repository connects to shared or production
infrastructure.

## Status

Sprints 0–3 delivered: foundation (contracts, local environment, CI/security
gates), identity (OIDC + server-side sessions, dynamic scoped RBAC,
transactional idempotency, append-only audit), the catalog control plane
(project/environment/service/cluster catalog with scoped read APIs, global
authorized search, and the premium web experience), and the metrics
foundation: versioned metric/query/dashboard registries, a server-side
Query Broker (authorization before cache/provider, fail-closed budgets,
Redis-backed cache with honest stale semantics, SSRF-bounded Prometheus
adapter), a digest-pinned local Prometheus fixture stack, and real golden
signals on the project and service screens. Drake stores no raw samples,
the browser never talks to a provider, and users cannot submit PromQL —
queries exist only as repository-controlled templates. The
kube-prometheus-stack dev package is rendered and policy-checked in CI
only; no cluster deployment has happened. Kubernetes inventory, GitHub,
tenants, and backups remain honestly not_configured.
