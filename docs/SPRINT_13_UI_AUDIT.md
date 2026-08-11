# Sprint 13 — UI audit

The evidence this redesign was built from. Written before the work, updated as
each route landed, and kept because the "current problems" column is the list
somebody will want when they ask why a screen looks the way it does now.

## What was there

Thirty routes of Next 15 App Router + Tailwind 4. The data semantics were
already good — `loading`, `stale`, `unknown`, `not_configured` and
`permission-denied` were distinct types, and no screen fabricated a value. The
*visual* layer was the problem:

| Area | Finding |
|---|---|
| Palette | `#4f46e5` indigo, unrelated to the brand. Nothing in the theme was sampled from the logo. |
| Brand | `assets/brand` masters pixel-differed from the authoritative artwork; only a square icon crop ever shipped, no wordmark. |
| Containers | One `Card` for everything. A dense table and a single number got the same frame. |
| Charts | Two hand-rolled inline SVGs. No axis, grid, tooltip, legend or unit. |
| Overview | Six hard-coded "not configured" tiles plus a card describing the UI's own foundation. |
| Shell | Fixed 64px rail, no collapse, flat route list; breadcrumb printed "Organization / <section>" plus a meaningless "detail" chip on every screen. |
| Theme | Two-state toggle. Choosing light or dark once stopped the app following the OS forever. |
| Type | `text-xl` page titles and ad-hoc sizes per screen; uppercase table headers. |
| Nav | Two permanently disabled "soon" entries. |

## Route matrix

`status` is where each route ended up. "Rebuilt" means the screen was
restructured around the question it answers; "reframed" means it kept its
structure and moved onto the shared frame, tokens, states and type scale.

| route | purpose | primary data | current UX problems | intended visualization | states | responsive risk | status |
|---|---|---|---|---|---|---|---|
| `/` | Where is the problem? | `catalog/context`, `alerts/summary`, `incidents`, `clusters`, `service-health/services`, `integrations/health` | Six placeholder cards; a card describing the UI itself | Triage strip → ranked attention list → standing state | loading, denied per-source, error, empty-with-provenance | tile grid at 390 | **rebuilt** |
| `/projects` | Which project needs me? | `projects` | Card list; Apply-button filters that lied about current state | Scannable table + criticality composition | loading, denied, empty, filtered-empty | low-priority columns drop < lg | **rebuilt** |
| `/projects/{id}` | What is this and is it well? | `projects/{id}`, `environments` | Provenance buried; dependencies and workloads conflated | Capabilities → signals → composition | loading, denied, not-found, error | 2-col → 1-col | **rebuilt** |
| `/projects/{id}/environments/{id}` | Environment detail | `environments/{id}`, services | Uppercase headers, ad-hoc type | Service list + dashboard | as page | — | reframed |
| `…/services/{id}` | Is this service well? | `services/{id}`, `telemetry/query` | Duplicate "Golden signals" headings; axis-less SVG | ECharts golden signals → binding → capabilities | loading, denied, not-found, stale, throttled, unavailable, no-data | chart min-height | **rebuilt** |
| `/service-health` | Fleet of bindings | `service-health/services` | — | Table | loading, denied, empty | — | reframed |
| `/service-health/{id}` | One binding | `bindings/{id}/health`, `/series` | Hand-rolled sparkline | Series chart + sections | stale, partial, not-configured | — | reframed |
| `/clusters` | Fleet connection | `clusters` | Connection and health read as one verdict | Table with SEPARATE agent / inventory columns | loading, denied, empty | table scroller | **rebuilt** |
| `/clusters/{id}` | One cluster | `clusters/{id}`, `inventory/summary` | — | Agent + freshness + rollups | disconnected, stale, reconciling, unknown | — | reframed |
| `/clusters/{id}/inventory` | Find a resource | `inventory/resources`, `/summary` | Filters lost on reload; no distribution | Sorted bar by kind + health composition + dense table | loading, denied, not-found, empty, missing-hidden notice | priority columns, sticky header | **rebuilt** |
| `/clusters/{id}/inventory/{id}` | One resource | `resources/{id}` | — | Allowlisted detail | not-found | long names wrap | reframed |
| `/incidents`, `/incidents/{id}` | Triage | `incidents` | Uppercase headers | Table + timeline | empty, denied, error | — | reframed |
| `/alerts`, `/alerts/{id}` | Firing now | `alerts`, `alerts/summary` | Uppercase headers | Table + label chips | empty, denied, error | — | reframed |
| `/slo`, `/slo/{id}` | Objectives | `slo`, `/evaluations` | — | Burn table | insufficient_data ≠ healthy | — | reframed |
| `/deployments`, `/deployments/{id}` | What changed | `deployments` | Uppercase headers | Table + revisions | unverified ≠ failed | — | reframed |
| `/protection`, `/protection/{id}` | Backup posture | `protection/*` | Uppercase headers | Summary + runs | never-tested ≠ failed | — | reframed |
| `/onboarding`, `/onboarding/{id}` | Admit a project | `onboarding/*` | — | Session steps + findings | gated repo refusal | wide forms | reframed |
| `/integrations` | Provider health | `integrations/health` | — | Operational list, not marketing cards | configured / not / degraded | table scroller | reframed |
| `/integrations/github` | App scope | `integrations/github/*` | — | Installation + repository policy | not-configured, reconcile-required, gated | — | reframed |
| `/notification-policies`, `/notifications`, `/notification-deliveries` | Routing | `notification-*` | — | Tables + policy form | denied, empty | wide form | reframed |
| `/admin` | Roles, grants, audit | `roles`, `grants`, `audit-events` | Denied branch had NO `h1` — a hole in the outline | Tabs + tables | denied (with heading) | — | **rebuilt** |
| `/not-found`, `/error` | System states | — | Next's defaults | Named, distinct pages | 404 ≠ render failure | — | **added** |

