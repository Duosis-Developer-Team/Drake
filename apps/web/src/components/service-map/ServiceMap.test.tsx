import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ServiceMap } from "@/components/service-map/ServiceMap";
import type { GraphEdge, GraphNode } from "@/lib/dashboards";

const node = (id: string, kind: GraphNode["kind"], extra: Partial<GraphNode> = {}): GraphNode => ({
  id,
  kind,
  label: id,
  requests: 1000,
  p95: 120,
  codes: kind === "service" ? { "200": 950, "404": 30, "500": 20 } : null,
  series: [10, 20, 30, 40],
  slow: false,
  longLived: false,
  ...extra,
});

const graph = {
  nodes: [
    node("users", "users"),
    node("b-core", "service", { label: "core-service", p95: 4260, slow: true }),
    node("b-auth", "service", { label: "auth-service", p95: 1500, slow: true }),
    node("db:pg:auth", "database", { label: "auth_db" }),
  ],
  edges: [
    { from: "users", to: "b-core", kind: "request", count: 1157, p95: null },
    { from: "b-core", to: "b-auth", kind: "call", count: 1102, p95: 1500 },
    { from: "b-auth", to: "db:pg:auth", kind: "query", count: 31217, p95: 171 },
  ] satisfies GraphEdge[],
};

describe("ServiceMap", () => {
  it("makes every service a named control that opens on Enter", () => {
    const onOpen = vi.fn();
    render(<ServiceMap graph={graph} bucketSeconds={3600} onOpen={onOpen} />);
    const core = screen.getByRole("button", { name: /core-service: .* per minute, p95 4.26 s\. Open core-service/ });
    fireEvent.keyDown(core, { key: "Enter" });
    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ id: "b-core" }));
    // users and databases are described, not offered as actions
    expect(screen.queryByRole("button", { name: /Users/ })).not.toBeInTheDocument();
    expect(screen.getByRole("img", { name: /auth_db: 1,000 queries/ })).toBeInTheDocument();
  });

  it("labels a slow call with its p95 and lists every edge in the table", () => {
    render(<ServiceMap graph={graph} bucketSeconds={3600} />);
    expect(screen.getAllByText("1,102 · 1.50 s").length).toBeGreaterThan(0);
    const table = screen.getByRole("table");
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(1 + graph.edges.length);
    expect(within(rows[1]).getByText("auth-service")).toBeInTheDocument();
    expect(within(rows[1]).getByText("31,217")).toBeInTheDocument();
  });

  it("does not offer services as controls when nothing can be opened", () => {
    render(<ServiceMap graph={graph} bucketSeconds={3600} />);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });
});
