# ADR-0019 — GitHub App identity, webhook trust boundary, and least privilege

Status: accepted (Sprint 5A)
Builds on: ADR-0003 (browser talks only to the Drake API), ADR-0010
(deny-by-default scoped RBAC), ADR-0016 (agent enrollment and the mTLS
trust boundary — the same "identity is proven, never claimed" discipline).

## Context

Drake needs to observe how DuoSis repositories are governed: default
branch, branch protection or rulesets, required checks, and whether
production deployments are gated. That means talking to GitHub as a
**GitHub App**, and accepting **webhooks** from GitHub.

Two failure modes decide the design. First, GitHub App credentials are
extraordinarily powerful: a private key that can mint installation
tokens for every installed repository. Second, a webhook endpoint is
unauthenticated by construction — anyone on the internet can POST to it.
Both must be safe by default, not by discipline.

## Decision

### 1. Three distinct credential layers, never conflated

| Layer | Material | Lifetime | Where it may exist |
|---|---|---|---|
| App identity | RSA private key (PEM) | until rotated | external secret store only, referenced by name |
| App JWT | RS256 JWT, `iss`=client id | ≤ 10 minutes | process memory, per request |
| Installation token | `ghs_…` opaque string | ~1 hour | process memory cache only |
| Webhook identity | shared secret | until rotated | external secret store only, referenced by name |

The PEM, the JWT, the installation token, and the webhook secret are
**never** written to the database, a log line, an exception message, an
audit record, or an API response. The database stores only *reference
names* (`config_ref`, matching the existing integrations contract) and
non-secret metadata.

### 2. JWT construction is exactly what GitHub documents

Signed with **RS256** (GitHub: "Your JWT must be signed using the
`RS256` algorithm"). `iat` is set **60 seconds in the past** to absorb
clock drift, per GitHub's own recommendation. `exp` is bounded to at
most **10 minutes** in the future — GitHub rejects anything longer, so
the ceiling is enforced locally and a caller asking for more is refused
rather than silently clamped into a token GitHub would reject. `iss` is
the **client id** (GitHub: "Use of the client ID is recommended"); the
app id remains accepted for operators who configured it earlier. The
clock is injectable so every boundary is testable without sleeping.

### 3. Installation tokens are minted lazily, cached per installation

A token is requested only when a call actually needs one, cached in
**process memory keyed by installation id**, and refreshed once it is
inside a safety buffer before its expiry — never at the last second, so
a slow request cannot ride an already-dead token. Tokens are opaque:
the code never assumes a fixed length or parses structure beyond the
documented `ghs_` prefix, because GitHub has changed token formats
before and will again. Where the API allows it, the token is minted
**scoped to the target repositories and to the minimum permissions**,
so even in-memory the blast radius is smaller than the installation.

### 4. The webhook is a signature boundary, not a session boundary

The endpoint belongs to no user session; it has no cookies and no CSRF
token. Trust comes from one place: an HMAC-SHA256 over the **raw request
bytes**, compared in constant time. The order is fixed and fail-closed:

1. read the raw body once, under a hard size ceiling;
2. require `X-Hub-Signature-256`, `X-GitHub-Delivery`, `X-GitHub-Event`;
3. compute HMAC-SHA256 over those exact bytes;
4. compare constant-time;
5. **only then** parse JSON;
6. validate the delivery id shape and the event allowlist;
7. validate that the installation/owner in the payload matches what
   Drake already knows;
8. hand the work to the queue.

No JSON parsing, no queue write, and no domain mutation happens before
step 4 succeeds. A body that fails any step produces a bounded, generic
refusal and a secret-free audit record.

### 5. Replay is a database invariant, not a cache heuristic

Each delivery id is unique **at the database level**. A repeated
delivery with the **same payload digest** is acknowledged as an
idempotent no-op. A repeated delivery id with a **different digest** is
treated as an attack signal: refused fail-closed and audited. Concurrent
deliveries of the same id race on the unique index, so exactly one
winner performs the work. Raw payloads are never stored: Drake keeps the
event envelope, a small set of explicitly chosen fields, and a digest.

### 6. Least privilege is read-only in this sprint

Every permission is justified against an endpoint Drake actually calls
(see `docs/github-app-permission-matrix.md`). Defaults are read-only.
The app requests **no** write permission of any kind in Sprint 5A:
no contents, no workflows, no deployments, no administration write.
Drake never mutates repository settings, never dispatches a workflow,
never creates a deployment, never opens or merges a pull request, and
never writes a check or status result.

If the real installation grants **less** than a rule needs, the rule
reports `UNKNOWN` and the integration moves to `DEGRADED` or `BLOCKED`.
Drake never attempts to escalate its own privileges, and a missing
permission is never allowed to look like a passing check.

## Consequences

- Losing the database loses no credential material; rotating the app key
  or webhook secret is an operator action in the secret store, with no
  schema change.
- Webhook handling stays cheap and constant-time in the rejection path,
  so an unauthenticated flood cannot push Drake into JSON parsing or
  database work.
- Read-only permissions mean some governance questions are simply not
  answerable (for example, workflow file contents). Those answer
  `UNKNOWN` with an explicit reason instead of guessing — consistent
  with ADR-0011's state semantics.


## Amendment — durability and real limits (CTO fix gate)

**A delivery is acknowledged only once its work is durable.** The original
design claimed the delivery id and started the domain work in a separate
transaction. That is a lost-update hole: a crash in between leaves a row
whose digest matches GitHub's retry, so the retry is indistinguishable
from a harmless replay and the event disappears behind two 202s. The
delivery row is therefore the durable work item — an inbox, not a receipt.
It is claimed `pending`, and only a transaction that commits the domain
work *and* the `processed` flag together may close it. Recovery has two
paths, because GitHub does not redeliver forever: a redelivery runs a
`pending` row, and a drain worker picks up rows nobody redelivers.
Attempts are counted outside the work transaction — counting them inside
would roll the count back with the failure — so a poison delivery
dead-letters and is audited instead of retrying without end.

**Stored deliveries are evidence.** A conflicting replay does not modify
the original row's digest, status, or processed timestamp, and does no
domain work. Rewriting the record on the strength of an unverified second
message would let an attacker edit history by replaying it.

**Limits are enforced where the bytes arrive.** The body is streamed and
refused the moment it crosses the ceiling; `Content-Length` is an
early-exit hint, since a chunked or understated one would otherwise let
the entire payload into memory before any check ran. The same applies
outbound: upstream responses are read incrementally against a budget
rather than buffered and then measured.

**Ownership is checked even when absent.** The account comparison only ran
when an account was present, which exempted precisely the payloads that
omitted it. Missing installation id, missing or empty account, foreign
owner, mixed-owner repository lists, and an installation bound to a
different scope are all refusals now.
