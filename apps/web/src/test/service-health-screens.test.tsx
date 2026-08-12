import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ServiceHealthDetailPage from "@/app/service-health/[bindingId]/page";
import ServiceHealthPage from "@/app/service-health/page";
import { BindingForm } from "@/components/service-health/BindingForm";
import { SessionProvider } from "@/lib/session";
import { installFetchMock, makeMe } from "@/test/mock-api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/service-health",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({ bindingId: "b1" }),
}));

const BINDING = {
  id: "b1",
  lifecycle: "active",
  resolved: true,
  resolved_at: "2026-08-08T10:00:00Z",
  revision: 3,
  namespace: "hermes-dev",
  workload_kind: "Deployment",
  workload_name: "hermes-api",
  cluster_ref: "cluster-a",
  cluster_id: "c1",
  preset_key: "kubernetes.baseline.v1",
  health_policy_key: "default.v1",
  project_key: "pilot",
  environment_key: "dev",
  service_key: "api",
  environment_service_id: "es1",
  datasource_configured: true,
};

const SECTION = { status: "healthy", reasons: [] };

function health(overrides: Record<string, unknown> = {}) {
  return {
    status: "healthy",
    computed_at: "2026-08-08T12:00:00Z",
    newest_sample_at: "2026-08-08T11:59:30Z",
    freshness_age_seconds: 30,
    availability: { ...SECTION, desired_replicas: 3, ready_replicas: 3 },
    stability: {
      ...SECTION,
      restarts_in_window: 0,
      crash_looping: false,
      oom_killed: false,
    },
    resources: {
      ...SECTION,
      cpu_cores_used: 0.4,
      cpu_limit_cores: 2,
      cpu_utilization: 0.2,
      memory_bytes_used: 200000000,
      memory_limit_bytes: 1000000000,
      memory_utilization: 0.2,
      cpu_throttled_ratio: 0,
    },
    application: {
      status: "not_configured",
      reasons: ["application_metrics_missing"],
      metrics_present: false,
      request_rate: null,
      error_ratio: null,
      latency_p95_seconds: null,
    },
    reasons: [],
    messages: [],
    missing_signals: [],
    partial: false,
    policy_key: "default.v1",
    binding_id: "b1",
    served_from_last_good: false,
    cached: false,
    binding: BINDING,
    ...overrides,
  };
}

function signal(overrides: Record<string, unknown> = {}) {
  return {
    value: 1,
    state: "ok",
    newest_sample_at: "2026-08-08T11:59:30Z",
    from_cache: false,
    ...overrides,
  };
}

const METRICS = {
  binding: BINDING,
  status: "healthy",
  computed_at: "2026-08-08T12:00:00Z",
  partial: false,
  missing_signals: [],
  readable_signals: ["ready_replicas", "restarts", "cpu_usage", "memory_usage"],
  metrics: {
    availability: {
      desired_replicas: signal({ value: 3 }),
      ready_replicas: signal({ value: 3 }),
    },
    stability: { restarts: signal({ value: 0 }) },
    resources: {
      cpu_usage: signal({ value: 0.4 }),
      cpu_limit: signal({ value: 2 }),
      cpu_utilization: 0.2,
      memory_usage: signal({ value: 200000000 }),
      memory_limit: signal({ value: 1000000000 }),
      memory_utilization: 0.2,
      cpu_throttling: signal({ value: 0 }),
    },
    application: {
      request_rate: signal({ value: null, state: "not_collected" }),
      error_ratio: signal({ value: null, state: "not_collected" }),
      latency_p95: signal({ value: null, state: "not_collected" }),
    },
    freshness: signal({ value: 1 }),
  },
};

