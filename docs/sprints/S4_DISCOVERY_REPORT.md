# Sprint 4 — Discovery Report

Pre-implementation survey for the Cluster Agent & Kubernetes Inventory
slice. Public-safe: no internal topology, hosts, or credentials.

## 1. What exists (verified in-repo)

| Area | State | Sprint 4 impact |
|---|---|---|
| `apps/cluster-agent` | Sprint 0 foundation: env config + validation, structured logging, loopback `/healthz`, graceful shutdown, collector **registry guard** (forbidden kinds incl. secrets/exec/attach/portforward/wildcards rejected at registration), enrollment/transport stubs, redaction helpers. **No Kubernetes client, no network transport.** Pure stdlib `go.mod`. | Real client-go, LIST/WATCH engine, mTLS transport, and CSR enrollment replace the stubs; the registry guard and redaction contracts stay load-bearing. |
| Catalog `clusters` table (0004) | `cluster_ref`, display/site, lifecycle, provenance, RBAC `scope_id` (RESTRICT). | Agent identity, snapshots, and inventory tables reference `clusters.id`; authorization reuses the cluster scope. |
| RBAC | `cluster.view` and `integration.manage` exist in the pack permission catalog; `visible_scope_ids` computes subtree unions before search/count/cursor. | Token creation gates on `integration.manage` @ cluster scope; all inventory reads gate on `cluster.view`; consistent-404 contract reused. |
| Integration observations | `integrations` projection with bounded `last_error_code` (DB CHECK + validator), `cluster-agent` type seeded per project; cluster scopes accept integrations. | Agent connectivity feeds a `cluster-agent` integration observation at the cluster scope: `not_configured` (no agent) / `unknown` / `ok` / `degraded` / `stale`. |
| Audit service | Transactional append-only audit with metadata redaction. | Token create/consume audited (no token material in payloads). |
| Migrations | Chain `0001..0005`, head `0005`; up/down/up proven on disposable DBs. | New `0006_agent_inventory` extends the chain; no edits to existing revisions. |
| API error/correlation | Typed envelope, `X-Correlation-ID` middleware (pure ASGI since S3), consistent 404s, cursor pagination patterns. | Internal agent endpoints reuse the envelope; user-facing inventory APIs reuse keyset cursors. |
| Cluster web screens | `/clusters` list + detail exist with honest `not_configured` operational cards and referenced-environments table. | Screens gain real agent/inventory/health data; state semantics (`unknown`/`stale`/`reconciling`/`empty`/`denied`/`error`) extend the existing widget language. |
| Helm policy harness | `deploy/dev/observability/validate.sh`: lint + template + rendered-manifest policy greps (no LB/NodePort/Ingress, no credential-shaped content, no wildcard RBAC). | The agent chart gets the same treatment plus verb/resource-specific RBAC policy checks. |
| CI | 8 required checks; e2e job runs compose Prometheus; integration job runs helm validation. | e2e job gains a disposable Kubernetes cluster (k3d) for RBAC proof + agent acceptance; no required-check renames. |
| Local tooling | `kubectl` and `k3d` available locally; CI can install both (pinned) — **kind is not installed locally, k3d is; the disposable-cluster scripts use k3d**. | Disposable cluster is created and destroyed by test scripts only. |

## 2. CTO pack requirements distilled

- **KUBERNETES_DISCOVERY_STANDARD**: allowed verbs exactly `get/list/watch`; resource families namespaces→events (+ Prometheus Operator CRD metadata only if present); forbidden: secrets, configmap data, exec/attach/portforward, token/subject-access reviews, writes, wildcards. Identity = `cluster_id + api_group + kind + namespace + uid` (name is mutable). Watch from LIST resourceVersion; bounded full reconcile after disconnect. Health derivation per family; unknown ≠ healthy; reasons preserved.
- **DOMAIN_DATA_MODEL**: UUID keys, UTC, soft lifecycle (`missing` before archive), no cascade delete of history, inventory as normalized identity + bounded selected fields — never full manifests/secrets.
- **API_AND_EVENT_CONTRACT**: `/internal/v1/agent/*` family; cursor pagination; typed envelope; correlation IDs; idempotency.
- **SECURITY_RBAC_AND_AUDIT**: mTLS/SPIFFE-ready service identity; one-time short-lived enrollment token → certificate; agent cannot read Secrets; no cluster-wide write; restricted pod security.
- **SCREEN_WIDGET_MATRIX / TEST_AND_ACCEPTANCE_STRATEGY**: cluster screens show agent/inventory/health honestly; acceptance runs the real chain against a disposable cluster.

## 3. Key design constraints discovered

1. **uvicorn does not expose the TLS peer certificate to ASGI**, so
   client-cert identity cannot be read in-app on the existing server.
   Consequence (ADR-0016): transport-level mTLS termination (handshake
   verified against the Drake Agent CA, `CERT_REQUIRED`) **plus**
   application-level proof-of-possession — every post-enrollment agent
   request is signed with the enrolled private key; identity derives from
   the signature, never from claimed headers. This also survives a future
   proxy-terminated-mTLS deployment unchanged (defense in depth).
2. **The S0 agent has zero dependencies**; adding pinned `k8s.io/client-go`
   (dynamic client) is the first real dependency and is unavoidable.
3. **Cluster capability wiring already exists**: the catalog seeds a
   `cluster-agent` integration; agent connectivity becomes its observation
   without new integration plumbing.
4. **The S3 deployment blocker stands**: production ingress must route
   `/v1` directly to FastAPI (browser-abort propagation). The internal
   agent listener is a *separate* bind with its own blocker: production
   boot fails closed unless the Agent CA material and internal TLS are
   explicitly configured.

## 4. Decisions recorded as ADRs

- ADR-0016 — Agent enrollment, certificate lifecycle, and the mTLS trust
  boundary.
- ADR-0017 — Snapshot/watch ordering, dedupe, and atomic inventory
  projection.
- ADR-0018 — Inventory allowlist, bounded metadata, and health derivation.
