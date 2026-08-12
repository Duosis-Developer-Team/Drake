/**
 * The chart layer's contract.
 *
 * Two halves, and the split matters:
 *
 * The ADAPTER is where a monitoring tab left open overnight leaks. An ECharts
 * instance owns a canvas, a render loop and its own listeners, and a route
 * change that disposes the React tree but not the instance leaks all three.
 * These tests drive the real lifecycle against a stubbed `echarts/core` and
 * assert init/resize/dispose pairing.
 *
 * The FRAME is where a chart tells the truth. A non-success state renders
 * INSTEAD of the plot, never behind it: an axis pair with no series reads as
 * "zero", and no data is not zero.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** One fake instance per `init`, recording everything done to it. */
interface FakeChart {
  disposed: boolean;
  resizes: number;
  options: Record<string, unknown>[];
}

const charts: FakeChart[] = [];
const resizeObservers: { callback: ResizeObserverCallback; disconnected: boolean }[] = [];

vi.mock("echarts/core", () => ({
  use: vi.fn(),
  init: vi.fn(() => {
    const chart: FakeChart = { disposed: false, resizes: 0, options: [] };
    charts.push(chart);
    return {
      setOption: (option: Record<string, unknown>) => chart.options.push(option),
      resize: () => {
        chart.resizes += 1;
      },
      dispose: () => {
        chart.disposed = true;
      },
      isDisposed: () => chart.disposed,
    };
  }),
}));
vi.mock("echarts/charts", () => ({
  BarChart: {},
  CustomChart: {},
  HeatmapChart: {},
  LineChart: {},
  PieChart: {},
}));
vi.mock("echarts/components", () => ({
  DatasetComponent: {},
  GridComponent: {},
  LegendComponent: {},
  MarkAreaComponent: {},
  MarkLineComponent: {},
  TooltipComponent: {},
  VisualMapComponent: {},
}));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

const { EChart } = await import("@/components/charts/echarts");
const { ChartFrame } = await import("@/components/charts/ChartFrame");
const { TimeSeriesChart } = await import("@/components/charts/TimeSeriesChart");

beforeEach(() => {
  charts.length = 0;
  resizeObservers.length = 0;
  document.documentElement.classList.remove("dark");

  class FakeResizeObserver {
    constructor(callback: ResizeObserverCallback) {
      resizeObservers.push({ callback, disconnected: false });
      this.index = resizeObservers.length - 1;
    }
    private index: number;
    observe() {}
    unobserve() {}
    disconnect() {
      resizeObservers[this.index].disconnected = true;
    }
  }
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    })),
  );
});

afterEach(() => vi.unstubAllGlobals());

function build() {
  return { series: [] };
}

describe("ECharts adapter lifecycle", () => {
  it("creates exactly one instance and applies an option", async () => {
    render(<EChart build={build} deps={[]} height={200} ariaLabel="test chart" />);
    await waitFor(() => expect(charts).toHaveLength(1));
    expect(charts[0].options.length).toBeGreaterThan(0);
  });

  it("disposes the instance and disconnects the observer on unmount", async () => {
    const view = render(<EChart build={build} deps={[]} height={200} ariaLabel="test chart" />);
    await waitFor(() => expect(charts).toHaveLength(1));
    view.unmount();
    // Both, not one: the canvas and the observer are separate leaks.
    expect(charts[0].disposed).toBe(true);
    expect(resizeObservers[0].disconnected).toBe(true);
  });

  it("does not recreate the canvas when only the data changes", async () => {
    const view = render(<EChart build={build} deps={[1]} height={200} ariaLabel="test chart" />);
    await waitFor(() => expect(charts).toHaveLength(1));
    const optionsBefore = charts[0].options.length;
    view.rerender(<EChart build={build} deps={[2]} height={200} ariaLabel="test chart" />);
    await waitFor(() => expect(charts[0].options.length).toBeGreaterThan(optionsBefore));
    expect(charts).toHaveLength(1);
    expect(charts[0].disposed).toBe(false);
  });

  it("ignores a resize to a zero-sized container", async () => {
    // A panel animating open reports width 0 for a frame; resizing to it makes
    // ECharts drop the canvas and it never comes back.
    render(<EChart build={build} deps={[]} height={200} ariaLabel="test chart" />);
    await waitFor(() => expect(charts).toHaveLength(1));
    resizeObservers[0].callback([], {} as ResizeObserver);
    expect(charts[0].resizes).toBe(0);
  });

  it("re-applies the option when the theme class flips, without refetching", async () => {
    render(<EChart build={build} deps={[]} height={200} ariaLabel="test chart" />);
    await waitFor(() => expect(charts).toHaveLength(1));
    const before = charts[0].options.length;
    document.documentElement.classList.add("dark");
    await waitFor(() => expect(charts[0].options.length).toBeGreaterThan(before));
    expect(charts).toHaveLength(1);
  });

  it("disables animation when the reader asks for reduced motion", async () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockImplementation((query: string) => ({
        matches: query.includes("reduced-motion"),
        media: query,
        addEventListener: () => {},
        removeEventListener: () => {},
      })),
    );
    const spy = vi.fn((_tokens: unknown, _animate: boolean) => ({ series: [] }));
    render(<EChart build={spy} deps={[]} height={200} ariaLabel="test chart" />);
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy.mock.calls[0][1], "animation should be off").toBe(false);
  });

  it("disables animation in deterministic mode, for comparable screenshots", async () => {
    const spy = vi.fn((_tokens: unknown, _animate: boolean) => ({ series: [] }));
    render(<EChart build={spy} deps={[]} height={200} deterministic ariaLabel="test chart" />);
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy.mock.calls[0][1], "animation should be off").toBe(false);
  });

  it("carries an accessible name", async () => {
    render(<EChart build={build} deps={[]} height={200} ariaLabel="Request rate over 24h" />);
    expect(await screen.findByRole("img", { name: /request rate over 24h/i })).toBeInTheDocument();
  });
});

