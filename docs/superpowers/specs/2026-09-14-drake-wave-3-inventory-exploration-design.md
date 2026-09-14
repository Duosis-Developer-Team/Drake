# Drake Visual/UI-UX Remake — Wave 3 Inventory Exploration Design

**Date:** 2026-09-14

**Status:** CTO direction approved in conversation; written specification awaiting review

**Decision owner:** CTO

**Implementation owner:** Senior Developer (Sonnet 5)

**Depends on:** `2026-09-13-drake-visual-ui-ux-remake-design.md`, Wave 1, and Wave 2

**Protected baseline commits:** `ca87a95`, `292cccc`, `adc6410`

## 1. Executive decision

Wave 3 will turn Drake's catalog and inventory routes into one connected
operational investigation path. It is not a page-by-page facelift.

The primary journey is:

```text
Command Center finding
  -> affected project or cluster
  -> affected environment or inventory class
  -> service or resource evidence
```

An on-call Platform Operations or SRE user must be able to move from a risk
shown on the Command Center to the relevant service or inventory evidence in
no more than two drill-down transitions. Every screen continues the
Operational Canvas hierarchy established in Waves 1 and 2:

1. **Verdict** — what deserves attention now,
2. **Evidence** — what measured or recorded facts support that verdict,
3. **Action** — the next safe drill-down.

Wave 3 covers Projects, project/environment/service hierarchy, Clusters,
cluster inventory, and the inventory resource evidence route. It does not
change backend behavior, authorization, data semantics, or deployment state.

## 2. Why this approach

Three approaches were considered.

### A. Independent page facelift

This would modernize each route quickly, but preserve the current navigation
burden and allow each page to invent a different information hierarchy. The
result would look newer without becoming materially easier to operate.

### B. Connected operational hierarchy — selected

This approach composes existing authorized APIs into stable presentation
models and preserves scope as the user drills from portfolio to evidence. It
creates a consistent mental model across seven related route surfaces and can
be delivered through three sequential quality gates.

### C. Topology-graph-first explorer

This would be visually distinctive, but the current API does not expose every
relationship or historical state needed for a complete graph. Inferring
missing edges would make Drake appear more certain than its evidence permits.

**Decision:** Approach B is the Wave 3 architecture. Graph-like layouts are
used only where explicit project/environment/service relationships already
exist. No relationship is inferred from naming, temporal proximity, or an
incomplete inventory page.

## 3. Current-state findings that shape the design

- Projects, project detail, service detail, clusters, and inventory already
  contain useful semantics and tests, but their visual hierarchy is uneven.
- Environment detail and cluster detail still use older catalog cards and
  primitives, while later surfaces use the Wave 1/2 panel and state system.
- The Projects route primarily communicates catalog identity and criticality;
  it does not help the operator distinguish a critical healthy project from a
  critical project with failing or absent evidence.
- Project detail has telemetry and composition, but the user must scan
  separate capability, environment, and dependency areas to locate impact.
- Environment detail lists services without service-health verdicts.
- Service detail already puts golden signals first and supports URL time
  ranges, accessible charts, stale last-good data, and honest unavailable
  states. Wave 3 should strengthen this rather than replace it.
- Clusters correctly separates agent connectivity from inventory freshness,
  but the list still requires row-by-row scanning.
- Cluster detail spends substantial space on multiple donut cards. These are
  valid values but weak comparisons for operational triage.
- Inventory correctly treats the dense table as the product, keeps missing
  resources visible, uses URL filters, and excludes Secrets and ConfigMaps.
  These behaviors are protected.

## 4. Scope and delivery gates

Wave 3 is one product journey delivered through three sequential gates. A
later gate does not begin until the previous gate has passed its targeted and
browser-level verification.

### Wave 3A — Project portfolio and project topology

- `/projects`
- `/projects/[projectId]`
- shared project risk and topology presentation models

### Wave 3B — Environment and service diagnostics

- `/projects/[projectId]/environments/[environmentId]`
- `/projects/[projectId]/environments/[environmentId]/services/[serviceId]`
- scoped incident and deployment context for the service route

### Wave 3C — Cluster and inventory operations

- `/clusters`
- `/clusters/[clusterId]`
- `/clusters/[clusterId]/inventory`
- `/clusters/[clusterId]/inventory/[resourceId]` as the terminal evidence
  surface reached from the inventory table

