# Notifications: routing incidents, and delivering them reliably

Drake turns incident lifecycle events into in-app notifications and
outbound webhooks. There is no alert-rule language here — the incidents
already exist ([INCIDENT_LIFECYCLE.md](INCIDENT_LIFECYCLE.md)); a policy
only says *which of them* are routed and *to whom*.

## The flow

```
incident_events (immutable, already committed)
        │
        ▼  planner: bounded batch, leased, idempotent
matching policies ── AND-filtered by project / environment / service
        │            / event type / severity
        ▼
distinct destinations
        ├── in_app_user  → in_app_notifications  → inbox
        └── webhook      → webhook_deliveries    → delivery worker → receiver
```

It is an **outbox**, deliberately. Calling a webhook inside the incident
transaction would let a slow receiver hold a database lock and a failing
one roll back the incident that caused it. So the incident commits first,
and the planner reads committed events afterwards. **A notification
failure never changes an incident.**

## Which events notify

| Event | Notifies |
| --- | --- |
| `opened` | yes, if the policy subscribes (default) |
| `auto_resolved` | yes, if the policy subscribes (default) |
| `acknowledged` | only when the policy explicitly opts in |
| `recovery_started` | never |
| `recovery_interrupted` | never |

The last two are internal progress markers; notifying on them would tell
someone twice about one recovery. They are excluded in the API, in the
planner, and by a database CHECK constraint.

## Policy matching

Every filter is `AND`, and every one is an id or an allowlist membership —
there is no expression to evaluate:

- project — **required**
- environment — optional; must belong to the policy's project
- service — optional; must belong to the policy's project
- event type — from the table above
- severity — `critical` in this sprint
- policy, destination and the link between them must all be enabled

**Overlapping policies do not multiply.** Matching groups on the CANONICAL
target — the recipient identity for in-app, the runtime destination key for
webhooks — so three policies, or three destination rows naming one person
or one endpoint, produce one notification. The database enforces the same
thing under concurrency:

```
UNIQUE (incident_event_id, recipient_identity_id)   -- in_app_notifications
UNIQUE (incident_event_id, destination_key)         -- webhook_deliveries
```

The channel is part of the identity (separate tables), and the
`Idempotency-Key` is derived from the same canonical triple, so a receiver
sees one key per event per endpoint. Which policies matched is recorded on
the row.

### When a policy starts applying

Every policy and every policy→destination binding carries a server-owned
`effective_from`. The planner compares it against the event's immutable
`created_at` — the moment Drake wrote the event down — and routes an event
only when it is at least as new as both.

| Action | `effective_from` |
| --- | --- |
| policy created | now |
| filters changed (environment, service, event types, severity) | reset to now |
| renamed only | unchanged |
| disabled → enabled | reset to now |
| destination attached | now |
| disabled binding re-attached | reset to now |

Consequences worth stating plainly:

- **A new policy never replays history.** No baseline command is required
  for this; correctness does not depend on remembering to run anything.
- **Re-scoping a policy does not capture older unplanned events.** The
  clock restarts with the new rule.
- **Re-enabling does not backfill.** Events that happened while a policy
  was off were, at that moment, not routed by it.
- **A backlog is still delivered.** If the policy did not change and the
  planner was simply behind — or switched off for a day — those events are
  newer than the policy and are routed when it catches up. Retroactivity
  and backlog are different things, and only the first is prevented.

`mark_existing_events_planned` remains available as optional housekeeping
to stop the planner re-reading a large historical backlog, but skipping it
changes nothing an operator would notice.

**Editing a policy affects only future events.** A delivery already
planned also keeps its frozen payload, destination and idempotency key.

## Destinations

Two kinds, and neither accepts an address:

**`in_app_user`** — a Drake identity, selected from the directory. The
recipient must be able to see something inside the project; a user with no
access cannot be added, because a notification would otherwise be a side
channel around scope.

**`webhook`** — an **opaque key** into the operator's runtime registry.
The database stores the key, never a URL, token or header. The API returns
the key and a display name, never a target.

