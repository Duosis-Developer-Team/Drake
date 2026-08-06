import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ClusterDetailPage from "@/app/clusters/[clusterId]/page";
import ClusterInventoryPage from "@/app/clusters/[clusterId]/inventory/page";
import InventoryResourcePage from "@/app/clusters/[clusterId]/inventory/[resourceId]/page";
import ClustersPage from "@/app/clusters/page";
import {
  AgentBadge,
  InventoryStateBadge,
} from "@/components/inventory/primitives";
import { errorBody, installFetchMock } from "@/test/mock-api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/clusters",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({ clusterId: "c1", resourceId: "r1" }),
}));

const CLUSTER = {
  id: "c1",
  cluster_ref: "cluster-a",
  display_name: "Cluster A",
  site: "fra",
  lifecycle: "active",
  version: 1,
  scope: { type: "cluster", ref: "cluster-a" },
  source: {
    kind: "fixture",
    ref: "fixture:cluster-a",
    revision: "v1",
    accepted_at: "2026-08-06T00:00:00Z",
  },
  operational: { agent: "connected", inventory: "stale" },
  referenced_environments: [],
  as_of: "2026-08-06T00:00:00Z",
};

const ROLLUP = { total: 0, healthy: 0, degraded: 0, unhealthy: 0, unknown: 0 };

const SUMMARY = {
  cluster_id: "c1",
  agent: {
    status: "connected",
    agent_version: "0.4.0",
    last_heartbeat_at: "2026-08-06T10:00:00Z",
    certificate_not_after: "2026-08-08T10:00:00Z",
    certificate_expiry_warning: true,
  },
  inventory: {
    state: "stale",
    last_reconcile_at: "2026-08-06T08:00:00Z",
    last_event_at: null,
    active_resources: 4,
    missing_resources: 1,
  },
  nodes: { ...ROLLUP, total: 2, healthy: 1, unknown: 1 },
  namespaces: { ...ROLLUP, total: 3, healthy: 3 },
  pods: { ...ROLLUP, total: 5, unhealthy: 2, crashloop: 2, oom_killed: 1, restarts: 17 },
  workloads: { ...ROLLUP, total: 2, degraded: 1, healthy: 1 },
  persistent_volume_claims: { ...ROLLUP },
  by_kind: {
    Pod: { ...ROLLUP, total: 5, unhealthy: 2 },
    Node: { ...ROLLUP, total: 2, healthy: 1, unknown: 1 },
  },
  as_of: "2026-08-06T10:05:00Z",
};

const POD_ROW = {
  id: "r1",
  api_group: "",
  api_version: "v1",
  kind: "Pod",
  namespace: "team-a",
  name: "api-1",
  health: "unhealthy",
  health_reasons: ["crashloop_backoff"],
  lifecycle: "active",
  observed_at: "2026-08-06T10:00:00Z",
  last_seen_at: "2026-08-06T10:00:00Z",
  status_summary: { phase: "Running", restarts: 7 },
};

const RESOURCE_LIST = {
  cluster_id: "c1",
  resources: [
    POD_ROW,
    { ...POD_ROW, id: "r2", name: "gone-1", health: "unknown", lifecycle: "missing" },
  ],
  next_cursor: null,
  inventory: { state: "stale" },
  as_of: "2026-08-06T10:05:00Z",
};

const RESOURCE_DETAIL = {
  ...POD_ROW,
  cluster_id: "c1",
  uid: "aaaa1111-0000-0000-0000-000000000002",
  resource_version: "100",
  labels: { "app.kubernetes.io/name": "api" },
  annotations: {},
  owners: [{ kind: "ReplicaSet", name: "api-6b7", uid: "aaaa1111-0000-0000-0000-00000000000a" }],
  spec_summary: { node: "node-1" },
  conditions: [{ type: "Ready", status: "False", reason: "ContainersNotReady" }],
  first_seen_at: "2026-08-05T10:00:00Z",
  provenance: { source: "cluster-agent", last_snapshot_id: "s1" },
  inventory: { state: "stale" },
  as_of: "2026-08-06T10:05:00Z",
};

