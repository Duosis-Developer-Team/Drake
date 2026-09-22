/**
 * Proves the alerting area is wired to the catalogue: the alert list renders
 * in Turkish under a pinned `LocaleProvider`. The English tests in
 * alerting-screens.test.tsx run without a provider and are untouched.
 */
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import AlertsPage from "@/app/alerts/page";
import { LocaleProvider } from "@/lib/i18n";
import { installFetchMock } from "@/test/mock-api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/alerts",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({ alertId: "al-1" }),
}));

const ALERT = {
  id: "al-1",
  fingerprint_prefix: "a1b2c3d4e5f6",
  alert_name: "HighErrorRate",
  status: "firing",
  severity: "critical",
  priority: "P1",
  mapping_state: "unmapped",
  mapping_error_code: "service_unknown",
  owner_team: "platform",
  slo_key: null,
  runbook_key: null,
  starts_at: "2026-08-08T11:00:00Z",
  ends_at: null,
  last_seen_at: "2026-08-08T11:55:00Z",
  source_event_at: "2026-08-08T11:00:00Z",
  ingested_at: "2026-08-08T11:56:00Z",
  resolved_at: null,
  labels: { alertname: "HighErrorRate" },
  annotations: {},
  occurrence: 1,
  silenced: false,
  inhibited: false,
  namespace: null,
  version: 2,
  project_key: null,
  environment_key: null,
  service_key: null,
  cluster_ref: null,
  incident: null,
};

afterEach(() => vi.unstubAllGlobals());

describe("alert list in Turkish", () => {
  it("renders the heading, row facts, summary and mapping explanation from the catalogue", async () => {
    installFetchMock({
      "/v1/alerts/summary": {
        status: 200,
        body: { firing: 3, p1: 1, p2: 1, silenced: 1, unmapped: 2, with_incident: 2 },
      },
      "/v1/alerts": {
        status: 200,
        body: { items: [ALERT], total: 1, limit: 25, offset: 0 },
      },
    });
    render(
      <LocaleProvider locale="tr">
        <AlertsPage />
      </LocaleProvider>,
    );

    const row = await screen.findByTestId("alert-row-HighErrorRate");
    expect(row).toHaveTextContent("Tetiklendi");
    expect(row).toHaveTextContent("olay yok");
    expect(row).toHaveTextContent("bildiriyor");
    expect(row).toHaveTextContent("katalog eşleşmesi yok");
    expect(screen.getByText("Tetiklenmiş uyarılar")).toBeInTheDocument();
    expect(screen.getByTestId("unmapped-note")).toHaveTextContent(
      "Servis etiketi bu projedeki hiçbir servisle eşleşmiyor.",
    );
    expect(screen.getByTestId("alert-summary")).toHaveTextContent("Olayı olan");
    expect(screen.getByLabelText("Durum")).toBeInTheDocument();
  });
});
