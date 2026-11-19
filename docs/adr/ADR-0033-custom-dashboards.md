# ADR-0033: Custom dashboards, built from reads Drake already trusts

**Status:** Accepted
**Extends:** ADR-0011 (states are not zeros), ADR-0015 (query broker), ADR-0029 (OpenTelemetry alongside Prometheus), ADR-0031 (alert rules), ADR-0032 (infrastructure screen)

## Context

Our own team watches the system on Drake. Drake may be sold later, so the
dashboards must look and work like a product. The approved design (the v18
prototype, 2026-11-19) shows one environment of a project at once:
- an overview: four numbers, then requests, speed and errors per hour on one time axis;
- a service table;
- traffic between the services as a node graph;
- what needs attention, each item with its evidence;
- a latency heatmap and an endpoint leaderboard;
- capacity, database work and the make-up of the logs.

The per-service reads answer for one binding at a time (ADR-0029). A
dashboard needs the same numbers for every service of an environment
without one round trip per service, and some numbers only exist across
services: who called whom, and the latency histogram of the whole
environment.

## Decision

### Fixed reads, no free-form queries

Every widget is drawn from a fixed read. No widget carries PromQL or SQL.
Prometheus numbers (capacity) go through the broker as before. Trace and log
numbers go through new fixed ClickHouse statements in
`observability.overview`. Users choose widgets from a catalog; they never
write queries.

### The environment overview read

`GET /v1/service-health/overview?binding_id=…&range=…` takes the binding
ids of the services on the dashboard, like the cross-service signal read.
It answers eight sections:
- `traffic`: requests, errors and p95 per binding per bucket;
- `latency`: the request count per bucket per latency class;
- `codes`: HTTP status codes per binding;
- `routes`: the busiest routes, with p50, p95 and p99;
- `calls`: server spans whose parent is another bound workload's span;
- `databases`: client spans that name a database system, per operation;
- `log_lines`: log lines, errors and warnings per bucket;
- `log_patterns`: the most frequent log shapes.

There are 13 latency classes, each a third of a decade, from 1 ms up. Class
9 starts at 1 s, where a request starts to feel slow. Buckets are the
timeline's own (24 per day), so the dashboard and the service page agree.

### Scope is enforced in SQL, not after

The signal read narrows by cluster and namespace, then drops the groups that
match no authorized binding. That does not work here: the histogram and the
log patterns add up across workloads, so a stranger's rows cannot be dropped
afterwards.

So every statement narrows to the exact
`cluster|namespace|kind|workload` of the authorized bindings, passed as a
typed array parameter.

A call counts only when both its caller and its callee are among them.
Both sides of the join are narrowed before the join. Another team's
workload in the same namespace never contributes a number, and no label in
the SQL text comes from the request.

### The same rules as the other reads

The overview applies the same rules as the per-service reads:
- Health checks and scrapes are not traffic.
- Long-lived connections stay out of latency (#151).
- Trace sections need `traces.view` and log sections `logs.view`.
- A binding the caller may read for neither is absent.
- Each section states its own condition: `not_configured`, `unavailable`
  or `ok`. A store outage is never shown as zero.

### Bounded

A request may carry at most 32 bindings. The widest range has 30 buckets,
and 32 × 30 stays under the reader profile's 1000-row ceiling. The
row-ceiling test reads that ceiling from the deployed configuration.

### What needs attention, found from the same numbers

The prototype's "needs attention" items were analysed by hand. Drake now
finds them itself, from the sections the overview already answered. It
makes no further read (`observability.findings`, pure functions).

There are four detectors, each with conservative thresholds:

| Kind | Fires when | Severity |
|---|---|---|
| `periodic_error_logs` | error-or-worse lines keep coming at a steady rate (≥ 90% of buckets since they first appeared, coefficient of variation ≤ 0.3, at least 6 full buckets) | warning |
| `connection_per_query` | a service opens a new database connection for ≥ 20% of its queries (≥ 100 connects) | warning |
| `slow_call` | one bound service calls another with p95 ≥ 1 s (≥ 10 calls). When the callee also has a `connection_per_query` finding, that is named as the likely cause | warning |
| `client_errors` | ≥ 5% of a service's answers are 4xx (≥ 20) | info when only 401/403, warning otherwise |

- Steadiness is judged from the first error line, not from the window
  start. Before #149, PostgreSQL's FATAL lines had no severity, so the same
  probe failure looked new six hours into the window.
- A detector only speaks when its sections answered `ok`. One that could
  not run is listed in `skipped`, so "no findings" is never claimed for a
  part that could not be read.
- The API carries kinds, bindings and numbers. The screen does the wording.

Checked read-only against production hermes-test on 2026-11-19, it finds
what we found by hand:
- the FATAL lines from auth-db and core-db (`pg_isready` without `-d`);
- the missing connection pool in auth-service;
- the 401s in core-service.

It also found a missing pool in core-service, which the hand analysis
had missed.

### One dashboard per environment, every save a version

Migration 0027 adds two tables, additive only:
- `dashboards`: one row per environment, holding its title and layout;
- `dashboard_versions`: every saved layout, kept.

An environment without a row still has a dashboard. Drake draws the
default layout at version 0, and the first save writes version 1.

A layout is an ordered list of widgets from a fixed catalog of nine. Each
entry has a key, a size (S, M or L) and whether it is hidden.
- A widget carries no query. What it draws is decided by its key, so a layout
  can never read anything the fixed reads do not.
- At least one widget is shown.
- A widget added to the catalog later comes back hidden in older layouts.

Saving and history:
- A save names the version it edited. A save over a newer one is a 409,
  never a silent overwrite.
- Restoring is saving an old layout again, so history only grows.
- There is no delete endpoint. Archiving is a column, and the foreign keys
  are RESTRICT.

Permissions:
- Seeing a dashboard is `environment.view`. Every number on it still needs
  `traces.view` or `logs.view`.
- Saving needs the new `dashboard.manage` at the project. It is granted to
  the Platform Owner template only, as 0026 did for alert rules.
- Every save is audited (`dashboard.save`).

### The screen

`/dashboards` lists every environment with services. `/dashboards/{environment}`
draws the v18 design from the overview read, in nine widgets:
- the overview: four numbers, then requests, speed and errors on one time axis;
- service status;
- traffic between services, as a node graph with response-code rings;
- what needs attention, each finding with its own small chart. A slow
  call's evidence is a donut of where the slowest request's time went;
- the latency heatmap, with totals per row;
- the endpoint leaderboard (most called / slowest);
- capacity, from the infrastructure read (ADR-0032);
- database operations;
- the make-up of the logs.

Behaviour:
- The screen polls once a minute.
- A section that did not answer says so, never draws a zero.
- With `dashboard.manage` you can move, resize and hide widgets, then save
  with a note. A conflicting save is reported, not overwritten. History
  restores a version by saving it again.

## Consequences

- The dashboard costs eight bounded statements per load, not eight per service.
- The calls statement joins spans and is the most expensive read Drake
  makes. Both sides are narrowed to the environment's workloads and the
  window first, and it is bounded to 200 rows.
- A database is only a label taken from what the span says, not another
  binding. Drake does not claim to know which database server it was.