The implementation plan may create smaller commits inside each gate, but it
must not mix unrelated gates in one commit.

## 5. Shared information architecture

### 5.1 Scope continuity

The current entity hierarchy remains canonical:

```text
Project
  -> Environment
    -> Environment service
      -> Service health binding and signals

Cluster
  -> Inventory summary
    -> Inventory resource
```

Where an environment service is bound to a workload, the UI may link the
service path to its authorized cluster or inventory evidence. The UI must not
claim that a service owns every resource with a similar label.

### 5.2 Navigation rules

- Existing route paths remain stable.
- Breadcrumbs use human-readable keys while links retain opaque IDs.
- A Command Center drill-down lands on the narrowest route supported by its
  existing evidence.
- Time range is preserved only between routes whose queries use it.
- Project, environment, health, lifecycle, kind, and search filters remain in
  the URL where they affect the visible result set.
- Back navigation must restore the previous filtered view.
- A drawer may provide quick evidence, but it may not open another drawer.
  Deep context uses a full route.

### 5.3 Shared status ordering

Presentation models use the existing central status mapping and severity
ordering. They may group and sort; they may not compute domain health.

The operational scan order is:

```text
critical -> unhealthy -> degraded -> warning -> stale -> unknown
-> not configured / not collected -> healthy / ok -> not applicable
```

This is a presentation order, not a conversion between domain vocabularies.
Agent connection, inventory freshness, service health, criticality, and
lifecycle remain separate axes.

## 6. Wave 3A design

### 6.1 Projects — portfolio risk map

**Primary question:** Which project needs investigation first?

The route gains a **criticality × observed-health risk map** above the catalog
table. Rows represent recorded business criticality. Columns represent the
worst service-health state reported for a project. Each project remains a
named, keyboard-focusable item; color is secondary to visible status text.

Rules:

- Project criticality is not treated as health.
- Project observed health is the worst reported service-health status among
  the services actually loaded for that project.
- A project with no service evidence is **unassessed**, never healthy.
- Stale, partial, denied, not-configured, and incomplete coverage remain
  visible and cannot be averaged away.
- Selecting a cell applies visible URL-backed filters to the supporting table.
- The supporting table remains the precise comparison and search surface.
- Cursor continuation is exposed; the current silent first-page boundary may
  not be presented as the entire authorized portfolio.

The risk map may make an estate-wide claim only when the required project and
service-health result sets are complete. During progressive loading it must
say that coverage is incomplete and avoid a global verdict. If completing the
existing paginated APIs violates the measured request or latency budget, the
feature stops at an explicit partial state and a separate backend aggregate
proposal is raised; the client must not conceal the limitation.

At desktop widths the map is a compact matrix. Below 1280 px it becomes
criticality-grouped disclosure sections, not a horizontally scrolling grid.

### 6.2 Project detail — environment/service topology lanes

**Primary question:** How is this project composed, and where is impact
concentrated?

The header gains one project verdict derived from existing service-health
states and evidence coverage. It is accompanied by the recorded criticality,
but the two are visually and semantically distinct.

The primary evidence surface is a set of **environment topology lanes**:

- one lane per authorized environment,
- explicit runtime and cluster/namespace placement,
- one child row per authorized environment service,
- visible service health, freshness, binding state, and workload identity,
- worst-first ordering without hiding healthy services,
- direct links to environment, service, service-health, and authorized
  cluster detail where those relationships exist.

External environments render their provider health and not-applicable fields
without fabricating cluster or workload concepts.

Capability coverage and telemetry trends remain available beneath the
topology. Managed dependencies, in-cluster dependencies, owners, repository,
and provenance move into a lower standing-state section. Managed dependency
verification remains evidence quality, not health.

## 7. Wave 3B design

### 7.1 Environment detail — service health lanes

**Primary question:** Which service in this environment is degraded, and what
evidence is missing?

The route moves from metadata-plus-list to a verdict-led diagnostic view:

1. environment verdict and coverage,
2. service health lanes ordered by attention,
3. runtime placement and operational capabilities,
4. provenance and catalog metadata.

Each service lane includes:

- service name and component,
- health status and freshness,
- ready/desired replicas when measured,
- restart or instability evidence when measured,
- CPU and memory utilization when measured,
- binding state and workload identity,
- an action to open the service evidence route.

