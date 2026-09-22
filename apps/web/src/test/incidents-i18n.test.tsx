/**
 * Proves the incidents area is wired to the catalogue: the list renders in
 * Turkish under a pinned `LocaleProvider`. The English tests in
 * incident-screens.test.tsx run without a provider and are untouched.
 */
import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import IncidentsPage from "@/app/incidents/page";
import { LocaleProvider } from "@/lib/i18n";
import { installFetchMock } from "@/test/mock-api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/incidents",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({ incidentId: "inc-1" }),
}));

const INCIDENT = {
  id: "inc-1",
  state: "open",
  severity: "critical",
  title: "api (dev): No replicas ready",
  primary_reason: "no_ready_replicas",
  opened_at: "2026-08-08T12:00:00Z",
  last_critical_at: "2026-08-08T12:05:00Z",
  acknowledged_at: null,
  resolved_at: null,
  version: 1,
  project_key: "pilot",
  environment_key: "dev",
  service_key: "api",
  environment_service_id: "es1",
  binding: {
    id: "b1",
    namespace: "pilot-dev",
    workload_kind: "Deployment",
    workload_name: "pilot-api",
    cluster_ref: "cluster-a",
  },
  current_health: null,
};

afterEach(() => vi.unstubAllGlobals());

describe("incident list in Turkish", () => {
  it("renders the queue, filters, state badge and lifecycle rule from the catalogue", async () => {
    installFetchMock({
      "/v1/incidents": {
        status: 200,
        body: { items: [INCIDENT], next_cursor: null, total: 1, limit: 25 },
      },
    });
    render(
      <LocaleProvider locale="tr">
        <IncidentsPage />
      </LocaleProvider>,
    );

    const row = await screen.findByTestId("incident-row-api");
    expect(within(row).getByText("Açık")).toBeInTheDocument();
    expect(within(row).getByText("sağlık bilinmiyor")).toBeInTheDocument();
    expect(screen.getByText("Olay kuyruğu")).toBeInTheDocument();
    expect(screen.getByText("Olaylar nasıl ilerler")).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Filtreler" })).toBeInTheDocument();
    expect(screen.getByText(/1 olaydan 1 tanesi gösteriliyor/)).toBeInTheDocument();
  });
});
