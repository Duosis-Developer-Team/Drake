import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
  checkRegistryIntegrity,
  registryContentHash,
  type TelemetryRegistry,
} from "../src/registry.js";
import { validateContent } from "../src/validator.js";

const REGISTRY_DIR = join(__dirname, "..", "registry");
const INVALID_DIR = join(__dirname, "..", "fixtures", "registry-invalid");

function loadRegistry(): TelemetryRegistry {
  return {
    metricCatalog: JSON.parse(readFileSync(join(REGISTRY_DIR, "metric-catalog.json"), "utf8")),
    queryTemplates: JSON.parse(
      readFileSync(join(REGISTRY_DIR, "query-templates.json"), "utf8"),
    ),
    dashboardTemplates: JSON.parse(
      readFileSync(join(REGISTRY_DIR, "dashboard-templates.json"), "utf8"),
    ),
  };
}

describe("authoritative registry schemas", () => {
  it.each([
    ["metric-catalog.json", "metric-catalog"],
    ["query-templates.json", "query-template"],
    ["dashboard-templates.json", "dashboard-template"],
  ] as const)("%s conforms to its schema", (file, schema) => {
    const result = validateContent(readFileSync(join(REGISTRY_DIR, file), "utf8"), schema);
    expect(result.issues).toEqual([]);
    expect(result.valid).toBe(true);
  });

  it("rejects unsupported metric type/unit at the schema boundary", () => {
    const result = validateContent(
      readFileSync(join(INVALID_DIR, "invalid-unit.json"), "utf8"),
      "metric-catalog",
    );
    expect(result.valid).toBe(false);
  });

  it("rejects unknown fields (additionalProperties: false everywhere)", () => {
    const registry = loadRegistry();
    const tampered = {
      ...registry.metricCatalog,
      metrics: [{ ...registry.metricCatalog.metrics[0], providerUrl: "http://x" }],
    };
    const result = validateContent(JSON.stringify(tampered), "metric-catalog");
    expect(result.valid).toBe(false);
  });
});

describe("registry cross-reference integrity", () => {
  it("the authoritative registry is coherent", () => {
    expect(checkRegistryIntegrity(loadRegistry())).toEqual([]);
  });

  it("every dashboard widget resolves to a query template and every template to a metric", () => {
    const registry = loadRegistry();
    const templateKeys = new Set(registry.queryTemplates.templates.map((t) => t.key));
    for (const dashboard of registry.dashboardTemplates.dashboards) {
      for (const section of dashboard.sections) {
        for (const widget of section.widgets) {
          expect(templateKeys.has(widget.queryTemplateKey)).toBe(true);
        }
      }
    }
  });

  it("rejects duplicate metric key/version pairs", () => {
    const registry = loadRegistry();
    registry.metricCatalog = JSON.parse(
      readFileSync(join(INVALID_DIR, "duplicate-metric.json"), "utf8"),
    );
    const violations = checkRegistryIntegrity(registry);
    expect(violations.join("\n")).toContain("duplicate");
  });

  it("rejects templates referencing unknown metrics", () => {
    const registry = loadRegistry();
    registry.queryTemplates = JSON.parse(
      readFileSync(join(INVALID_DIR, "unknown-metric-ref.json"), "utf8"),
    );
    const violations = checkRegistryIntegrity(registry);
    expect(violations.join("\n")).toContain("unknown metric");
  });

  it("rejects raw route labels in the metric catalog", () => {
    const registry = loadRegistry();
    registry.metricCatalog = JSON.parse(
      readFileSync(join(INVALID_DIR, "route-label.json"), "utf8"),
    );
    const violations = checkRegistryIntegrity(registry);
    expect(violations.join("\n")).toContain('forbidden label "route"');
    expect(violations.join("\n")).toContain('forbidden label "path"');
  });

  it("rejects snapshot metrics behind provider query templates", () => {
    const registry = loadRegistry();
    registry.queryTemplates.templates[0] = {
      ...registry.queryTemplates.templates[0],
      metricKey: "tenant.storage.logical_bytes",
      metricVersion: 1,
    };
    const violations = checkRegistryIntegrity(registry);
    expect(violations.join("\n")).toContain("snapshot metric");
  });

  it("rejects unknown expression placeholders and missing matchers", () => {
    const registry = loadRegistry();
    registry.queryTemplates.templates[0] = {
      ...registry.queryTemplates.templates[0],
      expression: "sum(rate(x{ {{matchers}} }[{{evil}}]))",
    };
    expect(checkRegistryIntegrity(registry).join("\n")).toContain("unknown placeholder");

    const registry2 = loadRegistry();
    registry2.queryTemplates.templates[0] = {
      ...registry2.queryTemplates.templates[0],
      expression: "sum(rate(x[5m]))",
    };
    expect(checkRegistryIntegrity(registry2).join("\n")).toContain("missing the scope matchers");
  });

  it("rejects unsorted registries (deterministic ordering)", () => {
    const registry = loadRegistry();
    registry.metricCatalog.metrics.reverse();
    expect(checkRegistryIntegrity(registry).join("\n")).toContain("sorted");
  });
});

describe("registry content hash", () => {
  it("is deterministic and key-order independent", () => {
    const first = registryContentHash(loadRegistry());
    const second = registryContentHash(
      JSON.parse(JSON.stringify(loadRegistry())) as TelemetryRegistry,
    );
    expect(first).toBe(second);
    expect(first).toMatch(/^[0-9a-f]{64}$/);
  });

  it("changes when any registry content changes", () => {
    const registry = loadRegistry();
    const before = registryContentHash(registry);
    registry.queryTemplates.templates[0] = {
      ...registry.queryTemplates.templates[0],
      version: 2,
    };
    expect(registryContentHash(registry)).not.toBe(before);
  });
});
