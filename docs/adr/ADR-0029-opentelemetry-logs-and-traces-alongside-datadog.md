# ADR-0029 — OpenTelemetry logs and traces, stored by Drake, alongside Datadog

**Status:** Proposed
**Amends:** ADR-0001 (control plane, not a telemetry backend)
**Builds on:** ADR-0003 (browser boundary), ADR-0004/0015 (Query Broker),
ADR-0011 (state semantics), ADR-0005 (read-only agent)

## Context

Drake reads metrics from Prometheus and nothing else. The goal is for Drake to
become an APM and observability product in its own right: metrics, logs and
traces for the projects it catalogues, correlated in one place.

Four facts about the estate shape the decision, all observed read-only on the
production cluster:

1. **Datadog is running and must keep running, unchanged.** Its operator
   injects APM libraries into every pod (`admissionController.mutateUnlabelled`,
   single-step instrumentation for dotnet/java/js/php/python/ruby) and collects
   every container's logs. People watch it. Nothing in this work may disable,
   reconfigure or degrade it, cluster-wide or for any single workload.
2. **There is no Loki, Tempo or other log/trace store**, and none is wanted.
3. **Node capacity is uneven.** The control-plane node is saturated (load
   well above its core count, little free memory, no swap, most of its CPU and
   memory already requested). The worker node has clear headroom.
4. **Every PersistentVolume is `local-path` with `ReclaimPolicy: Delete`.**
   Deleting a claim destroys its data. That includes production databases.

## Decision

### 1. Drake stores logs and traces — this amends ADR-0001

ADR-0001 kept raw telemetry out of Drake because mature stores existed to hold
it. For logs and traces no such store exists here, and the product goal is to
own the experience end to end. Drake therefore operates its own store for
**logs and traces only**, in **ClickHouse**, a dedicated datastore that is not
Drake's PostgreSQL. PostgreSQL remains catalog, RBAC, audit and projections,
exactly as ADR-0001 says.

**Metrics stay in Prometheus.** Service health, SLOs, incidents and alerting
are built on it, and it causes no problem. OTel-collected metrics may be
written to it later via remote write. Replacing it is not part of this ADR.

### 2. Collection is OpenTelemetry, side by side with Datadog

The collection model mirrors Datadog's: one agent per node.

- **Agent:** an OpenTelemetry Collector (contrib distribution, pinned by digest)
  as a DaemonSet. It reads container log files from the node (`filelog`
  receiver, read-only host mounts) and accepts OTLP from applications.
  Reading a log file is passive: Datadog reads the same files and neither
  process affects the other.
- **Gateway:** a small Collector Deployment that batches, applies the
  redaction and allowlist processors, and writes to ClickHouse.
- **No SDK injection while Datadog injects.** Two tracers in one process is
  the failure mode that can break an application. Drake installs no
  admission webhook and no OTel Operator `Instrumentation` that mutates
  application pods, and changes no Datadog label or annotation on any
  application workload.
- **Traces come first from eBPF.** OpenTelemetry eBPF Instrumentation (OBI)
  observes HTTP/gRPC/SQL at the kernel and emits spans and RED metrics without
  touching the process, so it cannot collide with dd-trace. It is a v0 project
  whose configuration changes between minor versions, so it is pinned, enabled
  per namespace and introduced after logs are proven. SDK-level instrumentation
  is a later decision, taken workload by workload, and only with the owner's
  agreement about Datadog for that workload.
- Drake's **own** telemetry pods opt out of Datadog injection with
  `admission.datadoghq.com/enabled: "false"` on their pod templates. That
  label is on Drake's pods only; it changes nothing Datadog does anywhere else.

### 3. Capacity and data safety are part of the contract

- ClickHouse runs **only on a node with headroom**, selected by an
  operator-set node label (never the saturated control-plane node), with
  explicit requests and
  limits, a bounded `max_server_memory_usage`, and table TTLs (default 7 days
  logs, 3 days traces). A store that can fill a disk is a store that can take a
  node down.
- Its volume uses a **dedicated `Retain` StorageClass**, so no Helm operation
  can delete its data by deleting a claim. No existing PVC, StorageClass or
  workload is modified.
- Collector agents carry small, hard limits (memory limiter processor plus
  container limits) and a low priority class, so under pressure on a saturated node they
  are what gets throttled or evicted, not an application.
- If ClickHouse is unavailable, the gateway's bounded queue drops data rather
  than growing. Losing telemetry is acceptable; destabilising a node is not.

### 4. The read path keeps every existing boundary

The browser still talks only to the Drake API (ADR-0003). The API reaches
ClickHouse through a server-owned connector (ADR-0015 §8: no URL from a
request, no credentials in responses). Log and trace queries are
repository-controlled templates with typed, bounded parameters (time range,
service, severity, trace id, a bounded free-text term that is passed as a
query parameter, never concatenated). Scope comes from the catalog: a caller
sees telemetry only for namespaces bound to services they may view (ADR-0010,
ADR-0014). Budgets (range, row limit, timeout, concurrency) fail closed, as
they do for Prometheus.

Absent telemetry is `not_configured` or `unknown`, never "no errors"
(ADR-0011).

### 5. Logs are redacted before storage

Container logs can contain credentials. The gateway applies a redaction
processor before export (the same credential shapes the agent's `redact`
package and ADR-0018 already reject), and log bodies are bounded in size.
Redaction is best-effort defence in depth, not a guarantee, and is described
that way in the UI.

## Rollout

1. Local stack and tests: ClickHouse + Collector in `deploy/local`, API
   provider and templates, contract tests. No server change.
2. Production, logs only: a new `drake-otel` namespace, approved step by step.
   Applications are untouched.
3. Traces via OBI, one namespace at a time, starting with a dev namespace.
4. Only after parity and with the owner's agreement: anything that touches
   Datadog. Not before.

Each step has a rollback that removes only what it added (`helm uninstall` of
the `drake-otel` release; the `Retain` volume survives it).

## Consequences

- Drake becomes a telemetry store for logs and traces and owns their
  retention, capacity and security. That is new operational responsibility,
  bounded by TTLs and limits from day one.
- Telemetry is collected twice (Datadog and OTel) during coexistence. The cost
  is node CPU/memory and is why the limits above are hard.
- eBPF traces are shallower than SDK traces (no in-process spans). Accepted for
  the coexistence period.
- The free-text log search is bounded and parameterised; it is not a query
  language, consistent with ADR-0004.

## Alternatives considered

**Loki + Tempo.** Rejected by the product owner.

**Replace Prometheus now.** Rejected: health, SLO, incidents and alerting
depend on it and it is working. Revisit separately.

**OTel Operator auto-instrumentation now.** Rejected while Datadog injects into
the same pods: double instrumentation can break applications.

**Store logs and traces in Drake's PostgreSQL.** Rejected: wrong engine for
append-heavy, TTL-bound, full-text and wide-scan workloads, and it would put
telemetry volume next to the catalog and audit data that must never be at risk.