describe("cluster inventory screens", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("cluster list shows real agent + inventory states, never fabricated", async () => {
    installFetchMock({
      "/v1/clusters": {
        status: 200,
        body: { clusters: [CLUSTER], next_cursor: null, as_of: "now" },
      },
    });
    render(<ClustersPage />);
    await waitFor(() => expect(screen.getByTestId("cluster-list")).toBeInTheDocument());
    expect(screen.getByText("connected")).toBeInTheDocument();
    // Stale renders as STALE (its own tone), never as the healthy badge.
    const stale = screen.getByText("stale");
    expect(stale.closest("[data-testid]")).toHaveAttribute("data-testid", "status-stale");
  });

  it("cluster detail renders agent card, cert warning, and honest freshness", async () => {
    installFetchMock({
      "/v1/clusters/c1": { status: 200, body: CLUSTER },
      "/v1/clusters/c1/inventory/summary": { status: 200, body: SUMMARY },
    });
    render(<ClusterDetailPage />);
    await waitFor(() => expect(screen.getByTestId("agent-card")).toBeInTheDocument());
    const agentCard = within(screen.getByTestId("agent-card"));
    expect(agentCard.getByText("connected")).toBeInTheDocument();
    expect(agentCard.getByText("0.4.0")).toBeInTheDocument();
    expect(agentCard.getByText("expires soon")).toBeInTheDocument();

    const freshness = within(screen.getByTestId("freshness-card"));
    expect(freshness.getByTestId("status-stale")).toBeInTheDocument();
    expect(freshness.queryByTestId("status-healthy")).not.toBeInTheDocument();

    const pods = within(screen.getByTestId("pods-card"));
    expect(pods.getByText("17")).toBeInTheDocument(); // restarts are real sums
    expect(pods.getByText("CrashLoop")).toBeInTheDocument();
    // Unknown buckets stay visible: node rollup shows unknown=1.
    expect(screen.getAllByText("unknown").length).toBeGreaterThan(0);
  });

  it("cluster detail: summary error is an error state, not empty data", async () => {
    installFetchMock({
      "/v1/clusters/c1": { status: 200, body: CLUSTER },
      "/v1/clusters/c1/inventory/summary": {
        status: 503,
        body: errorBody("dependency_unavailable", "inventory unavailable"),
      },
    });
    render(<ClusterDetailPage />);
    await waitFor(() => expect(screen.getByTestId("state-error")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("resource browser lists rows, marks missing distinctly, filters by kind", async () => {
    const calls = installFetchMock({
      "/v1/clusters/c1/inventory/resources": { status: 200, body: RESOURCE_LIST },
    });
    render(<ClusterInventoryPage />);
    await waitFor(() => expect(screen.getByTestId("resource-rows")).toBeInTheDocument());
    const rows = within(screen.getByTestId("resource-rows"));
    expect(rows.getByText("api-1")).toBeInTheDocument();
    // The missing resource renders its OWN state; it is not dropped.
    expect(rows.getByText("gone-1")).toBeInTheDocument();
    expect(rows.getByText("missing")).toBeInTheDocument();

    fireEvent.change(screen.getByTestId("filter-kind"), { target: { value: "Pod" } });
    await waitFor(() =>
      expect(
        calls.some((call) => call.path.includes("kind=Pod")),
      ).toBe(true),
    );
  });

  it("resource browser: authorization failure is a uniform not-found", async () => {
    installFetchMock({
      "/v1/clusters/c1/inventory/resources": {
        status: 404,
        body: errorBody("not_found", "not found"),
      },
    });
    render(<ClusterInventoryPage />);
    await waitFor(() =>
      expect(screen.getByTestId("state-not-configured")).toBeInTheDocument(),
    );
    expect(screen.getByText(/not found/i)).toBeInTheDocument();
  });

  it("resource detail shows health reasons, conditions, and provenance", async () => {
    installFetchMock({
      "/v1/clusters/c1/inventory/resources/r1": { status: 200, body: RESOURCE_DETAIL },
    });
    render(<InventoryResourcePage />);
    await waitFor(() => expect(screen.getByTestId("health-card")).toBeInTheDocument());
    expect(screen.getByText("crashloop_backoff")).toBeInTheDocument();
    expect(screen.getByText("ContainersNotReady")).toBeInTheDocument();
    expect(screen.getByText("cluster-agent")).toBeInTheDocument();
    expect(screen.getByText(/app\.kubernetes\.io\/name=/)).toBeInTheDocument();
  });

  it("badge mappings never dress non-fresh states in healthy colors", () => {
    const { container } = render(
      <>
        <InventoryStateBadge state="stale" />
        <InventoryStateBadge state="empty" />
        <InventoryStateBadge state="reconcile_required" />
        <InventoryStateBadge state="reconciling" />
        <AgentBadge status="disconnected" />
        <AgentBadge status="revoked" />
        <AgentBadge status="not_configured" />
      </>,
    );
    expect(container.querySelectorAll('[data-testid="status-healthy"]')).toHaveLength(0);
  });
});
