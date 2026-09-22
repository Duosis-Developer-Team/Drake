/**
 * Proves the Protection area is wired to the catalogue: the list page,
 * rendered under a pinned Turkish locale, shows Turkish copy. Translation
 * quality is reviewed in the message file; this only checks the plumbing.
 */
import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ProtectionPage from "@/app/protection/page";
import { LocaleProvider } from "@/lib/i18n";
import { installFetchMock } from "@/test/mock-api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/protection",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({ policyId: "pol-1" }),
}));

const POLICY = {
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
  evaluation: {
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
  },
};

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

describe("protection center in Turkish", () => {
  it("renders the page, the row badges and the reason through the catalogue", async () => {
    installFetchMock({
      "/v1/protection/summary": { status: 200, body: SUMMARY },
      "/v1/protection/policies": {
        status: 200,
        body: { items: [POLICY], total: 1, limit: 25, offset: 0 },
      },
    });
    render(
      <LocaleProvider locale="tr">
        <ProtectionPage />
      </LocaleProvider>,
    );

    const row = await screen.findByTestId("protection-row-hermes-core");
    expect(screen.getByRole("heading", { name: "Koruma" })).toBeInTheDocument();
    expect(within(row).getByText("Korunuyor")).toBeInTheDocument();
    expect(within(row).getByText("Hiç doğrulanmadı")).toBeInTheDocument();
    expect(within(row).getByText("Geri yükleme hiç sınanmadı")).toBeInTheDocument();
    // The RPO window comes from the locale-aware formatter: "7d" → "7 g".
    expect(within(row).getByText("7 g")).toBeInTheDocument();
    // Filters and the shared "showing" count are Turkish too.
    expect(screen.getByRole("group", { name: "Filtreler" })).toBeInTheDocument();
    expect(screen.getByText("1 kayıttan 1 tanesi gösteriliyor")).toBeInTheDocument();
  });

  it("uses the Turkish empty state", async () => {
    installFetchMock({
      "/v1/protection/summary": { status: 200, body: SUMMARY },
      "/v1/protection/policies": {
        status: 200,
        body: { items: [], total: 0, limit: 25, offset: 0 },
      },
    });
    render(
      <LocaleProvider locale="tr">
        <ProtectionPage />
      </LocaleProvider>,
    );
    expect(await screen.findByText("Koruma politikası yok")).toBeInTheDocument();
  });
});
