/**
 * Error tracking (ADR-0036): the environment's issues, grouped, newest
 * first, each opening in place with its sample.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ErrorsPage from "@/app/errors/page";
import { SessionProvider } from "@/lib/session";
import { installFetchMock, makeMe } from "@/test/mock-api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/errors",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

const ENV = {
  environment_id: "e1", environment_key: "test", project_id: "p1", project_key: "hermes", project_name: "Hermes",
  can_manage: false, title: "Hermes · test", version: 0, saved: false, updated_at: null, services: 1,
};

function row(id: string, name: string) {
  return {
    environment_service_id: `es-${id}`, project_id: "p1", project_key: "hermes", environment_id: "e1", environment_key: "test",
    service_key: name, display_name: null, component: null,
    binding: { id, lifecycle: "active", namespace: "hermes-test", workload_kind: "Deployment", workload_name: name, resolved: true, preset_key: "x", health_policy_key: "y", revision: 1, cluster: { cluster_ref: "c", id: "c1" } },
    health: { status: "healthy", computed_at: "", newest_sample_at: null, freshness_age_seconds: 1, partial: false, served_from_last_good: false, reasons: [], availability: {}, stability: {}, resources: {} },
  };
}

const now = new Date().toISOString();
const ok = { state: "ok" };
function read(issues: unknown[]) {
  return {
    range: "24h", as_of: now, bucket_seconds: 3600, buckets: [1, 2, 3],
    sections: { exception: ok, failed_request: ok, error_log: ok },
    issues,
    summary: {
      issues: { exception: 1, failed_request: 0, error_log: 1 },
      events: { exception: 7, failed_request: 0, error_log: 30 },
      total_issues: issues.length,
    },
  };
}
const EXC = {
  id: "i1", kind: "exception", binding_id: "auth", title: "KeyError: missing key <id>", count: 7,
  first_seen: now, last_seen: now, trend: [3, 0, 4], trace_id: "abc123", message: "missing key 42",
  exception_type: "KeyError", stacktrace: "Traceback (most recent call last):", routes: ["GET /cart"],
};
const LOG = {
  id: "i2", kind: "error_log", binding_id: "auth", title: 'FATAL: database "<s>" does not exist', count: 30,
  first_seen: now, last_seen: now, trend: [30, 0, 0], trace_id: null, message: 'FATAL: database "hermes" does not exist',
  severity_number: 21,
};

function routes(issues: unknown[]) {
  return installFetchMock({
    "/v1/me": { status: 200, body: makeMe({ permissions: ["environment.view", "traces.view", "logs.view"] }) },
    "/v1/dashboards": { status: 200, body: { items: [ENV] } },
    "/v1/service-health/services": { status: 200, body: { items: [row("auth", "auth-service")], total: 1, limit: 100, offset: 0 } },
    "/v1/service-health/errors": { status: 200, body: read(issues) },
  });
}

afterEach(() => vi.unstubAllGlobals());

describe("error tracking", () => {
  it("lists the environment's issues and opens one in place", async () => {
    const calls = routes([EXC, LOG]);
    render(<SessionProvider><ErrorsPage /></SessionProvider>);
    const issue = await screen.findByRole("button", { name: /KeyError: missing key <id>/ });
    const asked = calls.find((c) => c.path.startsWith("/v1/service-health/errors"))!;
    expect(asked.path).toContain("binding_id=auth");
    expect(asked.path).toContain("range=24h");
    fireEvent.click(issue);
    const item = issue.closest("li")!;
    expect(within(item).getByText("Traceback (most recent call last):")).toBeInTheDocument();
    expect(within(item).getByText("GET /cart")).toBeInTheDocument();
    expect(within(item).getByRole("link", { name: "Open its service health page" })).toHaveAttribute("href", "/service-health/auth");
  });

  it("filters by kind from the summary", async () => {
    routes([EXC, LOG]);
    render(<SessionProvider><ErrorsPage /></SessionProvider>);
    await screen.findByRole("button", { name: /KeyError/ });
    fireEvent.click(screen.getByRole("button", { name: /^Error logs 30/ }));
    await waitFor(() => expect(screen.queryByRole("button", { name: /KeyError/ })).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: /does not exist/ })).toBeInTheDocument();
  });

  it("says plainly when nothing went wrong", async () => {
    routes([]);
    render(<SessionProvider><ErrorsPage /></SessionProvider>);
    expect(await screen.findByText("No errors in the last 24 hours")).toBeInTheDocument();
  });
});
