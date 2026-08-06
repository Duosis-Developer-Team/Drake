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
| Unit tests | `uv run pytest -m "not integration" -q` | **116 passed** (74 S0–S2 + 29 telemetry + 13 hardening) | PASS |

Telemetry unit coverage: registry fail-closed paths (duplicates, unknown
refs, route label, unsorted, snapshot-backed template, global-ceiling
violation, malformed JSON), compiler escaping/injection/determinism,
range/step budget math, normalization honesty (NaN/±Inf → null+partial,
unexpected label fail-closed, series/point budgets, deterministic order),
adapter contract failures, SSRF refusals, broker-metrics hygiene.

## 2. Integration (live disposable local stack)

`uv run pytest -m integration` — **96 passed** (78 S0–S2 regression +
18 telemetry). Real PostgreSQL + Redis; deterministic fake Prometheus via
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
| Unit/component | `pnpm --filter @drake/web test` | **48 passed** (11 files, incl. the dashboard scheduling suite) | PASS |
| Lint / typecheck | eslint, `tsc --noEmit` | clean | PASS |
| Production build | `pnpm --filter @drake/web build` | compiled | PASS |
| Provider guard | part of the suite | extended: `/api/v1/query*`, provider port, config refs, PromQL mentions now fail the build if present in browser code — 0 violations | PASS |
| Bundle impact | charts are inline SVG | **zero new dependencies, zero bundle delta** (lockfile unchanged) | PASS |

## 6. Browser E2E (real stack, no route mocking)

`make e2e-test` — **25 passed** in **three consecutive runs** (16
existing S1/S2 scenarios kept + 9 metrics scenarios). Fake OIDC
(test-only), FastAPI, PostgreSQL, Redis, local fixture Prometheus behind
the E2E flaky proxy, production Next.js build, Chromium.

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
| Real end-to-end cancellation: slow-provider churn stays within real Redis budgets (principal ≤4, target ≤8), final dashboard fully loads (no lingering 429), lease tokens drain to 0 far below TTL, raw-socket disconnects register closed connections at the provider boundary before any timeout | PASS |

Viewport/theme/state matrix: 390/768/1280/1536 px via responsive classes +
the 390px E2E check; light/dark via the themed component library; loading/
success/empty/zero/stale/partial/not-configured/unavailable states each
have dedicated widget renderings exercised by unit + E2E tests.

## 7. Security scans

| Check | Result | Status |
|---|---|---|
| gitleaks history + tree + canaries | no leaks; shape allowlists REMOVED — exact single-finding fingerprints only (`.gitleaksignore`); all THREE runtime canaries (AWS-format YAML + credential-shaped values in the exempted JSON field shapes) individually detected | PASS |
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

## 9. CTO acceptance hardening (post-review closure)

Findings from the CTO review of the initial Sprint 3 submission, closed
with follow-up commits on the same branch (no history rewrite):

| Finding | Closure | Status |
|---|---|---|
| A single dashboard could 429 itself (all queries fired at once; no real cancellation) | Bounded browser-side queue (≤3 concurrent per dashboard), AbortController through the API layer, generation guards; measured max client concurrency = 3; 5- and 6-query dashboards load with zero self-inflicted 429s; a real 429 renders as `throttled` | PASS |
| `/v1/internal/metrics` unauthenticated and on by default | Default OFF; local/test explicit opt-in registers the route; production-like enable refuses to boot; disabled = nonexistent route (no oracle); histogram `_sum`/`_count` completed and format parser-verified | PASS |
| 2 MiB cap applied after full buffering | True streaming budget: chunk-by-chunk accounting, early stop proven by a consumption counter (33/64 chunks), content-type fail-closed before any body read, mid-stream failures typed+redacted | PASS |
| Connector map granted implicit private-network access | Typed `{url, allow_private}` connectors; explicit opt-in for private targets; per-scheme DNS ports; all answers validated; mixed answers refused; hostname connectors FAIL-CLOSED outside local/test (rebinding unreachable; documented deployment blocker) | PASS |
| Template lookup ran before authorization (oracle) | ADR order restored: scope lookup + grants precede template resolution; unauthorized callers get one uniform 404 with 0 provider calls, 0 cache reads, 0 connector lookups, 0 integration lookups (spied) | PASS |
| Naive datetimes accepted despite the UTC contract | Naive → 422; aware offsets normalize to one UTC instant (single cache identity); responses always explicit UTC | PASS |
| Same-duration historical windows could share last-good | Relative near-now vs historical absolute last-good identities split; stale responses separate requested `range`, actual `data_range`, and true `as_of` — UI shows the distinction | PASS |
| Value-shape gitleaks allowlists could hide a real secret | Shape rules removed; exact single-finding fingerprints only; two new runtime canaries plant credential-shaped values in the exempted field shapes and must each be detected | PASS |

## 10. Final cancellation closure (post-hardening review)

| Finding | Closure | Status |
|---|---|---|
| Client aborts were browser-side only; the endpoint awaited the broker directly and could leave provider work + Redis leases running | `POST /v1/telemetry/query` runs the broker as a supervised task racing an event-driven `http.disconnect` watcher (no busy polling): disconnect cancels AND awaits the broker; provider stream/client close; both leases release immediately with own tokens; the watcher itself is cancelled+awaited on every path; mid-acquire cancellation releases partial tokens; cancellation is never recorded as a provider failure, never mints stale fallbacks, never maps to provider-unavailable | PASS |
| No end-to-end cancellation proof | Manual-ASGI integration test (real Redis): provider transport observes exactly one CancelledError, leases vanish immediately, observation untouched, zero orphan tasks. E2E over the full stack: real-HTTP disconnects against the browser's own uvicorn register closed connections at the provider proxy before any timeout; accounting closes; leases drain. Frontend scheduling test honestly re-scoped to client-scheduler evidence with a real 1h→24h→7d walk | PASS |
| Near-now accepted future-ending windows | Two-sided policy with one step of forward skew tolerance; future windows are historical (never share relative last-good) — integration-tested | PASS |
| **Platform finding (documented deployment requirement)** | Next's proxy hop does not reliably propagate client aborts upstream (drains pooled responses; Route Handler + `request.signal` behaves the same). Browser-driven server-side cancellation therefore requires the deployment ingress to route `/v1` DIRECTLY to the API; the local rewrite is dev/E2E-only. Recorded in `next.config.ts` and the security report | NOTED |

Observed bounds in the cancellation E2E: client concurrency ≤3 (component
suite: exactly 3 under a slow provider), principal leases ≤4, target
leases ≤8 on live Redis; lease drain to 0 in ≤5s (TTL backstop 30s).
