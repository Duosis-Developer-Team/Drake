/**
 * The onboarding screens rendered in Turkish.
 *
 * Proves the wiring, not the translation: onboarding-screens keeps asserting
 * English without a provider; this wraps the same screens in a
 * `LocaleProvider` pinned to `tr` and checks that the catalogue, the enum
 * badges (`session.*`, `gitops.*`) and the action panel all follow it.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import OnboardingSessionPage from "@/app/onboarding/[sessionId]/page";
import OnboardingPage from "@/app/onboarding/page";
import { LocaleProvider } from "@/lib/i18n";
import { installFetchMock, makeMe } from "@/test/mock-api";

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

vi.mock("next/navigation", () => ({
  usePathname: () => "/onboarding",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({ sessionId: "sess-1" }),
}));

const COMMIT = "4f1c9a2b7e5d3086c1a4b9e7f2d0538ac6b1e492";

const STATUS = {
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
};

const SESSION = {
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
    name: "Widget-Service",
    full_name: "Duosis-Developer-Team/Widget-Service",
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
  gitops_requests: [
    {
      id: "g1",
      state: "pending",
      branch_name: "drake/onboarding/abcd1234",
      file_path: ".drake/project.yaml",
      base_commit_sha: COMMIT,
      provider_pr_number: null,
      pull_request_url: null,
      error_code: null,
      created_at: "2026-08-09T10:05:00Z",
      version: 1,
    },
  ],
};

const PLAN = {
  plan: {
    id: "plan-1",
    plan_version: 1,
    state: "ready",
    commit_sha: COMMIT,
    manifest_digest: "aa11bb22",
    analyzer_version: 1,
    plan_digest: "ab12cd34ef56",
    blocking_items: 0,
    total_items: 1,
    created_at: "2026-08-09T10:00:00Z",
    applicable: true,
  },
  items: [
    {
      entity_kind: "service",
      action: "create",
      item_key: "service:widget-api",
      proposed_name: "widget-api",
      existing_entity_id: null,
      existing_name: null,
      reason_code: null,
      reason: "",
      detail: {},
      blocking: false,
    },
  ],
};

const FINDINGS = {
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
  findings: [],
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("onboarding in Turkish", () => {
  it("renders the start page: steps, picker, pipeline and session rows", async () => {
    installFetchMock({
      "/v1/onboarding/github/status": { status: 200, body: STATUS },
      "/v1/onboarding/sessions": {
        status: 200,
        body: { items: [SESSION], total: 1, limit: 25, offset: 0 },
      },
      "/v1/onboarding/repositories": {
        status: 200,
        body: {
          items: [
            {
              id: "repo-1",
              full_name: "Duosis-Developer-Team/Widget-Service",
              default_branch: "main",
              onboarding_state: "ready",
              access_state: "accessible",
              security_gate: null,
              active_session_id: null,
              startable: true,
              reason_code: null,
            },
          ],
          next_cursor: null,
        },
      },
    });
    render(
      <LocaleProvider locale="tr">
        <OnboardingPage />
      </LocaleProvider>,
    );

    const steps = await screen.findByTestId("wizard-steps");
    expect(steps).toHaveTextContent("Güvenli keşif");
    expect(steps).toHaveTextContent("Onay");
    expect(screen.getByRole("heading", { name: "Proje katılımı" })).toBeInTheDocument();
    expect(screen.getByText(/derleme, kurulum, betik, hook/)).toBeInTheDocument();
    expect(screen.getByTestId("gitops-disabled")).toHaveTextContent("hiçbir depoya yazmayacak");
    expect(screen.getByPlaceholderText("Depo ara…")).toBeInTheDocument();
    expect(screen.getByTestId("start-onboarding-button")).toHaveTextContent("Katılımı başlat");
    // The session state badge follows the locale through `session.*`.
    const row = screen.getByTestId("session-row-Widget-Service");
    expect(within(row).getByText("Onaya hazır")).toBeInTheDocument();
    expect(screen.queryByText("Onboard a project")).not.toBeInTheDocument();
  });

  it("renders the session page: plan groups, actions and the confirmation", async () => {
    installFetchMock({
      "/v1/onboarding/github/status": { status: 200, body: STATUS },
      "/v1/onboarding/sessions/sess-1": { status: 200, body: SESSION },
      "/v1/onboarding/sessions/sess-1/plan": { status: 200, body: PLAN },
      "/v1/onboarding/sessions/sess-1/findings": { status: 200, body: FINDINGS },
    });
    render(
      <LocaleProvider locale="tr">
        <OnboardingSessionPage />
      </LocaleProvider>,
    );

    const created = await screen.findByTestId("plan-group-create");
    expect(created).toHaveTextContent("Oluşturulacak");
    expect(within(created).getByText("Oluştur")).toBeInTheDocument();
    expect(screen.getByTestId("apply-available")).toHaveTextContent(
      "depoda hiçbir şeyi değiştirmez",
    );
    expect(screen.getByTestId("gitops-requests")).toHaveTextContent("GitHub'da bekliyor");
    expect(screen.getByText("Güvenli keşif")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("action-approve"));
    const dialog = await screen.findByTestId("confirm-approve");
    expect(dialog).toHaveTextContent("Bu plan onaylansın mı?");
    expect(dialog).toHaveTextContent("Plan sürümü");
    await waitFor(() => expect(screen.getByTestId("confirm-approve-yes")).toHaveTextContent("Onayla"));
  });
});
