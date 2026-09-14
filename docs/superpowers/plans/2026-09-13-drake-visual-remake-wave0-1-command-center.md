# Drake Visual/UX Remake — Wave 0 + Wave 1 + Command Center Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Note on grain:** the design brief (§22) asks for a plan at "file and test level," and the requester asked for it short. Tasks below are sized at file + exact signature + exact test scenarios, not the skill's default 2-minute-step/full-code-block granularity. Every file, type, and test name is concrete and final — nothing here is a placeholder to fill in later.

**Goal:** Ship Wave 0 (visual baseline + data inventory), Wave 1 (design tokens, typography, shell, command palette phase 1), and the Command Center vertical slice of the Drake "Operational Canvas" remake — without touching the backend, data semantics, or security boundaries.

**Architecture:** Extend the existing token → status → `useResource` → `ChartFrame`/`states.tsx` architecture in place. No new state library, table engine, motion library, or UI kit. New shared visuals live under `src/components/data-viz/`, Command Center composition under `src/components/command-center/`, new pure view-model modules under `src/lib/view-models/` (first use of that folder — existing domain files in `src/lib/*.ts` are left as they are).

**Tech stack:** unchanged — Next 15.3 App Router, React 19, Tailwind 4 (`@theme inline`), ECharts 6 via the existing adapter, vitest + Testing Library, Playwright + `@axe-core/playwright`. No new dependency is added by this plan.

**Spec:** `docs/superpowers/specs/2026-09-13-drake-visual-ui-ux-remake-design.md`

## Global constraints

- No backend change, no new endpoint, no relaxed permission or RBAC check (brief §13.3, §21; ADR-0010).
- No new npm dependency without CTO sign-off (brief §19). None is added here; see **Decisions flagged for the CTO** below for the one deferred candidate (IBM Plex).
- `unknown` / `stale` / `partial` / `not_configured` / `permission-denied` / real `zero` stay distinct everywhere; never collapsed into each other or into "healthy" (brief §2.1; ADR-0011).
- No `/v1/overview` aggregate. Command Center stays a client-side composition of existing authorized endpoints (`apps/web/src/lib/overview.ts:1-14`).
- Every new data surface uses the existing `ChartFrame` contract or `components/ui/states.tsx` primitives for loading/empty/denied/stale/partial/error/unknown — no new ad hoc state markup.
- No horizontal page scroll above 390px; light/dark parity; zero axe critical/serious on Command Center (brief §4, §15).
- Visual QA is manual screenshots (desktop/1280/mobile, light/dark), not pixel-diff snapshots — this repo already decided against `toHaveScreenshot` (`apps/web/e2e/experience.spec.ts:14-17`) and this plan keeps that decision.
- All color/tone comes from `lib/design/status.ts` (`toneSpec`, `toneForHealth`, `toneForThreshold`) and `lib/design/tokens.ts`. No new inline hex value anywhere in a component.
- Small, reviewable commits per task, in the order below; no "rewrite everything" commit (brief §19).

## Decisions flagged for the CTO

1. **Obsidian sidebar in both themes.** Brief §6.2 wants PayFlow's dark, calm rail against a light workspace. Implemented as a new `--sidebar-*` token family declared with the **same** hex values inside both `:root` and `.dark` — the one deliberate exception to "every surface flips with the theme." Proceeding with this unless told otherwise; it's the largest structural token change in Wave 1.
2. **Typography scale is a token change, so it is global on landing.** `--text-title`/`--text-display`/`--text-section` feed `PageHeader` (`apps/web/src/components/shell/AppShell.tsx:170-207`), which every one of the 30 routes uses. Bumping the token in Wave 1 raises every page's `<h1>` immediately, not just Command Center's. This is intentional (brief §7.2 says the type is too small everywhere) and is covered by re-running the full existing route/axe suite (Task 1.4) before Command Center work starts — no per-route redesign happens yet, only the type-scale token.
3. **IBM Plex font — not doing it in this slice.** Brief §7.2 recommends it. Self-hosting a new type family means either a new package or new binary font assets fetched into the repo — the kind of call brief §19 says goes to the CTO before the wave starts, and swapping the product's typeface is a highly visible, hard-to-reverse brand decision on its own. Keeping self-hosted Inter (`apps/web/src/app/layout.tsx:18-34`) for Wave 0/1/Command Center. Flagging Plex as an open decision rather than silently deciding it.
4. **Exact color values.** Light-theme brand/status hexes are taken verbatim from brief §7.1. Dark-theme hexes start from the brief's four sample anchors and get nudged during implementation as needed to keep `design-system.test.ts`'s AA/3:1 assertions green — the brief itself says the dark critical/warning/stale/unknown separation "ayrıca test edilecektir" (will be separately tested), so exact final dark hexes are an implementation detail of Task 1.1, not a deviation from the brief.

