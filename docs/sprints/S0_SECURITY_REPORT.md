# Sprint 0 — Security Report

Public-safe summary of the security posture established in Sprint 0.

## 1. Repository hygiene

- The internal product/architecture dossier is **not** part of this
  repository: it is ignored via `.gitignore` and verified absent from the
  git index and full history.
- `.gitignore` blocks `.env*` (except `.env.example`), key/certificate
  formats, kubeconfig-pattern files, state files, and build artifacts.
- `.env.example` contains placeholders only; no real values.
- No real credentials, tokens, connection strings, or internal endpoints
  exist anywhere in the repository or its history (verified by scan, below).

## 2. Scans (digest-pinned tools, executed locally and gated in CI)

| Scan | Tool | Result |
|---|---|---|
| Secret scan, full git history | gitleaks (image pinned by sha256 digest) | **no leaks found** |
| Secret scan, working tree | gitleaks `dir` mode | **no leaks found** |
| Dependency vulnerabilities | osv-scanner over `pnpm-lock.yaml`, `uv.lock`, `go.mod` (464 packages) | **no issues** |

Notes:

- Two vulnerable transitive npm packages surfaced during Sprint 0 were fixed
  by pnpm version overrides to their patched releases (verified by re-scan),
  not by suppressing the scanner.
- **The gitleaks configuration contains no path allowlists.** No source,
  test, fixture, docs, or contracts directory is excluded from scanning.
  Credential-shaped rejection test cases are generated at test runtime from
  concatenated parts and written only to non-committed temp locations, so
  nothing scanner-detectable is ever a committed literal.
- The tree scan runs over a clean copy of exactly the tracked/staged file
  set, so gitignored build output can neither mask findings nor add noise —
  scan scope equals what is or would be committed.
- A **canary regression test** (`scripts/secret-scan.sh canary`, part of the
  CI secret-scan job) plants a temporary, non-committed, high-confidence
  fake credential inside the negative-fixture directory and requires the
  scanner to detect it — continuously proving that fixture directories are
  not exempt. The planted value is generated per run, never printed, and
  always cleaned up.

## 3. Enforced security boundaries (tested, not just documented)

| Boundary | Enforcement |
|---|---|
| Browser never reaches telemetry providers / Kubernetes API | static guard test scans web sources; absolute URLs forbidden in Sprint 0 web code |
| Audit trail is append-only | PostgreSQL trigger rejects UPDATE/DELETE/TRUNCATE; negative tests hit the live database |
| Audit/log/job payloads cannot carry credentials | redaction + credential-shape guards in API logging, audit metadata, worker payloads; unit-tested |
| Manifests cannot carry secrets/SQL/plaintext endpoints | contracts content-policy rules with per-rule negative fixtures; findings never echo matched values |
| Agent read-only contract | collector registry rejects secrets/exec/attach/portforward/wildcard kinds; allowed verbs frozen to get/list/watch by test |
| Agent has no inbound control surface | only listener is loopback-bound `/healthz`; non-loopback bind rejected by config validation; non-GET and other paths return 404 |
| Agent transport | outbound-only; `https` required for non-loopback API endpoints; embedded credentials in URLs rejected |
| API CORS | deny-by-default; middleware only exists when origins are explicitly configured |
| Error envelope | unhandled errors return a generic message; internals/secrets never reach clients (tested) |
| Metric label policy | forbidden PII/unbounded labels break tests/CI |
| Local destructive operations | `destroy-local-data` refuses non-local environments and is chained to nothing |

## 4. CI security properties

- All GitHub Actions pinned to immutable commit SHAs (version noted inline).
- Scanner images pinned by sha256 digest.
- Workflow `permissions: contents: read`; no secrets configured or required;
  safe for public fork PRs.
- Secret scan and dependency scan are blocking gates.

## 5. Known security work ahead (tracked, not blocking Sprint 0)

- Authentication/RBAC arrive in Sprint 1 (OIDC + scoped grants + IDOR
  negative tests). Until then the API exposes only health endpoints.
- Agent mTLS enrollment is a stub; the contract is documented and the
  implementation is a later sprint with its own review gate.
- One source-repository security review item is tracked privately outside
  this repository and gates the corresponding integration sprint. Details
  are intentionally not published here.
