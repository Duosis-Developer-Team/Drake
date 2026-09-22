/**
 * Proves the Admin area is wired to the catalogue: the Audit & access page,
 * rendered under a pinned Turkish locale, shows Turkish copy in the header,
 * the tabs and the roles panel.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import AccessControlPage from "@/app/admin/page";
import { LocaleProvider } from "@/lib/i18n";
import { SessionProvider } from "@/lib/session";
import { installFetchMock, makeMe } from "@/test/mock-api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/admin",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

function renderPage() {
  return render(
    <LocaleProvider locale="tr">
      <SessionProvider>
        <AccessControlPage />
      </SessionProvider>
    </LocaleProvider>,
  );
}

describe("Audit & access page in Turkish", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders the header, tabs and the roles panel through the catalogue", async () => {
    installFetchMock({
      "/v1/me": {
        status: 200,
        body: makeMe({ permissions: ["rbac.manage", "audit.view"] }),
      },
      "/v1/roles": {
        status: 200,
        body: {
          roles: [
            {
              id: "r1",
              name: "Platform Owner",
              description: "",
              is_system: true,
              status: "active",
              version: 1,
              permissions: ["rbac.manage"],
              etag: 'W/"role-1"',
            },
          ],
        },
      },
      "/v1/permissions": {
        status: 200,
        body: {
          permissions: [
            { key: "rbac.manage", description: "Manage roles", catalog_version: 1 },
          ],
        },
      },
    });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("role-list")).toBeInTheDocument());

    expect(screen.getByRole("heading", { name: "Denetim ve erişim" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Roller" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Atamalar" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Denetim" })).toBeInTheDocument();
    expect(screen.getByText("1 yetki")).toBeInTheDocument();
    expect(screen.getByText("Rol seçilmedi")).toBeInTheDocument();

    screen.getByText("Platform Owner").click();
    await waitFor(() =>
      expect(screen.getByTestId("permission-matrix")).toBeInTheDocument(),
    );
    expect(screen.getByText(/Sistem şablonları değiştirilemez/)).toBeInTheDocument();
  });

  it("shows the Turkish permission-denied copy without rbac.manage or audit.view", async () => {
    installFetchMock({ "/v1/me": { status: 200, body: makeMe() } });
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("state-permission-denied")).toBeInTheDocument(),
    );
    expect(
      screen.getByText(/Erişimi yönetmek için rbac\.manage/),
    ).toBeInTheDocument();
  });
});