## Wave 0 — Visual baseline & data inventory

### Task 0.1: Baseline screenshots

**Files:**
- Create: `apps/web/e2e/wave0-baseline.spec.ts` — manual-run only (`test.skip(!!process.env.CI, "baseline capture, not a CI gate")`), reusing the `signIn`/`setTheme` helpers already proven in `apps/web/e2e/experience.spec.ts:55-60`. Navigates to `/`, `/projects`, `/clusters`, `/incidents`, `/integrations` (the same `CRITICAL_ROUTES` set as `experience.spec.ts:44`) at 1920, 1280 and 390px, in light and dark, and calls `page.screenshot({ path: ..., fullPage: true })` into `apps/web/.wave0-baseline/<route>-<viewport>-<theme>.png`.
- Modify: `apps/web/.gitignore` — add `.wave0-baseline/`.

**Test:** this file *is* the tooling; verify by running it once against the real stack (`make up && bash scripts/e2e-setup.sh`, then `pnpm --filter @drake/web exec playwright test e2e/wave0-baseline.spec.ts`) and confirming 30 PNGs land on disk before any Wave 1 change is made.

### Task 0.2: Endpoint → UI → state matrix

**Files:**
- Create: `docs/superpowers/plans/2026-09-13-wave0-endpoint-ui-state-matrix.md` — a table of {route, endpoints it calls, `Resource`/`ChartStatus` states it handles, shared components used}, seeded from this plan's own research (Command Center's 6 sources in `apps/web/src/app/page.tsx:62-75`, `ChartFrame`'s 8 statuses in `apps/web/src/components/charts/ChartFrame.tsx:36-44`, the two parallel state systems found — `components/ui/states.tsx` + `lib/useResource.ts` on rebuilt routes vs. the older `components/state/DataState` + `components/catalog/primitives.useApi` still used by `/alerts` and `/deployments`).

**Test:** documentation only, reviewed alongside this plan; no automated check.

## Wave 1 — Design tokens, typography, shell, command palette

### Task 1.1: Token values

**Files:**
- Modify: `apps/web/src/app/globals.css`
- Modify: `apps/web/src/lib/design/tokens.ts`
- Modify: `apps/web/src/test/design-system.test.ts`

