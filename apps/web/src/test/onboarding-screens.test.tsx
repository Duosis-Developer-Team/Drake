import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import OnboardingSessionPage from "@/app/onboarding/[sessionId]/page";
import OnboardingPage from "@/app/onboarding/page";
import { errorBody, installFetchMock } from "@/test/mock-api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/onboarding",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({ sessionId: "sess-1" }),
}));

const COMMIT = "4f1c9a2b7e5d3086c1a4b9e7f2d0538ac6b1e492";

function status(overrides: Record<string, unknown> = {}) {
  return {
    configuration_state: "configured",
    missing_operator_inputs: [],
    gitops_pr_enabled: false,
    can_manage: true,
    can_apply: true,
    can_gitops: false,
    sessions: 1,
    needs_review: 0,
    ready: 1,
    imported: 0,
    stale: 0,
    provider_unavailable: 0,
    analyses: 1,
    analyses_truncated: 0,
    analyses_failed: 0,
    last_analyzed_at: "2026-08-09T10:00:00Z",
    gitops_pending: 0,
    gitops_active: 0,
    gitops_failed: 0,
    ...overrides,
  };
}

function session(overrides: Record<string, unknown> = {}) {
  return {
    id: "sess-1",
    state: "ready",
    reason_code: null,
    reason: "",
    analyzed_commit_sha: COMMIT,
    analyzed_at: "2026-08-09T10:00:00Z",
    approved_at: null,
    approved_plan_version: null,
    imported_project_id: null,
    imported_project_key: null,
    imported_at: null,
    version: 3,
    created_at: "2026-08-09T09:00:00Z",
    repository: {
      id: "repo-1",
      owner: "Duosis-Developer-Team",
      name: "Datalake-Platform-GUI",
      full_name: "Duosis-Developer-Team/Datalake-Platform-GUI",
      default_branch: "main",
      security_gate: null,
    },
    plan: {
      plan_version: 1,
      state: "ready",
      blocking_items: 0,
      total_items: 9,
      plan_digest: "ab12cd34ef56",
      commit_sha: COMMIT,
    },
    can_manage: true,
    can_apply: true,
    can_gitops: false,
    gitops_requests: [],
    ...overrides,
  };
}

function planItem(overrides: Record<string, unknown> = {}) {
  return {
    entity_kind: "service",
    action: "create",
    item_key: "service:datalake-api",
    proposed_name: "datalake-api",
    existing_entity_id: null,
    existing_name: null,
    reason_code: null,
    reason: "",
    detail: {},
    blocking: false,
    ...overrides,
  };
}

function plan(overrides: Record<string, unknown> = {}) {
  return {
    plan: {
      id: "plan-1",
      plan_version: 1,
      state: "ready",
      commit_sha: COMMIT,
      manifest_digest: "aa11bb22",
      analyzer_version: 1,
      plan_digest: "ab12cd34ef56",
      blocking_items: 0,
      total_items: 2,
      created_at: "2026-08-09T10:00:00Z",
      applicable: true,
    },
    items: [
      planItem(),
      planItem({
        entity_kind: "project",
        action: "create",
        item_key: "project:datalake",
        proposed_name: "datalake",
      }),
    ],
    ...overrides,
  };
}

function analysis(overrides: Record<string, unknown> = {}) {
  return {
    analysis: {
      id: "an-1",
      commit_sha: COMMIT,
      analyzer_version: 1,
      status: "complete",
      truncated: false,
      manifest_found: true,
      files_read: 6,
      bytes_read: 2048,
      provider_calls: 12,
      error_code: null,
      analyzed_at: "2026-08-09T10:00:00Z",
    },
    findings: [
      {
        finding_type: "file.read",
        safe_path: "pyproject.toml",
        confidence: "high",
        evidence_kind: "content_digest",
        proposed_target: null,
        review_reason: null,
      },
    ],
    ...overrides,
  };
}

