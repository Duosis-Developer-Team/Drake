# Sprint 3 — Test Report

Every `PASS` reflects a command actually executed on the Sprint 3 branch
during final verification, judged by exit code. Status vocabulary:
`PASS` / `FAIL` / `NOT RUN` / `BLOCKED` / `MANUAL` / `PARTIAL`.

## 1. Python (api + worker)

| Check | Command | Result | Status |
|---|---|---|---|
| Format | `uv run ruff format --check .` | clean | PASS |
| Lint | `uv run ruff check .` | clean | PASS |
| Typecheck (strict) | `uv run mypy apps/api/src apps/worker/src` | 0 issues / 53 source files | PASS |
| Unit tests | `uv run pytest -m "not integration" -q` | **103 passed** (74 S0–S2 + 29 telemetry) | PASS |

Telemetry unit coverage: registry fail-closed paths (duplicates, unknown
refs, route label, unsorted, snapshot-backed template, global-ceiling
violation, malformed JSON), compiler escaping/injection/determinism,
range/step budget math, normalization honesty (NaN/±Inf → null+partial,
unexpected label fail-closed, series/point budgets, deterministic order),
adapter contract failures, SSRF refusals, broker-metrics hygiene.

## 2. Integration (live disposable local stack)

`uv run pytest -m integration` — **90 passed** (78 S0–S2 regression +
12 telemetry). Real PostgreSQL + Redis; deterministic fake Prometheus via
injected transport; real local Prometheus for the smoke.

| Area | Evidence | Status |
|---|---|---|
| Migration head | `0005` (no new migration needed — the integrations projection already carries every observation column); `base→0005→base→0005` on a disposable DB | PASS |
| Authorization order | unauthorized queries: provider call count **0** and cache reads **0** (spied); missing `telemetry.query`, cross-project, sibling env/service, cluster↔project probes, ghost ids → consistent 404 | PASS |
| Input abuse | `query`/`promql`/`metric_name`/`provider_url`/`config_ref` fields 422 (`extra=forbid`); parameter smuggling 422; huge range 422; tiny step adjusted + disclosed (`step_adjusted`); unknown template 404; incompatible scope 422 | PASS |
| Provider flows | success envelope; fresh-hit absorbs repeats; scope-isolated cache (A never serves B); outage → last-good as explicit `stale` → typed retryable 503 without last-good; malformed/oversized/unexpected-label → 502 fail-closed; timeout → 503; raw upstream errors redacted | PASS |
| Budgets | concurrency 429 + release recovery; atomic Lua lease rejects limit+1; **Redis down → 503, provider untouched (never bypassed)** | PASS |
| Cache hygiene | Redis entries contain no PromQL, connector URL, config_ref, or connector name; cache identity = integration configuration (id+hashed ref), not observation version bumps | PASS |
| Observation projection | success→`ok`; failure→`degraded`; overdue success→`stale`; bounded error codes only; unsafe codes rejected by the app validator; cache hits record nothing | PASS |
| Real Prometheus smoke | all **10 registry templates** compile to PromQL the real parser accepts (`query_range` status=success each); broker end-to-end returns live scrape data (`up`=1) through a real connector | PASS |

## 3. Contracts

`pnpm --filter @drake/contracts lint/typecheck/test` — **59 passed**.

| Area | Evidence | Status |
|---|---|---|
| New schemas | metric-catalog / query-template / dashboard-template: `additionalProperties:false`, bounded sizes, shaped keys, enum-locked units/types, immutable versions | PASS |
| Registry integrity | duplicates, unknown metric/query refs, forbidden labels, placeholder allowlist, snapshot-behind-template, unsorted registries all rejected; deterministic content hash | PASS |
| Route label correction | `route` (and `path`/`raw_path`/`url`/…) rejected; `route_template` is the only canonical route label; denylist extended (`git_sha`, `commit_sha`) | PASS |
| CLI | `drake-validate` accepts the three new schema names; all three authoritative registry files validate OK | PASS |

## 4. Infrastructure

