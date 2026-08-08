# Alerts, SLOs and incident operations

The division of labour, and Drake's side of it:

```
PrometheusRule  decides when a condition is true
Alertmanager    grouping, dedupe, inhibition, silence, base notification
Drake           business context, incident projection, ownership,
                timeline, SLO visibility, controlled operations
```

Drake is not a second Prometheus and not a second Alertmanager. It does not
re-evaluate conditions, re-group alerts, or decide whether a receiver
should have been called. What it adds is what neither of them has: which
project and service an alert belongs to, who owns it, what happened next,
and how much error budget is left.

## The chain

```
PrometheusRule → Alertmanager → authenticated webhook → alert projection
  → incident correlation → ack / assign / silence → notification planner
  → SLO / error budget → UI
```

## Webhook ingest

```
POST /webhooks/alertmanager/{opaque_integration_key}
Authorization: Bearer <token>
```

Native Alertmanager does not sign its bodies. Rather than accept a
signature header Alertmanager never sends — which any client could forge —
the guarantee is a bearer token over TLS plus a fully idempotent
projection. A signing proxy would be a separate, versioned auth mode; this
sprint does not build one.

The opaque key resolves in server-side settings to exactly one project. An
alert's own `project` label is a claim to CHECK, never a selector to
honour: a mismatch is refused.

**Identity is `(integration, fingerprint)`.** Not `groupKey` — a group is a
notification batch whose membership changes between deliveries, so keying
on it would merge unrelated services and split related ones.

**Every alert in a batch is normalized on its own.** A delivery marked
`resolved` can still carry a firing alert; letting the group status win
would close an incident for a service that is still down.

Idempotence has two layers, both in the database:

| Layer | Key | Stops |
| --- | --- | --- |
| Delivery | `(integration_id, delivery_digest)` | The identical payload being processed twice |
| Event | `(alert_instance_id, dedupe_key)` | A re-serialized retry becoming a second transition |

Projection updates are guarded on `source_event_at`, so a redelivered
`resolved` from an hour ago cannot drag a currently firing alert back.
Provider time and Drake time are separate columns throughout.

## What is never stored

The raw webhook body, the `Authorization` header, `generatorURL`,
`externalURL`, annotation URLs, group label values (the group key is
hashed), and any label outside the allowlist. Labels carrying an email, a
user id, a trace id, a pod name, an instance address or any value with a
URL scheme are dropped at the boundary — an alert label is writable by
anyone who can write a recording rule.

Kept labels: `alertname`, `severity`, `project`, `environment`, `service`,
`cluster`, `namespace`, `team`/`owner_team`, `slo`, `runbook` (a KEY, not a
URL), `component`, `signal`, `tenant_key`, and the burn-window markers.

## Catalog resolution

Resolved server-side, within the integration's project, and fail-closed:

| Outcome | Meaning |
| --- | --- |
| `mapped` | Every label resolved exactly |
| `unmapped` | A label named something the catalog does not have |
| `ambiguous` | One service, several environments, no environment label |

An unmapped alert is kept as integration evidence, opens no incident, and
is visible only to operators holding `integration.manage`. It belongs to no
project, so nobody's project view can show it. Filing it against a guessed
project would send it to the wrong team.

A service label is not required. A genuinely project-level alert resolves
to a project and environment and is not attached to an invented service.

## Severity, priority and what pages

```
critical → P1      medium → P3
high     → P2      info   → P4
```

An unrecognised severity becomes **P3**, never P1: a label typo must not
page someone at 3am, and it must not vanish either.

Only P1 and P2 open an incident, and only when mapped. P3 and P4 are
recorded, filterable and visible — every warning is not a page.

## Incidents

Alert incidents go through the Sprint 6 lifecycle, keyed on
`alert:{integration}:{fingerprint}` in the `correlation_key` column, with a
partial unique index over active incidents. Two concurrent deliveries lose
the race in the database.

**A resolved alert does not close its incident.** Alertmanager stopping is
evidence the condition cleared — not that anyone looked or that the problem
was handled. The incident is marked `mitigated_at` and stays open.

**A reopen after closure gets a new incident, with lineage.** A resolved
incident is immutable (Sprint 6), and reviving one would rewrite a timeline
people have already read. `alert_incident_links` carries the lineage.

An incident no longer requires a workload binding. `binding_id`,
`environment_service_id` and `service_id` are nullable; a project-level
alert or a backup policy protecting a store is a real problem, and
declining to record it because there was no pod was silence, not safety.

An alert labelled `signal: protection` links to an open protection incident
in the same project rather than opening a second one — two witnesses to one
fact should not page the same person twice.

## Acknowledge, assign, silence

Three different facts, three columns, three timeline events:

- **Acknowledge** — a human has seen this. Requires `incident.ack`.
- **Assign** — this person is looking. Requires `incident.assign`, and the
  proposed owner must actually be able to see the incident; assigning to
  someone without access produces an owner who cannot open the page, which
  looks handled and is not.
- **Silence** — Alertmanager will stop notifying, for a bounded time.
  Requires `alert.silence` in that project.

None of them implies the others. A silence does not acknowledge, does not
resolve, does not delete history, and does not make an SLO healthy.

All three are version-checked (409 on a stale version), idempotent on a
safe retry, and audited only when something actually changed.

## Silences

```
POST /v1/alerts/{id}/silence        → 202, state: pending
POST /v1/silences/{id}/expire       → 202, state: cancel_pending
```

