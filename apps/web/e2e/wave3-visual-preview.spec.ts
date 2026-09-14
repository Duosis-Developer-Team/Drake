import { expect, test, type Page } from "@playwright/test";

/**
 * Wave 3 manual visual evidence capture.
 *
 * Mirrors `visual-preview.spec.ts`'s pattern — a manual, revision-specific
 * snapshot, not a CI gate — but resolves real project/environment/service/
 * cluster/resource IDs from authorized API responses instead of hard-coding
 * fixture UUIDs, since every Wave 3 route is keyed by an opaque ID assigned
 * at seed time.
 *
 * Run manually against the real stack:
 *   make up && bash scripts/e2e-setup.sh
 *   DRAKE_VISUAL_OUT_DIR=.visual-preview/my-label \
 *     pnpm --filter @drake/web exec playwright test e2e/wave3-visual-preview.spec.ts
 */

test.skip(!!process.env.CI, "Wave 3 visual evidence is a manual gate");
const OUT_DIR = process.env.DRAKE_VISUAL_OUT_DIR ?? ".visual-preview/wave3-current";

test.describe.configure({ mode: "serial" });
test.setTimeout(120_000);

const VIEWPORTS = [
  { name: "1920", width: 1920, height: 1080 },
  { name: "1280", width: 1280, height: 900 },
  { name: "1024", width: 1024, height: 900 },
  { name: "390", width: 390, height: 844 },
] as const;

type RouteTarget = { slug: string; path: string; ready: string };

async function resolveWave3Routes(page: Page): Promise<RouteTarget[]> {
  const projects = (await (await page.request.get("/v1/projects?limit=100")).json()) as {
    projects: { id: string }[];
  };
  const project = projects.projects[0];
  expect(project, "an authorized project is required for Wave 3 capture").toBeTruthy();
  const environments = (await (
    await page.request.get(`/v1/projects/${project.id}/environments?limit=100`)
  ).json()) as { environments: { id: string }[] };
  const environment = environments.environments[0];
  expect(environment, "an authorized environment is required").toBeTruthy();
  const services = (await (
    await page.request.get(
      `/v1/projects/${project.id}/environments/${environment.id}/services?limit=100`,
    )
  ).json()) as { services: { id: string }[] };
  const service = services.services[0];
  expect(service, "an authorized service is required").toBeTruthy();

  const clusters = (await (await page.request.get("/v1/clusters?limit=100")).json()) as {
    clusters: { id: string }[];
  };
  const cluster = clusters.clusters[0];
  expect(cluster, "an authorized cluster is required").toBeTruthy();
  const inventory = (await (
    await page.request.get(`/v1/clusters/${cluster.id}/inventory/resources?limit=100`)
  ).json()) as { resources: { id: string }[] };

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

async function signIn(page: Page) {
  await page.goto("/v1/auth/login?redirect=/&login_hint=user-owner");
  await expect(page.getByRole("heading", { name: "Command Center" })).toBeVisible();
}

async function setTheme(page: Page, theme: "light" | "dark") {
  await page.evaluate((value) => localStorage.setItem("drake-theme", value), theme);
  await page.reload();
}

for (const theme of ["light", "dark"] as const) {
  for (const viewport of VIEWPORTS) {
    test(`preview: ${theme} at ${viewport.name}`, async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await signIn(page);
      await setTheme(page, theme);

      const targets = await resolveWave3Routes(page);
      for (const target of targets) {
        await page.goto(target.path);
        await page.waitForLoadState("networkidle");
        // The readiness testid documents what a fully migrated Wave 3 route
        // should expose; a pre-migration "before" capture may not have it
        // yet, so a missed wait never blocks the capture itself.
        await page
          .getByTestId(target.ready)
          .first()
          .waitFor({ state: "visible", timeout: 5_000 })
          .catch(() => {});
        await page.screenshot({
          path: `${OUT_DIR}/${target.slug}-${viewport.name}-${theme}.png`,
          fullPage: true,
        });
      }
    });
  }
}