```
DRAKE_NOTIFICATION_WEBHOOKS='{"ops-primary": {
  "url": "https://<operator-controlled-host>/<path>",
  "display_name": "Ops primary",
  "signing_secret_file": "/run/secrets/<name>",
  "timeout_seconds": 10,
  "payload_schema_version": 1
}}'
```

`signing_secret_file` is a **reference**, following the same `*_file`
convention as the Agent CA and GitHub App material. The secret is read at
send time and never becomes a settings value, a column, a log line, an
audit entry, or part of any response.

## Webhook safety

Checked on **every** send, not once at configuration time — DNS answers
change, and the address a hostname resolved to last week is not a promise
about today:

- HTTPS only outside local/test
- no credentials in the URL, no fragment
- refused targets: loopback, link-local (including `169.254.169.254`,
  the cloud metadata endpoint), private, multicast, unspecified, reserved
- a name answering with both public and private addresses is refused as a
  rebinding smell
- redirects are **never followed**; a `3xx` is a terminal failure
- no caller-supplied headers, and a bounded timeout
- the response body is never read into anything Drake keeps

**The validated address is the one that is dialled.** Checking DNS and then
letting the HTTP client resolve the hostname again leaves a window in which
a hostile answer flips public→private between the two — the check would
have proved nothing. So the request goes to the validated IP literal, with:

- the original hostname in the `Host` header, so the receiver routes it
- the original hostname in the TLS SNI extension, so certificate
  verification still happens against the real name (verification is never
  disabled)
- IPv6 answers bracketed correctly, and checked by the same rules
- **every** answer checked, not just the one used: a name that can answer
  with a forbidden address is not a safe name
- the whole check repeated on **every attempt**, so a retry after a DNS
  change is validated again rather than trusting the first result

## The payload

Server-composed, small, and versioned. Headers: `Idempotency-Key`,
`X-Drake-Timestamp`, and `X-Drake-Signature` when a signing secret is
configured.

```json
{
  "schema_version": 1,
  "delivery_id": "...",
  "idempotency_key": "...",
  "event_type": "opened",
  "occurred_at": "...",
  "incident": {
    "id": "...", "state": "open", "severity": "critical",
    "title": "...", "primary_reason": "no_ready_replicas",
    "opened_at": "...", "acknowledged_at": null, "resolved_at": null,
    "url": "<base-url>/incidents/<id>"
  },
  "service": {
    "project_key": "...", "environment_key": "...", "service_key": "...",
    "cluster_ref": "...", "namespace": "...", "workload_name": "..."
  }
}
```

It carries no PromQL, query response, metric label, credential, internal
exception, inventory payload or session material.

### Verifying the signature

```
signature = "v1=" + HMAC_SHA256(secret, timestamp + "." + raw_body)
```

Compare against `X-Drake-Signature` in constant time, using the **raw**
body bytes, and reject a `X-Drake-Timestamp` outside your tolerance. The
timestamp is inside the signed material precisely so a captured request
cannot be replayed forever.

## Delivery semantics

Delivery is **at-least-once**. A crash between sending and recording the
result means the request is repeated — that is why every request carries a
stable `Idempotency-Key`, derived from the incident event and destination.
Receivers must deduplicate on it.

| Response | Outcome |
| --- | --- |
| `2xx` | delivered |
| `408`, `429`, `5xx` | retryable |
| network timeout / connect failure | retryable |
| `3xx` | terminal (redirect refused) |
| other `4xx` | terminal |
| SSRF refusal | terminal |
| destination key no longer registered | `suppressed` |

### Terminal states

Three, and they are final — a terminal delivery is never claimed again and
never moves to another terminal state:

| State | Means |
| --- | --- |
| `delivered` | the receiver accepted it |
| `dead_letter` | Drake tried and stopped: a terminal `4xx`, a redirect, an SSRF refusal, or the retry budget ran out. There is always an attempt row saying which |
| `suppressed` | Drake never tried and never will: the destination key is no longer in the registry. No attempt row, because there was no attempt |

