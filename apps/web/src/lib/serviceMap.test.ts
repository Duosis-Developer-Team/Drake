import { describe, expect, it } from "vitest";

import type { GraphEdge, GraphNode } from "@/lib/dashboards";
import { flowSeconds, layoutServiceMap, neighbourhood, perMinute } from "@/lib/serviceMap";

const node = (id: string, kind: GraphNode["kind"], requests = 100): GraphNode => ({
  id,
  kind,
  label: id,
  requests,
  p95: 120,
  codes: null,
  series: [1, 2, 3],
  slow: false,
  longLived: false,
});
const edge = (from: string, to: string, kind: GraphEdge["kind"] = "call", count = 10): GraphEdge => ({
  from,
  to,
  kind,
  count,
  p95: null,
});

describe("service map layout", () => {
  const graph = {
    nodes: [
      node("users", "users", 3000),
      node("frontend", "service", 1000),
      node("core", "service", 1100),
      node("auth", "service", 800),
      node("mcp", "service", 60),
      node("db:pg:auth", "database", 30000),
      node("db:pg:core", "database", 14000),
    ],
    edges: [
      edge("users", "frontend", "request", 1000),
      edge("users", "core", "request", 1100),
      edge("users", "mcp", "request", 60),
      edge("core", "auth", "call", 1100),
      edge("frontend", "auth", "call", 110),
      edge("auth", "db:pg:auth", "query", 30000),
      edge("auth", "db:pg:core", "query", 14000),
    ],
  };

  it("puts users first, callees after callers, databases last", () => {
    const { placed } = layoutServiceMap(graph, 1000);
    const x = (id: string) => placed.get(id)!.x;
    expect(x("users")).toBeLessThan(x("core"));
    expect(x("core")).toBeLessThan(x("auth"));
    expect(x("auth")).toBeLessThan(x("db:pg:auth"));
    expect(x("db:pg:auth")).toEqual(x("db:pg:core"));
  });

  it("never lets two nodes in one column overlap, labels included", () => {
    const { placed } = layoutServiceMap(graph, 1000);
    const byColumn = new Map<number, { y: number; r: number }[]>();
    for (const p of placed.values()) byColumn.set(p.column, [...(byColumn.get(p.column) ?? []), p]);
    for (const list of byColumn.values()) {
      const sorted = [...list].sort((a, b) => a.y - b.y);
      for (let i = 1; i < sorted.length; i++) {
        // ring + name + latency below each node need ~50px past the radius
        expect(sorted[i].y - sorted[i - 1].y).toBeGreaterThan(sorted[i].r + sorted[i - 1].r + 50);
      }
    }
  });

  it("keeps every node inside the drawing", () => {
    const { placed, width, height } = layoutServiceMap(graph, 900);
    for (const p of placed.values()) {
      expect(p.x - p.r).toBeGreaterThan(0);
      expect(p.x + p.r).toBeLessThan(width);
      expect(p.y - p.r).toBeGreaterThan(0);
      expect(p.y + p.r).toBeLessThan(height);
    }
  });

  it("survives a call cycle", () => {
    const cyclic = {
      nodes: [node("a", "service"), node("b", "service")],
      edges: [edge("a", "b"), edge("b", "a")],
    };
    const { placed } = layoutServiceMap(cyclic, 800);
    expect(placed.size).toBe(2);
  });

  it("draws a neighbourhood of one service and its direct links only", () => {
    const local = neighbourhood(graph, "auth");
    expect(local.nodes.map((n) => n.id).sort()).toEqual(["core", "auth", "db:pg:auth", "db:pg:core", "frontend"].sort());
    expect(local.edges.every((e) => e.from === "auth" || e.to === "auth")).toBe(true);
  });
});

describe("service map numbers", () => {
  it("averages requests per minute over the buckets the series covers", () => {
    // 3 hourly buckets, 600 requests in total → 200 per hour → 3.33 per minute
    const n = { ...node("x", "service"), series: [100, 200, 300] };
    expect(perMinute(n, 3600)).toBeCloseTo(600 / 60 / 3);
  });

  it("flows faster on busier edges, within bounds", () => {
    expect(flowSeconds(10_000, 10_000)).toBeLessThan(flowSeconds(10, 10_000));
    expect(flowSeconds(0, 10_000)).toBeLessThanOrEqual(4);
    expect(flowSeconds(10_000, 10_000)).toBeGreaterThanOrEqual(0.8);
  });
});
