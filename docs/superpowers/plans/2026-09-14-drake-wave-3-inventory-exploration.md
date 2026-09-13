# Drake Wave 3 Inventory Exploration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one evidence-preserving drill-down journey from project or cluster risk to service or inventory-resource evidence, without changing Drake's backend, authorization, dependencies, or deployment state.

**Architecture:** Existing authorized collection endpoints are composed in route-owned data loaders, then transformed by pure `src/lib/view-models/` functions into portfolio, topology, lane, matrix, and composition models. Small DOM/CSS visuals live in `src/components/data-viz/`; route-specific composition lives in `src/components/features/`. Wave 3A, 3B, and 3C are sequential review gates on one shared operational hierarchy.

**Tech Stack:** Next.js 15.3 App Router, React 19, TypeScript 5.7, Tailwind 4, existing ECharts 6 adapter, Vitest + Testing Library, Playwright + `@axe-core/playwright`, Node 24 from `.nvmrc`.

**Spec:** `docs/superpowers/specs/2026-09-14-drake-wave-3-inventory-exploration-design.md`

## Global Constraints

- Begin from commit `3d16c38`; preserve Wave 2 commits `ca87a95`, `292cccc`, and `adc6410` unchanged.
- Use Node 24. Before the first test run, verify `node --version` begins with `v24.`.
- No backend, database, migration, worker, authorization, API-contract, or security change.
- No new npm dependency, UI framework, chart library, table engine, state library, or motion library.
- Browser traffic remains same-origin `/v1`; no provider URL or provider query vocabulary enters browser code.
- Keep criticality, lifecycle, health, agent connection, inventory freshness, and evidence verification as separate dimensions.
- Preserve `unknown`, `stale`, `partial`, `not_configured`, `denied`, real zero, and `not-applicable` as distinct states.
- A project or cluster collection that still has a next page is incomplete. It cannot make an estate-wide verdict or pretend its client-side sort covers the whole estate.
- Use the backend maximum page size of 100 for Wave 3 collection reads. Additional pages load only through an explicit user action; no automatic unbounded page crawl.
- Preserve current URL-backed search/filter/range behavior and browser Back restoration.
- Keep Secrets and ConfigMaps absent from inventory charts, filters, tables, detail links, payload assertions, and screenshots.
- Do not invent health thresholds, capacity thresholds, forecast values, dependency edges, or causality claims.
- Retain existing `DashboardRenderer` query concurrency, cancellation, lazy ECharts loading, stale last-good behavior, chart tables, and correlation IDs.
- Automated responsive gates cover 390, 768, 1024, 1280, 1440, and 1920 px in light and dark; revision screenshots cover the required representative 390, 1024, 1280, and 1920 px set.
- No production, staging, or `sslip.io` deploy during this plan.
- Every task follows red -> green -> refactor: write a focused failing test, confirm the expected failure, implement the smallest coherent behavior, rerun the focused test, then commit.
- Do not change unrelated lint/test debt. Report it with a clean-baseline comparison if it blocks a gate.

---

## File Structure

### New shared files

- `apps/web/src/lib/useProgressiveCollection.ts` — cancellable, explicit user-driven collection pagination with completeness state.
- `apps/web/src/lib/useProgressiveCollection.test.tsx` — first page, next page, scope reset, abort, and partial-error tests.
- `apps/web/src/lib/view-models/portfolio-risk.ts` — project criticality × observed-health model.
- `apps/web/src/lib/view-models/portfolio-risk.test.ts` — aggregation and incomplete-evidence tests.
- `apps/web/src/lib/view-models/scope-health.ts` — project topology and environment service-lane models.
- `apps/web/src/lib/view-models/scope-health.test.ts` — topology, worst-state, external-runtime, and unbound-state tests.
- `apps/web/src/lib/view-models/service-context.ts` — time-windowed incident/deployment annotations.
- `apps/web/src/lib/view-models/service-context.test.ts` — range, ordering, and non-causality data tests.
- `apps/web/src/lib/view-models/cluster-visibility.ts` — connection × freshness grouping.
- `apps/web/src/lib/view-models/cluster-visibility.test.ts` — raw-state preservation and completeness tests.
- `apps/web/src/lib/view-models/inventory-exploration.ts` — resource-class rollups and safe inventory filter links.
- `apps/web/src/lib/view-models/inventory-exploration.test.ts` — health composition and URL tests.
- `apps/web/src/components/data-viz/ProjectRiskMap.tsx` — accessible desktop matrix and narrow disclosure.
- `apps/web/src/components/data-viz/ProjectRiskMap.test.tsx` — interaction, partial state, and accessibility semantics.
- `apps/web/src/components/data-viz/ClusterVisibilityMatrix.tsx` — accessible connection × freshness matrix.
- `apps/web/src/components/data-viz/ClusterVisibilityMatrix.test.tsx` — desktop/narrow and raw-state tests.
- `apps/web/src/components/data-viz/InventoryCompositionBoard.tsx` — aligned resource-class composition bars.
- `apps/web/src/components/data-viz/InventoryCompositionBoard.test.tsx` — exact counts, links, and zero-state tests.
- `apps/web/src/components/features/catalog/ServiceHealthLane.tsx` — one reusable service evidence row.
- `apps/web/src/components/features/catalog/ProjectTopology.tsx` — environment lanes composed from `ServiceHealthLane`.
- `apps/web/src/components/features/catalog/ProjectTopology.test.tsx` — hierarchy, links, and absent-evidence tests.
- `apps/web/src/components/features/service/ServiceContextTimeline.tsx` — incident/deployment context using the existing operational timeline.
- `apps/web/src/components/features/service/ServiceContextTimeline.test.tsx` — source-state and accessible event tests.
- `apps/web/src/components/features/inventory/InventoryRiskSummary.tsx` — problem-first cross-filter links above the inventory table.
- `apps/web/src/components/features/inventory/InventoryRiskSummary.test.tsx` — URL-filter target and missing-resource tests.
- `apps/web/e2e/inventory-exploration.spec.ts` — connected route journey, URL, responsive, keyboard, axe, and overflow gate.
- `apps/web/e2e/wave3-visual-preview.spec.ts` — manual, revision-specific Wave 3 screenshot capture.

### Existing files modified

- `apps/web/src/lib/serviceHealth.ts` — export a typed list-path builder with `limit` and `offset`.
- `apps/web/src/app/projects/page.tsx` — portfolio risk map, explicit completeness, map-to-table cross-filter, project cursor continuation.
- `apps/web/src/app/projects/[projectId]/page.tsx` — verdict-led project topology and standing-state reflow.
- `apps/web/src/app/projects/[projectId]/environments/[environmentId]/page.tsx` — migrate legacy cards to service health lanes and current state primitives.
- `apps/web/src/app/projects/[projectId]/environments/[environmentId]/services/[serviceId]/page.tsx` — time-related incident/deployment evidence under golden signals.
- `apps/web/src/app/clusters/page.tsx` — visibility matrix, URL cross-filter, cursor continuation.
- `apps/web/src/app/clusters/[clusterId]/page.tsx` — visibility/headroom verdict and aligned inventory compositions.
- `apps/web/src/app/clusters/[clusterId]/inventory/page.tsx` — interactive problem-first summary and preserved dense table.
- `apps/web/src/app/clusters/[clusterId]/inventory/[resourceId]/page.tsx` — current Panel/state hierarchy for terminal evidence.
- `apps/web/src/test/catalog-screens.test.tsx` — route-level project and cluster collection assertions.
- `apps/web/src/test/inventory-screens.test.tsx` — cluster, inventory, and resource detail assertions.
- `apps/web/e2e/catalog.spec.ts` — hierarchy and authorization journey assertions.
- `apps/web/e2e/metrics.spec.ts` — annotated service signal assertions.
- `apps/web/e2e/zz-inventory-a11y.spec.ts` — extend existing real-agent accessibility route set to inventory resource detail.

