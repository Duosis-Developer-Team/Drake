/**
 * Dashboard query scheduling (Sprint 3 hardening §2):
 * bounded concurrency (≤3), full loads without self-inflicted 429s,
 * real cancellation on range/scope changes and unmount, stale-generation
 * write protection, and honest 429 classification.
 */
import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DashboardRenderer, MAX_CONCURRENT_QUERIES } from "@/components/telemetry/DashboardRenderer";
import { SessionProvider } from "@/lib/session";
import { makeMe } from "@/test/mock-api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/projects/p1",
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

function widget(key: string, template: string, requiredProfile?: string) {
  return {
    key,
    title: key,
    display: "kpi",
    queryTemplateKey: template,
    reducer: "latest",
    unit: "requests_per_second",
    emptyBehavior: "show_empty",
    ...(requiredProfile ? { requiredProfile } : {}),
  };
}

function dashboardDefinition(templates: string[], requiredProfile?: string) {
  return {
    key: "test-board-v1",
    version: 1,
    title: "Test Board",
    profiles: ["fastapi-v1", "kubernetes-service-v1"],
    scopeTypes: ["service"],
    summary: "test",
    sections: [
      {
        key: "main",
        title: "Main",
        widgets: templates.map((template, index) =>
          widget(`w-${index}`, template, requiredProfile),
        ),
      },
    ],
  };
}

function envelope(template: string) {
  return {
    template_key: template,
    template_version: 1,
    metric_key: "service.request_rate",
    scope: { type: "service", ref: "alpha/dev/api" },
    unit: "requests_per_second",
    result_type: "timeseries",
    series: [{ labels: {}, points: [[1, 2.5]] }],
    range: {
      from: "2026-08-06T00:00:00+00:00",
      to: "2026-08-06T01:00:00+00:00",
      requested_step_seconds: 60,
      effective_step_seconds: 60,
      step_adjusted: false,
    },
    data_state: "ok",
    cache_state: "miss",
    partial: false,
    warnings: [],
    source_type: "prometheus",
    as_of: "2026-08-06T01:00:00+00:00",
    correlation_id: "test",
  };
}

interface PendingQuery {
  template: string;
  signal: AbortSignal | null | undefined;
  resolve: (status?: number) => void;
}

/** Deferred telemetry-query fetch mock with concurrency accounting. */
function installTelemetryMock(definition: unknown) {
  const pending: PendingQuery[] = [];
  const counters = {
    inFlight: 0,
    maxInFlight: 0,
    started: 0,
    startsByTemplate: {} as Record<string, number>,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input).split("?")[0];
      if (path === "/v1/me") {
        return Promise.resolve(
          new Response(JSON.stringify(makeMe()), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        );
      }
      if (path.startsWith("/v1/dashboard-templates/")) {
        return Promise.resolve(
          new Response(JSON.stringify({ dashboard: definition }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        );
      }
      if (path === "/v1/telemetry/query") {
        counters.started += 1;
        counters.inFlight += 1;
        counters.maxInFlight = Math.max(counters.maxInFlight, counters.inFlight);
        const body = JSON.parse(String(init?.body)) as { template_key: string };
        counters.startsByTemplate[body.template_key] =
          (counters.startsByTemplate[body.template_key] ?? 0) + 1;
        return new Promise<Response>((resolve, reject) => {
          const entry: PendingQuery = {
            template: body.template_key,
            signal: init?.signal,
            resolve: (status = 200) => {
              counters.inFlight -= 1;
              if (status === 200) {
                resolve(
                  new Response(JSON.stringify(envelope(body.template_key)), {
                    status: 200,
                    headers: { "Content-Type": "application/json" },
                  }),
                );
              } else {
                resolve(
                  new Response(
                    JSON.stringify({
                      error: {
                        code: status === 429 ? "rate_limited" : "error",
                        message: "limited",
                        correlation_id: "ref-429",
                      },
                    }),
                    { status, headers: { "Content-Type": "application/json" } },
                  ),
                );
              }
            },
          };
          init?.signal?.addEventListener("abort", () => {
            counters.inFlight -= 1;
            const index = pending.indexOf(entry);
            if (index >= 0) pending.splice(index, 1);
            reject(new DOMException("aborted", "AbortError"));
          });
          pending.push(entry);
        });
      }
      return Promise.resolve(new Response("{}", { status: 404 }));
    }),
  );
  return { pending, counters };
}

