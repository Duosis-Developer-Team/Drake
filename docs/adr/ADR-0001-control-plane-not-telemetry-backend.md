# ADR-0001: Drake is a control plane, not a telemetry backend

**Status:** Accepted

## Context

Mature engines already exist for metric collection/storage (Prometheus,
Thanos), alert evaluation/routing (Alertmanager), and log/trace pipelines
(OpenTelemetry, Loki, Tempo). Rebuilding any of them would consume the team
without adding differentiated value.

## Decision

Drake owns business context and user experience: the project/environment/
service/tenant/data-store/backup/deployment catalog, RBAC, audit, incident
projection, and query mediation. It never re-implements a time-series engine,
and raw metric samples, full logs, or trace spans are never copied into
Drake's own PostgreSQL.

## Consequences

- Drake's value concentrates in correlation and honest presentation.
- Availability of underlying engines is surfaced as explicit source health;
  Drake degrades to last-known snapshots marked stale, never fabricates data.
- Storage/retention concerns of telemetry stay in the data plane.
