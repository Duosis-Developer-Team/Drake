import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DevelopmentView } from "@/components/agents/dev/DevelopmentView";
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

const MAP: DevMap = {
  window: "30d",
  as_of: "2026-11-22T12:00:00Z",
  days: ["2026-11-21", "2026-11-22"],
  configured: { commits: true, telemetry: true, repositories: 2 },
  summary: {
    commits: 3,
    agent_commits: 2,
    people: 2,
    repositories: 1,
    top_agent: "claude_code",
    agents: { claude_code: 2, none: 1 },
    cost_usd: 184.6,
    tokens: 900000,
    sessions: 42,
  },
  people: [
    {
      key: "gencay", name: "Gencay Coşkun", logins: ["gencaycoskuntr"], linked: true, bot: false,
      commits: 2, additions: 10, deletions: 2, agents: { claude_code: 2 }, tools: ["claude_code"],
      projects: ["p1"], active_days: 1, last_at: "2026-11-22T10:00:00Z", series: [0, 2], telemetry: TELEMETRY,
    },
    {
      key: "eneserman", name: "Enes Erman", logins: ["eneserman"], linked: true, bot: false,
      commits: 1, additions: 3, deletions: 0, agents: { none: 1 }, tools: [], projects: ["p1"],
      active_days: 1, last_at: "2026-11-21T10:00:00Z", series: [1, 0], telemetry: null,
    },
  ],
  projects: [{ id: "p1", key: "hermes", name: "Hermes", repository: "acme/hermes", url: "/repo", commits: 3, agent_commits: 2, people: 2, services: 1 }],
  services: [{ project_id: "p1", key: "core-service", commits: 3 }],
  edges: [
    { person: "gencay", project_id: "p1", agent: "claude_code", commits: 2, additions: 10, deletions: 2, last_at: null, telemetry: TELEMETRY },
    { person: "eneserman", project_id: "p1", agent: "none", commits: 1, additions: 3, deletions: 0, last_at: null, telemetry: null },
  ],
};

afterEach(() => vi.unstubAllGlobals());

describe("development view", () => {
  it("shows who works where, cost where Claude Code reports, and never a zero where it does not", async () => {
    installFetchMock({
      "/v1/agents/dev/map": { status: 200, body: MAP },
      "/v1/agents/dev/connect": { status: 200, body: { endpoint: null, collecting: true, projects: ["hermes"], protocol: "http/protobuf", content_capture: "dropped" } },
      "/v1/agents/dev/commits": { status: 200, body: { items: [], total: 0 } },
    });
    render(<DevelopmentView />);
    expect((await screen.findAllByText("$185")).length).toBeGreaterThan(0);
    expect(screen.getByText("67%")).toBeInTheDocument(); // 2 of 3 commits named an agent

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

  it("says why it is empty", async () => {
    installFetchMock({
      "/v1/agents/dev/map": {
        status: 200,
        body: { ...MAP, people: [], projects: [], services: [], edges: [], configured: { commits: true, telemetry: false, repositories: 0 } },
      },
      "/v1/agents/dev/connect": { status: 200, body: { endpoint: null, collecting: false, projects: [], protocol: "http/protobuf", content_capture: "dropped" } },
    });
    render(<DevelopmentView />);
    expect(await screen.findByTestId("dev-empty")).toHaveTextContent(/GitHub App/);
  });
});
