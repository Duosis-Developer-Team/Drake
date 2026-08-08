# ADR-0024: Notification routing and reliable delivery

**Status:** Accepted (Sprint 7)
**Extends:** ADR-0023 (incident lifecycle)

## Context

ADR-0023 produced an immutable incident timeline. Nothing read it, so an
incident opened at 3am reached whoever happened to be looking at a screen.

Notifications are where an observability plane usually acquires two new
failure modes at once: it starts calling arbitrary endpoints on behalf of
its users, and it starts holding a queue that can lose or duplicate the
thing it exists to deliver. Both decisions below are about not acquiring
those.

## Decision

### An outbox, not a call inside the transaction

The incident commits first. A planner reads committed events afterwards
and writes delivery rows in its own transaction; a worker sends them
later.

Calling a webhook inside the incident transaction would mean a slow
receiver holds a database lock while a service is down, and a failing one
rolls back the incident that caused it. The observability system would
then be least reliable exactly when it matters. **Notification failure can
never change an incident** — and the tests assert that directly.

### Every event is planned exactly once, including "nothing matched"

A row in `notification_event_plans` records that an event was considered.
Without it, an event with no matching policy would be rescanned on every
cycle forever, and the planner's cost would grow with history rather than
with traffic.

Duplicate suppression is structural — see the canonical-recipient section
below for where the constraints actually sit. Two planners racing produce
one row, and that does not depend on timing.

### A policy applies from when it was configured

Freezing a planned delivery's payload was not enough: an event that had not
been planned yet would still be matched by a policy created — or re-scoped
— afterwards. Policies and their destination bindings therefore carry an
`effective_from`, compared against the event's immutable `created_at`.

The distinction that matters: **retroactivity is prevented, backlog is
not**. A scan window would have dropped both, and losing the backlog is
exactly the failure an outbox exists to avoid.

### Deduplication is on the canonical recipient, not the row

Uniqueness on `(event, destination_row)` prevents duplicates per row, which
is not what a person on the receiving end experiences. Two rows naming one
user, or two rows pointing at one webhook key, would each produce their own
notification. The constraints now sit on the canonical target — recipient
identity for in-app, runtime destination key for webhooks — and the
idempotency key is derived from the same triple.

### The validated address is the one that is dialled

Validating DNS and then handing the hostname to an HTTP client that
resolves it again is a check that proves nothing: a hostile answer can flip
public→private in between. Requests are sent to the validated IP literal
with the hostname preserved in the `Host` header and the TLS SNI extension,
so certificate verification is unchanged and only the socket target is
pinned. Every answer is checked, not just the chosen one, and the whole
check runs again on every attempt.

### A destination is a key, never an address

Policies reference an opaque key; the URL lives only in the operator's
settings, alongside a `signing_secret_file` reference. There is no column,
no request field and no response field anywhere in Drake that carries a
webhook URL, header or token.

This is the whole SSRF story. A system where users can type a URL needs an
allowlist, a resolver check, a redirect policy and a rebinding defence — and
one gap in any of them is a request from Drake's network to somewhere the
user chose. A system where users pick from a registry an operator wrote has
none of that surface. The resolver checks still exist (targets are
re-validated on every send, because DNS changes), but they are defence in
depth rather than the only thing standing between a user and the metadata
endpoint.

### At-least-once, stated plainly

Exactly-once delivery to a third-party HTTP endpoint does not exist without
a distributed transaction with the receiver. Rather than implying
otherwise, Drake sends a stable `Idempotency-Key` derived from the incident
event, the channel and the canonical target, documents at-least-once, and tells receivers to
deduplicate on it. Retries send byte-identical payloads, so the key is
meaningful.

The payload is frozen at plan time for the same reason: editing a policy
must never rewrite a delivery that was already scheduled.

### Bounded retry, then a dead letter

Six attempts, exponential with jitter, capped in both count and elapsed
time. `Retry-After` is honoured only when small. A retry loop with no
ceiling does not deliver anything extra; it just converts a receiver's
outage into a permanent backlog that nobody looks at.

### The inbox belongs to its recipient, and scope keeps applying

No endpoint takes a recipient parameter — the identity comes from the
session, so there is nothing to tamper with.

A notification whose incident the reader may no longer see disappears from
their inbox: not listed, not counted, and not addressable by mark-read
(404, the same answer an unknown id gives). The row stays in the database,
so history is not rewritten and restoring the grant restores the
notification, unread, exactly as it was.

Returning it as a redacted placeholder was the first implementation and was
rejected on review: a placeholder still answers "an incident exists here
that you may not see", which is precisely the enumeration a scope filter
exists to prevent.

## Consequences

- Enabling notifications does not replay history, and that guarantee is
  structural: a policy only routes events recorded at or after its
  `effective_from`. No baseline command has to be remembered.
- Routing is uniform and unconfigurable beyond project/environment/service
  and event type. That is a real limitation, and deliberate: a rule builder
  is a language, and a language needs a semantics nobody has agreed yet.
- Both actors are off by default and independent, so an operator can route
  to the in-app inbox without Drake ever making an outbound call.
- Every message a person reads is composed by the server from reason codes
  and catalog identifiers. There is no endpoint that accepts a title, body
  or template, so a notification cannot carry another user's text.

## Alternatives considered

**Let users configure webhook URLs.** Rejected: it makes Drake a
general-purpose HTTP client operated by whoever can edit a policy, and no
allowlist survives contact with DNS rebinding indefinitely.

**Send inside the incident transaction.** Rejected: it couples the
correctness of the incident record to the availability of a third party.

**Claim exactly-once delivery.** Rejected as untrue. Publishing the
idempotency key and documenting at-least-once lets receivers do the one
thing that actually works.

**Delete notifications when access is revoked.** Rejected: it rewrites
what happened. The row is kept and simply stops being visible — not
listed, not counted, not addressable — so restoring the grant restores the
notification exactly as it was.

**Return revoked notifications as redacted placeholders.** Rejected after
review: a placeholder still answers "an incident exists here that you may
not see", which is the enumeration the scope filter exists to prevent.
