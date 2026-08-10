import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import IncidentDetailPage from "@/app/incidents/[incidentId]/page";
import IncidentsPage from "@/app/incidents/page";
import {
  HealthTransitions,
  ServiceIncidents,
} from "@/components/incidents/ServiceIncidents";
import { SessionProvider } from "@/lib/session";
import { errorBody, installFetchMock, makeMe } from "@/test/mock-api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/incidents",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({ incidentId: "inc-1", bindingId: "b1" }),
}));

const BINDING = {
  id: "b1",
  namespace: "pilot-dev",
  workload_kind: "Deployment",
  workload_name: "pilot-api",
  cluster_ref: "cluster-a",
};

function summary(overrides: Record<string, unknown> = {}) {
  return {
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
    binding: BINDING,
    current_health: {
      status: "critical",
      reasons: ["no_ready_replicas"],
      last_observed_at: "2026-08-08T12:05:00Z",
    },
    ...overrides,
  };
}

function detail(overrides: Record<string, unknown> = {}) {
  return {
    ...summary(),
    opening_reasons: ["no_ready_replicas"],
    binding_revision: 1,
    resolution_source: null,
    acknowledged_by: null,
    project_id: "p1",
    environment_id: "e1",
    can_acknowledge: true,
    ...overrides,
  };
}

const EVENTS = {
  events: [
    {
      event_type: "opened",
      occurred_at: "2026-08-08T12:00:00Z",
      detail: { primary_reason: "no_ready_replicas" },
      actor: null,
    },
  ],
};

afterEach(() => vi.unstubAllGlobals());

// --- list ---------------------------------------------------------------

describe("incident list", () => {
  it("shows severity, state, workload, reason, opened time and current health", async () => {
    installFetchMock({
      "/v1/incidents": {
        status: 200,
        body: { items: [summary()], next_cursor: null, total: 1, limit: 25 },
      },
    });
    render(<IncidentsPage />);

    const row = await screen.findByTestId("incident-row-api");
    expect(within(row).getByText("api (dev): No replicas ready")).toBeInTheDocument();
    expect(within(row).getByText("critical")).toBeInTheDocument();
    expect(within(row).getByText("Open")).toBeInTheDocument();
    expect(within(row).getByText(/cluster-a\/pilot-dev\/pilot-api/)).toBeInTheDocument();
    expect(within(row).getByText("No replicas ready")).toBeInTheDocument();
    // Current health is the backend's status, rendered — not recomputed.
    expect(within(row).getByText("Critical")).toBeInTheDocument();
  });

  it("offers only the allowlisted filter values", async () => {
    installFetchMock({
      "/v1/incidents": {
        status: 200,
        body: { items: [summary()], next_cursor: null, total: 1, limit: 25 },
      },
    });
    render(<IncidentsPage />);
    await screen.findByTestId("incident-table");

    const filters = screen.getByRole("group", { name: /filters/i });
    // Selects only: there is no free-text filter behind this screen, so
    // there is nowhere to type a query.
    expect(within(filters).queryByRole("textbox")).not.toBeInTheDocument();
    const state = within(filters).getByLabelText(/state/i);
    expect(
      Array.from((state as HTMLSelectElement).options).map((option) => option.value),
    ).toEqual(["", "open", "acknowledged", "resolved"]);
  });

  it("separates empty from permission denied from error", async () => {
    installFetchMock({
      "/v1/incidents": {
        status: 200,
        body: { items: [], next_cursor: null, total: 0, limit: 25 },
      },
    });
    const { unmount } = render(<IncidentsPage />);
    expect(await screen.findByTestId("state-empty")).toBeInTheDocument();
    unmount();

    installFetchMock({
      "/v1/incidents": { status: 404, body: errorBody("not_found", "not found") },
    });
    const denied = render(<IncidentsPage />);
    expect(await screen.findByTestId("state-permission-denied")).toBeInTheDocument();
    denied.unmount();

    installFetchMock({
      "/v1/incidents": { status: 503, body: errorBody("unavailable", "database is down") },
    });
    render(<IncidentsPage />);
    expect(await screen.findByTestId("state-error")).toBeInTheDocument();
  });

  it("shows a loading state before the first response", () => {
    installFetchMock({});
    render(<IncidentsPage />);
    expect(screen.getByTestId("state-loading")).toBeInTheDocument();
  });
});

// --- detail -------------------------------------------------------------

function renderDetail(
  overrides: Record<string, unknown> = {},
  routes: Record<string, { status: number; body: unknown }> = {},
  permissions: string[] = ["environment.view", "incident.ack"],
) {
  const calls = installFetchMock({
    "/v1/me": { status: 200, body: makeMe({ permissions }) },
    "/v1/incidents/inc-1": { status: 200, body: detail(overrides) },
    "/v1/incidents/inc-1/events": { status: 200, body: EVENTS },
    ...routes,
  });
  render(
    <SessionProvider>
      <IncidentDetailPage />
    </SessionProvider>,
  );
  return calls;
}

