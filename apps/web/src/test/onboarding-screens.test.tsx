import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import OnboardingSessionPage from "@/app/onboarding/[sessionId]/page";
import OnboardingPage from "@/app/onboarding/page";
import { errorBody, installFetchMock, makeMe } from "@/test/mock-api";

const sessionState = {
  current: makeMe({ permissions: ["onboarding.view", "onboarding.manage", "onboarding.apply"] }),
};
vi.mock("@/lib/session", () => ({
  useSession: () => ({
    state: { status: "authenticated", me: sessionState.current },
    refresh: vi.fn(),
    signOut: vi.fn(),
    hasPermission: (permission: string) =>
      sessionState.current.permissions.includes(permission),
  }),
}));

const routerState = { push: vi.fn() };
vi.mock("next/navigation", () => ({
  usePathname: () => "/onboarding",
  useRouter: () => ({ push: routerState.push, replace: vi.fn() }),
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

function candidate(overrides: Record<string, unknown> = {}) {
  return {
    id: "repo-1",
    full_name: "Duosis-Developer-Team/Datalake-Platform-GUI",
    default_branch: "main",
    onboarding_state: "ready",
    access_state: "accessible",
    security_gate: null,
    active_session_id: null,
    startable: true,
    reason_code: null,
    ...overrides,
  };
}

function renderList(overrides: Record<string, { status: number; body: unknown }> = {}) {
  const calls = installFetchMock({
    "/v1/onboarding/github/status": { status: 200, body: status() },
    "/v1/onboarding/sessions": {
      status: 200,
      body: { items: [session()], total: 1, limit: 25, offset: 0 },
    },
    "/v1/onboarding/repositories": {
      status: 200,
      body: { items: [candidate()], next_cursor: null },
    },
    ...overrides,
  });
  const view = render(<OnboardingPage />);
  return { calls, ...view };
}

function renderDetail(overrides: Record<string, { status: number; body: unknown }> = {}) {
  const calls = installFetchMock({
    "/v1/onboarding/github/status": { status: 200, body: status() },
    "/v1/onboarding/sessions/sess-1": { status: 200, body: session() },
    "/v1/onboarding/sessions/sess-1/plan": { status: 200, body: plan() },
    "/v1/onboarding/sessions/sess-1/findings": { status: 200, body: analysis() },
    ...overrides,
  });
  const view = render(<OnboardingSessionPage />);
  return { calls, ...view };
}

afterEach(() => {
  vi.unstubAllGlobals();
  routerState.push.mockReset();
  sessionState.current = makeMe({
    permissions: ["onboarding.view", "onboarding.manage", "onboarding.apply"],
  });
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

// ===========================================================================
// Sprint 12A.2a — the operator actions, and the states they run from
// ===========================================================================

function applyResult(overrides: Record<string, unknown> = {}) {
  return {
    outcome: "applied",
    project_id: "proj-1",
    created_entities: 7,
    linked_entities: 1,
    unchanged_entities: 2,
    no_change_count: 2,
    metadata_updated: 3,
    slo_definitions_created: 1,
    slo_definitions_updated: 0,
    bindings_created: 2,
    ...overrides,
  };
}

describe("starting an onboarding", () => {
  it("does not create a session merely because the page was opened", async () => {
    const { calls } = renderList();
    expect(await screen.findByTestId("start-onboarding")).toBeInTheDocument();
    // Arriving with a preselected repository is a link somebody was sent.
    // Acting on it without a click would open a session on their behalf.
    expect(calls.filter((call) => call.init?.method === "POST")).toHaveLength(0);
    expect(routerState.push).not.toHaveBeenCalled();
  });

  it("creates a session only when the operator asks, then goes to it", async () => {
    const { calls } = renderList({
      "/v1/onboarding/sessions": {
        status: 200,
        body: { items: [], total: 0, limit: 25, offset: 0 },
      },
    });
    const select = await screen.findByTestId("repository-select");
    fireEvent.change(select, { target: { value: "repo-1" } });
    fireEvent.click(screen.getByTestId("start-onboarding-button"));

    await waitFor(() => expect(routerState.push).toHaveBeenCalled());
    const posted = calls.find((call) => call.init?.method === "POST");
    expect(posted?.path).toBe("/v1/onboarding/sessions");
    expect(JSON.parse(String(posted?.init?.body))).toEqual({ repository_id: "repo-1" });
  });

  it("sends an operator to the session that already exists", async () => {
    const { calls } = renderList({
      "/v1/onboarding/repositories": {
        status: 200,
        body: {
          items: [
            candidate({
              active_session_id: "sess-9",
              startable: false,
              reason_code: "session_in_progress",
            }),
          ],
          next_cursor: null,
        },
      },
    });
    fireEvent.change(await screen.findByTestId("repository-select"), {
      target: { value: "repo-1" },
    });
    fireEvent.click(screen.getByTestId("start-onboarding-button"));
    await waitFor(() => expect(routerState.push).toHaveBeenCalledWith("/onboarding/sess-9"));
    // No second session beside the first one.
    expect(calls.filter((call) => call.init?.method === "POST")).toHaveLength(0);
  });

  it("says why a gated repository cannot be started, and disables the button", async () => {
    renderList({
      "/v1/onboarding/repositories": {
        status: 200,
        body: {
          items: [candidate({ startable: false, reason_code: "security_gate_open", security_gate: "manual_env_review" })],
          next_cursor: null,
        },
      },
    });
    fireEvent.change(await screen.findByTestId("repository-select"), {
      target: { value: "repo-1" },
    });
    // Not only grey: the reason is in words.
    expect(await screen.findByTestId("repository-blocked")).toHaveTextContent(/security review/i);
    expect(screen.getByTestId("start-onboarding-button")).toBeDisabled();
  });

  it("separates 'you may not' from 'there are none'", async () => {
    const denied = renderList({ "/v1/onboarding/github/status": { status: 200, body: status({ can_manage: false }) } });
    expect(await screen.findByTestId("start-permission-denied")).toBeInTheDocument();
    denied.unmount();

    renderList({
      "/v1/onboarding/repositories": { status: 200, body: { items: [], next_cursor: null } },
    });
    expect(await screen.findByTestId("start-empty")).toBeInTheDocument();
  });

  it("does not offer to start anything when GitHub is not configured", async () => {
    renderList({
      "/v1/onboarding/github/status": {
        status: 200,
        body: status({ configuration_state: "not_configured", missing_operator_inputs: ["app_identity"] }),
      },
    });
    expect(await screen.findByTestId("github-not-configured")).toBeInTheDocument();
    expect(screen.queryByTestId("start-onboarding")).not.toBeInTheDocument();
  });
});

describe("session actions", () => {
  it("offers analyse on a draft session and nothing that needs a plan", async () => {
    renderDetail({
      "/v1/onboarding/sessions/sess-1": {
        status: 200,
        body: session({ state: "draft", plan: null, analyzed_commit_sha: null }),
      },
      "/v1/onboarding/sessions/sess-1/plan": { status: 200, body: { plan: null, items: [] } },
      "/v1/onboarding/sessions/sess-1/findings": { status: 200, body: { analysis: null, findings: [] } },
    });
    expect(await screen.findByTestId("action-analyze")).toHaveTextContent("Analyse repository");
    expect(screen.queryByTestId("action-approve")).not.toBeInTheDocument();
    expect(screen.queryByTestId("action-apply")).not.toBeInTheDocument();
    // No analysis yet, so there is nothing to build a draft from.
    expect(screen.queryByTestId("action-manifest-draft")).not.toBeInTheDocument();
  });

  it("says 'analyse again' once a plan exists", async () => {
    renderDetail();
    expect(await screen.findByTestId("action-analyze")).toHaveTextContent("Analyse again");
  });

  it("offers no mutation at all to a viewer", async () => {
    renderDetail({
      "/v1/onboarding/sessions/sess-1": {
        status: 200,
        body: session({ can_manage: false, can_apply: false, can_gitops: false }),
      },
    });
    await screen.findByTestId("session-actions");
    for (const action of ["analyze", "approve", "apply", "cancel", "gitops"]) {
      expect(screen.queryByTestId(`action-${action}`)).not.toBeInTheDocument();
    }
  });

  it("confirms an approval with the version, commit, digest and item count", async () => {
    const { calls } = renderDetail();
    fireEvent.click(await screen.findByTestId("action-approve"));
    const dialog = await screen.findByTestId("confirm-approve");
    expect(dialog).toHaveTextContent("v1");
    expect(dialog).toHaveTextContent(COMMIT.slice(0, 12));
    expect(dialog).toHaveTextContent("ab12cd34ef56");
    // Never the manifest, and never a payload dump.
    expect(dialog.textContent).not.toContain("apiVersion");

    fireEvent.click(screen.getByTestId("confirm-approve-yes"));
    await waitFor(() =>
      expect(calls.some((call) => call.path.endsWith("/approve"))).toBe(true),
    );
    const posted = calls.find((call) => call.path.endsWith("/approve"));
    expect(JSON.parse(String(posted?.init?.body))).toEqual({
      plan_version: 1,
      expected_version: 3,
    });
  });

  it("blocks approval when an item needs a decision, in words", async () => {
    renderDetail({
      "/v1/onboarding/sessions/sess-1/plan": {
        status: 200,
        body: {
          ...plan(),
          plan: { ...plan().plan, applicable: false, blocking_items: 2 },
        },
      },
    });
    expect(await screen.findByTestId("approve-blocked")).toHaveTextContent(/2 item/);
    expect(screen.getByTestId("action-approve")).toBeDisabled();
  });

  it("says apply writes to the catalog and not to the repository", async () => {
    renderDetail({
      "/v1/onboarding/sessions/sess-1": {
        status: 200,
        body: session({ state: "approved", approved_plan_version: 1, approved_at: "2026-08-09T10:30:00Z" }),
      },
    });
    fireEvent.click(await screen.findByTestId("action-apply"));
    const dialog = await screen.findByTestId("confirm-apply");
    expect(dialog).toHaveTextContent("writes the approved plan to Drake");
    expect(dialog).toHaveTextContent("does not write to the repository");
  });

  it("shows every counter, and 'Not recorded' for one a legacy receipt lacks", async () => {
    const { calls } = renderDetail({
      "/v1/onboarding/sessions/sess-1": {
        status: 200,
        body: session({ state: "approved", approved_plan_version: 1 }),
      },
      "/v1/onboarding/sessions/sess-1/apply": {
        status: 200,
        body: applyResult({ metadata_updated: null, bindings_created: null }),
      },
    });
    fireEvent.click(await screen.findByTestId("action-apply"));
    fireEvent.click(await screen.findByTestId("confirm-apply-yes"));

    const result = await screen.findByTestId("apply-result");
    expect(result).toHaveTextContent("Created");
    expect(result).toHaveTextContent("SLOs created");
    // `null` is "the receipt never recorded this", not zero.
    expect(within(result).getAllByText("Not recorded")).toHaveLength(2);
    expect(result).not.toHaveTextContent("NaN");
    expect(calls.some((call) => call.path.endsWith("/apply"))).toBe(true);
  });

  it("keeps one idempotency key across a retry, in the header and the body", async () => {
    // First attempt fails at the network layer, second succeeds. A new key
    // on the retry would describe a new operation — so an apply that had
    // already committed would be applied twice.
    let attempt = 0;
    const bodies: string[] = [];
    const headers: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input);
        if (path.endsWith("/apply")) {
          attempt += 1;
          bodies.push(String(init?.body));
          headers.push(String((init?.headers as Record<string, string>)["Idempotency-Key"]));
          if (attempt === 1) throw new TypeError("network down");
          return new Response(JSON.stringify(applyResult()), { status: 200 });
        }
        const routes: Record<string, unknown> = {
          "/v1/onboarding/github/status": status(),
          "/v1/onboarding/sessions/sess-1": session({ state: "approved", approved_plan_version: 1 }),
          "/v1/onboarding/sessions/sess-1/plan": plan(),
          "/v1/onboarding/sessions/sess-1/findings": analysis(),
        };
        const body = routes[path.split("?")[0]];
        return new Response(JSON.stringify(body ?? {}), { status: body ? 200 : 404 });
      }),
    );
    render(<OnboardingSessionPage />);

    fireEvent.click(await screen.findByTestId("action-apply"));
    fireEvent.click(await screen.findByTestId("confirm-apply-yes"));
    await screen.findByTestId("action-error");

    fireEvent.click(screen.getByTestId("action-apply"));
    fireEvent.click(await screen.findByTestId("confirm-apply-yes"));
    await screen.findByTestId("apply-result");

    expect(attempt).toBe(2);
    const first = JSON.parse(bodies[0]).idempotency_key;
    const second = JSON.parse(bodies[1]).idempotency_key;
    expect(first).toBe(second);
    expect(headers[0]).toBe(first);
    expect(headers[1]).toBe(first);
    // And it lives only in component memory.
    expect(window.localStorage.getItem("idempotency-key")).toBeNull();
    expect(window.location.search).not.toContain(first);
  });

  it("refetches instead of retrying a stale client's action", async () => {
    const { calls } = renderDetail({
      "/v1/onboarding/sessions/sess-1/approve": {
        status: 409,
        body: errorBody("version_conflict", "The session changed since it was read."),
      },
    });
    fireEvent.click(await screen.findByTestId("action-approve"));
    const before = calls.length;
    fireEvent.click(await screen.findByTestId("confirm-approve-yes"));

    expect(await screen.findByTestId("action-error")).toHaveTextContent(/changed while you were/i);
    // The confirmation closed — it was asking about a state that has moved.
    await waitFor(() => expect(screen.queryByTestId("confirm-approve")).not.toBeInTheDocument());
    await waitFor(() => expect(calls.length).toBeGreaterThan(before + 1));
  });

  it("disables the GitOps request while repository writes are off, and sends nothing", async () => {
    const { calls } = renderDetail({
      "/v1/onboarding/sessions/sess-1": { status: 200, body: session({ can_gitops: true }) },
      "/v1/onboarding/github/status": { status: 200, body: status({ gitops_pr_enabled: false }) },
    });
    const button = await screen.findByTestId("action-gitops");
    expect(button).toBeDisabled();
    expect(screen.getByTestId("gitops-disabled")).toHaveTextContent(
      "No branch or pull request will be created.",
    );
    fireEvent.click(button);
    expect(calls.some((call) => call.path.endsWith("/gitops-request"))).toBe(false);
  });

  it("offers nothing on an imported session, and offers the project", async () => {
    renderDetail({
      "/v1/onboarding/sessions/sess-1": {
        status: 200,
        body: session({
          state: "imported",
          imported_project_id: "proj-1",
          imported_project_key: "datalake",
          imported_at: "2026-08-09T11:00:00Z",
        }),
      },
    });
    await screen.findByTestId("session-actions");
    for (const action of ["analyze", "approve", "apply", "cancel", "gitops"]) {
      expect(screen.queryByTestId(`action-${action}`)).not.toBeInTheDocument();
    }
    expect(screen.getAllByText("datalake").length).toBeGreaterThan(0);
  });

  it("offers nothing on a cancelled session", async () => {
    renderDetail({
      "/v1/onboarding/sessions/sess-1": { status: 200, body: session({ state: "cancelled" }) },
    });
    await screen.findByTestId("session-actions");
    for (const action of ["analyze", "approve", "apply", "cancel"]) {
      expect(screen.queryByTestId(`action-${action}`)).not.toBeInTheDocument();
    }
  });

  it("offers analyse again on a failed or provider-unavailable session", async () => {
    for (const state of ["failed", "provider_unavailable", "stale"]) {
      const view = renderDetail({
        "/v1/onboarding/sessions/sess-1": { status: 200, body: session({ state }) },
      });
      expect(await screen.findByTestId("action-analyze")).toBeInTheDocument();
      view.unmount();
      vi.unstubAllGlobals();
    }
  });

  it("offers nothing while an analysis is running", async () => {
    renderDetail({
      "/v1/onboarding/sessions/sess-1": { status: 200, body: session({ state: "analyzing" }) },
    });
    await screen.findByTestId("session-actions");
    for (const action of ["analyze", "approve", "apply", "cancel", "gitops"]) {
      expect(screen.queryByTestId(`action-${action}`)).not.toBeInTheDocument();
    }
  });

  it("requires a confirmation before cancelling, and says nothing is removed", async () => {
    const { calls } = renderDetail();
    fireEvent.click(await screen.findByTestId("action-cancel"));
    const dialog = await screen.findByTestId("confirm-cancel");
    expect(dialog).toHaveTextContent("Nothing is removed from the catalog");
    // Not sent until it is confirmed.
    expect(calls.some((call) => call.path.endsWith("/cancel"))).toBe(false);
    fireEvent.click(screen.getByTestId("confirm-cancel-yes"));
    await waitFor(() => expect(calls.some((call) => call.path.endsWith("/cancel"))).toBe(true));
  });
});

describe("the plan a reviewer reads", () => {
  it("shows a metadata update as an update, not as no change", async () => {
    renderDetail({
      "/v1/onboarding/sessions/sess-1/plan": {
        status: 200,
        body: {
          ...plan(),
          items: [
            planItem({
              entity_kind: "project",
              action: "update_metadata",
              item_key: "project:datalake",
              proposed_name: "Datalake Platform",
              changes: {
                display_name: { before: "Datalake", after: "Datalake Platform" },
              },
            }),
          ],
        },
      },
    });
    // Its own group, not filed under "No change".
    expect(await screen.findByTestId("plan-group-update_metadata")).toBeInTheDocument();
    const changes = screen.getByTestId("changes-project:datalake");
    expect(changes).toHaveTextContent("display_name");
    expect(changes).toHaveTextContent("before: Datalake");
    expect(changes).toHaveTextContent("after: Datalake Platform");
  });

  it("shows an absent previous value as absent, not as an empty string", async () => {
    renderDetail({
      "/v1/onboarding/sessions/sess-1/plan": {
        status: 200,
        body: {
          ...plan(),
          items: [
            planItem({
              entity_kind: "service",
              action: "update_metadata",
              item_key: "service:datalake-api",
              changes: { component: { before: null, after: "api" } },
            }),
          ],
        },
      },
    });
    const changes = await screen.findByTestId("changes-service:datalake-api");
    // "there was nothing recorded" is not "it was blank".
    expect(changes).toHaveTextContent("before: —");
    expect(changes).toHaveTextContent("after: api");
  });

  it("keeps a workload binding as its own kind", async () => {
    renderDetail({
      "/v1/onboarding/sessions/sess-1/plan": {
        status: 200,
        body: {
          ...plan(),
          items: [
            planItem({
              entity_kind: "workload_binding",
              action: "create",
              item_key: "binding:datalake-api",
              proposed_name: "datalake-api",
            }),
          ],
        },
      },
    });
    expect(await screen.findByText("workload_binding")).toBeInTheDocument();
  });

  it("does not present a deployment source as something that was written", async () => {
    renderDetail({
      "/v1/onboarding/sessions/sess-1/plan": {
        status: 200,
        body: {
          ...plan(),
          items: [
            planItem({
              entity_kind: "deployment_source",
              action: "create",
              item_key: "deployment_source:github-actions",
              proposed_name: "github-actions",
              detail: { materialized: false, not_materialized_reason: "catalog_projection_not_supported" },
            }),
          ],
        },
      },
    });
    expect(await screen.findByTestId("deployment-source-note")).toHaveTextContent(
      /evidence only/i,
    );
  });

  it("renders no raw JSON for a structured change", async () => {
    renderDetail({
      "/v1/onboarding/sessions/sess-1/plan": {
        status: 200,
        body: {
          ...plan(),
          items: [
            planItem({
              action: "update_metadata",
              item_key: "service:datalake-api",
              changes: { labels: { before: null, after: { team: "data" } } },
            }),
          ],
        },
      },
    });
    const changes = await screen.findByTestId("changes-service:datalake-api");
    expect(changes).toHaveTextContent("(structured value)");
    expect(changes.textContent).not.toContain("{");
  });
});
