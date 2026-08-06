# Sprint 2 — Test Report

Every `PASS` reflects a command actually executed on the Sprint 2 branch
during final verification. Status vocabulary: `PASS` / `FAIL` / `NOT RUN` /
`BLOCKED` / `MANUAL` / `PARTIAL`.

## 1. Python (api + worker)

| Check | Command | Result | Status |
|---|---|---|---|
| Format | `uv run ruff format --check .` | clean | PASS |
| Lint | `uv run ruff check .` | clean | PASS |
| Typecheck (strict) | `uv run mypy apps/api/src apps/worker/src` | 0 issues / 42 source files | PASS |
| Unit tests | `uv run pytest -m "not integration" -q` | 74 passed | PASS |

## 2. Integration (live disposable local stack)

`make integration-test` — **63 passed** (43 Sprint 0/1 regression +
10 catalog persistence + 10 catalog API).

| Area | Evidence | Status |
|---|---|---|
| Migrations 0001→0004 | `upgrade head` clean; `0004 → 0003 → head` cycle on disposable DB with **Sprint 1 identities/audit rows preserved** | PASS |
| Atomic entity+scope creation | project/environment/service-binding/cluster each verified against the scopes table with ADR-0014 parentage | PASS |
| Rollback-together | forced failure inside the transaction leaves neither catalog row nor scope node | PASS |
| Multi-cluster project topology | one project's environments on two clusters via FK reference | PASS |
| Runtime constraints | kubernetes requires cluster+namespace (DB-enforced); external requires no cluster | PASS |
| Duplicate rejection | project key, environment key, active cluster/namespace pair | PASS |
| Soft archive | history preserved; active namespace slot freed | PASS |
| Bounded metadata | URL-in-health, credential-shaped selector, oversized selector all rejected | PASS |
| Fixture bootstrap guard | refuses to run outside local/test (fail-closed) | PASS |
| Project list isolation | unauthorized projects absent; breadcrumb visibility for narrow env grants; authorized-only counts | PASS |
| Sibling IDOR | environment/service/cluster details outside scope → consistent 404, no namespace leak in bodies | PASS |
| Cluster/project separation | project grant sees no clusters (list empty, detail 404); cluster grant sees no projects | PASS |
| Integration scope isolation | other scopes' integrations silently absent; `config_ref` never serialized | PASS |
| Search side-channels | authorization filtering at SQL level before match/limit; unauthorized names return nothing; wildcards are literals; length bounds → 422 | PASS |
| Pagination | deterministic order, cursor stability, unauthorized rows cannot extend pages; invalid cursor → 422 | PASS |
| Honest states | configured integration without observation stays `unknown`; nothing fabricates `healthy` | PASS |
| Sprint 0/1 regressions | auth flow/replay/fixation, RBAC delegation, transactional idempotency (incl. concurrency), audit append-only, queue | PASS |

## 3. Contracts / Go / Worker (unchanged, re-verified)

| Check | Result | Status |
|---|---|---|
| Contracts lint/typecheck/tests | 43 passed | PASS |
| Go agent fmt/vet/build/test | clean; 5 packages ok | PASS |

## 4. Web

| Check | Command | Result | Status |
|---|---|---|---|
| Unit/component tests | `pnpm --filter @drake/web test` | **39 passed** (9 files) — catalog screens (list success/empty/error+correlation-ID, clusters empty, integrations safe fields, operational grid honest states, search dialog open/query/keyboard/empty/error), shell/session/grant suites | PASS |
| Lint / typecheck | eslint, `tsc --noEmit` | clean | PASS |
| Production build | `pnpm --filter @drake/web build` | compiled successfully | PASS |
| Provider-access guard | part of the suite | 0 violations | PASS |

## 5. Browser E2E (real stack, no route mocking)

`make e2e-test` — **16 passed** (two consecutive runs). Fixtures reach the
browser only through PostgreSQL (deterministic local/test reset + bootstrap).

| Scenario | Status |
|---|---|
| S1 regression: login, unauthorized lockout, role editing, grant lifecycle, logout, session expiry, mobile, theme, keyboard, axe | PASS |
| Owner full catalog walk: Projects → Overview → Environment → Service with real PostgreSQL data and `not_configured` operational cards | PASS |
| Narrow env user: own environment/services only; authorized-only counts; forged sibling URL → not-found | PASS |
| Project user blocked from cluster detail; cluster viewer reads it (agent/inventory `not_configured`, no fabricated environments) | PASS |
| Search: authorized-only results; sibling names yield nothing for narrow users, results for the owner | PASS |
| Integration Health: safe fields for owner; honest empty state for narrow env user | PASS |
| Catalog accessibility smoke (axe): no critical violations on list + overview | PASS |

## 6. Not run / deferred (honest list)

| Item | Status | Reason |
|---|---|---|
| Real Entra ID smoke | BLOCKED | unchanged; no tenant/app registration |
| Formal WCAG 2.2 AA audit | MANUAL | axe smoke passed; viewport/theme/state matrix exercised via responsive tests + E2E |
| Real provider integrations (Prometheus/K8s/GitHub/backup) | NOT RUN | later sprints; states honestly `not_configured` |
| Session inactivity timeout / auth rate limiting | NOT RUN | known hardening items |
| Load/scale | NOT RUN | Sprint 12 |
