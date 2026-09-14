import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ClustersPage from "@/app/clusters/page";
import IntegrationsPage from "@/app/integrations/page";
import ProjectOverviewPage from "@/app/projects/[projectId]/page";
import ProjectsPage from "@/app/projects/page";
import { CatalogSearch } from "@/components/shell/CatalogSearch";
import { OperationalGrid } from "@/components/catalog/primitives";
import { SessionProvider } from "@/lib/session";
import { errorBody, installFetchMock, makeMe } from "@/test/mock-api";

/** The palette needs a session to resolve page-result permissions. */
function renderSearch() {
  return render(
    <SessionProvider>
      <CatalogSearch />
    </SessionProvider>,
  );
}

const routerPush = vi.fn();
const routerReplace = vi.fn();

/**
 * A stateful router mock for the Projects page's URL-backed filters
 * (`risk=`, in this file). A mock whose `replace` threw the new query away
 * would make every filter look broken here while working perfectly in the
 * browser, so this one reflects it back through `useSearchParams` and
 * notifies subscribers, the way Next does — same pattern as
 * `inventory-screens.test.tsx`.
 */
const { routerState, listeners } = vi.hoisted(() => ({
  routerState: { params: new URLSearchParams() },
  listeners: new Set<() => void>(),
}));

vi.mock("next/navigation", async () => {
  const react = await import("react");
  return {
    usePathname: () => "/projects",
    useRouter: () => ({
      push: routerPush,
      replace: (href: string) => {
        routerReplace(href);
        const query = href.includes("?") ? href.slice(href.indexOf("?") + 1) : "";
        routerState.params = new URLSearchParams(query);
        listeners.forEach((listener) => listener());
      },
    }),
    useSearchParams: () =>
      react.useSyncExternalStore(
        (onChange: () => void) => {
          listeners.add(onChange);
          return () => listeners.delete(onChange);
        },
        () => routerState.params,
        () => routerState.params,
      ),
    useParams: () => ({ projectId: "p1", environmentId: "e1", serviceId: "s1" }),
  };
});

const PROJECT = {
  id: "p1",
  project_key: "alpha",
  display_name: "Alpha",
  lifecycle: "active",
  criticality: "high",
  tenant_model: "none",
  repository: { provider: "github", owner: "example-org", name: "alpha", default_branch: "dev" },
  version: 1,
  scope: { type: "project", ref: "alpha" },
  source: { kind: "fixture", ref: "fixture:alpha", revision: "v1", accepted_at: "2026-08-06T00:00:00Z" },
  counts: { environments: 2, services: 3 },
  as_of: "2026-08-06T00:00:00Z",
};

const SECOND_PROJECT = {
  ...PROJECT,
  id: "p2",
  project_key: "beta",
  display_name: "Beta",
  criticality: "low",
};

const ENVIRONMENT = {
  id: "env-1",
  environment_key: "prod",
  runtime: "kubernetes",
  branch: "main",
  criticality: "high",
  namespace: "default",
  lifecycle: "active",
  cluster: { ref: "cluster-a", display_name: "Cluster A" },
  hosting_provider: null,
  version: 1,
  scope: { type: "environment", ref: "env-1" },
  source: { kind: "fixture", ref: "fixture:env-1", revision: "v1", accepted_at: "2026-08-06T00:00:00Z" },
  as_of: "2026-08-06T00:00:00Z",
};

function serviceRow(status: string) {
  return {
    environment_service_id: "es-1",
    project_id: "p1",
    project_key: "alpha",
    environment_id: "env-1",
    environment_key: "prod",
    service_key: "checkout",
    display_name: null,
    component: null,
    binding: null,
    health: {
      status,
      computed_at: "2026-08-06T00:00:00Z",
      newest_sample_at: null,
      freshness_age_seconds: null,
      partial: false,
      served_from_last_good: false,
      reasons: [],
      availability: {},
      stability: {},
      resources: {},
    },
  };
}

