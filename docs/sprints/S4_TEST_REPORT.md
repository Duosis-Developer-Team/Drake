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
| Tests (race) | `go test -race -count=1 ./...` | all 8 packages ok (collector, config, engine, health, identity, inventory, logging, redact) | PASS |

Engine coverage (fake dynamic client, `-race`): snapshot begin/pages/
complete ordering with a stable snapshot uid; strictly gapless inventory
sequences with heartbeats proven not to consume numbers; sequence
persistence (`TestSequencePersistsOnlyAfterAck`: the engine resumes from
the persisted cursor and stores each value only after the server ACK;
`TestFailedSendDoesNotPersistSequence`: refusals never advance it); 600
resources paged under the 500-per-page bound with zero loss; watch events
flowing to the bounded queue; `410 Gone` surfacing as a reconcile demand;
queue overflow dropping + raising the reconcile signal; sync-cycle state
transitions (`reconciling → fresh`); clean cancellation teardown of every
goroutine; optional CRDs collected only when present; renewal delay
targeting jittered 2/3 lifetime. Renewal protocol coverage
(`renewal_test.go`, against a STATEFUL fake server that tracks which key
it accepts and can commit a promotion while losing the response):
promotion only after activation
(`TestRenewOncePromotesOnlyAfterActivation`); the two ambiguity cases are
distinct and both proven —
**request never committed**: the old identity keeps working server-side,
the pending bundle and renewal id survive every retry, and the SAME
pending activates once the server returns
(`TestActivationNeverReachedServerKeepsOldIdentityAndRetries`);
**request committed but every response lost**: the server already
refuses the old key (asserted with a real 403 probe), yet the RUNNING
RenewalLoop reconciles in process — same renewal id, same bundle, zero
new prepares, idempotent 200, atomic local promotion, transport-swap
callback fired — with no restart
(`TestActivationCommittedButAllResponsesLostRecoversWithoutRestart`).
Context cancellation mid-retry exits cleanly without losing the pending
material (`TestCancellationDuringAmbiguousRetriesIsClean`); explicit
refusals discard the pending WITHOUT ever assuming the new key
(`TestExplicitRefusalDiscardsPendingWithoutAssumingNewKey`); refused
prepares leave no pending state; captured logs carry no private
material. Identity/bundle coverage
(`store_test.go`): versioned bundles are inert until the atomic pointer
names them; a failed promote keeps the old identity working; a simulated
crash mid-renewal loads the fully-old identity and, after promotion, the
fully-new one (`TestCrashLeavesFullyOldOrFullyNew`) — a key from one
generation with the certificate of another is impossible; 0600 key
permissions; no private material in pointer files; a corrupt sequence
file fails closed to 0. Plus PoP signature round-trip/tamper refusal
from the earlier identity tests.
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
| Unit tests | `uv run pytest -m "not integration" -q` | **149 passed** (incl. the 59-case health-rule table) | PASS |

Health-rule unit table (59 tests): Deployment replica axes
(ready/available/updated vs desired), `ReplicaFailure`,
`Progressing=False` incl. `ProgressDeadlineExceeded`, observed-generation
lag (only when both generations are reliably present); StatefulSet
current/updated replica and revision lag; DaemonSet rollout lag +
misscheduling; Pod `Ready`/`ContainersReady`/`PodScheduled` conditions
with the `Unschedulable` reason and CrashLoop/OOM precedence; Job
`Failed`/`Complete` conditions with bounded reason codes and
retrying-after-failures; CronJob schedule lag over bounded cron shapes
with an injected timezone-aware UTC clock
(`test_cronjob_schedule_lag_uses_injected_utc_clock`: naive datetimes
fail closed, unmodelled schedules stay honestly silent); Namespace
terminating vs finalizer-stuck; ResourceQuota exhausted/near-limit with
identical-unit comparison only (mixed units → unknown); PVC binding.
Rule-less kinds are `unknown` **with a reason**; healthy never carries
blame; malformed summaries fail closed to unknown.

