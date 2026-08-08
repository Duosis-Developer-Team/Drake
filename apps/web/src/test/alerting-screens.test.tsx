import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import AlertDetailPage from "@/app/alerts/[alertId]/page";
import AlertsPage from "@/app/alerts/page";
import SloDetailPage from "@/app/slo/[sloId]/page";
import SloPage from "@/app/slo/page";
import { errorBody, installFetchMock } from "@/test/mock-api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/alerts",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({ alertId: "al-1", sloId: "slo-1" }),
}));

function alert(overrides: Record<string, unknown> = {}) {
  return {
    id: "al-1",
    fingerprint_prefix: "a1b2c3d4e5f6",
    alert_name: "HighErrorRate",
    status: "firing",
    severity: "critical",
    priority: "P1",
    mapping_state: "mapped",
    mapping_error_code: null,
    owner_team: "platform",
    slo_key: "availability.30d",
    runbook_key: "runbook.high-error-rate",
    starts_at: "2026-08-08T11:00:00Z",
    ends_at: null,
    last_seen_at: "2026-08-08T11:55:00Z",
    source_event_at: "2026-08-08T11:00:00Z",
    ingested_at: "2026-08-08T11:56:00Z",
    resolved_at: null,
    labels: { alertname: "HighErrorRate", severity: "critical", service: "api" },
    annotations: { summary: "Errors above objective" },
    occurrence: 1,
    silenced: false,
    inhibited: false,
    namespace: "pilot-dev",
    version: 2,
    project_key: "pilot",
    environment_key: "dev",
    service_key: "api",
    cluster_ref: "cl-1",
    incident: {
      id: "inc-1",
      state: "open",
      severity: "critical",
      priority: "P1",
      title: "api: HighErrorRate",
      acknowledged_at: null,
      assigned_at: null,
    },
    ...overrides,
  };
}

function burnRates(activeIndex: number | null = null) {
  return [
    {
      name: "page_fast",
      factor: 14.4,
      long_window_seconds: 3600,
      short_window_seconds: 300,
      severity: "critical",
      long_burn_rate: 20.5,
      short_burn_rate: activeIndex === 0 ? 18.2 : 0.4,
      active: activeIndex === 0,
    },
    {
      name: "page_slow",
      factor: 6,
      long_window_seconds: 21600,
      short_window_seconds: 1800,
      severity: "critical",
      long_burn_rate: 2.1,
      short_burn_rate: 1.4,
      active: false,
    },
  ];
}

function slo(overrides: Record<string, unknown> = {}) {
  return {
    id: "slo-1",
    slo_key: "availability.30d",
    display_name: "API availability",
    indicator: "availability",
    objective_ratio: 0.999,
    window_seconds: 2592000,
    threshold_profile_key: null,
    burn_profile_key: "standard.30d.v1",
    enabled: true,
    version: 1,
    project_key: "pilot",
    environment_key: "dev",
    service_key: "api",
    measurement: "Error ratio weighted by request rate.",
    evaluation: {
      status: "warning",
      data_quality: "ok",
      compliance_ratio: 0.9985,
      error_budget_total: 30,
      error_budget_consumed: 1.5,
      error_budget_remaining: -0.5,
      burn_rates: burnRates(0),
      evaluated_for: "2026-08-08T12:00:00Z",
      window_start: "2026-07-09T12:00:00Z",
      window_end: "2026-08-08T12:00:00Z",
      freshness_seconds: 60,
      error_code: null,
      sample_count: 720,
      objective_ratio: 0.995,
      definition_version: 1,
    },
    ...overrides,
  };
}

const SUMMARY = { firing: 3, p1: 1, p2: 1, silenced: 1, unmapped: 2, with_incident: 2 };

function renderAlerts(overrides: Record<string, { status: number; body: unknown }> = {}) {
  installFetchMock({
    "/v1/alerts/summary": { status: 200, body: SUMMARY },
    "/v1/alerts": {
      status: 200,
      body: { items: [alert()], total: 1, limit: 25, offset: 0 },
    },
    ...overrides,
  });
  return render(<AlertsPage />);
}

