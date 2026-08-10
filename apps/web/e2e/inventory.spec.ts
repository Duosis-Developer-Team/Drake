import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import https from "node:https";
import { join } from "node:path";

import { expect, test, type Page } from "@playwright/test";

/**
 * Full-chain cluster inventory E2E — NOTHING is mocked:
 * fake OIDC → production web build → FastAPI → PostgreSQL, plus the
 * internal mTLS agent listener, the REAL Go agent binary, and a disposable
 * k3d Kubernetes cluster. Prerequisite: `bash scripts/e2e_agent_stack.sh up`
 * (CI runs it before Playwright).
 *
 * Scenario (12 steps):
 *  1. authorized operator mints a one-time enrollment token in a UI session
 *  2. the agent enrolls over TLS (key local, CSR only) and starts syncing
 *  3. a full snapshot lands atomically; freshness turns fresh
 *  4. cluster list shows real connectivity + freshness
 *  5. cluster detail shows real nodes/namespaces/workloads from k3d
 *  6. resource browser filters real inventory
 *  7. a live change in k3d propagates via WATCH (no restart, no re-snapshot)
 *  8. Kubernetes Secrets never appear anywhere in Drake
 *  9. killing the agent turns connectivity/freshness honest (disconnected/stale)
 * 10. restarting the agent reconciles back to fresh with the SAME identity
 * 11. a user without cluster scope gets uniform 404s (UI and API)
 * 12. browser payloads carry no certificates, keys, or agent endpoints
 */

test.describe.configure({ mode: "serial" });
test.setTimeout(180_000);

const REPO_ROOT = join(__dirname, "..", "..", "..");
const STACK_DIR = join(REPO_ROOT, ".e2e-agent");
const STATE_DIR = join(STACK_DIR, "state");
const KUBECONFIG = join(STACK_DIR, "kubeconfig");
const INTERNAL_PORT = 58443;
const INTERNAL_BASE = `https://127.0.0.1:${INTERNAL_PORT}`;

const stackReady = existsSync(KUBECONFIG) && existsSync(join(STACK_DIR, "tls.json"));

// Locally, a missing stack is a skip: not every developer has k3d, and
// blocking them on it would be hostile. In CI it is a FAILURE. These
// scenarios are the only browser coverage of the cluster-agent path, and a
// silent skip there is a green run that proved nothing — which is exactly
// the risk created by splitting the k3d smokes into their own job.
if (process.env.CI && !stackReady) {
  throw new Error(
    "agent E2E stack missing under CI: scripts/e2e_agent_stack.sh up must run " +
      "before the browser suite. Refusing to skip the inventory scenarios.",
  );
}

const databaseUrl =
  process.env.DRAKE_E2E_DATABASE_URL ??
  "postgresql+psycopg://drake:drake_local_only_dev@127.0.0.1:55432/drake";
const redisUrl = process.env.DRAKE_E2E_REDIS_URL ?? "redis://127.0.0.1:56379/0";

let internalApi: ChildProcess | null = null;
let agent: ChildProcess | null = null;
let clusterId = "";
// Unique per run so consecutive suite runs never collide in k3d.
const liveNamespace = `e2e-live-${Date.now().toString(36)}`;

function tlsConfig(): Record<string, string> {
  return JSON.parse(readFileSync(join(STACK_DIR, "tls.json"), "utf8"));
}

function kubectl(...args: string[]): string {
  const result = spawnSync("kubectl", args, {
    env: { ...process.env, KUBECONFIG },
    encoding: "utf8",
  });
  if (result.status !== 0) {
    throw new Error(`kubectl ${args.join(" ")} failed: ${result.stderr}`);
  }
  return result.stdout;
}

function startInternalApi(): ChildProcess {
  const tls = tlsConfig();
  const child = spawn(
    "uv",
    [
      "run",
      "python",
      "scripts/run_internal_agent_api.py",
      "--host",
      "127.0.0.1",
      "--port",
      String(INTERNAL_PORT),
      "--tls-cert",
      tls.internal_server_cert,
      "--tls-key",
      tls.internal_server_key,
      "--client-ca",
      tls.agent_ca_cert_file,
      "--client-cert-optional",
    ],
    {
      cwd: REPO_ROOT,
      env: {
        ...process.env,
        DRAKE_ENV: "local",
        DRAKE_DATABASE_URL: databaseUrl,
        DRAKE_REDIS_URL: redisUrl,
        DRAKE_AGENT_CA_CERT_FILE: tls.agent_ca_cert_file,
        DRAKE_AGENT_CA_KEY_FILE: tls.agent_ca_key_file,
      },
      stdio: ["ignore", "inherit", "inherit"],
    },
  );
  return child;
}

