import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DevMap as DevMapView } from "@/components/agents/dev/DevMap";
import { catalogOnly, DevelopmentView } from "@/components/agents/dev/DevelopmentView";
import type { DevMap, DevTelemetry } from "@/lib/devAgents";
import { installFetchMock } from "@/test/mock-api";

const TELEMETRY: DevTelemetry = {
  cost_usd: 184.6,
  input_tokens: 1000,
  output_tokens: 2000,
  cache_read_tokens: 800000,
  cache_creation_tokens: 97000,
  tokens: 900000,
  api_requests: 40,
  api_errors: 1,
  prompts: 9,
  sessions: 42,
  active_seconds: 7200,
  lines_added: null,
  lines_removed: null,
  commits: null,
  pull_requests: null,
  edits_accepted: 12,
  edits_rejected: 1,
  tools: [{ name: "Edit", calls: 30, failures: 1, cost_usd: 0, tokens: 0 }],
  mcp_tools: [],
  models: [],
  languages: [],
};

const HERMES = "gh:101";
const ZABAKE = "gh:202";

const MAP: DevMap = {
  window: "30d",
  as_of: "2026-11-22T12:00:00Z",
  days: ["2026-11-21", "2026-11-22"],
  configured: { commits: true, telemetry: true, repositories: 2, organisations: ["duosis-developer-team"] },
  summary: {
    commits: 5,
    agent_commits: 3,
    people: 2,
    repositories: 2,
    catalogued: 1,
    top_agent: "claude_code",
    agents: { claude_code: 3, none: 2 },
    cost_usd: 184.6,
    tokens: 900000,
    sessions: 42,
  },
  people: [
    {
      key: "gencay", name: "Gencay Coşkun", logins: ["gencaycoskuntr"], linked: true, bot: false,
      commits: 2, additions: 10, deletions: 2, agents: { claude_code: 2 }, tools: ["claude_code"],
      repositories: [HERMES], active_days: 1, last_at: "2026-11-22T10:00:00Z", series: [0, 2], telemetry: TELEMETRY,
    },
    {
      key: "eneserman", name: "Enes Erman", logins: ["eneserman"], linked: true, bot: false,
      commits: 3, additions: 3, deletions: 0, agents: { none: 2, claude_code: 1 }, tools: ["claude_code"],
      repositories: [HERMES, ZABAKE], active_days: 1, last_at: "2026-11-21T10:00:00Z", series: [1, 0], telemetry: null,
    },
  ],
  repositories: [
    {
      id: HERMES, full_name: "Duosis-Developer-Team/Hermes", name: "Hermes", url: "/repo", catalogued: true,
      project_id: "p1", project_key: "hermes", project_name: "Hermes", commits: 3, agent_commits: 2, people: 2,
      services: 1, cost_usd: 184.6,
    },
    {
      id: ZABAKE, full_name: "Duosis-Developer-Team/project-zabake", name: "project-zabake", url: "/zabake",
      catalogued: false, project_id: null, project_key: null, project_name: null, commits: 2, agent_commits: 1,
      people: 1, services: 0, cost_usd: null,
    },
  ],
  services: [{ repository: HERMES, key: "core-service", commits: 3 }],
  edges: [
    { person: "gencay", repository: HERMES, agent: "claude_code", commits: 2, additions: 10, deletions: 2, last_at: null, telemetry: TELEMETRY },
    { person: "eneserman", repository: HERMES, agent: "none", commits: 1, additions: 3, deletions: 0, last_at: null, telemetry: null },
    { person: "eneserman", repository: ZABAKE, agent: "claude_code", commits: 1, additions: 1, deletions: 0, last_at: null, telemetry: null },
    { person: "eneserman", repository: ZABAKE, agent: "none", commits: 1, additions: 1, deletions: 0, last_at: null, telemetry: null },
  ],
};

const CONNECT = { endpoint: null, collecting: true, projects: ["hermes"], protocol: "http/protobuf", content_capture: "dropped" };

afterEach(() => vi.unstubAllGlobals());

