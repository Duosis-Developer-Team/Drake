/**
 * The infrastructure screen renders the API's snapshot in plain words, in
 * both languages: a quiet title and summary, the ledger shows each
 * machine, the project table explains each project against its request,
 * and opening an environment loads its pods, each of which can be picked.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import InfrastructurePage from "@/app/infrastructure/page";
import { clearPodCache } from "@/lib/infrastructure";
import { LocaleProvider } from "@/lib/i18n";
import { SessionProvider } from "@/lib/session";
import { installFetchMock, makeMe } from "@/test/mock-api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/infrastructure",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

const GiB = 1024 ** 3;
const N = 5;
const times = Array.from({ length: N }, (_, i) => 1_763_400_000 + i * 300);
const flat = (v: number) => Array.from({ length: N }, () => v);

function node(name: string, over: Record<string, unknown> = {}) {
  return {
    name,
    kubelet_version: "v1.34.3",
    health: "healthy",
    cores: 6,
    cpu: { now: 0.68, series: flat(0.68), allocatable: 5.4, requested: 4.6 },
    memory: { now: 0.55, series: flat(0.55), allocatable: 10.8 * GiB, requested: 8.3 * GiB },
    disk: { now: 0.31, series: flat(0.31), size_bytes: 193 * GiB, growth_per_day: 0 },
    load5: { now: 5.1, series: flat(5.1) },
    pods: { running: 39, allocatable: 110 },
    ...over,
  };
}

function ns(namespace: string, nodeName: string, group: Record<string, string>, over: Record<string, unknown> = {}) {
  return {
    namespace,
    node: nodeName,
    group,
    project: null,
    cpu: { now: 0.5, series: flat(0.5) },
    memory: { now: 1 * GiB, series: flat(1 * GiB) },
    cpu_requested: 1.0,
    memory_requested: 1 * GiB,
    cpu_limit: 2.0,
    memory_limit: 2 * GiB,
    pods: 3,
    restarts_24h: 0,
    ...over,
  };
}

const SNAPSHOT = {
  cluster: { id: "c1", ref: "duosis-prod-1", name: "Duosis Production" },
  state: "ok",
  built_at: "2026-11-18T06:00:00Z",
  node_times: times,
  node_step_seconds: 300,
  times,
  step_seconds: 300,
  nodes: [
    node("node1"),
    node("node2", {
      disk: { now: 0.78, series: [0.77, 0.775, 0.777, 0.779, 0.78], size_bytes: 193 * GiB, growth_per_day: 0.011 },
    }),
  ],
  namespaces: [
    ns("hermes-test", "node1", { kind: "project", key: "hermes", name: "Hermes" }, {
      project: { project_id: "p1", project_key: "hermes", project_name: "Hermes", environment_id: "e1", environment_key: "test" },
      cpu: { now: 0.3, series: flat(0.3) },
      cpu_requested: 1.8,
      restarts_24h: 3,
    }),
    ns("drake-otel", "node2", { kind: "drake", key: "drake", name: "drake" }, {
      cpu: { now: 1.08, series: flat(1.08) },
      cpu_requested: 0.9,
    }),
    ns("kube-system", "node1", { kind: "kubernetes", key: "kubernetes", name: "kubernetes" }),
  ],
  missing: [],
  age_seconds: 4,
  refreshing: false,
};

const PODS = {
  namespace: "drake-otel",
  times,
  step_seconds: 300,
  state: "ok",
  missing: [],
  pods: [
    { pod: "clickhouse-0", node: "node2", cpu: { now: 0.71, series: flat(0.71) }, memory: 2.6 * GiB, cpu_requested: 0.5, cpu_limit: 1.1, memory_limit: 3 * GiB, restarts_24h: 0 },
    { pod: "otel-agent-j4k2m", node: "node2", cpu: { now: 0.03, series: flat(0.03) }, memory: 0.09 * GiB, cpu_requested: 0.1, cpu_limit: 0.2, memory_limit: 0.2 * GiB, restarts_24h: 0 },
  ],
};

function routes() {
  return installFetchMock({
    "/v1/me": { status: 200, body: makeMe({ permissions: ["cluster.view", "telemetry.query"] }) },
    "/v1/infrastructure/clusters": {
      status: 200,
      body: { clusters: [{ id: "c1", ref: "duosis-prod-1", name: "Duosis Production", metrics_configured: true, nodes: 2 }] },
    },
    "/v1/infrastructure/clusters/c1": { status: 200, body: SNAPSHOT },
    "/v1/infrastructure/clusters/c1/namespaces/drake-otel": { status: 200, body: PODS },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  clearPodCache();
});

describe("infrastructure screen", () => {
  it("names the cluster and lists every machine", async () => {
    routes();
    render(
      <SessionProvider>
        <InfrastructurePage />
      </SessionProvider>,
    );
    expect(await screen.findByRole("heading", { level: 1, name: "Infrastructure" })).toBeInTheDocument();
    expect(screen.getByText("Duosis Production · 2 machines · 78 pods")).toBeInTheDocument();
    const ledger = screen.getByRole("region", { name: "Machine" });
    expect(within(ledger).getByRole("heading", { name: "node1" })).toBeInTheDocument();
    expect(within(ledger).getByText("At this rate it fills in ≈20 days")).toBeInTheDocument();
  });

  it("explains each project against its request and opens an environment's pods", async () => {
    const calls = routes();
    render(
      <SessionProvider>
        <InfrastructurePage />
      </SessionProvider>,
    );
    const drake = await screen.findByRole("button", { name: /Tracery.*Uses more than requested/s });
    expect(screen.getByRole("button", { name: /Hermes.*Requested too much/s })).toHaveTextContent("Problem in 1 environment");
    fireEvent.click(drake);
    fireEvent.click(await screen.findByRole("button", { name: /drake-otel/ }));
    const panel = await screen.findByRole("region", { name: "drake-otel" });
    expect((await within(panel).findAllByText("clickhouse-0")).length).toBeGreaterThan(0);
    expect(within(panel).getAllByText(/1\.2× what it requested/).length).toBeGreaterThan(0);
    expect(within(panel).getByText("near memory limit")).toBeInTheDocument();
    expect(within(panel).getByText("over-reserved")).toBeInTheDocument();
    const pod = within(panel).getByRole("button", { name: /clickhouse-0/ });
    expect(pod).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(pod);
    expect(pod).toHaveAttribute("aria-pressed", "true");
    expect(within(panel).getAllByText(/clickhouse-0/).length).toBeGreaterThan(1);
    await waitFor(() =>
      expect(calls.some((c) => c.path === "/v1/infrastructure/clusters/c1/namespaces/drake-otel")).toBe(true),
    );
  });

  it("says so when the cluster's metrics are not readable", async () => {
    installFetchMock({
      "/v1/me": { status: 200, body: makeMe({ permissions: ["cluster.view"] }) },
      "/v1/infrastructure/clusters": {
        status: 200,
        body: { clusters: [{ id: "c1", ref: "duosis-prod-1", name: "Duosis Production", metrics_configured: true, nodes: 2 }] },
      },
      "/v1/infrastructure/clusters/c1": { status: 200, body: { ...SNAPSHOT, state: "denied", nodes: [], namespaces: [] } },
    });
    render(
      <SessionProvider>
        <InfrastructurePage />
      </SessionProvider>,
    );
    expect(await screen.findByText(/reading its metrics needs the telemetry permission/)).toBeInTheDocument();
  });
});

describe("infrastructure screen in Turkish", () => {
  it("renders the title and the table from the catalogue", async () => {
    routes();
    render(
      <LocaleProvider locale="tr">
        <SessionProvider>
          <InfrastructurePage />
        </SessionProvider>
      </LocaleProvider>,
    );
    expect(await screen.findByRole("heading", { level: 1, name: "Altyapı" })).toBeInTheDocument();
    expect(screen.getByText("Duosis Production · 2 makine · 78 pod")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Tracery.*İsteğini aşıyor/s })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Hangi makineler" })).toBeInTheDocument();
  });
});
