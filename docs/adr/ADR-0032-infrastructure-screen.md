# ADR-0032: The infrastructure screen, read through the broker

**Status:** Accepted
**Extends:** ADR-0015 (query broker), ADR-0029 (OpenTelemetry alongside Prometheus)

## Context

Drake answered "is this service healthy" but not "is this machine
healthy, and who is using it". Operators reached for Datadog's host map for
that. Everything needed is already in the cluster's Prometheus:
- node-exporter for CPU, memory, disk and load;
- kube-state-metrics for capacity, requests, limits, pod phase and restarts;
- cAdvisor for container usage.

Two constraints shaped the design:
- **Prometheus runs on node1, which is already saturated.** The screen must
  not add load that grows with the number of applications.
- **The broker is the only way Drake reads Prometheus** (ADR-0015). Its
  templates, authorization, budgets and cache are why a query cannot become
  a free-text PromQL hole.

## Decision

### Templates, not queries

The screen reads 23 new registry templates:
- 17 at **cluster** scope: per-node series and capacity, and per
  namespace-on-node usage, requests, limits, running pods and 24-hour
  restarts;
- 6 at a new **namespace** scope: per-pod values inside one namespace.

Every template groups by fixed labels (`node`; `namespace, node`; `pod`),
and the registry refuses any other output label.

Requests, limits and pod counts only count **Running** pods. Joined with
`kube_pod_status_phase`, completed Jobs would otherwise add their requests
to a node's total, which produced 116% requested CPU in the first draft.

### A namespace scope bound to an inventory row

A namespace drill-down names the inventory row of a `Namespace` the cluster
agent reported. The broker resolves `cluster_ref` and the namespace name
from that row and its cluster. The caller supplies an id, never a label
value. Authorization and the provider integration are the cluster's own.
The scope is internal: the public query endpoint does not accept it.

### A fixed query count and a shared, stale-while-refresh snapshot

- **Fixed query count.** A snapshot is 17 queries whatever the number of
  applications. Pods are fetched only when someone opens a namespace (6
  queries).
- **Bounded concurrency.** Queries run two at a time, so one viewer holds at
  most two of the broker's per-principal leases.
- **Step chosen to fit the budget.** The 24-hour step is the finest one that
  keeps the response inside the broker's point budget (`step_for`).
- **Stale-while-refresh.** The assembled snapshot is cached per cluster for
  60 seconds. After that it is served as it is while one background refresh
  runs under a Redis lock, so the screen opens at once and many viewers
  cause one refresh.

Measured against production before release: each query took 2–353 ms and
returned at most 29 series.

### Who may see what

- `cluster.view` decides which clusters a caller can open. Anything else is
  a uniform 404.
- `telemetry.query` on the cluster is checked by the broker. A cluster
  viewer without it gets an explicit `denied` state, never zeros.
- A Drake project is named, and linked, only if the viewer can see the
  environment that claims the namespace. Otherwise the namespace is shown
  as unclaimed. The stored snapshot is re-filtered per caller.
- Namespaces no project claims are grouped by what they are: Kubernetes,
  monitoring, Drake. An unknown namespace stays on its own, never hidden
  inside a bucket.

## Consequences

- One cluster can show at most 200 namespace-on-node pairs, the broker's
  series ceiling. Beyond that the namespace queries are refused and the
  snapshot reports `partial`. Today's cluster has 26.
- "Now" values are the last point of a 20-minute rate window, not an
  instantaneous reading.
- Disk forecasts come from the 24-hour slope, and the screen says so.
