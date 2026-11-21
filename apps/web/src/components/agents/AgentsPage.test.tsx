import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AgentsPage from "@/app/agents/page";
import type { Agent, AgentInventory } from "@/lib/agents";
import { LocaleProvider } from "@/lib/i18n";

vi.mock("next/navigation", () => ({
  usePathname: () => "/agents",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
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
  inference_calls: 0,
  tool_calls: 0,
  errors: 0,
  input_tokens: 0,
  output_tokens: 0,
  cache_read_tokens: 0,
  cache_write_tokens: 0,
  reasoning_tokens: 0,
  duration_ms: 0,
};

function agent(id: string, over: Partial<Agent>): Agent {
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
    project: null,
    environment: null,
    binding_id: null,
    totals: { ...ZERO, inference_calls: 4 },
    cost_usd: 0.42,
    unpriced_tokens: 0,
    providers: [{ provider: "openai", calls: 4 }],
    models: [{ model: "gpt-5", provider: "openai", calls: 4 }],
    series: [],
    ...over,
  };
}

const CONNECT = {
  cluster_endpoint: "gw.internal:4318",
  external_endpoint: null,
  protocol: "http/protobuf",
  content_capture: "dropped",
};

function inventory(items: Agent[]): AgentInventory {
  return {
    window: "24h",
    discovering: false,
    items,
    summary: {
      agents: items.length,
      active: items.length,
      new: 0,
      unowned: items.length,
      outside_catalog: items.filter((a) => !a.project).length,
      by_confidence: {
        stable: 0,
        named: items.length,
        service: 0,
        observed: 0,
      },
      providers: [{ provider: "openai", calls: 8 }],
      totals: {
        invocations: 0,
        inference_calls: 8,
        tool_calls: 0,
        errors: 0,
        input_tokens: 10,
        output_tokens: 5,
      },
      series: [
        {
          t: "2026-11-21T09:00:00Z",
          calls: 8,
          errors: 0,
          tokens: 15,
          cost_usd: 0.84,
        },
      ],
      cost_usd: 0.84,
      unpriced_tokens: 0,
    },
  };
}

const PRICES = {
  enabled: true,
  currency: "USD",
  basis: "list",
  priced_models: 1200,
  last_fetch: null,
  unpriced: [{ provider: "ollama", model: "llama-5" }],
  last_good_fetch: {
    fetched_at: "2026-11-21T08:00:00Z",
    status: "live",
    models: 1200,
    changed: 0,
    source:
      "raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json",
  },
};

let MCP: { window: string; servers: unknown[] } = {
  window: "24h",
  servers: [],
};

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

function serve(inv: AgentInventory) {
  fetchMock.mockImplementation(async (input: string) =>
    String(input).includes("/v1/agents/connect")
      ? respond(CONNECT)
      : String(input).includes("/v1/agents/mcp-servers")
        ? respond(MCP)
        : String(input).includes("/v1/agents/prices")
          ? respond(PRICES)
          : respond(inv),
  );
}

const renderPage = () =>
  render(
    <LocaleProvider>
      <AgentsPage />
    </LocaleProvider>,
  );

describe("Agents inventory", () => {
  it("with no agent yet, is the way to connect one", async () => {
    serve(inventory([]));
    renderPage();
    const empty = await screen.findByTestId("agents-empty");
    expect(within(empty).getByText("No agent has reported yet")).toBeTruthy();
    expect(
      await within(empty).findByText(
        /OTEL_EXPORTER_OTLP_ENDPOINT=gw\.internal:4318/,
      ),
    ).toBeTruthy();
    expect(
      within(empty).getByText(
        "The external door is not open in this deployment.",
      ),
    ).toBeTruthy();
  });

  it("lists every agent and filters by name and catalog", async () => {
    serve(
      inventory([
        agent("planner", {
          project: { id: "p1", key: "hermes", name: "Hermes" },
        }),
        agent("triage", { confidence: "observed" }),
      ]),
    );
    renderPage();
    const grid = await screen.findByTestId("agents-grid");
    expect(within(grid).getByTestId("agent-card-planner")).toBeTruthy();
    expect(within(grid).getByTestId("agent-card-triage")).toBeTruthy();
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      "/v1/agents?window=24h",
    );
    // Cost, and where its prices come from.
    expect(screen.getByTestId("agents-kpi-cost").textContent).toContain(
      "$0.84",
    );
    expect((await screen.findByTestId("price-note")).textContent).toContain(
      "LiteLLM",
    );
    expect(screen.getByTestId("price-note").textContent).toContain("llama-5");

    fireEvent.change(screen.getByLabelText("Search agents"), {
      target: { value: "plan" },
    });
    expect(screen.queryByTestId("agent-card-triage")).toBeNull();

    fireEvent.change(screen.getByLabelText("Search agents"), {
      target: { value: "" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: /Outside the catalog/ }),
    );
    expect(screen.queryByTestId("agent-card-planner")).toBeNull();
    expect(screen.getByTestId("agent-card-triage")).toBeTruthy();
  });

  it("asks again for the other window", async () => {
    serve(inventory([agent("planner", {})]));
    renderPage();
    await screen.findByTestId("agents-grid");
    fireEvent.click(screen.getByRole("radio", { name: "7 days" }));
    await screen.findByTestId("agents-grid");
    expect(
      fetchMock.mock.calls.some((c) => String(c[0]).includes("window=7d")),
    ).toBe(true);
  });

  it("shows MCP servers even before any agent reports", async () => {
    MCP = {
      window: "24h",
      servers: [
        {
          id: "m1",
          service_name: "hermes-mcp",
          cluster_ref: "c1",
          namespace: "hermes-test",
          workload: "hermes-mcp",
          first_seen_at: "2026-11-21T21:00:00Z",
          last_seen_at: "2026-11-21T21:59:59Z",
          project: null,
          environment: null,
          calls: 2,
          errors: 0,
          avg_ms: 436,
          tools: [{ name: "hermes_whoami", calls: 1, errors: 0, avg_ms: 93 }],
          clients: [{ name: "claude-code", calls: 2, errors: 0, avg_ms: 436 }],
          methods: [],
          series: [],
        },
      ],
    };
    serve(inventory([]));
    renderPage();
    fireEvent.click(await screen.findByTestId("agents-mcp-teaser"));
    const card = await screen.findByTestId("mcp-server-hermes-mcp");
    expect(within(card).getByText("hermes_whoami")).toBeTruthy();
    expect(within(card).getByText("claude-code")).toBeTruthy();
    MCP = { window: "24h", servers: [] };
  });
});
