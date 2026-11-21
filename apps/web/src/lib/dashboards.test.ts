import { describe, expect, it } from "vitest";

import {
  WIDGETS,
  apdex,
  logMakeup,
  moveWidget,
  overviewPath,
  requestsPerMinute,
  sameLayout,
  serviceLines,
  setWidget,
  trafficGraph,
  type Overview,
  type WidgetSlot,
} from "@/lib/dashboards";
import type { ServiceHealthRow } from "@/lib/serviceHealth";

const layout: WidgetSlot[] = WIDGETS.map((key) => ({ key, size: "l", hidden: false }));

function row(id: string, name: string): ServiceHealthRow {
  return {
    environment_service_id: `es-${id}`,
    project_id: "p",
    project_key: "hermes",
    environment_id: "e",
    environment_key: "test",
    service_key: name,
    display_name: null,
    component: null,
    binding: {
      id, lifecycle: "active", namespace: "hermes-test", workload_kind: "Deployment", workload_name: name,
      resolved: true, preset_key: "x", health_policy_key: "y", revision: 1, cluster: { cluster_ref: "c", id: "cid" },
    },
    health: {
      status: "healthy", computed_at: "", newest_sample_at: null, freshness_age_seconds: null, partial: false,
      served_from_last_good: false, reasons: [], availability: {}, stability: {}, resources: {},
    },
  };
}

const ok = <T,>(data: T) => ({ state: "ok" as const, data });
function overview(): Overview {
  return {
    range: "24h", as_of: "", bucket_seconds: 3600, buckets: [1, 2], window: { start: "", end: "" }, bindings: [],
    traffic: ok([
      { binding_id: "core", buckets: [{ t: 1, requests: 60, errors: 0, p95_ms: 120 }, { t: 2, requests: 0, errors: 0, p95_ms: null }] },
      { binding_id: "auth", buckets: [{ t: 1, requests: 120, errors: 1, p95_ms: 1300 }, { t: 2, requests: 60, errors: 0, p95_ms: 80 }] },
    ]),
    latency: ok({ classes: [], slow_from_class: 9, cells: [], totals: [90, 0, 0, 0, 0, 0, 0, 0, 0, 6, 2, 2, 0] }),
    codes: ok([{ binding_id: "core", codes: { "200": 50, "401": 10 } }]),
    routes: ok([]),
    calls: ok([{ from_binding_id: "core", to_binding_id: "auth", calls: 43, errors: 0, p95_ms: 1160 }]),
    databases: ok([
      { binding_id: "auth", system: "postgresql", database: "auth_db", operation: "SELECT auth_db", calls: 200, errors: 0, p95_ms: 70 },
      { binding_id: "auth", system: "postgresql", database: "auth_db", operation: "connect", calls: 90, errors: 0, p95_ms: 110 },
    ]),
    log_lines: ok([{ t: 1, lines: 1000, errors: 300, warnings: 0 }, { t: 2, lines: 1000, errors: 300, warnings: 0 }]),
    log_patterns: ok([
      { pattern: 'FATAL: database "hermes"', lines: 600, severity_number: 21, severity_text: "FATAL", workloads: 1, binding_ids: [] },
      { pattern: "GET /health <n>", lines: 1200, severity_number: 9, severity_text: "INFO", workloads: 2, binding_ids: [] },
    ]),
    findings: { items: [], skipped: [] },
  };
}

describe("layout edits", () => {
  it("moves within bounds and keeps a permutation of the catalog", () => {
    const moved = moveWidget(layout, "findings", -1);
    expect(moved.map((w) => w.key).slice(2, 4)).toEqual(["findings", "traffic_graph"]);
    expect(moveWidget(layout, "overview", -1)).toBe(layout);
    expect(new Set(moved.map((w) => w.key))).toEqual(new Set(WIDGETS));
  });
  it("moves past hidden widgets to the next visible one", () => {
    // findings is hidden between traffic_graph and latency_heatmap.
    const withHidden = setWidget(layout, "findings", { hidden: true });
    const visible = (l: WidgetSlot[]) => l.filter((w) => !w.hidden).map((w) => w.key);
    const up = moveWidget(withHidden, "latency_heatmap", -1);
    expect(visible(up).slice(2, 4)).toEqual(["latency_heatmap", "traffic_graph"]);
    const down = moveWidget(withHidden, "traffic_graph", 1);
    expect(visible(down).slice(2, 4)).toEqual(["latency_heatmap", "traffic_graph"]);
    // Every visible move changes what the page shows.
    expect(visible(up)).not.toEqual(visible(withHidden));
    // Hidden ones at the very end: nothing visible below the last one.
    const tail = setWidget(layout, "logs", { hidden: true });
    expect(moveWidget(tail, "databases", 1)).toBe(tail);
  });
  it("never hides the last visible widget", () => {
    let all = layout;
    for (const key of WIDGETS) all = setWidget(all, key, { hidden: true });
    expect(all.filter((w) => !w.hidden)).toHaveLength(1);
    expect(sameLayout(layout, setWidget(layout, "logs", { size: "s" }))).toBe(false);
  });
});

describe("numbers from the overview read", () => {
  it("asks for sorted unique ids, at most 32", () => {
    expect(overviewPath([], "24h")).toBeNull();
    expect(overviewPath(["b", "a", "b"], "6h")).toBe("/v1/service-health/overview?binding_id=a&binding_id=b&range=6h");
  });
  it("adds requests per minute across services, bucket by bucket", () => {
    expect(requestsPerMinute(overview())).toEqual([3, 1]);
  });
  it("scores Apdex with one second as the threshold", () => {
    const a = apdex(overview().latency.data!);
    expect(a.fast).toBe(90);
    expect(a.tolerable).toBe(8);
    expect(a.slow).toBe(2);
    expect(a.score).toBeCloseTo(0.94);
    expect(apdex(null).score).toBeNull();
  });
  it("splits the logs into health checks, errors and the application", () => {
    expect(logMakeup(overview())).toEqual({ total: 2000, health: 1200, errors: 600, app: 200 });
  });
  it("builds service lines and the traffic graph without double counting calls", () => {
    const lines = serviceLines([row("core", "core-service"), row("auth", "auth-service")], overview());
    const auth = lines.find((l) => l.bindingId === "auth")!;
    expect(auth.requests).toBe(180);
    expect(auth.worstP95).toBe(1300);
    expect(auth.slowBuckets).toBe(1);
    const { nodes, edges } = trafficGraph(lines, overview());
    const fromUsers = edges.find((e) => e.from === "users" && e.to === "auth")!;
    expect(fromUsers.count).toBe(180 - 43);
    expect(edges.find((e) => e.kind === "call")).toMatchObject({ from: "core", to: "auth", count: 43 });
    const db = nodes.find((n) => n.kind === "database")!;
    expect(db.label).toBe("auth_db");
    expect(db.requests).toBe(290);
  });
});
