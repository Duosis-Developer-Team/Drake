import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ProtectionDetailPage from "@/app/protection/[policyId]/page";
import ProtectionPage from "@/app/protection/page";
import { errorBody, installFetchMock } from "@/test/mock-api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/protection",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({ policyId: "pol-1" }),
}));

function evaluation(overrides: Record<string, unknown> = {}) {
  return {
    backup_state: "protected",
    recoverability_state: "unverified",
    overall_state: "protected_unverified",
    reasons: ["restore_never_verified"],
    last_success_at: "2026-08-08T06:00:00Z",
    last_attempt_at: "2026-08-08T06:00:00Z",
    last_restore_at: null,
    reporter_seen_at: "2026-08-08T11:50:00Z",
    consecutive_failures: 0,
    computed_at: "2026-08-08T12:00:00Z",
    ...overrides,
  };
}

function policy(overrides: Record<string, unknown> = {}) {
  return {
    id: "pol-1",
    display_name: "Hermes core database (dev)",
    store_key: "hermes-core",
    store_kind: "postgresql",
    provider_key: "postgresql-dump",
    connector_key: "hermes-backup",
    rpo_seconds: 604800,
    rto_seconds: 14400,
    restore_verification_ttl_seconds: 7776000,
    requires_offsite: true,
    requires_integrity_check: true,
    enabled: true,
    schedule_description: "Weekly",
    project_key: "hermes",
    environment_key: "dev",
    project_id: "p1",
    environment_id: "e1",
    evaluation: evaluation(),
    ...overrides,
  };
}

const SUMMARY = {
  total_policies: 3,
  backup: { protected: 2, at_risk: 0, overdue: 1, failed: 0, unknown: 0 },
  recoverability: { verified: 1, unverified: 2, failed: 0, unknown: 0 },
  overall: {
    recoverable_verified: 1,
    protected_unverified: 1,
    at_risk: 0,
    overdue: 1,
    failed: 0,
    unknown: 0,
  },
};

afterEach(() => vi.unstubAllGlobals());

describe("protection center", () => {
  it("shows backup and recoverability as separate columns", async () => {
    installFetchMock({
      "/v1/protection/summary": { status: 200, body: SUMMARY },
      "/v1/protection/policies": {
        status: 200,
        body: { items: [policy()], total: 1, limit: 25, offset: 0 },
      },
    });
    render(<ProtectionPage />);

    const row = await screen.findByTestId("protection-row-hermes-core");
    // A green backup and an unproven restore, side by side. Collapsing
    // them is exactly what this screen refuses to do.
    expect(within(row).getByText("Protected")).toBeInTheDocument();
    expect(within(row).getByText("Never verified")).toBeInTheDocument();
    expect(within(row).getByText("Never restore-tested")).toBeInTheDocument();
    expect(within(row).getByText("7d")).toBeInTheDocument();
  });

  it("summarises both axes without merging them", async () => {
    installFetchMock({
      "/v1/protection/summary": { status: 200, body: SUMMARY },
      "/v1/protection/policies": {
        status: 200,
        body: { items: [policy()], total: 1, limit: 25, offset: 0 },
      },
    });
    render(<ProtectionPage />);

    const summary = await screen.findByTestId("protection-summary");
    expect(within(summary).getByTestId("count-protected")).toHaveTextContent("2");
    expect(within(summary).getByTestId("count-overdue")).toHaveTextContent("1");
    expect(within(summary).getByTestId("count-restore-verified")).toHaveTextContent("1");
    expect(within(summary).getByTestId("count-never-verified")).toHaveTextContent("2");
  });

  it("shows a policy with no evaluation as not evaluated, not as healthy", async () => {
    installFetchMock({
      "/v1/protection/summary": { status: 200, body: SUMMARY },
      "/v1/protection/policies": {
        status: 200,
        body: { items: [policy({ evaluation: null })], total: 1, limit: 25, offset: 0 },
      },
    });
    render(<ProtectionPage />);

    const row = await screen.findByTestId("protection-row-hermes-core");
    expect(within(row).getAllByText("not evaluated")).toHaveLength(2);
    expect(within(row).queryByText("Protected")).not.toBeInTheDocument();
  });

  it("offers only the allowlisted filter values", async () => {
    installFetchMock({
      "/v1/protection/summary": { status: 200, body: SUMMARY },
      "/v1/protection/policies": {
        status: 200,
        body: { items: [policy()], total: 1, limit: 25, offset: 0 },
      },
    });
    render(<ProtectionPage />);
    await screen.findByTestId("protection-table");

    const filters = screen.getByRole("group", { name: /filters/i });
    expect(within(filters).queryByRole("textbox")).not.toBeInTheDocument();
    const recoverability = within(filters).getByLabelText(
      /recoverability/i,
    ) as HTMLSelectElement;
    expect(Array.from(recoverability.options).map((option) => option.value)).toEqual([
      "",
      "verified",
      "unverified",
      "failed",
      "unknown",
    ]);
    fireEvent.change(recoverability, { target: { value: "failed" } });
    expect(recoverability.value).toBe("failed");
  });

  it("separates empty, permission denied and error", async () => {
    installFetchMock({
      "/v1/protection/summary": { status: 200, body: SUMMARY },
      "/v1/protection/policies": {
        status: 200,
        body: { items: [], total: 0, limit: 25, offset: 0 },
      },
    });
    const { unmount } = render(<ProtectionPage />);
    expect(await screen.findByText("No protection policies")).toBeInTheDocument();
    unmount();

    installFetchMock({
      "/v1/protection/summary": { status: 200, body: SUMMARY },
      "/v1/protection/policies": { status: 404, body: errorBody("not_found", "not found") },
    });
    const denied = render(<ProtectionPage />);
    expect(await screen.findByTestId("state-permission-denied")).toBeInTheDocument();
    denied.unmount();

    installFetchMock({
      "/v1/protection/summary": { status: 200, body: SUMMARY },
      "/v1/protection/policies": { status: 503, body: errorBody("unavailable", "down") },
    });
    render(<ProtectionPage />);
    expect(await screen.findByTestId("state-error")).toBeInTheDocument();
  });
});

