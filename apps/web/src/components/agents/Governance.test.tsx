import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  GovernancePanel,
  GovernanceView,
  LifecycleBadge,
} from "@/components/agents/Governance";
import type { Agent, AgentInventory } from "@/lib/agents";
import { LocaleProvider } from "@/lib/i18n";

vi.mock("@/lib/session", () => ({
  useSession: () => ({
    state: {
      status: "authenticated",
      me: { csrf_token: "csrf-9", permissions: [] },
    },
    refresh: vi.fn(),
    signOut: vi.fn(),
    hasPermission: () => true,
  }),
}));

function respond(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "",
    json: async () => body,
  };
}

const ZERO = {
  invocations: 0,
  inference_calls: 10,
  tool_calls: 0,
  errors: 0,
  input_tokens: 0,
  output_tokens: 0,
  cache_read_tokens: 0,
  cache_write_tokens: 0,
  reasoning_tokens: 0,
  duration_ms: 0,
};

function agent(id: string, over: Partial<Agent> = {}): Agent {
  return {
    id,
    display_name: id,
    confidence: "named",
    ingest: "cluster",
    lifecycle: "observed",
    service_name: "svc",
    namespace: "ns",
    workload: "wl",
    cluster_ref: "c1",
    agent_external_id: null,
    first_seen_at: "2026-11-20T10:00:00Z",
    last_seen_at: "2026-11-21T10:00:00Z",
    new: false,
    project: { id: "p1", key: "hermes", name: "Hermes" },
    environment: { id: "e1", key: "prod" },
    binding_id: null,
    totals: ZERO,
    cost_usd: 0,
    unpriced_tokens: 0,
    providers: [],
    models: [],
    series: [],
    owner: null,
    purpose: null,
    shadow: false,
    can_manage: true,
    ...over,
  };
}

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("confirm", () => true);
});
afterEach(() => vi.unstubAllGlobals());

const wrap = (node: React.ReactNode) =>
  render(<LocaleProvider>{node}</LocaleProvider>);

describe("Governance", () => {
  it("names shadow AI before anything else about an agent", () => {
    wrap(<LifecycleBadge agent={{ lifecycle: "observed", shadow: true }} />);
    expect(screen.getByText("Shadow AI")).toBeTruthy();
  });

  it("approves an agent and takes ownership from its panel", async () => {
    const onChange = vi.fn();
    wrap(<GovernancePanel agent={agent("triager")} onChange={onChange} />);
    fetchMock.mockResolvedValueOnce(
      respond(agent("triager", { lifecycle: "approved" })),
    );
    fireEvent.click(screen.getByTestId("gov-approve"));
    await waitFor(() => expect(onChange).toHaveBeenCalled());
    const [path, init] = fetchMock.mock.calls[0];
    expect(String(path)).toBe("/v1/agents/triager/governance");
    expect(init.headers["X-CSRF-Token"]).toBe("csrf-9");
    expect(JSON.parse(init.body)).toEqual({ lifecycle: "approved" });

    fetchMock.mockResolvedValueOnce(
      respond(agent("triager", { owner: { id: "u1", name: "Owner One" } })),
    );
    fireEvent.click(screen.getByTestId("gov-owner"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({
      owner: "me",
    });
  });

  it("is read-only without agent.manage", () => {
    wrap(
      <GovernancePanel
        agent={agent("triager", { can_manage: false })}
        onChange={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("gov-approve")).toBeNull();
    expect(screen.getByText(/agent.manage/)).toBeTruthy();
  });

  it("lists what waits for review, shadow AI first, and approves from the list", async () => {
    const inventory = {
      window: "24h",
      discovering: true,
      items: [
        agent("busy", { totals: { ...ZERO, inference_calls: 900 } }),
        agent("hidden", { shadow: true, project: null, environment: null }),
        agent("done", {
          lifecycle: "approved",
          owner: { id: "u1", name: "Owner One" },
        }),
        agent("old", { lifecycle: "retired", seen_after_retirement: true }),
      ],
      summary: {},
    } as unknown as AgentInventory;
    fetchMock.mockResolvedValueOnce(respond(inventory));
    wrap(<GovernanceView span="24h" />);
    await screen.findByTestId("agents-governance");
    expect(String(fetchMock.mock.calls[0][0])).toContain("lifecycle=all");
    const rows = screen
      .getAllByTestId(/^gov-row-/)
      .map((r) => r.getAttribute("data-testid"));
    expect(rows.slice(0, 2)).toEqual(["gov-row-hidden", "gov-row-busy"]);
    expect(
      within(screen.getByTestId("gov-kpi-shadow")).getByText("1"),
    ).toBeTruthy();
    expect(screen.getByText("1 retired agents were seen again")).toBeTruthy();

    fetchMock.mockResolvedValueOnce(
      respond(agent("hidden", { lifecycle: "approved", shadow: false })),
    );
    fireEvent.click(screen.getByTestId("gov-approve-hidden"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({
      lifecycle: "approved",
    });
  });
});