function renderBoard(props: Partial<Parameters<typeof DashboardRenderer>[0]> = {}) {
  return render(
    <SessionProvider>
      <DashboardRenderer
        templateKey="test-board-v1"
        scopeType="service"
        scopeId="s1"
        preset="24h"
        profile="fastapi-v1"
        {...props}
      />
    </SessionProvider>,
  );
}

async function flushPending(pending: PendingQuery[]) {
  while (pending.length > 0) {
    const entry = pending.shift();
    await act(async () => {
      entry?.resolve();
      await Promise.resolve();
    });
  }
}

const FIVE = ["t.one", "t.two", "t.three", "t.four", "t.five"];
const SIX = [...FIVE, "t.six"];

describe("dashboard query scheduling", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("never exceeds the bounded concurrency with a slow provider", async () => {
    const { pending, counters } = installTelemetryMock(dashboardDefinition(SIX));
    renderBoard({ profile: "kubernetes-service-v1" });
    await waitFor(() => expect(counters.started).toBe(MAX_CONCURRENT_QUERIES));
    // Provider is "slow" (nothing resolved) — no more than 3 in flight:
    expect(counters.maxInFlight).toBe(MAX_CONCURRENT_QUERIES);
    await flushPending(pending);
    expect(counters.maxInFlight).toBeLessThanOrEqual(MAX_CONCURRENT_QUERIES);
  });

  it("a five-query project dashboard loads fully without self-inflicted 429s", async () => {
    const { pending, counters } = installTelemetryMock(dashboardDefinition(FIVE));
    renderBoard();
    await waitFor(() => expect(counters.started).toBeGreaterThan(0));
    await flushPending(pending);
    await waitFor(() => expect(counters.started).toBe(FIVE.length));
    expect(counters.maxInFlight).toBeLessThanOrEqual(MAX_CONCURRENT_QUERIES);
    await waitFor(() =>
      expect(screen.getAllByText(/req\/s/).length).toBeGreaterThanOrEqual(FIVE.length),
    );
    expect(screen.queryByText(/query limit reached/i)).not.toBeInTheDocument();
  });

  it("a six-query kubernetes service dashboard loads fully", async () => {
    const { pending, counters } = installTelemetryMock(dashboardDefinition(SIX));
    renderBoard({ profile: "kubernetes-service-v1" });
    await waitFor(() => expect(counters.started).toBeGreaterThan(0));
    await flushPending(pending);
    await waitFor(() => expect(counters.started).toBe(SIX.length));
    expect(counters.maxInFlight).toBeLessThanOrEqual(MAX_CONCURRENT_QUERIES);
    expect(screen.queryByText(/query limit reached/i)).not.toBeInTheDocument();
  });

  it("rapid 1h → 24h → 7d changes abort each stale generation's requests", async () => {
    // CLIENT-SCHEDULER evidence only: this proves the browser aborts its
    // fetches and starts nothing from stale queues. Server-side broker
    // cancellation, provider cleanup, and Redis lease release are proven
    // separately by the API disconnect integration test and the real-HTTP
    // E2E cancellation scenario — a mock counter here is NOT that proof.
    const { pending, counters } = installTelemetryMock(dashboardDefinition(FIVE));
    const view = renderBoard({ preset: "1h" });
    await waitFor(() => expect(pending.length).toBe(MAX_CONCURRENT_QUERIES));
    const generationOneSignals = pending.map((entry) => entry.signal);
    const startedBeforeFirstSwitch = counters.started;

    const rerenderWith = (preset: "24h" | "7d") =>
      view.rerender(
        <SessionProvider>
          <DashboardRenderer
            templateKey="test-board-v1"
            scopeType="service"
            scopeId="s1"
            preset={preset}
            profile="fastapi-v1"
          />
        </SessionProvider>,
      );

    rerenderWith("24h");
    await waitFor(() => {
      for (const signal of generationOneSignals) expect(signal?.aborted).toBe(true);
    });
    await waitFor(() => expect(pending.length).toBe(MAX_CONCURRENT_QUERIES));
    const generationTwoSignals = pending.map((entry) => entry.signal);
    // Generation 1's queued remainder (5 - 3 = 2 jobs) never started:
    expect(counters.started).toBe(startedBeforeFirstSwitch + MAX_CONCURRENT_QUERIES);

    rerenderWith("7d");
    await waitFor(() => {
      for (const signal of generationTwoSignals) expect(signal?.aborted).toBe(true);
    });
    await waitFor(() => expect(pending.length).toBe(MAX_CONCURRENT_QUERIES));
    // Client concurrency stayed bounded across all three generations:
    expect(counters.maxInFlight).toBeLessThanOrEqual(MAX_CONCURRENT_QUERIES);

    // The final generation completes fully.
    await flushPending(pending);
    await waitFor(() => expect(screen.queryByText(/query limit reached/i)).toBeNull());
  });

  it("a stale generation's late response cannot write newer state", async () => {
    const { pending } = installTelemetryMock(dashboardDefinition(["t.one"]));
    const view = renderBoard({ scopeId: "scope-old" });
    await waitFor(() => expect(pending.length).toBe(1));
    const old = pending.shift();

    view.rerender(
      <SessionProvider>
        <DashboardRenderer
          templateKey="test-board-v1"
          scopeType="service"
          scopeId="scope-new"
          preset="24h"
          profile="fastapi-v1"
        />
      </SessionProvider>,
    );
    await waitFor(() => expect(pending.length).toBe(1));
    // The OLD scope's response arrives late — the widget must stay loading
    // until the NEW scope's own response lands.
    await act(async () => {
      old?.resolve();
      await Promise.resolve();
    });
    expect(screen.queryByText(/req\/s/)).not.toBeInTheDocument();
    await flushPending(pending);
    await waitFor(() =>
      expect(screen.getAllByText(/req\/s/).length).toBeGreaterThanOrEqual(1),
    );
  });

  it("unmount aborts in-flight work and starts nothing from the queue", async () => {
    const { pending, counters } = installTelemetryMock(dashboardDefinition(SIX));
    const view = renderBoard({ profile: "kubernetes-service-v1" });
    await waitFor(() => expect(counters.started).toBe(MAX_CONCURRENT_QUERIES));
    const inFlight = [...pending];
    pending.length = 0;
    view.unmount();
    for (const entry of inFlight) expect(entry.signal?.aborted).toBe(true);
    // Resolving the aborted work must not start the queued remainder:
    await act(async () => {
      await Promise.resolve();
    });
    expect(counters.started).toBe(MAX_CONCURRENT_QUERIES);
  });

  it("a real backend 429 renders as throttled, never as provider unavailability", async () => {
    const { pending } = installTelemetryMock(dashboardDefinition(["t.one"]));
    renderBoard();
    await waitFor(() => expect(pending.length).toBe(1));
    await act(async () => {
      pending.shift()?.resolve(429);
      await Promise.resolve();
    });
    await waitFor(() =>
      expect(screen.getByText(/query limit reached/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/telemetry source unavailable/i)).not.toBeInTheDocument();
    expect(screen.getByText(/ref: ref-429/)).toBeInTheDocument();
  });
});

