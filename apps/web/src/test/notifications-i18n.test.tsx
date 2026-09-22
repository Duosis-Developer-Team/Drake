/**
 * Proves the notifications area is wired to the catalogue: the inbox
 * renders in Turkish under a pinned `LocaleProvider`. The English screen
 * tests keep asserting English without a provider; this one asserts a few
 * Turkish strings, not the translation as a whole.
 */
import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import NotificationsPage from "@/app/notifications/page";
import { LocaleProvider } from "@/lib/i18n";
import { SessionProvider } from "@/lib/session";
import { installFetchMock, makeMe } from "@/test/mock-api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/notifications",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

function inboxItem(overrides: Record<string, unknown> = {}) {
  return {
    id: "n1",
    event_type: "opened",
    title: "Incident opened: api (dev): No replicas ready",
    body: "pilot/dev/api — No replicas ready. Drake opened an incident.",
    target_path: "/incidents/inc-1",
    metadata: { severity: "critical", primary_reason: "no_ready_replicas" },
    created_at: "2026-08-08T12:00:00Z",
    read_at: null,
    incident_id: "inc-1",
    ...overrides,
  };
}

function renderInbox(items: unknown[]) {
  installFetchMock({
    "/v1/me": { status: 200, body: makeMe() },
    "/v1/notifications": { status: 200, body: { items, next_cursor: null, limit: 25 } },
  });
  render(
    <LocaleProvider locale="tr">
      <SessionProvider>
        <NotificationsPage />
      </SessionProvider>
    </LocaleProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("notifications in Turkish", () => {
  it("renders the inbox chrome and row actions in Turkish", async () => {
    renderInbox([inboxItem()]);
    const row = await screen.findByTestId("notification-n1");
    expect(screen.getByRole("heading", { name: "Bildirimler" })).toBeInTheDocument();
    // Server-composed title and body are data and stay as sent.
    expect(
      within(row).getByText("Incident opened: api (dev): No replicas ready"),
    ).toBeInTheDocument();
    // The event chip is the catalogue's label, not the English record.
    expect(within(row).getByText("Olay açıldı")).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Okundu işaretle" })).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "Olayı aç" })).toHaveAttribute(
      "href",
      "/incidents/inc-1",
    );
    expect(screen.getByText("Gelen kutusu")).toBeInTheDocument();
    expect(screen.getByText("1 gösteriliyor · 1 okunmamış")).toBeInTheDocument();
  });

  it("names the empty inbox in Turkish", async () => {
    renderInbox([]);
    expect(await screen.findByText("Bildirim yok")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Yönlendirme politikalarını incele" }),
    ).toBeInTheDocument();
  });
});