**Changes:**
- `:root`: `--brand: #0A5B3D` (forest), `--brand-accent: #18B566` (serpent), `--text-primary: #11231B` (ink), `--canvas: #F2F5F2`, `--surface-1: #FFFFFF` (paper); `--status-success: #078A66`, `--status-critical: #D14343`, `--status-warning: #B97809`, `--status-info: #2E6FD8`, `--status-unknown: #667085`; `--status-stale` stays `#8A6410` (already matches the brief).
- New sidebar family, declared with **identical** values in both `:root` and `.dark`: `--sidebar-canvas: #0B1511`, `--sidebar-surface-hover`, `--sidebar-surface-selected`, `--sidebar-border`, `--sidebar-text`, `--sidebar-text-muted`, `--sidebar-active-rail` (derived from `--brand-accent`).
- `.dark`: `--canvas: #07100C`, `--surface-1: #0D1A14`, `--surface-2: #14241C`, `--border-subtle: #294034`, `--text-primary: #EEF5F1`, `--brand: #35D07F`; status hues re-picked near the brief's light values and re-validated for contrast (exact hex finalized against the test below).
- `@theme inline` additions: `--color-sidebar`, `--color-sidebar-hover`, `--color-sidebar-selected`, `--color-sidebar-border`, `--color-sidebar-ink`, `--color-sidebar-ink-muted`; `--radius-canvas: 1rem` (16px, "dominant canvas" per brief §7.3); `--sidebar-width-expanded: 15.5rem` (248px); `--sidebar-width-collapsed: 4.5rem` (72px).
- `@theme inline` type-scale bump: `--text-display: 1.875rem` (30px), `--text-title: 1.375rem` (22px), `--text-section: 1.0625rem` (17px). `--radius-control` (8px) and `--radius-panel` (12px) already match the brief exactly — untouched.
- `apps/web/src/lib/design/tokens.ts`: update `TOKEN_NAMES`, `LIGHT_TOKENS`, `DARK_TOKENS` to match the new `globals.css` literals exactly (the existing mirror test fails first, by construction, until this file is edited — that's the intended TDD loop already built into the repo).

**Tests:**
- `design-system.test.ts`'s existing "TypeScript mirror matches globals.css exactly" test needs no logic change — it goes red the moment `globals.css` changes and green once `tokens.ts` is updated to match.
- Add `describe("sidebar tokens")` in `design-system.test.ts`: `sidebar-text`/`sidebar-text-muted` clear 4.5:1 on `sidebar-canvas`; `sidebar-active-rail` clears 3:1 on `sidebar-canvas`; the sidebar family's six values are byte-identical between the `:root` and `.dark` blocks (proves the rail truly does not reflow with theme).
- Re-run the full existing contrast `describe` blocks; adjust the new dark status hexes until every assertion is green (no test-file logic change needed there, only the token values).

### Task 1.2: Obsidian sidebar shell

**Files:**
- Modify: `apps/web/src/components/shell/Sidebar.tsx` — `bg-surface`→`bg-sidebar`, `text-ink-secondary`→`text-sidebar-ink-muted`, `hover:bg-surface-hover`→`hover:bg-sidebar-hover`, active `bg-surface-selected`→`bg-sidebar-selected` / `text-brand`→`text-sidebar-text`, active rail `bg-brand`→`bg-sidebar-active-rail`, `border-border`→`border-sidebar-border` throughout.
- Modify: `apps/web/src/components/shell/AppShell.tsx` — `aside` width classes `w-14`/`w-60` (lines 79) → arbitrary-value classes reading `--sidebar-width-collapsed`/`--sidebar-width-expanded`; mobile drawer (lines 89-102) gets `bg-sidebar` instead of inheriting the light surface, so it doesn't flash light on open.
- Modify: `apps/web/src/components/shell/Brand.tsx` — read current light/dark branching first; pin the sidebar's logo lockup to the dark-background wordmark/mark unconditionally (it always sits on obsidian now, independent of the app's own light/dark theme), likely via an explicit `variant` prop rather than relying on the ancestor `.dark` class.

**Tests:**
- Modify `apps/web/src/components/shell/AppShell.test.tsx`: assert the sidebar root carries `bg-sidebar` in both a `dark`-classed and a non-`dark`-classed render (proves the rail is theme-invariant); update any existing literal `w-14`/`w-60` assertions to the new width tokens.
- Re-run `apps/web/src/test/session-shell.test.tsx` unchanged — only classes/colors move, not markup or roles, so permission-aware nav filtering should still pass without edits.

### Task 1.3: Command palette, phase 1 (brief §8.1)

Only static page/action search merges with the existing authorized catalog search this wave — incident/alert/deployment/SLO indexing needs a backend search contract that does not exist yet and is out of scope (brief §8.1 explicitly phases this).

**Files:**
- Modify: `apps/web/src/components/shell/CatalogSearch.tsx` — merge a client-side static index built from `NAV_ITEMS` (`apps/web/src/lib/navigation.ts:161`) into the results, filtered through the same `hasPermission` check `Sidebar.tsx` already uses, so the palette never offers a page the sidebar itself would hide. Add a "recently visited" list (last 5, `sessionStorage`-backed, label sourced from the same crumb registry `Breadcrumbs.tsx` publishes). Rename the visible affordance and `aria-label`s from "Search catalog…" to "Search Drake…".
- Check first, then modify or create: search for an existing `CatalogSearch.test.tsx`/similar before adding a new file — extend it if found.

