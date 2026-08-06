import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GrantsPanel } from "@/components/admin/GrantsPanel";
import { SessionProvider } from "@/lib/session";
import { installFetchMock, makeMe } from "@/test/mock-api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/admin",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

const OPTIONS = {
  directory_scope: "subtree",
  scopes: [
    {
      id: "scope-a",
      scope_type: "project",
      scope_ref: "project-a",
      display_name: "Project A",
      delegable_role_ids: ["role-dev"],
    },
  ],
  roles: [
    { id: "role-dev", name: "Developer", permissions: ["project.view"] },
    { id: "role-analyst", name: "Analyst", permissions: ["tenant.usage.export"] },
  ],
  identities: [{ id: "ident-1", display_name: "Plain User" }],
  group_mappings: [{ id: "gm-1", display_name: "Mapped Group" }],
};

function renderPanel(extraRoutes: Record<string, { status: number; body: unknown }> = {}) {
  const calls = installFetchMock({
    "/v1/me": {
      status: 200,
      body: makeMe({ permissions: ["rbac.manage", "audit.view"] }),
    },
    "/v1/grants": { status: 200, body: { grants: [] } },
    "/v1/grant-options": { status: 200, body: OPTIONS },
    ...extraRoutes,
  });
  render(
    <SessionProvider>
      <GrantsPanel />
    </SessionProvider>,
  );
  return calls;
}

describe("grant create form", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders scoped options and filters roles to delegable ones", async () => {
    renderPanel();
    await waitFor(() =>
      expect(screen.getByTestId("grant-create-form")).toBeInTheDocument(),
    );
    // Scope from options, honest subtree note shown:
    expect(screen.getByText("project/project-a")).toBeInTheDocument();
    expect(screen.getByText(/directory integration/i)).toBeInTheDocument();
    // Role select offers only delegable roles:
    const roleSelect = screen.getByLabelText("Role");
    expect(roleSelect).toHaveTextContent("Developer");
    expect(roleSelect).not.toHaveTextContent("Analyst");
  });

  it("submits the selected principal/role/scope with CSRF and idempotency headers", async () => {
    const calls = renderPanel({
      "/v1/grants-post": { status: 201, body: { id: "g-1" } },
    });
    await waitFor(() =>
      expect(screen.getByTestId("grant-create-form")).toBeInTheDocument(),
    );

    fireEvent.change(screen.getByLabelText("Identity", { selector: "select" }), { target: { value: "ident-1" } });
    fireEvent.change(screen.getByLabelText("Role"), { target: { value: "role-dev" } });
    fireEvent.click(screen.getByRole("button", { name: /create grant/i }));

    await waitFor(() => {
      const post = calls.find(
        (call) => call.path === "/v1/grants" && call.init?.method === "POST",
      );
      expect(post).toBeDefined();
    });
    const post = calls.find(
      (call) => call.path === "/v1/grants" && call.init?.method === "POST",
    );
    const headers = post?.init?.headers as Record<string, string>;
    expect(headers["X-CSRF-Token"]).toBe("csrf-test-token");
    expect(headers["Idempotency-Key"]).toBeTruthy();
    const body = JSON.parse(String(post?.init?.body));
    expect(body).toMatchObject({
      role_id: "role-dev",
      scope_id: "scope-a",
      identity_id: "ident-1",
      group_mapping_id: null,
    });
  });

  it("guards against double submit: one in-flight request only", async () => {
    const calls = renderPanel();
    await waitFor(() =>
      expect(screen.getByTestId("grant-create-form")).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByLabelText("Identity", { selector: "select" }), { target: { value: "ident-1" } });
    fireEvent.change(screen.getByLabelText("Role"), { target: { value: "role-dev" } });

    const button = screen.getByRole("button", { name: /create grant/i });
    fireEvent.click(button);
    fireEvent.click(button);
    fireEvent.click(button);

    await waitFor(() => {
      const posts = calls.filter(
        (call) => call.path === "/v1/grants" && call.init?.method === "POST",
      );
      expect(posts).toHaveLength(1);
    });
  });

  it("rejects an invalid validity interval client-side without calling the API", async () => {
    const calls = renderPanel();
    await waitFor(() =>
      expect(screen.getByTestId("grant-create-form")).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByLabelText("Identity", { selector: "select" }), { target: { value: "ident-1" } });
    fireEvent.change(screen.getByLabelText("Role"), { target: { value: "role-dev" } });
    fireEvent.change(screen.getByLabelText(/valid from/i), {
      target: { value: "2026-08-06T12:00" },
    });
    fireEvent.change(screen.getByLabelText(/valid to/i), {
      target: { value: "2026-08-06T11:00" },
    });
    fireEvent.click(screen.getByRole("button", { name: /create grant/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/after valid-from/i);
    const posts = calls.filter(
      (call) => call.path === "/v1/grants" && call.init?.method === "POST",
    );
    expect(posts).toHaveLength(0);
  });

  it("switching principal type swaps the candidate list", async () => {
    renderPanel();
    await waitFor(() =>
      expect(screen.getByTestId("grant-create-form")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByLabelText(/mapped group/i));
    const select = screen.getByLabelText("Group mapping");
    expect(select).toHaveTextContent("Mapped Group");
  });

  it("shows a typed error state when options cannot load", async () => {
    installFetchMock({
      "/v1/me": {
        status: 200,
        body: makeMe({ permissions: ["rbac.manage"] }),
      },
      "/v1/grants": { status: 200, body: { grants: [] } },
      "/v1/grant-options": {
        status: 503,
        body: { error: { code: "dependency_unavailable", message: "unavailable" } },
      },
    });
    render(
      <SessionProvider>
        <GrantsPanel />
      </SessionProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("state-error")).toBeInTheDocument());
  });
});