import { THROTTLE_RETRY_DELAY_MS } from "@/components/telemetry/DashboardRenderer";

async function flushMicrotasks() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("throttle retries stay inside the concurrency budget (fake timers)", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("retry timers wake the pump but can never exceed the budget", async () => {
    vi.useFakeTimers();
    const { pending, counters } = installTelemetryMock(dashboardDefinition(SIX));
    renderBoard({ profile: "kubernetes-service-v1" });
    await flushMicrotasks();
    expect(counters.started).toBe(MAX_CONCURRENT_QUERIES);
    const firstThree = pending.splice(0, MAX_CONCURRENT_QUERIES);
    const throttledTemplates = firstThree.map((entry) => entry.template);

    // All three answered 429 → three retry timers armed; the scheduler
    // starts the next three NORMAL queue jobs (slow, left unresolved).
    for (const entry of firstThree) {
      await act(async () => {
        entry.resolve(429);
        await Promise.resolve();
      });
    }
    await flushMicrotasks();
    expect(counters.started).toBe(6);
    expect(counters.inFlight).toBe(MAX_CONCURRENT_QUERIES);

    // Retry delay elapses while all three slots are BUSY: the timers only
    // re-enqueue — not a single retry may start.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(THROTTLE_RETRY_DELAY_MS + 10);
    });
    expect(counters.started).toBe(6);
    expect(counters.inFlight).toBe(MAX_CONCURRENT_QUERIES);
    expect(counters.maxInFlight).toBe(MAX_CONCURRENT_QUERIES);

    // Completing ONE slow query frees exactly one slot → exactly one retry
    // enters it.
    await act(async () => {
      pending.shift()?.resolve();
      await Promise.resolve();
    });
    await flushMicrotasks();
    expect(counters.started).toBe(7);
    expect(counters.inFlight).toBe(MAX_CONCURRENT_QUERIES);

    // Drain everything (retries succeed).
    while (pending.length > 0) {
      await act(async () => {
        pending.shift()?.resolve();
        await Promise.resolve();
      });
      await flushMicrotasks();
    }
    // Each throttled template got exactly ONE automatic retry (2 starts):
    for (const template of throttledTemplates) {
      expect(counters.startsByTemplate[template]).toBe(2);
    }
    // ...and never a second one, even after another retry-delay window:
    await act(async () => {
      await vi.advanceTimersByTimeAsync(THROTTLE_RETRY_DELAY_MS + 10);
    });
    await flushMicrotasks();
    expect(counters.started).toBe(9); // 6 initial slots + 3 retries, nothing more
    expect(counters.inFlight).toBe(0);
    expect(counters.maxInFlight).toBe(MAX_CONCURRENT_QUERIES);
    expect(vi.getTimerCount()).toBe(0); // no pending retry timers or queued work
  });

  it("a 429'd template that gets throttled again is not auto-retried twice", async () => {
    vi.useFakeTimers();
    const { pending, counters } = installTelemetryMock(dashboardDefinition(["t.one"]));
    renderBoard();
    await flushMicrotasks();
    await act(async () => {
      pending.shift()?.resolve(429);
      await Promise.resolve();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(THROTTLE_RETRY_DELAY_MS + 10);
    });
    await flushMicrotasks();
    expect(counters.started).toBe(2); // the single automatic retry started
    await act(async () => {
      pending.shift()?.resolve(429); // the retry is throttled again
      await Promise.resolve();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(THROTTLE_RETRY_DELAY_MS + 10);
    });
    await flushMicrotasks();
    expect(counters.started).toBe(2); // no second automatic retry, ever
    expect(vi.getTimerCount()).toBe(0);
  });

  it("unmount clears pending retry timers — advancing time starts nothing", async () => {
    vi.useFakeTimers();
    const { pending, counters } = installTelemetryMock(dashboardDefinition(["t.one"]));
    const view = renderBoard();
    await flushMicrotasks();
    await act(async () => {
      pending.shift()?.resolve(429);
      await Promise.resolve();
    });
    view.unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(THROTTLE_RETRY_DELAY_MS * 2);
    });
    expect(counters.started).toBe(1); // the retry never fired
  });

  it("a stale generation's retry cannot leak into the next range", async () => {
    vi.useFakeTimers();
    const { pending, counters } = installTelemetryMock(dashboardDefinition(["t.one"]));
    const view = renderBoard({ preset: "1h" });
    await flushMicrotasks();
    await act(async () => {
      pending.shift()?.resolve(429);
      await Promise.resolve();
    });
    // Range changes BEFORE the retry timer fires:
    view.rerender(
      <SessionProvider>
        <DashboardRenderer
          templateKey="test-board-v1"
          scopeType="service"
          scopeId="s1"
          preset="7d"
          profile="fastapi-v1"
        />
      </SessionProvider>,
    );
    await flushMicrotasks();
    expect(counters.started).toBe(2); // old initial + new generation's own query
    await act(async () => {
      await vi.advanceTimersByTimeAsync(THROTTLE_RETRY_DELAY_MS * 2);
    });
    await flushMicrotasks();
    expect(counters.started).toBe(2); // the stale retry never started
    await act(async () => {
      pending.shift()?.resolve();
      await Promise.resolve();
    });
  });
});