| Check | Evidence | Status |
|---|---|---|
| Local Prometheus | v3.5.0 by digest on 127.0.0.1:59090; `promtool check config` valid; fixture exporter targets scraped (`up`=1 for all fixture services) | PASS |
| Real `query_range` | live rate data returned from the fixture world (integration smoke above) | PASS |
| Helm dev package | kube-prometheus-stack **88.1.5** pinned (Chart.lock committed); `helm lint` + `helm template` (83 documents) + policy checks: no LoadBalancer/NodePort/Ingress, no credential-shaped secret content, no wildcard-everything RBAC, Grafana off | PASS |
| Real dev cluster deployment | — | **NOT RUN — operator approval required** |

## 5. Web

| Check | Command | Result | Status |
|---|---|---|---|
| Unit/component | `pnpm --filter @drake/web test` | **41 passed** (10 files) | PASS |
| Lint / typecheck | eslint, `tsc --noEmit` | clean | PASS |
| Production build | `pnpm --filter @drake/web build` | compiled | PASS |
| Provider guard | part of the suite | extended: `/api/v1/query*`, provider port, config refs, PromQL mentions now fail the build if present in browser code — 0 violations | PASS |
| Bundle impact | charts are inline SVG | **zero new dependencies, zero bundle delta** (lockfile unchanged) | PASS |

## 6. Browser E2E (real stack, no route mocking)

`make e2e-test` — **24 passed** in **two consecutive runs** (16 existing
S1/S2 scenarios kept + 8 metrics scenarios). Fake OIDC (test-only),
FastAPI, PostgreSQL, Redis, local fixture Prometheus behind the E2E flaky
proxy, production Next.js build, Chromium.

| Scenario | Status |
|---|---|
| S1/S2 regression: login, lockout, roles, grant lifecycle, logout, expiry, mobile, theme, keyboard, axe, catalog walk, IDOR, cluster separation, search, integrations, catalog a11y | PASS |
| Project overview metrics: live fixture data, URL `?range=` surviving reload | PASS |
| Environment selector with URL state; alpha/test honestly **empty** (no telemetry targets) | PASS |
| Service golden signals: SVG chart (role=img), data-table fallback, fastapi profile hides kubernetes-only widgets | PASS |
| Provider outage: last-good served as explicit **stale**, then honest **unavailable + correlation id** on a range with no last-good | PASS |
| Beta project: telemetry honestly **not configured** (no fabricated zero) | PASS |
| Narrow environment grant: selector offers only the authorized environment | PASS |
| Keyboard operation of the range control + axe (0 critical) | PASS |
| 390px mobile: dashboards render, no horizontal overflow | PASS |

Viewport/theme/state matrix: 390/768/1280/1536 px via responsive classes +
the 390px E2E check; light/dark via the themed component library; loading/
success/empty/zero/stale/partial/not-configured/unavailable states each
have dedicated widget renderings exercised by unit + E2E tests.

## 7. Security scans

| Check | Result | Status |
|---|---|---|
| gitleaks history + tree + canary | no leaks; canary still detected (fixture dirs scanned). Two narrow line-shape allowlists added for registry key fields — no path exclusions; documented in `.gitleaks.toml` | PASS |
| OSV (digest-pinned, CI-identical) | 476 packages, no issues | PASS |
| `git diff --check` | clean | PASS |

## 8. Not run / deferred (honest list)

| Item | Status | Reason |
|---|---|---|
| Real dev kube-prometheus-stack deployment | NOT RUN | operator approval required; package is rendered+policy-checked only |
| Real Hermes cluster telemetry | NOT RUN | Sprint 5 scope |
| Cluster Agent enrollment / K8s inventory | NOT RUN | Sprint 4 scope |
| Real Entra ID smoke | BLOCKED | unchanged; no tenant/app registration |
| Formal WCAG 2.2 AA audit | MANUAL | axe smoke passed (0 critical) |
| Thanos / HA / object storage | NOT RUN | later sprints |
| Load/scale | NOT RUN | Sprint 12 |
| Session inactivity timeout / auth rate limiting | NOT RUN | known hardening items |
