import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ClustersPage from "@/app/clusters/page";
import { LocaleProvider } from "@/lib/i18n";
import { installFetchMock } from "@/test/mock-api";

/**
 * The one Turkish test for the clusters area: it proves the screen is wired
 * to the catalogue, not that every translation is right. The English tests
 * in inventory-screens.test.tsx and catalog-screens.test.tsx stay as they are.
 */

vi.mock("next/navigation", () => ({
  usePathname: () => "/clusters",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({ clusterId: "c1" }),
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

describe("clusters area in Turkish", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders the cluster list from the tr catalogue", async () => {
    installFetchMock({
      "/v1/clusters": {
        status: 200,
        body: { clusters: [CLUSTER], next_cursor: null, as_of: "now" },
      },
    });
    render(
      <LocaleProvider locale="tr">
        <ClustersPage />
      </LocaleProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("cluster-list")).toBeInTheDocument());

    expect(screen.getByRole("heading", { level: 1, name: "Kümeler" })).toBeInTheDocument();
    expect(screen.getByText("Filo")).toBeInTheDocument();

    const list = within(screen.getByTestId("cluster-list"));
    // The agent's own vocabulary and the sweep state, in Turkish, and the
    // stale badge keeps its own tone rather than borrowing the healthy one.
    expect(list.getByText("Bağlı")).toBeInTheDocument();
    const stale = list.getByText("Güncel değil");
    expect(stale.closest("[data-testid]")).toHaveAttribute("data-testid", "status-stale");
    expect(list.getByText("0 ortam")).toBeInTheDocument();

    // Glossary: Freshness is "Tazelik".
    expect(screen.getByText("Envanter tazeliği")).toBeInTheDocument();
  });
});