---

## Task 0: Freeze the Wave 3 visual baseline

**Files:**

- Create: `apps/web/e2e/wave3-visual-preview.spec.ts`

**Interfaces:**

- Consumes: real local fake-OIDC/API/Postgres/Redis/telemetry stack and, for populated inventory detail, the optional real agent fixture.
- Produces: revision-labelled PNGs below `apps/web/.visual-preview/<label>/`; the directory is already ignored by the repository.

- [ ] **Step 1: Write the manual capture spec**

Use static routes plus IDs resolved from authorized API responses. Do not hard-code fixture UUIDs.

```ts
const VIEWPORTS = [
  { name: "1920", width: 1920, height: 1080 },
  { name: "1280", width: 1280, height: 900 },
  { name: "1024", width: 1024, height: 900 },
  { name: "390", width: 390, height: 844 },
] as const;

type RouteTarget = { slug: string; path: string; ready: string };

async function resolveWave3Routes(page: Page): Promise<RouteTarget[]> {
  const projects = await (await page.request.get("/v1/projects?limit=100")).json() as {
    projects: { id: string }[];
  };
  const project = projects.projects[0];
  expect(project, "an authorized project is required for Wave 3 capture").toBeTruthy();
  const environments = await (
    await page.request.get(`/v1/projects/${project.id}/environments?limit=100`)
  ).json() as { environments: { id: string }[] };
  const environment = environments.environments[0];
  expect(environment, "an authorized environment is required").toBeTruthy();
  const services = await (
    await page.request.get(
      `/v1/projects/${project.id}/environments/${environment.id}/services?limit=100`,
    )
  ).json() as { services: { id: string }[] };
  const service = services.services[0];
  expect(service, "an authorized service is required").toBeTruthy();

  const clusters = await (await page.request.get("/v1/clusters?limit=100")).json() as {
    clusters: { id: string }[];
  };
  const cluster = clusters.clusters[0];
  expect(cluster, "an authorized cluster is required").toBeTruthy();
  const inventory = await (
    await page.request.get(`/v1/clusters/${cluster.id}/inventory/resources?limit=100`)
  ).json() as { resources: { id: string }[] };

  const targets: RouteTarget[] = [
    { slug: "projects", path: "/projects", ready: "project-list" },
    { slug: "project", path: `/projects/${project.id}`, ready: "environment-list" },
    {
      slug: "environment",
      path: `/projects/${project.id}/environments/${environment.id}`,
      ready: "service-list",
    },
    {
      slug: "service",
      path: `/projects/${project.id}/environments/${environment.id}/services/${service.id}`,
      ready: "dashboard-service-golden-signals-v1",
    },
    { slug: "clusters", path: "/clusters", ready: "cluster-list" },
    { slug: "cluster", path: `/clusters/${cluster.id}`, ready: "agent-card" },
    {
      slug: "inventory",
      path: `/clusters/${cluster.id}/inventory`,
      ready: "resource-rows",
    },
  ];
  const resource = inventory.resources[0];
  if (resource) {
    targets.push({
      slug: "inventory-resource",
      path: `/clusters/${cluster.id}/inventory/${resource.id}`,
      ready: "health-card",
    });
  }
  return targets;
}
```

The spec must start with:

```ts
test.skip(!!process.env.CI, "Wave 3 visual evidence is a manual gate");
const OUT_DIR = process.env.DRAKE_VISUAL_OUT_DIR ?? ".visual-preview/wave3-current";
```

- [ ] **Step 2: Verify the spec lists every approved route**

Run:

```bash
rg -n "projects|environments|services|clusters|inventory" apps/web/e2e/wave3-visual-preview.spec.ts
```

Expected: targets for Projects, project detail, environment detail, service detail, Clusters, cluster detail, Inventory, and inventory resource detail.

- [ ] **Step 3: Commit only the reusable capture spec**

```bash
git add apps/web/e2e/wave3-visual-preview.spec.ts
git commit -m "test(web): add Wave 3 visual evidence capture"
```

This commit changes test tooling only; its application UI remains byte-for-byte the `3d16c38` baseline.

- [ ] **Step 4: Capture the immutable before set from the Task 0 commit**

At execution time, use `superpowers:using-git-worktrees` to create an isolated worktree pinned to the Task 0 commit. This makes the capture spec available while keeping application source at the approved Wave 3 baseline. Run the spec there with the same local dependencies and a distinct output label:

```bash
DRAKE_VISUAL_OUT_DIR=.visual-preview/wave3-before pnpm --filter @drake/web exec playwright test e2e/wave3-visual-preview.spec.ts
```

Expected: light/dark screenshots at all four widths; no file under `.wave0-baseline`, `wave1`, or any `wave2-*` directory changes.

---

## Task 1: Add bounded progressive collection loading

**Files:**

- Create: `apps/web/src/lib/useProgressiveCollection.ts`
- Create: `apps/web/src/lib/useProgressiveCollection.test.tsx`
- Modify: `apps/web/src/lib/serviceHealth.ts`

**Interfaces:**

- Consumes: `apiGet`, `ApiError`, a first path, and a page adapter.
- Produces:

```ts
export interface PageSlice<Item> {
  items: Item[];
  nextPath: string | null;
  total?: number | null;
}

export interface ProgressiveCollection<Item> {
  items: Item[];
  total: number | null;
  complete: boolean;
  loading: boolean;
  refreshing: boolean;
  loadingMore: boolean;
  denied: boolean;
  notFound: boolean;
  error: string | null;
  correlationId?: string;
  loadMore: () => void;
  reload: () => void;
}

export function useProgressiveCollection<Page, Item>(options: {
  firstPath: string;
  pageToSlice: (page: Page, loadedCount: number) => PageSlice<Item>;
  refreshMs?: number;
}): ProgressiveCollection<Item>;
```

- [ ] **Step 1: Write failing hook tests**

Start with the first-page/next-page contract:

```ts
it("loads only the first page until loadMore is called", async () => {
  installFetchMock({
    "/page-1": { status: 200, body: { items: ["a"], next: "/page-2" } },
    "/page-2": { status: 200, body: { items: ["b"], next: null } },
  });
  const pageToSlice = (page: { items: string[]; next: string | null }) => ({
    items: page.items,
    nextPath: page.next,
  });
  const { result } = renderHook(() =>
    useProgressiveCollection({ firstPath: "/page-1", pageToSlice }),
  );
  await waitFor(() => expect(result.current.items).toEqual(["a"]));
  expect(result.current.complete).toBe(false);
  act(() => result.current.loadMore());
  await waitFor(() => expect(result.current.items).toEqual(["a", "b"]));
  expect(result.current.complete).toBe(true);
});
```

