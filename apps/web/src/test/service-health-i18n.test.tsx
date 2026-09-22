/**
 * Proves the service-health area is wired to the catalogue: the list page
 * rendered inside a Turkish `LocaleProvider` shows Turkish copy, including
 * the enum labels other areas read through `t.dyn("status", …)`.
 *
 * This is about wiring, not translation quality — the English tests in
 * service-health-screens.test.tsx keep asserting English without a provider.
 */
import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ServiceHealthPage from "@/app/service-health/page";
import { LocaleProvider } from "@/lib/i18n";
import { installFetchMock } from "@/test/mock-api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/service-health",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({ bindingId: "b1" }),
}));

afterEach(() => vi.unstubAllGlobals());

const BOUND_ROW = {
  environment_service_id: "es1",
  project_id: "p1",
  project_key: "pilot",
  environment_id: "e1",
  environment_key: "dev",
  service_key: "api",
  display_name: "API",
  component: "api",
  binding: {
    id: "b1",
    lifecycle: "active",
    namespace: "hermes-dev",
    workload_kind: "Deployment",
    workload_name: "hermes-api",
    resolved: true,
    preset_key: "kubernetes.baseline.v1",
    health_policy_key: "default.v1",
    revision: 1,
    cluster: { cluster_ref: "cluster-a", id: "c1" },
  },
  health: {
    status: "healthy",
    computed_at: "2026-08-08T12:00:00Z",
    newest_sample_at: "2026-08-08T11:59:30Z",
    freshness_age_seconds: 30,
    partial: false,
    served_from_last_good: false,
    reasons: [],
    availability: { ready_replicas: 3, desired_replicas: 3 },
    stability: { restarts_in_window: 0 },
    resources: { cpu_utilization: 0.2, memory_utilization: 0.45 },
  },
};

const UNBOUND_ROW = {
  environment_service_id: "es2",
  project_id: "p1",
  project_key: "pilot",
  environment_id: "e1",
  environment_key: "dev",
  service_key: "web",
  display_name: "Web",
  component: "web",
  binding: null,
  health: {
    status: "not_configured",
    computed_at: "2026-08-08T12:00:00Z",
    newest_sample_at: null,
    freshness_age_seconds: null,
    partial: false,
    served_from_last_good: false,
    reasons: ["no_binding"],
    availability: {},
    stability: {},
    resources: {},
  },
};

describe("service health in Turkish", () => {
  it("renders the list page through the serviceHealth catalogue", async () => {
    installFetchMock({
      "/v1/service-health/services": {
        status: 200,
        body: { items: [BOUND_ROW, UNBOUND_ROW], total: 2, limit: 25, offset: 0 },
      },
    });
    render(
      <LocaleProvider locale="tr">
        <ServiceHealthPage />
      </LocaleProvider>,
    );

    // Page title, from the catalogue rather than the English literal.
    expect(await screen.findByRole("heading", { level: 1, name: "Servis sağlığı" })).toBeInTheDocument();

    // Enum labels: the status badge and the binding state badge.
    const bound = await screen.findByTestId("service-row-api");
    expect(within(bound).getByText("Sağlıklı")).toBeInTheDocument();
    expect(within(bound).getByText("Çözümlendi")).toBeInTheDocument();
    expect(within(bound).getByText("30 sn önce", { exact: false })).toBeInTheDocument();

    // An unbound service: its status and the action that fixes it.
    const unbound = screen.getByTestId("service-row-web");
    expect(within(unbound).getByText("Yapılandırılmamış")).toBeInTheDocument();
    expect(within(unbound).getByText("Bağlı iş yükü yok")).toBeInTheDocument();
    expect(within(unbound).getByRole("link", { name: "İş yükü bağla" })).toBeInTheDocument();

    // The status filter offers the Turkish "all" from the common namespace.
    const filter = screen.getByRole("group", { name: "Duruma göre filtrele" });
    expect(within(filter).getByRole("button", { name: /Tümü/ })).toBeInTheDocument();

    // Nothing English leaked into the KPI strip.
    expect(screen.getByText("Kapsamdaki servisler")).toBeInTheDocument();
    expect(screen.queryByText("Services in scope")).not.toBeInTheDocument();
  });
});
