import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ServiceExplorer } from "@/components/explore/ServiceExplorer";
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

const traces = (requests: number, errors: number) => ({
  requests,
  errors,
  error_ratio: requests ? errors / requests : null,
  p95_ms: 120,
  newest_at: null,
});

afterEach(() => {
  vi.unstubAllGlobals();
  replace.mockClear();
  search = new URLSearchParams();
});

describe("ServiceExplorer", () => {
  it("lists bound services, failing first, and opens one into the URL", async () => {
    installFetchMock({
      "/v1/service-health/services": {
        status: 200,
        body: { items: [row("quiet-api", "b1"), row("core-service", "b2"), row("unbound", null)], total: 3, limit: 100, offset: 0 },
      },
      "/v1/service-health/signals": {
        status: 200,
        body: {
          range: "1h",
          as_of: "2026-11-21T12:00:00Z",
          logs: { state: "ok" },
          traces: { state: "ok" },
          items: [
            { binding_id: "b1", logs: null, traces: traces(5000, 0) },
            { binding_id: "b2", logs: null, traces: traces(800, 40) },
          ],
        },
      },
    });
    render(<ServiceExplorer kind="traces" />);

    const list = await screen.findByRole("list");
    // Once the last hour's signals arrive, the failing service leads.
    await waitFor(() => expect(within(list).getAllByRole("button")[0]).toHaveTextContent("core-service"));
    const items = within(list).getAllByRole("button");
    // An unbound service has nothing to read and is not offered.
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("5% failed");
    fireEvent.click(items[0]);
    expect(replace).toHaveBeenCalledWith("/traces?binding=b2", { scroll: false });
  });

  it("says so when no service is bound", async () => {
    installFetchMock({
      "/v1/service-health/services": { status: 200, body: { items: [row("web", null)], total: 1, limit: 100, offset: 0 } },
    });
    render(<ServiceExplorer kind="logs" />);
    expect(await screen.findByText(/No service is bound to a workload yet/)).toBeInTheDocument();
  });
});