Add four adjacent tests: retain first-page rows and `complete:false` after a later-page failure; abort and clear the old scope after `firstPath` changes; retain last-good rows while `refreshing:true`; ignore repeated `loadMore` calls while one is active.

- [ ] **Step 2: Run the hook test and confirm red**

```bash
pnpm --filter @drake/web exec vitest run src/lib/useProgressiveCollection.test.tsx
```

Expected: failure because `useProgressiveCollection` does not exist.

- [ ] **Step 3: Implement the state machine**

Use one `AbortController` per generation. `loadMore` may start only the current `nextPath`; repeated clicks while `loadingMore` do nothing. A first-page reload retains current rows under `refreshing`; changing `firstPath` clears them before the new answer renders.

The page adapter is held in a ref so inline callback identity cannot restart the request. A generation number rejects late responses.

- [ ] **Step 4: Export the service-health path builder**

Add to `serviceHealth.ts`:

```ts
export interface ServiceHealthFilters {
  projectId?: string;
  environmentId?: string;
  limit?: number;
  offset?: number;
}

export function serviceHealthListPath(filters: ServiceHealthFilters = {}): string {
  const query = new URLSearchParams();
  if (filters.projectId) query.set("project_id", filters.projectId);
  if (filters.environmentId) query.set("environment_id", filters.environmentId);
  if (filters.limit) query.set("limit", String(filters.limit));
  if (filters.offset) query.set("offset", String(filters.offset));
  const suffix = query.toString();
  return suffix ? `/v1/service-health/services?${suffix}` : "/v1/service-health/services";
}
```

The existing `/service-health` page must import this builder instead of retaining a private duplicate.

- [ ] **Step 5: Make all focused tests green**

```bash
pnpm --filter @drake/web exec vitest run src/lib/useProgressiveCollection.test.tsx src/test/service-health-screens.test.tsx
```

Expected: both files pass; no act warning is printed by the new hook tests.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/lib/useProgressiveCollection.ts apps/web/src/lib/useProgressiveCollection.test.tsx apps/web/src/lib/serviceHealth.ts apps/web/src/app/service-health/page.tsx
git commit -m "feat(web): add bounded progressive collection loading"
```

---

## Task 2: Build the project portfolio risk model and visual

**Files:**

- Create: `apps/web/src/lib/view-models/portfolio-risk.ts`
- Create: `apps/web/src/lib/view-models/portfolio-risk.test.ts`
- Create: `apps/web/src/components/data-viz/ProjectRiskMap.tsx`
- Create: `apps/web/src/components/data-viz/ProjectRiskMap.test.tsx`

**Interfaces:**

```ts
export type PortfolioEvidence = "complete" | "incomplete" | "unassessed";

export interface ProjectRiskItem {
  projectId: string;
  projectKey: string;
  displayName: string;
  criticality: Project["criticality"];
  tone: StatusTone;
  healthLabel: string;
  servicesObserved: number;
  evidence: PortfolioEvidence;
  href: string;
}

export interface PortfolioRiskModel {
  items: ProjectRiskItem[];
  complete: boolean;
  projectsLoaded: number;
  servicesLoaded: number;
  servicesTotal: number | null;
}

export function buildPortfolioRisk(
  projects: Project[],
  services: ServiceHealthRow[],
  completeness: { projects: boolean; services: boolean; servicesTotal: number | null },
): PortfolioRiskModel;
```

- [ ] **Step 1: Write failing model tests**

Use a critical project with one healthy and one critical service and assert:

```ts
const model = buildPortfolioRisk(
  [{ ...project("p1", "alpha"), criticality: "critical" }],
  [service("p1", "healthy"), service("p1", "critical")],
  { projects: true, services: true, servicesTotal: 2 },
);
expect(model.items[0]).toMatchObject({
  projectKey: "alpha",
  criticality: "critical",
  tone: "critical",
  servicesObserved: 2,
  evidence: "complete",
});
```

Add adjacent cases proving: no-service becomes `unassessed` only when service evidence is complete; either incomplete collection makes `model.complete` false; a healthy majority cannot hide one critical service; ordering is criticality first and shared tone severity second.

- [ ] **Step 2: Confirm the model test fails**

```bash
pnpm --filter @drake/web exec vitest run src/lib/view-models/portfolio-risk.test.ts
```

Expected: missing module or export failure.

- [ ] **Step 3: Implement the pure model**

Use `toneForHealth`, `compareTone`, and explicit criticality order. When `services` is incomplete, zero matched rows means `incomplete`, not `unassessed`. `healthLabel` must say `Evidence incomplete` or `Unassessed`; neither may use a success tone.

- [ ] **Step 4: Write failing component tests**

Render a complete model and assert named row/column headers, a link named for each project plus its status, and no color-only control. Render an incomplete model and assert `Evidence incomplete` while `All projects assessed` is absent. Render with `compact` and assert the disclosure exists while the desktop matrix is absent. Click the critical group twice and assert `onToneChange("critical")`, then `onToneChange(null)`.

- [ ] **Step 5: Implement `ProjectRiskMap`**

```ts
export interface ProjectRiskMapProps {
  model: PortfolioRiskModel;
  compact?: boolean;
  activeTone: string | null;
  onToneChange: (tone: string | null) => void;
}
export function ProjectRiskMap(props: ProjectRiskMapProps): React.ReactElement;
```

Desktop uses a semantic table; compact uses `<details>` grouped by criticality. Controls expose `aria-pressed`. The component imports no ECharts code and contains no hex colors.

- [ ] **Step 6: Run both focused suites**

```bash
pnpm --filter @drake/web exec vitest run src/lib/view-models/portfolio-risk.test.ts src/components/data-viz/ProjectRiskMap.test.tsx
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/lib/view-models/portfolio-risk.ts apps/web/src/lib/view-models/portfolio-risk.test.ts apps/web/src/components/data-viz/ProjectRiskMap.tsx apps/web/src/components/data-viz/ProjectRiskMap.test.tsx
git commit -m "feat(web): add the project portfolio risk map"
```

---

## Task 3: Integrate portfolio risk and pagination into Projects

**Files:**

- Modify: `apps/web/src/app/projects/page.tsx`
- Modify: `apps/web/src/test/catalog-screens.test.tsx`

**Interfaces:**

- Consumes: `useProgressiveCollection`, `serviceHealthListPath`, `buildPortfolioRisk`, and `ProjectRiskMap`.
- Produces: URL parameter `risk=<StatusTone|unassessed|incomplete>` applied only to the loaded table view; explicit completeness summary; `Load more projects` and `Load more evidence` actions.

- [ ] **Step 1: Add failing route tests**

Extend `catalog-screens.test.tsx`:

Add five cases with exact mock responses: a high-criticality project whose service health is critical; a project response with `next_cursor:"next"`; service health with `total:101` and one returned row; a second project page requested only after clicking `Load more projects`; and a complete service-health response with no row for the project. Assert the independent `High criticality` and `Critical health` labels, `Partial view`, the `risk=critical` router replacement, appended project name, and `Unassessed` with no healthy badge respectively.

Use exact mock paths with `limit=100`, and include `total`, `limit`, and `offset` in service-health responses.

- [ ] **Step 2: Confirm the new route tests fail**

```bash
pnpm --filter @drake/web exec vitest run src/test/catalog-screens.test.tsx
```

Expected: the new map/pagination assertions fail while the previous catalog cases remain green.

- [ ] **Step 3: Replace the single-page reads**

Projects first path:

```ts
const firstProjectPath = `/v1/projects?${new URLSearchParams({
  lifecycle,
  limit: "100",
  ...(search.length >= 2 ? { search } : {}),
  ...(criticality ? { criticality } : {}),
})}`;
```

The page adapter maps `next_cursor` to the same filters plus `cursor`. Service health uses `serviceHealthListPath({ limit: 100, offset })`. Neither collection auto-loads its next page.

- [ ] **Step 4: Add risk cross-filter and completeness copy**

Use `risk` from `useSearchParams`; selecting the active matrix group again clears it. The table filters only `projects.items` and says `Filtered within N loaded projects` while incomplete. Search, lifecycle, criticality, and risk reset together only through the existing Reset action.

- [ ] **Step 5: Preserve the existing table contract**

Keep repository provenance, environment/service counts, accepted time, stable row links, honest empty/error/denied states, and the current URL debounce. Do not convert the table to cards.

- [ ] **Step 6: Run focused tests and typecheck**

```bash
pnpm --filter @drake/web exec vitest run src/test/catalog-screens.test.tsx src/lib/view-models/portfolio-risk.test.ts src/components/data-viz/ProjectRiskMap.test.tsx
pnpm --filter @drake/web typecheck
```

Expected: all focused tests and TypeScript pass.

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/app/projects/page.tsx apps/web/src/test/catalog-screens.test.tsx
git commit -m "feat(web): make Projects an operational portfolio"
```

