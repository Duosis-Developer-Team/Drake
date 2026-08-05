# Sprint 1 — Test Report

Every `PASS` reflects a command actually executed on the Sprint 1 branch
during final verification. Status vocabulary: `PASS` / `FAIL` / `NOT RUN` /
`BLOCKED` / `MANUAL` / `PARTIAL`.

## 1. Python (api + worker)

| Check | Command | Result | Status |
|---|---|---|---|
| Format | `uv run ruff format --check .` | clean | PASS |
| Lint | `uv run ruff check .` | clean | PASS |
| Typecheck (strict) | `uv run mypy apps/api/src apps/worker/src` | 0 issues / 33 files | PASS |
| Unit tests | `uv run pytest -m "not integration" -q` | 74 passed | PASS |

Unit coverage highlights: OIDC validation matrix (wrong issuer/audience,
expired, not-yet-valid, invalid signature, invalid nonce, JWKS rotation,
group claims, overage fail-closed, no token material in errors), session
store (hashed keys, single-use login state, fixation, fail-closed backend),
cookie policy (HttpOnly/Lax everywhere; Secure outside local/test), redirect
allowlist (open-redirect attempts collapse), plaintext-issuer production
guard, audit writer validation, job envelope/queue/runner (Sprint 0 suite).

## 2. Integration (live disposable local stack)

`make integration-test` — **28 passed**.

| Area | Evidence | Status |
|---|---|---|
| Full OIDC login flow via API endpoints | login → authorize → callback → `/v1/me` | PASS |
| Cookie policy on real callback | HttpOnly + SameSite=Lax asserted on Set-Cookie | PASS |
| Authorization code / state replay | replayed state 403; stolen code with fresh state rejected | PASS |
| Session fixation | attacker-planted cookie never becomes a session; server mints new ID | PASS |
| Logout | server-side invalidation; missing CSRF → 403 | PASS |
| Session expiry | TTL elapsed → 401 | PASS |
| Redis down | login and `/v1/me` → typed 503, never anonymous | PASS |
| Provider down | typed 503, no internals leaked | PASS |
| Login success/failure audited | audit rows verified in DB | PASS |
| Deny-by-default | fresh identity: zero permissions, RBAC surfaces 403 | PASS |
| Role lifecycle | create/update/permissions/archive with Idempotency-Key replay, missing headers → 428, stale If-Match → 412 | PASS |
| System role immutability | 409 `system_role_immutable` | PASS |
| Grant inheritance direction | parent→child applies; child→parent and sibling never | PASS |
| Tenant permission separation | `project.view` never implies `tenant.view` | PASS |
| Validity windows & revocation | future/expired/revoked grants contribute nothing | PASS |
| Group mappings | mapped group grants flow; unmapped grants nothing; overage → zero group authority | PASS |
| Self-escalation & delegation | self-grant 403; org/sibling scope 404; superset delegation 403; subset allowed; attempts audited | PASS |
| Role-edit escalation guard | editor cannot add permissions they lack | PASS |
| Last Platform Owner protection | revoking the last org-root `rbac.manage` identity grant → 409 | PASS |
| Transactional audit (fail-closed) | forced audit failure rolls the role mutation back | PASS |
| IDOR: Project A → Project B | list absence, guessed-UUID consistent 404s, cross-scope create 404, no leakage in bodies | PASS |
| Audit query | scope filtering, unscoped platform events only at root, cursor pagination without overlap, invalid cursor 422, cross-scope filter 404 | PASS |
| Migrations | `upgrade head → downgrade base → upgrade head` (0001+0002) on disposable DB | PASS |
| Audit append-only | UPDATE/DELETE/TRUNCATE rejected by trigger | PASS |
| Worker queue on real Redis | roundtrip, idempotency, dead-letter | PASS |

## 3. Web

| Check | Command | Result | Status |
|---|---|---|---|
| Unit/component tests | `pnpm --filter @drake/web test` | 24 passed (6 files) | PASS |
| Lint / typecheck | eslint, `tsc --noEmit` | clean | PASS |
| Production build | `pnpm --filter @drake/web build` | compiled successfully | PASS |
| Provider-access guard | part of the test suite | 0 violations | PASS |

## 4. Browser E2E (real stack: fake OIDC + API + production web build)

`npx playwright test` — **7 passed** (two consecutive full runs).

| Scenario | Status |
|---|---|
| Signed-out → fake OIDC login → signed-in shell with identity | PASS |
| Permission-aware navigation + role create/permission edit + live audit rows | PASS |
| Logout invalidates; protected route stays locked | PASS |
| Mobile (390px) drawer navigation | PASS |
| Dark/light theme toggle | PASS |
| Keyboard reachability of sign-in | PASS |
| Accessibility smoke (axe): no critical violations, signed-out + shell | PASS |

## 5. Unchanged Sprint 0 gates (re-verified)

| Check | Result | Status |
|---|---|---|
| Contracts (schema/policy/CLI) | 46 passed | PASS |
| Go agent fmt/vet/build/test | clean, 5 packages ok | PASS |
| Secret scan (history + tree + canary) | no leaks; canary detected | PASS |
| Dependency scan (osv-scanner) | no issues | PASS |
| `git diff --check` | clean | PASS |

## 6. Not run / blocked (honest list)

| Item | Status | Reason |
|---|---|---|
| Real Entra ID smoke test | BLOCKED | no tenant/app registration provided; harness-only by design |
| E2E in CI | NOT RUN | E2E runs locally via `make e2e-test`; CI wiring is a Sprint 2 item |
| Full WCAG 2.2 AA audit | MANUAL | axe smoke passed; formal audit deferred |
| Load/scale tests | NOT RUN | Sprint 12 scope |
