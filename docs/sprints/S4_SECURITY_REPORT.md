# Sprint 4 — Security Report

Scope: cluster agent enrollment and identity, the internal trust boundary,
read-only Kubernetes discovery, inventory ingestion, and the scoped
inventory API. Everything below is enforced by code and proven by tests
listed in the S4 test report; nothing is aspirational.

## 1. Two-layer agent identity (ADR-0016)

Transport mTLS gates **who can connect**: the internal listener verifies
client certificates against the Drake Agent CA (`CERT_REQUIRED`; a real
handshake test proves bare and untrusted-CA clients are refused).
Per-request proof-of-possession decides **who the caller is**: every
post-enrollment request carries a detached ECDSA P-256 signature over
`method\npath\nsha256(body)\ntimestamp\nnonce`, verified against the
enrolled PUBLIC key. Claimed identity headers and claimed body ids are
cross-checked against the verified principal and are inert on their own —
spoofed headers achieve nothing even inside the TLS perimeter. Nonces are
single-use (Redis SETNX inside a bounded freshness window); stale
timestamps are refused. All identity failures share one generic refusal.

## 2. Enrollment: one-time, hashed, generic

Tokens are minted only by `integration.manage` at cluster scope (uniform
404 outside it), are ≥256-bit, live 10 minutes, are stored **only** as
sha256 digests, and are shown exactly once. Consumption is a single atomic
`UPDATE … WHERE used_at IS NULL … RETURNING`; a concurrent double-use test
admits exactly one winner. Unknown, expired, reused, and wrong-cluster
tokens are indistinguishable (`enrollment refused`). The agent generates
its P-256 key locally, sends only a CSR, and persists material atomically
with 0600 permissions; the private key never appears in the API, database,
logs, or rendered charts. Issued certificates are short-lived (14 days),
carry the SPIFFE URI SAN `spiffe://drake/cluster/{cluster_id}/agent/{agent_id}`
and clientAuth EKU; renewal at jittered 2/3 lifetime uses a FRESH key and
is bound to the verified principal, never to claimed ids. The CA private
key is referenced only through external file configuration; a
production-like environment refuses to start the internal app without it
(fail-closed `validate_runtime_security`).

## 3. Internal trust boundary

The internal agent app is a separate ASGI application: no sessions, no
cookies, no CSRF surface (cookies are proven inert), no public routes. It
is served only by the dedicated TLS listener. A pure-ASGI body cap refuses
oversized declared lengths early and cuts lying chunked streams at the
same ceiling (413), because `BaseHTTPMiddleware` remains banned in this
codebase (S3 finding: it swallowed `http.disconnect`).

## 4. Read-only collection, bounded to the byte

The agent's RBAC is exactly `get/list/watch` over an explicit resource
list — no secrets, no configmaps, no exec/attach/portforward, no
tokenreviews/subjectaccessreviews, no leases, no wildcards, no write verb.
This is enforced three times: the chart's policy validator refuses any
rendered drift; a disposable-cluster `kubectl auth can-i` matrix proves
the live surface (21 yes / 19 no); and the collector registry plus the
ingest schema refuse forbidden kinds at runtime. Normalization ships
bounded metadata only: allowlisted label/annotation keys (credential- and
`last-applied`-shaped keys dropped), scalar-only spec/status summaries,
capped owners/conditions. Full manifests never leave the cluster. Ingest
re-validates everything fail-closed (extra=forbid, bounds, forbidden
kinds, credential-shaped keys AND values) and the database adds a final
CHECK (`kind NOT IN ('Secret','ConfigMap')`, payload size bound). The E2E
canary — a real Secret in the disposable cluster — appears nowhere in any
Drake response.

## 5. Outbound-only agent

The agent dials out; nothing dials the agent. Its only listener is a
loopback liveness probe. The chart renders **no Service and no Ingress**,
a zero-ingress NetworkPolicy scaffold, a hardened single-replica pod
(non-root, read-only rootfs, all capabilities dropped, seccomp
RuntimeDefault, no privilege escalation, no host namespaces or hostPath),
and references existing Secrets only — the repository never templates
credential material. The transport pins the server CA, never follows
redirects, bounds timeouts and response bodies, and retries with jitter.

## 6. Projection integrity and honest states (ADR-0017)

Users only ever see a consistent cut: snapshots stage in separate tables
and swap the projection in ONE transaction; torn or abandoned snapshots
are discarded with the previous projection intact (proven: zero rows after
a torn complete). Sequence gaps, out-of-order pages, and overflow all
converge on an explicit durable `reconcile_required`; replays are
idempotent no-ops, which is what makes the single-replica agent safe to
crash and restart (no Lease permission exists or is needed). Deletes mark
`missing` — inventory history is never silently erased. Connectivity
(heartbeat age), inventory freshness (snapshot/event age), and workload
health are independent axes: a heartbeat alone never makes inventory
fresh, unknown is never presented as healthy, and stale is derived
server-side and rendered in its own visual state, never the healthy color.

## 7. Browser boundary

The browser talks only to the Drake `/v1` read API. Captured E2E network
payloads contain no certificates, keys, internal agent endpoints, or
ports. The provider-access guard still forbids provider vocabulary in
browser source; monitoring CRD kinds are reachable only through
data-driven links. Enrollment tokens surface exactly once, in the
authorized operator flow that mints them.

## 8. Standing deployment blocker (unchanged from Sprint 3)

Verified platform finding: Next's proxy hop does not propagate client
aborts, so the ingress must route `/v1` directly to the API for
browser-driven cancellation to reach it — the local rewrite exists only
for dev/E2E. This remains a deployment requirement.

## 9. Residual risks / accepted trade-offs

- Enrollment happens pre-certificate, so the internal listener runs
  CERT_OPTIONAL in local/test; production is expected to expose the
  enrollment path behind the dedicated internal gateway (ADR-0016), and
  the PoP layer — not the handshake — remains the identity authority in
  both modes.
- The agent identity in the chart lives in a memory-backed emptyDir:
  rescheduling discards it, and re-enrollment is a deliberate operator
  action with a fresh one-time token (documented in the runbooks). This is
  preferred over persisting private keys to cluster storage.
- Observation windows (90s heartbeat / 900s inventory) are ops-tunable via
  environment; E2E shrinks them to observe transitions. Values below the
  agent's heartbeat interval would flap `disconnected` — the defaults keep
  a 3× margin.
