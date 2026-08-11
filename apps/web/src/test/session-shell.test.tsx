import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/shell/AppShell";
import { errorBody, installFetchMock, makeMe } from "@/test/mock-api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

describe("session-aware shell", () => {
  beforeEach(() => {
    document.documentElement.classList.remove("dark");
    localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows the signed-out screen with a sign-in action on 401", async () => {
    installFetchMock({
      "/v1/me": { status: 401, body: errorBody("unauthorized", "authentication required") },
    });
    render(
      <AppShell>
        <p>secret-content</p>
      </AppShell>,
    );
    await waitFor(() =>
      expect(screen.getByTestId("screen-signed-out")).toBeInTheDocument(),
    );
    const signIn = screen.getByRole("link", { name: /sign in/i });
    expect(signIn).toHaveAttribute("href", expect.stringContaining("/v1/auth/login"));
    expect(screen.queryByText("secret-content")).not.toBeInTheDocument();
  });

  it("shows the provider-unavailable screen on 503 without faking sign-out", async () => {
    installFetchMock({
      "/v1/me": { status: 503, body: errorBody("dependency_unavailable", "unavailable") },
    });
    render(
      <AppShell>
        <p>content</p>
      </AppShell>,
    );
    await waitFor(() =>
      expect(screen.getByTestId("screen-unavailable")).toBeInTheDocument(),
    );
    expect(screen.getByText(/dependency outage/i)).toBeInTheDocument();
  });

  it("renders the authenticated shell with identity and children", async () => {
    installFetchMock({ "/v1/me": { status: 200, body: makeMe() } });
    render(
      <AppShell>
        <p>dashboard-content</p>
      </AppShell>,
    );
    await waitFor(() => expect(screen.getByText("dashboard-content")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /account menu/i })).toBeInTheDocument();
  });

  it("gates the Access Control navigation on permissions", async () => {
    installFetchMock({
      "/v1/me": { status: 200, body: makeMe({ permissions: ["rbac.manage"] }) },
    });
    render(
      <AppShell>
        <p>content</p>
      </AppShell>,
    );
    await waitFor(() => expect(screen.getByText("content")).toBeInTheDocument());
    expect(
      screen.getByRole("link", { name: /audit & access/i }),
    ).toBeInTheDocument();
  });

  it("keeps Access Control locked without permissions", async () => {
    installFetchMock({ "/v1/me": { status: 200, body: makeMe() } });
    render(
      <AppShell>
        <p>content</p>
      </AppShell>,
    );
    await waitFor(() => expect(screen.getByText("content")).toBeInTheDocument());
    expect(
      screen.queryByRole("link", { name: /audit & access/i }),
    ).not.toBeInTheDocument();
  });

  it("signs out via POST with the CSRF token and never touches storage", async () => {
    const calls = installFetchMock({
      "/v1/me": { status: 200, body: makeMe() },
      "/v1/auth/logout": { status: 200, body: { status: "signed_out" } },
    });
    render(
      <AppShell>
        <p>content</p>
      </AppShell>,
    );
    await waitFor(() => expect(screen.getByText("content")).toBeInTheDocument());

    screen.getByRole("button", { name: /account menu/i }).click();
    (await screen.findByRole("menuitem", { name: /sign out/i })).click();

    await waitFor(() => expect(screen.getByTestId("screen-signed-out")).toBeInTheDocument());
    const logoutCall = calls.find((call) => call.path.includes("/v1/auth/logout"));
    expect(logoutCall).toBeDefined();
    expect(logoutCall?.init?.method).toBe("POST");
    expect(
      (logoutCall?.init?.headers as Record<string, string>)["X-CSRF-Token"],
    ).toBe("csrf-test-token");
    // No tokens in browser storage — the cookie (HttpOnly) is the only artifact.
    expect(Object.keys(localStorage)).toEqual([]);
    expect(Object.keys(sessionStorage)).toEqual([]);
  });
});
