# Sprint 5A — Test Report

Every `PASS` reflects a command actually executed on the Sprint 5A branch
during final verification, judged by exit code. Status vocabulary:
`PASS` / `FAIL` / `NOT RUN` / `BLOCKED` / `MANUAL` / `PARTIAL`.

## 1. Verification sweep

| Check | Command | Result | Status |
|---|---|---|---|
| Python format | `uv run ruff format --check .` | 177 files already formatted | PASS |
| Python lint | `uv run ruff check .` | All checks passed | PASS |
| Python types | `uv run mypy apps/api/src` | no issues in 69 source files | PASS |
| Python unit | `uv run pytest -m "not integration" -q` | 303 passed | PASS |
| Python integration | `uv run pytest -m integration -q` | 152 passed | PASS |
| Contracts | `pnpm --filter @drake/contracts lint/typecheck/test` | 63 tests passed | PASS |
| Web lint | `pnpm lint` | clean | PASS |
| Web types | `pnpm typecheck` | clean | PASS |
| Web unit | `pnpm test` | 67 passed across 12 files | PASS |
| Web build | `pnpm build` | compiled | PASS |
| Provider guard | `vitest run src/test/provider-guard.test.ts` | 2 passed | PASS |
| Go format/vet/build | `gofmt -l .`, `go vet ./...` | clean | PASS |
| Go tests (race) | `go test -race -count=1 ./...` | 8 packages ok | PASS |
| Helm (observability) | `deploy/dev/observability/validate.sh` | 83 documents checked | PASS |
| Helm (agent chart) | `deploy/agent/validate.sh` | 5 documents, fail-closed renders | PASS |
| Secret scan | `bash scripts/secret-scan.sh all` | history + tree clean, all three canaries detected | PASS |
| Whitespace (fix gate) | `git diff --check origin/main...HEAD` | clean | PASS |
| Dependency scan | `osv-scanner` | not installed locally; runs in CI | NOT RUN |
| Migration round trip | `alembic downgrade base` → `upgrade head`, twice | ends at `0008 (head)` | PASS |
| Audit append-only | `pytest -m integration -k "audit or append"` | 9 passed | PASS |
| Whitespace | `git diff --check origin/main...HEAD` | clean | PASS |

## 2. GitHub-specific tests

**Authentication (22 unit).** RS256 is mandatory and `none`/HS256 are
refused; `iat` is backdated 60s for clock drift; `exp` never exceeds
GitHub's 10-minute ceiling regardless of configured TTL; the clock is
injected, so expiry is tested rather than slept through. Token caching is
per-installation and lazy, refreshes inside the buffer, and is not shared
across installations. No test asserts a fixed token length; `ghs_` format
is recognised without being required.

**Webhook (38 unit).** The first test is GitHub's own published vector
(secret `It's a Secret to Everybody`, payload `Hello, World!`), so a
regression in the HMAC path fails against the vendor's evidence rather
than our own. Also covered: UTF-8 bodies hashed over the exact bytes sent;
any body mutation breaking the signature; wrong secret refused; missing,
truncated, `sha1=`, unprefixed and non-hex signatures all refused; a
missing secret failing closed; uppercase hex still verifying; the
comparison provably routed through `hmac.compare_digest`; delivery-id
format validation; the event allowlist; and envelope construction proven
bounded against a hostile payload (5 000-character fields, 500
repositories, malformed entries) while dropping every field not explicitly
chosen.

**Policy (23 unit).** Fifteen rules with stable ids, severities, evidence
and secret-free remediation. Classic branch protection and rulesets are
normalised into one set of facts, so a repository governed either way is
judged the same. Unreadable inputs produce UNKNOWN with the reason
stated. Drake's own eight check names are never imposed on another
repository.

**Integration (10, real PostgreSQL).** Webhook verification and replay
semantics end to end; the onboarding lifecycle across rename, transfer and
removal; the Datalake gate refusing before any API call (asserted by the
fake recording zero calls); dry-run evaluation making only GETs plus the
documented token mint; provider failures degrading without ever passing;
RBAC read/manage separation with IDOR probes; status reporting missing
operator inputs without their values; concurrent duplicate deliveries
admitting exactly one winner; audit records written without secrets; and
the re-delivery regression described in §4.

**Web (8 of 67).** Readiness, installation and repository rendering; the
Datalake gate with reconciliation disabled; an honest `NOT_CONFIGURED`
state listing missing operator inputs; blocking violations and UNKNOWN
verdicts shown distinctly; the manage action hidden from a read-only user;
API failure surfaced as an error state with retry; a colour-honesty gate
asserting blocked/degraded/disabled/unknown never render healthy; and
staleness derivation.

## 3. Full-chain E2E

`apps/web/e2e/github.spec.ts` runs against the real stack — fake OIDC,
FastAPI, PostgreSQL, Redis, a production Next.js build, and a local fake
GitHub REST API (`scripts/e2e_fake_github.py`). Webhooks are delivered as
GitHub delivers them: raw bytes plus a real HMAC header, so the signature
path is exercised rather than stubbed.

