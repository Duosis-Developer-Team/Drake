import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ProjectsPage from "@/app/projects/page";
import { LocaleProvider } from "@/lib/i18n";
import { installFetchMock } from "@/test/mock-api";

/**
 * The catalog area in Turkish.
 *
 * One test, as the i18n README asks: it proves the projects list is wired
 * through the `catalog` namespace, not that every string is translated —
 * the catalogue's completeness is typechecked, and `i18n.test.tsx` refuses a
 * Turkish leaf left in English. The English screens keep their own tests in
 * `catalog-screens.test.tsx`, unchanged.
 */

vi.mock("next/navigation", () => ({
  usePathname: () => "/projects",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({ projectId: "p1" }),
}));

const PROJECT = {
  id: "p1",
  project_key: "alpha",
  display_name: "Alpha",
  lifecycle: "active",
  criticality: "high",
  tenant_model: "none",
  repository: { provider: "github", owner: "example-org", name: "alpha", default_branch: "dev" },
  version: 1,
  scope: { type: "project", ref: "alpha" },
  source: { kind: "fixture", ref: "fixture:alpha", revision: "v1", accepted_at: "2026-08-06T00:00:00Z" },
  counts: { environments: 2, services: 3 },
  as_of: "2026-08-06T00:00:00Z",
};

describe("catalog screens in Turkish", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders the projects list through the catalog namespace", async () => {
    installFetchMock({
      "/v1/projects": {
        status: 200,
        body: { projects: [PROJECT], next_cursor: "next", as_of: "now" },
      },
      "/v1/service-health/services": {
        status: 200,
        body: { items: [], total: 0, limit: 100, offset: 0 },
      },
    });
    render(
      <LocaleProvider locale="tr">
        <ProjectsPage />
      </LocaleProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("project-list")).toBeInTheDocument());

    // Page chrome: the title and the search field's accessible name.
    expect(screen.getByRole("heading", { level: 1, name: "Projeler" })).toBeInTheDocument();
    expect(screen.getByLabelText("Projeleri anahtara veya ada göre ara")).toBeInTheDocument();

    // A row: the recorded criticality chip and the "nothing observed yet"
    // evidence state, both from the catalogue's enum groups.
    const row = within(screen.getByTestId("project-list")).getByRole("row", { name: /Alpha/ });
    expect(within(row).getByText("Kritiklik: Yüksek")).toBeInTheDocument();
    await waitFor(() => expect(within(row).getByText("Değerlendirilmedi")).toBeInTheDocument());

    // A partial collection: the header badge and the load-more action.
    expect(screen.getByText("Kısmi görünüm")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Daha fazla proje yükle" })).toBeInTheDocument();

    // Nothing English leaked through on the parts this area owns.
    expect(screen.queryByText("High criticality")).not.toBeInTheDocument();
    expect(screen.queryByText("Load more projects")).not.toBeInTheDocument();
  });
});
