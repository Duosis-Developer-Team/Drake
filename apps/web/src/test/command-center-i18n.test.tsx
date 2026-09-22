/**
 * Command Center in Turkish.
 *
 * One test per area (i18n README rule 8): the page rendered inside a
 * `LocaleProvider` pinned to `tr` shows Turkish where the catalogue has it —
 * the page title, a panel heading, an attention row built from a structured
 * `reason`, and the verdict's sources sentence. This proves the wiring, not
 * the translation; the English tests in command-center.test.tsx keep
 * asserting the same facts without a provider.
 *
 * The fixtures mirror command-center.test.tsx (that file does not export
 * them); `installFetchMock` is the shared helper.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import CommandCenterPage from "@/app/page";
import { LocaleProvider } from "@/lib/i18n";
import { installFetchMock } from "@/test/mock-api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

const CONTEXT = { projects: 3, environments: 5, clusters: 1, as_of: "2026-08-11T00:00:00Z" };

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
    pods: { total: 97, healthy: 85, degraded: 10, unhealthy: 2, unknown: 0, crashloop: 0, oom_killed: 0, restarts: 3 },
    workloads: { total: 58, healthy: 55, degraded: 3, unhealthy: 0, unknown: 0 },
    persistent_volume_claims: { total: 9, healthy: 9, degraded: 0, unhealthy: 0, unknown: 0 },
    by_kind: {},
    as_of: "2026-08-11T00:00:00Z",
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Command Center (tr)", () => {
  it("renders the page, its panels and a structured attention row in Turkish", async () => {
    installFetchMock({
      ...QUIET_SOURCES,
      "/v1/catalog/context": { status: 200, body: CONTEXT },
      "/v1/clusters": { status: 200, body: { clusters: [cluster("disconnected", "stale")] } },
      "/v1/clusters/c1/inventory/summary": { status: 200, body: summary("disconnected") },
    });
    render(
      <LocaleProvider locale="tr">
        <CommandCenterPage />
      </LocaleProvider>,
    );

    // Page title and a panel heading come straight from the catalogue.
    expect(screen.getByRole("heading", { level: 1, name: "Komuta Merkezi" })).toBeInTheDocument();
    expect(screen.getByText("Dikkat kuyruğu")).toBeInTheDocument();
    expect(screen.getByText("Kanıt kapsamı")).toBeInTheDocument();

    // An attention row is translated from its `reason`, not from the English
    // `state` string the view-model also carries.
    const group = within(await screen.findByTestId("attention-group-cluster-c1"));
    expect(group.getByText("Ajan bağlantısı kesildi")).toBeInTheDocument();
    expect(group.getByText("Envanter güncel değil")).toBeInTheDocument();
    expect(group.getByText("küme bağlantısı")).toBeInTheDocument();

    // The verdict's sources sentence is one key with two variables, so the
    // Turkish word order ("5 kaynaktan 5 tanesi") is the catalogue's, not JSX's.
    const sources = screen.getByTestId("verdict-sources");
    await waitFor(() => expect(sources).toHaveTextContent("5 kaynaktan 5 tanesi yanıt verdi"));
    expect(screen.queryByText(/sources answered/)).not.toBeInTheDocument();
  });
});
