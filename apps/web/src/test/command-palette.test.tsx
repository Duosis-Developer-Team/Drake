/**
 * Command palette, phase 1 (2026-09-13 remake brief §8.1).
 *
 * Static page/action search merges with the existing authorized catalog
 * search. These tests cover what's new in that merge: page results, the
 * permission parity with the sidebar, and the recently-visited list. The
 * catalog-only behaviour (debounce, abort, keyboard nav, typed error) is
 * covered in `catalog-screens.test.tsx` and is unchanged here.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CatalogSearch } from "@/components/shell/CatalogSearch";
import { SessionProvider } from "@/lib/session";
import { installFetchMock, makeMe } from "@/test/mock-api";

const routerPush = vi.fn();
vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ push: routerPush, replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

function renderSearch(permissions: string[] = []) {
  installFetchMock({
    "/v1/me": { status: 200, body: makeMe({ permissions }) },
    "/v1/catalog/search": { status: 200, body: { results: [] } },
  });
  return render(
    <SessionProvider>
      <CatalogSearch />
    </SessionProvider>,
  );
}

async function open() {
  fireEvent.click(screen.getAllByRole("button", { name: /search drake/i })[0]);
  return screen.findByLabelText("Search query");
}

describe("command palette: page results", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    routerPush.mockClear();
    sessionStorage.clear();
  });

  it("surfaces a page result and navigates to it on Enter", async () => {
    renderSearch(["environment.view"]);
    const input = await open();
    fireEvent.change(input, { target: { value: "Incidents" } });

    const group = await screen.findByTestId("palette-group-pages");
    expect(group).toHaveTextContent("Incidents");

    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Enter" });
    expect(routerPush).toHaveBeenCalledWith("/incidents");
  });

  it("groups a page result separately from a catalog result", async () => {
    installFetchMock({
      "/v1/me": { status: 200, body: makeMe({ permissions: ["project.view"] }) },
      "/v1/catalog/search": {
        status: 200,
        body: {
          results: [
            {
              kind: "project",
              id: "p1",
              key: "projects-team",
              display_name: "Projects Team",
              project_key: "projects-team",
              parent_id: null,
              project_id: "p1",
            },
          ],
        },
      },
    });
    render(
      <SessionProvider>
        <CatalogSearch />
      </SessionProvider>,
    );
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const input = await open();
    fireEvent.change(input, { target: { value: "Projects" } });
    await vi.advanceTimersByTimeAsync(300);

    await waitFor(() => expect(screen.getByTestId("palette-group-catalog")).toBeInTheDocument());
    expect(screen.getByTestId("palette-group-pages")).toHaveTextContent("Projects");
    expect(screen.getByTestId("palette-group-catalog")).toHaveTextContent("Projects Team");
    vi.useRealTimers();
  });

  it("never offers a page the sidebar would also hide for this session", async () => {
    // No permissions at all: only the ungated Command Center entry survives.
    renderSearch([]);
    const input = await open();
    fireEvent.change(input, { target: { value: "Incidents" } });
    expect(screen.queryByTestId("palette-group-pages")).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/no authorized results/i)).toBeInTheDocument());

    fireEvent.change(input, { target: { value: "Command" } });
    expect(await screen.findByTestId("palette-group-pages")).toHaveTextContent("Command Center");
  });
});

describe("command palette: recently visited", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    routerPush.mockClear();
    sessionStorage.clear();
  });

  it("has no recent list before anything has been opened from the palette", async () => {
    renderSearch(["environment.view"]);
    await open();
    expect(screen.queryByTestId("palette-group-recent")).not.toBeInTheDocument();
    expect(screen.getByText(/type to search/i)).toBeInTheDocument();
  });

  it("records a page opened via the palette and shows it on reopen, capped at 5", async () => {
    renderSearch(["environment.view"]);
    const input = await open();
    fireEvent.change(input, { target: { value: "Incidents" } });
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Enter" });
    expect(routerPush).toHaveBeenCalledWith("/incidents");

    // Reopen: the dialog closed itself on navigate.
    const reopened = await open();
    expect(screen.getByTestId("palette-group-recent")).toHaveTextContent("Incidents");
    expect(reopened).toHaveValue("");
  });

  it("caps the recent list at 5 and keeps the newest first", async () => {
    sessionStorage.setItem(
      "drake-recent-pages",
      JSON.stringify([
        { href: "/a", label: "A" },
        { href: "/b", label: "B" },
        { href: "/c", label: "C" },
        { href: "/d", label: "D" },
        { href: "/e", label: "E" },
      ]),
    );
    renderSearch(["environment.view"]);
    const input = await open();
    fireEvent.change(input, { target: { value: "Incidents" } });
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Enter" });

    await open();
    const stored = JSON.parse(sessionStorage.getItem("drake-recent-pages") ?? "[]");
    expect(stored).toHaveLength(5);
    expect(stored[0]).toEqual({ href: "/incidents", label: "Incidents" });
    expect(stored.map((entry: { href: string }) => entry.href)).not.toContain("/e");
  });
});
