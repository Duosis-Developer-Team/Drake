# Sprint 2 — Test Report

Every `PASS` reflects a command actually executed on the Sprint 2 branch
during final verification. Status vocabulary: `PASS` / `FAIL` / `NOT RUN` /
`BLOCKED` / `MANUAL` / `PARTIAL`.

> **Correction (CTO review closure).** An earlier revision of this report
> implied cursor pagination covered all collection endpoints. That was an
> overclaim: at initial Sprint 2 submission only the **project** and
> **cluster** collections had keyset cursors; the **environment**,
> **service**, and **integration** collections were unbounded and were
> completed after CTO review (see §7). The evidence below reflects the
> post-closure state.

## 1. Python (api + worker)

| Check | Command | Result | Status |
|---|---|---|---|
| Format | `uv run ruff format --check .` | clean | PASS |
| Lint | `uv run ruff check .` | clean | PASS |
| Typecheck (strict) | `uv run mypy apps/api/src apps/worker/src` | 0 issues / 42 source files | PASS |
| Unit tests | `uv run pytest -m "not integration" -q` | 74 passed | PASS |

## 2. Integration (live disposable local stack)

`uv run pytest -m integration` — **78 passed** (43 Sprint 0/1 regression +
18 catalog persistence + 17 catalog API).

| Area | Evidence | Status |
|---|---|---|
| Migrations 0001→0005 | `upgrade head` clean; `0004 → 0003 → head` and two full `0005 → 0004 → head` cycles on disposable DB with **Sprint 1 identities/audit rows and valid Sprint 2 bindings preserved** (binding `project_id` re-backfilled) | PASS |
| 0005 fails closed on invalid data | a planted cross-project binding makes the upgrade RAISE — never silently fixed or deleted; row intact afterwards | PASS |
| Cross-project binding integrity | rejected by `CatalogService` (validation) AND by composite RESTRICT FKs when the service layer is bypassed; scope + binding roll back together | PASS |
| Scope parent fail-closed | `ScopeResolver.ensure` raises on an existing scope under a different parent — no silent re-parenting | PASS |
| Bounded error codes / key shapes | uppercase, newline, URL, and >64-char `last_error_code` rejected at the DB CHECK and by the app validator; malformed keys rejected at the DB boundary | PASS |
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
| Integration scope isolation | other scopes' integrations silently absent; `config_ref` never serialized; authorization is a **SQL boundary** — project grants never see cluster-scope integrations and vice versa, under filters and full page walks | PASS |
| Search side-channels | authorization filtering at SQL level before match/limit; unauthorized names return nothing; wildcards are literals; length bounds → 422 | PASS |
| Pagination (all collections) | keyset cursors on projects, environments, services, clusters, and integrations (no offset anywhere); deterministic order with unique tie-breakers; unauthorized and archived rows cannot extend pages; archived hidden by default; bounded filters; invalid cursor/filter → 422 | PASS |
| Search determinism | identical keys across projects yield one stable order (kind, project_key, key, id) across repeated queries | PASS |
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
| Unit/component tests | `pnpm --filter @drake/web test` | **41 passed** (9 files) — catalog screens (list success/empty/error+correlation-ID, clusters empty, integrations safe fields, operational grid honest states, search dialog open/query/keyboard/empty/error), shell/session/grant suites, brand asset budget regression | PASS |
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

## 7. CTO review closure (post-acceptance-review changes)

Findings from the CTO acceptance review of the initial Sprint 2 submission,
closed on the same branch with follow-up commits (no history rewrite):

| Finding | Closure | Status |
|---|---|---|
| Environment/service/integration collections unbounded; report overclaimed pagination coverage | Keyset cursors, bounded limits, search, lifecycle (default `active`) and criticality/state filters on every collection; report corrected (see the notice at the top) | PASS |
| Integration authorization filtered in Python after fetch-all | Moved into the SQL boundary: per-permission visible-scope sets are WHERE predicates derived from the single scope-type→permission mapping | PASS |
| Cross-project service binding representable in the schema | Migration `0005_catalog_integrity_hardening`: authoritative `project_id` on bindings (fail-closed backfill) + composite RESTRICT FKs to both parents; service-layer validation on top | PASS |
| Caller-supplied scope identity (keys passed alongside ids) | `create_environment`/`bind_service` now derive keys and scope parents from authoritative rows; `ScopeResolver.ensure` fails closed on parent mismatch | PASS |
| `last_error_code` unbounded | DB CHECK + app validator: bounded machine-readable code, no free text/URLs/newlines; key-shape CHECKs added for external stable refs | PASS |
| Search order non-deterministic on ties | Unique tie-breaker (kind, project_key, key, id) | PASS |
| Brand icons ~1.77MB on first sidebar load | 64px (2×) derivatives from the unchanged official masters: **6.3KB** total runtime transfer; masters moved out of the public path; budget regression test in CI | PASS |

Record kept for honesty: the initial Sprint 2 push failed the `python` CI
check once (ruff RUF059, an unused variable) because local verification had
read scanner output instead of exit codes; fixed in `9e631cc`. Final
verification is exit-code based. That CI failure history is intentionally
preserved, not rewritten.
