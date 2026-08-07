# Sprint 5A — Security Report

Scope: GitHub App credentials, the webhook trust boundary, least-privilege
permissions, repository onboarding, and the read-only policy engine.

## 1. Credential handling

Three credential layers, none of which ever becomes a value in
configuration, a field in an API response, a column in the database, or a
string in a log line.

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

Startup is fail-closed. With the feature enabled and a reference missing
or unreadable, `validate_runtime_security` refuses to start rather than
running in a degraded, silently unauthenticated state. With the feature
disabled the routes return 404 and the UI states `NOT_CONFIGURED`,
listing which operator inputs are missing — by name, never by value.

## 2. Webhook trust boundary

The endpoint belongs to no user session: no cookie, no CSRF token, no
principal. Trust is the HMAC over the raw bytes and nothing else. The
order is the security contract:

1. read the raw body **once**, under a hard byte ceiling (declared
   `Content-Length` is rejected before reading, then the actual length);
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

**Replay.** A unique constraint on `delivery_id` is the arbiter, so a
concurrent race is settled by the database and exactly one caller wins
(asserted by a concurrency test). Same id with the same digest is an
idempotent acknowledgement. Same id with a **different** digest is a
security violation: 409, refused, and audited with the reason and no
payload content. Only a bounded envelope of identity fields is stored —
never the raw payload — and the envelope drops every field not explicitly
chosen (proven against a payload carrying an unrelated `secret_field` and
an email address).

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

## 8. Deliberately still open

The Sprint 3 production ingress `/v1` requirement remains open and is
**not** closed by this sprint. Production activation of the GitHub App is a
separate CTO and operator decision that inherits it.