describe("catalog screens", () => {
  beforeEach(() => {
    routerState.params = new URLSearchParams();
    routerPush.mockClear();
    routerReplace.mockClear();
  });
  afterEach(() => vi.unstubAllGlobals());

  it("project list: success renders real catalog rows with badges", async () => {
    installFetchMock({
      "/v1/projects": {
        status: 200,
        body: { projects: [PROJECT], next_cursor: null, as_of: "now" },
      },
    });
    render(<ProjectsPage />);
    await waitFor(() => expect(screen.getByTestId("project-list")).toBeInTheDocument());
    // The portfolio risk map above the table also names this project, so
    // the row lookup is scoped to the table, not the whole page.
    const table = within(screen.getByTestId("project-list"));
    const row = table.getByRole("row", { name: /Alpha/ });
    expect(within(row).getByText("Alpha")).toBeInTheDocument();
    // Counts are their own right-aligned, tabular columns now: the point of
    // the table is that these can be compared down a column.
    expect(within(row).getByText("2")).toBeInTheDocument();
    expect(within(row).getByText("3")).toBeInTheDocument();
    // Badge labels are sentence case now; the state itself is unchanged.
    expect(within(row).getByTestId("status-warning")).toHaveTextContent(/high/i);
  });

  it("project list: empty state is honest, not an error", async () => {
    installFetchMock({
      "/v1/projects": {
        status: 200,
        body: { projects: [], next_cursor: null, as_of: "now" },
      },
    });
    render(<ProjectsPage />);
    await waitFor(() => expect(screen.getByTestId("state-empty")).toBeInTheDocument());
    expect(screen.getByText(/no projects in your scope/i)).toBeInTheDocument();
  });

  it("project list: error state shows correlation id and retry", async () => {
    installFetchMock({
      "/v1/projects": {
        status: 503,
        body: {
          error: {
            code: "dependency_unavailable",
            message: "catalog unavailable",
            correlation_id: "corr-cat-123",
          },
        },
      },
    });
    render(<ProjectsPage />);
    await waitFor(() => expect(screen.getByTestId("state-error")).toBeInTheDocument());
    expect(screen.getByText(/corr-cat-123/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("project list: recorded criticality and observed health are shown independently, never conflated", async () => {
    installFetchMock({
      "/v1/projects": {
        status: 200,
        body: { projects: [PROJECT], next_cursor: null, as_of: "now" },
      },
      "/v1/service-health/services": {
        status: 200,
        body: { items: [serviceRow("critical")], total: 1, limit: 100, offset: 0 },
      },
    });
    render(<ProjectsPage />);
    await screen.findByTestId("project-list");
    const table = within(screen.getByTestId("project-list"));
    const row = await table.findByRole("row", { name: /Alpha/ });
    await waitFor(() => expect(within(row).getByText("High criticality")).toBeInTheDocument());
    expect(within(row).getByText("Critical health")).toBeInTheDocument();
  });

  it("project list: an incomplete project collection is marked Partial view", async () => {
    installFetchMock({
      "/v1/projects": {
        status: 200,
        body: { projects: [PROJECT], next_cursor: "next", as_of: "now" },
      },
      "/v1/service-health/services": {
        status: 200,
        body: { items: [], total: 0, limit: 100, offset: 0 },
      },
    });
    render(<ProjectsPage />);
    await waitFor(() => expect(screen.getAllByText("Alpha").length).toBeGreaterThan(0));
    expect(screen.getByText("Partial view")).toBeInTheDocument();
  });

  it("project list: clicking the risk map's critical group filters the table through ?risk=critical", async () => {
    installFetchMock({
      "/v1/projects": {
        status: 200,
        body: { projects: [PROJECT], next_cursor: null, as_of: "now" },
      },
      "/v1/service-health/services": {
        status: 200,
        body: { items: [serviceRow("critical")], total: 1, limit: 100, offset: 0 },
      },
    });
    render(<ProjectsPage />);
    // Both the risk map and the table name this project, so presence is
    // checked without assuming a single match.
    await waitFor(() => expect(screen.getAllByText("Alpha").length).toBeGreaterThan(0));
    fireEvent.click(await screen.findByRole("button", { name: "Critical" }));
    await waitFor(() => expect(routerReplace).toHaveBeenCalled());
    const lastHref = routerReplace.mock.calls.at(-1)?.[0] as string;
    expect(new URL(lastHref, "http://test").searchParams.get("risk")).toBe("critical");
  });

  it("project list: Load more projects fetches the next page only after the button is clicked", async () => {
    let projectCalls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.startsWith("/v1/projects")) {
          projectCalls += 1;
          const body = url.includes("cursor=next")
            ? { projects: [SECOND_PROJECT], next_cursor: null, as_of: "now" }
            : { projects: [PROJECT], next_cursor: "next", as_of: "now" };
          return new Response(JSON.stringify(body), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        if (url.startsWith("/v1/service-health/services")) {
          return new Response(JSON.stringify({ items: [], total: 0, limit: 100, offset: 0 }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        return new Response("{}", { status: 404 });
      }),
    );
    render(<ProjectsPage />);
    await waitFor(() => expect(screen.getAllByText("Alpha").length).toBeGreaterThan(0));
    expect(projectCalls).toBe(1);
    expect(screen.queryAllByText("Beta").length).toBe(0);

    fireEvent.click(screen.getByRole("button", { name: /load more projects/i }));
    await waitFor(() => expect(screen.getAllByText("Beta").length).toBeGreaterThan(0));
    expect(screen.getAllByText("Alpha").length).toBeGreaterThan(0);
  });

  it("project list: zero matched services is Unassessed, never a healthy badge", async () => {
    installFetchMock({
      "/v1/projects": {
        status: 200,
        body: { projects: [PROJECT], next_cursor: null, as_of: "now" },
      },
      "/v1/service-health/services": {
        status: 200,
        body: { items: [], total: 0, limit: 100, offset: 0 },
      },
    });
    render(<ProjectsPage />);
    await screen.findByTestId("project-list");
    const table = within(screen.getByTestId("project-list"));
    const row = await table.findByRole("row", { name: /Alpha/ });
    await waitFor(() => expect(within(row).getByText(/Unassessed/)).toBeInTheDocument());
    expect(within(row).queryByText(/healthy/i)).not.toBeInTheDocument();
  });

  it("project detail: names worst observed health separately from the recorded criticality", async () => {
    installFetchMock({
      "/v1/me": { status: 200, body: makeMe() },
      "/v1/projects/p1": { status: 200, body: PROJECT },
      "/v1/projects/p1/environments": {
        status: 200,
        body: { environments: [ENVIRONMENT], next_cursor: null, as_of: "now" },
      },
      "/v1/service-health/services": {
        status: 200,
        body: { items: [serviceRow("critical")], total: 1, limit: 100, offset: 0 },
      },
    });
    render(
      <SessionProvider>
        <ProjectOverviewPage />
      </SessionProvider>,
    );
    await waitFor(() => expect(screen.getByText("Alpha")).toBeInTheDocument());
    expect(screen.getByText("High criticality")).toBeInTheDocument();
    expect(screen.getByText(/Critical across observed environments/)).toBeInTheDocument();
  });

  it("project detail: the environment topology lists a working service link", async () => {
    installFetchMock({
      "/v1/me": { status: 200, body: makeMe() },
      "/v1/projects/p1": { status: 200, body: PROJECT },
      "/v1/projects/p1/environments": {
        status: 200,
        body: { environments: [ENVIRONMENT], next_cursor: null, as_of: "now" },
      },
      "/v1/service-health/services": {
        status: 200,
        body: { items: [serviceRow("healthy")], total: 1, limit: 100, offset: 0 },
      },
    });
    render(
      <SessionProvider>
        <ProjectOverviewPage />
      </SessionProvider>,
    );
    const lanes = await screen.findByTestId("environment-list");
    expect(within(lanes).getByRole("heading", { name: /prod/i })).toBeInTheDocument();
    const link = within(lanes).getByRole("link", { name: /checkout/i });
    expect(link).toHaveAttribute("href", "/projects/p1/environments/env-1/services/es-1");
  });

  it("project detail: an incomplete service page says so and links to full service health", async () => {
    installFetchMock({
      "/v1/me": { status: 200, body: makeMe() },
      "/v1/projects/p1": { status: 200, body: PROJECT },
      "/v1/projects/p1/environments": {
        status: 200,
        body: { environments: [ENVIRONMENT], next_cursor: null, as_of: "now" },
      },
      "/v1/service-health/services": {
        status: 200,
        body: { items: [serviceRow("healthy")], total: 50, limit: 100, offset: 0 },
      },
    });
    render(
      <SessionProvider>
        <ProjectOverviewPage />
      </SessionProvider>,
    );
    // The topology lane for env-1 also reads "Evidence incomplete" in its own
    // header badge, agreeing with the page-level note — both are honest
    // about the same partial load, so presence is checked without assuming
    // a single match.
    await waitFor(() =>
      expect(screen.getAllByText("Evidence incomplete").length).toBeGreaterThan(0),
    );
    expect(screen.getByRole("link", { name: /view full service health/i })).toHaveAttribute(
      "href",
      "/service-health?project_id=p1",
    );
  });

  it("clusters: empty scope stays empty (no fabricated inventory)", async () => {
    installFetchMock({
      "/v1/clusters": {
        status: 200,
        body: { clusters: [], next_cursor: null, as_of: "now" },
      },
    });
    render(<ClustersPage />);
    await waitFor(() => expect(screen.getByTestId("state-empty")).toBeInTheDocument());
  });

  it("integrations: safe fields only, states never faked healthy", async () => {
    installFetchMock({
      "/v1/integrations/health": {
        status: 200,
        body: {
          integrations: [
            {
              integration_type: "prometheus",
              scope: { type: "project", ref: "alpha" },
              configuration_state: "not_configured",
              observed_state: "unknown",
              last_sync_attempt_at: null,
              last_success_at: null,
              last_error_code: null,
              schema_version: 1,
              as_of: "2026-08-06T00:00:00Z",
            },
          ],
          as_of: "now",
        },
      },
    });
    render(<IntegrationsPage />);
    await waitFor(() =>
      expect(screen.getByTestId("integration-table")).toBeInTheDocument(),
    );
    expect(screen.getByText("not_configured")).toBeInTheDocument();
    expect(screen.getByText("unknown")).toBeInTheDocument();
    expect(screen.getByText("never")).toBeInTheDocument();
    expect(screen.queryByText(/healthy/i)).not.toBeInTheDocument();
  });

  it("operational grid renders honest states: unknown is never zero", () => {
    render(
      <OperationalGrid
        states={{ telemetry: "not_configured", inventory: "unknown", deployment: "stale" }}
        labels={{ telemetry: "Telemetry", inventory: "Inventory", deployment: "Deployments" }}
      />,
    );
    expect(screen.getByTestId("state-not-configured")).toBeInTheDocument();
    expect(screen.getByTestId("state-unknown")).toBeInTheDocument();
    expect(screen.getByTestId("state-stale")).toBeInTheDocument();
    expect(screen.getByTestId("state-unknown")).not.toHaveTextContent("0");
  });

  it("operational grid: a reporting capability is a status, never 'Zero'", () => {
    // `ok` used to map to the `zero` DataState, whose own description reads
    // "The source reported an actual value of 0." So a service with working
    // metrics rendered a sentence that was false. The same conflation put
    // `degraded` — which means it IS answering, just not well — behind
    // "Query failed".
    render(
      <OperationalGrid
        states={{ metrics: "ok", deployments: "degraded", logs: "not_configured" }}
        labels={{ metrics: "Metrics", deployments: "Deployments", logs: "Logs" }}
      />,
    );
    expect(screen.getByText("Reporting")).toBeInTheDocument();
    expect(screen.getByText("Degraded")).toBeInTheDocument();
    expect(screen.getByTestId("state-not-configured")).toBeInTheDocument();

    // The two false sentences must not be on the screen at all.
    expect(screen.queryByText(/actual value of 0/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/did not complete/i)).not.toBeInTheDocument();
    expect(screen.queryByTestId("state-zero")).not.toBeInTheDocument();
    expect(screen.queryByTestId("state-error")).not.toBeInTheDocument();
  });

  it("search dialog: opens, queries, navigates with keyboard, closes on escape", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    installFetchMock({
      "/v1/me": { status: 200, body: makeMe() },
      "/v1/catalog/search": {
        status: 200,
        body: {
          results: [
            {
              kind: "project", id: "p1", key: "alpha", display_name: "Alpha",
              project_key: "alpha", parent_id: null, project_id: "p1",
            },
          ],
        },
      },
    });
    renderSearch();
    fireEvent.click(screen.getAllByRole("button", { name: /search drake/i })[0]);
    const input = await screen.findByLabelText("Search query");
    fireEvent.change(input, { target: { value: "alp" } });
    await vi.advanceTimersByTimeAsync(300);
    await waitFor(() => expect(screen.getByRole("option")).toBeInTheDocument());

    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Enter" });
    expect(routerPush).toHaveBeenCalledWith("/projects/p1");

    fireEvent.click(screen.getAllByRole("button", { name: /search drake/i })[0]);
    fireEvent.keyDown(await screen.findByRole("dialog"), { key: "Escape" });
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    vi.useRealTimers();
  });

  it("search dialog: empty result set says so without inventing rows", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    installFetchMock({
      "/v1/me": { status: 200, body: makeMe() },
      "/v1/catalog/search": { status: 200, body: { results: [] } },
    });
    renderSearch();
    fireEvent.click(screen.getAllByRole("button", { name: /search drake/i })[0]);
    fireEvent.change(await screen.findByLabelText("Search query"), {
      target: { value: "ghost" },
    });
    await vi.advanceTimersByTimeAsync(300);
    await waitFor(() =>
      expect(screen.getByText(/no authorized results/i)).toBeInTheDocument(),
    );
    vi.useRealTimers();
  });

  it("search dialog: error state is typed, not silent", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    installFetchMock({
      "/v1/me": { status: 200, body: makeMe() },
      "/v1/catalog/search": {
        status: 503,
        body: errorBody("dependency_unavailable", "search unavailable"),
      },
    });
    renderSearch();
    fireEvent.click(screen.getAllByRole("button", { name: /search drake/i })[0]);
    fireEvent.change(await screen.findByLabelText("Search query"), {
      target: { value: "alpha" },
    });
    await vi.advanceTimersByTimeAsync(300);
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/search unavailable/i),
    );
    vi.useRealTimers();
  });
});
