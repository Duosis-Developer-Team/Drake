import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LogExplorer } from "@/components/service-health/LogExplorer";
import { LocaleProvider } from "@/lib/i18n";
import type { LogEntry, LogPage } from "@/lib/logs";

function line(overrides: Partial<LogEntry> = {}): LogEntry {
  return {
    timestamp: "2026-11-04T09:15:02.000000+00:00",
    severity: null,
    severity_number: 0,
    body: "GET /v1/orders 200",
    trace_id: null,
    span_id: null,
    pod: "api-7d9f-a",
    container: "api",
    ...overrides,
  };
}

function page(overrides: Partial<LogPage> = {}): LogPage {
  return {
    binding_id: "b1",
    range: "1h",
    as_of: "2026-11-04T09:16:00+00:00",
    state: "ok",
    window: { start: "2026-11-04T08:16:00+00:00", end: "2026-11-04T09:16:00+00:00" },
    entries: [],
    next_before: null,
    ...overrides,
  };
}

function respond(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "",
    json: async () => body,
  };
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function requestedParams(call = 0): Record<string, string> {
  const url = new URL(String(fetchMock.mock.calls[call][0]), "file:///");
  return Object.fromEntries(url.searchParams);
}

describe("LogExplorer", () => {
  it("reads the binding's logs with sensible defaults", async () => {
    fetchMock.mockResolvedValue(respond(page({ entries: [line()] })));
    render(<LogExplorer bindingId="b1" />);

    await screen.findByText("GET /v1/orders 200");
    const path = String(fetchMock.mock.calls[0][0]);
    expect(path.startsWith("/v1/service-health/bindings/b1/logs?")).toBe(true);
    expect(requestedParams()).toEqual({ range: "1h", severity: "any", limit: "100" });
  });

  it("hides health checks by default, says how many, and brings them back", async () => {
    fetchMock.mockResolvedValue(
      respond(
        page({
          entries: [
            line({ body: 'INFO: 10.0.0.1 - "GET /health HTTP/1.1" 200 OK' }),
            line({ body: "kube-probe/1.34 GET / 200" }),
            line({ body: "payment captured" }),
          ],
        }),
      ),
    );
    render(<LogExplorer bindingId="b1" />);

    await screen.findByText("payment captured");
    expect(screen.queryByText(/GET \/health/)).not.toBeInTheDocument();
    const hidden = screen.getByTestId("log-health-hidden");
    expect(hidden).toHaveTextContent("2 health-check lines hidden");

    fireEvent.click(within(hidden).getByRole("button", { name: "Show them" }));
    expect(await screen.findByText(/GET \/health/)).toBeInTheDocument();
  });

  it("says when a window holds nothing but health checks", async () => {
    fetchMock.mockResolvedValue(respond(page({ entries: [line({ body: "GET /healthz 200" })] })));
    render(<LogExplorer bindingId="b1" />);
    expect(await screen.findByText("Only health checks in this window")).toBeInTheDocument();
  });

  it("an empty window is 'no lines matched', never 'no errors'", async () => {
    fetchMock.mockResolvedValue(respond(page()));
    render(<LogExplorer bindingId="b1" />);
    expect(await screen.findByText("No lines matched")).toBeInTheDocument();
    // The empty state explains itself rather than reassuring.
    expect(screen.getByText(/not the same as no errors/)).toBeInTheDocument();
  });

  it("keeps not configured and unavailable apart", async () => {
    fetchMock.mockResolvedValueOnce(respond(page({ state: "not_configured" })));
    const view = render(<LogExplorer bindingId="b1" />);
    expect(await screen.findByText("Log store not configured")).toBeInTheDocument();
    view.unmount();

    fetchMock.mockResolvedValueOnce(respond(page({ state: "unavailable", reason: "store_timeout" })));
    render(<LogExplorer bindingId="b2" />);
    expect(await screen.findByText("Logs could not be read")).toBeInTheDocument();
    expect(screen.getByText(/store_timeout/)).toBeInTheDocument();
  });

  it("a 404 on a visible binding is missing logs.view, said in those words", async () => {
    fetchMock.mockResolvedValue(respond({ error: { code: "not_found", message: "not found" } }, 404));
    render(<LogExplorer bindingId="b1" />);
    expect(await screen.findByText("Log access required")).toBeInTheDocument();
    // No filters are offered for logs the caller cannot read.
    expect(screen.queryByTestId("log-filters")).not.toBeInTheDocument();
  });

  it("sends severity and a debounced search to the server", async () => {
    fetchMock.mockResolvedValue(respond(page({ entries: [line()] })));
    render(<LogExplorer bindingId="b1" />);
    await screen.findByText("GET /v1/orders 200");

    fireEvent.change(screen.getByTestId("log-severity"), { target: { value: "error" } });
    await waitFor(() => expect(requestedParams(fetchMock.mock.calls.length - 1).severity).toBe("error"));

    fireEvent.change(screen.getByLabelText("Search log lines"), { target: { value: "timeout" } });
    await waitFor(() => expect(requestedParams(fetchMock.mock.calls.length - 1).q).toBe("timeout"), {
      timeout: 2000,
    });
  });

  it("counts errors and warnings and filters to them on click", async () => {
    fetchMock.mockResolvedValue(
      respond(
        page({
          entries: [
            line({ severity: "ERROR", severity_number: 17, body: "upstream timeout" }),
            line({ body: '{"level":"WARNING","message":"slow query"}' }),
            line({ body: "all good" }),
          ],
        }),
      ),
    );
    render(<LogExplorer bindingId="b1" />);
    await screen.findByText("upstream timeout");

    const summary = screen.getByTestId("log-summary");
    expect(summary).toHaveTextContent("3 lines");
    expect(summary).toHaveTextContent("1 error");
    expect(summary).toHaveTextContent("1 warning");

    fireEvent.click(within(summary).getByRole("button", { name: "1 error" }));
    expect(screen.queryByText("all good")).not.toBeInTheDocument();
    expect(screen.getByText("upstream timeout")).toBeInTheDocument();
  });

  it("shows a JSON line's message, and its fields and raw text when opened", async () => {
    fetchMock.mockResolvedValue(
      respond(
        page({
          entries: [
            line({
              body: '{"level":"INFO","message":"request served","path":"/v1/me"}',
              trace_id: "4bf92f3577b34da6a3ce929d0e0e4736",
            }),
          ],
        }),
      ),
    );
    render(<LogExplorer bindingId="b1" />);

    const headline = await screen.findByText("request served");
    fireEvent.click(headline.closest("button")!);
    const detail = await screen.findByTestId("log-detail");
    expect(detail).toHaveTextContent("path");
    expect(detail).toHaveTextContent("/v1/me");
    expect(detail).toHaveTextContent('{"level":"INFO","message":"request served","path":"/v1/me"}');
    expect(detail).toHaveTextContent("4bf92f35");
  });

  it("loads older lines below the newest page", async () => {
    fetchMock
      .mockResolvedValueOnce(
        respond(page({ entries: [line({ body: "newest" })], next_before: "2026-11-04T09:00:00+00:00" })),
      )
      .mockResolvedValueOnce(respond(page({ entries: [line({ body: "older" })], next_before: null })));
    render(<LogExplorer bindingId="b1" />);

    await screen.findByText("newest");
    fireEvent.click(screen.getByTestId("log-load-older"));
    await screen.findByText("older");
    expect(requestedParams(1).before).toBe("2026-11-04T09:00:00+00:00");
    expect(screen.getByText("Start of the window reached")).toBeInTheDocument();
  });

  it("live mode re-reads the newest page on a timer", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fetchMock.mockResolvedValue(respond(page({ entries: [line()] })));
    render(<LogExplorer bindingId="b1" />);
    await screen.findByText("GET /v1/orders 200");
    const before = fetchMock.mock.calls.length;

    fireEvent.click(screen.getByRole("switch", { name: "Live" }));
    await act(async () => {
      vi.advanceTimersByTime(10_500);
    });
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(before));
  });

  it("renders in Turkish", async () => {
    fetchMock.mockResolvedValue(respond(page()));
    render(
      <LocaleProvider locale="tr">
        <LogExplorer bindingId="b1" />
      </LocaleProvider>,
    );
    expect(await screen.findByText("Eşleşen satır yok")).toBeInTheDocument();
    expect(screen.getByText("Loglar")).toBeInTheDocument();
  });

  it("bounds the list: the first lines, then more on request, before older pages", async () => {
    const entries = Array.from({ length: 45 }, (_, index) =>
      line({ body: `line ${index}`, timestamp: `2026-11-04T09:15:${String(59 - index).padStart(2, "0")}.000000+00:00` }),
    );
    fetchMock.mockResolvedValue(
      respond(page({ entries, next_before: "2026-11-04T09:00:00+00:00" })),
    );
    render(<LogExplorer bindingId="b1" />);

    const list = await screen.findByTestId("log-lines");
    expect(within(list).getAllByRole("listitem")).toHaveLength(30);
    expect(screen.getByTestId("log-lines-shown")).toHaveTextContent("Showing 30 of 45 lines");
    // Older pages are offered only once what is loaded has been shown.
    expect(screen.queryByTestId("log-load-older")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("log-lines-more"));
    expect(within(list).getAllByRole("listitem")).toHaveLength(45);
    expect(screen.queryByTestId("log-lines-more")).not.toBeInTheDocument();
    expect(screen.getByTestId("log-load-older")).toBeInTheDocument();
  });
});
