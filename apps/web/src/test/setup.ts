import { vi } from "vitest";

import "@testing-library/jest-dom/vitest";

// jsdom has no ResizeObserver. Charts (ECharts via components/charts/echarts.tsx)
// observe their container's size in a real browser; in tests a no-op stub is
// enough — no layout ever actually changes under jsdom.
if (typeof globalThis.ResizeObserver === "undefined") {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  globalThis.ResizeObserver = ResizeObserverStub;
}

// jsdom implements neither <canvas> 2D contexts nor ResizeObserver-driven
// layout, so a real ECharts instance throws the moment it tries to paint.
// A screen test that renders a page which happens to mount a live chart (a
// gauge in a hero, a sparkline in a summary) should still be able to assert
// on everything around it — src/test/charts.test.tsx already stubs these
// same modules to test the chart adapter itself in isolation; this global
// stub gives every OTHER test that same safety net by default. A test file
// that defines its own `vi.mock` for one of these modules overrides this.
vi.mock("echarts/core", () => ({
  use: vi.fn(),
  init: vi.fn(() => ({
    setOption: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
    isDisposed: () => false,
  })),
}));
vi.mock("echarts/charts", () => ({
  BarChart: {},
  CustomChart: {},
  GaugeChart: {},
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