A dash remains “not measured”, not zero. An unbound service remains visible as
not configured and links to the existing safe binding flow where authorized.
The page uses the existing filtered service-health API and does not implement
a second health policy in the browser.

### 7.2 Service detail — annotated golden signals

**Primary question:** Why is this service unhealthy or uncertain?

The existing golden-signal dashboard remains the dominant surface. Wave 3
recomposes it into consistent small multiples for availability, traffic,
errors, latency, and saturation according to the service's existing metrics
profile. Profile gating remains authoritative: unavailable signals do not
produce empty decorative charts.

Existing incident and deployment list endpoints, filtered by
`environment_service_id`, provide annotations in the same selected time
window. An annotation states only that an incident or deployment occurred at
that time. It must never say or imply that the deployment caused the incident.

Supporting sections retain workload selectors, health paths, capabilities,
catalog version, provenance, cached/last-good state, newest sample time, and
correlation IDs for provider failures.

If incident or deployment history is unavailable, its lane says unavailable;
it does not draw an empty line that implies no events occurred.

## 8. Wave 3C design

### 8.1 Clusters — visibility matrix

**Primary question:** Which cluster is invisible or operating on stale
evidence?

The primary visual is a **connection × inventory-freshness matrix**. Every
authorized cluster appears as a named item positioned by the agent state and
inventory state that the API reported.

- Connectivity and inventory freshness remain separate dimensions.
- Disconnected means Drake cannot currently see the cluster; it does not mean
  the cluster is unhealthy.
- Reconcile-required, empty, stale, enrolled, and revoked remain distinct.
- Selecting a matrix group filters the supporting table through URL state.
- The table retains exact cluster identity, lifecycle, environments, and
  observation time.
- Attention states sort first only when the complete loaded result permits a
  truthful ordering; pagination completeness remains visible.

On mobile, the matrix becomes state-grouped disclosure sections followed by
a reduced but complete card/list representation. The DOM order matches the
visual and keyboard order.

### 8.2 Cluster detail — visibility and headroom board

**Primary question:** Is Drake's view trustworthy, and where is verified
capacity or workload risk concentrated?

The top verdict combines no states. It presents four separate answers in one
decision band:

- agent connectivity,
- inventory freshness,
- certificate runway and server-owned warning,
- capacity evidence availability.

The existing cluster-capacity dashboard remains the source for CPU, memory,
and storage headroom. The UI uses provider values and existing reference
bands; it does not create new risk thresholds or forecasts.

The repeated health donuts are replaced with compact, aligned composition
bars for nodes, namespaces, workloads, pods, and persistent volume claims.
This makes unhealthy, degraded, and unknown counts comparable across resource
classes. Pod restart, CrashLoop, OOM-killed, missing-resource, and certificate
warnings receive direct links to appropriately filtered inventory views.

Referenced environments become authorized links when the response provides a
resolvable environment identity. If the current payload lacks the opaque ID
required for a safe route, the item remains labelled context rather than a
guessed link.

A complete namespace × workload heatmap is **not** produced from the first
page of `/inventory/resources`. The current summary endpoint has no namespace
aggregate, and crawling an unbounded cluster inventory in the browser would
be misleading and expensive. That heatmap is deferred unless a separately
approved backend aggregate contract is introduced.

### 8.3 Inventory — problem-first resource exploration

**Primary question:** How are unhealthy or missing resources distributed, and
which exact resource is the evidence?

The dense inventory table remains the main product. The area above it becomes
a thin problem-first summary:

- sorted resource-kind bars,
- aligned health-composition bars by resource class,
- direct counters for CrashLoop, OOM-killed, restarts, and missing resources,
- freshness and last reconcile context.

Selecting a bar or status updates the existing URL filters and the resource
table. The active cross-filter is visible and removable in one action.

Protected behavior:

- default lifecycle remains active,
- the count of hidden missing resources remains visible,
- missing resources remain queryable and are never silently discarded,
- event type is only sent for Event rows,
- search keeps its minimum-length and debounce behavior,
- cursor pages accumulate without losing the active filter,
- Secrets and ConfigMaps never appear in chart, filters, table, or detail,
- exact resource values and provenance remain on the resource detail route.

The inventory resource detail route adopts the same verdict/evidence/action
hierarchy so the redesigned journey does not terminate on a legacy-looking
page. It does not expose new fields or raw sensitive payloads.

## 9. Existing API composition

Wave 3 starts with the following existing read contracts:

