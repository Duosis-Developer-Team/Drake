/**
 * Telemetry registry integrity: cross-reference and determinism checks that
 * JSON Schema alone cannot express. The same rules are re-enforced
 * independently by the API's registry loader at boot (fail-closed).
 */

import { createHash } from "node:crypto";

import { FORBIDDEN_METRIC_LABELS } from "./metric-policy.js";

interface MetricEntry {
  key: string;
  version: number;
  type: string;
  unit: string;
  sourceType: string;
  allowedInputLabels: string[];
  allowedOutputLabels: string[];
  profiles: string[];
  [extra: string]: unknown;
}

interface TemplateEntry {
  key: string;
  version: number;
  metricKey: string;
  metricVersion: number;
  expression: string;
  matchers: { label: string; source: string }[];
  output: { resultType: string; unit: string; labels: string[] };
  [extra: string]: unknown;
}

interface DashboardEntry {
  key: string;
  version: number;
  profiles: string[];
  sections: {
    key: string;
    widgets: { key: string; queryTemplateKey: string; requiredProfile?: string }[];
  }[];
  [extra: string]: unknown;
}

export interface TelemetryRegistry {
  metricCatalog: { metrics: MetricEntry[] };
  queryTemplates: { templates: TemplateEntry[] };
  dashboardTemplates: { dashboards: DashboardEntry[] };
}

const PLACEHOLDER = /\{\{([a-z_]+)\}\}/g;
const ALLOWED_PLACEHOLDERS = new Set(["matchers", "window"]);

/** Returns human-readable violations (empty array = registry is coherent). */
export function checkRegistryIntegrity(registry: TelemetryRegistry): string[] {
  const violations: string[] = [];
  const metrics = registry.metricCatalog.metrics;
  const templates = registry.queryTemplates.templates;
  const dashboards = registry.dashboardTemplates.dashboards;

  const metricIndex = new Map<string, MetricEntry>();
  for (const metric of metrics) {
    const id = `${metric.key}@${metric.version}`;
    if (metricIndex.has(id)) {
      violations.push(`metric duplicate: ${id}`);
    }
    metricIndex.set(id, metric);
    for (const label of [...metric.allowedInputLabels, ...metric.allowedOutputLabels]) {
      if (FORBIDDEN_METRIC_LABELS.includes(label)) {
        violations.push(`metric ${metric.key}: forbidden label "${label}"`);
      }
    }
  }

  const templateIndex = new Map<string, TemplateEntry>();
  for (const template of templates) {
    const id = `${template.key}@${template.version}`;
    if (templateIndex.has(id)) {
      violations.push(`template duplicate: ${id}`);
    }
    templateIndex.set(id, template);

    const metric = metricIndex.get(`${template.metricKey}@${template.metricVersion}`);
    if (!metric) {
      violations.push(
        `template ${template.key}: unknown metric ${template.metricKey}@${template.metricVersion}`,
      );
    } else {
      if (metric.sourceType === "snapshot") {
        violations.push(
          `template ${template.key}: snapshot metric ${metric.key} cannot back a provider query template`,
        );
      }
      for (const matcher of template.matchers) {
        if (!metric.allowedInputLabels.includes(matcher.label)) {
          violations.push(
            `template ${template.key}: matcher label "${matcher.label}" not in metric input labels`,
          );
        }
      }
      for (const label of template.output.labels) {
        if (!metric.allowedOutputLabels.includes(label)) {
          violations.push(
            `template ${template.key}: output label "${label}" not in metric output labels`,
          );
        }
      }
    }

    for (const match of template.expression.matchAll(PLACEHOLDER)) {
      if (!ALLOWED_PLACEHOLDERS.has(match[1])) {
        violations.push(`template ${template.key}: unknown placeholder "${match[1]}"`);
      }
    }
    if (!template.expression.includes("{{matchers}}")) {
      violations.push(`template ${template.key}: expression is missing the scope matchers`);
    }
    for (const label of FORBIDDEN_METRIC_LABELS) {
      if (new RegExp(`[({,\\s]${label}\\s*=`).test(template.expression)) {
        violations.push(`template ${template.key}: forbidden label "${label}" in expression`);
      }
    }
  }

  for (const dashboard of dashboards) {
    const seen = new Set<string>();
    const id = `${dashboard.key}@${dashboard.version}`;
    if (seen.has(id)) {
      violations.push(`dashboard duplicate: ${id}`);
    }
    seen.add(id);
    for (const section of dashboard.sections) {
      for (const widget of section.widgets) {
        const found = [...templateIndex.values()].some(
          (template) => template.key === widget.queryTemplateKey,
        );
        if (!found) {
          violations.push(
            `dashboard ${dashboard.key}/${section.key}/${widget.key}: unknown query template "${widget.queryTemplateKey}"`,
          );
        }
        if (widget.requiredProfile && !dashboard.profiles.includes(widget.requiredProfile)) {
          violations.push(
            `dashboard ${dashboard.key}/${widget.key}: requiredProfile "${widget.requiredProfile}" not in dashboard profiles`,
          );
        }
      }
    }
  }

  // Deterministic order: registries are sorted by (key, version) so the
  // content hash — and everything keyed on it — is stable.
  const assertSorted = (kind: string, keys: string[]): void => {
    const sorted = [...keys].sort();
    if (JSON.stringify(keys) !== JSON.stringify(sorted)) {
      violations.push(`${kind}: entries must be sorted by key for deterministic hashing`);
    }
  };
  assertSorted(
    "metric catalog",
    metrics.map((m) => `${m.key}@${m.version}`),
  );
  assertSorted(
    "query templates",
    templates.map((t) => `${t.key}@${t.version}`),
  );
  assertSorted(
    "dashboard templates",
    dashboards.map((d) => `${d.key}@${d.version}`),
  );

  return violations;
}

/** Canonical sha256 over the sorted-key JSON serialization of the registry. */
export function registryContentHash(registry: TelemetryRegistry): string {
  const canonical = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(canonical);
    if (value !== null && typeof value === "object") {
      return Object.fromEntries(
        Object.entries(value as Record<string, unknown>)
          .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
          .map(([k, v]) => [k, canonical(v)]),
      );
    }
    return value;
  };
  return createHash("sha256")
    .update(JSON.stringify(canonical(registry)))
    .digest("hex");
}
