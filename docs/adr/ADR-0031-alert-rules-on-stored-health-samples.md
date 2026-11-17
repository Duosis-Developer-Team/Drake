# ADR-0031: Alert rules, judged on stored health samples

**Status:** Accepted
**Extends:** ADR-0023 (incident lifecycle), ADR-0024 (notification delivery)

## Context

Up to now Drake opened incidents in two ways. The health engine opened one when
a service's verdict went critical, and Alertmanager could deliver one. Nobody
could say "page me when checkout's error rate is above 2% for 5 minutes".
Datadog monitors do this, and it was the first gap people asked about.

The incident runner already works out every active binding's error ratio,
latency, request rate, restarts, CPU, memory and replica counts once a cycle.
Prometheus load is the cost we have to avoid adding: node1 is saturated, and
the runner's query budget is already used in full (ADR-0023).

## Decision

### Rules are judged on the runner's own verdict, never on a new query

Each runner cycle writes one `service_health_samples` row per binding, holding
the numbers it just measured. In the same transaction, every enabled rule that
covers the binding takes one step of a small state machine
(`alert_rules/model.py`):

- `ok` → `pending` on the first breach.
- `pending` → `firing` once the breach has held for `for_seconds`. This
  records a `fired` event.
- `firing` → `ok` on the first sample that is not a breach. This records a
  `resolved` event.

A missing value changes nothing. No data is not a recovery, and it is not a
breach. A verdict served from the last-good cache records no sample and moves
no rule: old evidence is not a new observation.

A rule therefore costs no provider query. Its resolution is the runner's
interval.

### The same function decides live and in the preview

The screen's "this rule would have fired N times in the last 24 hours" replays
the stored samples through the same `step` (`replay`). A preview is an honest
forecast of what the runner would have done, not an approximation of it.
Samples are kept for 8 days.

### Severity decides whether a rule pages

- A **critical** rule that fires opens an ordinary incident: source `rule`,
  `correlation_key` `rule:<rule>:<binding>`. When the rule resolves, it
  resolves that incident with `resolution_source` `rule_resolved`. The
  incident's `opened` and `auto_resolved` events go through the existing
  notification planner, so policies, the inbox, webhooks and e-mail all work
  unchanged.
- A **warning** rule records its history but opens nothing.
- A **muted** rule (for up to 24 hours) is still judged and still keeps its
  history, but it opens no incident.

The partial unique index on open incidents' `correlation_key` means one rule
can never hold two open incidents for one binding.

### Scope and permissions

A rule targets a project, one environment of it, or one service in one
environment. A service rule must name its environment. `alert.view` at the
project lets someone read its rules. The new `alert_rule.manage` lets someone
write them, and it is granted to Platform Owner only. Outside the caller's
reach, every rule answers 404. Every write records its audit row in the same
transaction.

The target is fixed once a rule exists. Changing the condition resets any
breach that was still pending, but leaves a firing state alone until the next
sample judges it.

## Consequences

- Rules add one insert per binding per cycle, plus one state upsert per
  covering rule. They add no Prometheus query.
- A rule cannot react faster than the runner interval, which is 60 s in prod.
- If rule evaluation fails, it is logged and skipped. It never stops the
  binding's own health-incident lifecycle.
- Log-based alerts are a later decision. They will need their own sample
  source.
