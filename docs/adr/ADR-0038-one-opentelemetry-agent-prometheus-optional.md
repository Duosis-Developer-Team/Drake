# ADR-0038 — One OpenTelemetry agent; Prometheus becomes optional

Status: accepted (2026-11-23), phase 1 of 4
Builds on: ADR-0029 (OpenTelemetry logs and traces), ADR-0037 (Tracesis
Agent: one install, the data stays in the cluster).

## Context

Tracesis collects logs and traces with OpenTelemetry and metrics with
Prometheus. ADR-0037 packages all three as one chart, but they remain two
collection systems with two stores, two query languages (SQL and PromQL) and
two sets of permissions. The goal is the product people expect from Datadog
or New Relic, with less to run: install the Tracesis Agent once and
everything is collected, by one agent, into one store, and Tracesis manages
the agent afterwards.

OpenTelemetry allows this. It is Apache 2.0: we may build, change,
redistribute and sell our own distribution under our own name, as Splunk,
AWS (ADOT), Grafana (Alloy) and Elastic (EDOT) do. We keep its `LICENSE` and
`NOTICE` files, say "built on OpenTelemetry", and do not use its name or logo
as our product's (ADR-0037 §6).

The collector can collect the metrics we take from Prometheus today:

| Today (Prometheus) | OpenTelemetry |
|---|---|
| cAdvisor and kubelet: pod and container CPU, memory, file system, network | `kubeletstats` |
| kube-state-metrics: replicas, restarts, pod phase, HPA, jobs | `k8s_cluster` |
| node-exporter: node CPU, memory, disk | `kubeletstats` (node) now; `hostmetrics` later |
| scraping an application's `/metrics` | the `prometheus` receiver: Prometheus's scrape engine, embedded |
| request rate, errors, latency per service | the `spanmetrics` connector, from traces |

## Decision

Tracesis moves to one OpenTelemetry agent, in four phases. No phase deletes
data and no phase turns Prometheus off. Prometheus becomes optional when the
numbers prove it can be.

### Phase 1 (this change): metrics into ClickHouse, next to Prometheus

- **The new collector.** A separate `drake-otel-metrics` Deployment, one
  replica, `metrics.enabled` (off by default) and pinned by `nodeSelector`.
  It collects:
  - `k8s_cluster`: cluster state;
  - `kubeletstats` for **every node**, discovered through `k8s_observer` and
    `receiver_creator` and read from each node's kubelet on its host IP;
  - only the metric names Tracesis reads (an allowlist), and only for the
    namespaces the log agent collects.
- **Where it writes.** Straight into ClickHouse (`otel_metrics_*`, the tables
  the external ingest already created), through its own bounded queue.
- **What it does not touch.** The log agent and the gateway are unchanged:
  - the metrics collector shares no process, queue or CPU limit with log and
    trace collection;
  - nothing is added to the control-plane node, which is saturated.
- **One replica, by rule.** Two replicas would double every cluster-level
  value. The template refuses any other count.
- **Prometheus.** It keeps running, unchanged. Tracesis still reads it.
- **Our own build.** `tracesis-collector` gains the components phase 1
  needs, so the product chart can switch metrics on: `kubeletstats`,
  `k8s_cluster`, `receiver_creator`, `k8s_observer`.

### Phase 2: the side-by-side comparison

A read-only comparison puts the same signal from both stores next to each
other, per service and per hour:

- CPU and memory per workload;
- restarts;
- desired and ready replicas.

The switch is allowed only where the two agree within a stated tolerance.
The report itself is the evidence.

### Phase 3: Tracesis reads metrics from ClickHouse

The health engine, the infrastructure screen and the SLOs gain a ClickHouse
metrics reader next to the PromQL one, chosen per cluster. This is the
`telemetry_location` idea of ADR-0037, applied to metrics:

- **New clusters:** start on OpenTelemetry only.
- **Prometheus:** stays a supported option, for a customer who already runs
  it or wants PromQL.

#### Phase 3, first step (2026-11-23)

- **Where the switch sits.** In the broker, at the one call that asked
  Prometheus (`adapter.query_range`): for a template and cluster the
  settings name, the same Prometheus-shaped matrix is computed from
  ClickHouse (`telemetry/otel_metrics.py`). Normalization, envelopes, caches
  and the health engine are unchanged.
- **The switch.** `otel_metrics_clusters` and `otel_metrics_templates` (chart:
  `telemetry.otelMetrics`), both empty by default; emptying either switches
  back.
- **Answers are kept apart.**
  - The cache identity carries the source, so an OpenTelemetry answer and a
    Prometheus one never share an entry.
  - The envelope says which one answered (`source_type`).
  - A ClickHouse failure serves last-good like a Prometheus failure. It is
    not recorded against the Prometheus integration's health.
- **The first templates.**
  - `workload.replicas-desired.v1` and `workload.replicas-ready.v1`:
    against Prometheus in prod, 31 of 31 points were equal for a
    Deployment and a StatefulSet.
  - `workload.restarts-delta.v1`: needs the workload on each pod's
    metrics, so the metrics collector now runs `k8sattributes`, as the log
    agent does. That is the OpenTelemetry form of Prometheus's
    `drake:pod:workload` rule.
- **Statements.**
  - They name their cluster (`{cluster:String}`), so the store routes them
    to a cluster's own ClickHouse (ADR-0037 §3).
  - Every value is a typed parameter.

### Phase 4: Tracesis manages its agents

- **OpAMP** (OpenTelemetry's agent management protocol; the extension is
  already in our build). Tracesis sees every agent's version and health,
  changes its configuration and upgrades it. Neither the customer nor we
  touch a cluster by hand after install.
- **The OpenTelemetry Operator, opt in per namespace.** It adds application
  SDKs without code changes, for exceptions and in-process spans that eBPF
  cannot see.

## No data loss

- **Collection.**
  - Kubelet and cluster metrics are pulled each interval.
  - A collector restart loses at most one interval of points, as a Prometheus
    restart does.
  - A failed export is retried, then queued in memory up to a bound.
  - When the queue is full, points are dropped, never memory; the collector's
    own error counters show it.
- **Storage.**
  - **ClickHouse's disk guard** (`keep_free_space_bytes`) refuses writes
    before the node's disk is full, so a full disk can never take the node
    down.
  - **Volume.** Phase 1 collects only the allowlisted names for the
    allowlisted namespaces, so the volume is measured before it is widened.
- **Transition.** Nothing is switched or removed until phase 2 shows the
  numbers agree:
  - Prometheus, its volume and its 15 days of history stay as they are.
  - Rolling back phase 1 is one flag, `metrics.enabled: false`. The rows
    already written expire by the table's TTL. Nothing is deleted by hand.

## Consequences

- During the transition we run two metric collectors, and two copies of
  infrastructure metrics are stored. This is deliberate.
- PromQL recording rules (`drake:`) have to be rewritten as ClickHouse reads
  in phase 3. That is the largest piece of work in this ADR.
- Metric names differ between the two systems (`k8s.pod.cpu.usage` against
  `container_cpu_usage_seconds_total`). Phase 2's comparison is where that
  mapping is written down and checked.