---

## Task 4: Build project topology and integrate Project detail

**Files:**

- Create: `apps/web/src/lib/view-models/scope-health.ts`
- Create: `apps/web/src/lib/view-models/scope-health.test.ts`
- Create: `apps/web/src/components/features/catalog/ServiceHealthLane.tsx`
- Create: `apps/web/src/components/features/catalog/ProjectTopology.tsx`
- Create: `apps/web/src/components/features/catalog/ProjectTopology.test.tsx`
- Modify: `apps/web/src/app/projects/[projectId]/page.tsx`
- Modify: `apps/web/src/test/catalog-screens.test.tsx`

**Interfaces:**

```ts
export interface ServiceLaneModel {
  id: string;
  serviceKey: string;
  displayName: string;
  component: string | null;
  tone: StatusTone;
  statusLabel: string;
  freshnessAgeSeconds: number | null;
  partial: boolean;
  binding: ServiceHealthRow["binding"];
  measurements: {
    ready: number | null;
    desired: number | null;
    restarts: number | null;
    cpu: number | null;
    memory: number | null;
  };
  href: string;
}

export interface EnvironmentLaneModel {
  id: string;
  key: string;
  runtime: Environment["runtime"];
  placement: string;
  tone: StatusTone;
  evidence: "complete" | "incomplete" | "unassessed" | "not-applicable";
  services: ServiceLaneModel[];
  href: string;
}

export function buildServiceLane(row: ServiceHealthRow): ServiceLaneModel;
export function buildProjectTopology(
  projectId: string,
  environments: Environment[],
  rows: ServiceHealthRow[],
  servicesComplete: boolean,
): EnvironmentLaneModel[];
```

- [ ] **Step 1: Write failing model tests**

Cover worst-first service order, unbound service retention, partial/stale preservation, external runtime `not-applicable`, missing Kubernetes placement as unknown, and the rule that incomplete service pages cannot turn an empty lane into unassessed.

- [ ] **Step 2: Confirm model tests fail**

```bash
pnpm --filter @drake/web exec vitest run src/lib/view-models/scope-health.test.ts
```

- [ ] **Step 3: Implement the pure topology model**

Join only on opaque `environment_id`; do not join on project/environment/service names. Build service links as:

```ts
`/projects/${projectId}/environments/${row.environment_id}/services/${row.environment_service_id}`
```

- [ ] **Step 4: Write failing component tests**

Render lanes containing: one empty environment, unhealthy/stale/healthy services in reverse input order, one bound service with opaque cluster ID, and one external environment. Assert every environment heading exists; service link order is unhealthy -> stale -> healthy; only rows carrying opaque IDs have links; external placement reads `Not applicable`; and every status has visible text in addition to its icon.

- [ ] **Step 5: Implement topology components**

`ServiceHealthLane` is presentational and reusable by the Environment route. `ProjectTopology` owns environment headings/disclosures and receives only `EnvironmentLaneModel[]`; neither component fetches.

- [ ] **Step 6: Recompose Project detail**

Add a top project verdict that names worst observed status and coverage separately from criticality. Render topology before `ProjectMetricsSection`. Keep telemetry trends. Move capabilities, dependencies, owners, repository, and provenance into a `Standing state` section using current `Panel` primitives.

Fetch service rows with:

```ts
serviceHealthListPath({ projectId, limit: 100, offset: 0 })
```

If `total > items.length`, show `Evidence incomplete` plus a link to `/service-health?project_id=<id>`; do not auto-fetch the remainder on Project detail.

- [ ] **Step 7: Run focused suites**