function renderAlertDetail(overrides: Record<string, { status: number; body: unknown }> = {}) {
  installFetchMock({
    "/v1/alerts/al-1": {
      status: 200,
      body: { ...alert(), can_silence: true, silences: [] },
    },
    "/v1/alerts/al-1/events": {
      status: 200,
      body: {
        events: [
          {
            event_type: "firing",
            status: "firing",
            occurrence: 1,
            source_event_at: "2026-08-08T11:00:00Z",
            received_at: "2026-08-08T11:56:00Z",
            detail: {},
          },
        ],
      },
    },
    ...overrides,
  });
  return render(<AlertDetailPage />);
}

function renderSlo(overrides: Record<string, { status: number; body: unknown }> = {}) {
  installFetchMock({
    "/v1/slo": { status: 200, body: { items: [slo()], total: 1, limit: 25, offset: 0 } },
    ...overrides,
  });
  return render(<SloPage />);
}

function renderSloDetail(overrides: Record<string, { status: number; body: unknown }> = {}) {
  installFetchMock({
    "/v1/slo/slo-1": {
      status: 200,
      body: {
        ...slo(),
        context: {
          deployments: [
            {
              id: "d1",
              observed_at: "2026-08-08T10:00:00Z",
              rollout_state: "complete",
              evidence_state: "verified",
              generation: 7,
            },
          ],
          incidents: [],
          alerts: [],
          correlation_note:
            "Temporal correlation only. Drake does not claim a deployment caused an SLO breach.",
        },
      },
    },
    "/v1/slo/slo-1/evaluations": {
      status: 200,
      body: { evaluations: [slo().evaluation] },
    },
    ...overrides,
  });
  return render(<SloDetailPage />);
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("alerts", () => {
  it("keeps firing, incident and silence state as separate columns", async () => {
    renderAlerts();
    const row = await screen.findByTestId("alert-row-HighErrorRate");
    // An alert can be firing, have an incident, and still be notifying —
    // three facts, three cells, never collapsed into one tick.
    expect(row).toHaveTextContent("Firing");
    expect(row).toHaveTextContent("open");
    expect(row).toHaveTextContent("notifying");
  });

  it("shows the received time alongside the alert time", async () => {
    renderAlerts();
    const row = await screen.findByTestId("alert-row-HighErrorRate");
    // A late delivery must be visible as a late delivery, not as a late
    // outage.
    expect(row).toHaveTextContent(/received/i);
  });

  it("summarises priorities without merging them into one count", async () => {
    renderAlerts();
    const summary = await screen.findByTestId("alert-summary");
    expect(summary).toHaveTextContent("P1");
    expect(summary).toHaveTextContent("P2");
    expect(summary).toHaveTextContent("Unmapped");
  });

  it("explains an unmapped alert rather than attaching it to a project", async () => {
    renderAlerts({
      "/v1/alerts": {
        status: 200,
        body: {
          items: [
            alert({
              mapping_state: "unmapped",
              mapping_error_code: "service_unknown",
              project_key: null,
              environment_key: null,
              service_key: null,
              incident: null,
            }),
          ],
          total: 1,
          limit: 25,
          offset: 0,
        },
      },
    });
    const note = await screen.findByTestId("unmapped-note");
    expect(note).toHaveTextContent(/does not match any service/i);
    expect(screen.getByTestId("alert-row-HighErrorRate")).toHaveTextContent(
      "no catalog match",
    );
    expect(screen.getByText("no incident")).toBeInTheDocument();
  });

  it("offers only allowlisted filter values", async () => {
    renderAlerts();
    await screen.findByTestId("alert-row-HighErrorRate");
    const status = screen.getByLabelText("Status") as HTMLSelectElement;
    const priority = screen.getByLabelText("Priority") as HTMLSelectElement;
    // Fixed vocabularies. There is no text input anywhere on this screen
    // through which a matcher, a regex or a PromQL fragment could be typed.
    expect([...status.options].map((option) => option.value)).toEqual([
      "",
      "firing",
      "resolved",
    ]);
    expect([...priority.options].map((option) => option.value)).toEqual([
      "",
      "P1",
      "P2",
      "P3",
      "P4",
    ]);
    expect(document.querySelectorAll('input[type="text"]')).toHaveLength(0);
  });

  it("separates empty, permission denied and error", async () => {
    const { unmount } = renderAlerts({
      "/v1/alerts": { status: 200, body: { items: [], total: 0, limit: 25, offset: 0 } },
    });
    expect(await screen.findByTestId("state-empty")).toBeInTheDocument();
    unmount();

    const denied = renderAlerts({
      "/v1/alerts": { status: 404, body: errorBody("not_found", "not found") },
    });
    expect(await screen.findByTestId("state-permission-denied")).toBeInTheDocument();
    denied.unmount();

    renderAlerts({
      "/v1/alerts": { status: 503, body: errorBody("unavailable", "provider down") },
    });
    expect(await screen.findByTestId("state-error")).toBeInTheDocument();
  });
});

describe("alert detail", () => {
  it("says a silence suppresses notification and nothing else", async () => {
    renderAlertDetail();
    expect(await screen.findByText("Not silenced")).toBeInTheDocument();
    expect(
      screen.getByText(/does not acknowledge the incident, does not resolve it/i),
    ).toBeInTheDocument();
  });

  it("shows a pending silence as pending rather than active", async () => {
    renderAlertDetail({
      "/v1/alerts/al-1": {
        status: 200,
        body: {
          ...alert(),
          can_silence: true,
          silences: [
            {
              id: "s1",
              state: "pending",
              reason_code: "planned_maintenance",
              reason_note: null,
              requested_seconds: 900,
              requested_at: "2026-08-08T11:50:00Z",
              starts_at: null,
              ends_at: null,
              error_code: null,
              version: 1,
            },
          ],
        },
      },
    });
    const silences = await screen.findByTestId("alert-silences");
    // Alertmanager has not confirmed it, so it is suppressing nothing yet.
    expect(silences).toHaveTextContent("Pending at Alertmanager");
    expect(silences).not.toHaveTextContent(/^Active$/);
  });

  it("shows a failed silence with its bounded code and not as active", async () => {
    renderAlertDetail({
      "/v1/alerts/al-1": {
        status: 200,
        body: {
          ...alert(),
          can_silence: true,
          silences: [
            {
              id: "s1",
              state: "failed",
              reason_code: "known_issue",
              reason_note: null,
              requested_seconds: 900,
              requested_at: "2026-08-08T11:50:00Z",
              starts_at: null,
              ends_at: null,
              error_code: "http_500",
              version: 2,
            },
          ],
        },
      },
    });
    const silences = await screen.findByTestId("alert-silences");
    expect(silences).toHaveTextContent("Failed");
    expect(silences).toHaveTextContent("http_500");
  });

  it("renders no raw payload, URL, header or fingerprint", async () => {
    const { container } = renderAlertDetail();
    await screen.findByTestId("alert-labels");
    const rendered = container.textContent ?? "";
    expect(rendered).not.toMatch(/https?:\/\//);
    expect(rendered.toLowerCase()).not.toContain("authorization");
    expect(rendered.toLowerCase()).not.toContain("bearer");
    expect(rendered).not.toContain("generatorURL");
    // Links are rendered as text, never as anchors to somewhere outside.
    expect(container.querySelectorAll('a[href^="http"]')).toHaveLength(0);
  });

  it("explains why an unmapped alert opened no incident", async () => {
    renderAlertDetail({
      "/v1/alerts/al-1": {
        status: 200,
        body: {
          ...alert({
            mapping_state: "ambiguous",
            mapping_error_code: "environment_ambiguous",
            incident: null,
          }),
          can_silence: false,
          silences: [],
        },
      },
    });
    const explanation = await screen.findByTestId("mapping-explanation");
    expect(explanation).toHaveTextContent(/more than one environment/i);
    expect(explanation).toHaveTextContent(/opened no incident/i);
  });
});

describe("service objectives", () => {
  it("shows objective, compliance and remaining budget as separate numbers", async () => {
    renderSlo();
    const row = await screen.findByTestId("slo-row-availability.30d");
    expect(row).toHaveTextContent("99.900%");
    expect(row).toHaveTextContent("99.850%");
    // Negative, and shown as negative.
    expect(row).toHaveTextContent("-50.0%");
  });

  it("never shows an unmeasured objective as healthy or as zero", async () => {
    renderSlo({
      "/v1/slo": {
        status: 200,
        body: {
          items: [
            slo({ id: "slo-a", slo_key: "a.30d", evaluation: null }),
            slo({
              id: "slo-b",
              slo_key: "b.30d",
              display_name: "Quiet service",
              evaluation: {
                ...slo().evaluation,
                status: "insufficient_data",
                compliance_ratio: null,
                error_budget_remaining: null,
                error_budget_consumed: null,
                sample_count: 0,
                burn_rates: [],
              },
            }),
          ],
          total: 2,
          limit: 25,
          offset: 0,
        },
      },
    });
    const unevaluated = await screen.findByTestId("slo-row-a.30d");
    expect(unevaluated).toHaveTextContent("not evaluated");
    expect(unevaluated).not.toHaveTextContent("100.000%");
    expect(unevaluated).not.toHaveTextContent("0.0%");

    const quiet = screen.getByTestId("slo-row-b.30d");
    expect(quiet).toHaveTextContent("Insufficient data");
    const caveats = screen.getByTestId("slo-caveats");
    expect(caveats).toHaveTextContent(/not the same as a perfect score/i);
    expect(caveats).toHaveTextContent(/never been measured/i);
  });

  it("keeps a failed query distinct from a stale one", async () => {
    renderSlo({
      "/v1/slo": {
        status: 200,
        body: {
          items: [
            slo({
              id: "slo-f",
              slo_key: "failed.30d",
              display_name: "Unreadable",
              evaluation: {
                ...slo().evaluation,
                status: "query_failed",
                compliance_ratio: null,
                error_budget_remaining: null,
              },
            }),
            slo({
              id: "slo-s",
              slo_key: "stale.30d",
              display_name: "Outdated",
              evaluation: {
                ...slo().evaluation,
                status: "stale",
                compliance_ratio: null,
                error_budget_remaining: null,
              },
            }),
          ],
          total: 2,
          limit: 25,
          offset: 0,
        },
      },
    });
    expect(await screen.findByTestId("slo-row-failed.30d")).toHaveTextContent(
      "Query failed",
    );
    expect(screen.getByTestId("slo-row-stale.30d")).toHaveTextContent("Stale");
    const caveats = screen.getByTestId("slo-caveats");
    expect(caveats).toHaveTextContent(/says nothing about the service/i);
    expect(caveats).toHaveTextContent(/older than this SLO's freshness limit/i);
  });
});

describe("SLO detail", () => {
  it("shows both windows for every burn level, active or not", async () => {
    renderSloDetail();
    const table = await screen.findByTestId("burn-table");
    // The long and the short window are both visible, because a level is
    // active only when both exceed the threshold.
    expect(table).toHaveTextContent("20.50×");
    expect(table).toHaveTextContent("18.20×");
    expect(table).toHaveTextContent("Active");
    expect(table).toHaveTextContent("Not active");
    expect(
      screen.getByText(/long and its short window both exceed the threshold/i),
    ).toBeInTheDocument();
  });

  it("states the measurement method and the objective it was judged against", async () => {
    renderSloDetail();
    expect(await screen.findByTestId("measurement")).toHaveTextContent(
      /weighted by request rate/i,
    );
    const evaluation = screen.getByTestId("slo-evaluation");
    // The row's own objective (99.5%) differs from today's (99.9%), and the
    // screen shows the one it was actually judged against.
    expect(evaluation).toHaveTextContent("99.500%");
  });

  it("presents nearby deployments as correlation, not cause", async () => {
    renderSloDetail();
    const context = await screen.findByTestId("slo-context");
    expect(context).toHaveTextContent("gen 7");
    expect(screen.getByTestId("correlation-note")).toHaveTextContent(
      /does not claim a deployment caused/i,
    );
  });

  it("says an objective was never evaluated rather than implying it passed", async () => {
    renderSloDetail({
      "/v1/slo/slo-1": { status: 200, body: { ...slo({ evaluation: null }), context: null } },
      "/v1/slo/slo-1/evaluations": { status: 200, body: { evaluations: [] } },
    });
    expect(await screen.findByText("Never evaluated")).toBeInTheDocument();
    expect(
      screen.getByText(/not the same as meeting it/i),
    ).toBeInTheDocument();
  });
});
