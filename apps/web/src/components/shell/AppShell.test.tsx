import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/shell/AppShell";
import { NAVIGATION } from "@/lib/navigation";

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
}));

describe("AppShell", () => {
  beforeEach(() => {
    document.documentElement.classList.remove("dark");
    localStorage.clear();
  });

  it("renders all ten primary navigation sections", () => {
    render(
      <AppShell>
        <p>content</p>
      </AppShell>,
    );
    const nav = screen.getByRole("navigation", { name: "Primary" });
    for (const item of NAVIGATION) {
      expect(within(nav).getByText(item.label)).toBeInTheDocument();
    }
    expect(NAVIGATION).toHaveLength(10);
  });

  it("marks the active section and disables future sections honestly", () => {
    render(
      <AppShell>
        <p>content</p>
      </AppShell>,
    );
    const active = screen.getByRole("link", { name: /command center/i });
    expect(active).toHaveAttribute("aria-current", "page");
    // Disabled entries are not links and are labeled as coming later.
    const nav = screen.getByRole("navigation", { name: "Primary" });
    expect(within(nav).getAllByText("soon").length).toBe(NAVIGATION.length - 1);
  });

  it("renders children in the main region", () => {
    render(
      <AppShell>
        <p>hello-drake</p>
      </AppShell>,
    );
    expect(within(screen.getByRole("main")).getByText("hello-drake")).toBeInTheDocument();
  });

  it("theme toggle switches the dark class and persists the choice", async () => {
    render(
      <AppShell>
        <p>content</p>
      </AppShell>,
    );
    const toggle = await screen.findByRole("button", { name: /switch to dark theme/i });
    toggle.click();
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(localStorage.getItem("drake-theme")).toBe("dark");
  });

  it("placeholder controls are disabled, not fake-functional", () => {
    render(
      <AppShell>
        <p>content</p>
      </AppShell>,
    );
    expect(screen.getByTitle(/time range control arrives/i)).toBeDisabled();
    expect(screen.getByText(/search — not configured/i)).toBeInTheDocument();
  });
});