`suppressed` deliberately does **not** become `dead_letter` if the key
returns — "Drake tried and gave up" would send someone looking for a
receiver problem that does not exist.

Retry is exponential with bounded jitter, up to `webhook_max_attempts` (6)
and `webhook_max_elapsed_seconds` (24h), then `dead_letter`. `Retry-After`
is honoured only when it is a small number of seconds (≤300).

Claiming uses `FOR UPDATE SKIP LOCKED` plus a time-bounded lease: two
workers take different rows, and a worker that dies holding one loses it
when the lease expires.

## Enabling it

Both actors are **off by default** and are **not enabled in any production
manifest**:

```
DRAKE_NOTIFICATION_PLANNER_ENABLED=true
DRAKE_NOTIFICATION_PLANNER_INTERVAL_SECONDS=60
DRAKE_NOTIFICATION_PLANNER_BATCH_SIZE=50
DRAKE_WEBHOOK_WORKER_ENABLED=true
DRAKE_WEBHOOK_WORKER_INTERVAL_SECONDS=30
DRAKE_WEBHOOK_WORKER_BATCH_SIZE=20
DRAKE_WEBHOOK_WORKER_CONCURRENCY=4
DRAKE_WEBHOOK_DESTINATION_CONCURRENCY=2
DRAKE_PUBLIC_APP_BASE_URL=https://<your-drake-origin>
```

The two flags are independent: an operator can route notifications into
the in-app inbox without ever letting Drake call an external endpoint.
Each cycle takes its own Redis lease, so multiple replicas are safe.

There is deliberately **no** user-callable "send test notification",
"replay event" or "retry now" endpoint — any of those would let an
authenticated user drive Drake's outbound traffic on cue.

## API and permissions

| Endpoint | Permission |
| --- | --- |
| `GET /v1/notifications` | authenticated (own rows only) |
| `GET /v1/notifications/unread-count` | authenticated (own rows only) |
| `POST /v1/notifications/read` | authenticated + CSRF (own rows only) |
| `GET /v1/notification-policies` | `notification.view` |
| `GET /v1/notification-policies/options` | authenticated |
| `POST /v1/notification-policies` | `notification.manage` + CSRF |
| `POST /v1/notification-policies/{id}` | `notification.manage` + CSRF, `expected_version` |
| `GET /v1/notification-destinations` | `notification.view` |
| `POST /v1/notification-destinations` | `notification.manage` + CSRF |
| `GET /v1/notification-deliveries` | `notification.view` |
| `GET /v1/notification-deliveries/{id}/attempts` | `notification.view` |

The inbox takes **no recipient parameter**: the identity comes from the
session. Reading is not managing — `notification.view` cannot create or
edit a policy. Anything outside scope answers 404.

## Troubleshooting

**Nobody was notified about an incident.**
Check in order: is the planner enabled; does a policy exist for that
project with the event type subscribed; are the policy, destination and
link all enabled; is the destination in the same project. Then look at
`notification_event_plans` for that event — `matched_destinations = 0`
means nothing matched, which is a finished decision, not a pending one.

**Notifications stopped after enabling the feature.**
Events created before the first planner cycle are baselined as planned
with no destinations. That is intended; new events flow normally.

**A delivery is stuck in `retrying`.**
Look at the attempt timeline: `http_503` means the receiver is failing,
`timeout` that it is slow. It progresses to `dead_letter` after the retry
budget. Nothing retries it by hand, by design.

**A delivery went straight to `dead_letter` on the first attempt.**
Either a terminal `4xx`, a redirect, or an SSRF refusal. The attempt's
`error_code` says which — `destination_private_refused` and
`destination_target_refused` mean the hostname resolved somewhere it is
not allowed to reach.

**A delivery is `suppressed`.**
Its destination key is no longer in the runtime registry. Re-add the key,
or leave it: there is nowhere to send it.

**A notification vanished from someone's inbox.**
Their access to that service was revoked. The row is not listed, not
counted, and cannot be marked read (404) — a redacted placeholder would
still answer "an incident exists here you may not see". The row stays in
the database, so restoring the grant makes it visible again, unread.
