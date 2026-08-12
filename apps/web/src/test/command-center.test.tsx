/**
 * Command Center honesty.
 *
 * The page's whole job is to distinguish "healthy" from "nobody is looking".
 * These assert the three states that matter: a live agent reports counts, an
 * agent that is not connected reports THAT instead of its last known numbers,
 * and a page with no source says what it checked rather than implying health.
 *
 * The screen was rebuilt around a triage list in Sprint 13, so the selectors
 * follow it; every claim under test is the one the previous version made.
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

const CONTEXT = { projects: 3, environments: 5, clusters: 1, as_of: "2026-08-11T00:00:00Z" };

/** The other five sources the page reads; empty so each test isolates one. */
const QUIET_SOURCES = {
  "/v1/alerts/summary": {
    status: 200,
    body: { firing: 0, p1: 0, p2: 0, silenced: 0, unmapped: 0, with_incident: 0 },
  },
  "/v1/incidents": { status: 200, body: { items: [], next_cursor: null, total: 0, limit: 25 } },
  "/v1/service-health/services": { status: 200, body: { items: [] } },
  "/v1/integrations/health": { status: 200, body: { integrations: [] } },
} as const;

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
      ...QUIET_SOURCES,
      "/v1/catalog/context": { status: 200, body: CONTEXT },
      "/v1/clusters": { status: 200, body: { clusters: [cluster("connected", "fresh")] } },
      "/v1/clusters/c1/inventory/summary": { status: 200, body: summary("connected") },
    });
    render(<CommandCenterPage />);

    // The row renders before its per-cluster summary resolves, so wait for the
    // summary itself rather than the panel that will hold it.
    await waitFor(() => expect(screen.getByText("/58")).toBeTruthy());
    const fleet = within(screen.getByTestId("fleet-panel"));
    expect(fleet.getByText("Duosis Production")).toBeTruthy();
    // Healthy-over-total, both read from the summary — never a percentage this
    // component computed and never a bare "healthy".
    expect(fleet.getByText("/2")).toBeTruthy();
    expect(fleet.getByText("/58")).toBeTruthy();
    expect(fleet.getByText("/97")).toBeTruthy();
    // Connection and freshness stay separate claims, in the agent's own words:
    // "the agent answers" and "the sweep is current" are different facts, and
    // a row that collapsed them would let a silent cluster read as a well one.
    const row = within(fleet.getByRole("row", { name: /Duosis Production/ }));
    expect(row.getByText("Connected")).toBeTruthy();
    expect(row.getByText("Fresh")).toBeTruthy();
  });

  it("does not show last-known numbers for an agent that is not connected", async () => {
    installFetchMock({
      ...QUIET_SOURCES,
      "/v1/catalog/context": { status: 200, body: CONTEXT },
      "/v1/clusters": { status: 200, body: { clusters: [cluster("disconnected", "stale")] } },
      "/v1/clusters/c1/inventory/summary": { status: 200, body: summary("disconnected") },
    });
    render(<CommandCenterPage />);

    await waitFor(() => expect(screen.getByText(/Agent disconnected/)).toBeTruthy());
    expect(screen.queryByText("/58")).toBeNull();
    // A disconnected agent and a stale sweep are both things needing
    // attention, and they are listed as two separate facts.
    const attention = within(screen.getByTestId("attention-list"));
    expect(attention.getByText(/agent disconnected/i)).toBeTruthy();
    expect(attention.getByText(/inventory stale/i)).toBeTruthy();
  });

  it("says what it checked instead of claiming the platform is healthy", async () => {
    // The replacement for the old "arrives with the X sprint" cards: with
    // nothing flagged, the page names its sources rather than implying health.
    installFetchMock({
      ...QUIET_SOURCES,
      "/v1/catalog/context": { status: 200, body: CONTEXT },
      "/v1/clusters": { status: 200, body: { clusters: [] } },
    });
    render(<CommandCenterPage />);

    const empty = await screen.findByTestId("attention-empty");
    expect(empty).toHaveTextContent(/not a statement that the platform is healthy/i);
    expect(empty).toHaveTextContent(/checked/i);
    expect(screen.queryByText(/arrives with the/)).toBeNull();
    expect(screen.queryByText(/all systems/i)).toBeNull();
    expect(screen.getByTestId("fleet-panel")).toHaveTextContent(/no clusters in scope/i);
  });

  it("shows a dash for a source it could not read, never a zero", async () => {
    // A zero would read as "nothing wrong here" when the truth is "you cannot
    // see this" — the single most misleading thing the strip could render.
    installFetchMock({
      ...QUIET_SOURCES,
      "/v1/catalog/context": { status: 200, body: CONTEXT },
      "/v1/clusters": { status: 403, body: { error: { code: "forbidden", message: "denied" } } },
    });
    render(<CommandCenterPage />);

    const tile = await screen.findByTestId("triage-clusters");
    await waitFor(() => expect(tile).toHaveTextContent(/permission required/i));
    expect(tile).toHaveTextContent("—");
  });
});
