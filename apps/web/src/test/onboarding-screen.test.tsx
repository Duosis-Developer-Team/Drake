import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { OnboardingPanel } from "@/components/github/OnboardingPanel";
import { blockedReason, type GitHubRepository, type OnboardingDraft } from "@/lib/github";
import { errorBody, installFetchMock } from "@/test/mock-api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/integrations/github",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

const REPO: GitHubRepository = {
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

const SHA = "b".repeat(40);

function draft(overrides: Partial<OnboardingDraft> = {}): OnboardingDraft {
  return {
    repository_id: "r1",
    state: "ready_to_import",
    commit_sha: SHA,
    manifest_source: "repository",
    findings: [],
    discovery: {
      commit_sha: SHA,
      files: [{ path: ".drake/project.yaml", size: 400, sha256: "abc" }],
      detections: [
        { kind: "runtime", value: "fastapi", evidence: "pyproject.toml", confidence: "high" },
      ],
      truncated: false,
    },
    operator_inputs_required: [],
    importable: true,
    as_of: "2026-08-07T00:00:00Z",
    ...overrides,
  };
}

function routes(body: OnboardingDraft, extra: Record<string, { status: number; body: unknown }> = {}) {
  return installFetchMock({
    "/v1/integrations/github/repositories/r1/onboarding": { status: 200, body },
    "/v1/integrations/github/repositories/r1/onboarding/scan": { status: 202, body },
    ...extra,
  });
}

describe("catalog onboarding panel", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows the scanned commit and what discovery found", async () => {
    routes(draft());
    render(<OnboardingPanel repository={REPO} csrfToken="t" canManage />);
    fireEvent.click(screen.getByTestId("onboarding-scan-button"));

    await waitFor(() => expect(screen.getByTestId("onboarding-files")).toBeInTheDocument());
    expect(screen.getByText(new RegExp(SHA.slice(0, 12)))).toBeInTheDocument();
    const detections = within(screen.getByTestId("onboarding-detections"));
    expect(detections.getByText(/runtime: fastapi/)).toBeInTheDocument();
    expect(detections.getByText(/high confidence/)).toBeInTheDocument();
  });

  it("enables import only when the server says the draft is importable", async () => {
    routes(draft());
    render(<OnboardingPanel repository={REPO} csrfToken="t" canManage />);
    fireEvent.click(screen.getByTestId("onboarding-scan-button"));
    await waitFor(() =>
      expect(screen.getByTestId("onboarding-import-button")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("onboarding-import-button")).toBeEnabled();
  });

  it("offers a generated draft for download and refuses to import it", async () => {
    routes(
      draft({
        state: "needs_input",
        manifest_source: "operator_draft",
        importable: false,
        draft_manifest: "apiVersion: drake.duosis.com/v1alpha1\nmetadata:\n  name: REPLACE_ME\n",
        operator_inputs_required: [
          { field: "spec.owners[].team", reason: "Ownership is an organizational decision." },
        ],
      }),
    );
    render(<OnboardingPanel repository={REPO} csrfToken="t" canManage />);
    fireEvent.click(screen.getByTestId("onboarding-scan-button"));

    await waitFor(() => expect(screen.getByTestId("onboarding-generated")).toBeInTheDocument());
    expect(screen.getByText(/Commit this file to the repository/i)).toBeInTheDocument();
    expect(screen.getByTestId("onboarding-download")).toHaveAttribute("download", "project.yaml");
    expect(screen.getByTestId("onboarding-import-button")).toBeDisabled();
    expect(screen.getByTestId("onboarding-import-blocked")).toHaveTextContent(
      /Commit the manifest to the repository/i,
    );
    expect(
      within(screen.getByTestId("onboarding-operator-inputs")).getByText("spec.owners[].team"),
    ).toBeInTheDocument();
  });

  it("shows validation findings without dressing them as healthy", async () => {
    routes(
      draft({
        state: "invalid",
        importable: false,
        findings: [
          {
            path: "spec.dataStores[0].connectionSecretRef",
            rule: "credential-in-url",
            message: "URL value embeds inline credentials.",
          },
        ],
      }),
    );
    const { container } = render(
      <OnboardingPanel repository={REPO} csrfToken="t" canManage />,
    );
    fireEvent.click(screen.getByTestId("onboarding-scan-button"));

    await waitFor(() => expect(screen.getByTestId("onboarding-findings")).toBeInTheDocument());
    expect(screen.getByText("credential-in-url")).toBeInTheDocument();
    expect(screen.getByTestId("onboarding-import-button")).toBeDisabled();
    expect(container.querySelectorAll('[data-testid="status-healthy"]')).toHaveLength(0);
  });

  it("says plainly when a scan was cut short by a budget", async () => {
    routes(
      draft({
        state: "needs_input",
        importable: false,
        discovery: { ...draft().discovery, truncated: true },
      }),
    );
    render(<OnboardingPanel repository={REPO} csrfToken="t" canManage />);
    fireEvent.click(screen.getByTestId("onboarding-scan-button"));
    await waitFor(() => expect(screen.getByTestId("onboarding-truncated")).toBeInTheDocument());
    expect(screen.getByTestId("onboarding-truncated")).toHaveTextContent(/not a complete picture/i);
  });

  it("links to the project after an import", async () => {
    routes(draft({ state: "imported", accepted_project_id: "p1", importable: false }));
    render(<OnboardingPanel repository={REPO} csrfToken="t" canManage />);
    fireEvent.click(screen.getByTestId("onboarding-scan-button"));

    await waitFor(() => expect(screen.getByTestId("onboarding-imported")).toBeInTheDocument());
    expect(screen.getByTestId("onboarding-project-link")).toHaveAttribute("href", "/projects/p1");
    expect(screen.getByText(/nothing was deployed/i)).toBeInTheDocument();
  });

  it("hides every mutation control from a read-only user", async () => {
    routes(draft());
    render(<OnboardingPanel repository={REPO} csrfToken="t" canManage={false} />);
    expect(screen.queryByTestId("onboarding-scan-button")).not.toBeInTheDocument();
    expect(screen.queryByTestId("onboarding-import-button")).not.toBeInTheDocument();
    expect(screen.getByText(/needs integration management permission/i)).toBeInTheDocument();
  });

  it("refuses to offer onboarding for a gated repository", () => {
    render(
      <OnboardingPanel
        repository={{ ...REPO, security_gate: "manual_env_review" }}
        csrfToken="t"
        canManage
      />,
    );
    expect(screen.getByTestId("onboarding-blocked")).toHaveTextContent(/security gate/i);
    expect(screen.queryByTestId("onboarding-scan-button")).not.toBeInTheDocument();
  });

  it("surfaces a scan failure as an error, not an empty success", async () => {
    routes(draft(), {
      "/v1/integrations/github/repositories/r1/onboarding/scan": {
        status: 503,
        body: errorBody("github_unavailable", "GitHub could not be read right now."),
      },
    });
    render(<OnboardingPanel repository={REPO} csrfToken="t" canManage />);
    fireEvent.click(screen.getByTestId("onboarding-scan-button"));
    await waitFor(() => expect(screen.getByTestId("onboarding-error")).toBeInTheDocument());
    expect(screen.queryByTestId("onboarding-import-button")).not.toBeInTheDocument();
  });

  it("never treats a browser-edited manifest as importable", async () => {
    routes(
      draft({
        state: "needs_input",
        manifest_source: "operator_draft",
        importable: false,
        draft_manifest: "apiVersion: drake.duosis.com/v1alpha1\n",
      }),
      {
        "/v1/integrations/github/repositories/r1/onboarding/validate": {
          status: 200,
          body: {
            repository_id: "r1",
            valid: true,
            findings: [],
            importable: false,
            next_step: "Commit this file to the repository as .drake/project.yaml, then scan again.",
            as_of: "2026-08-07T00:00:00Z",
          },
        },
      },
    );
    render(<OnboardingPanel repository={REPO} csrfToken="t" canManage />);
    fireEvent.click(screen.getByTestId("onboarding-scan-button"));
    await waitFor(() =>
      expect(screen.getByTestId("onboarding-validate-button")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("onboarding-validate-button"));

    await waitFor(() => expect(screen.getByTestId("onboarding-validation")).toBeInTheDocument());
    const validation = within(screen.getByTestId("onboarding-validation"));
    expect(validation.getByText(/This draft is valid/)).toBeInTheDocument();
    expect(validation.getByText(/Commit this file to the repository/)).toBeInTheDocument();
    expect(screen.getByTestId("onboarding-import-button")).toBeDisabled();
  });
});

describe("blockedReason", () => {
  it("names the strongest reason first", () => {
    expect(blockedReason({ ...REPO, security_gate: "manual_env_review" })).toMatch(/security gate/i);
    expect(blockedReason({ ...REPO, access_state: "removed" })).toMatch(/access/i);
    expect(blockedReason({ ...REPO, onboarding_state: "degraded" })).toMatch(/Reconcile/i);
  });

  it("returns null when a repository is genuinely ready", () => {
    expect(blockedReason(REPO)).toBeNull();
  });
});
