import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/shell/AppShell";
import { NAVIGATION, NAV_ITEMS } from "@/lib/navigation";
import { installFetchMock, makeMe } from "@/test/mock-api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

/** Everything a session would need to see the whole menu. */
const ALL_PERMISSIONS = [
  ...new Set(NAV_ITEMS.flatMap((item) => item.anyPermission ?? [])),
];

async function renderAuthenticated(permissions: string[] = ALL_PERMISSIONS) {
  installFetchMock({ "/v1/me": { status: 200, body: makeMe({ permissions }) } });
  render(
    <AppShell>
      <p>shell-content</p>
    </AppShell>,
  );
  await waitFor(() => expect(screen.getByText("shell-content")).toBeInTheDocument());
}

describe("AppShell (authenticated)", () => {
  beforeEach(() => {
    document.documentElement.classList.remove("dark");
    localStorage.clear();
  });

  afterEach(() => vi.unstubAllGlobals());

  it("renders every navigation entry, grouped", async () => {
    await renderAuthenticated();
    const nav = screen.getByRole("navigation", { name: "Primary" });
    for (const group of NAVIGATION) {
      expect(within(nav).getByText(group.label)).toBeInTheDocument();
      for (const item of group.items) {
        expect(within(nav).getByRole("link", { name: item.label })).toBeInTheDocument();
      }
    }
  });

  it("every navigation entry points at a route that exists", async () => {
    // The regression this guards: a permanently disabled "coming soon" entry.
    // A menu that lists destinations you cannot reach stops being trusted, so
    // there is no disabled state in the rail at all.
    await renderAuthenticated();
    const nav = screen.getByRole("navigation", { name: "Primary" });
    const links = within(nav).getAllByRole("link");
    expect(links).toHaveLength(NAV_ITEMS.length);
    for (const link of links) {
      expect(link).toHaveAttribute("href");
      expect(link.getAttribute("aria-disabled")).toBeNull();
    }
    expect(within(nav).queryByText(/soon/i)).not.toBeInTheDocument();
  });

  it("marks the active entry by more than colour", async () => {
    await renderAuthenticated();
    const active = screen.getByRole("link", { name: /command center/i });
    expect(active).toHaveAttribute("aria-current", "page");
    // Weight and surface change too, so the state survives greyscale.
    expect(active.className).toMatch(/font-semibold/);
    expect(active.className).toMatch(/bg-surface-selected/);
  });

  it("hides entries whose permissions the session does not hold", async () => {
    await renderAuthenticated([]);
    const nav = screen.getByRole("navigation", { name: "Primary" });
    // Command Center is ungated; everything else in the rail is not.
    expect(within(nav).getAllByRole("link")).toHaveLength(1);
    expect(within(nav).getByRole("link", { name: /command center/i })).toBeInTheDocument();
  });

  it("theme control offers system, light and dark, and persists a choice", async () => {
    await renderAuthenticated();
    const dark = screen.getAllByRole("radio", { name: /dark/i })[0];
    const light = screen.getAllByRole("radio", { name: /light/i })[0];
    const system = screen.getAllByRole("radio", { name: /system/i })[0];
    expect(system).toBeInTheDocument();

    dark.click();
    await waitFor(() =>
      expect(document.documentElement.classList.contains("dark")).toBe(true),
    );
    expect(localStorage.getItem("drake-theme")).toBe("dark");

    light.click();
    await waitFor(() =>
      expect(document.documentElement.classList.contains("dark")).toBe(false),
    );
    expect(localStorage.getItem("drake-theme")).toBe("light");
  });

  it("returning to system clears the stored override", async () => {
    // "System" has to be a real state, not the absence of one: a stored
    // "light" that survives a switch back to system means the app stops
    // following the OS forever.
    await renderAuthenticated();
    screen.getAllByRole("radio", { name: /dark/i })[0].click();
    await waitFor(() => expect(localStorage.getItem("drake-theme")).toBe("dark"));
    screen.getAllByRole("radio", { name: /system/i })[0].click();
    await waitFor(() => expect(localStorage.getItem("drake-theme")).toBeNull());
  });

  it("shows no time-range control off telemetry screens while search is real", async () => {
    await renderAuthenticated();
    // The time-range control is real but appears ONLY on telemetry screens
    // (project overview / service detail) — never as a misleading control on
    // the Command Center:
    expect(screen.queryByRole("group", { name: /time range/i })).not.toBeInTheDocument();
    // Catalog search is a real, enabled control:
    const searchButtons = screen.getAllByRole("button", { name: /search catalog/i });
    expect(searchButtons.length).toBeGreaterThan(0);
    expect(searchButtons[0]).toBeEnabled();
  });

  it("offers a skip link before the navigation", async () => {
    await renderAuthenticated();
    const skip = screen.getByRole("link", { name: /skip to content/i });
    expect(skip).toHaveAttribute("href", "#main");
  });
});