**Tests (new or extended file, `apps/web/src/test/command-palette.test.tsx` if none exists):**
- Typing a page name ("Incidents") surfaces a page result; Enter navigates to `/incidents`.
- A page result renders in a group distinct from a catalog-entity result (`data-testid="palette-group-pages"` vs `"palette-group-catalog"`).
- A nav item gated by `anyPermission` is not offered when `hasPermission` returns false for all of them — mirrors `Sidebar`'s own filter so the two never disagree.
- Recently-visited persists across a close/reopen in the same session (mock `sessionStorage`) and is capped at 5.
- Existing catalog-entity behavior (debounce, abort-on-keystroke, keyboard nav, no result leak on a 403) is unchanged.

### Task 1.4: Regression gate before Command Center work starts

No new files. Run, and fix anything the token/shell change broke:
- `pnpm --filter @drake/web typecheck`
- `pnpm --filter @drake/web lint`
- `pnpm --filter @drake/web test`
- `pnpm --filter @drake/web exec playwright test e2e/experience.spec.ts e2e/zz-inventory-a11y.spec.ts` (needs `make up` + `scripts/e2e-setup.sh` per the header of `playwright.config.ts`)
- Manual screenshots of the same routes/viewports/themes as Task 0.1, compared by eye against the Wave 0 baseline.

**Test:** the run itself is the gate — no new test code.

## Wave 2 — Command Center

Current `/` (`apps/web/src/app/page.tsx`) already implements "triage strip → ranked attention list → standing state" from Sprint 13. This wave replaces the 5-tile `TriageStrip` (brief §9.3 explicitly forbids five equal KPI cards) with an `Operational verdict` panel, adds the two visuals the brief calls for that don't exist yet (`Correlation timeline`, `Health matrix` as a matrix rather than a donut, `Capacity risk`), and promotes the existing empty-state coverage `<dl>` into an always-visible `Evidence coverage` panel. No new backend endpoint; two new client-side list fetches (deployments, alerts) that already exist and are already used elsewhere in the app.

### Task 2.1: Operational verdict (replaces `TriageStrip`)

**Files:**
- Create: `apps/web/src/lib/view-models/verdict.ts`:
  ```ts
  export interface OperationalVerdict {
    headline: string;
    criticalCount: number;
    warningCount: number;
    affectedScope: { projects: number; services: number; clusters: number };
    sourcesAnswered: number;
    sourcesTotal: number;
    oldestSuspectEvidence: { label: string; asOf: string | null } | null;
  }
  export function buildVerdict(
    attention: AttentionItem[],
    sources: { label: string; resource: Resource<unknown> }[],
  ): OperationalVerdict
  ```
  Pure function: headline text per brief §9.3 ("N critical paths need attention" / "Nothing is currently flagged, N of M sources answered"); counts via the existing `tallyByTone`; affected scope via distinct `context` strings among critical/warning items; oldest evidence via the minimum `asOf` among items still needing attention (ignoring `null`).
- Modify: `apps/web/src/app/page.tsx` — remove the `TriageStrip` function and its call site; add `<VerdictPanel verdict={buildVerdict(attention, sources)} onRefresh={reloadAll} refreshing={refreshing} />`.
- Create: `apps/web/src/components/command-center/VerdictPanel.tsx` — uses `Panel` with the new `radius="canvas"` prop (add this prop to `apps/web/src/components/ui/Panel.tsx`, default `"panel"` so every other caller is unaffected); one `text-display` headline, tone-colored; inline critical/warning counts and affected-scope chips as links (reusing `toneSpec`) instead of five equal tiles.

**Tests:**
- Create `apps/web/src/lib/view-models/verdict.test.ts`: headline text for zero-attention vs. critical-present vs. warning-only; affected-scope counts de-duplicate by `context`; oldest-evidence ignores `null` `asOf`; `sourcesAnswered`/`sourcesTotal` pass through unchanged.
- Modify `apps/web/src/test/command-center.test.tsx`: update the four existing tests' selectors from `triage-strip`/`triage-clusters` to `VerdictPanel`'s new test ids (`verdict-panel`, `verdict-critical-count`, etc.) while keeping every existing assertion (dash-not-zero on a denied cluster source, "Agent disconnected" wording, "not a statement that the platform is healthy" empty copy) — these are the brief's own non-negotiables (§2.1, §9.3), not incidental to the old markup.

