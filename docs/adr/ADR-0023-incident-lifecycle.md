# ADR-0023: Health-driven incident lifecycle

**Status:** Accepted (Sprint 6)
**Extends:** ADR-0022 (service health), ADR-0015 (query broker)

## Context

ADR-0022 gave Drake a verdict about a service. Nothing remembered it, so
nothing could tell "still broken" from "just broke" — and an observability
plane that cannot answer that is a dashboard.

The obvious next step is user-defined alert rules. We are deliberately not
taking it yet. A rule builder needs a threshold language, an evaluation
engine, silences, and a way to reason about rules that contradict each
other. Every one of those is a place for a rule to be wrong in a way nobody
notices until an outage. So Sprint 6 ships one server-owned rule, applied
consistently, and nothing configurable.

## Decision

### Incidents come from evidence, not from a single reading

An incident opens only when **two consecutive trustworthy** evaluations say
`critical`, and resolves only when two consecutive trustworthy ones say
`healthy`. One sample is a spike; the second is a pattern.

"Trustworthy" is a specific thing, and it is where most of the value is:

| Situation | Effect |
| --- | --- |
| `healthy` / `degraded` / `critical`, complete, live | counts |
| `unknown`, `stale`, `not_configured` | breaks both streaks, opens nothing |
| partial result, or served from last-good | breaks both streaks |
| datasource unavailable, query failed | breaks both streaks |
| binding disabled or unresolved | breaks both streaks |

`degraded` is trustworthy but opens nothing in this sprint. It is a real
measurement, so it breaks a critical streak — a run of criticals
interrupted by a degraded reading is not a run.

The rule this encodes: **Drake failing to measure is never reported as the
service failing.** A Prometheus restart must not page anyone.

### The same evaluation is never counted twice

An evaluation's identity is derived from its `computed_at`, which a cached
or last-good response carries from when it was *originally* computed.
`served_at` is deliberately not an input — it changes on every read, and
using it would turn one observation into an endless stream of new ones,
letting a page refresh open an incident.

### One active incident per binding, enforced in the database

A partial unique index on `state IN ('open','acknowledged')`. Application
logic checks too, but two workers both pass an application check; only one
survives an index. The processor treats losing that race as success —
an incident is already open, which was the goal.

### Acknowledge, but no manual resolve

Acknowledging records that a human has seen it. It does not close the
incident and does not pause monitoring, because the service is still down.

Manual resolve is deliberately absent. With a critical service still
failing, a closed incident would reopen on the next cycle, and the history
would show a flapping lifecycle that describes the operator's clicks rather
than the service. Resolution follows evidence, or it does not happen.

Resolved incidents are immutable. A later failure opens a *new* incident
rather than reviving the old one, so every incident describes one outage.

### Incidents are produced by a runner, never by a read

No GET creates or changes an incident. Otherwise the estate's incident
history would depend on who opened which page, and an unlucky refresh
during an outage would change the record.

The runner is off by default, bounded on every axis (interval floor, batch
size, concurrency, lease TTL), and holds a Redis lease so that multiple
replicas do not evaluate the same estate simultaneously.

### The runner has an identity, not a bypass

The Query Broker authorizes against a `Principal`. Rather than adding a
"system" path around that check — a second authorization path is where
authorization bugs live — the runner acts as a real identity holding
exactly one permission (`telemetry.query`) at the organization root. It
appears in the RBAC screens like any other grant, and it cannot
authenticate: its issuer is a URN no provider can mint a token for, and it
has no local credential.

## Consequences

- A datasource outage produces `unknown` verdicts and no incidents, which
  is the honest answer and the quiet one.
- Incident titles come from a server-owned reason dictionary. There is no
  endpoint that accepts a title, so no user text can reach one.
- History is transitions, not samples: a row is written when the status or
  its reason set changes, so the table stays a record of decisions rather
  than a second copy of the metrics.
- Two operators acknowledging at once produce one acknowledgement and one
  clear conflict message, via `expected_version`.
- The estate's alerting behaviour is currently uniform and unconfigurable.
  That is a real limitation, and the intended next step — per-service
  policy already exists in ADR-0022, so severity and thresholds have a
  natural home when they are needed.

## Alternatives considered

**Open on the first critical.** Rejected: a single scrape gap or a rolling
restart would page someone, and the resulting noise trains people to
ignore incidents.

**Let operators close incidents manually.** Rejected for this sprint: with
the underlying condition unresolved, close-then-reopen produces a timeline
that documents clicking rather than the outage.

**Resolve when a binding is disabled.** Rejected: silence is not recovery.
Closing there would let an outage be hidden behind a configuration change.

**Evaluate inside the read endpoints.** Rejected: it makes incident
history a function of who was looking, and it makes a GET expensive and
non-idempotent.
