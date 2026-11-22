import { describe, expect, it } from "vitest";

import { buildCrumbs } from "./Breadcrumbs";

describe("buildCrumbs", () => {
  const names: Record<string, string> = { p1: "hermes", e1: "test", s1: "auth-service" };
  const labelFor = (id: string) => names[id];

  it("links every ancestor that is a page, entity pages included", () => {
    const crumbs = buildCrumbs("/projects/p1/environments/e1/services/s1", labelFor);
    expect(crumbs.map((c) => [c.label, c.href])).toEqual([
      ["Projects", "/projects"],
      ["hermes", "/projects/p1"],
      // No page of its own: it opens the project, which lists them.
      ["Environments", "/projects/p1"],
      ["test", "/projects/p1/environments/e1"],
      ["Services", "/projects/p1/environments/e1"],
      ["auth-service", null],
    ]);
  });

  it("keeps the current page unlinked", () => {
    const crumbs = buildCrumbs("/projects/p1/environments/e1", labelFor);
    expect(crumbs.at(-1)).toMatchObject({ label: "test", href: null });
    expect(crumbs[1]).toMatchObject({ label: "hermes", href: "/projects/p1" });
  });

  it("links a cluster from its inventory", () => {
    const crumbs = buildCrumbs("/clusters/c1/inventory", () => undefined);
    expect(crumbs.map((c) => c.href)).toEqual(["/clusters", "/clusters/c1", null]);
  });
});