describe("incident detail", () => {
  it("renders the opening reasons, context and timeline", async () => {
    renderDetail();
    expect(await screen.findByText("api (dev): No replicas ready")).toBeInTheDocument();
    // Two reason lists on this screen: why it opened, and what health says
    // right now. The first is the opening snapshot.
    expect(screen.getAllByTestId("incident-reasons")[0]).toHaveTextContent(
      "No replicas ready",
    );

    const timeline = await screen.findByTestId("incident-timeline");
    expect(within(timeline).getByText("Incident opened")).toBeInTheDocument();
    expect(within(timeline).getByText("2026-08-08T12:00:00Z")).toBeInTheDocument();
  });

  it("explains that acknowledging does not close the incident", async () => {
    renderDetail();
    await screen.findByRole("button", { name: /^acknowledge$/i });
    expect(screen.getByText(/does not close the incident/i)).toBeInTheDocument();
  });

  it("acknowledges and reports what changed", async () => {
    renderDetail({}, {
      "/v1/incidents/inc-1/acknowledge": {
        status: 200,
        body: { id: "inc-1", state: "acknowledged", version: 2, changed: true },
      },
    });
    const button = await screen.findByRole("button", { name: /^acknowledge$/i });
    fireEvent.click(button);

    const notice = await screen.findByTestId("ack-notice");
    expect(notice).toHaveTextContent(/monitoring continues/i);
  });

  it("sends only a version — never an actor or a note", async () => {
    const calls = renderDetail({}, {
      "/v1/incidents/inc-1/acknowledge": {
        status: 200,
        body: { id: "inc-1", state: "acknowledged", version: 2, changed: true },
      },
    });
    fireEvent.click(await screen.findByRole("button", { name: /^acknowledge$/i }));
    await screen.findByTestId("ack-notice");

    const call = calls.find((entry) => entry.path.endsWith("/acknowledge"));
    expect(call).toBeDefined();
    expect(JSON.parse(String(call?.init?.body))).toEqual({ expected_version: 1 });
  });

  it("explains a version conflict instead of retrying silently", async () => {
    renderDetail({}, {
      "/v1/incidents/inc-1/acknowledge": {
        status: 409,
        body: errorBody("conflict", "the incident changed since it was read"),
      },
    });
    fireEvent.click(await screen.findByRole("button", { name: /^acknowledge$/i }));

    const conflict = await screen.findByTestId("ack-conflict");
    expect(conflict).toHaveTextContent(/someone else acted on it/i);
    expect(within(conflict).getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("hides acknowledge from a caller without the permission", async () => {
    renderDetail({ can_acknowledge: false });
    await screen.findByText("api (dev): No replicas ready");
    expect(screen.queryByRole("button", { name: /^acknowledge$/i })).not.toBeInTheDocument();
    expect(screen.getByTestId("state-permission-denied")).toBeInTheDocument();
  });

  it("offers no acknowledge control on a resolved incident", async () => {
    renderDetail({
      state: "resolved",
      resolved_at: "2026-08-08T13:00:00Z",
      resolution_source: "health_recovered",
    });
    expect(await screen.findByText(/health recovered/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /acknowledge/i })).not.toBeInTheDocument();
    expect(screen.getAllByText("Resolved").length).toBeGreaterThan(0);
  });

  it("renders no query, credential or raw payload", async () => {
    renderDetail();
    await screen.findByTestId("incident-timeline");
    const text = document.body.textContent ?? "";
    for (const forbidden of ["sum(rate(", "kube_workload", "promql", "config_ref"]) {
      expect(text.toLowerCase()).not.toContain(forbidden);
    }
  });
});

// --- service detail integration ------------------------------------------

describe("service health integration", () => {
  it("surfaces an open incident with a link to it", async () => {
    installFetchMock({
      "/v1/service-health/bindings/b1/incidents": {
        status: 200,
        body: { items: [summary()], total: 1 },
      },
    });
    render(<ServiceIncidents bindingId="b1" />);

    const link = await screen.findByRole("link", { name: /open incident/i });
    expect(link).toHaveAttribute("href", "/incidents/inc-1");
    expect(screen.getByTestId("recent-incidents")).toBeInTheDocument();
  });

  it("shows an honest empty state when a service has never had one", async () => {
    installFetchMock({
      "/v1/service-health/bindings/b1/incidents": { status: 200, body: { items: [], total: 0 } },
    });
    render(<ServiceIncidents bindingId="b1" />);
    expect(await screen.findByText("No incidents")).toBeInTheDocument();
  });

  it("renders recorded health transitions, including the first observation", async () => {
    installFetchMock({
      "/v1/service-health/bindings/b1/transitions": {
        status: 200,
        body: {
          transitions: [
            {
              previous_status: "healthy",
              new_status: "critical",
              reasons: ["no_ready_replicas"],
              computed_at: "2026-08-08T12:00:00Z",
              recorded_at: "2026-08-08T12:00:01Z",
              binding_revision: 1,
            },
            {
              previous_status: null,
              new_status: "healthy",
              reasons: [],
              computed_at: "2026-08-08T11:00:00Z",
              recorded_at: "2026-08-08T11:00:01Z",
              binding_revision: 1,
            },
          ],
        },
      },
    });
    render(<HealthTransitions bindingId="b1" />);

    const panel = await screen.findByTestId("health-transitions");
    expect(within(panel).getByText("No replicas ready")).toBeInTheDocument();
    // A first observation is not a transition out of `unknown`, and says so.
    expect(within(panel).getByText("first observation")).toBeInTheDocument();
  });

  it("explains why the transition list is short rather than showing nothing", async () => {
    installFetchMock({
      "/v1/service-health/bindings/b1/transitions": { status: 200, body: { transitions: [] } },
    });
    render(<HealthTransitions bindingId="b1" />);
    await waitFor(() =>
      expect(screen.getByText(/not on every evaluation/i)).toBeInTheDocument(),
    );
  });
});