function renderDetail(overrides: Record<string, unknown> = {}, extra = {}) {
  installFetchMock({
    "/v1/protection/policies/pol-1": { status: 200, body: policy(overrides) },
    "/v1/protection/policies/pol-1/runs": {
      status: 200,
      body: {
        runs: [
          {
            id: "run-1",
            provider_run_id: "hermes-core-2026-08-08",
            status: "succeeded",
            started_at: "2026-08-08T06:00:00Z",
            completed_at: "2026-08-08T06:04:00Z",
            duration_seconds: 240,
            error_code: null,
            attempt: 1,
            source_event_at: "2026-08-08T06:04:00Z",
            ingested_at: "2026-08-08T06:05:00Z",
            artifact_count: 1,
          },
        ],
      },
    },
    "/v1/protection/policies/pol-1/drills": { status: 200, body: { drills: [] } },
    "/v1/protection/policies/pol-1/incidents": { status: 200, body: { incidents: [] } },
    ...extra,
  });
  render(<ProtectionDetailPage />);
}

describe("protection detail", () => {
  it("shows the policy promises and the reason it is unverified", async () => {
    renderDetail();
    expect(await screen.findByText("Hermes core database (dev)")).toBeInTheDocument();
    expect(screen.getByTestId("protection-reasons")).toHaveTextContent(
      "Never restore-tested",
    );
    const timeline = await screen.findByTestId("run-timeline");
    expect(within(timeline).getByText("succeeded")).toBeInTheDocument();
    expect(within(timeline).getByText("1 artifact")).toBeInTheDocument();
  });

  it("says never restore-tested rather than showing an empty list", async () => {
    renderDetail();
    // The phrase appears twice on purpose: once as the reason for the
    // verdict, once as the drill section's own empty state.
    expect((await screen.findAllByText("Never restore-tested")).length).toBe(2);
    expect(
      screen.getByText(/nobody has restored is not proven recoverable/i),
    ).toBeInTheDocument();
  });

  it("shows a failed drill alongside a healthy backup", async () => {
    renderDetail(
      {
        evaluation: evaluation({
          recoverability_state: "failed",
          overall_state: "failed",
          reasons: ["restore_failed"],
          last_restore_at: "2026-08-08T09:00:00Z",
        }),
      },
      {
        "/v1/protection/policies/pol-1/drills": {
          status: 200,
          body: {
            drills: [
              {
                id: "drill-1",
                drill_external_id: "weekly-smoke",
                target_profile: "ephemeral",
                result: "failed",
                started_at: "2026-08-08T09:00:00Z",
                completed_at: "2026-08-08T09:20:00Z",
                duration_seconds: 1200,
                rto_met: null,
                validations: { schema_present: true, application_smoke: false },
                error_code: "restore_smoke_failed",
              },
            ],
          },
        },
      },
    );

    const drills = await screen.findByTestId("drill-timeline");
    expect(within(drills).getByText("failed")).toBeInTheDocument();
    expect(within(drills).getByText(/Application smoke test: fail/)).toBeInTheDocument();
    // Backup is still protected — the two axes disagree, and both are shown.
    expect(screen.getByText("Protected")).toBeInTheDocument();
    expect(screen.getByText("Restore failed")).toBeInTheDocument();
  });

  it("explains an unevaluated policy instead of implying a state", async () => {
    renderDetail({ evaluation: null });
    expect(await screen.findByText("Not evaluated yet")).toBeInTheDocument();
  });

  it("renders no storage location, credential or provider payload", async () => {
    renderDetail();
    await screen.findByTestId("run-timeline");
    const text = (document.body.textContent ?? "").toLowerCase();
    for (const forbidden of ["http://", "https://", "token", "password", ".sql", ".dump"]) {
      expect(text).not.toContain(forbidden);
    }
  });
});
