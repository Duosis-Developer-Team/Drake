/**
 * Command Center honesty.
 *
 * The page's whole job is to distinguish "healthy" from "nobody is
 * looking", and it used to answer the second question with copy about a
 * future sprint. These assert the three states that matter: a live agent
 * reports counts, an agent that is not connected reports that instead of
 * its last known numbers, and a card with no source says so.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import CommandCenterPage from "@/app/page";
import { installFetchMock } from "@/test/mock-api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

const CONTEXT = { projects: 3, environments: 5, clusters: 1 };

function cluster(agent: string, inventory: string) {
  return {
    id: "c1",
    cluster_ref: "duosis-prod-1",
    display_name: "Duosis Production",
    site: "duosis-hetzner-fsn",
    lifecycle: "active",
    version: 1,
    scope: { type: "cluster", ref: "duosis-prod-1" },
    source: { kind: "operator", ref: "operator:-", revision: "v1", accepted_at: "2026-08-11T00:00:00Z" },
    operational: { agent, inventory },
    as_of: "2026-08-11T00:00:00Z",
  };
}

function summary(agentStatus: string) {
  return {
    cluster_id: "c1",
    agent: {
      status: agentStatus,
      agent_version: "0.4.0",
      last_heartbeat_at: "2026-08-11T00:00:00Z",
      certificate_not_after: "2026-08-25T00:00:00Z",
      certificate_expiry_warning: false,
    },
    inventory: {
      state: agentStatus === "connected" ? "fresh" : "stale",
      last_reconcile_at: "2026-08-11T00:00:00Z",
      last_event_at: "2026-08-11T00:00:00Z",
      active_resources: 1900,
      missing_resources: 0,
    },
    nodes: { total: 2, healthy: 2, degraded: 0, unhealthy: 0, unknown: 0 },
    namespaces: { total: 17, healthy: 17, degraded: 0, unhealthy: 0, unknown: 0 },
    pods: {
      total: 97, healthy: 85, degraded: 10, unhealthy: 2, unknown: 0,
      crashloop: 0, oom_killed: 0, restarts: 3,
    },
    workloads: { total: 58, healthy: 55, degraded: 3, unhealthy: 0, unknown: 0 },
    persistent_volume_claims: { total: 9, healthy: 9, degraded: 0, unhealthy: 0, unknown: 0 },
    by_kind: {},
    as_of: "2026-08-11T00:00:00Z",
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Command Center", () => {
  it("reports fleet health from the agent's own inventory", async () => {
    installFetchMock({
      "/v1/catalog/context": { status: 200, body: CONTEXT },
      "/v1/clusters": { status: 200, body: { clusters: [cluster("connected", "fresh")] } },
      "/v1/clusters/c1/inventory/summary": { status: 200, body: summary("connected") },
    });
    render(<CommandCenterPage />);

    // The card renders before its per-cluster summary resolves, so wait for
    // the summary itself rather than the list that will hold it.
    await waitFor(() => expect(screen.getByText("/58")).toBeTruthy());
    const fleet = within(screen.getByTestId("fleet-health"));
    expect(fleet.getByText("Duosis Production")).toBeTruthy();
    // Healthy-over-total, both read from the summary — never a percentage
    // this component computed and never a bare "healthy".
    expect(fleet.getByText("/2")).toBeTruthy();
    expect(fleet.getByText("/58")).toBeTruthy();
    expect(fleet.getByText("/97")).toBeTruthy();
    expect(screen.getByText("Cluster inventory connected")).toBeTruthy();
  });

  it("does not show last-known numbers for an agent that is not connected", async () => {
    installFetchMock({
      "/v1/catalog/context": { status: 200, body: CONTEXT },
      "/v1/clusters": { status: 200, body: { clusters: [cluster("disconnected", "stale")] } },
      "/v1/clusters/c1/inventory/summary": { status: 200, body: summary("disconnected") },
    });
    render(<CommandCenterPage />);

    await waitFor(() => expect(screen.getByText(/Agent disconnected/)).toBeTruthy());
    expect(screen.queryByText("/58")).toBeNull();
    expect(screen.getByText("No operational sources connected")).toBeTruthy();
  });

  it("says what a card is missing instead of naming a future sprint", async () => {
    installFetchMock({
      "/v1/catalog/context": { status: 200, body: CONTEXT },
      "/v1/clusters": { status: 200, body: { clusters: [] } },
    });
    render(<CommandCenterPage />);

    await waitFor(() =>
      expect(screen.getByText(/No cluster is registered/)).toBeTruthy(),
    );
    expect(screen.queryByText(/arrives with the/)).toBeNull();
    expect(screen.getByText(/No backup reporter is configured/)).toBeTruthy();
  });
});
