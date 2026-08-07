# Sprint 5A — Discovery Report

Written before any Sprint 5A code, from reading the existing tree. It
records the conventions the GitHub integration had to fit into, and the
decisions that followed from them.

## 1. What already existed

| Area | What Drake already does | Consequence for 5A |
|---|---|---|
| App construction | `create_app(settings, oidc_client, telemetry_transport, github_transport)` — every upstream is injectable | The GitHub client takes an injectable `httpx` transport; tests never touch the network |
| Errors | One envelope: `{error: {code, message, correlation_id, retryable, details}}`, built from `HTTPException.detail` | Webhook refusals return one bounded message; the caller learns nothing about which check failed |
| Secrets | `DRAKE_` env prefix and a `*_file` secret-reference pattern (path in config, material on disk) | `github_app_private_key_file` / `github_webhook_secret_file` follow it exactly; no material ever enters settings |
| Audit | `record_audit_event(engine, AuditEventData(...))`, 8 KiB metadata ceiling, redaction validation, DB triggers enforcing append-only | Every GitHub security event is audited through the same path, so the triggers apply unchanged |
| RBAC | 14 permissions, `visible_scope_ids(connection, principal, permission)` with a zero-UUID sentinel so an empty set never means "match all" | Read and manage separate; `integration.manage` gates every write path |
| Not-found | Uniform `HTTPException(404, "not found")` | An unauthorized repository id is indistinguishable from a missing one |
| Migrations | Alembic 0001–0007, helpers `_uuid_pk()` / `_timestamps()` | 0008 is additive only and reversible |
| Web | Design-system primitives, `provider-guard.test.ts` forbidding URLs, `config_ref`, or provider vocabulary in `src/` | The GitHub screen names no endpoint and shows no reference value |

## 2. Decisions this forced

**Identity is the provider's permanent id, not a name.** `full_name`
changes on rename and transfer; GitHub's numeric repository id does not.
Everything keys on `(provider, external_id)` with a unique constraint, so
a rename is an update rather than a second row. ADR-0020 §1.

**The webhook is a signature boundary, not a session boundary.** It has
no cookie, no CSRF token and no principal. Trust is the HMAC over the raw
bytes and nothing else, which is why the body must be read once, bounded,
and verified *before* it is parsed. ADR-0019 §4.

**Replay is a database invariant, not application logic.** A unique
constraint on `delivery_id` decides the winner of a concurrent race;
same-id-same-digest is an idempotent acknowledgement and
same-id-different-digest is a security event, not a retry. ADR-0019 §5.

**Read-only is a permission decision, not a code convention.** The App
requests no write permission at all, so a bug cannot escalate into a
mutation. `administration`, `contents`, `workflows` and `deployments`
write are all forbidden by the requested permission set. See
[the permission matrix](../github-app-permission-matrix.md).

**Missing evidence is UNKNOWN, never PASS.** A permission we were not
granted, a rate limit, a timeout or an unreadable response all produce
UNKNOWN with the reason stated. This mattered in practice: an early
version returned FAIL "unprotected" when branch protection was unreadable
but rulesets came back empty. Absence of evidence is not evidence of
absence, and it is not a policy verdict either.

## 3. Datalake-Platform-GUI

The Sprint 0 discovery recorded a tracked `.env` in this repository. That
finding is a **manual security gate**, and Sprint 5A treats it as
authoritative: the repository is present in the catalog and visible in the
UI, in a `blocked` state with the gate named, and every path that would
touch GitHub on its behalf refuses first. No secret is read, no
installation access is used, no reconciliation runs, and no API call is
made — a test asserts the fake provider recorded **zero** calls.

Closing the gate is an operator decision (review, rotation where needed,
history containment, `.gitignore` remediation, a safe `.env.example`
contract). Nothing in this sprint closes it automatically, and nothing in
the code assumes it is closed.
