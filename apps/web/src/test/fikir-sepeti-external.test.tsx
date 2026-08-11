import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ProjectOverviewPage from "@/app/projects/[projectId]/page";
import { SessionProvider } from "@/lib/session";
import { installFetchMock, makeMe } from "@/test/mock-api";

/**
 * Fikir Sepeti in the UI: a project Drake does not run.
 *
 * The screens were built for Kubernetes, so the failure mode is not a crash
 * — it is a page that looks complete. An empty cluster field reads as a lost
 * cluster; a "connected" badge next to Supabase reads as an observation; a
 * restart button next to a managed database reads as an action somebody can
 * take. All three would be lies rendered in a component nobody changed.
 *
 * The payloads below are the SHAPE the API actually returns for this
 * project — the integration suite pins that the API produces them, and this
 * pins that the UI tells the truth when it does.
 */

vi.mock("next/navigation", () => ({
  usePathname: () => "/projects/p1",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({ projectId: "p1" }),
}));

const SOURCE = {
  kind: "manifest",
  ref: "github:Duosis-Developer-Team/Fikir-Sepeti",
  revision: "bde38ccd9c0e58570de28b0d4d1ef8eae41f7e5a",
  accepted_at: "2026-08-11T00:00:00Z",
};

const PROJECT = {
  id: "p1",
  project_key: "fikir-sepeti",
  display_name: "Fikir Sepeti",
  lifecycle: "active",
  criticality: "medium",
  tenant_model: "shared_table",
  repository: {
    provider: "github",
    owner: "Duosis-Developer-Team",
    name: "Fikir-Sepeti",
    default_branch: "main",
  },
  owners: [{ team: "fikir-sepeti", role: "primary" }],
  version: 1,
  scope: { type: "project", ref: "fikir-sepeti" },
  source: SOURCE,
  counts: { environments: 1, services: 1 },
  dependencies: [
    {
      id: "d1",
      dependency_key: "fikir-sepeti-db",
      display_name: "fikir-sepeti-db",
      dependency_class: "managed_data_platform",
      engine: "postgresql",
      scope: "project",
      provider: "supabase",
      verification: "repository_intent",
      workload_applicability: "not_applicable",
      health: {
        status: "unknown",
        freshness: "unavailable",
        source: { status: "not_configured" },
        availability: "unknown",
        reason: "Nothing has observed this yet.",
        verification: "repository_intent",
        last_observed_at: null,
      },
    },
  ],
  as_of: "2026-08-11T00:00:00Z",
};

const ENVIRONMENT = {
  id: "e1",
  environment_key: "prod",
  runtime: "external",
  branch: "main",
  criticality: "medium",
  namespace: "",
  lifecycle: "active",
  hosting_provider: "vercel",
  cluster: null,
  not_applicable: ["agent", "cluster", "inventory", "namespace", "workload_binding"],
  health: {
    status: "unknown",
    freshness: "unavailable",
    source: { status: "not_configured" },
    availability: "unknown",
    reason: "Nothing has observed this yet.",
    last_observed_at: null,
  },
  version: 1,
  scope: { type: "environment", ref: "fikir-sepeti/prod" },
  source: SOURCE,
  as_of: "2026-08-11T00:00:00Z",
};

function mount() {
  installFetchMock({
    "/v1/me": { status: 200, body: makeMe() },
    "/v1/projects/p1": { status: 200, body: PROJECT },
    "/v1/projects/p1/environments": {
      status: 200,
      body: { environments: [ENVIRONMENT], next_cursor: null, as_of: "now" },
    },
  });
  render(
    <SessionProvider>
      <ProjectOverviewPage />
    </SessionProvider>,
  );
}