const OPTIONS = {
  clusters: [{ id: "c1", cluster_ref: "cluster-a", display_name: "Cluster A" }],
  namespaces: ["hermes-dev", "hermes-prod"],
  workloads: [
    { kind: "Deployment", name: "hermes-api", last_seen_at: "2026-08-08T11:00:00Z" },
    { kind: "StatefulSet", name: "hermes-db", last_seen_at: null },
  ],
  workload_kinds: ["DaemonSet", "Deployment", "StatefulSet"],
  datasource: {
    configured: true,
    integration_type: "prometheus",
    configuration_state: "configured",
    observed_state: "healthy",
    last_success_at: "2026-08-08T11:00:00Z",
  },
  presets: [
    {
      key: "kubernetes.baseline.v1",
      title: "Kubernetes workload (infrastructure signals)",
      description: "Replicas, restarts and resource use.",
      signals: [],
      includes_application_signals: false,
    },
    {
      key: "http.service.v1",
      title: "HTTP service (infrastructure + golden signals)",
      description: "Adds request rate, error ratio and latency.",
      signals: [],
      includes_application_signals: true,
    },
  ],
  policies: [
    { key: "default.v1", title: "Default service health" },
    { key: "tolerant.v1", title: "Tolerant (batch and background workloads)" },
  ],
  ranges: ["15m", "1h", "24h", "6h"],
};

afterEach(() => vi.unstubAllGlobals());

// --- list ---------------------------------------------------------------

function listBody(items: unknown[]) {
  return { items, total: items.length, limit: 25, offset: 0 };
}

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

describe("service health list", () => {
  it("lists unbound services as not configured rather than omitting them", async () => {
    installFetchMock({
      "/v1/service-health/services": {
        status: 200,
        body: listBody([BOUND_ROW, UNBOUND_ROW]),
      },
    });
    render(<ServiceHealthPage />);

    const bound = await screen.findByTestId("service-row-api");
    expect(within(bound).getByText("Healthy")).toBeInTheDocument();
    expect(within(bound).getByText("Resolved")).toBeInTheDocument();

    const unbound = screen.getByTestId("service-row-web");
    expect(within(unbound).getByText("Not configured")).toBeInTheDocument();
    expect(within(unbound).getByRole("link", { name: /bind a workload/i })).toBeInTheDocument();
  });

  it("renders unmeasured values as a dash, never as zero", async () => {
    installFetchMock({
      "/v1/service-health/services": { status: 200, body: listBody([UNBOUND_ROW]) },
    });
    render(<ServiceHealthPage />);

    const row = await screen.findByTestId("service-row-web");
    // Every measurement cell is a dash: an unbound service has no numbers,
    // and a 0 here would read as "idle" instead of "unobserved".
    expect(within(row).queryByText("0")).not.toBeInTheDocument();
    expect(within(row).queryByText("0.0%")).not.toBeInTheDocument();
    expect(within(row).getAllByText("—").length).toBeGreaterThanOrEqual(3);
  });

  it("marks a last-good row so its numbers are not read as current", async () => {
    const stale = {
      ...BOUND_ROW,
      health: {
        ...BOUND_ROW.health,
        status: "stale",
        partial: true,
        served_from_last_good: true,
        reasons: ["telemetry_stale"],
      },
    };
    installFetchMock({
      "/v1/service-health/services": { status: 200, body: listBody([stale]) },
    });
    render(<ServiceHealthPage />);

    const row = await screen.findByTestId("service-row-api");
    expect(within(row).getByTestId("status-stale")).toBeInTheDocument();
    expect(within(row).getByText(/last known values/i)).toBeInTheDocument();
  });
});

// --- detail -------------------------------------------------------------

function renderDetail(overrides: Record<string, unknown> = {}) {
  return installFetchMock({
    "/v1/service-health/bindings/b1/health": { status: 200, body: health(overrides) },
    "/v1/service-health/bindings/b1/metrics": { status: 200, body: METRICS },
    "/v1/service-health/bindings/b1/series": {
      status: 200,
      body: {
        signal: "cpu_usage",
        unit: "cores",
        series: [{ labels: {}, points: [[1000, 0.4]] }],
        series_truncated: false,
        range: {},
        data_state: "ok",
        cache_state: "miss",
        partial: false,
        warnings: [],
        as_of: "2026-08-08T12:00:00Z",
        range_key: "1h",
        binding: BINDING,
      },
    },
  });
}

