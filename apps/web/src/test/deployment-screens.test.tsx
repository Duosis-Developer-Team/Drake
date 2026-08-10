import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import DeploymentDetailPage from "@/app/deployments/[deploymentId]/page";
import DeploymentsPage from "@/app/deployments/page";
import { errorBody, installFetchMock } from "@/test/mock-api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/deployments",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({ deploymentId: "dep-1" }),
}));

const DIGEST = `sha256:${"a".repeat(64)}`;

function deployment(overrides: Record<string, unknown> = {}) {
  return {
    id: "dep-1",
    namespace: "pilot-dev",
    workload_kind: "Deployment",
    workload_name: "pilot-api",
    revision: 7,
    observed_generation: 7,
    images: [
      {
        name: "api",
        image: `ghcr.io/acme/api@${DIGEST}`,
        digest: DIGEST,
        short_digest: "a".repeat(12),
      },
    ],
    primary_image: `ghcr.io/acme/api@${DIGEST}`,
    primary_digest: DIGEST,
    short_digest: "a".repeat(12),
    commit_sha: "0123456789abcdef",
    short_commit: "0123456",
    workflow: {
      provider: "github",
      repository: "acme/api",
      run_id: "4242",
      run_url: "https://github.com/acme/api/actions/runs/4242",
    },
    evidence_state: "verified",
    evidence_detail: {
      commit: true,
      workflow: true,
      declared_digest: true,
      running_digest: true,
      digest_match: true,
    },
    rollout_state: "healthy",
    rollout_reason: null,
    replicas: { desired: 3, ready: 3, updated: 3, available: 3 },
    rollout_started_at: "2026-08-08T12:00:00Z",
    rollout_completed_at: "2026-08-08T12:03:00Z",
    last_seen_at: "2026-08-08T12:30:00Z",
    cluster: { cluster_ref: "cluster-a", id: "c1" },
    project_key: "pilot",
    environment_key: "dev",
    service_key: "api",
    environment_service_id: "es1",
    binding_id: "b1",
    previous_revision_id: null,
    health_comparison: { verdict: "stable", incident_count: 0 },
    ...overrides,
  };
}

afterEach(() => vi.unstubAllGlobals());

describe("deployment list", () => {
  it("shows the revision, rollout, evidence and short refs", async () => {
    installFetchMock({
      "/v1/deployments": {
        status: 200,
        body: { items: [deployment()], next_cursor: null, total: 1, limit: 25 },
      },
    });
    render(<DeploymentsPage />);

    const row = await screen.findByTestId("deployment-row-pilot-api");
    expect(within(row).getByText("#7")).toBeInTheDocument();
    expect(within(row).getByText("Healthy")).toBeInTheDocument();
    expect(within(row).getByText("Verified")).toBeInTheDocument();
    // Digests are shortened; a 64-character hex string is noise in a table.
    expect(within(row).getByText("a".repeat(12))).toBeInTheDocument();
    expect(within(row).queryByText(DIGEST)).not.toBeInTheDocument();
    expect(within(row).getByText("3 / 3")).toBeInTheDocument();
  });

  it("marks an unverified deployment without calling it a failure", async () => {
    installFetchMock({
      "/v1/deployments": {
        status: 200,
        body: {
          items: [
            deployment({
              evidence_state: "unverified",
              short_digest: null,
              short_commit: null,
              primary_digest: null,
            }),
          ],
          next_cursor: null,
          total: 1,
          limit: 25,
        },
      },
    });
    render(<DeploymentsPage />);

    const row = await screen.findByTestId("deployment-row-pilot-api");
    expect(within(row).getByText("Unverified")).toBeInTheDocument();
    // An absence of evidence is not an error state.
    expect(within(row).queryByTestId("status-critical")).not.toBeInTheDocument();
    expect(within(row).getByText("no image digest")).toBeInTheDocument();
  });

  it("offers only the allowlisted filter values", async () => {
    installFetchMock({
      "/v1/deployments": {
        status: 200,
        body: { items: [deployment()], next_cursor: null, total: 1, limit: 25 },
      },
    });
    render(<DeploymentsPage />);
    await screen.findByTestId("deployment-table");

    const filters = screen.getByRole("group", { name: /filters/i });
    expect(within(filters).queryByRole("textbox")).not.toBeInTheDocument();
    const evidence = within(filters).getByLabelText(/evidence/i) as HTMLSelectElement;
    expect(Array.from(evidence.options).map((option) => option.value)).toEqual([
      "",
      "verified",
      "partial",
      "unverified",
      "conflict",
    ]);
    fireEvent.change(evidence, { target: { value: "conflict" } });
    expect(evidence.value).toBe("conflict");
  });

  it("separates empty, permission denied and error", async () => {
    installFetchMock({
      "/v1/deployments": {
        status: 200,
        body: { items: [], next_cursor: null, total: 0, limit: 25 },
      },
    });
    const { unmount } = render(<DeploymentsPage />);
    expect(await screen.findByText("No deployments")).toBeInTheDocument();
    unmount();

    installFetchMock({
      "/v1/deployments": { status: 404, body: errorBody("not_found", "not found") },
    });
    const denied = render(<DeploymentsPage />);
    expect(await screen.findByTestId("state-permission-denied")).toBeInTheDocument();
    denied.unmount();

    installFetchMock({
      "/v1/deployments": { status: 503, body: errorBody("unavailable", "down") },
    });
    render(<DeploymentsPage />);
    expect(await screen.findByTestId("state-error")).toBeInTheDocument();
  });
});

