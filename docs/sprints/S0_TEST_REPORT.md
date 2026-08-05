# Sprint 0 — Test Report

Every `PASS` below reflects a command actually executed on the Sprint 0
branch during final verification. Nothing untested is marked `PASS`.

Status vocabulary: `PASS` / `FAIL` / `NOT RUN` / `BLOCKED` / `MANUAL` / `PARTIAL`.

## 1. JavaScript / TypeScript

| Check | Command | Result | Status |
|---|---|---|---|
| Dependency install | `pnpm install` | lockfile-consistent install | PASS |
| Contracts lint | `pnpm --filter @drake/contracts lint` | 0 errors | PASS |
| Contracts typecheck | `pnpm --filter @drake/contracts typecheck` | 0 errors | PASS |
| Contracts tests (schema + policy + CLI fixtures) | `pnpm --filter @drake/contracts test` | 46 passed / 46 (4 files) | PASS |
| Validator CLI exit codes | `node dist/cli.js` on valid / invalid / missing files | 0 / 1 / 2 as contracted | PASS |
| Web lint | `pnpm --filter @drake/web lint` | 0 errors | PASS |
| Web typecheck | `pnpm --filter @drake/web typecheck` | 0 errors | PASS |
| Web tests (shell, state primitives, provenance, theme, provider guard) | `pnpm --filter @drake/web test` | 16 passed / 16 | PASS |
| Web production build | `pnpm --filter @drake/web build` | compiled successfully, static routes generated | PASS |

## 2. Python

| Check | Command | Result | Status |
|---|---|---|---|
| Dependency sync | `uv sync --all-packages` | clean resolve | PASS |
| Format | `uv run ruff format --check .` | 53 files formatted | PASS |
| Lint | `uv run ruff check .` | all checks passed | PASS |
| Typecheck | `uv run mypy apps/api/src apps/worker/src` | 0 issues / 19 files (strict) | PASS |
| Unit tests (api + worker) | `uv run pytest -m "not integration" -q` | 51 passed, 3 deselected | PASS |

## 3. Integration (live disposable local stack)

Stack: Compose PostgreSQL 16 + Redis 7, loopback-only, health-checked.

| Check | Result | Status |
|---|---|---|
| `make up` health gate | both services healthy | PASS |
| API `/health/ready` against live stack | 200, `database: ok`, `redis: ok` | PASS |
| API `/health/ready` with unreachable deps (unit) | 503, per-component `unavailable` | PASS |
| Alembic `upgrade head → downgrade base → upgrade head` | full cycle clean on disposable DB | PASS |
| Audit insert via service layer | row persisted, id returned | PASS |
| Audit UPDATE / DELETE / TRUNCATE | all rejected by DB trigger ("append-only") | PASS |
| Worker queue on real Redis (roundtrip, idempotent duplicate suppression, dead-letter) | 3 integration tests passed | PASS |
| `make down` keeps volumes | verified by design + compose config | PASS |

## 4. Go (cluster agent)

| Check | Command | Result | Status |
|---|---|---|---|
| Format | `test -z "$(gofmt -l .)"` | clean | PASS |
| Vet | `go vet ./...` | clean | PASS |
| Build | `go build ./...` | clean | PASS |
| Tests | `go test ./...` | 5 packages ok (config, collector, health, logging, redact) | PASS |

## 5. Security gates (see also S0_SECURITY_REPORT)

| Check | Result | Status |
|---|---|---|
| Secret scan — full git history (gitleaks, digest-pinned) | no leaks found | PASS |
| Secret scan — working tree (gitleaks dir) | no leaks found | PASS |
| Dependency scan (osv-scanner over pnpm-lock/uv.lock/go.mod) | no issues (after overriding two vulnerable transitive npm packages) | PASS |
| Forbidden metric-label guard | catalog clean; negative cases rejected | PASS |
| Browser provider-access guard | 0 violations across scanned web sources | PASS |
| `git diff --check` | clean | PASS |
| Compose config validation | `docker compose config --quiet` clean | PASS |

## 6. Not run / deferred (honest list)

| Item | Status | Reason |
|---|---|---|
| GitHub Actions workflow execution | NOT RUN at authoring time | runs on push/PR; result visible on the PR checks tab |
| Visual QA matrix (390/768/1280/1536 px, light/dark screenshots) | MANUAL | structural/responsive tests exist; pixel review is human |
| Accessibility audit (WCAG 2.2 AA) | NOT RUN | foundation uses semantic roles/focus states; formal audit later sprint |
| E2E scenarios | NOT RUN | planned from Sprint 2 (login → project flows need those features) |
| Load/scale tests | NOT RUN | Sprint 12 scope |
| Kubernetes/cluster-connected tests | BLOCKED (by design) | no cluster access permitted in Sprint 0 |
| Node 24 local parity | PARTIAL | CI pins Node 24 via `.nvmrc`; local verification ran on Node 23 (`engines` documents 24) |