### Task 2.2: Correlation timeline

**Files:**
- Create: `apps/web/src/lib/view-models/timeline.ts`:
  ```ts
  export type TimelineEventKind =
    | "incident_opened" | "incident_resolved"
    | "alert_firing" | "alert_resolved"
    | "deployment";
  export interface TimelineEvent {
    id: string; kind: TimelineEventKind; tone: StatusTone;
    at: string; label: string; href: string;
  }
  export interface TimelineLane {
    key: string; label: string; events: TimelineEvent[]; historyAvailable: boolean;
  }
  export function incidentEvents(incidents: IncidentSummary[]): TimelineEvent[]
  export function alertEvents(alerts: AlertInstance[]): TimelineEvent[]
  export function deploymentEvents(deployments: DeploymentRow[]): TimelineEvent[]
  export function buildTimeline(
    incidents: IncidentSummary[], alerts: AlertInstance[], deployments: DeploymentRow[],
  ): TimelineLane[]
  ```
  `buildTimeline` always emits a `cluster-service-health` lane with `historyAvailable: false` and zero events — brief §9.3 forbids fabricating a state-transition history that does not exist, so this lane is explicit rather than silently absent or silently flat.
- Modify: `apps/web/src/app/page.tsx` — add `useResource<DeploymentPage>(deploymentListPath({ startedWithin: "24h" }))` and `useResource<{items: AlertInstance[]; ...}>(alertListPath({ window: "24h" }))` (confirm the exact `/v1/alerts` list response shape from `apps/web/src/app/alerts/page.tsx` before wiring — it already fetches this list today, just via the older `useApi` hook). Both are additive reads of endpoints already used elsewhere; refreshed on the existing `REFRESH_MS`.
- Create: `apps/web/src/components/data-viz/OperationalTimeline.tsx` — DOM-based (not ECharts, matching `InlineBars.tsx`'s "small operational visuals don't need the 250kB engine" convention), one row per `TimelineLane`. Each event is a `<button>`/`Link` with an accessible name ("Incident opened, 14:02 UTC, checkout-api"); a `historyAvailable: false` lane renders a muted "history unavailable" label, never an empty or flat track. Ends with a `<details>`-disclosed `<table>` listing every event as a row (same numbers as the track, per the `ChartFrame` contract's own rule at `ChartFrame.tsx:16`).

**Tests:**
- Create `apps/web/src/lib/view-models/timeline.test.ts`: each `*Events` mapper produces the right `kind`/`tone` from fixture rows; `buildTimeline` always emits the service/cluster lane with `historyAvailable:false` and zero events even when other lanes show a live incident (proves no fabrication); events within a lane sort ascending by `at`.
- Create `apps/web/src/components/data-viz/OperationalTimeline.test.tsx`: every rendered event exposes an accessible name; the unavailable-history lane renders its literal state text and zero event buttons; the table disclosure lists exactly as many rows as events rendered on the track; `Tab` reaches every event in chronological order.

### Task 2.3: Attention queue (extract + root-cause grouping)

**Files:**
- Modify: `apps/web/src/lib/overview.ts` — add `export function groupByRootCause(items: AttentionItem[]): AttentionItem[][]`, grouping items that share a `context` (e.g., a cluster's agent + inventory rows) — a display grouping only, documented as not a new causality claim, matching the file's existing comment style.
- Create: `apps/web/src/components/command-center/AttentionQueue.tsx` — move the existing `NeedsAttention` function here verbatim (brief's suggested shared name), same props, rendering `groupByRootCause(items)` with a subtle divider between groups instead of one flat `<ul>`.
- Modify: `apps/web/src/app/page.tsx` — import `AttentionQueue` in place of the inline `NeedsAttention`.

**Tests:**
- Modify `apps/web/src/test/command-center.test.tsx`: the "disconnected agent" test's two attention rows (`agent disconnected`, `inventory stale`) must render inside the same group (e.g. `within(screen.getByTestId("attention-group-c1"))`).
- Add a case with two unrelated critical items from different clusters landing in two separate groups, so grouping never over-merges unrelated failures.

### Task 2.4: Health matrix

**Files:**
- Create: `apps/web/src/lib/view-models/health-matrix.ts`:
  ```ts
  export interface HealthMatrixCell {
    projectKey: string; environmentKey: string;
    services: { serviceKey: string; tone: StatusTone }[];
  }
  export function buildHealthMatrix(services: ServiceHealthRow[]): HealthMatrixCell[]
  ```
  Groups the same `/v1/service-health/services` payload the page already fetches, by project → environment; a cell's tone is the worst among its services via `compareTone`.
- Create: `apps/web/src/components/data-viz/HealthMatrix.tsx` — CSS-grid matrix (rows = environments, cells = tone-colored squares with counts) above 1024px; below it, a `<details>`-per-project disclosure list instead of a horizontal-scrolling grid (brief §9.4).
- Modify: `apps/web/src/app/page.tsx` — add `<HealthMatrix cells={buildHealthMatrix(services.data?.items ?? [])} status={resourceStatus(services)} />`; keep the existing `ServiceHealthPanel` donut as the compact rail summary (brief's wireframe shows both a full-width health map and a side-rail element).

**Tests:**
- Create `apps/web/src/lib/view-models/health-matrix.test.ts`: groups by project/environment correctly; a cell's tone is the worst service's tone even when most services are healthy; behavior for a project with zero in-scope services is asserted explicitly (not a silently-omitted phantom cell).
- Create `apps/web/src/components/data-viz/HealthMatrix.test.tsx`: one cell per environment with the correct tone; a `compact` prop (not a real media query, matching how this codebase tests responsive components) renders the disclosure list; empty `cells` renders `NotConfiguredState`/`EmptyState`, never an empty grid that reads as "everything healthy."

### Task 2.5: Capacity risk board

Real data found for this slice: `Cluster.operational` plus each cluster's `/v1/clusters/{id}/inventory/summary` response already carries `agent.certificate_not_after` / `agent.certificate_expiry_warning` and `persistent_volume_claims: {total,healthy,degraded,unhealthy,unknown}` (fixture at `apps/web/src/test/command-center.test.tsx:52-80`). No new endpoint needed; no client-derived expiry threshold is invented — the tone comes straight from the backend's own `certificate_expiry_warning` flag.

**Files:**
- Create: `apps/web/src/lib/view-models/capacity-risk.ts`:
  ```ts
  export interface CapacityRiskItem {
    key: string; clusterId: string; clusterName: string;
    kind: "certificate" | "pvc"; label: string; tone: StatusTone;
    detail: string; deadline?: string | null; href: string;
  }
  export function certificateRiskItems(
    clusters: Cluster[], summaries: Map<string, InventorySummary>,
  ): CapacityRiskItem[]
  export function pvcRiskItems(
    clusters: Cluster[], summaries: Map<string, InventorySummary>,
  ): CapacityRiskItem[]
  ```
- Create: `apps/web/src/lib/clusterSummaries.ts` — `useClusterInventorySummaries(clusters: Cluster[]): Map<string, Resource<InventorySummary>>`, one `useResource` per cluster id, fetched once and shared. During implementation, check whether `FleetCounts` (`apps/web/src/app/page.tsx:639-694`) can consume this same map instead of its own per-row fetch (removing today's N+1 duplication, per brief §16); if that refactor risks the well-tested `FleetPanel`, leave `FleetPanel` unchanged and accept two independent per-cluster fetch sets rather than destabilize it — record the decision made in the PR description.
- Create: `apps/web/src/components/data-viz/CapacityRiskBoard.tsx` — ranked list (worst tone first via `compareTone`), reusing the `Countdown` primitive from `apps/web/src/components/charts/visuals.tsx` for certificate deadlines. Empty state says "No certificate or PVC risk reported by the sources checked" — never "all capacity healthy" — and separately lists clusters with no inventory summary as "forecast unavailable."
- Modify: `apps/web/src/app/page.tsx` — wire `CapacityRiskBoard` from `useClusterInventorySummaries(clusters.data?.clusters ?? [])`.

**Tests:**
- Create `apps/web/src/lib/view-models/capacity-risk.test.ts`: certificate item appears only when `certificate_expiry_warning` is true; PVC item appears only when `degraded + unhealthy > 0`; a cluster with no summary produces neither item and is reported separately as unassessed.
- Modify `apps/web/src/test/command-center.test.tsx`: add a `certificate_expiry_warning: true` fixture case asserting the board surfaces it with a countdown; add a zero-risk case asserting the honest empty copy.

### Task 2.6: Evidence coverage (always visible, not empty-state-only)

**Files:**
- Create: `apps/web/src/components/command-center/EvidenceCoverage.tsx` — takes the same `sources` array already built in `page.tsx`, classifies each via `resourceStatus()` (`apps/web/src/lib/useResource.ts:135-143`) plus a freshness check where the resource exposes one, into `configured-fresh | configured-stale | not-configured | permission-denied | unavailable`; always renders this breakdown, regardless of whether the attention list is empty.
- Modify: `apps/web/src/components/command-center/AttentionQueue.tsx` — remove the checked/not-checked `<dl>` from the empty branch (now redundant with `EvidenceCoverage`), keep the "this is not a statement the platform is healthy" sentence, since that copy is about the empty state specifically.
- Modify: `apps/web/src/app/page.tsx` — render `<EvidenceCoverage sources={sources} />` as its own always-visible panel per the brief's wireframe (§9.2).

**Tests:**
- Create `apps/web/src/components/command-center/EvidenceCoverage.test.tsx`: one source per state renders the correct label/tone; the component never renders the literal phrase "all systems healthy" / "all sources healthy" when any source is denied or unavailable — assert via `queryByText(/all (systems|sources) healthy/i)` returning null.
- Modify `apps/web/src/test/command-center.test.tsx`: the existing "dash, never zero" denied-cluster test gains an assertion that `EvidenceCoverage` also reports clusters as "permission required"; the existing empty-state test's checked/not-checked assertions move from `attention-empty` to the new `evidence-coverage` test id.

### Task 2.7: Responsive reflow

**Files:**
- Modify: `apps/web/src/app/page.tsx` — restructure the grid per brief §9.4: below 1024px, order becomes verdict → attention queue → timeline summary; health matrix already collapses via Task 2.4; on mobile, a "View full timeline" trigger opens a focus-trapped dialog (reusing `useDismissable`/`useScrollLock` from `apps/web/src/components/ui/overlay.tsx`, the same pattern already proven by `AppShell`'s mobile nav drawer) instead of adding a new route.

**Tests:**
- Create `apps/web/e2e/command-center-responsive.spec.ts`: at 390px, the attention queue precedes the full timeline in DOM/tab order; the timeline dialog trap-focuses and returns focus to its trigger on Escape (mirrors the existing mobile-drawer assertion); at 1024px the health matrix renders as a disclosure list, at 1280px+ as a grid.
- Confirm (or extend) `apps/web/e2e/zz-inventory-a11y.spec.ts` runs its axe check against `/` at 390 and 1280 in both themes — Command Center is already in `experience.spec.ts`'s `CRITICAL_ROUTES` (line 44); verify the a11y spec covers it too, not only the structural spec.

## Definition of done for this plan

- Wave 0 baseline captured before any Wave 1 token change lands.
- Every new component uses `ChartFrame` / `components/ui/states.tsx` / `toneSpec` — no new inline color, no new ad hoc empty-state copy pattern.
- `pnpm --filter @drake/web typecheck && lint && test` green; `playwright test e2e/experience.spec.ts e2e/zz-inventory-a11y.spec.ts e2e/command-center-responsive.spec.ts` green against the real stack.
- Manual screenshots — Command Center at 1920/1440 (desktop), 1280, 390 (mobile), light + dark, 8 images — attached to the PR(s) for CTO comparison against brief §9.2.
- No new dependency added; IBM Plex left an open, explicit decision, not silently made.
- `TriageStrip` and the old empty-state-only coverage `<dl>` are fully retired — no screen left half on old components, half on new (brief §20).