function renderList(overrides: Record<string, { status: number; body: unknown }> = {}) {
  installFetchMock({
    "/v1/onboarding/github/status": { status: 200, body: status() },
    "/v1/onboarding/sessions": {
      status: 200,
      body: { items: [session()], total: 1, limit: 25, offset: 0 },
    },
    ...overrides,
  });
  return render(<OnboardingPage />);
}

function renderDetail(overrides: Record<string, { status: number; body: unknown }> = {}) {
  installFetchMock({
    "/v1/onboarding/sessions/sess-1": { status: 200, body: session() },
    "/v1/onboarding/sessions/sess-1/plan": { status: 200, body: plan() },
    "/v1/onboarding/sessions/sess-1/findings": { status: 200, body: analysis() },
    ...overrides,
  });
  return render(<OnboardingSessionPage />);
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("onboarding", () => {
  it("reports a missing GitHub App as not configured, not as empty", async () => {
    renderList({
      "/v1/onboarding/github/status": {
        status: 200,
        body: status({
          configuration_state: "not_configured",
          missing_operator_inputs: ["feature_disabled", "private_key_reference"],
        }),
      },
      "/v1/onboarding/sessions": {
        status: 200,
        body: { items: [], total: 0, limit: 25, offset: 0 },
      },
    });
    const panel = await screen.findByTestId("github-not-configured");
    // "Drake cannot look" and "Drake looked and found nothing" are different
    // answers, and only one of them means someone should go configure
    // something.
    expect(panel).toHaveTextContent(/nothing has been contacted/i);
    expect(panel).toHaveTextContent(/no token has been issued/i);
    expect(panel).toHaveTextContent(/switched off/i);
    expect(panel).toHaveTextContent(/private key reference/i);
    // And no reference name or value is shown, only which one is absent.
    expect(panel.textContent).not.toMatch(/\/etc\/|\.pem|\.key/);
  });

  it("names the wizard steps and says nothing is executed", async () => {
    renderList();
    const steps = await screen.findByTestId("wizard-steps");
    for (const step of ["Repository", "Safe discovery", "Review", "Approval"]) {
      expect(steps).toHaveTextContent(step);
    }
    expect(screen.getByText(/no build, no install, no script, no hook/i)).toBeInTheDocument();
  });

  it("says GitOps is off rather than leaving it ambiguous", async () => {
    renderList();
    expect(await screen.findByTestId("gitops-disabled")).toHaveTextContent(
      /will not write to any repository/i,
    );
  });

  it("separates empty, permission denied and error", async () => {
    const empty = renderList({
      "/v1/onboarding/sessions": {
        status: 200,
        body: { items: [], total: 0, limit: 25, offset: 0 },
      },
    });
    expect(await screen.findByTestId("state-empty")).toBeInTheDocument();
    empty.unmount();

    const denied = renderList({
      "/v1/onboarding/sessions": {
        status: 404,
        body: errorBody("not_found", "not found"),
      },
    });
    expect(await screen.findByTestId("state-permission-denied")).toBeInTheDocument();
    denied.unmount();

    renderList({
      "/v1/onboarding/sessions": {
        status: 503,
        body: errorBody("unavailable", "github unavailable"),
      },
    });
    expect(await screen.findByTestId("state-error")).toBeInTheDocument();
  });
});

describe("onboarding session", () => {
  it("groups the plan by what it would actually do", async () => {
    renderDetail();
    const created = await screen.findByTestId("plan-group-create");
    expect(created).toHaveTextContent("datalake-api");
    expect(created).toHaveTextContent("Create");
    expect(screen.getByTestId("apply-available")).toHaveTextContent(
      /changes nothing in the repository/i,
    );
  });

  it("blocks apply when an item needs a decision and says why", async () => {
    renderDetail({
      "/v1/onboarding/sessions/sess-1/plan": {
        status: 200,
        body: plan({
          plan: { ...plan().plan, blocking_items: 1, applicable: false, state: "needs_review" },
          items: [
            planItem(),
            planItem({
              entity_kind: "cluster_binding",
              action: "unmapped",
              item_key: "cluster_binding:dev",
              proposed_name: "ghost-cluster",
              reason_code: "cluster_unknown",
              reason: "The manifest references a cluster Drake does not have.",
              blocking: true,
            }),
          ],
        }),
      },
    });
    const decisions = await screen.findByTestId("plan-group-conflict");
    expect(decisions).toHaveTextContent("ghost-cluster");
    expect(decisions).toHaveTextContent(/cluster Drake does not have/i);
    expect(screen.getByTestId("apply-blocked")).toHaveTextContent(/need a decision/i);
    expect(screen.queryByTestId("apply-available")).not.toBeInTheDocument();
  });

  it("tells a reviewer without apply rights that they may look, not apply", async () => {
    renderDetail({
      "/v1/onboarding/sessions/sess-1": {
        status: 200,
        body: session({ can_apply: false }),
      },
    });
    expect(await screen.findByTestId("apply-blocked")).toHaveTextContent(
      /review this plan but not apply it/i,
    );
  });

  it("says a stale plan describes a commit that moved", async () => {
    renderDetail({
      "/v1/onboarding/sessions/sess-1": {
        status: 200,
        body: session({ state: "stale", reason: "The repository moved." }),
      },
    });
    const notice = await screen.findByTestId("stale-notice");
    expect(notice).toHaveTextContent(/no longer the branch head/i);
    expect(notice).toHaveTextContent(/cannot be applied/i);
  });

  it("shows a partial analysis as partial rather than complete", async () => {
    renderDetail({
      "/v1/onboarding/sessions/sess-1/findings": {
        status: 200,
        body: analysis({
          analysis: { ...analysis().analysis, truncated: true, status: "partial" },
        }),
      },
    });
    const truncated = await screen.findByTestId("analysis-truncated");
    expect(truncated).toHaveTextContent(/part of the repository/i);
    expect(truncated).toHaveTextContent(/not a complete picture/i);
  });

  it("refuses a gated repository visibly and explains that nothing was called", async () => {
    renderDetail({
      "/v1/onboarding/sessions/sess-1": {
        status: 200,
        body: session({
          repository: { ...session().repository, security_gate: "manual_env_review" },
        }),
      },
    });
    const gate = await screen.findByTestId("security-gate");
    expect(gate).toHaveTextContent(/manual security gate/i);
    expect(gate).toHaveTextContent(/no provider call and issues no token/i);
  });

  it("keeps a pending pull request distinct from an open one", async () => {
    renderDetail({
      "/v1/onboarding/sessions/sess-1": {
        status: 200,
        body: session({
          gitops_requests: [
            {
              id: "g1",
              state: "pending",
              branch_name: "drake/onboarding/abcd1234",
              file_path: ".drake/project.yaml",
              base_commit_sha: COMMIT,
              provider_pr_number: null,
              error_code: null,
              created_at: "2026-08-09T10:05:00Z",
              version: 1,
            },
          ],
        }),
      },
    });
    const requests = await screen.findByTestId("gitops-requests");
    expect(requests).toHaveTextContent("Pending at GitHub");
    expect(requests).not.toHaveTextContent("Pull request open");
    expect(
      screen.getByText(/merging a pull request does not import anything/i),
    ).toBeInTheDocument();
  });

  it("renders no repository content, credential or URL", async () => {
    const { container } = renderDetail();
    await screen.findByTestId("plan");
    const rendered = container.textContent ?? "";
    expect(rendered).not.toMatch(/https?:\/\//);
    expect(rendered).not.toContain("apiVersion");
    expect(rendered).not.toContain("-----BEGIN");
    expect(rendered.toLowerCase()).not.toContain("authorization");
    expect(container.querySelectorAll('a[href^="http"]')).toHaveLength(0);
    // Paths and digests are shown; file content never is.
    const analysisCard = screen.getByTestId("analysis");
    expect(analysisCard).toHaveTextContent(/stores no file content/i);
    expect(analysisCard).toHaveTextContent(/never reads environment files, private keys/i);
  });
});