## 3. Integration (live disposable local stack)

`uv run pytest -m integration` — **111 passed** (99 S0–S3 regression +
12 agent tests across three suites). Real PostgreSQL + Redis; the
internal agent app exercised through signed proof-of-possession requests.
Every durability claim below is asserted on a FRESH database connection
AFTER the HTTP response closed (`test_agent_closure_integration.py`) —
response text alone is never accepted as proof.

| Area | Evidence | Status |
|---|---|---|
| Migration chain | `0007` (writer/generation state, snapshot generations, page content hashes, pending-renewal columns) on top of `0006`; `base→0007→base→0007` cycle on a scratch database | PASS |
| Enrollment tokens | one-time semantics, sha256-at-rest (plaintext shown exactly once), generic refusals (unknown/expired/reused/wrong-cluster indistinguishable), scope-authorized minting with uniform 404, **concurrent double-use admits exactly one** | PASS |
| PoP identity | spoofed headers inert without the enrolled key, replayed nonces refused, stale timestamps refused, cookies inert on the internal app, only PUBLIC keys stored | PASS |
| Two-phase renewal | prepare leaves the CURRENT key untouched; same renewal_id+CSR retry returns the SAME pending certificate (lost response safe); same renewal_id+different CSR refused; activation requires possession of the PENDING key (the old key cannot promote); idempotent activation retry after a lost response; old key live until activation and refused after; new key accepted; pending material cleared on promotion | PASS |
| Real TLS handshake | CA-signed client cert handshake accepted; bare and untrusted-CA handshakes refused by a real uvicorn CERT_REQUIRED listener | PASS |
| Atomic snapshots | begin/pages/complete swaps the projection in one transaction; replayed complete/pages/events are idempotent duplicates; absent resources flip to `missing` (never hard-deleted) with change events | PASS |
| Torn snapshots | wrong totals → 409 `reconcile_required`, snapshot discarded, **projection untouched (0 rows)**, refusal durable across the rolled-back request | PASS |
| Sequences (durable) | gaps on page/complete/watch each → 409 AND `reconcile_required` proven durable on a fresh connection after the response; refused payloads leave zero staging/projection residue; heartbeats never clear the demand; ONLY a successful full snapshot returns to fresh; restarts resume the persisted sequence (never blind re-base); a delayed stale-sequence begin is a no-op; exact replays no-op with exactly one applied change event | PASS |
| Page continuity | pages {2,3} and {1,3} with matching COUNTERS refused (the SET must be exactly 1..total_pages); duplicate UIDs across pages cannot inflate totals; the same page number with different content is a torn stream; identical replays idempotent | PASS |
| Generations/writers | a superseded snapshot can never complete; a delayed old begin regresses nothing (applied generation asserted unchanged); two agents racing one cluster → only the newest enrolled writer wins; the superseded agent is blocked from every inventory write (incl. watch events) while its heartbeat still lands | PASS |
| TTL + bounded cleanup | a timed-out pending snapshot is refused at complete with the last-good projection intact; expired/over-cap pendings discarded; dead staging drained in bounded batches; snapshot history pruned with the applied snapshot always retained; change events bounded by age and per-cluster rows; another cluster's data untouched; a second maintenance pass is a no-op | PASS |
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

Browser acceptance gates (`zz-inventory-a11y.spec.ts`, production build,
REAL inventory data): 390px mobile AND 1280px desktop, light AND dark
theme — each combination scans cluster list/detail/browser with axe
(critical+serious = 0) and zero horizontal viewport overflow; heading and
`th[scope=col]` table semantics; keyboard-only filter, row link, and
drilldown with visible focus; stale-never-healthy and always-visible
unknown buckets on live screens; uniform denied/not-found; captured
payloads free of certificates, keys, and internal endpoints.

## 6. Helm / policy

