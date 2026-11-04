import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TraceExplorer } from "@/components/service-health/TraceExplorer";
import { LocaleProvider } from "@/lib/i18n";
import type { TraceDetail, TracePage } from "@/lib/traces";

const TRACE = "4bf92f3577b34da6a3ce929d0e0e4736";

function page(overrides: Partial<TracePage> = {}): TracePage {
  return {
    binding_id: "b1",
    range: "1h",
    as_of: "2026-11-04T10:00:00+00:00",
    state: "ok",
    totals: { requests: 45, errors: 5, error_ratio: 5 / 45, p95_ms: 190 },
    routes: [
      { route: "GET /orders/*", requests: 40, errors: 0, error_ratio: 0, p50_ms: 100, p95_ms: 180 },
      { route: "GET /fail/*", requests: 5, errors: 5, error_ratio: 1, p50_ms: 140, p95_ms: 190 },
    ],
    requests: [
      {
        timestamp: "2026-11-04T09:59:00.000+00:00",
        trace_id: TRACE,
        route: "GET /fail/*",
        duration_ms: 148.2,
        error: true,
        method: "GET",
        http_status: "500",
        path: "/fail/3",
        pod: "api-1",
      },
    ],
    ...overrides,
  };
}

const DETAIL: TraceDetail = {
  binding_id: "b1",
  trace_id: TRACE,
  state: "ok",
  spans: [
    {
      timestamp: "2026-11-04T09:59:00.000+00:00",
      span_id: "a1",
      parent_span_id: null,
      name: "GET /fail/*",
      kind: "Server",
      duration_ms: 148.2,
      error: true,
      status_message: null,
      attributes: { "http.response.status_code": "500" },
      pod: "api-1",
    },
    {
      timestamp: "2026-11-04T09:59:00.010+00:00",
      span_id: "a2",
      parent_span_id: "a1",
      name: "processing",
      kind: "Internal",
      duration_ms: 120,
      error: false,
      status_message: null,
      attributes: {},
      pod: "api-1",
    },
  ],
};

function respond(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, statusText: "", json: async () => body };
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function params(call: number): Record<string, string> {
  return Object.fromEntries(new URL(String(fetchMock.mock.calls[call][0]), "file:///").searchParams);
}

describe("TraceExplorer", () => {
  it("leads with requests, error rate and p95, then the endpoints", async () => {
    fetchMock.mockResolvedValue(respond(page()));
    render(<TraceExplorer bindingId="b1" />);

    const totals = await screen.findByTestId("trace-totals");
    expect(totals).toHaveTextContent("45");
    expect(totals).toHaveTextContent("11%");
    expect(totals).toHaveTextContent("190 ms");
    const routes = screen.getByTestId("trace-routes");
    expect(within(routes).getByText("GET /orders/*")).toBeInTheDocument();
    expect(within(routes).getByText("5 · 100%")).toBeInTheDocument();
    expect(params(0)).toEqual({ range: "1h", status: "any", sort: "recent", limit: "50" });
  });

  it("sends sort and errors-only to the server", async () => {
    fetchMock.mockResolvedValue(respond(page()));
    render(<TraceExplorer bindingId="b1" />);
    await screen.findByTestId("trace-totals");

    fireEvent.click(screen.getByRole("radio", { name: "Slowest" }));
    await waitFor(() => expect(params(fetchMock.mock.calls.length - 1).sort).toBe("slowest"));
    fireEvent.click(screen.getByLabelText("Errors only"));
    await waitFor(() => expect(params(fetchMock.mock.calls.length - 1).status).toBe("error"));
  });

  it("opens a request into its waterfall and a span into its details", async () => {
    fetchMock.mockImplementation(async (input: string) =>
      String(input).includes(`/traces/${TRACE}`) ? respond(DETAIL) : respond(page()),
    );
    render(<TraceExplorer bindingId="b1" />);

    fireEvent.click((await screen.findByText("/fail/3")).closest("button")!);
    const waterfall = await screen.findByTestId("trace-waterfall");
    expect(await within(waterfall).findByText("processing")).toBeInTheDocument();
    expect(waterfall).toHaveTextContent("2 spans");

    fireEvent.click(within(waterfall).getByText("processing").closest("button")!);
    expect(within(waterfall).getByText("Internal")).toBeInTheDocument();
  });

  it("says a trace is gone when it has expired", async () => {
    fetchMock.mockImplementation(async (input: string) =>
      String(input).includes(`/traces/${TRACE}`)
        ? respond({ error: { code: "not_found", message: "not found" } }, 404)
        : respond(page()),
    );
    render(<TraceExplorer bindingId="b1" />);
    fireEvent.click((await screen.findByText("/fail/3")).closest("button")!);
    expect(await screen.findByText(/no longer stored/)).toBeInTheDocument();
  });

  it("keeps not enabled, unavailable and a quiet window apart", async () => {
    fetchMock.mockResolvedValueOnce(respond(page({ state: "not_configured", totals: null, routes: [], requests: [] })));
    const a = render(<TraceExplorer bindingId="b1" />);
    expect(await screen.findByText("Tracing not enabled")).toBeInTheDocument();
    a.unmount();

    fetchMock.mockResolvedValueOnce(
      respond(page({ state: "unavailable", reason: "store_timeout", totals: null, routes: [], requests: [] })),
    );
    const b = render(<TraceExplorer bindingId="b2" />);
    expect(await screen.findByText("Traces could not be read")).toBeInTheDocument();
    expect(screen.getByText(/store_timeout/)).toBeInTheDocument();
    b.unmount();

    fetchMock.mockResolvedValueOnce(
      respond(page({ totals: { requests: 0, errors: 0, error_ratio: null, p95_ms: null }, routes: [], requests: [] })),
    );
    render(<TraceExplorer bindingId="b3" />);
    expect(await screen.findByText("No requests in this window")).toBeInTheDocument();
  });

  it("a 404 on a visible binding is missing traces.view", async () => {
    fetchMock.mockResolvedValue(respond({ error: { code: "not_found", message: "not found" } }, 404));
    render(<TraceExplorer bindingId="b1" />);
    expect(await screen.findByText("Trace access required")).toBeInTheDocument();
  });

  it("renders in Turkish", async () => {
    fetchMock.mockResolvedValue(respond(page()));
    render(
      <LocaleProvider locale="tr">
        <TraceExplorer bindingId="b1" />
      </LocaleProvider>,
    );
    expect(await screen.findByText("İstekler ve trace'ler")).toBeInTheDocument();
    expect(screen.getByText("Hata oranı")).toBeInTheDocument();
  });
});