```bash
pnpm --filter @drake/web exec vitest run src/lib/view-models/scope-health.test.ts src/components/features/catalog/ProjectTopology.test.tsx src/test/catalog-screens.test.tsx
pnpm --filter @drake/web typecheck
```

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/lib/view-models/scope-health.ts apps/web/src/lib/view-models/scope-health.test.ts apps/web/src/components/features/catalog/ServiceHealthLane.tsx apps/web/src/components/features/catalog/ProjectTopology.tsx apps/web/src/components/features/catalog/ProjectTopology.test.tsx 'apps/web/src/app/projects/[projectId]/page.tsx' apps/web/src/test/catalog-screens.test.tsx
git commit -m "feat(web): add project environment-service topology"
```

---

## Task 5: Close the Wave 3A quality gate

**Files:**

- Modify: `apps/web/e2e/catalog.spec.ts`
- Create: `apps/web/e2e/inventory-exploration.spec.ts` with shared helpers used by the later gates.

**Interfaces:**

- Produces: real-stack proof for Projects -> Project detail, URL cross-filter restoration, completeness wording, authorization, axe, and responsive behavior.

- [ ] **Step 1: Add failing E2E assertions**

Add scenarios:

Implement four scenarios: owner selects the critical-health group and opens its project topology while the criticality label remains independent; browser Back restores `risk=critical`; `user-env` sees only the authorized environment and services; and a loop over 390/768/1024/1280/1440/1920 × light/dark runs the shared axe serious/critical and document-overflow assertions.

- [ ] **Step 2: Run the E2E file and confirm the new assertions red**

```bash
pnpm --filter @drake/web exec playwright test e2e/catalog.spec.ts e2e/inventory-exploration.spec.ts
```

- [ ] **Step 3: Make only gate-fix changes**

Fix failures inside Wave 3A files. Do not begin Environment or Service work while this gate is red.

- [ ] **Step 4: Run the Wave 3A gate**

```bash
pnpm --filter @drake/web typecheck
pnpm --filter @drake/web lint
pnpm --filter @drake/web exec vitest run src/lib/useProgressiveCollection.test.tsx src/lib/view-models/portfolio-risk.test.ts src/lib/view-models/scope-health.test.ts src/components/data-viz/ProjectRiskMap.test.tsx src/components/features/catalog/ProjectTopology.test.tsx src/test/catalog-screens.test.tsx
pnpm --filter @drake/web exec playwright test e2e/catalog.spec.ts e2e/inventory-exploration.spec.ts
```

Expected: every command exits zero under Node 24.

- [ ] **Step 5: Capture and inspect Wave 3A screenshots**

```bash
DRAKE_VISUAL_OUT_DIR=.visual-preview/wave3a pnpm --filter @drake/web exec playwright test e2e/wave3-visual-preview.spec.ts
```

Inspect project list/detail at 1920, 1280, 1024, and 390 in both themes. Reject clipped labels, unreadable status text, false healthy wording, empty dead space, and page-level overflow.

- [ ] **Step 6: Commit gate-only changes**

```bash
git add apps/web/e2e/catalog.spec.ts apps/web/e2e/inventory-exploration.spec.ts
git commit -m "test(web): close the Wave 3A project exploration gate"
```

---

## Task 6: Rebuild Environment detail around service health lanes

**Files:**

- Modify: `apps/web/src/app/projects/[projectId]/environments/[environmentId]/page.tsx`
- Modify: `apps/web/src/components/features/catalog/ServiceHealthLane.tsx`
- Modify: `apps/web/src/components/features/catalog/ProjectTopology.test.tsx`
- Modify: `apps/web/src/test/catalog-screens.test.tsx`

**Interfaces:**

- Consumes: `buildServiceLane`, `ServiceHealthLane`, environment detail, and `serviceHealthListPath({ environmentId, limit: 100 })`.
- Produces: verdict -> service evidence -> runtime/capabilities -> provenance order.

- [ ] **Step 1: Write failing Environment route tests**

Mock mixed critical/stale/healthy/unbound rows and assert DOM order plus total row count; assert the unbound row links to `/service-health/bind?environment_service_id=...`; assert `3 / 3` and restart `0` render as measured values while null CPU/memory render `—`; run separate external and Kubernetes-missing fixtures; and return a 503 with correlation ID to assert the current retry state.

- [ ] **Step 2: Confirm red**

```bash
pnpm --filter @drake/web exec vitest run src/test/catalog-screens.test.tsx src/components/features/catalog/ProjectTopology.test.tsx
```

- [ ] **Step 3: Replace legacy loading and panels**

Use `useResource`, `PageHeader`, `Panel`, `PanelHeader`, `StatusBadge`, and current `ui/states.tsx`. Remove this route's use of `LoadGate`, `OperationalGrid`, `Card`, legacy `DataState`, and `components/state/StatusBadge`.

- [ ] **Step 4: Render environment verdict and service lanes**

The verdict reports worst service status and `N of total services loaded`. Service health policy remains server-owned. Each lane links to the existing service route. An unbound row links to:

```ts
`/service-health/bind?environment_service_id=${row.environment_service_id}`
```

- [ ] **Step 5: Preserve runtime truth and metadata**

Keep branch, runtime, cluster/namespace, external hosting provider, source, catalog version, operational capabilities, `last_observed_at`, and freshness. No external environment receives an agent or namespace concept.

- [ ] **Step 6: Run focused tests and typecheck**

```bash
pnpm --filter @drake/web exec vitest run src/lib/view-models/scope-health.test.ts src/components/features/catalog/ProjectTopology.test.tsx src/test/catalog-screens.test.tsx
pnpm --filter @drake/web typecheck
```

- [ ] **Step 7: Commit**

```bash
git add 'apps/web/src/app/projects/[projectId]/environments/[environmentId]/page.tsx' apps/web/src/components/features/catalog/ServiceHealthLane.tsx apps/web/src/components/features/catalog/ProjectTopology.test.tsx apps/web/src/test/catalog-screens.test.tsx
git commit -m "feat(web): make Environment detail service-first"
```

---

## Task 7: Add time-related incident and deployment evidence to Service detail

**Files:**

- Create: `apps/web/src/lib/view-models/service-context.ts`
- Create: `apps/web/src/lib/view-models/service-context.test.ts`
- Create: `apps/web/src/components/features/service/ServiceContextTimeline.tsx`
- Create: `apps/web/src/components/features/service/ServiceContextTimeline.test.tsx`
- Modify: `apps/web/src/app/projects/[projectId]/environments/[environmentId]/services/[serviceId]/page.tsx`
- Modify: `apps/web/e2e/metrics.spec.ts`

**Interfaces:**

```ts
export interface ServiceContextInput {
  incidents: IncidentSummary[];
  deployments: DeploymentRow[];
  range: RangePreset;
  nowMs: number;
}

export function contextFetchWindow(range: RangePreset): "24h" | "7d";
export function buildServiceContextTimeline(input: ServiceContextInput): TimelineLane[];
```

- Consumes: `incidentListPath`, `deploymentListPath`, `OperationalTimeline`, `RangePreset`, and existing service ID (`environment_service_id`).
- Produces: incident and deployment lanes filtered to the exact selected UI range.

- [ ] **Step 1: Write failing pure-model tests**

Start with the range contract:

```ts
expect(contextFetchWindow("1h")).toBe("24h");
expect(contextFetchWindow("24h")).toBe("24h");
expect(contextFetchWindow("7d")).toBe("7d");
const lanes = buildServiceContextTimeline({
  incidents: [incidentAt("2026-09-14T08:30:00Z"), incidentAt("2026-09-14T07:00:00Z")],
  deployments: [deploymentAt("2026-09-14T08:00:00Z")],
  range: "1h",
  nowMs: Date.parse("2026-09-14T09:00:00Z"),
});
expect(lanes.flatMap((lane) => lane.events).map((event) => event.at)).toEqual([
  "2026-09-14T08:00:00Z",
  "2026-09-14T08:30:00Z",
]);
```

Add assertions that visible labels use “related in time”, contain none of `caused by`, `root cause`, or `because of`, and an unavailable source produces `historyAvailable:false` with zero invented events.

- [ ] **Step 2: Confirm red**

```bash
pnpm --filter @drake/web exec vitest run src/lib/view-models/service-context.test.ts
```

- [ ] **Step 3: Implement exact range filtering**

Use `RANGE_PRESETS.seconds` and injected `nowMs`; do not call `Date.now()` inside the pure function. Incident timestamps come from `opened_at` and optional `resolved_at`; deployment timestamps come from `rollout_started_at`. Links target existing incident and deployment detail routes.

- [ ] **Step 4: Write failing component tests**

Render loading/error/ready combinations and assert the source name appears in its state; render one incident and one deployment and assert timestamped accessible link names; set one lane to `historyAvailable:false` and assert `History unavailable`; open the table disclosure and compare its row count with the two track events.

- [ ] **Step 5: Implement `ServiceContextTimeline`**

```ts
export interface ServiceContextTimelineProps {
  lanes: TimelineLane[];
  incidentStatus: ReturnType<typeof resourceStatus>;
  deploymentStatus: ReturnType<typeof resourceStatus>;
}
export function ServiceContextTimeline(
  props: ServiceContextTimelineProps,
): React.ReactElement;
```

The visible description must include: `Related in time; Drake does not assert cause and effect.`

- [ ] **Step 6: Wire Service detail**

Fetch:

```ts
incidentListPath({
  environmentServiceId: serviceId,
  openedWithin: contextFetchWindow(preset),
})