describe("service health detail", () => {
  it("shows all four sections with the API's own statuses", async () => {
    renderDetail();
    render(<ServiceHealthDetailPage />);

    for (const title of ["Availability", "Stability", "Resources", "Application"]) {
      expect(await screen.findByTestId(`section-${title.toLowerCase()}`)).toBeInTheDocument();
    }
    // Ready-over-desired is a ring plus its exact digits now; the digits are
    // what the assertion is about — a percentage alone would hide the
    // difference between 0/0 and an unmeasured pair.
    const availability = screen.getByTestId("section-availability");
    expect(availability).toHaveTextContent("3/3");
    expect(within(availability).getByTestId("ring-progress")).toBeInTheDocument();
  });

  it("explains a service that publishes no golden signals without blaming it", async () => {
    renderDetail();
    render(<ServiceHealthDetailPage />);

    const application = await screen.findByTestId("section-application");
    expect(within(application).getByText("No application metrics")).toBeInTheDocument();
    expect(within(application).getByText(/not counted against its health/i)).toBeInTheDocument();
    // Not a failure badge: the section reports "not configured".
    expect(within(application).queryByTestId("status-critical")).not.toBeInTheDocument();
  });

  it("renders reason codes as text and lists what was not measured", async () => {
    renderDetail({
      status: "degraded",
      reasons: ["restart_spike", "partial_result"],
      messages: ["Containers are restarting repeatedly.", "Some signals were unavailable."],
      missing_signals: ["resources.limits"],
      partial: true,
    });
    render(<ServiceHealthDetailPage />);

    expect(await screen.findByText("Restart spike")).toBeInTheDocument();
    expect(screen.getByText(/Containers are restarting repeatedly/)).toBeInTheDocument();
    expect(screen.getByTestId("missing-signals")).toHaveTextContent("Resource limits");
  });

  it("labels a last-good answer with the time it was actually computed", async () => {
    renderDetail({
      status: "stale",
      served_from_last_good: true,
      partial: true,
      served_at: "2026-08-08T12:30:00Z",
      reasons: ["telemetry_stale"],
      messages: ["Telemetry is older than this policy allows."],
    });
    render(<ServiceHealthDetailPage />);

    const notice = await screen.findByTestId("freshness-notice");
    expect(notice).toHaveTextContent("Last known values");
    // Both timestamps are present: when it was true, and when it was shown.
    expect(notice).toHaveTextContent("2026-08-08T12:00:00Z");
    expect(notice).toHaveTextContent("2026-08-08T12:30:00Z");
  });

  it("offers only the fixed time ranges for charts", async () => {
    renderDetail();
    render(<ServiceHealthDetailPage />);

    const group = await screen.findByRole("group", { name: /time range/i });
    const labels = within(group)
      .getAllByRole("button")
      .map((button) => button.textContent);
    expect(labels).toEqual(["15m", "1h", "6h", "24h"]);
    // No free-form range entry anywhere on the screen.
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("charts only the signals the binding's preset actually reads", async () => {
    renderDetail();
    render(<ServiceHealthDetailPage />);

    const history = await screen.findByRole("region", { name: /signal history/i });
    expect(within(history).getByText("CPU usage")).toBeInTheDocument();
    // The baseline preset reads no golden signals, so there is no chart for
    // one — and no control that could request it.
    expect(within(history).queryByText("Error ratio")).not.toBeInTheDocument();
  });
});

// --- binding form -------------------------------------------------------

function renderForm(
  routes: Record<string, { status: number; body: unknown }> = {},
  permissions: string[] = ["integration.manage", "environment.view"],
) {
  const calls = installFetchMock({
    "/v1/me": { status: 200, body: makeMe({ permissions }) },
    "/v1/service-health/binding-options": { status: 200, body: OPTIONS },
    ...routes,
  });
  render(
    <SessionProvider>
      <BindingForm environmentServiceId="es1" />
    </SessionProvider>,
  );
  return calls;
}

describe("binding form", () => {
  it("offers selects only — there is nowhere to type a selector or a query", async () => {
    renderForm();
    await screen.findByRole("form", { name: /workload binding/i });

    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.getAllByRole("combobox").length).toBe(5);
  });

  it("clears the namespace and workload when the cluster changes", async () => {
    renderForm();
    await screen.findByRole("form", { name: /workload binding/i });

    const cluster = screen.getByLabelText("Cluster");
    const namespace = screen.getByLabelText("Namespace");
    const workload = screen.getByLabelText("Workload");

    fireEvent.change(cluster, { target: { value: "c1" } });
    await waitFor(() => expect(namespace).not.toBeDisabled());
    fireEvent.change(namespace, { target: { value: "hermes-dev" } });
    await waitFor(() => expect(workload).not.toBeDisabled());
    fireEvent.change(workload, { target: { value: "Deployment/hermes-api" } });
    expect((workload as HTMLSelectElement).value).toBe("Deployment/hermes-api");

    // Changing the cluster must not leave a workload from the old one
    // selected — that is how someone binds prod while reading dev.
    fireEvent.change(cluster, { target: { value: "" } });
    expect((namespace as HTMLSelectElement).value).toBe("");
    expect((workload as HTMLSelectElement).value).toBe("");
    expect(workload).toBeDisabled();
  });

  it("keeps downstream selects disabled until their upstream choice is made", async () => {
    renderForm();
    await screen.findByRole("form", { name: /workload binding/i });

    expect(screen.getByLabelText("Namespace")).toBeDisabled();
    expect(screen.getByLabelText("Workload")).toBeDisabled();
    expect(screen.getByRole("button", { name: /create binding/i })).toBeDisabled();
  });

  it("says a concurrent edit was refused instead of silently overwriting it", async () => {
    renderForm({
      "/v1/service-health/bindings": {
        status: 409,
        body: {
          error: {
            code: "conflict",
            message: "the binding changed since it was read",
            correlation_id: "cid-1",
          },
        },
      },
    });
    await screen.findByRole("form", { name: /workload binding/i });

    fireEvent.change(screen.getByLabelText("Cluster"), { target: { value: "c1" } });
    await waitFor(() => expect(screen.getByLabelText("Namespace")).not.toBeDisabled());
    fireEvent.change(screen.getByLabelText("Namespace"), { target: { value: "hermes-dev" } });
    await waitFor(() => expect(screen.getByLabelText("Workload")).not.toBeDisabled());
    fireEvent.change(screen.getByLabelText("Workload"), {
      target: { value: "Deployment/hermes-api" },
    });
    fireEvent.click(screen.getByRole("button", { name: /create binding/i }));

    const conflict = await screen.findByTestId("version-conflict");
    expect(conflict).toHaveTextContent(/someone else edited it/i);
  });

  it("reports an unresolved workload as unresolved, not as a failure", async () => {
    renderForm({
      "/v1/service-health/bindings": {
        status: 201,
        body: { id: "b9", revision: 1, resolved: false },
      },
    });
    await screen.findByRole("form", { name: /workload binding/i });

    fireEvent.change(screen.getByLabelText("Cluster"), { target: { value: "c1" } });
    await waitFor(() => expect(screen.getByLabelText("Namespace")).not.toBeDisabled());
    fireEvent.change(screen.getByLabelText("Namespace"), { target: { value: "hermes-dev" } });
    await waitFor(() => expect(screen.getByLabelText("Workload")).not.toBeDisabled());
    fireEvent.change(screen.getByLabelText("Workload"), {
      target: { value: "Deployment/hermes-api" },
    });
    fireEvent.click(screen.getByRole("button", { name: /create binding/i }));

    const notice = await screen.findByTestId("save-notice");
    expect(notice).toHaveTextContent(/unresolved rather than unhealthy/i);
  });

  it("disables every control for a caller without integration.manage", async () => {
    renderForm({}, ["environment.view"]);
    await screen.findByRole("form", { name: /workload binding/i });

    expect(screen.getByTestId("state-permission-denied")).toBeInTheDocument();
    for (const select of screen.getAllByRole("combobox")) {
      expect(select).toBeDisabled();
    }
    expect(screen.getByRole("button", { name: /create binding/i })).toBeDisabled();
  });

  it("shows the datasource state without exposing how it is configured", async () => {
    renderForm({
      "/v1/service-health/binding-options": {
        status: 200,
        body: { ...OPTIONS, datasource: { ...OPTIONS.datasource, configured: false } },
      },
    });

    const state = await screen.findByTestId("datasource-state");
    expect(state).toHaveTextContent("not configured");
    expect(state.textContent).not.toMatch(/http|token|secret|ref/i);
  });
});