async function waitForInternalApi(): Promise<void> {
  const ca = readFileSync(tlsConfig().internal_server_cert);
  for (let attempt = 0; attempt < 60; attempt++) {
    const alive = await new Promise<boolean>((resolve) => {
      const request = https.request(
        { host: "127.0.0.1", port: INTERNAL_PORT, path: "/internal/v1/agent/enroll", method: "GET", ca },
        (response) => {
          response.resume();
          resolve(true);
        },
      );
      request.on("error", () => resolve(false));
      request.end();
    });
    if (alive) return;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error("internal agent API never became ready");
}

function startAgent(): ChildProcess {
  return spawn(join(STACK_DIR, "agent"), [], {
    env: {
      ...process.env,
      DRAKE_AGENT_API_BASE_URL: INTERNAL_BASE,
      DRAKE_AGENT_CLUSTER_ID: clusterId,
      DRAKE_AGENT_CLUSTER_NAME: "cluster-a",
      DRAKE_AGENT_SERVER_CA_FILE: tlsConfig().internal_server_cert,
      DRAKE_AGENT_ENROLLMENT_TOKEN_FILE: join(STATE_DIR, "enrollment-token"),
      DRAKE_AGENT_STATE_DIR: STATE_DIR,
      DRAKE_AGENT_KUBECONFIG: KUBECONFIG,
      DRAKE_AGENT_LOG_LEVEL: "info",
      DRAKE_AGENT_HEALTH_LISTEN_ADDR: "127.0.0.1:58090",
      // Match the API's 6s heartbeat-stale E2E window: beat every 2s so
      // "connected" is continuously observable, not a 6s-per-30s lottery.
      DRAKE_AGENT_HEARTBEAT_SECONDS: "2",
    },
    stdio: ["ignore", "inherit", "inherit"],
  });
}

async function stopProcess(child: ChildProcess | null): Promise<void> {
  if (!child || child.exitCode !== null) return;
  child.kill("SIGTERM");
  await new Promise<void>((resolve) => {
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      resolve();
    }, 5000);
    child.once("exit", () => {
      clearTimeout(timer);
      resolve();
    });
  });
}

async function signInAs(page: Page, subject: string) {
  await page.goto(`/v1/auth/login?redirect=/&login_hint=${subject}`);
  await expect(page.getByRole("heading", { name: "Command Center" })).toBeVisible();
}

async function signOutIfNeeded(page: Page) {
  await page.goto("/");
  const menu = page.getByRole("button", { name: /account menu/i });
  if (await menu.isVisible().catch(() => false)) {
    await menu.click();
    await page.getByRole("menuitem", { name: /sign out/i }).click();
    await expect(page.getByTestId("screen-signed-out")).toBeVisible();
  }
}

async function apiJson<T>(page: Page, path: string): Promise<{ status: number; body: T }> {
  const response = await page.request.get(path);
  return { status: response.status(), body: (await response.json().catch(() => ({}))) as T };
}

