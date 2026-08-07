# Sprint 5A — Security Report

Scope: GitHub App credentials, the webhook trust boundary, least-privilege
permissions, repository onboarding, and the read-only policy engine.

## 1. Credential handling

Three credential layers. Material is of course loaded into process memory
— a key that is never read cannot sign anything — but it never becomes a
value in configuration, a field in an API response, a column in the
database, an entry in the audit log, or a string in a log line. That is
the claim, and it is the one the tests check.

| Material | Where it lives | How it is referenced | Lifetime |
|---|---|---|---|
| App private key (PEM) | Operator's secret store, on disk, `0600` | `DRAKE_GITHUB_APP_PRIVATE_KEY_FILE` — a **path** | Until rotated |
| App JWT | Process memory | Minted per request-batch | ≤ 10 minutes (GitHub's ceiling) |
| Installation token | Process memory, per-installation cache | Never persisted | GitHub's expiry, minus a refresh buffer |
| Webhook secret | Operator's secret store, on disk, `0600` | `DRAKE_GITHUB_WEBHOOK_SECRET_FILE` — a **path** | Until rotated |

`AppJwt` and `InstallationToken` carry redacted `__repr__` implementations,
so a credential cannot reach a traceback, a log line, or an error message
by accident. The E2E asserts positively on the other side of the boundary:
no response body and no rendered page contains a PEM header, a `ghs_`
token, a JWT shape, or the webhook secret.

Startup is fail-closed, and it verifies the credentials rather than the
configuration strings. When the feature is enabled — in **any**
environment, including local — startup opens both references and proves
they are usable: the private key must exist, be readable, parse as an
unencrypted PEM, and be an RSA key (GitHub signs RS256); the webhook
secret must exist, be readable, and be non-empty. The JWT TTL (1–600 s),
the token refresh buffer, and the webhook body limit are range-checked,
and a production-like environment additionally requires an HTTPS API URL.
A broken reference is a refusal to start, not something the first webhook
discovers.

Refusals name what is wrong and never quote file contents, so they are
safe to log; a test asserts that a PEM-shaped file's contents never appear
in the exception or its cause. With the feature disabled, no secret file
is opened at all, the routes return 404, and the UI states
`NOT_CONFIGURED`, listing which operator inputs are missing — by name,
never by value.

## 2. Webhook trust boundary

The endpoint belongs to no user session: no cookie, no CSRF token, no
principal. Trust is the HMAC over the raw bytes and nothing else. The
order is the security contract:

1. **stream** the raw body once and refuse the moment it crosses the byte
   ceiling, so at most one chunk beyond the limit is ever held. A declared
   `Content-Length` is an early-exit hint only — never the boundary, since
   a chunked or understated one would otherwise let the whole payload
   through;
2. require the delivery, event and signature headers;
3. HMAC-SHA256 over those exact bytes;
4. constant-time comparison (`hmac.compare_digest`, asserted by a test
   that spies on the call);
5. **only now** parse the JSON;
6. validate the delivery-id format;
7. check the installation/owner relationship against the expected org;
8. apply the event allowlist;
9. claim the delivery id;
10. do idempotent domain work.

Nothing before step 5 trusts the payload's structure, so a hostile body
cannot reach the parser without a valid signature. Every refusal returns
one bounded message — the caller cannot learn which check failed.

**Durability.** The claim and the durable work item commit in one
transaction, and the endpoint acknowledges only after that commit. The
delivery row starts `pending` and is closed out only by a transaction that
writes the domain work and the `processed` flag together. A redelivery of
a `pending` row runs it rather than acking it; a drain worker recovers
rows GitHub will not redeliver forever; attempts are counted in their own
transaction so a poison delivery dead-letters and is audited instead of
spinning.

**Replay.** A unique constraint on `delivery_id` is the arbiter, so a
concurrent race is settled by the database and exactly one caller wins
(asserted by a concurrency test). Same id with the same digest, already
finished, is an idempotent acknowledgement. Same id with a **different**
digest is a security violation: 409, refused, audited with the reason and
no payload content — and the original row is left untouched, because it is
the record of what actually happened. Only a bounded envelope of identity
fields is stored, never the raw payload; the envelope drops every field
not explicitly chosen (proven against a payload carrying an unrelated
`secret_field` and an email address), and it is fitted to a byte budget
that provably satisfies the column constraint. An over-budget payload is
stored as explicitly truncated, with its observed count, flagged for
installation-level reconciliation rather than persisted as a partial list
that reads like the whole truth.

## 3. Least privilege

The App requests **read-only permissions only**. Because no write
permission is granted, a bug cannot escalate into a repository mutation —
GitHub itself would refuse. Derived from the official documentation and
recorded endpoint-by-endpoint in
[the permission matrix](../github-app-permission-matrix.md).

Explicitly **not** requested: `administration: write`, `contents: write`,
`workflows: write`, `deployments: write`. Explicitly not performed: branch
protection or ruleset mutation, secret or variable creation, workflow
dispatch, deployment creation, pull request creation or merge, check or
status writes, and any access to a repository where the App is not
installed.

Granted-narrower-than-requested is handled honestly: the affected rules
return UNKNOWN naming the missing permission, and the repository moves to
DEGRADED or BLOCKED. Permissions are never escalated, and a missing
permission never becomes a PASS.

Installation tokens are requested for the target repository's permanent id
and only the read permissions an evaluation needs, and they are cached
under that **scope** rather than under the installation id. Keying on the
id alone would let a token minted for one repository answer for another,
or a metadata-only token satisfy a caller that asked for more.

## 4. Onboarding state and access loss

Repositories are keyed on GitHub's permanent numeric id, so a rename or
transfer updates one row rather than creating a second. States are
`DISCOVERED / VALIDATING / READY / BLOCKED / DEGRADED / DISABLED`, with
one module owning every transition and one function owning the state
column.

Losing access is **soft state**: the repository moves to DISABLED and its
history is preserved. Nothing is deleted, so an access change cannot be
used to erase evidence.

The derivation is deliberately fail-closed in precedence order: an open
manual security gate outranks everything; then lost access; then an error
after a successful reconcile is DEGRADED (a transient failure must not
look like a policy decision); then READY once reconciled.

## 5. Policy engine

Read-only and dry-run only. Verdicts are `PASS / WARN / FAIL / UNKNOWN`,
each with a stable rule id, severity, evidence, expected-vs-observed, a
blocking flag and secret-free remediation text.

The central honesty rule: **a missing permission, a rate limit, a timeout
or an unreadable response is UNKNOWN or BLOCKED — never PASS.** An early
version violated this by reporting FAIL "unprotected" when protection was
unreadable and rulesets happened to be empty; absence of evidence is not
evidence of absence. Drake's own eight required check names are never
imposed on another repository.

Two further corrections came out of the CTO review. Ruleset evidence now
comes from `GET /repos/{owner}/{repo}/rules/branches/{branch}` — the rules
actually in effect on the branch — because the ruleset *list* endpoint
returns summaries with no `rules` member, so an entry there proves a
ruleset exists but says nothing about what it enforces or whether it
covers the default branch. And an aggregate that spans several objects
(production environments especially) can only be a PASS if every one of
them was readable: one unreadable member makes the verdict UNKNOWN with
the per-object reason recorded, while a known violation still outranks an
unknown. Pagination follows the same rule — reaching the page cap with a
full final page is an explicit error, never a short answer that a rule
could read as "nothing found".

## 6. Authorization surface

Read and manage are separate permissions, resolved per scope through
`visible_scope_ids` with the zero-UUID sentinel that keeps an empty set
from degenerating into "match all". An unauthorized repository id returns
the same uniform 404 as a missing one, so identifiers cannot be probed.
IDOR is tested directly. The webhook is deliberately outside this system
because it trusts a signature, not a session.

## 7. Datalake-Platform-GUI — manual security gate (OPEN)

This repository is **closed to real onboarding** for the whole sprint. It
appears in the catalog and the UI in a `blocked` state naming the gate, and
every path that would reach GitHub on its behalf refuses first — a test
asserts the fake provider recorded **zero** calls, so the refusal happens
before the network, not after.

No secret was read, no installation access was used, no reconciliation ran,
and no live API query was made. The gate is not closed and nothing in the
code assumes it is. Closing it is an authorized-operator decision.

## 7b. Recovery, lifecycle, and readiness (fix gate 2)

Stranded deliveries are finished by a lifespan-owned worker that claims
rows with `FOR UPDATE SKIP LOCKED`, so several instances never process the
same delivery and a crash releases its locks with its connection. A
dead-lettered delivery is terminal: the ceiling cannot be walked past by
pressing GitHub's redeliver button, and the endpoint says `failed` rather
than reporting work it did not do.

Each (event, action) pair maps to an explicit plan. An action outside the
allowlist performs no domain mutation — treating an unrecognised action as
"active" is how a future GitHub action would silently re-enable something.
Suspension and uninstall act on the rows we hold rather than on whatever
the payload happened to list, so an uninstall cannot leave repositories
sitting in an accessible state.

A truncated envelope records durable installation-level intent in the same
transaction as the delivery, never drives a removal from a partial list,
and is surfaced in the UI as reconciliation-required until the full
membership has been re-derived. A repository transferred out of the
organization becomes an access loss rather than a stale accessible row.

Readiness is separated from compliance: `BLOCKED` for a gate or a missing
required grant, `DEGRADED` for partial evidence, `READY` only for a
complete and current projection. A repository read completely and failing
policy is `READY` with a `FAIL` verdict — a real answer. `READY` on
partial evidence is not reachable.

The Datalake gate holds through the new paths too: installation-level
reconciliation makes no call for a gated repository, and its blocked state
survives an access observation rather than being downgraded to disabled.

## 7c. State precedence and provider identity (fix gate 3)

One ordering decides every repository's state — security gate, then
installation deleted, then installation suspended, then access removed,
then incomplete evidence, then accessible — and every path derives through
it. A weaker observation cannot override a stronger reason, which is what
stops a rename from restoring access under a suspended App or reopening a
manual security gate.

Provider responses are verified against the identity they were requested
for. The permanent id must be an integer and must match; the owner must be
the expected organization; the installation id and account must match. A
mismatch writes nothing, is audited once, and leaves the repository
blocked with an identity conflict rather than binding a different object
to our row. A repository transferred out becomes a soft access loss.

The Datalake gate is re-derived from what the provider actually returned,
so a repository renamed into the gated name is blocked before any policy
subresource is read — and a rename away from it does not close the gate.
A gate may be opened by an observation and closed only by an operator.

Installation reconciliation carries its scope and verifies it against the
persisted installation; the root fallback is gone, and a composite
foreign key makes the database enforce that a repository's scope is its
installation's scope. Membership sync uses a Metadata-only token,
validates what was granted, and treats a documented 404 as an uninstall
while a 403, rate limit or timeout is never mistaken for one.

Reconciliation jobs are claimed with a durable, expiring lease rather than
a row lock that is released at claim-commit — two workers could otherwise
both have called GitHub for the same job.

## 8. Deliberately still open

The Sprint 3 production ingress `/v1` requirement remains open and is
**not** closed by this sprint. Production activation of the GitHub App is a
separate CTO and operator decision that inherits it.
