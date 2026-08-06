# ADR-0016 — Agent enrollment, certificate lifecycle, and the mTLS trust boundary

Status: accepted (Sprint 4)
Builds on: ADR-0005 (read-only agent), ADR-0013 (auth architecture),
ADR-0015 (fail-closed runtime boundaries).

## Context

The cluster agent needs a service identity that is not a user session:
outbound-only, unattended, per-cluster, revocable, and never dependent on
spoofable request headers. uvicorn (our ASGI server) verifies TLS client
certificates at the handshake but does not expose the peer certificate to
the application, so certificate identity cannot be read in-app on the
public listener.

## Decision

### 1. One-time enrollment tokens

`POST /v1/clusters/{cluster_id}/agent-enrollment-tokens` requires
`integration.manage` resolved at the cluster scope (out-of-scope and
unknown clusters are one uniform 404). Tokens are 256-bit random values,
default TTL 10 minutes (hard cap 15), stored **only as SHA-256 hashes**,
shown exactly once in the creation response, absent from every list/read
API, and consumed atomically — concurrent double-use admits exactly one
winner (`UPDATE … WHERE used_at IS NULL` row-count discipline). Creation
and consumption are audited without token material. Used/expired/unknown/
malformed tokens all fail with one generic result — no oracle.

### 2. CSR enrollment — the private key never travels

The agent generates its keypair locally, submits `{token, CSR}` over
server-TLS to `POST /internal/v1/agent/enroll`, and receives a short-lived
certificate (default 14 days) plus the CA chain. The server signs the
CSR's public key only; it never generates, sees, stores, or logs a private
key. The certificate binds identity cryptographically via a SPIFFE-style
URI SAN — `spiffe://drake/cluster/{cluster_id}/agent/{agent_id}` — and a
clientAuth EKU. Certificate serial/expiry and the agent's **public** key
are stored as metadata; CA private material comes exclusively from
file/external-secret references (`DRAKE_AGENT_CA_CERT_FILE`,
`DRAKE_AGENT_CA_KEY_FILE`) and never enters the repository, the database,
or a response. Local/CI tests generate ephemeral CAs at test time. The
agent writes its key/cert atomically with `0600` permissions.

### 3. Two-layer identity: mTLS transport + proof-of-possession

- **Transport**: the internal agent listener is a separate bind serving
  only `/internal/v1/agent/*`, TLS-terminated with `CERT_REQUIRED`
  against the Drake Agent CA for post-enrollment endpoints (enrollment
  itself is server-TLS + token). Nothing on the public listener serves
  agent endpoints; agent endpoints accept no cookies, sessions, or CSRF
  identities.
- **Application**: every post-enrollment request additionally carries a
  detached signature over `method\npath\nsha256(body)\ntimestamp\nnonce`
  made with the enrolled private key. The server resolves the agent by ID,
  verifies the signature against the **stored public key**, enforces a
  freshness window and per-agent nonce replay protection, and only then
  trusts the identity. Claimed IDs, `X-Client-Cert`-style headers, or any
  other client-writable material are never a trust source: without the
  private key a request cannot impersonate an agent, even from inside the
  TLS perimeter.

This composition survives every deployment shape: direct hypervisor TLS,
proxy-terminated mTLS (proxy must strip inbound identity headers; the PoP
layer still binds identity end-to-end), or a future SPIFFE mesh.

### 4. Renewal

`POST /internal/v1/agent/certificates/renew` accepts a CSR signed-for by
the **current** verified identity (PoP with the current key); the claimed
cluster/agent in the CSR SAN must match the verified identity or the
request fails closed. Expired, wrong-CA, wrong-cluster, or revoked
identities cannot renew. The agent renews with jitter inside the final
third of certificate lifetime and backs off exponentially on failure —
never a tight loop.

### 5. Fail-closed production boot

In a production-like environment, enabling the internal agent API without
CA material and TLS configuration refuses to boot. Real ingress/gateway
wiring is a deployment step outside this sprint and is tracked as a
blocker alongside the S3 `/v1`-direct-routing requirement.

## Consequences

- No secret material in repo/DB/logs; revocation = deleting/archiving the
  agent row (PoP fails) plus short cert lifetime.
- The PoP layer costs one signature verification per agent request —
  negligible against ingest work, and it makes the trust boundary
  auditable in tests (spoofed headers and cookie identities are provably
  inert).
- Single CA for agents; rotation is a documented runbook (issue new CA,
  dual-trust window, agent renewals migrate, retire old CA).