describe("development view", () => {
  it("shows who works where, cost where Claude Code reports, and never a zero where it does not", async () => {
    installFetchMock({
      "/v1/agents/dev/map": { status: 200, body: MAP },
      "/v1/agents/dev/connect": { status: 200, body: CONNECT },
      "/v1/agents/dev/commits": { status: 200, body: { items: [], total: 0 } },
    });
    render(<DevelopmentView />);
    expect((await screen.findAllByText("$185")).length).toBeGreaterThan(0);
    expect(screen.getByText("60%")).toBeInTheDocument(); // 3 of 5 commits named an agent
    // The summary counts repositories, and how many are catalogued.
    expect(screen.getByText(/2 repositories · 1 in the catalog/)).toBeInTheDocument();

    // jsdom has no layout width, so the map falls back to its list.
    const list = await screen.findByTestId("dev-list");
    fireEvent.click(within(list).getByText("Enes Erman"));
    expect(await screen.findByTestId("dev-telemetry-off")).toHaveTextContent("Telemetry not connected");

    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    fireEvent.click(within(list).getByText("Gencay Coşkun"));
    const telemetry = await screen.findByTestId("dev-telemetry");
    expect(within(telemetry).getByText("42")).toBeInTheDocument(); // sessions
    expect(within(telemetry).getAllByText("not reported").length).toBeGreaterThan(0); // lines: not zero
  });

  it("puts a repository outside the catalog on the map by its own name", async () => {
    const calls = installFetchMock({
      "/v1/agents/dev/map": { status: 200, body: MAP },
      "/v1/agents/dev/connect": { status: 200, body: CONNECT },
      "/v1/agents/dev/commits": { status: 200, body: { items: [], total: 0 } },
    });
    render(<DevelopmentView />);
    const list = await screen.findByTestId("dev-list");
    expect(within(list).getAllByText("project-zabake").length).toBe(2); // two agents, one repository
    expect(screen.getByTestId("dev-legend")).toHaveTextContent("Not in the catalog");

    // The arrow's commits are asked for by repository, not by project.
    fireEvent.click(within(list).getAllByText("project-zabake")[0]);
    expect(await screen.findByTestId("dev-edge-detail")).toHaveTextContent("Enes Erman → project-zabake");
    expect(calls.some((c) => c.path.includes(`repository=${encodeURIComponent(ZABAKE)}`))).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));

    // Catalog only: the org's other repositories step aside.
    fireEvent.click(screen.getByRole("radio", { name: "Catalog (1)" }));
    expect(within(screen.getByTestId("dev-list")).queryByText("project-zabake")).toBeNull();
  });

  it("draws catalogued and other repositories apart, with services only under the catalog's", () => {
    render(<DevMapView map={MAP} width={1200} picked={null} onPick={() => {}} />);
    const hermes = screen.getByTestId("dev-repo-hermes");
    const zabake = screen.getByTestId("dev-repo-project-zabake");
    expect(hermes).toHaveAttribute("data-catalogued", "true");
    expect(zabake).toHaveAttribute("data-catalogued", "false");
    expect(zabake).toHaveTextContent("not in catalog");
    expect(within(zabake).getByTestId("dev-repo-icon-project-zabake")).toBeInTheDocument();
    expect(hermes).not.toHaveTextContent("not in catalog");
    expect(screen.getByText("Repositories")).toBeInTheDocument();
    expect(screen.getAllByText("core-service")).toHaveLength(1);
  });

  it("keeps only the catalog's repositories and the people who worked there", () => {
    const only = catalogOnly({
      ...MAP,
      people: [
        ...MAP.people,
        { ...MAP.people[1], key: "solo", name: "Solo", repositories: [ZABAKE] },
      ],
      edges: [...MAP.edges, { ...MAP.edges[2], person: "solo" }],
    });
    expect(only.repositories.map((r) => r.id)).toEqual([HERMES]);
    expect(only.edges.every((e) => e.repository === HERMES)).toBe(true);
    expect(only.people.map((p) => p.key)).toEqual(["gencay", "eneserman"]);
    expect(only.people[1].repositories).toEqual([HERMES]);
  });

  it("says why it is empty", async () => {
    installFetchMock({
      "/v1/agents/dev/map": {
        status: 200,
        body: {
          ...MAP,
          people: [],
          repositories: [],
          services: [],
          edges: [],
          configured: { commits: true, telemetry: false, repositories: 0, organisations: [] },
        },
      },
      "/v1/agents/dev/connect": { status: 200, body: { ...CONNECT, collecting: false, projects: [] } },
    });
    render(<DevelopmentView />);
    expect(await screen.findByTestId("dev-empty")).toHaveTextContent(/GitHub App/);
  });
});
