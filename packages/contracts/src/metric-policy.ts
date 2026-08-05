/**
 * Metric label policy: the cardinality/PII firewall.
 *
 * These labels may never appear as allowed labels on any metric definition —
 * they are PII, unbounded-cardinality, or both. This list backs the CI guard;
 * extending a metric's labels requires a schema review, not a quiet edit.
 */

export const FORBIDDEN_METRIC_LABELS: readonly string[] = [
  "email",
  "user_id",
  "full_name",
  "request_id",
  "trace_id",
  "span_id",
  "raw_path",
  "url",
  "query",
  "query_string",
  "sql",
  "ip",
  "client_ip",
  "tenant_name",
  "display_name",
  "error_message",
  "filename",
  "artifact_id",
];

export interface MetricDefinition {
  key: string;
  allowedLabels?: string[];
  forbiddenLabels?: string[];
  [extra: string]: unknown;
}

export interface MetricCatalog {
  apiVersion: string;
  kind: string;
  metrics: MetricDefinition[];
}

/**
 * Returns human-readable violations (empty array = catalog is clean).
 * A violation is: a forbidden label in allowedLabels, or a metric-local
 * forbiddenLabels entry that also appears in its allowedLabels.
 */
export function checkMetricLabels(catalog: MetricCatalog): string[] {
  const violations: string[] = [];
  for (const metric of catalog.metrics) {
    const allowed = metric.allowedLabels ?? [];
    for (const label of allowed) {
      if (FORBIDDEN_METRIC_LABELS.includes(label)) {
        violations.push(`${metric.key}: forbidden label "${label}" in allowedLabels`);
      }
    }
    for (const label of metric.forbiddenLabels ?? []) {
      if (allowed.includes(label)) {
        violations.push(
          `${metric.key}: label "${label}" is both allowed and forbidden`,
        );
      }
    }
  }
  return violations;
}