| Surface | Existing source | Use |
|---|---|---|
| Projects | `/v1/projects` | identity, criticality, lifecycle, counts |
| Project topology | `/v1/projects/{id}/environments` and `/v1/service-health/services?project_id=...` | environments, services, binding, measured health |
| Environment | `/v1/projects/{id}/environments/{id}` and `/v1/service-health/services?environment_id=...` | placement, services, workload and health evidence |
| Service | service catalog detail, dashboard templates, health/series endpoints | binding, current verdict, golden signals |
| Service annotations | `/v1/incidents?environment_service_id=...` and `/v1/deployments?environment_service_id=...` | time-related context without causality claim |
| Clusters | `/v1/clusters` | identity, connection, freshness |
| Cluster detail | `/v1/clusters/{id}`, capacity dashboard, `/inventory/summary` | placement, headroom, agent and inventory rollups |
| Inventory | `/inventory/summary`, `/inventory/resources`, resource detail | distribution, filtering, exact evidence |

All collection limits and cursors remain part of the UI contract. A view model
must receive explicit completeness information; an absent next page cannot be
assumed merely because a component was given an array.

No new endpoint is included in Wave 3. A backend proposal requires a separate
CTO decision if measurement proves that complete existing-API composition is
too slow, too large, authorization-sensitive, or semantically incomplete.

## 10. Frontend architecture

Wave 3 follows the existing boundaries established by the master brief:

```text
src/components/data-viz/       reusable operational visuals
src/components/features/       route/domain compositions
src/components/ui/             existing controls, panels and state surfaces
src/lib/view-models/            pure API-to-presentation transforms
src/lib/design/                 shared status ordering and formatting
```

Requirements:

- View models are deterministic, side-effect free, and unit tested.
- Route components own data loading and URL state; charts do not fetch.
- Shared visuals receive explicit ready/loading/empty/denied/stale/partial/
  error and completeness states.
- Existing `HealthMatrix`, composition bars, status mapping, data tables,
  chart frames, and telemetry renderer are reused or generalized when their
  contracts fit. A near-duplicate component is not created solely for Wave 3.
- Environment and cluster routes migrate off the legacy catalog Card/state
  primitives only as part of their approved redesign.
- No new UI framework, chart library, table engine, or motion dependency is
  added.
- ECharts remains lazily loaded and must not enter the shell bundle eagerly.

## 11. Data truth and security rules

- Browser requests continue to target Drake's same-origin `/v1` API only.
- Existing authorization semantics remain unchanged: authorized collections
  may return empty; out-of-scope details remain honest not-found responses.
- No UI response, error, link, tooltip, or screenshot may expose credentials,
  configuration references, raw provider errors, subjects, Secrets, or
  ConfigMaps.
- Worst-state aggregation may surface a problem but may not turn missing or
  denied data into a healthy result.
- Counts are computed only from authorized records returned by the API.
- Criticality, lifecycle, connection, freshness, verification, and health are
  never converted into one synthetic score.
- Percentages and utilization values use server or dashboard contracts;
  frontend thresholds are not invented.
- Forecasts appear only when an existing source supplies the forecast and its
  confidence. Otherwise the UI says forecast unavailable.
- Temporal co-occurrence is labelled “related in time”, never “caused by”.

## 12. Responsive and accessibility behavior

Required widths are 390, 768, 1024, 1280, 1440, and 1920 px in light and dark
themes.

- Desktop comparison matrices reflow into grouped disclosure/list views;
  they do not force page-level horizontal scrolling on narrow screens.
- Dense inventory tables may use their existing contained table overflow, but
  the document itself must not overflow horizontally.
- Mobile DOM order follows verdict -> attention/evidence -> action/detail.
- Every route has one meaningful `h1`.
- Every visual status is named in text; color is never the only carrier.
- Matrix items, chart controls, disclosure elements, filters, and drill-downs
  are keyboard reachable with visible focus.
- Charts retain accessible summaries and table fallbacks.
- Tooltip content remains within the viewport at both edges.
- Focus order follows visual order, and dialogs/drawers trap and restore focus.
- `prefers-reduced-motion` remains honored.
- Critical routes maintain zero serious or critical axe violations.

## 13. Loading, empty, partial, and failure behavior

Every primary surface explicitly handles:

- initial loading,
- background refresh with last-good content retained,
- complete empty,
- filtered empty,
- not configured,
- permission denied,
- not found,
- unknown,
- stale,
- partial or incomplete pagination,
- provider unavailable,
- throttled,
- render/runtime error.

