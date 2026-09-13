import { describe, expect, it } from "vitest";

import { groupByRootCause, type AttentionItem } from "@/lib/overview";

function item(overrides: Partial<AttentionItem> & Pick<AttentionItem, "key" | "origin">): AttentionItem {
  return {
    tone: "critical",
    state: "state",
    subject: "subject",
    context: "context",
    href: "/",
    asOf: null,
    ...overrides,
  };
}

describe("groupByRootCause", () => {
  it("groups a cluster's agent and inventory rows together", () => {
    const items = [
      item({ key: "cluster-agent:c1", origin: "cluster", context: "cluster connection" }),
      item({ key: "cluster-inventory:c1", origin: "cluster", context: "cluster inventory" }),
    ];
    const groups = groupByRootCause(items);
    expect(groups).toHaveLength(1);
    expect(groups[0]).toHaveLength(2);
  });

  it("never merges unrelated critical items from different clusters", () => {
    const items = [
      item({ key: "cluster-agent:c1", origin: "cluster", subject: "prod-1" }),
      item({ key: "cluster-agent:c2", origin: "cluster", subject: "prod-2" }),
    ];
    const groups = groupByRootCause(items);
    expect(groups).toHaveLength(2);
    expect(groups[0]).toHaveLength(1);
    expect(groups[1]).toHaveLength(1);
  });

  it("never merges rows from different origins that happen to share a subject", () => {
    const items = [
      item({ key: "incident:i1", origin: "incident", subject: "core-api" }),
      item({ key: "service:s1", origin: "service", subject: "core-api" }),
    ];
    const groups = groupByRootCause(items);
    expect(groups).toHaveLength(2);
  });

  it("never merges two integrations of the same type in different scopes", () => {
    const items = [
      item({ key: "integration:telemetry:project:alpha", origin: "integration", subject: "Telemetry" }),
      item({ key: "integration:telemetry:project:beta", origin: "integration", subject: "Telemetry" }),
    ];
    const groups = groupByRootCause(items);
    expect(groups).toHaveLength(2);
  });

  it("preserves input order across groups", () => {
    const items = [
      item({ key: "incident:i1", origin: "incident" }),
      item({ key: "cluster-agent:c1", origin: "cluster" }),
      item({ key: "cluster-inventory:c1", origin: "cluster" }),
      item({ key: "alerts:p1", origin: "alert" }),
    ];
    const groups = groupByRootCause(items);
    expect(groups.map((group) => group[0].key)).toEqual([
      "incident:i1",
      "cluster-agent:c1",
      "alerts:p1",
    ]);
  });

  it("returns an empty list for no items", () => {
    expect(groupByRootCause([])).toEqual([]);
  });
});
