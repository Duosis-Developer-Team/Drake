import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";
import { parse } from "yaml";

import {
  FORBIDDEN_METRIC_LABELS,
  checkMetricLabels,
  type MetricCatalog,
} from "../src/metric-policy.js";

const CATALOG_PATH = join(__dirname, "..", "fixtures", "catalog", "metric-catalog.yaml");

describe("forbidden metric-label guard", () => {
  it("the example metric catalog is clean", () => {
    const catalog = parse(readFileSync(CATALOG_PATH, "utf8")) as MetricCatalog;
    expect(catalog.metrics.length).toBeGreaterThan(0);
    expect(checkMetricLabels(catalog)).toEqual([]);
  });

  it("rejects PII/unbounded labels in allowedLabels", () => {
    const catalog: MetricCatalog = {
      apiVersion: "drake.duosis.com/v1alpha1",
      kind: "MetricCatalog",
      metrics: [
        { key: "bad.metric", allowedLabels: ["project", "user_id"] },
        { key: "worse.metric", allowedLabels: ["email", "tenant_name"] },
      ],
    };
    const violations = checkMetricLabels(catalog);
    expect(violations).toHaveLength(3);
    expect(violations[0]).toContain("user_id");
  });

  it("rejects a label that is simultaneously allowed and forbidden", () => {
    const catalog: MetricCatalog = {
      apiVersion: "drake.duosis.com/v1alpha1",
      kind: "MetricCatalog",
      metrics: [
        {
          key: "conflicted.metric",
          allowedLabels: ["route"],
          forbiddenLabels: ["route"],
        },
      ],
    };
    expect(checkMetricLabels(catalog)).toHaveLength(1);
  });

  it("keeps the global denylist meaningful", () => {
    for (const label of ["email", "user_id", "trace_id", "raw_path", "tenant_name"]) {
      expect(FORBIDDEN_METRIC_LABELS).toContain(label);
    }
  });
});
