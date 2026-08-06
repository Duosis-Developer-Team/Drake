# Sprint 4 — Test Report

Every `PASS` reflects a command actually executed on the Sprint 4 branch
during final verification, judged by exit code. Status vocabulary:
`PASS` / `FAIL` / `NOT RUN` / `BLOCKED` / `MANUAL` / `PARTIAL`.

## 1. Go agent

| Check | Command | Result | Status |
|---|---|---|---|
| Format | `gofmt -l .` | empty | PASS |
| Vet | `go vet ./...` | clean | PASS |
| Build | `go build ./...` | clean | PASS |
| Tests (race) | `go test -race -count=1 ./...` | all packages ok (collector, config, engine, health, identity, inventory, logging, redact) | PASS |

Engine coverage (fake dynamic client, `-race`): snapshot begin/pages/
complete ordering with a stable snapshot uid; strictly gapless inventory
sequences with heartbeats proven not to consume numbers; 600 resources
paged under the 500-per-page bound with zero loss; watch events flowing to
the bounded queue; `410 Gone` surfacing as a reconcile demand; queue
overflow dropping + raising the reconcile signal; sync-cycle state
transitions (`reconciling → fresh`); clean cancellation teardown of every
goroutine; optional CRDs collected only when present; renewal delay
targeting jittered 2/3 lifetime. Identity coverage: save/load round-trip,
0600 key permissions, PoP signature verifying against the canonical string
and refusing a tampered body, no private material in cert/CA files.
Inventory normalization coverage: identity fields, forbidden annotation
keys (`last-applied`/token/secret shapes) never passing, 32-entry/512-byte
label bounds, owner/condition caps, scalar-only summaries (container env
with credential-shaped values proven absent from output), CrashLoop/OOM/
restart extraction, allowlist free of Secret/ConfigMap/wildcards/
subresources.

## 2. Python (api + worker)

| Check | Command | Result | Status |
|---|---|---|---|
| Format | `uv run ruff format --check .` | 102 files clean | PASS |
| Lint | `uv run ruff check .` | clean | PASS |
| Typecheck (strict) | `uv run mypy apps/api/src apps/worker/src` | 0 issues / 63 source files | PASS |
| Unit tests | `uv run pytest -m "not integration" -q` | **152 passed** (116 S0–S3 + 36 health-rule table) | PASS |

Health-rule unit table: Deployment/ReplicaSet/StatefulSet replica verdicts
(healthy/degraded/unhealthy/scaled-to-zero/unknown), DaemonSet
misscheduling, Pod phases + CrashLoop/OOM/restart-churn precedence, Job
and CronJob outcomes, Node readiness + pressure conditions, Namespace
termination, PVC binding; rule-less kinds are `unknown` **with a reason**;
healthy never carries blame; malformed summaries fail closed to unknown.

## 3. Integration (live disposable local stack)

`uv run pytest -m integration` — **108 passed** (99 S0–S3 regression + 9
agent suites). Real PostgreSQL + Redis; the internal agent app exercised
through signed proof-of-possession requests.

| Area | Evidence | Status |
|---|---|---|
| Migration chain | `0006` (tokens, agents, snapshots, pages, staging, projection, change events); `base→0006→base→0006` cycle on a scratch database | PASS |
| Enrollment tokens | one-time semantics, sha256-at-rest (plaintext shown exactly once), generic refusals (unknown/expired/reused/wrong-cluster indistinguishable), scope-authorized minting with uniform 404, **concurrent double-use admits exactly one** | PASS |
| PoP identity | spoofed headers inert without the enrolled key, replayed nonces refused, stale timestamps refused, cookies inert on the internal app, renewal rotates keys bound to the VERIFIED principal, only PUBLIC keys stored | PASS |
| Real TLS handshake | CA-signed client cert handshake accepted; bare and untrusted-CA handshakes refused by a real uvicorn CERT_REQUIRED listener | PASS |
| Atomic snapshots | begin/pages/complete swaps the projection in one transaction; replayed complete/pages/events are idempotent duplicates; absent resources flip to `missing` (never hard-deleted) with change events | PASS |
| Torn snapshots | wrong totals → 409 `reconcile_required`, snapshot discarded, **projection untouched (0 rows)**, refusal durable across the rolled-back request | PASS |
| Sequences | gap → 409 + reconcile_required; restart re-bases at `snapshot_begin` (crash-only agent proven); exact replays no-op with exactly one applied change event | PASS |
| Ingest security | Secret kind 422, ConfigMap kind 422, credential-shaped annotation keys 422, private-key-shaped values 422, nested raw manifests fail schema, claimed cluster/agent id mismatch → generic 403, 8 MiB+ body → 413 at the stream boundary; staging + projection contain zero forbidden rows | PASS |
| User API | cluster.view gates summary/resources/detail; no-grant and cross-scope both uniform 404 (including a real resource id through the wrong cluster path); bounded kind filter refuses Secret; stable keyset cursor over 3 resources at limit 2 | PASS |
| Freshness honesty | heartbeat alone never turns inventory fresh (`empty` stays); a `fresh` claim with 2-hour-old activity reads **stale** while connectivity stays `connected` (separate axes) | PASS |