function renderDetail(overrides: Record<string, unknown> = {}) {
  installFetchMock({
    "/v1/deployments/dep-1": { status: 200, body: deployment(overrides) },
    "/v1/deployments/dep-1/revisions": {
      status: 200,
      body: {
        revisions: [
          {
            id: "dep-1",
            revision: 7,
            rollout_state: "healthy",
            evidence_state: "verified",
            short_digest: "a".repeat(12),
            short_commit: "0123456",
            rollout_started_at: "2026-08-08T12:00:00Z",
            rollout_completed_at: "2026-08-08T12:03:00Z",
          },
        ],
      },
    },
    "/v1/deployments/dep-1/incidents": {
      status: 200,
      body: { incidents: [], correlation_only: true },
    },
  });
  render(<DeploymentDetailPage />);
}

describe("deployment detail", () => {
  it("shows which links of the evidence chain were observed", async () => {
    renderDetail();
    const chain = await screen.findByTestId("evidence-chain");
    expect(chain).toHaveTextContent("Commit SHA");
    expect(chain).toHaveTextContent("Digest the node pulled");
    expect(within(chain).getAllByText("observed")).toHaveLength(4);
  });

  it("names the unobserved links rather than hiding them", async () => {
    renderDetail({
      evidence_state: "partial",
      evidence_detail: {
        commit: false,
        workflow: false,
        declared_digest: true,
        running_digest: true,
        digest_match: true,
      },
      workflow: { provider: null, repository: null, run_id: null, run_url: null },
    });
    const chain = await screen.findByTestId("evidence-chain");
    expect(within(chain).getAllByText("not observed")).toHaveLength(2);
    expect(screen.getByText(/does not close end to end/i)).toBeInTheDocument();
  });

  it("labels the health comparison as correlation, not causation", async () => {
    renderDetail({
      health_comparison: {
        verdict: "regressed",
        incident_count: 1,
        signals: {
          error_ratio: {
            before: 0.01,
            after: 0.4,
            delta: 0.39,
            direction: "regressed",
            lower_is_better: true,
          },
          latency_p95: {
            before: null,
            after: null,
            delta: null,
            direction: "unknown",
            lower_is_better: true,
          },
        },
        missing_signals: ["latency_p95"],
        before: { from: "2026-08-08T11:30:00Z", to: "2026-08-08T12:00:00Z" },
        after: { from: "2026-08-08T12:02:00Z", to: "2026-08-08T12:32:00Z" },
        computed_at: "2026-08-08T13:00:00Z",
      },
    });

    const table = await screen.findByTestId("health-comparison");
    expect(within(table).getByText("Error ratio")).toBeInTheDocument();
    // An unmeasured signal says so rather than rendering as zero.
    expect(within(table).getByText("not measured")).toBeInTheDocument();
    expect(within(table).getAllByText("—").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/not a causal claim/i)).toBeInTheDocument();
  });

  it("explains an uncompared deployment instead of showing a verdict", async () => {
    renderDetail({ health_comparison: null });
    expect(await screen.findByText("Not compared yet")).toBeInTheDocument();
  });

  it("renders no raw Kubernetes, query or credential", async () => {
    renderDetail();
    await screen.findByTestId("evidence-chain");
    const text = (document.body.textContent ?? "").toLowerCase();
    for (const forbidden of ["spec_summary", "annotations", "sum(rate(", "promql", "secret"]) {
      expect(text).not.toContain(forbidden);
    }
  });
});
