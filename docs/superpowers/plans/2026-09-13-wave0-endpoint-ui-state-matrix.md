# Wave 0 — Endpoint → UI → state matrix

Snapshot of the current frontend before the Wave 1/2 remake work starts. Feeds
the implementation plan at `2026-09-13-drake-visual-remake-wave0-1-command-center.md`.

## Two parallel state-handling systems

The codebase currently has both of these live at once:

| System | Data hook | State components | Used by |
|---|---|---|---|
| Current (Sprint 13) | `lib/useResource.ts` → `useResource<T>()` | `components/ui/states.tsx` (`DeniedState`, `NotConfiguredState`, `UnknownState`, `NoDataState`, `ErrorState`, `StaleBanner`, `PartialBanner`, `LoadingSkeleton`) | `/`, `/projects`, `/projects/{id}`, service detail, `/clusters`, `/clusters/{id}/inventory`, `/admin` |
| Legacy (pre-Sprint-13) | `components/catalog/primitives.tsx` → `useApi<T>()` | `components/state/DataState`, `components/state/StatusBadge`, `components/ui/Card` (now a shim over `Panel`) | `/alerts`, `/deployments`, and other "reframed" (not "rebuilt") routes |

Any Command Center task that reaches into `deployments.ts` / `alerting.ts` for
list data must use `useResource` + `apiGet`, not adopt the legacy `useApi`
pattern — see plan Task 2.2.

## Command Center (`/`) — current composition

6 client-side fetches, all via `useResource`, `REFRESH_MS = 60_000`:

| Endpoint | Type | Feeds |
|---|---|---|
| `/v1/catalog/context` | `CatalogContext` | `CatalogPanel`, freshness in `PageHeader` |
| `/v1/incidents?state=open&limit=25` | `{items: IncidentSummary[]; total}` | `TriageStrip`, `NeedsAttention` via `incidentItems()` |
| `/v1/alerts/summary` | `AlertSummary` | `TriageStrip`, `NeedsAttention` via `alertItems()` |
| `/v1/clusters` | `{clusters: Cluster[]}` | `TriageStrip`, `FleetPanel`, `NeedsAttention` via `clusterItems()` |
| `/v1/service-health/services` | `{items: ServiceHealthRow[]}` | `TriageStrip`, `ServiceHealthPanel`, `NeedsAttention` via `serviceItems()` |
| `/v1/integrations/health` | `{integrations: IntegrationHealth[]}` | `TriageStrip`, `IntegrationsPanel`, `NeedsAttention` via `integrationItems()` |

Plus, per rendered cluster row: `/v1/clusters/{id}/inventory/summary` inside
`FleetCounts` — one request per cluster (N+1), flagged in the plan (Task 2.5)
as a candidate to share via a single `useClusterInventorySummaries` hook
rather than fixed by a new backend endpoint.

No `/v1/overview` exists by design (`lib/overview.ts:1-14`): each endpoint
stays the authority on its own state so two screens cannot disagree about it.

## `ChartFrame` contract — the 8 states every new chart must express

`loading | ready | empty | no-data | not-configured | unknown | denied | error`
(`components/charts/ChartFrame.tsx:36-44`), plus `freshness: stale` and
`partial` as flags on top of `ready`. Every new Wave 2 visual
(`OperationalTimeline`, `HealthMatrix`, `CapacityRiskBoard`) either renders
through `ChartFrame` or reuses the matching primitive from
`components/ui/states.tsx` directly — no new ad hoc "empty" text.

## Known debts carried into this remake (not fixed by it unless noted)

- Fleet inventory N+1 request pattern (`FleetCounts`) — addressed opportunistically in Task 2.5, not guaranteed fixed.
- `/alerts` and `/deployments` still on the legacy `useApi`/`DataState`/`Card` stack — out of scope for this plan (Wave 3/4 per the brief's own phased rollout), but any new Command Center code must not copy that pattern.
- Command palette only indexes catalog entities (`project`/`environment`/`service`/`cluster`) — Task 1.3 adds pages/actions; incident/alert/deployment/SLO indexing waits on a backend search contract (brief §8.1).