| Check | Command | Result | Status |
|---|---|---|---|
| Agent chart | `bash deploy/agent/validate.sh` | lint + negative fail-closed renders (digest-less image, empty CIDRs) + policy OK (5 documents) | PASS |
| Chart smoke | `bash scripts/chart_smoke_k3d.sh` | image build → disposable k3d → FULL rendered chart applied → pod Ready → in-container `drake-agent healthcheck` exit 0 → no Service/Ingress → securityContext intact → agent syncs to fresh THROUGH the restrictive NetworkPolicy → unroutable API CIDR stops heartbeats → full cleanup | PASS |
| Observability chart (S3) | `bash deploy/dev/observability/validate.sh` | unchanged, passing | PASS |
| Prometheus config | `promtool check config` (digest-pinned image) | valid | PASS |

Agent chart policy assertions (executable, fail-closed): verbs exactly
get/list/watch; explicit resources only; no secrets/configmaps/exec/
attach/portforward/tokenreviews/subjectaccessreviews/wildcards; single
replica; non-root + read-only rootfs + drop ALL + seccomp RuntimeDefault +
no privilege escalation; no host namespaces/hostPath; digest-pinned image;
resource limits; **no Service/Ingress (zero inbound surface)**; no Secret
material rendered (existingSecret refs only); NetworkPolicy denies ALL
ingress, refuses `0.0.0.0/0` and `::/0`, requires explicit CIDRs for the
Drake endpoint and the Kubernetes API, and restricts DNS egress to the
kube-dns pods; liveness must be the agent binary's own `healthcheck`
subcommand (the `apps/cluster-agent/Dockerfile` contract: digest-pinned
multi-stage build, distroless static nonroot runtime, static binary at
`/usr/local/bin/drake-agent`, uid 65532) with a bounded timeout.

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
suite (`access` + `catalog` + `inventory` + `metrics` +
`zz-inventory-a11y`):

| Run | Result | Status |
|---|---|---|
| Run 1 | 42 passed, exit 0 | PASS |
| Run 2 | 42 passed, exit 0 | PASS |

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

## 9. UI acceptance gates (within the E2E suite)

`zz-inventory-a11y.spec.ts` runs against the production build with the
REAL enrolled inventory: axe (critical+serious = 0) and zero horizontal
overflow at 390×844 AND 1280×900, in light AND dark theme, across cluster
list/detail/browser and the drilldown; exactly one `h1` with `h2`
sections; `th[scope=col]` table semantics; keyboard-only filter →
row-link → drilldown with `:focus-visible`; the server-derived stale
state rendered as `status-stale` with zero `status-healthy` in the
freshness card; unknown buckets always visible; denied/not-found uniform
at mobile width; captured `/v1` payloads free of certificates, keys, and
internal endpoints. Fixing these gates required only contrast-token
darkening (same hues) and dropping one fixed table min-width — no visual
redesign.

## 9b. Hygiene

| Check | Result | Status |
|---|---|---|
| `git diff --check` | clean | PASS |
| Tracked-tree audit | no CTO pack, no `.env` (only `.env.example`), no key/cert material | PASS |
| Secret scan | `bash scripts/secret-scan.sh all` — history + tree + 3-canary regression, no leaks | PASS |
| Dependency scan | osv-scanner not installed locally | NOT RUN (CI job passes on the branch) |
| Main CI after S3 merge | merge commit `d4e5334` — all 8 required checks success | PASS |

Numbers above reflect the closure-final tree: two consecutive 42/42 E2E
runs, 149 unit + 111 integration Python tests, 63 contracts, 59 web unit
tests, all 8 Go packages under `-race -count=1`, the `base→0007→base→0007`
migration cycle, the 21-yes/19-no RBAC matrix, the full chart smoke with
positive AND negative NetworkPolicy paths, both Helm policy gates with
fail-closed negative renders, promtool, and the 3-canary secret scan —
every one judged by exit code on this branch.
