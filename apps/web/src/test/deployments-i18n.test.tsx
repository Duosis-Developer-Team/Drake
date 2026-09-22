/**
 * Proves the deployments area is wired to the catalogue: the list renders
 * in Turkish under a pinned `LocaleProvider`. The English screen tests
 * keep asserting English without a provider; this one asserts a few
 * Turkish strings, not the translation as a whole.
 */
import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import DeploymentsPage from "@/app/deployments/page";
import { LocaleProvider } from "@/lib/i18n";
import { installFetchMock } from "@/test/mock-api";

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
      { name: "api", image: `ghcr.io/acme/api@${DIGEST}`, digest: DIGEST, short_digest: "a".repeat(12) },
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

describe("deployments in Turkish", () => {
  it("renders the list, the row badges and the empty ref in Turkish", async () => {
    installFetchMock({
      "/v1/deployments": {
        status: 200,
        body: {
          items: [deployment({ short_commit: null })],
          next_cursor: null,
          total: 1,
          limit: 25,
        },
      },
    });
    render(
      <LocaleProvider locale="tr">
        <DeploymentsPage />
      </LocaleProvider>,
    );

    const row = await screen.findByTestId("deployment-row-pilot-api");
    expect(screen.getByRole("heading", { name: "Dağıtımlar" })).toBeInTheDocument();
    // Rollout and evidence badges come from the catalogue, not the English records.
    expect(within(row).getByText("Sağlıklı")).toBeInTheDocument();
    expect(within(row).getByText("Doğrulandı")).toBeInTheDocument();
    expect(within(row).getByText("Kararlı")).toBeInTheDocument();
    // A missing ref is a whole sentence in one key, not "no" + noun.
    expect(within(row).getByText("commit SHA'sı yok")).toBeInTheDocument();
    expect(screen.getByText("Rollout'lar")).toBeInTheDocument();
    expect(
      screen.getByText("Yetkili olduğunuz kapsamdaki 1 dağıtımdan 1 tanesi gösteriliyor."),
    ).toBeInTheDocument();
  });

  it("names the empty state in Turkish", async () => {
    installFetchMock({
      "/v1/deployments": {
        status: 200,
        body: { items: [], next_cursor: null, total: 0, limit: 25 },
      },
    });
    render(
      <LocaleProvider locale="tr">
        <DeploymentsPage />
      </LocaleProvider>,
    );
    expect(await screen.findByText("Dağıtım yok")).toBeInTheDocument();
    expect(screen.getByText("Rollout durumu · Tümü")).toBeInTheDocument();
  });
});