describe("ChartFrame states", () => {
  const base = { title: "Request rate", unit: "requests_per_second" as const };

  it("renders the plot only when there is data", () => {
    render(
      <ChartFrame {...base} status="no-data">
        <p>plot</p>
      </ChartFrame>,
    );
    expect(screen.getByTestId("state-no-data")).toBeInTheDocument();
    // An empty axis pair reads as zero, so the plot must not be behind it.
    expect(screen.queryByText("plot")).not.toBeInTheDocument();
  });

  it("keeps denied, not-configured, no-data and error visually distinct", () => {
    for (const [status, testId] of [
      ["denied", "state-permission-denied"],
      ["not-configured", "state-not-configured"],
      ["no-data", "state-no-data"],
      ["unknown", "state-unknown"],
      ["error", "state-error"],
    ] as const) {
      const view = render(
        <ChartFrame {...base} status={status}>
          <p>plot</p>
        </ChartFrame>,
      );
      expect(screen.getByTestId(testId)).toBeInTheDocument();
      view.unmount();
    }
  });

  it("shows the stale banner above the plot, with the instant measured", () => {
    render(
      <ChartFrame {...base} status="ready" freshness="stale" asOf="2026-08-06T00:00:00Z">
        <p>plot</p>
      </ChartFrame>,
    );
    const banner = screen.getByTestId("state-stale");
    expect(banner).toHaveTextContent(/last successful update/i);
    expect(banner.compareDocumentPosition(screen.getByText("plot"))).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });

  it("says when the server widened the step rather than hiding it", () => {
    render(
      <ChartFrame
        {...base}
        status="ready"
        window={{ from: "2026-08-10T00:00:00Z", to: "2026-08-11T00:00:00Z", stepSeconds: 300, stepAdjusted: true }}
      >
        <p>plot</p>
      </ChartFrame>,
    );
    expect(screen.getByText(/step widened to 300s/i)).toBeInTheDocument();
  });
});

describe("TimeSeriesChart", () => {
  const points: [number, number | null][] = [
    [1_760_000_000_000, 1],
    [1_760_000_060_000, null],
    [1_760_000_120_000, 3],
  ];

  it("draws a null sample as a gap and says so, rather than plotting zero", async () => {
    render(
      <TimeSeriesChart
        title="Request rate"
        unit="requests_per_second"
        status="ready"
        series={[{ name: "api", points }]}
      />,
    );
    const summary = await screen.findByTestId("chart-summary");
    expect(summary).toHaveTextContent(/1 missing sample shown as gaps/i);
    // The option never connects across the gap.
    await waitFor(() => expect(charts.length).toBeGreaterThan(0));
    const option = charts[0].options.at(-1) as { series: { connectNulls?: boolean }[] };
    expect(option.series[0].connectNulls).toBe(false);
  });

  it("offers the same numbers as a table, with the gap as a dash", async () => {
    render(
      <TimeSeriesChart
        title="Request rate"
        unit="requests_per_second"
        status="ready"
        series={[{ name: "api", points }]}
      />,
    );
    expect(await screen.findByText(/view as table/i)).toBeInTheDocument();
    expect(screen.getByRole("table")).toHaveTextContent("—");
  });

  it("gives each series a distinct colour and dash, resolved not as a var()", async () => {
    render(
      <TimeSeriesChart
        title="Latency"
        unit="seconds"
        status="ready"
        series={[
          { name: "p50", points },
          { name: "p95", points },
        ]}
      />,
    );
    await waitFor(() => expect(charts.length).toBeGreaterThan(0));
    const option = charts[0].options.at(-1) as {
      series: { lineStyle: { color: string; type: unknown } }[];
    };
    const colours = option.series.map((entry) => entry.lineStyle.color);
    expect(new Set(colours).size).toBe(2);
    for (const colour of colours) {
      // The canvas renderer cannot read custom properties.
      expect(colour).not.toMatch(/^var\(/);
      expect(colour).toMatch(/^#[0-9a-f]{6}$/i);
    }
    expect(option.series[0].lineStyle.type).not.toEqual(option.series[1].lineStyle.type);
  });

  it("caps the drawn series and reports what it did not draw", async () => {
    const many = Array.from({ length: 9 }, (_, index) => ({
      name: `series-${index}`,
      points,
    }));
    render(
      <TimeSeriesChart title="Fan out" unit="count" status="ready" series={many} />,
    );
    const summary = await screen.findByTestId("chart-summary");
    expect(summary).toHaveTextContent(/3 further series not drawn/i);
  });
});
