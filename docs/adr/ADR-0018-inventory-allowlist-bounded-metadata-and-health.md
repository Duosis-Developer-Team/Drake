# ADR-0018 — Inventory allowlist, bounded metadata, and health derivation

Status: accepted (Sprint 4)
Builds on: ADR-0005 (read-only agent), ADR-0011 (state semantics),
ADR-0017 (atomic projection).

## Context

Storing Kubernetes objects wholesale would smuggle secrets, unbounded
cardinality, and someone else's API surface into Drake. The inventory
must be a bounded, reviewed projection — and health must be a rule
output, not a guess.

## Decision

### 1. Explicit resource allowlist (agent AND server)

v1 collects exactly: namespaces, nodes, pods, services, endpointslices,
deployments, replicasets, statefulsets, daemonsets, jobs, cronjobs,
persistentvolumeclaims, persistentvolumes, storageclasses,
horizontalpodautoscalers, poddisruptionbudgets, resourcequotas,
limitranges, events — plus Prometheus Operator CRDs
(servicemonitors/podmonitors/prometheusrules, metadata+status only) when
the CRD is present. `Secret` and `ConfigMap` are rejected at BOTH the
agent registry and server ingest; so are wildcards, exec/attach/
portforward subresources, and anything not on the list. RBAC verbs are
exactly `get`, `list`, `watch`.

### 2. Bounded record shape

A resource record carries only: api group/version, kind, namespace, name,
UID, opaque resourceVersion, allowlisted labels/annotations (Kubernetes
well-known + `drake.duosis.com/*` keys; bounded count and length), an
owner-reference summary (kind/name/uid, bounded), a per-kind spec summary
(e.g. replicas, selector hash, service ports — no env vars, no volumes
beyond PVC names), selected status/conditions (bounded count), and the
source observed time. Full manifests, `data`/`binaryData`/`stringData`,
managedFields, environment variables, secret volume payloads,
service-account tokens, and credential-shaped values are rejected
fail-closed by schema and by the ingest content guard (the S0 redaction
contract). Byte/count budgets (page size, body size, label/annotation
counts, value lengths, condition counts, snapshot totals) are enforced in
the contract schema, at the API boundary, and by DB constraints — and
negative-tested at each layer.

### 3. Health is derived server-side, deterministically

Health per resource is computed from the bounded status fields by table-
driven rules with machine-readable reason codes:

- Deployment: available/desired mismatch, Progressing=False,
  ReplicaFailure=True.
- StatefulSet: ready/current/updated divergence; missing PVCs.
- DaemonSet: desired vs ready/available.
- Pod: phase, readiness, restart velocity, OOMKilled/CrashLoopBackOff,
  Unschedulable.
- Job/CronJob: Failed/BackoffLimitExceeded, missed schedule/deadline.
- Node: Ready condition plus Memory/Disk/PID pressure.
- Namespace: Terminating/stuck; quota saturation.
- PVC: Pending/Lost vs Bound.

States are `healthy | degraded | unhealthy | unknown`; **unknown is not
healthy** and missing data yields unknown with a reason, never a default
green. Summaries aggregate counts per state; every non-healthy result
carries its reason codes and a short safe message. The rules live in one
module and are proven by table tests.

### 4. Never fabricate

No usage percentages or capacity numbers are synthesized from inventory
(those come from the metrics provider, Sprint 3's boundary). The UI shows
counts, states, conditions, and freshness — nothing the agent did not
observe.

## Consequences

- Adding a resource family or field is a reviewed contract change (schema
  + allowlist + rules), never a runtime toggle.
- Some Kubernetes detail is simply not visible in Drake by design; deep
  debugging stays in kubectl/K9s with the operator's own credentials.
- Health explanations are testable and stable across releases.