Loading skeletons approximate the final hierarchy. An empty collection does
not imply a healthy estate. Retry surfaces show safe messages and correlation
IDs when supplied. A partially loaded matrix states what is incomplete and
does not offer a global verdict or risk ordering as if it were complete.

## 14. Performance constraints

- Existing refresh intervals and cancellation behavior are preserved.
- No per-row timer is introduced; relative times use the shared clock.
- Project and service-health pagination is progressive and cancellable.
- A filter change gives local visual feedback within 100 ms even when remote
  data is still loading.
- No unbounded inventory crawl is permitted from a browser route.
- Data already loaded for the same scope and freshness window should be reused
  through existing resource behavior rather than refetched by every child
  visual.
- Production build output is checked for an accidental eager chart-library
  or duplicate-library regression.

## 15. Verification and visual evidence

Each Wave 3 gate must run under the repository's supported Node 24 toolchain:

- targeted view-model and component tests,
- full web typecheck,
- ESLint for `src`,
- full Vitest suite,
- production build,
- relevant real-stack Playwright suites,
- axe, keyboard, and horizontal-overflow checks.

TDD applies to each behavior-changing slice: failing acceptance evidence is
written first, then the smallest implementation, then refactoring.

Fresh before/after screenshots are required for:

- Projects portfolio map: populated and incomplete/empty,
- Project detail: mixed service states,
- Environment detail: healthy, degraded, and unbound services,
- Service detail: live signals, stale last-good, provider unavailable, and
  incident/deployment annotations,
- Clusters: mixed connection/freshness states,
- Cluster detail: populated inventory, stale/disconnected, and no inventory,
- Inventory: default, unhealthy/missing cross-filter, mobile filter state,
  and resource detail,
- all primary surfaces at 1920, 1280, 1024, and 390 px in both themes.

Screenshots must be generated from isolated revision-specific builds or
separate immutable output directories. A Wave 3 test run may not overwrite
the Wave 0, Wave 1, or Wave 2 evidence directories.

## 16. Acceptance criteria

Wave 3 is complete only when all of the following are true:

1. A Command Center project or cluster finding reaches relevant detail
   evidence in no more than two drill-down transitions.
2. Projects clearly separates recorded criticality from observed health and
   exposes pagination completeness.
3. Project detail shows environment/service impact without hiding healthy,
   unknown, stale, or unbound services.
4. Environment detail identifies the worst affected service without
   recomputing backend health policy.
5. Service detail aligns golden signals with filtered incident/deployment
   timing while making no causality claim.
6. Clusters keeps connectivity and inventory freshness as separate axes.
7. Cluster detail makes headroom and inventory risk more comparable than the
   current repeated donut layout without inventing thresholds.
8. Inventory visual filters and table URL state remain shareable and
   reversible; missing resources and exclusions are preserved.
9. Every aggregate is explicit about incomplete, stale, unknown, denied, or
   not-configured evidence.
10. The supported responsive, keyboard, axe, test, and production-build gates
    pass with fresh evidence.
11. No backend, authorization, dependency, or deployment change is included.
12. The protected Wave 2 commit chain remains intact.

## 17. Explicit non-goals

- Backend or database changes
- New aggregate endpoints in the Wave 3 implementation branch
- Changes to RBAC, scope visibility, or not-found behavior
- A synthetic project or cluster health score
- Inferred dependency edges or automated root-cause claims
- A namespace × workload heatmap built from incomplete client-side data
- Redesign of Incidents, Alerts, Deployments, SLO, Protection, Integrations,
  Onboarding, Notifications, Access, or Audit routes
- Datalake and Drake frontend code sharing
- New UI/chart/table/motion dependencies
- Production, staging, or `sslip.io` deployment
- Removal of legacy primitives outside files migrated by this wave

## 18. Handoff sequence

1. CTO and user review this written specification.
2. After written-spec approval, create a file- and test-level implementation
   plan covering Wave 3A, 3B, and 3C with explicit review checkpoints.
3. Senior Developer reads the master brief, this specification, relevant
   ADRs, current routes, API contracts, and tests before implementation.
4. Senior Developer works one tested vertical slice at a time and reports
   evidence at each gate.
5. No deploy occurs during Wave 3 without a separate explicit CTO decision.