deploymentListPath({
  environmentServiceId: serviceId,
  startedWithin: contextFetchWindow(preset),
})
```

Place context after the golden-signal dashboard and before binding/capability standing state. Preserve all existing dashboard behavior and URL `range` handling.

- [ ] **Step 7: Add real-stack E2E evidence**

In `metrics.spec.ts`, create or reuse a real deployment revision and incident attached to `core-api`, select `Last 1h`, and assert both links appear. Assert the page text does not match `/caused by|root cause|because of/i`.

- [ ] **Step 8: Run focused tests**

```bash
pnpm --filter @drake/web exec vitest run src/lib/view-models/service-context.test.ts src/components/features/service/ServiceContextTimeline.test.tsx src/test/catalog-screens.test.tsx
pnpm --filter @drake/web exec playwright test e2e/metrics.spec.ts
pnpm --filter @drake/web typecheck
```

- [ ] **Step 9: Commit**

```bash
git add apps/web/src/lib/view-models/service-context.ts apps/web/src/lib/view-models/service-context.test.ts apps/web/src/components/features/service/ServiceContextTimeline.tsx apps/web/src/components/features/service/ServiceContextTimeline.test.tsx 'apps/web/src/app/projects/[projectId]/environments/[environmentId]/services/[serviceId]/page.tsx' apps/web/e2e/metrics.spec.ts
git commit -m "feat(web): annotate service signals with operational context"
```

---

## Task 8: Close the Wave 3B quality gate

**Files:**

- Modify: `apps/web/e2e/inventory-exploration.spec.ts`

- [ ] **Step 1: Add Environment -> Service journey scenarios**

Implement five scenarios matching these claims: attention lane reaches service in one click; `range=1h` survives reload and excludes an older fixture event; unbound row has dashes and no fabricated zero; the six-width × two-theme loop passes axe/overflow; and `user-env` cannot see sibling service events or forged detail routes.

- [ ] **Step 2: Run and fix only Wave 3B failures**

```bash
pnpm --filter @drake/web exec playwright test e2e/catalog.spec.ts e2e/metrics.spec.ts e2e/inventory-exploration.spec.ts
```

- [ ] **Step 3: Run the Wave 3B gate**

```bash
pnpm --filter @drake/web typecheck
pnpm --filter @drake/web lint
pnpm --filter @drake/web exec vitest run src/lib/view-models/scope-health.test.ts src/lib/view-models/service-context.test.ts src/components/features/catalog/ProjectTopology.test.tsx src/components/features/service/ServiceContextTimeline.test.tsx src/test/catalog-screens.test.tsx
pnpm --filter @drake/web exec playwright test e2e/catalog.spec.ts e2e/metrics.spec.ts e2e/inventory-exploration.spec.ts
```

Expected: every command exits zero under Node 24.

- [ ] **Step 4: Capture and inspect Wave 3B screenshots**

```bash
DRAKE_VISUAL_OUT_DIR=.visual-preview/wave3b pnpm --filter @drake/web exec playwright test e2e/wave3-visual-preview.spec.ts
```

Inspect Environment and Service at all four required widths/themes, including one stale-last-good and one unavailable provider capture.

- [ ] **Step 5: Commit gate-only changes**

```bash
git add apps/web/e2e/inventory-exploration.spec.ts
git commit -m "test(web): close the Wave 3B service diagnostics gate"
```

---

## Task 9: Build the cluster visibility model and matrix

**Files:**

- Create: `apps/web/src/lib/view-models/cluster-visibility.ts`
- Create: `apps/web/src/lib/view-models/cluster-visibility.test.ts`
- Create: `apps/web/src/components/data-viz/ClusterVisibilityMatrix.tsx`
- Create: `apps/web/src/components/data-viz/ClusterVisibilityMatrix.test.tsx`
- Modify: `apps/web/src/app/clusters/page.tsx`
- Modify: `apps/web/src/test/inventory-screens.test.tsx`

**Interfaces:**

```ts
export interface ClusterVisibilityItem {
  id: string;
  name: string;
  clusterRef: string;
  agent: NonNullable<Cluster["operational"]["agent"]> | "not_configured";
  inventory: NonNullable<Cluster["operational"]["inventory"]> | "not_configured";
  agentTone: StatusTone;
  inventoryTone: StatusTone;
  href: string;
}

export interface ClusterVisibilityModel {
  items: ClusterVisibilityItem[];
  complete: boolean;
}

export function buildClusterVisibility(
  clusters: Cluster[],
  complete: boolean,
): ClusterVisibilityModel;
```

- [ ] **Step 1: Write failing model tests**

Verify connected+stale, disconnected+fresh, enrolled+empty, revoked+reconcile-required, absent values -> not-configured, no vocabulary collapse, and incomplete collection state.

- [ ] **Step 2: Confirm red**

```bash
pnpm --filter @drake/web exec vitest run src/lib/view-models/cluster-visibility.test.ts
```

- [ ] **Step 3: Implement the model using raw axes**

Use `toneForHealth` only for color/icon presentation. Keep the raw `agent` and `inventory` strings as cell labels and filter values.

- [ ] **Step 4: Write failing matrix tests**

Render connected+stale and disconnected+fresh clusters. Assert both axis headings, accessible names containing both raw states, compact disclosure behavior, incomplete coverage copy with no `All clusters visible`, and `onFilter({ agent:"connected", inventory:"stale" })` when the first group is activated.

- [ ] **Step 5: Implement and integrate**

```ts
export interface ClusterVisibilityMatrixProps {
  model: ClusterVisibilityModel;
  compact?: boolean;
  onFilter: (filter: { agent: string; inventory: string } | null) => void;
}
export function ClusterVisibilityMatrix(
  props: ClusterVisibilityMatrixProps,
): React.ReactElement;
```

Clusters route uses `useProgressiveCollection` with `/v1/clusters?limit=100`; next pages are user-triggered. Matrix filter state lives in `agent` and `inventory` URL parameters and applies only to loaded rows. Keep the exact DataTable and observed timestamps.

- [ ] **Step 6: Run focused tests**

```bash
pnpm --filter @drake/web exec vitest run src/lib/view-models/cluster-visibility.test.ts src/components/data-viz/ClusterVisibilityMatrix.test.tsx src/test/inventory-screens.test.tsx
pnpm --filter @drake/web typecheck
```

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/lib/view-models/cluster-visibility.ts apps/web/src/lib/view-models/cluster-visibility.test.ts apps/web/src/components/data-viz/ClusterVisibilityMatrix.tsx apps/web/src/components/data-viz/ClusterVisibilityMatrix.test.tsx apps/web/src/app/clusters/page.tsx apps/web/src/test/inventory-screens.test.tsx
git commit -m "feat(web): add cluster connection-freshness matrix"
```

---

