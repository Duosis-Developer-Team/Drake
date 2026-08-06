import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
  FORBIDDEN_METRIC_LABELS,
  checkMetricLabels,
  type MetricCatalog,
} from "../src/metric-policy.js";

const CATALOG_PATH = join(__dirname, "..", "registry", "metric-catalog.json");

describe("forbidden metric-label guard", () => {
  it("the authoritative metric catalog is clean", () => {
    const catalog = JSON.parse(readFileSync(CATALOG_PATH, "utf8")) as MetricCatalog;
    expect(catalog.metrics.length).toBeGreaterThan(0);
    expect(checkMetricLabels(catalog)).toEqual([]);
  });

  it("rejects PII/unbounded labels in any allowed-label field", () => {
    const catalog: MetricCatalog = {
      apiVersion: "drake.duosis.com/v1alpha1",
      kind: "MetricCatalog",
      metrics: [
        { key: "bad.metric", allowedInputLabels: ["project", "user_id"] },
        { key: "worse.metric", allowedOutputLabels: ["email", "tenant_name"] },
        { key: "legacy.metric", allowedLabels: ["trace_id"] },
      ],
    };
    const violations = checkMetricLabels(catalog);
    expect(violations).toHaveLength(4);
    expect(violations[0]).toContain("user_id");
  });

  it("rejects raw route labels; route_template is the only canonical route label", () => {
    const catalog: MetricCatalog = {
      apiVersion: "drake.duosis.com/v1alpha1",
      kind: "MetricCatalog",
      metrics: [
        { key: "route.metric", allowedInputLabels: ["route", "path", "raw_path"] },
        { key: "good.metric", allowedOutputLabels: ["route_template"] },
      ],
    };
    const violations = checkMetricLabels(catalog);
    expect(violations).toHaveLength(3);
    expect(violations.join("\n")).not.toContain("route_template");
  });

  it("rejects a label that is simultaneously allowed and forbidden", () => {
    const catalog: MetricCatalog = {
      apiVersion: "drake.duosis.com/v1alpha1",
      kind: "MetricCatalog",
      metrics: [
        {
          key: "conflicted.metric",
          allowedInputLabels: ["status_class"],
          forbiddenLabels: ["status_class"],
        },
      ],
    };
    expect(checkMetricLabels(catalog)).toHaveLength(1);
  });

  it("keeps the global denylist meaningful", () => {
    for (const label of [
      "email",
      "user_id",
      "trace_id",
      "raw_path",
      "route",
      "path",
      "url",
      "query",
      "query_string",
      "ip",
      "client_ip",
      "sql",
      "tenant_name",
      "error_message",
      "filename",
      "artifact_id",
      "git_sha",
    ]) {
      expect(FORBIDDEN_METRIC_LABELS).toContain(label);
    }
  });
});
