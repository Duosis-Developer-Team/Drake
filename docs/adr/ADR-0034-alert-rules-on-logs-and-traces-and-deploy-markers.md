# ADR-0034: Alert rules on logs and traces, and deploy markers on the charts

**Status:** Accepted
**Extends:** ADR-0031 (alert rules), ADR-0029 (logs and traces), ADR-0033 (dashboards)

## Context

Under ADR-0031, alert rules are judged on the incident runner's health verdict.
That verdict carries metric signals: error ratio, p95, request rate, restarts,
CPU, memory and replicas. Two common requests were out of reach:

- "Tell me when auth-service writes a FATAL line."
- "Tell me when an endpoint takes longer than a second."

ADR-0031 left log-based alerts for later, because they need their own source of
samples. The logs and the traces are already in the store, so the runner can
read them.

The charts also had no way to answer "did this start with a deploy?". Drake
records every rollout (`/v1/deployments`). The service health timeline marks
them on its rail, but the dashboard charts did not.

## Decision

### Four signals, read from the store once per page of bindings

Each cycle, the runner reads these values for every binding on the page it is
about to judge (`alert_rules/telemetry.py`). Each one covers the last five
minutes:

| Signal | What it counts |
|---|---|
| `error_logs` | Log lines at ERROR (severity number ≥ 17) or above |
| `fatal_logs` | Log lines at FATAL (≥ 21) |
| `slow_requests` | Requests (server spans) of one second or more |
| `slowest_route_p95_seconds` | The p95 of the slowest route that had at least 5 requests |

Requests are counted as in every other read: health checks, scrapes and
long-lived connections are left out (observability.health_checks).

A page takes two statements in all, one on logs and one on traces, whatever its
size. They narrow in SQL to the exact (cluster, namespace, kind, workload) of
those bindings, the same way the overview read does (ADR-0033). At the
runner's default page of 25 bindings and a 60 s interval, that is about two
small, five-minute-wide statements a minute. Prometheus gets no new query.

The values are stored in four new nullable columns on `service_health_samples`
(migration 0028), next to the metric signals. Live judgement, the rule
history and the "would have fired N times" preview therefore all read the
same samples through the same `step`, unchanged from ADR-0031.

### Unknown is not zero

- If the store does not answer, every value it would have supplied is
  unknown (`NULL`). A missing value moves no rule, so a store outage neither
  opens nor closes anything.
- If a binding had no rows in an answered window, it counts 0 lines and 0 slow
  requests. That is a real observation. Its slowest-route p95 is unknown,
  because there is no route to measure.
- A switch that is off (logs or traces disabled) gives unknown for its signals.

### Old metric evidence does not hold back fresh logs

When the health verdict is served from the last-good cache, ADR-0031 records
nothing: old evidence is not a new observation. That still holds for the metric
signals. If the store answered this cycle, the runner records a sample at the
current time that holds only the log and trace values. Rules on those signals
move; rules on metrics do not.

### Why five minutes, sampled every minute

Every line is seen in five consecutive samples, so ingestion delay cannot hide
it. A rule such as "fatal lines above 0, at once" fires within a minute and
goes back to normal five minutes after the last such line. Each signal is
labelled with its window ("Fatal log lines (5 min)"), so the threshold reads
the way it is judged.

### Deploy markers

The dashboard asks `/v1/deployments` for the environment's rollouts over the
last 24 hours, its widest range. The same deployment scope and permissions
apply. Each rollout is shown as:

- a pin on a "Deploys" rail under the overview lanes;
- a dashed line through those lanes;
- a pin and line in the opened service's own charts.

A rollout whose health comparison regressed is shown in red. Hovering a pin
shows the service, the time, the commit, the rollout state and the health
verdict. Clicking it opens that service in the dashboard. If the deployment
read fails, the charts draw without markers.

## Consequences

- Log and trace rules use the screens, severities, mutes, incidents and
  notification routing that already exist.
- A rule on logs cannot react faster than the runner interval (60 s in prod).
  It cannot match text yet: it counts by severity. Text-pattern rules are a
  later decision, and they need per-rule statements.
- `slowest_route_p95_seconds` is judged per service, not per named route:
  "any endpoint of this service". A rule pinned to one route would also need a
  per-rule statement.
- Additive only: four nullable columns and a wider check on `alert_rules.signal`.
  No existing row is read or rewritten.
