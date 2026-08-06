import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ClustersPage from "@/app/clusters/page";
import IntegrationsPage from "@/app/integrations/page";
import ProjectsPage from "@/app/projects/page";
import { CatalogSearch } from "@/components/shell/CatalogSearch";
import { OperationalGrid } from "@/components/catalog/primitives";
import { errorBody, installFetchMock } from "@/test/mock-api";

const routerPush = vi.fn();
vi.mock("next/navigation", () => ({
  usePathname: () => "/projects",
  useRouter: () => ({ push: routerPush, replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({ projectId: "p1", environmentId: "e1", serviceId: "s1" }),
}));

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

describe("catalog screens", () => {
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
    expect(screen.getByText("Alpha")).toBeInTheDocument();
    expect(screen.getByText(/2 env · 3 services/)).toBeInTheDocument();
    expect(screen.getByText("high")).toBeInTheDocument();
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

  it("search dialog: opens, queries, navigates with keyboard, closes on escape", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    installFetchMock({
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
    render(<CatalogSearch />);
    fireEvent.click(screen.getAllByRole("button", { name: /search catalog/i })[0]);
    const input = await screen.findByLabelText("Search query");
    fireEvent.change(input, { target: { value: "alp" } });
    await vi.advanceTimersByTimeAsync(300);
    await waitFor(() => expect(screen.getByRole("option")).toBeInTheDocument());

    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Enter" });
    expect(routerPush).toHaveBeenCalledWith("/projects/p1");

    fireEvent.click(screen.getAllByRole("button", { name: /search catalog/i })[0]);
    fireEvent.keyDown(await screen.findByRole("dialog"), { key: "Escape" });
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    vi.useRealTimers();
  });

  it("search dialog: empty result set says so without inventing rows", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    installFetchMock({
      "/v1/catalog/search": { status: 200, body: { results: [] } },
    });
    render(<CatalogSearch />);
    fireEvent.click(screen.getAllByRole("button", { name: /search catalog/i })[0]);
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
      "/v1/catalog/search": {
        status: 503,
        body: errorBody("dependency_unavailable", "search unavailable"),
      },
    });
    render(<CatalogSearch />);
    fireEvent.click(screen.getAllByRole("button", { name: /search catalog/i })[0]);
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