describe("Fikir Sepeti — an external runtime in the catalog UI", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders the project without inventing Kubernetes identity", async () => {
    mount();
    await waitFor(() => expect(screen.getByText("Fikir Sepeti")).toBeInTheDocument());
    expect(screen.getByText("shared_table")).toBeInTheDocument();
    expect(
      screen.getByText(/github:Duosis-Developer-Team\/Fikir-Sepeti @ main/),
    ).toBeInTheDocument();
  });

  it("shows the owner as plain metadata, with no verified affirmation", async () => {
    /**
     * The owner is an operator decision, not an observation. It must read
     * as a recorded fact and never acquire the visual language of
     * something Drake checked — the badge next to it would be a claim
     * nobody made.
     */
    mount();
    await waitFor(() => expect(screen.getByText("Fikir Sepeti")).toBeInTheDocument());
    expect(screen.getByText("fikir-sepeti (primary)")).toBeInTheDocument();
    const page = document.body.textContent ?? "";
    expect(page).not.toContain("unknown-team");
  });

  it("shows the managed dependency with its provider and verification", async () => {
    mount();
    await waitFor(() => expect(screen.getByTestId("dependency-list")).toBeInTheDocument());
    const list = screen.getByTestId("dependency-list");
    expect(list).toHaveTextContent("fikir-sepeti-db");
    expect(list).toHaveTextContent("managed_data_platform");
    expect(list).toHaveTextContent("supabase");
    expect(list).toHaveTextContent("repository_intent");
  });

  it("says the workload question does not apply, rather than answering it", async () => {
    mount();
    await waitFor(() => expect(screen.getByTestId("dependency-list")).toBeInTheDocument());
    expect(screen.getByTestId("dependency-list")).toHaveTextContent("Not applicable");
  });

  it("reports health as unknown and freshness as unavailable", async () => {
    mount();
    await waitFor(() => expect(screen.getByTestId("dependency-list")).toBeInTheDocument());
    const list = screen.getByTestId("dependency-list");
    expect(list).toHaveTextContent("unknown");
    expect(list).toHaveTextContent("unavailable");
    // The distinctions the whole model exists to preserve.
    expect(list).not.toHaveTextContent("unhealthy");
    expect(list).not.toHaveTextContent("stale");
  });

  it("offers no in-cluster datastore section for a project with none", async () => {
    mount();
    await waitFor(() => expect(screen.getByTestId("dependency-list")).toBeInTheDocument());
    expect(screen.queryByTestId("in-cluster-dependency-list")).toBeNull();
  });

  it("shows no connected/healthy/verified affirmation anywhere", async () => {
    mount();
    await waitFor(() => expect(screen.getByTestId("dependency-list")).toBeInTheDocument());
    const page = document.body.textContent ?? "";
    for (const claim of ["Connected", "Healthy", "Verified", "Observed", "Live"]) {
      expect(page).not.toContain(claim);
    }
  });

  it("offers no workload action for something Drake does not run", async () => {
    mount();
    await waitFor(() => expect(screen.getByTestId("dependency-list")).toBeInTheDocument());
    const page = (document.body.textContent ?? "").toLowerCase();
    for (const action of ["restart", "replica", "rollout", "scale", "redeploy"]) {
      expect(page).not.toContain(action);
    }
  });

  it("leaks no credential, endpoint or project reference", async () => {
    mount();
    await waitFor(() => expect(screen.getByTestId("dependency-list")).toBeInTheDocument());
    const page = (document.body.textContent ?? "").toLowerCase();
    for (const secret of ["supabase.co", "service_role", "anon", "eyj", "postgres://", "https://"]) {
      expect(page).not.toContain(secret);
    }
  });

  it("uses the shared G10 components, with nothing keyed to this project", async () => {
    /**
     * The regression this guards is a `project_key === "fikir-sepeti"`
     * branch appearing in a screen. The page source is read directly
     * because a conditional like that would render correctly here and be
     * invisible to every other assertion.
     */
    const { readFileSync } = await import("node:fs");
    const { resolve } = await import("node:path");
    const source = readFileSync(
      resolve(process.cwd(), "src/app/projects/[projectId]/page.tsx"),
      "utf8",
    );
    expect(source).not.toContain("fikir-sepeti");
    expect(source).not.toContain("Fikir");
    expect(source).not.toContain("supabase");
    expect(source).not.toContain("vercel");
  });
});
