/**
 * The integrations screens rendered in Turkish.
 *
 * One test per screen, proving the wiring rather than the translation: the
 * English tests in catalog-screens and github-screens keep asserting English
 * without a provider, and these two assert that a `LocaleProvider` pinned to
 * `tr` flips the same screens to the Turkish catalogue.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import GitHubIntegrationPage from "@/app/integrations/github/page";
import IntegrationsPage from "@/app/integrations/page";
import { LocaleProvider } from "@/lib/i18n";
import { installFetchMock, makeMe } from "@/test/mock-api";

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
  usePathname: () => "/integrations",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("integrations in Turkish", () => {
  it("renders the overview from the Turkish catalogue", async () => {
    installFetchMock({
      "/v1/integrations/health": {
        status: 200,
        body: {
          integrations: [
            {
              integration_type: "github",
              scope: { type: "project", ref: "alpha" },
              configuration_state: "configured",
              observed_state: "ok",
              last_sync_attempt_at: "2026-08-06T00:00:00Z",
              last_success_at: "2026-08-06T00:00:00Z",
              last_error_code: null,
              schema_version: 1,
              as_of: "2026-08-06T00:00:00Z",
            },
            {
              integration_type: "prometheus",
              scope: { type: "project", ref: "beta" },
              configuration_state: "not_configured",
              observed_state: "unknown",
              last_sync_attempt_at: null,
              last_success_at: null,
              last_error_code: null,
              schema_version: 1,
              as_of: "2026-08-06T00:00:00Z",
            },
          ],
          next_cursor: null,
        },
      },
    });
    render(
      <LocaleProvider locale="tr">
        <IntegrationsPage />
      </LocaleProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("integration-table")).toBeInTheDocument());

    expect(screen.getByRole("heading", { name: "Entegrasyon sağlığı" })).toBeInTheDocument();
    expect(screen.getByText("Sağlayıcılar", { selector: "h2" })).toBeInTheDocument();
    expect(screen.getByText("Depo yönetişimi")).toBeInTheDocument();
    expect(screen.getByText("Gözlemlenen sağlık")).toBeInTheDocument();
    // A connector that never synced says so in Turkish; the API's own state
    // tokens stay as identifiers.
    expect(screen.getByText("hiç")).toBeInTheDocument();
    expect(screen.getByText("not_configured")).toBeInTheDocument();
    expect(screen.queryByText("Integration Health")).not.toBeInTheDocument();
  });

  it("renders the GitHub App page, its badges and the missing operator inputs in Turkish", async () => {
    installFetchMock({
      "/v1/integrations/github/status": {
        status: 200,
        body: {
          configuration_state: "not_configured",
          missing_operator_inputs: ["feature_disabled", "private_key_reference"],
          installations: 0,
          repositories: 0,
          blocked_repositories: 0,
          supported_events: [],
          policy_profiles: [],
          as_of: "2026-08-07T00:00:00Z",
        },
      },
      "/v1/integrations/github/installations": {
        status: 200,
        body: {
          installations: [
            {
              id: "i1",
              external_id: 55501,
              account_login: "Duosis-Developer-Team",
              account_type: "Organization",
              app_slug: "drake",
              repository_selection: "selected",
              granted_permissions: {},
              subscribed_events: ["installation"],
              state: "suspended",
              suspended_at: "2026-08-07T00:00:00Z",
              last_reconciled_at: null,
              last_error_code: null,
            },
          ],
        },
      },
      "/v1/integrations/github/repositories": {
        status: 200,
        body: { repositories: [], next_cursor: null },
      },
    });
    render(
      <LocaleProvider locale="tr">
        <GitHubIntegrationPage />
      </LocaleProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("github-not-configured")).toBeInTheDocument());

    expect(screen.getByRole("heading", { name: "GitHub App entegrasyonu" })).toBeInTheDocument();
    expect(screen.getByText("GitHub App henüz bağlı değil")).toBeInTheDocument();
    expect(screen.getByText("GitHub App entegrasyonu kapalı")).toBeInTheDocument();
    expect(screen.getByText("Özel anahtar referansı")).toBeInTheDocument();
    expect(screen.getByText("Kapsamınızda depo yok")).toBeInTheDocument();

    await waitFor(() => expect(screen.getByTestId("installation-list")).toBeInTheDocument());
    expect(within(screen.getByTestId("installation-list")).getByText("askıya alındı")).toBeInTheDocument();
    expect(screen.queryByText(/switched off/i)).not.toBeInTheDocument();
  });
});
