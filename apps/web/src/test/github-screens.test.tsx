import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import GitHubIntegrationPage from "@/app/integrations/github/page";
import { OnboardingBadge, VerdictBadge } from "@/components/github/primitives";
import { isStale } from "@/lib/github";
import { errorBody, installFetchMock, makeMe } from "@/test/mock-api";

const sessionState = { current: makeMe({ permissions: ["integration.manage"] }) };
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
  usePathname: () => "/integrations/github",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

const CONFIGURED_STATUS = {
  configuration_state: "configured",
  missing_operator_inputs: [],
  installations: 1,
  repositories: 2,
  blocked_repositories: 1,
  supported_events: ["installation", "installation_repositories", "repository"],
  policy_profiles: ["default", "library", "service"],
  as_of: "2026-08-07T00:00:00Z",
};

const INSTALLATION = {
  id: "i1",
  external_id: 55501,
  account_login: "Duosis-Developer-Team",
  account_type: "Organization",
  app_slug: "drake",
  repository_selection: "selected",
  granted_permissions: { metadata: "read", administration: "read" },
  subscribed_events: ["installation", "repository"],
  state: "active",
  suspended_at: null,
  last_reconciled_at: "2026-08-07T00:00:00Z",
  last_error_code: null,
};

const HERMES = {
  id: "r1",
  provider: "github",
  external_id: 900001,
  owner_login: "Duosis-Developer-Team",
  name: "Hermes",
  full_name: "Duosis-Developer-Team/Hermes",
  private: true,
  visibility: "private",
  archived: false,
  disabled: false,
  default_branch: "main",
  onboarding_state: "ready",
  state_reason: "reconciled",
  security_gate: null,
  security_gate_reason: "",
  access_state: "accessible",
  last_reconciled_at: new Date().toISOString(),
  last_policy_evaluated_at: new Date().toISOString(),
  last_error_code: null,
  installation_external_id: 55501,
  as_of: "2026-08-07T00:00:00Z",
};

const DATALAKE = {
  ...HERMES,
  id: "r2",
  external_id: 900003,
  name: "Datalake-Platform-GUI",
  full_name: "Duosis-Developer-Team/Datalake-Platform-GUI",
  onboarding_state: "blocked",
  state_reason: "security_gate_manual_env_review",
  security_gate: "manual_env_review",
  security_gate_reason:
    "tracked .env manual security gate is open: operator review, credential rotation, git history containment are required before onboarding",
  last_reconciled_at: null,
  last_policy_evaluated_at: null,
};

const POLICY_SNAPSHOT = {
  repository_id: "r1",
  state: "evaluated",
  id: "p1",
  profile: "default",
  overall: "fail",
  blocking_count: 1,
  unknown_count: 1,
  dry_run: true,
  evaluated_at: "2026-08-07T00:00:00Z",
  evidence_digest: "abc",
  as_of: "2026-08-07T00:00:00Z",
  results: [
    {
      rule_id: "branch.protection.present",
      title: "Default branch is protected",
      verdict: "fail",
      severity: "critical",
      expected: "the default branch is covered by branch protection",
      observed: "no branch protection and no active ruleset cover the default branch",
      blocking: true,
      remediation: "Protect the default branch.",
      evidence: {},
    },
    {
      rule_id: "security.secret_scanning",
      title: "Secret scanning is enabled",
      verdict: "unknown",
      severity: "high",
      expected: "secret scanning is enabled",
      observed: "not determinable: missing permission",
      blocking: false,
      remediation: "Grant the scanning permission or enable scanning.",
      evidence: {},
    },
    {
      rule_id: "repo.default_branch.known",
      title: "Default branch is known",
      verdict: "pass",
      severity: "low",
      expected: "a default branch is reported",
      observed: "default branch is 'main'",
      blocking: false,
      remediation: "",
      evidence: {},
    },
  ],
};