Seven scenarios: a signed installation delivery onboarding the catalog;
the Datalake gate blocking both the button and the API while the fake
records no new calls; replay idempotency, a forged replay refused with
409, a bad signature refused with 401, a non-JSON body refused with 400,
an off-allowlist event refused and `ping` acknowledged; a dry-run
evaluation showing a blocking violation for an ungoverned repository while
every upstream call remains a GET; an unreadable upstream never
manufacturing a fresh pass; a read-only user seeing the surface but unable
to drive it; and a leak check asserting no response or rendered page
contains a PEM header, a `ghs_` token, a JWT shape, or the webhook secret.

**Two consecutive full-chain runs: 49/49 passed, both times** (2.7 and
2.6 min), with the disposable k3d agent stack up so nothing was skipped.
The GitHub scenario additionally proves the ruleset path: Fikir-Sepeti is
governed by a ruleset rather than classic protection, and the fake serves
ruleset summaries with no `rules` member, so its clean verdict can only
come from the effective-rules endpoint actually being called — which the
spec asserts.

## 4. CTO fix gate — regressions written first

Every blocker below got a test that **failed on `e12403a`** before any
implementation changed. The durability suite's baseline was 8 failed / 2
passed; the decisive failure was
`test_transient_failure_then_retry_processes_exactly_once` asserting
`0 == 2` — two 202 responses returned while zero repositories were ever
written.

| § | Regression | Failing evidence on `e12403a` | Now |
|---|---|---|---|
| 3 | Claim committed, work lost | two 202s, zero rows written | 10 durability tests pass |
| 3 | Poison delivery retried forever | attempts never incremented | dead-letters at 5, audited |
| 4 | Conflict rewrote the original row | original flipped to `rejected` | digest/status/timestamp preserved |
| 5 | `request.body()` is not a limit | full body buffered before measuring | streamed, refused at limit+1 |
| 6 | Envelope could exceed the column | 100 max-length repos over 8 KiB | fitted to budget, truncation declared |
| 7 | Missing account skipped the check | payload without account accepted | refused, nothing written |
| 8 | Whole delivery table returned | other scope's rows visible | filtered on `scope_id` |
| 9 | Only path strings checked | broken key accepted at startup | PEM parsed, RSA enforced |
| 10 | Ruleset summaries read as rules | fabricated `rules` member in fixtures | effective-rules endpoint |
| 11 | Partial evidence passed | approved + unreadable → PASS | UNKNOWN, FAIL still outranks |
| 12 | Cache keyed on installation id | repo A token served repo B | keyed on full scope |
| 13 | Response buffered then measured | whole body read | streamed; page cap is an error |

Two defects were found in the *fix* itself, by these same tests: the
attempt counter wrote an error code that violated its own CHECK constraint
and rolled back the transaction that bounded retries, and the
state-conflict signal was briefly lost when domain work moved into the
service layer. Both are covered now.

New suites: `test_github_durability_integration.py` (10),
`test_github_boundary_integration.py` (20),
`test_github_contract_unit.py` (45).

## 5. Defects found by these tests

**Re-delivery regressed an onboarded repository (found by E2E, fixed).**
The webhook announcement path derived every repository as "not yet
reconciled", so a re-delivery pushed an already-READY repository back to
DISCOVERED. That transition is illegal, so a validly signed GitHub
re-delivery became a 500 — and GitHub would have retried it forever
against an invariant that was never going to change. State is now derived
from the row's own facts, and an unmodelled transition is recorded as an
audited conflict instead of an unhandled exception. An open security gate
still wins outright. Regression test:
`test_redelivery_after_reconciliation_does_not_regress_a_ready_repository`.

**Unreadable protection reported as FAIL (found by unit tests, fixed).**
When branch protection was unreadable but rulesets returned empty, the
engine concluded "unprotected" and returned FAIL. It now returns UNKNOWN
whenever either source is unreadable and nothing was found.

**Consecutive E2E runs were not reproducible (found by the second run,
fixed).** The catalog reset truncates `cluster_agents`, so the server
forgets every enrolled agent, but the agent's local identity survived in
the disposable stack directory; the agent then presented a certificate the
server had never seen and correctly refused to re-enroll, so the second
run failed at enrollment. The two halves of the reset now happen together.
Separately, the inventory a11y gates asserted on data produced by the spec
that enrolls the agent but had no matching prerequisite guard, so a
missing stack looked like a UI regression instead of a skip.

## 6. Not run in this sprint

| Item | Why |
|---|---|
| Real GitHub App registration/installation | Explicitly out of scope; no real App exists |
| Any call against real `api.github.com` | Every test and the E2E use an injected transport or the local fake |
| Datalake reconciliation | Blocked by the manual security gate, by design |
| `osv-scanner` | Not installed locally; runs as a CI job |
| Production/staging deployment, Kubernetes apply | Out of scope; deployments remain 0 |
