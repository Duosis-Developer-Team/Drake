/**
 * The environment dashboard draws the overview read in the approved layout,
 * finds what needs attention, and saves a changed layout as a new version.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import DashboardsPage from "@/app/dashboards/page";
import EnvironmentDashboardPage from "@/app/dashboards/[environmentId]/page";
import { WIDGETS } from "@/lib/dashboards";
import { SessionProvider } from "@/lib/session";
import { installFetchMock, makeMe } from "@/test/mock-api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/dashboards/e1",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({ environmentId: "e1" }),
}));

const DASHBOARD = {
  id: null, environment_id: "e1", environment_key: "test", project_id: "p1", project_key: "hermes", project_name: "Hermes",
  can_manage: true, title: "Hermes · test", version: 0, saved: false, updated_at: null, updated_by: null,
  layout: WIDGETS.map((key) => ({ key, size: "l", hidden: false })),
};

function row(id: string, name: string) {
  return {
    environment_service_id: `es-${id}`, project_id: "p1", project_key: "hermes", environment_id: "e1", environment_key: "test",
    service_key: name, display_name: null, component: null,
    binding: { id, lifecycle: "active", namespace: "hermes-test", workload_kind: "Deployment", workload_name: name, resolved: true, preset_key: "x", health_policy_key: "y", revision: 1, cluster: { cluster_ref: "c", id: "c1" } },
    health: { status: "healthy", computed_at: "", newest_sample_at: null, freshness_age_seconds: 1, partial: false, served_from_last_good: false, reasons: [], availability: {}, stability: {}, resources: {} },
  };
}

const ok = (data: unknown) => ({ state: "ok", data });
const OVERVIEW = {
  range: "24h", as_of: "", bucket_seconds: 3600, buckets: [1_763_400_000, 1_763_403_600], window: { start: "", end: "" },
  bindings: [{ binding_id: "core", traces: true, logs: true }, { binding_id: "auth", traces: true, logs: true }],
  traffic: ok([
    { binding_id: "core", buckets: [{ t: 1_763_400_000, requests: 500, errors: 0, p95_ms: 120 }, { t: 1_763_403_600, requests: 480, errors: 0, p95_ms: 2100 }] },
    { binding_id: "auth", buckets: [{ t: 1_763_400_000, requests: 540, errors: 0, p95_ms: 90 }, { t: 1_763_403_600, requests: 520, errors: 0, p95_ms: 1050 }] },
  ]),
  latency: ok({ classes: [], slow_from_class: 9, cells: [[100, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], [80, 0, 0, 0, 0, 0, 0, 0, 0, 3, 0, 0, 0]], totals: [180, 0, 0, 0, 0, 0, 0, 0, 0, 3, 0, 0, 0] }),
  codes: ok([{ binding_id: "core", codes: { "200": 889, "401": 94 } }]),
  routes: ok([{ binding_id: "auth", route: "POST /internal/authz/resolve", requests: 890, errors: 0, client_errors: 0, long_lived: 0, p50_ms: 80, p95_ms: 539, p99_ms: 900 }]),
  calls: ok([{ from_binding_id: "core", to_binding_id: "auth", calls: 971, errors: 0, p95_ms: 513 }]),
  databases: ok([
    { binding_id: "auth", system: "postgresql", database: "auth_db", operation: "SELECT auth_db", calls: 21186, errors: 0, p95_ms: 68 },
    { binding_id: "auth", system: "postgresql", database: "auth_db", operation: "connect", calls: 9624, errors: 0, p95_ms: 93 },
  ]),
  log_lines: ok([{ t: 1_763_400_000, lines: 2300, errors: 1200, warnings: 0 }, { t: 1_763_403_600, lines: 2300, errors: 1200, warnings: 0 }]),
  log_patterns: ok([{ pattern: 'FATAL: database "hermes" does not exist', lines: 28469, severity_number: 21, severity_text: "FATAL", workloads: 2, binding_ids: [] }]),
  findings: {
    items: [
      { kind: "connection_per_query", severity: "warning", binding_ids: ["auth"], metrics: { system: "postgresql", database: "auth_db", connects: 9624, queries: 21186, ratio: 0.454, connect_p95_ms: 93 } },
      { kind: "client_errors", severity: "info", binding_ids: ["core"], metrics: { client_errors: 94, requests: 983, share: 0.0956, codes: { "401": 94 }, auth_only: true } },
    ],
    skipped: [],
  },
};

function routes(over: Record<string, unknown> = {}) {
  return installFetchMock({
    "/v1/me": { status: 200, body: makeMe({ permissions: ["environment.view", "traces.view", "logs.view", "dashboard.manage"] }) },
    "/v1/dashboards": { status: 200, body: { items: [{ ...DASHBOARD, services: 2 }] } },
    "/v1/dashboards/e1": { status: 200, body: { ...DASHBOARD, ...over } },
    "/v1/service-health/services": { status: 200, body: { items: [row("core", "core-service"), row("auth", "auth-service")], total: 2, limit: 100, offset: 0 } },
    "/v1/service-health/overview": { status: 200, body: OVERVIEW },
    "/v1/deployments": { status: 200, body: { items: [DEPLOY], next_cursor: null, total: 1, limit: 50 } },
  });
}

// Inside the second bucket of OVERVIEW, and a regression after it.
const DEPLOY = {
  id: "d1", namespace: "hermes-test", workload_kind: "Deployment", workload_name: "auth-service", revision: 7,
  observed_generation: 7, images: [], primary_image: null, primary_digest: null, short_digest: "abc123", commit_sha: "f00dcafe",
  short_commit: "f00dcaf", workflow: { provider: null, repository: null, run_id: null, run_url: null },
  evidence_state: "verified", evidence_detail: {}, rollout_state: "healthy", rollout_reason: null,
  replicas: { desired: 2, ready: 2, updated: 2, available: 2 },
  rollout_started_at: new Date((1_763_403_600 + 900) * 1000).toISOString(), rollout_completed_at: null,
  last_seen_at: "", cluster: { cluster_ref: "c", id: "c1" }, project_key: "hermes", environment_key: "test",
  service_key: "auth-service", environment_service_id: "es-auth", binding_id: "auth", previous_revision_id: null,
  health_comparison: { verdict: "regressed", incident_count: 0 },
};

afterEach(() => vi.unstubAllGlobals());

describe("dashboards", () => {
  it("lists the environments with services", async () => {
    routes();
    render(<SessionProvider><DashboardsPage /></SessionProvider>);
    expect(await screen.findByText("Hermes · test")).toBeInTheDocument();
    expect(screen.getByText("2 services")).toBeInTheDocument();
  });

  it("draws the overview, the services and what needs attention", async () => {
    const calls = routes();
    render(<SessionProvider><EnvironmentDashboardPage /></SessionProvider>);
    expect(await screen.findByRole("heading", { level: 1, name: "Hermes · test" })).toBeInTheDocument();
    expect((await screen.findAllByText("2,040")).length).toBeGreaterThan(0); // total requests
    expect(screen.getByText("auth-service opens a new database connection for many queries")).toBeInTheDocument();
    expect(screen.getByText("core-service answers many requests with 4xx")).toBeInTheDocument();
    const overview = calls.find((c) => c.path.startsWith("/v1/service-health/overview"))!;
    expect(overview.path).toContain("binding_id=auth&binding_id=core");
    expect(overview.path).toContain("range=24h");
  });

  it("aborts a superseded or abandoned overview read", async () => {
    const calls = routes();
    const { unmount } = render(<SessionProvider><EnvironmentDashboardPage /></SessionProvider>);
    const reads = () => calls.filter((c) => c.path.startsWith("/v1/service-health/overview"));
    await waitFor(() => expect(reads()).toHaveLength(1));
    fireEvent.click(await screen.findByRole("button", { name: "6 hours" }));
    await waitFor(() => expect(reads().some((c) => c.path.includes("range=6h"))).toBe(true));
    // The 24-hour read can no longer land on top of the 6-hour one.
    expect(reads()[0].init?.signal?.aborted).toBe(true);
    const sixHours = reads().find((c) => c.path.includes("range=6h"))!;
    expect(sixHours.init?.signal?.aborted).toBe(false);
    // Leaving the page stops what is still in flight.
    unmount();
    expect(sixHours.init?.signal?.aborted).toBe(true);
  });

  it("saves a changed layout against the version it edited", async () => {
    const calls = routes();
    render(<SessionProvider><EnvironmentDashboardPage /></SessionProvider>);
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    const hide = await screen.findAllByRole("button", { name: "Hide" });
    fireEvent.click(hide[hide.length - 1]);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.init?.method === "PUT")).toBe(true));
    const put = calls.find((c) => c.init?.method === "PUT")!;
    const body = JSON.parse(String(put.init?.body));
    expect(body.expected_version).toBe(0);
    expect(body.layout.find((w: { key: string }) => w.key === "logs").hidden).toBe(true);
  });

  it("opens a service's details inside the dashboard instead of leaving it", async () => {
    routes();
    render(<SessionProvider><EnvironmentDashboardPage /></SessionProvider>);
    fireEvent.click(await screen.findByRole("button", { name: "auth-service" }));
    const panel = await screen.findByRole("complementary", { name: "auth-service" });
    expect(panel).toHaveTextContent("Busiest routes");
    expect(panel).toHaveTextContent("POST /internal/authz/resolve");
    expect(panel).toHaveTextContent("← core-service");
    expect(screen.getByRole("link", { name: "Open its service health page" })).toHaveAttribute("href", "/service-health/auth");
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("complementary", { name: "auth-service" })).not.toBeInTheDocument());
  });

  it("marks a deploy on the charts and opens that service from the pin", async () => {
    const calls = routes();
    render(<SessionProvider><EnvironmentDashboardPage /></SessionProvider>);
    const pin = await screen.findByRole("button", { name: "auth-service deployed f00dcaf" });
    const asked = calls.find((c) => c.path.startsWith("/v1/deployments"))!;
    expect(asked.path).toContain("environment_id=e1");
    expect(asked.path).toContain("started_within=24h");
    fireEvent.mouseMove(pin);
    const tip = await screen.findByRole("tooltip");
    expect(tip).toHaveTextContent("auth-service deployed");
    expect(tip).toHaveTextContent("f00dcaf");
    fireEvent.click(pin);
    const panel = await screen.findByRole("complementary", { name: "auth-service" });
    expect(panel).toHaveTextContent("Deploys");
  });

  it("offers no editing to someone who may only look", async () => {
    routes({ can_manage: false });
    render(<SessionProvider><EnvironmentDashboardPage /></SessionProvider>);
    expect(await screen.findByRole("heading", { level: 1, name: "Hermes · test" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
  });
});