## Task 10: Recompose Cluster detail into visibility, headroom, and risk

**Files:**

- Create: `apps/web/src/lib/view-models/inventory-exploration.ts`
- Create: `apps/web/src/lib/view-models/inventory-exploration.test.ts`
- Create: `apps/web/src/components/data-viz/InventoryCompositionBoard.tsx`
- Create: `apps/web/src/components/data-viz/InventoryCompositionBoard.test.tsx`
- Modify: `apps/web/src/app/clusters/[clusterId]/page.tsx`
- Modify: `apps/web/src/test/inventory-screens.test.tsx`

**Interfaces:**

```ts
export interface InventoryClassModel {
  key: "nodes" | "namespaces" | "workloads" | "pods" | "persistent-volume-claims";
  label: string;
  segments: { name: string; value: number; tone: StatusTone }[];
  total: number;
  attentionCount: number;
  href: string;
}

export interface InventoryRiskLink {
  key: string;
  label: string;
  count: number;
  tone: StatusTone;
  href: string;
}

export function buildInventoryClasses(
  clusterId: string,
  summary: InventorySummary,
): InventoryClassModel[];

export function buildInventoryRiskLinks(
  clusterId: string,
  summary: InventorySummary,
): InventoryRiskLink[];
```

- [ ] **Step 1: Write failing model tests**

Assert exact healthy/degraded/unhealthy/unknown counts, attention count, all-zero behavior, and safe links:

```ts
expect(pod.href).toBe(`/clusters/c1/inventory?kind=Pod&health=unhealthy`);
expect(missing.href).toBe(`/clusters/c1/inventory?lifecycle=missing`);
expect(pvc.href).toBe(`/clusters/c1/inventory?kind=PersistentVolumeClaim&health=unhealthy`);
```

When both degraded and unhealthy exist, the summary may link to the resource class without a health filter; it must not claim one filtered view includes the other.

- [ ] **Step 2: Confirm red**

```bash
pnpm --filter @drake/web exec vitest run src/lib/view-models/inventory-exploration.test.ts
```

- [ ] **Step 3: Implement pure inventory models**

Build URLs with `URLSearchParams`, not string concatenation. Use only server counts. `certificate_expiry_warning` remains a separate warning; do not calculate a client-side date threshold.

- [ ] **Step 4: Write failing composition-board tests**

Render node/pod/PVC models and assert each row has the same Healthy/Degraded/Unhealthy/Unknown legend order and exact values; keep `Unknown 0` visible; assert the pod/PVC attention links use the model's precise href; and render all-zero totals to assert `No inventory reported` with no bar `role="img"`.

- [ ] **Step 5: Implement `InventoryCompositionBoard` without ECharts**

Reuse `CompositionBar` for each row. Render the class label, total, attention link, and the same four ordered segments. A zero unknown bucket remains in the DOM legend even when it occupies no bar width.

- [ ] **Step 6: Recompose Cluster detail**

Migrate from `LoadGate`, legacy `Card`, `DataState`, and legacy badges to `useResource`, `PageHeader`, current `Panel`, current `StatusBadge`, and `ui/states.tsx`.

Final order:

```text
Page header
Visibility verdict: agent | inventory | certificate | capacity evidence
Existing cluster-capacity DashboardRenderer
InventoryCompositionBoard + direct risk links
Referenced authorized environments
Standing metadata + provenance
```

Do not render a namespace × workload heatmap. Do not turn `project_key/environment_key` into a link because the cluster response lacks the opaque environment ID.

- [ ] **Step 7: Update route tests**

Replace donut-specific assertions with composition-board assertions while retaining: stale never healthy, unknown visible, exact restart/CrashLoop/OOM counts, cert warning, summary-error retry, and no fabricated environment links.

- [ ] **Step 8: Run focused tests**

```bash
pnpm --filter @drake/web exec vitest run src/lib/view-models/inventory-exploration.test.ts src/components/data-viz/InventoryCompositionBoard.test.tsx src/test/inventory-screens.test.tsx
pnpm --filter @drake/web typecheck
```

- [ ] **Step 9: Commit**

```bash
git add apps/web/src/lib/view-models/inventory-exploration.ts apps/web/src/lib/view-models/inventory-exploration.test.ts apps/web/src/components/data-viz/InventoryCompositionBoard.tsx apps/web/src/components/data-viz/InventoryCompositionBoard.test.tsx 'apps/web/src/app/clusters/[clusterId]/page.tsx' apps/web/src/test/inventory-screens.test.tsx
git commit -m "feat(web): turn Cluster detail into a headroom-risk board"
```

---

## Task 11: Make Inventory problem-first without weakening the resource table

**Files:**

- Create: `apps/web/src/components/features/inventory/InventoryRiskSummary.tsx`
- Create: `apps/web/src/components/features/inventory/InventoryRiskSummary.test.tsx`
- Modify: `apps/web/src/app/clusters/[clusterId]/inventory/page.tsx`
- Modify: `apps/web/src/app/clusters/[clusterId]/inventory/[resourceId]/page.tsx`
- Modify: `apps/web/src/test/inventory-screens.test.tsx`

**Interfaces:**

```ts
export interface InventoryRiskSummaryProps {
  classes: InventoryClassModel[];
  risks: InventoryRiskLink[];
  activeFilters: { kind: string; health: string; lifecycle: string };
}
export function InventoryRiskSummary(
  props: InventoryRiskSummaryProps,
): React.ReactElement;
```

- [ ] **Step 1: Write failing summary tests**

Render the populated `SUMMARY` fixture and assert heading order plus exact counts; inspect link hrefs for only `kind`, `health`, and `lifecycle`; pass the matching active filter and assert `aria-current="true"`; assert Secret/ConfigMap are absent; and render a stale zero-risk summary to assert freshness warning while `All healthy` is absent.

- [ ] **Step 2: Confirm red**

```bash
pnpm --filter @drake/web exec vitest run src/components/features/inventory/InventoryRiskSummary.test.tsx
```

- [ ] **Step 3: Implement the problem-first summary**

Use the models from Task 10. A click is a real `<Link>` to the existing inventory route; the route's current URL state remains the source of truth. Do not add client-side hidden filtering that disagrees with the API.

- [ ] **Step 4: Integrate above the existing resource table**

Replace the repeated donut rollups with `InventoryRiskSummary`; keep `SortedBarChart` for long-tail kind ranking and keep the dense `DataTable`, URL filters, search debounce, event-only filter behavior, load-more cursor accumulation, missing-resource notice, and retry behavior.

When a cross-filter link changes kind away from Event, omit `event_type`. Reset still returns to `/clusters/<id>/inventory` with active lifecycle default.

- [ ] **Step 5: Modernize inventory resource detail**

Migrate the terminal route from legacy `LoadGate`, `Card`, and legacy badges to current `useResource`, `PageHeader`, `Panel`, `PanelHeader`, `StatusBadge`, identifiers, and `ui/states.tsx`.

Order:

```text
Resource verdict + lifecycle + freshness
Health reasons and observed condition evidence
Bounded spec/status summaries
Owners
Allowlisted labels and annotations
Provenance and timestamps
```

Keep long names/UIDs wrapping, the conditions table, exact `health_reasons`, bounded maps, and not-found behavior. Do not display the original raw Kubernetes payload.