function mockRoutes(overrides: Record<string, { status: number; body: unknown }> = {}) {
  return installFetchMock({
    "/v1/integrations/github/status": { status: 200, body: CONFIGURED_STATUS },
    "/v1/integrations/github/installations": {
      status: 200,
      body: { installations: [INSTALLATION] },
    },
    "/v1/integrations/github/repositories": {
      status: 200,
      body: { repositories: [HERMES, DATALAKE], next_cursor: null },
    },
    "/v1/integrations/github/repositories/r1/policy": { status: 200, body: POLICY_SNAPSHOT },
    "/v1/integrations/github/repositories/r1/reconcile": {
      status: 202,
      body: { repository_id: "r1", overall: "fail", dry_run: true },
    },
    ...overrides,
  });
}

describe("github integration screen", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    sessionState.current = makeMe({ permissions: ["integration.manage"] });
  });

  it("shows readiness, installation state and repositories", async () => {
    mockRoutes();
    render(<GitHubIntegrationPage />);
    await waitFor(() =>
      expect(screen.getByTestId("github-status-card")).toBeInTheDocument(),
    );
    const statusCard = within(screen.getByTestId("github-status-card"));
    expect(statusCard.getByText("configured")).toBeInTheDocument();
    // A blocked repository is surfaced as an operator action, not hidden.
    expect(statusCard.getByText("operator action required")).toBeInTheDocument();

    await waitFor(() => expect(screen.getByTestId("installation-list")).toBeInTheDocument());
    expect(screen.getByText("Duosis-Developer-Team")).toBeInTheDocument();
    expect(within(screen.getByTestId("installation-list")).getByText("active")).toBeInTheDocument();

    const repositories = within(screen.getByTestId("repository-list"));
    expect(repositories.getByText("Duosis-Developer-Team/Hermes")).toBeInTheDocument();
    expect(repositories.getByText("ready")).toBeInTheDocument();
  });

  it("renders the Datalake manual security gate and disables reconciliation", async () => {
    mockRoutes();
    render(<GitHubIntegrationPage />);
    await waitFor(() => expect(screen.getByTestId("repository-list")).toBeInTheDocument());

    const gate = screen.getByTestId("security-gate-warning");
    expect(gate).toBeInTheDocument();
    expect(
      within(gate).getByText("Blocked by a manual security gate"),
    ).toBeInTheDocument();
    expect(screen.getByText(/tracked \.env/i)).toBeInTheDocument();

    const cards = screen.getAllByTestId("repository-card");
    const datalakeCard = cards.find((card) =>
      card.textContent?.includes("Datalake-Platform-GUI"),
    );
    expect(datalakeCard).toBeTruthy();
    const button = within(datalakeCard!).getByTestId("reconcile-button");
    expect(button).toBeDisabled();
    expect(within(datalakeCard!).getByText("blocked")).toBeInTheDocument();
  });

  it("shows an honest NOT_CONFIGURED state with the missing operator inputs", async () => {
    mockRoutes({
      "/v1/integrations/github/status": {
        status: 200,
        body: {
          ...CONFIGURED_STATUS,
          configuration_state: "not_configured",
          missing_operator_inputs: ["feature_disabled", "private_key_reference"],
          installations: 0,
          repositories: 0,
          blocked_repositories: 0,
        },
      },
      "/v1/integrations/github/installations": { status: 200, body: { installations: [] } },
      "/v1/integrations/github/repositories": {
        status: 200,
        body: { repositories: [], next_cursor: null },
      },
    });
    render(<GitHubIntegrationPage />);
    await waitFor(() =>
      expect(screen.getByTestId("github-not-configured")).toBeInTheDocument(),
    );
    expect(screen.getByText(/private key secret reference/i)).toBeInTheDocument();
    expect(screen.getByText(/switched off/i)).toBeInTheDocument();
    // Screens stay useful rather than blank or broken.
    expect(screen.getByText(/No installation yet/i)).toBeInTheDocument();
    expect(screen.getByText(/No repositories in your scope/i)).toBeInTheDocument();
  });

  it("shows blocking violations and unknown verdicts distinctly", async () => {
    mockRoutes();
    render(<GitHubIntegrationPage />);
    await waitFor(() => expect(screen.getByTestId("repository-list")).toBeInTheDocument());

    const cards = screen.getAllByTestId("repository-card");
    const hermes = cards.find((card) => card.textContent?.includes("/Hermes"))!;
    fireEvent.click(within(hermes).getByRole("button", { name: /show last policy result/i }));

    await waitFor(() => expect(within(hermes).getByTestId("policy-result")).toBeInTheDocument());
    const blocking = within(hermes).getByTestId("blocking-violations");
    expect(within(blocking).getByText("Default branch is protected")).toBeInTheDocument();
    expect(within(blocking).getByText(/Protect the default branch/i)).toBeInTheDocument();
    // An unreadable rule is shown as unknown — never as a pass.
    const result = within(hermes).getByTestId("policy-result");
    expect(within(result).getByText("unknown")).toBeInTheDocument();
    expect(within(result).getByText("dry run")).toBeInTheDocument();
  });

  it("hides the manage action from a read-only user", async () => {
    sessionState.current = makeMe({ permissions: ["project.view"] });
    mockRoutes();
    render(<GitHubIntegrationPage />);
    await waitFor(() => expect(screen.getByTestId("repository-list")).toBeInTheDocument());
    expect(screen.queryByTestId("reconcile-button")).not.toBeInTheDocument();
    // Reading the last result stays available.
    expect(screen.getAllByRole("button", { name: /show last policy result/i }).length).toBe(2);
  });

  it("surfaces an API failure as an error state with retry", async () => {
    mockRoutes({
      "/v1/integrations/github/repositories": {
        status: 503,
        body: errorBody("dependency_unavailable", "github integration unavailable"),
      },
    });
    render(<GitHubIntegrationPage />);
    await waitFor(() => expect(screen.getByTestId("state-error")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("never dresses blocked, degraded, disabled or unknown in the healthy colour", () => {
    const { container } = render(
      <>
        <OnboardingBadge state="blocked" />
        <OnboardingBadge state="degraded" />
        <OnboardingBadge state="disabled" />
        <OnboardingBadge state="discovered" />
        <OnboardingBadge state="validating" />
        <VerdictBadge verdict="fail" />
        <VerdictBadge verdict="warn" />
        <VerdictBadge verdict="unknown" />
      </>,
    );
    expect(container.querySelectorAll('[data-testid="status-healthy"]')).toHaveLength(0);
  });

  it("says plainly when an installation still owes a reconciliation", async () => {
    mockRoutes({
      "/v1/integrations/github/repositories": {
        status: 200,
        body: {
          repositories: [{ ...HERMES, pending_reconciliation: true }, DATALAKE],
          next_cursor: null,
        },
      },
    });
    render(<GitHubIntegrationPage />);
    await waitFor(() => expect(screen.getByTestId("repository-list")).toBeInTheDocument());
    const notice = screen.getByTestId("reconciliation-required");
    expect(within(notice).getByText("Reconciliation required")).toBeInTheDocument();
    expect(screen.getByText(/may be incomplete/i)).toBeInTheDocument();
  });

  it("does not claim reconciliation is pending when it is not", async () => {
    mockRoutes();
    render(<GitHubIntegrationPage />);
    await waitFor(() => expect(screen.getByTestId("repository-list")).toBeInTheDocument());
    expect(screen.queryByTestId("reconciliation-required")).not.toBeInTheDocument();
  });

  it("treats a missing or old reconciliation as stale", () => {
    const now = Date.parse("2026-08-07T12:00:00Z");
    expect(isStale(null, now)).toBe(true);
    expect(isStale("not-a-date", now)).toBe(true);
    expect(isStale("2026-08-05T12:00:00Z", now)).toBe(true);
    expect(isStale("2026-08-07T11:00:00Z", now)).toBe(false);
  });
});
