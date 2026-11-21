# Tracesis UI plan (named "Tracery" until 2026-11-21)

Status: in progress (2026-11-21). Design source: the "Tracery — Ürün Tasarımı"
canvas (system + Overview, Services, Service, Traces, Trace detail, Logs),
approved by the product owner.

## Rules

1. **No module is removed.** Every route, action and state that works today
   stays reachable. A screen may be regrouped, renamed or redrawn. It is never
   dropped. A visual is removed only when it repeats a number already shown on
   the same screen (listed per route below).
2. **Status before detail.** Each screen opens with its verdict (what is wrong,
   how many, since when), then the evidence.
3. **Every number is a door.** A count, a row or a chart bucket links to the
   screen that explains it.
4. **Unknown is not zero.** A missing measurement renders as "—" / "no data",
   never as 0, never as healthy.
5. **One path:** service → trace → log. Each of the three links to the other two
   for the same time window.
6. Tokens only. No hex literals in components. Gold (`--gold`) means "critical
   path / slowest" and nothing else.

## Information architecture

| Group | Entry | Route | Was |
|---|---|---|---|
| (top) | Overview | `/` | Command Center |
| | Services | `/service-health` | Service health |
| | Projects | `/projects` | Projects (Estate) |
| | Dashboards | `/dashboards` | Dashboards |
| Respond | Incidents | `/incidents` | Operations |
| | Alert rules | `/alert-rules` | Operations |
| | Incoming alerts | `/alerts` | Alerts |
| | Deployments | `/deployments` | Operations |
| Infrastructure | Clusters | `/clusters` | Estate |
| | Resource usage | `/infrastructure` | Infrastructure |
| | Objectives | `/slo` | Estate |
| | Protection | `/protection` | Operations |
| Settings | Onboard project, Integrations, Notification routing, Audit & access | unchanged | Configuration |

Later additions, not yet built: global **Traces** (`/traces`) and **Logs**
(`/logs`) explorers. Both reuse `TraceExplorer`/`LogExplorer` with a service
picker over the existing per-binding endpoints.

## Phases (one PR each)

| # | Scope |
|---|---|
| 1 | **Foundation.** Tokens (Cathedral dark / Vellum light), fonts (Plus Jakarta Sans, JetBrains Mono), Tracesis brand, flat shell, IA regrouping, visible product name. |
| 2 | **Service map.** Promote the dashboard `TrafficGraphWidget` into a reusable `ServiceMap`: keyboard access, no column overlap, rate-encoded motion, tokens. Show it on the environment page, the service page (neighbourhood), Services ("Map" view) and Overview. |
| 3 | **Services + service page.** Grouped list (project / environment), "—" for no data, one service page that joins the catalog service and its binding (golden signals, lanes with deploy marker, "why slow" findings, endpoints, dependencies, pods, error logs). |
| 4 | **Traces and Logs.** Global explorers; trace detail with the critical path in gold and a one-sentence summary; log line → trace link. |
| 5 | **Overview.** Verdict strip, "needs attention" list, environments, traffic with deploy markers, activity, slowest endpoints. |
| 6 | **Respond.** Incidents, alert rules, incoming alerts, deployments: merge the three KPI kits, server totals instead of first-page counts, pagination, URL filters. |
| 7 | **Infrastructure.** Clusters, inventory, resource usage, objectives, protection. |
| 8 | **Settings and sign-in.** Settings hub, onboarding, integrations, notification routing, access; sign-in with the new mark. |

## Route matrix

| Route | Keep (works well) | Change / drop as duplicate | Phase |
|---|---|---|---|
| `/` | AttentionQueue, verdict with sources-answered honesty, OperationalTimeline, HealthMatrix, SignalBoard, CapacityRiskBoard, EvidenceCoverage | SourceRing (repeats N/5), CatalogPanel connector, ServiceHealthPanel mosaic (repeats HealthMatrix), integrations avatar row, staggered reveals | 5 |
| `/projects` | ProjectRiskMap (a real filter), table | MiniBars per project | 3 |
| `/projects/[p]` | EnvironmentCard + ServiceTile, telemetry section | capability dots in KPI (repeat the panel) | 3 |
| `/projects/[p]/environments/[e]` | scheduled jobs | service cards gain health; link to the service map | 2–3 |
| `/projects/…/services/[s]` | golden signals | KPI tiles that repeat "what Tracesis measures"; add link to the binding page | 3 |
| `/service-health` | table, SignalBoard, CoverageCard | BreakdownCard (third copy of the tally), bound ring, healthy meter | 3 |
| `/service-health/[id]` | TimelineStrip, TraceExplorer, LogExplorer, LogPatterns, Why panel, gauges | SignalChart without a time axis, RingProgress beside "3/3", raw ISO times | 3–4 |
| `/dashboards/[env]` | Overview lanes, services, **traffic graph**, findings, heatmap, endpoints, capacity bars, DB ops, ServiceDrawer | logs donut, pod hexagons; overview failure shown as endless loading | 2 |
| `/incidents`, `/[id]` | row list, lifecycle stepper, timeline | BreakdownRing, "Shown" KPI, LifecycleRule; KPIs from ≤25 rows | 6 |
| `/alerts`, `/[id]` | AlertChain, rows | aside lanes repeating KPIs; silence create/expire exists in API, no UI | 6 |
| `/alert-rules` | lanes, SignalChart, preview + episodes | AlarmClock (radial, mouse-only, local time) | 6 |
| `/deployments`, `/[id]` | evidence checklist, before/after table | outcome ring, decorative rings; KPIs from ≤25 rows | 6 |
| `/slo`, `/[id]` | burn table, history table | compliance gauge, unscaled sparkline | 7 |
| `/protection`, `/[id]` | two-axis badges, evidence chain, run/drill lists | day-granular countdown | 7 |
| `/clusters`, `/[id]` | split agent/inventory pills, cert countdown, kinds list | duplicated aside bars, five donuts | 7 |
| `/clusters/[id]/inventory`, `/[r]` | URL filters, table, rollups | ECharts bar for a static list | 7 |
| `/infrastructure` | ledger, disk forecast, stacked usage, zone chart | separate CSS system → tokens; cold build stalls the screen | 7 |
| `/integrations`, `/github` | gallery, policy view, reconcile | donut repeating KPIs, decorative tick strip | 8 |
| `/onboarding`, `/[id]` | picker, state machine, plan | static stepper on every visit; findings never rendered | 8 |
| `/notification-*`, `/notifications` | rule cards, attempt timeline | donuts and static legends | 8 |
| `/admin` | role matrix with etags, grant form | loaded-rows-only stacked bar | 8 |

## Defects found during the audit (fixed in the phase that touches them)

- SLO detail: budget gauge divides a ratio by an absolute count (`app/slo/[sloId]/page.tsx`).
- Retry buttons wired to `() => undefined` on deployment and protection detail panels.
- Notification mark-read swallows errors; policy save notice lost on remount.
- Alert rules: "Cancel" on delete closes the whole inspector.
- Dashboard: a failed overview read renders as loading forever.
- Infrastructure: cold snapshot build can take 60–90 s with no single-flight; retry is dead when the cluster list fails.
- Lists (incidents, deployments, SLO, clusters, integrations, GitHub repos, onboarding sessions) read only the first 25 rows; KPIs computed from that page.
- `useApi` maps only 404 to "denied"; 403 shows a generic error.
- Undefined utilities: `bg-surface-subtle`, `accent-[var(--accent)]`.