- [ ] **Step 6: Add route-level tests**

Add five route tests with the existing stateful router mock: click the unhealthy Pod link and observe `kind=Pod&health=unhealthy` in both router state and fetch path; verify missing rows/count under lifecycle changes; assert one `h1` and the three section test IDs in DOM order; retain `crashloop_backoff`, `ContainersNotReady`, and `cluster-agent`; and assert raw payload/excluded kind strings are absent.

- [ ] **Step 7: Run focused tests**

```bash
pnpm --filter @drake/web exec vitest run src/lib/view-models/inventory-exploration.test.ts src/components/data-viz/InventoryCompositionBoard.test.tsx src/components/features/inventory/InventoryRiskSummary.test.tsx src/test/inventory-screens.test.tsx
pnpm --filter @drake/web typecheck
```

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/components/features/inventory/InventoryRiskSummary.tsx apps/web/src/components/features/inventory/InventoryRiskSummary.test.tsx 'apps/web/src/app/clusters/[clusterId]/inventory/page.tsx' 'apps/web/src/app/clusters/[clusterId]/inventory/[resourceId]/page.tsx' apps/web/src/test/inventory-screens.test.tsx
git commit -m "feat(web): make cluster inventory problem-first"
```

---

## Task 12: Close Wave 3C and the complete Wave 3 gate

**Files:**

- Modify: `apps/web/e2e/inventory-exploration.spec.ts`
- Modify: `apps/web/e2e/zz-inventory-a11y.spec.ts`
- Modify: `apps/web/e2e/wave3-visual-preview.spec.ts` only if final selectors differ from Task 0's stable readiness contract.

- [ ] **Step 1: Add failing Cluster -> Inventory -> Resource scenarios**

Implement eight scenarios matching these claims. Reuse the real `cluster-a` and resource IDs produced by `inventory.spec.ts`; assert exact URL queries, click count of no more than two from cluster risk to resource evidence, lifecycle=missing discoverability, keyboard focus visibility, the six-width × two-theme axe/overflow loop, uniform not-found for `user-plain`, and the existing payload hygiene canaries plus visible-text checks for excluded kinds.

- [ ] **Step 2: Extend the real-agent accessibility route set**

In `zz-inventory-a11y.spec.ts`, resolve the first authorized resource ID and add inventory resource detail to `INVENTORY_SCREENS`. Preserve its existing local skip/CI fail rule when the agent fixture is absent.

- [ ] **Step 3: Run the cluster/inventory E2E gate**

Start the normal local stack, run repository E2E setup, and start the optional agent fixture required by `inventory.spec.ts`. Then run:

```bash
pnpm --filter @drake/web exec playwright test e2e/inventory.spec.ts e2e/zz-inventory-a11y.spec.ts e2e/inventory-exploration.spec.ts
```

Expected: all enabled tests pass; an optional-agent skip is acceptable locally only when explicitly reported and must fail CI according to the existing file rule.

- [ ] **Step 4: Run the full Node 24 web gate**

```bash
node --version
pnpm --filter @drake/web typecheck
pnpm --filter @drake/web lint
pnpm --filter @drake/web test
pnpm --filter @drake/web build
```

Expected: Node reports `v24.x`; typecheck, lint, Vitest, and production build exit zero. Record exact test totals from the fresh output.

- [ ] **Step 5: Run the complete relevant browser gate**

```bash
pnpm --filter @drake/web exec playwright test e2e/experience.spec.ts e2e/catalog.spec.ts e2e/metrics.spec.ts e2e/inventory.spec.ts e2e/zz-inventory-a11y.spec.ts e2e/inventory-exploration.spec.ts
```

Expected: every enabled scenario passes. Report skips by file and documented fixture condition; do not roll skipped scenarios into the passed count.

- [ ] **Step 6: Capture immutable final visual evidence**

```bash
DRAKE_VISUAL_OUT_DIR=.visual-preview/wave3-final pnpm --filter @drake/web exec playwright test e2e/wave3-visual-preview.spec.ts
```

Required final captures:

- Projects populated and incomplete/empty,
- Project mixed service states,
- Environment healthy/degraded/unbound,
- Service live, stale-last-good, unavailable, and annotated,
- Clusters mixed connection/freshness,
- Cluster populated and stale/disconnected,
- Inventory default and unhealthy/missing filtered,
- Inventory resource detail,
- 1920, 1280, 1024, and 390 px in light and dark.

Inspect every image visually. Confirm no page-level overflow, clipped labels, edge tooltip overflow, low-contrast active state, false healthy statement, excessive empty space, or mobile DOM-order mismatch. Do not overwrite `wave3-before`, Wave 0, Wave 1, or Wave 2 evidence.

- [ ] **Step 7: Verify source and repository hygiene**

```bash
git diff --check
pnpm --filter @drake/web exec vitest run src/test/source-hygiene.test.ts src/test/provider-guard.test.ts
git status --short
```

Expected: no whitespace errors, hygiene tests pass, and only intended Wave 3 gate changes remain.

- [ ] **Step 8: Commit final gate changes**

```bash
git add apps/web/e2e/inventory-exploration.spec.ts apps/web/e2e/zz-inventory-a11y.spec.ts apps/web/e2e/wave3-visual-preview.spec.ts
git commit -m "test(web): close the Wave 3 inventory exploration gate"
```

- [ ] **Step 9: Produce the CTO handoff report**

Report:

- commit list grouped by Wave 3A/3B/3C,
- exact Node/typecheck/lint/Vitest/build results,
- exact Playwright passed/failed/skipped counts by file,
- screenshot directories and fixture state,
- any baseline-existing failure proved on `3d16c38`,
- API completeness limitations encountered,
- confirmation that no backend, dependency, security, or deploy change occurred,
- confirmation that `sslip.io` still serves the old build unless a later explicit deploy is approved.

Do not describe Wave 3 as complete until these fresh results and the visual review are available.

---

## Final Acceptance Checklist

- [ ] Command Center project/cluster findings reach relevant service/resource evidence in no more than two drill-down transitions.
- [ ] Projects visibly separates criticality from observed health.
- [ ] Portfolio and cluster views expose incomplete collection coverage.
- [ ] Project topology includes all authorized environments and does not hide unbound, unknown, stale, or healthy services.
- [ ] Environment detail is service-first and uses server-owned health.
- [ ] Service detail preserves golden signals and adds time-related incident/deployment context without causality language.
- [ ] Cluster visibility preserves connection and inventory freshness as independent raw axes.
- [ ] Cluster detail uses existing capacity evidence and aligned composition bars, with no invented heatmap or threshold.
- [ ] Inventory cross-filters update the URL and server query; dense table, missing resources, cursor behavior, and exclusions remain intact.
- [ ] Inventory resource detail shows bounded evidence and never exposes raw sensitive payloads.
- [ ] Light/dark automated gates cover 1920/1440/1280/1024/768/390; representative screenshots cover 1920/1280/1024/390; keyboard, axe, and overflow evidence is fresh.
- [ ] Full Node 24 web gate and relevant real-stack browser gate pass with exact counts recorded.
- [ ] Wave 0/1/2 visual evidence remains untouched.
- [ ] No backend, dependency, RBAC, security, or deployment change is present.