## 4. Contracts

`npm test` in `packages/contracts` — **63 passed** (59 prior + 4 agent
inventory: valid snapshot page, Secret-kind refusal, raw-manifest refusal,
CLI validation).

## 5. Web

| Check | Command | Result | Status |
|---|---|---|---|
| Lint | `npm run lint` | clean | PASS |
| Types | `npx tsc --noEmit` | clean | PASS |
| Component tests | `npm test -- --run` | **59 passed** (52 prior + 7 inventory screens) | PASS |
| Production build | `npm run build` | compiled | PASS |
| Provider guard | part of test suite | no provider vocabulary in browser source (monitoring CRD kinds reachable only via data-driven links) | PASS |

Inventory screen tests: real agent + freshness badges on the cluster list;
agent card with version/heartbeat/cert-expiry warning; stale rendered as
STALE (asserted `status-stale`, never `status-healthy`); summary errors as
error states with retry; resource browser rows with missing-lifecycle
rendering and kind-filter fetches; uniform not-found; resource detail
reason codes/conditions/provenance; a badge-mapping test proving no
non-fresh state ever uses the healthy color.

## 6. Helm / policy

| Check | Command | Result | Status |
|---|---|---|---|
| Agent chart | `bash deploy/agent/validate.sh` | lint + template + policy OK (5 documents) | PASS |
| Observability chart (S3) | `bash deploy/dev/observability/validate.sh` | unchanged, passing | PASS |
| Prometheus config | `promtool check config` (digest-pinned image) | valid | PASS |

Agent chart policy assertions (executable, fail-closed): verbs exactly
get/list/watch; explicit resources only; no secrets/configmaps/exec/
attach/portforward/tokenreviews/subjectaccessreviews/wildcards; single
replica; non-root + read-only rootfs + drop ALL + seccomp RuntimeDefault +
no privilege escalation; no host namespaces/hostPath; digest-pinned image;
resource limits; **no Service/Ingress (zero inbound surface)**; no Secret
material rendered (existingSecret refs only).

## 7. RBAC proof on a disposable cluster

`bash scripts/k3d_agent_acceptance.sh` — disposable k3d cluster, chart-
rendered RBAC applied, `kubectl auth can-i` matrix as the agent
ServiceAccount: **21 allowlisted reads = yes** and **19 forbidden
operations = no** (secrets get/list/watch, configmaps, pods/exec|attach|
portforward, pod create/delete, deployment update, node patch,
deletecollection, tokenreviews, subjectaccessreviews, leases, impersonate,
escalate, bind). Cluster deleted afterwards. Status: PASS.

## 8. Full-chain E2E (nothing mocked)

Chain: fake OIDC → production Next.js build → FastAPI → PostgreSQL →
internal TLS listener → **real Go agent binary** → disposable k3d
Kubernetes cluster. Two consecutive fresh runs of the entire Playwright
suite (`access` + `catalog` + `metrics` + `inventory`):

| Run | Result | Status |
|---|---|---|
| Run 1 | 32 passed, exit 0 | PASS |
| Run 2 | 32 passed, exit 0 | PASS |

The 12-step inventory scenario: UI-minted one-time token → CSR enrollment
(key never leaves the agent) → atomic snapshot to `fresh` → real
connectivity/freshness on the cluster screens → real nodes/namespaces/
workloads from k3d → filtered browsing + drilldown → a live namespace
creation propagating via WATCH with `last_reconcile_at` unchanged (events,
not re-snapshot) → a real cluster Secret proven absent from every Drake
response (kind filter 422, zero matching rows, canary value nowhere) →
agent kill turning `disconnected` then `stale` (stale badge asserted, no
healthy badge) → same-identity restart reconciling back to `fresh` →
uniform 404s for a user without cluster scope → captured browser payloads
free of certificates, keys, and internal agent endpoints.

## 9. Hygiene

| Check | Result | Status |
|---|---|---|
| `git diff --check` | clean | PASS |
| Tracked-tree audit | no CTO pack, no `.env` (only `.env.example`), no key/cert material | PASS |
| Secret scan | `bash scripts/secret-scan.sh all` — history + tree + 3-canary regression, no leaks | PASS |
| Dependency scan | osv-scanner not installed locally | NOT RUN (CI job passes on the branch) |
| Main CI after S3 merge | merge commit `d4e5334` — all 8 required checks success | PASS |
