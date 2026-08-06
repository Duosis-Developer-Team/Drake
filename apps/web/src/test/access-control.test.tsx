import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import AccessControlPage from "@/app/admin/page";
import { SessionProvider } from "@/lib/session";
import { installFetchMock, makeMe } from "@/test/mock-api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/admin",
}));

function renderPage() {
  return render(
    <SessionProvider>
      <AccessControlPage />
    </SessionProvider>,
  );
}

describe("Access Control page", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows permission-denied without rbac.manage or audit.view", async () => {
    installFetchMock({ "/v1/me": { status: 200, body: makeMe() } });
    renderPage();
    await waitFor(() =>
      expect(screen.getByTestId("state-permission-denied")).toBeInTheDocument(),
    );
  });

  it("renders roles and the permission matrix for rbac.manage", async () => {
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
    expect(screen.getByText("Platform Owner")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Audit" })).toBeInTheDocument();

    screen.getByText("Platform Owner").click();
    await waitFor(() =>
      expect(screen.getByTestId("permission-matrix")).toBeInTheDocument(),
    );
    expect(screen.getByText(/system templates are immutable/i)).toBeInTheDocument();
  });

  it("shows only the audit tab for audit.view-only users", async () => {
    installFetchMock({
      "/v1/me": { status: 200, body: makeMe({ permissions: ["audit.view"] }) },
      "/v1/audit-events": {
        status: 200,
        body: {
          events: [
            {
              id: "e1",
              occurred_at: "2026-08-06T10:00:00+00:00",
              actor_type: "user",
              actor_id: "a",
              action: "auth.login",
              scope_type: null,
              scope_ref: null,
              target_type: null,
              target_id: null,
              result: "success",
              correlation_id: "c",
            },
          ],
          next_cursor: null,
        },
      },
    });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("audit-table")).toBeInTheDocument());
    expect(screen.queryByRole("tab", { name: "Roles" })).not.toBeInTheDocument();
    expect(screen.getByText("auth.login")).toBeInTheDocument();
  });
});
