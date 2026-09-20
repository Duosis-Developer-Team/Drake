import { describe, expect, it } from "vitest";

import { buildVerdict } from "@/lib/view-models/verdict";
import type { AttentionItem } from "@/lib/overview";
import type { Resource } from "@/lib/useResource";

function resource<T>(data: T | null): Resource<T> {
  return {
    data,
    loading: false,
    refreshing: false,
    error: null,
    denied: false,
    fetchedAt: null,
    reload: () => {},
  } as unknown as Resource<T>;
}

function item(overrides: Partial<AttentionItem>): AttentionItem {
  return {
    key: "k1",
    tone: "critical",
    state: "Open incident",
    subject: "checkout",
    context: "alpha / production",
    href: "/incidents/1",
    asOf: "2026-08-10T00:00:00Z",
    origin: "incident",
    ...overrides,
  };
}

const SOURCES = [
  { label: "Incidents", resource: resource({}) },
  { label: "Alerts", resource: resource({}) },
  { label: "Clusters", resource: resource(null) },
];

describe("buildVerdict", () => {
  it("headline: nothing flagged names sources answered, not health", () => {
    const verdict = buildVerdict([], SOURCES);
    expect(verdict.headline).toMatch(/nothing is currently flagged/i);
    expect(verdict.headline).toMatch(/2 of 3 sources answered/i);
    expect(verdict.criticalCount).toBe(0);
    expect(verdict.warningCount).toBe(0);
  });

  it("headline: critical count leads when any critical item exists", () => {
    const verdict = buildVerdict(
      [item({ tone: "critical" }), item({ key: "k2", tone: "warning" })],
      SOURCES,
    );
    expect(verdict.headline).toBe("1 critical path needs attention");
    expect(verdict.criticalCount).toBe(1);
    expect(verdict.warningCount).toBe(1);
  });

  it("headline: warning-only, plural count", () => {
    const verdict = buildVerdict(
      [item({ key: "k1", tone: "warning" }), item({ key: "k2", tone: "warning" })],
      SOURCES,
    );
    expect(verdict.headline).toBe("2 warnings need attention");
  });

  it("affected scope de-duplicates by project/service/cluster, not by row", () => {
    const verdict = buildVerdict(
      [
        item({ key: "i1", origin: "incident", context: "alpha / production" }),
        item({ key: "i2", origin: "incident", context: "alpha / staging" }),
        item({ key: "s1", origin: "service", subject: "checkout", context: "beta / production" }),
        item({ key: "s2", origin: "service", subject: "checkout", context: "beta / production" }),
        item({ key: "c1", origin: "cluster", subject: "prod-1", context: "cluster connection" }),
        item({ key: "c2", origin: "cluster", subject: "prod-1", context: "cluster inventory" }),
      ],
      SOURCES,
    );
    // "alpha" appears twice (two environments) but is one project.
    expect(verdict.affectedScope.projects).toBe(2); // alpha, beta
    // Same service flagged twice (two rows) is still one service.
    expect(verdict.affectedScope.services).toBe(1);
    // Same cluster flagged for two different reasons is still one cluster.
    expect(verdict.affectedScope.clusters).toBe(1);
  });

  it("alerts contribute to counts but not to affected scope (no per-entity data)", () => {
    const verdict = buildVerdict(
      [item({ key: "a1", origin: "alert", tone: "critical", subject: "Priority 1", context: "alerting" })],
      SOURCES,
    );
    expect(verdict.criticalCount).toBe(1);
    expect(verdict.affectedScope.projects).toBe(0);
    expect(verdict.affectedScope.services).toBe(0);
    expect(verdict.affectedScope.clusters).toBe(0);
  });

  it("oldest suspect evidence ignores items with no asOf and picks the minimum", () => {
    const verdict = buildVerdict(
      [
        item({ key: "k1", subject: "newer", asOf: "2026-08-12T00:00:00Z" }),
        item({ key: "k2", subject: "no-timestamp", asOf: null }),
        item({ key: "k3", subject: "oldest", asOf: "2026-08-01T00:00:00Z" }),
      ],
      SOURCES,
    );
    expect(verdict.oldestSuspectEvidence).toEqual({
      label: "oldest",
      asOf: "2026-08-01T00:00:00Z",
    });
  });

  it("oldest suspect evidence is null when nothing flagged carries a timestamp", () => {
    const verdict = buildVerdict([item({ asOf: null })], SOURCES);
    expect(verdict.oldestSuspectEvidence).toBeNull();
  });

  it("sourcesAnswered/sourcesTotal pass through the sources list unchanged", () => {
    const verdict = buildVerdict([], SOURCES);
    expect(verdict.sourcesAnswered).toBe(2);
    expect(verdict.sourcesTotal).toBe(3);
  });

  it("success/neutral/unknown-toned items never count toward critical or warning", () => {
    const verdict = buildVerdict(
      [item({ tone: "unknown" }), item({ key: "k2", tone: "stale" })],
      SOURCES,
    );
    expect(verdict.criticalCount).toBe(0);
    expect(verdict.warningCount).toBe(0);
    expect(verdict.headline).toMatch(/nothing is currently flagged/i);
  });
});
