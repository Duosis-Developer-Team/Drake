import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/shell/AppShell";
import { NAVIGATION } from "@/lib/navigation";
import { installFetchMock, makeMe } from "@/test/mock-api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

async function renderAuthenticated(permissions: string[] = ["rbac.manage"]) {
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

  it("renders all ten primary navigation sections", async () => {
    await renderAuthenticated();
    const nav = screen.getByRole("navigation", { name: "Primary" });
    for (const item of NAVIGATION) {
      expect(within(nav).getByText(item.label)).toBeInTheDocument();
    }
    expect(NAVIGATION).toHaveLength(10);
  });

  it("marks the active section and labels future sections honestly", async () => {
    await renderAuthenticated();
    const active = screen.getByRole("link", { name: /command center/i });
    expect(active).toHaveAttribute("aria-current", "page");
    const nav = screen.getByRole("navigation", { name: "Primary" });
    // 10 items; Command Center + Access Control unlocked → 8 remain "soon".
    expect(within(nav).getAllByText("soon").length).toBe(8);
  });

  it("theme toggle switches the dark class and persists the choice", async () => {
    await renderAuthenticated();
    const toggle = await screen.findByRole("button", { name: /switch to dark theme/i });
    toggle.click();
    await waitFor(() =>
      expect(document.documentElement.classList.contains("dark")).toBe(true),
    );
    expect(localStorage.getItem("drake-theme")).toBe("dark");
  });

  it("keeps unfinished controls disabled while search is real", async () => {
    await renderAuthenticated();
    // Time range is still honestly disabled (telemetry sprint):
    expect(screen.getByTitle(/time range control arrives/i)).toBeDisabled();
    // Catalog search is now a real, enabled control:
    const searchButtons = screen.getAllByRole("button", { name: /search catalog/i });
    expect(searchButtons.length).toBeGreaterThan(0);
    expect(searchButtons[0]).toBeEnabled();
  });
});
