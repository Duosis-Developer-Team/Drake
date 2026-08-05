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
- The only gitleaks allowlist entries are (a) the validator's *negative*
  fixtures, which contain deliberately fake, non-functional credential
  shapes that exist to prove rejection, and (b) gitignored Next.js build
  caches. Default rules remain fully enabled for all committed content.
- A deliberately fake cloud-key test value is constructed by string
  concatenation at runtime so it never appears as a scannable literal.

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
