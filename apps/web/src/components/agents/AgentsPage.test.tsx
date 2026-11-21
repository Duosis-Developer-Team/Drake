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
  return { ok: status >= 200 && status < 300, status, statusText: "", json: async () => body };
}

const ZERO = {
  invocations: 0, inference_calls: 0, tool_calls: 0, errors: 0, input_tokens: 0, output_tokens: 0,
  cache_read_tokens: 0, cache_write_tokens: 0, reasoning_tokens: 0, duration_ms: 0,
};

function agent(id: string, over: Partial<Agent>): Agent {
  return {
    id, display_name: id, confidence: "named", ingest: "cluster", lifecycle: "observed",
    service_name: "svc", namespace: "ns", workload: "wl", cluster_ref: "c1", agent_external_id: null,
    first_seen_at: "2026-11-20T10:00:00Z", last_seen_at: "2026-11-21T10:00:00Z", new: false,
    project: null, environment: null, binding_id: null, totals: { ...ZERO, inference_calls: 4 },
    providers: [{ provider: "openai", calls: 4 }], models: [{ model: "gpt-5", provider: "openai", calls: 4 }],
    series: [], ...over,
  };
}

const CONNECT = { cluster_endpoint: "gw.internal:4318", external_endpoint: null, protocol: "http/protobuf", content_capture: "dropped" };

function inventory(items: Agent[]): AgentInventory {
  return {
    window: "24h", discovering: false, items,
    summary: {
      agents: items.length, active: items.length, new: 0, unowned: items.length,
      outside_catalog: items.filter((a) => !a.project).length,
      by_confidence: { stable: 0, named: items.length, service: 0, observed: 0 },
      providers: [{ provider: "openai", calls: 8 }],
      totals: { invocations: 0, inference_calls: 8, tool_calls: 0, errors: 0, input_tokens: 10, output_tokens: 5 },
      series: [{ t: "2026-11-21T09:00:00Z", calls: 8, errors: 0, tokens: 15 }],
    },
  };
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

function serve(inv: AgentInventory) {
  fetchMock.mockImplementation(async (input: string) =>
    String(input).includes("/v1/agents/connect") ? respond(CONNECT) : respond(inv),
  );
}

const renderPage = () => render(<LocaleProvider><AgentsPage /></LocaleProvider>);

describe("Agents inventory", () => {
  it("with no agent yet, is the way to connect one", async () => {
    serve(inventory([]));
    renderPage();
    const empty = await screen.findByTestId("agents-empty");
    expect(within(empty).getByText("No agent has reported yet")).toBeTruthy();
    expect(await within(empty).findByText(/OTEL_EXPORTER_OTLP_ENDPOINT=gw\.internal:4318/)).toBeTruthy();
    expect(within(empty).getByText("The external door is not open in this deployment.")).toBeTruthy();
  });

  it("lists every agent and filters by name and catalog", async () => {
    serve(inventory([
      agent("planner", { project: { id: "p1", key: "hermes", name: "Hermes" } }),
      agent("triage", { confidence: "observed" }),
    ]));
    renderPage();
    const grid = await screen.findByTestId("agents-grid");
    expect(within(grid).getByTestId("agent-card-planner")).toBeTruthy();
    expect(within(grid).getByTestId("agent-card-triage")).toBeTruthy();
    expect(String(fetchMock.mock.calls[0][0])).toContain("/v1/agents?window=24h");

    fireEvent.change(screen.getByLabelText("Search agents"), { target: { value: "plan" } });
    expect(screen.queryByTestId("agent-card-triage")).toBeNull();

    fireEvent.change(screen.getByLabelText("Search agents"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: /Outside the catalog/ }));
    expect(screen.queryByTestId("agent-card-planner")).toBeNull();
    expect(screen.getByTestId("agent-card-triage")).toBeTruthy();
  });

  it("asks again for the other window", async () => {
    serve(inventory([agent("planner", {})]));
    renderPage();
    await screen.findByTestId("agents-grid");
    fireEvent.click(screen.getByRole("radio", { name: "7 days" }));
    await screen.findByTestId("agents-grid");
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes("window=7d"))).toBe(true);
  });
});