async function pollSummary(
  page: Page,
  predicate: (summary: {
    agent: { status: string };
    inventory: { state: string; last_reconcile_at: string | null };
  }) => boolean,
  timeoutMs: number,
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let last = "";
  while (Date.now() < deadline) {
    const { status, body } = await apiJson<{
      agent: { status: string };
      inventory: { state: string; last_reconcile_at: string | null };
    }>(page, `/v1/clusters/${clusterId}/inventory/summary`);
    if (status === 200) {
      last = `${body.agent?.status}/${body.inventory?.state}`;
      if (predicate(body)) return;
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error(`summary never satisfied predicate (last: ${last})`);
}

test.beforeAll(async () => {
  test.skip(!stackReady, "agent E2E stack missing — run scripts/e2e_agent_stack.sh up");
  mkdirSync(STATE_DIR, { recursive: true });
  internalApi = startInternalApi();
  await waitForInternalApi();
});

test.afterAll(async () => {
  await stopProcess(agent);
  await stopProcess(internalApi);
});

test.beforeEach(async ({ page }) => {
  await signOutIfNeeded(page);
});

test("steps 1-3: token minted in UI session, real agent enrolls, snapshot turns fresh", async ({
  page,
}) => {
  await signInAs(page, "user-owner");

  const clusters = await apiJson<{ clusters: { id: string; cluster_ref: string }[] }>(
    page,
    "/v1/clusters",
  );
  const clusterA = clusters.body.clusters.find((row) => row.cluster_ref === "cluster-a");
  expect(clusterA).toBeTruthy();
  clusterId = clusterA!.id;

  const me = await apiJson<{ csrf_token: string }>(page, "/v1/me");
  const minted = await page.request.post(
    `/v1/clusters/${clusterId}/agent-enrollment-tokens`,
    {
      headers: {
        "X-CSRF-Token": me.body.csrf_token,
        "Idempotency-Key": crypto.randomUUID(),
      },
    },
  );
  expect(minted.status()).toBe(201);
  const tokenBody = (await minted.json()) as { token: string; expires_at: string };
  expect(tokenBody.token.length).toBeGreaterThanOrEqual(32);
  writeFileSync(join(STATE_DIR, "enrollment-token"), tokenBody.token, { mode: 0o600 });

  agent = startAgent();
  await pollSummary(
    page,
    (summary) => summary.agent.status === "connected" && summary.inventory.state === "fresh",
    90_000,
  );
});

test("steps 4-6: cluster screens show REAL k3d inventory", async ({ page }) => {
  await signInAs(page, "user-owner");

  await page.goto("/clusters");
  const list = page.getByTestId("cluster-list");
  await expect(list).toBeVisible();
  await expect(list.getByText("connected")).toBeVisible();
  await expect(list.getByText("fresh", { exact: true })).toBeVisible();

  await page.goto(`/clusters/${clusterId}`);
  await expect(page.getByTestId("agent-card")).toBeVisible();
  await expect(page.getByTestId("agent-card").getByText("connected")).toBeVisible();
  await expect(
    page.getByTestId("freshness-card").getByText("fresh", { exact: true }),
  ).toBeVisible();

  // Real k3d content: at least one node, the e2e-workloads namespace,
  // and the seeded deployment.
  const summary = await apiJson<{
    nodes: { total: number };
    namespaces: { total: number };
  }>(page, `/v1/clusters/${clusterId}/inventory/summary`);
  expect(summary.body.nodes.total).toBeGreaterThanOrEqual(1);
  expect(summary.body.namespaces.total).toBeGreaterThanOrEqual(4);

  await page.goto(`/clusters/${clusterId}/inventory`);
  await page.getByTestId("filter-kind").selectOption("Deployment");
  // Exact-name link is unique to the Deployment row (pod/RS names carry
  // suffixes), so this stays deterministic even while the filtered fetch
  // is still in flight.
  const deploymentLink = page
    .getByTestId("resource-rows")
    .getByRole("link", { name: "e2e-web", exact: true });
  await expect(deploymentLink).toBeVisible();

  // Drilldown to the deployment detail with reason-coded health.
  await deploymentLink.click();
  await expect(page.getByTestId("health-card")).toBeVisible();
  await expect(page.getByTestId("observation-card")).toContainText("cluster-agent");
});

test("step 7: live k3d change propagates via WATCH without a new snapshot", async ({
  page,
}) => {
  await signInAs(page, "user-owner");
  const before = await apiJson<{ inventory: { last_reconcile_at: string | null } }>(
    page,
    `/v1/clusters/${clusterId}/inventory/summary`,
  );

  kubectl("create", "namespace", liveNamespace);
  const deadline = Date.now() + 45_000;
  let found = false;
  while (Date.now() < deadline && !found) {
    const listing = await apiJson<{ resources: { name: string }[] }>(
      page,
      `/v1/clusters/${clusterId}/inventory/resources?kind=Namespace&search=${liveNamespace}`,
    );
    found = listing.body.resources?.some((row) => row.name === liveNamespace) ?? false;
    if (!found) await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  expect(found, "watch event never reached the projection").toBe(true);

  // Propagation happened via events, not a fresh snapshot.
  const after = await apiJson<{ inventory: { last_reconcile_at: string | null } }>(
    page,
    `/v1/clusters/${clusterId}/inventory/summary`,
  );
  expect(after.body.inventory.last_reconcile_at).toBe(
    before.body.inventory.last_reconcile_at,
  );
});

test("step 8: Kubernetes Secrets exist in k3d but NEVER inside Drake", async ({ page }) => {
  await signInAs(page, "user-owner");

  // The canary Secret is real in the cluster…
  expect(kubectl("get", "secret", "e2e-canary-secret", "-n", "e2e-workloads", "-o", "name"))
    .toContain("e2e-canary-secret");

  // …but the API refuses the kind filter outright…
  const refused = await page.request.get(
    `/v1/clusters/${clusterId}/inventory/resources?kind=Secret`,
  );
  expect(refused.status()).toBe(422);

  // …no row matches it, and no payload carries the canary value.
  const byName = await apiJson<{ resources: unknown[] }>(
    page,
    `/v1/clusters/${clusterId}/inventory/resources?search=e2e-canary`,
  );
  expect(byName.body.resources).toHaveLength(0);

  const summary = await page.request.get(`/v1/clusters/${clusterId}/inventory/summary`);
  const summaryText = await summary.text();
  expect(summaryText).not.toContain("Secret");
  const everything = await page.request.get(
    `/v1/clusters/${clusterId}/inventory/resources?limit=100`,
  );
  const everythingText = await everything.text();
  expect(everythingText).not.toContain("drake-e2e-secret-canary-value");
  expect(everythingText).not.toContain("ConfigMap");
});

test("steps 9-10: disconnect turns honest, reconnect reconciles with the SAME identity", async ({
  page,
}) => {
  await signInAs(page, "user-owner");

  await stopProcess(agent);
  agent = null;

  // Heartbeat window (E2E override) elapses → disconnected; inventory
  // activity ages past its window → stale. Stale is NEVER healthy-colored.
  await pollSummary(page, (summary) => summary.agent.status === "disconnected", 30_000);
  // Inventory staleness is measured from the last APPLIED activity (event
  // or reconcile), so it lags the 60s E2E window behind the last change.
  await pollSummary(page, (summary) => summary.inventory.state === "stale", 90_000);

  await page.goto(`/clusters/${clusterId}`);
  await expect(page.getByTestId("agent-card").getByText("disconnected")).toBeVisible();
  const freshness = page.getByTestId("freshness-card");
  await expect(freshness.getByTestId("status-stale")).toBeVisible();
  await expect(freshness.getByTestId("status-healthy")).toHaveCount(0);

  // Restart: identity persists on disk; NO new enrollment token is minted.
  agent = startAgent();
  await pollSummary(
    page,
    (summary) => summary.agent.status === "connected" && summary.inventory.state === "fresh",
    60_000,
  );
});

test("step 11: no cluster scope means uniform 404s in UI and API", async ({ page }) => {
  await signInAs(page, "user-plain");

  await page.goto("/clusters");
  await expect(page.getByTestId("state-empty")).toBeVisible();

  await page.goto(`/clusters/${clusterId}`);
  await expect(page.getByTestId("state-not-configured").first()).toBeVisible();

  const denied = await page.request.get(`/v1/clusters/${clusterId}/inventory/summary`);
  expect(denied.status()).toBe(404);
  const deniedList = await page.request.get(
    `/v1/clusters/${clusterId}/inventory/resources`,
  );
  expect(deniedList.status()).toBe(404);
});

test("step 12: browser payloads never carry certificates, keys, or agent endpoints", async ({
  page,
}) => {
  const payloads: string[] = [];
  page.on("response", async (response) => {
    if (response.url().includes("/v1/")) {
      payloads.push(await response.text().catch(() => ""));
    }
  });
  await signInAs(page, "user-owner");
  await page.goto(`/clusters/${clusterId}`);
  await expect(page.getByTestId("agent-card")).toBeVisible();
  await page.goto(`/clusters/${clusterId}/inventory`);
  await expect(page.getByTestId("resource-rows")).toBeVisible();

  const combined = payloads.join("\n");
  expect(combined).not.toContain("BEGIN CERTIFICATE");
  expect(combined).not.toContain("BEGIN PRIVATE KEY");
  expect(combined).not.toContain("BEGIN EC PRIVATE KEY");
  expect(combined).not.toContain("/internal/v1/agent");
  expect(combined).not.toContain("58443");
});