The caller supplies an alert, a bounded duration and a reason from a
reviewed vocabulary. The caller never supplies a matcher, a regex, a label,
a URL, a credential or an Alertmanager address.

Matchers are composed server-side from resolved values, always anchored on
the project, always `isRegex: false` — enforced by a CHECK constraint as
well as in code, because a regex matcher is how "silence this alert"
becomes "silence this environment".

`202` and `pending` are deliberate: the request is recorded and audited,
and the worker calls Alertmanager afterwards. **A silence is `active` only
once the provider returned an id.** A failed one is `failed` with a bounded
error code. An operator who believes an alert is suppressed when it is not
will stop watching it.

The outbound call reuses the Sprint 7 webhook boundary: HTTPS only,
credential from a `*_file` reference, target re-validated and pinned on
every attempt, redirects refused, TLS verification not disableable, and the
provider's response body never read into anything Drake keeps.

The browser never talks to Alertmanager.

## SLOs and error budget

Two indicators: `availability` and `latency`. SLI reads go through the
Sprint 5 Query Broker and a curated template key — there is no column an
expression could be stored in, and no parameter through which one arrives.

```
allowed_bad_ratio     = 1 - objective_ratio
observed_bad_ratio    = bad / total
burn_rate             = observed_bad_ratio / allowed_bad_ratio
error_budget_consumed = observed_bad / allowed_bad
```

Everything is a **ratio** — 0.999, never 99.9. A percentage is formatted
once, at the last moment, in the browser.

| State | Means |
| --- | --- |
| `healthy` | Within objective, not burning dangerously |
| `warning` | Burning faster than allowed, or ≥75% of budget spent |
| `critical` | A critical burn level active |
| `exhausted` | The budget for this window is spent |
| `insufficient_data` | `total = 0`. **Not 100%** |
| `stale` | Last-good past its freshness limit |
| `query_failed` | Drake could not read the SLI. Not zero errors |
| `not_configured` | No SLI mapped. Nothing is being measured |

Error budget remaining **may be negative** and is reported as negative:
180% burned is 80% past the objective, and rendering that as "0 left" hides
how far past.

A **100% objective** has no budget, so burn rate is `null` rather than
infinite. Any error at all exhausts it, which is what the objective says.

Measurement methods, stated on the screen rather than implied:
*availability* weights the error ratio by request rate; *latency* counts
p95 samples over a curated threshold (a sample count, not a request count).

### Multi-window burn rate

```
14.4×  long 1h  + short 5m     6×  long 6h  + short 30m
 3×    long 1d  + short 2h     1×  long 3d  + short 6h
```

A level is active **only when both windows exceed the factor**. One window
alone is a spike or a memory, and paging on either would flap.

Drake computes these for the dashboard. The authoritative paging signal is
PrometheusRule → Alertmanager; Drake does not run a second paging engine.

Historical evaluations freeze their objective, definition version and burn
profile, so tightening a target tomorrow cannot rewrite last month.

## Independent base notification

`packages/contracts/alerting/alertmanager-route.v1.yaml` is a reviewable
example, applied to no cluster by this repository. Its load-bearing line is
`continue: true` on Drake's route: without it, matching Drake's route
consumes the alert and the base receiver is never called.

So a critical alert reaches an independent base receiver **and** Drake. If
Drake's webhook is down, unreachable or 500s, base notification is
unaffected. Deleting Drake's receiver changes nothing about paging.

Integration Health reports `base_route_verified` as **`unknown`** and keeps
it there. Whether Alertmanager still notifies independently is a fact about
a config file Drake does not read; claiming `verified` without operator
evidence would be the most dangerous wrong answer this screen could give.

## API and permissions

```
GET  /v1/alerts            GET  /v1/slo
GET  /v1/alerts/summary    GET  /v1/slo/{id}
GET  /v1/alerts/{id}       GET  /v1/slo/{id}/evaluations
GET  /v1/alerts/{id}/events
GET  /v1/silences          GET  /v1/alerting/filters

POST /v1/incidents/{id}/acknowledge
POST /v1/incidents/{id}/assign
POST /v1/alerts/{id}/silence
POST /v1/silences/{id}/expire
```

Alerts need `alert.view` ∩ project access; SLOs need `slo.view` ∩ project
access. Incident visibility remains `environment.view` — that permission
already means exactly "may see this service's operational state", and a
duplicate `incident.view` would be a second path to the same authority.
Scope filtering happens in SQL before any count, summary or page.

No endpoint applies a PrometheusRule, edits an SLO objective, writes an
alert, deletes alert history, or reaches Alertmanager directly.

## Troubleshooting

**Alerts arrive but nothing appears.** Check Integration Health for
`alerts_unmapped` and `alerts_ambiguous`. The alert is stored; it just did
not resolve into the catalog. The rule needs correct `project`,
`environment` and `service` labels.

**A firing alert has no incident.** Either it is P3/P4, or it is not
mapped. Both are shown on the row.

**A silence says pending and stays there.** The silence worker is off
(`silence_worker_enabled`) or Alertmanager is unreachable. It is suppressing
nothing meanwhile, which is why it does not say active.

**An SLO shows `insufficient_data`.** No requests were observed in the
window. That is not a perfect score, and it usually means the scrape target
or the SLI mapping is wrong.

**Compliance disagrees with a Grafana panel.** Check the measurement method
and the `objective_ratio` on the row — a historical evaluation is judged
against the objective in force when it ran, not today's.