## Stack, as found

- Next 15.5, React 19, Tailwind 4 (`@theme inline`, no config file).
- `lucide-react` for icons — consistent, kept.
- **No** ECharts, TanStack Table, Motion or Radix, despite the sprint brief
  describing them as existing choices. ECharts was added (explicitly
  mandated); the rest were not, because the existing stack already covers what
  the screens need and a second table framework would be a second way to do
  everything.
- Playwright + axe already wired; vitest + Testing Library for units.

## Decisions worth recording

**Why no `/v1/overview` endpoint.** The Command Center composes six existing
authorized endpoints in the browser. Each is already the authority on its own
state; a new aggregate would have to re-derive all of it, and a second
implementation of "is this cluster stale" is how two screens start disagreeing.

**Why the empty state is long.** "Nothing is flagged" is not "the platform is
healthy". The panel names the sources it checked and the ones it could not,
because a green tick that actually means "four of your six sources are not
configured" is the most dangerous thing a monitoring product can render.

**Why sorted bars instead of pies.** Drake's distributions are long-tailed —
twenty resource kinds, most of them small. Nobody can rank twenty wedges.

**Why the tokens are duplicated in TypeScript.** ECharts draws to canvas and
cannot resolve `var(--series-1)`; passing one silently falls back to ECharts'
own palette, which is how four series ended up sharing a colour. A unit test
parses `globals.css` and fails if the mirror drifts.

## Defects the redesign surfaced

Found by the gates rather than by looking:

1. **Four series, one colour.** `var()` in a canvas option. Caught by writing
   the chart test.
2. **One `setInterval` per timestamp.** A dense table meant hundreds of timers.
   Caught by an existing concurrency test that asserts no stray timers.
3. **Contrast 4.42:1** on the active nav entry in dark mode. Caught by axe;
   now also a unit test.
4. **`<dl>` containing links** on the Command Center. Caught by axe.
5. **47px of sideways page scroll** at 390px. A flex child's default
   `min-width: auto` let a table grow past its scroller; `contain: paint` stops
   the width propagating to the root.
6. **`/admin` denied branch had no `h1`** — the one branch an unprivileged
   caller actually sees.
