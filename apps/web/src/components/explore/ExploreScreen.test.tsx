import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ExploreScreen } from "@/components/explore/ExploreScreen";
import { installFetchMock } from "@/test/mock-api";

const replace = vi.fn();
let search = new URLSearchParams();
vi.mock("next/navigation", () => ({
  usePathname: () => "/traces",
  useRouter: () => ({ push: vi.fn(), replace }),
  useSearchParams: () => search,
}));

const row = (key: string, bindingId: string | null) => ({
  environment_service_id: `es-${key}`,
  project_id: "p1",
  project_key: "hermes",
  environment_id: "e1",
  environment_key: "test",
  service_key: key,
  display_name: key,
  component: key,
  binding: bindingId
    ? {
        id: bindingId,
        lifecycle: "active",
        namespace: "hermes-test",
        workload_kind: "Deployment",
        workload_name: key,
        resolved: true,
        preset_key: "x",
        health_policy_key: "x",
        revision: 1,
        cluster: { cluster_ref: "c", id: "c1" },
      }
    : null,
  health: {
    status: "healthy",
    computed_at: "2026-11-21T12:00:00Z",
    newest_sample_at: null,
    freshness_age_seconds: null,
    partial: false,
    served_from_last_good: false,
    reasons: [],
    availability: {},
    stability: {},
    resources: {},
  },
});

const ROWS = [row("auth", "b-auth"), row("core", "b-core"), row("web", null)];
const T0 = 1_763_400_000;
const bucketsOf = (n: number[]) => n.map((requests, i) => ({ t: T0 + i * 3600, requests, errors: 0, p95_ms: 100 + i }));
const OVERVIEW = {
  range: "24h",
  as_of: "2026-11-22T00:00:00Z",
  bucket_seconds: 3600,
  buckets: [T0, T0 + 3600, T0 + 7200],
  window: { start: "", end: "" },
  bindings: [],
  traffic: {
    state: "ok",
    data: [
      { binding_id: "b-auth", buckets: bucketsOf([10, 20, 30]) },
      { binding_id: "b-core", buckets: bucketsOf([100, 200, 300]) },
    ],
  },
  latency: { state: "ok", data: { classes: [], slow_from_class: 9, cells: [], totals: [10, 20, 0, 0, 0, 0, 0, 0, 0, 3, 1, 0, 0] } },
  codes: { state: "ok", data: [] },
  routes: { state: "ok", data: [] },
  calls: { state: "ok", data: [] },
  databases: { state: "ok", data: [] },
  log_lines: { state: "ok", data: [] },
  log_patterns: { state: "ok", data: [] },
  findings: { items: [], skipped: [] },
};
const SIGNALS = {
  range: "1h",
  as_of: "2026-11-22T00:00:00Z",
  logs: { state: "ok" },
  traces: { state: "ok" },
  items: [
    { binding_id: "b-auth", logs: null, traces: { requests: 40, errors: 0, error_ratio: 0, p95_ms: 90, newest_at: null } },
    { binding_id: "b-core", logs: null, traces: { requests: 900, errors: 0, error_ratio: 0, p95_ms: 120, newest_at: null } },
  ],
};

function routes() {
  return installFetchMock({
    "/v1/service-health/services": { status: 200, body: { items: ROWS, total: 3, limit: 100, offset: 0 } },
    "/v1/service-health/overview": { status: 200, body: OVERVIEW },
    "/v1/service-health/signals": { status: 200, body: SIGNALS },
    "/v1/projects": { status: 200, body: { projects: [{ id: "p1", display_name: "Hermes" }], next_cursor: null } },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  replace.mockClear();
  search = new URLSearchParams();
});

describe("ExploreScreen", () => {
  it("opens on the traffic overview, ranking services by requests", async () => {
    routes();
    render(<ExploreScreen kind="traces" />);
    const busiest = await screen.findByTestId("busiest-services");
    const names = within(busiest)
      .getAllByRole("button")
      .map((b) => b.textContent ?? "");
    expect(names[0]).toContain("core");
    expect(names[1]).toContain("auth");
    // 60 + 600 requests over the window, from the overview read.
    expect((await screen.findAllByText("660")).length).toBeGreaterThan(0);
  });

  it("with one app and one environment, lists its bound services busiest first and opens the busiest", async () => {
    routes();
    render(<ExploreScreen kind="traces" />);
    const strip = await screen.findByTestId("explore-services");
    // Ranked once the last hour's signals answer.
    await waitFor(() =>
      expect(within(strip).getAllByRole("button").map((c) => c.getAttribute("data-testid"))).toEqual([
        "explore-service-core",
        "explore-service-auth",
      ]),
    );
    expect(within(strip).getByTestId("explore-service-core")).toHaveAttribute("aria-pressed", "true");
    // The unbound service is said, not silently dropped.
    expect(screen.getByText(/1 service is not bound/)).toBeInTheDocument();
    expect(await screen.findByTestId("explore-explorer")).toBeInTheDocument();
  });

  it("lands a binding-only link on its application, environment and service", async () => {
    search = new URLSearchParams("binding=b-auth");
    routes();
    render(<ExploreScreen kind="traces" />);
    const strip = await screen.findByTestId("explore-services");
    expect(within(strip).getByTestId("explore-service-auth")).toHaveAttribute("aria-pressed", "true");
  });

  it("keeps the service picker one tap away as the List view", async () => {
    search = new URLSearchParams("view=list");
    routes();
    render(<ExploreScreen kind="logs" />);
    expect(await screen.findByRole("radiogroup", { name: "Application" })).toBeInTheDocument();
    expect(screen.queryByTestId("logs-overview")).not.toBeInTheDocument();
  });
});
