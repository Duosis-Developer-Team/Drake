/**
 * Environment dashboards (ADR-0033).
 *
 * The API answers the numbers (the overview read) and stores the layout
 * (one dashboard per environment, every save a version). This module only
 * reshapes what it answered for the widgets. It never invents a number:
 * a section that did not answer stays unanswered, and a missing value
 * stays null, never zero.
 */

import { apiGet, apiMutate } from "@/lib/api";
import type { ServiceHealthRow } from "@/lib/serviceHealth";

/* ---------------- layout ---------------- */

export const WIDGETS = [
  "overview",
  "services",
  "traffic_graph",
  "findings",
  "latency_heatmap",
  "endpoints",
  "capacity",
  "databases",
  "logs",
] as const;
export type WidgetKey = (typeof WIDGETS)[number];
export type WidgetSize = "s" | "m" | "l";

export interface WidgetSlot {
  key: WidgetKey;
  size: WidgetSize;
  hidden: boolean;
}

export interface Dashboard {
  id: string | null;
  environment_id: string;
  environment_key: string;
  project_id: string;
  project_key: string;
  project_name: string;
  can_manage: boolean;
  title: string;
  layout: WidgetSlot[];
  version: number;
  saved: boolean;
  updated_at: string | null;
  updated_by: string | null;
}

export interface DashboardListItem {
  environment_id: string;
  environment_key: string;
  project_id: string;
  project_key: string;
  project_name: string;
  can_manage: boolean;
  title: string;
  version: number;
  saved: boolean;
  updated_at: string | null;
  services: number;
}

export interface DashboardVersion {
  version: number;
  title: string;
  layout: WidgetSlot[];
  note: string | null;
  saved_by: string | null;
  saved_at: string;
}

export const fetchDashboards = () => apiGet<{ items: DashboardListItem[] }>("/v1/dashboards");
export const fetchDashboard = (environmentId: string) =>
  apiGet<Dashboard>(`/v1/dashboards/${encodeURIComponent(environmentId)}`);
export const fetchDashboardVersions = (environmentId: string) =>
  apiGet<{ items: DashboardVersion[] }>(
    `/v1/dashboards/${encodeURIComponent(environmentId)}/versions`,
  );
export function saveDashboard(
  csrfToken: string,
  environmentId: string,
  body: { title: string; layout: WidgetSlot[]; note?: string | null; expected_version: number },
): Promise<Dashboard> {
  return apiMutate<Dashboard>(`/v1/dashboards/${encodeURIComponent(environmentId)}`, {
    csrfToken,
    method: "PUT",
    body,
  });
}

/** Move a widget one step; the layout stays a permutation of the catalog. */
export function moveWidget(layout: WidgetSlot[], key: WidgetKey, step: -1 | 1): WidgetSlot[] {
  const i = layout.findIndex((w) => w.key === key);
  const j = i + step;
  if (i < 0 || j < 0 || j >= layout.length) return layout;
  const next = layout.slice();
  [next[i], next[j]] = [next[j], next[i]];
  return next;
}

export function setWidget(
  layout: WidgetSlot[],
  key: WidgetKey,
  change: Partial<Pick<WidgetSlot, "size" | "hidden">>,
): WidgetSlot[] {
  const next = layout.map((w) => (w.key === key ? { ...w, ...change } : w));
  // A dashboard always shows something: the last visible widget stays.
  return next.some((w) => !w.hidden) ? next : layout;
}

export const sameLayout = (a: WidgetSlot[], b: WidgetSlot[]) =>
  a.length === b.length &&
  a.every((w, i) => w.key === b[i].key && w.size === b[i].size && w.hidden === b[i].hidden);

/* ---------------- the overview read ---------------- */

export type SectionState = "ok" | "not_configured" | "unavailable";
export interface Section<T> {
  state: SectionState;
  reason?: string;
  data?: T | null;
  /** The store did not answer this time; this is its last good answer. */
  stale?: boolean;
}

export interface TrafficBucket {
  t: number;
  requests: number;
  errors: number;
  p95_ms: number | null;
}
export interface LatencyData {
  classes: { lo_ms: number; hi_ms: number | null }[];
  slow_from_class: number;
  cells: number[][];
  totals: number[];
}
export interface RouteRow {
  binding_id: string;
  route: string;
  requests: number;
  errors: number;
  client_errors: number;
  long_lived: number;
  p50_ms: number | null;
  p95_ms: number | null;
  p99_ms: number | null;
}
export interface CallRow {
  from_binding_id: string;
  to_binding_id: string;
  calls: number;
  errors: number;
  p95_ms: number | null;
}
export interface DatabaseRow {
  binding_id: string;
  system: string | null;
  database: string | null;
  operation: string;
  calls: number;
  errors: number;
  p95_ms: number | null;
}
export interface LogBucket {
  t: number;
  lines: number;
  errors: number;
  warnings: number;
}
export interface LogPattern {
  pattern: string;
  lines: number;
  severity_number: number;
  severity_text: string | null;
  workloads: number;
  binding_ids: string[];
}
export type FindingKind = "periodic_error_logs" | "connection_per_query" | "slow_call" | "client_errors";
export interface Finding {
  kind: FindingKind;
  severity: "warning" | "info";
  binding_ids: string[];
  metrics: Record<string, unknown>;
}

export interface Overview {
  range: string;
  as_of: string;
  bucket_seconds: number | null;
  buckets: number[];
  window: { start: string; end: string };
  bindings: { binding_id: string; traces: boolean; logs: boolean }[];
  traffic: Section<{ binding_id: string; buckets: TrafficBucket[] }[]>;
  latency: Section<LatencyData>;
  codes: Section<{ binding_id: string; codes: Record<string, number> }[]>;
  routes: Section<RouteRow[]>;
  calls: Section<CallRow[]>;
  databases: Section<DatabaseRow[]>;
  log_lines: Section<LogBucket[]>;
  log_patterns: Section<LogPattern[]>;
  findings: { items: Finding[]; skipped: string[] };
  /** Seconds since these numbers were read; 0 when read for this request. */
  age_seconds?: number;
}

export const OVERVIEW_RANGES = ["6h", "24h"] as const;
export type OverviewRange = (typeof OVERVIEW_RANGES)[number];
export const MAX_OVERVIEW_BINDINGS = 32;

export function overviewPath(bindingIds: string[], range: OverviewRange): string | null {
  const ids = [...new Set(bindingIds)].sort().slice(0, MAX_OVERVIEW_BINDINGS);
  if (ids.length === 0) return null;
  const q = new URLSearchParams();
  for (const id of ids) q.append("binding_id", id);
  q.set("range", range);
  return `/v1/service-health/overview?${q.toString()}`;
}

export const fetchOverview = (path: string, signal?: AbortSignal) => apiGet<Overview>(path, signal);

/** Data of a section that answered, or null. */
export function answered<T>(section: Section<T> | undefined): T | null {
  return section && section.state === "ok" && section.data ? section.data : null;
}

/* ---------------- derived numbers ---------------- */

export const SLOW_MS = 1000;

/** Apdex with T = 1 s: under 1 s satisfied, up to 4 s tolerating. Classes
 * are thirds of a decade from 1 ms, so class 9 starts at 1 s and class 11
 * at ~4.6 s. */
export function apdex(latency: LatencyData | null): {
  score: number | null;
  fast: number;
  tolerable: number;
  slow: number;
  total: number;
} {
  const totals = latency?.totals ?? [];
  const fast = totals.slice(0, latency?.slow_from_class ?? 9).reduce((a, b) => a + b, 0);
  const tolerable = totals.slice(9, 11).reduce((a, b) => a + b, 0);
  const slow = totals.slice(11).reduce((a, b) => a + b, 0);
  const total = fast + tolerable + slow;
  return { score: total ? (fast + tolerable / 2) / total : null, fast, tolerable, slow, total };
}

export interface ServiceLine {
  bindingId: string;
  name: string;
  row: ServiceHealthRow;
  requests: number | null;
  errors: number | null;
  worstP95: number | null;
  perBucket: number[];
  p95PerBucket: (number | null)[];
  errorsPerBucket: number[];
  slowBuckets: number;
  codes: Record<string, number> | null;
  longLived: boolean;
}

export function serviceLines(rows: ServiceHealthRow[], overview: Overview | null): ServiceLine[] {
  const traffic = answered(overview?.traffic);
  const codes = answered(overview?.codes);
  const routes = answered(overview?.routes) ?? [];
  return rows
    .filter((r) => r.binding)
    .map((row) => {
      const id = row.binding!.id;
      const t = traffic?.find((x) => x.binding_id === id);
      const p95s = (t?.buckets ?? []).map((b) => b.p95_ms).filter((v): v is number => v !== null);
      const own = routes.filter((r) => r.binding_id === id);
      return {
        bindingId: id,
        name: row.service_key,
        row,
        requests: t ? t.buckets.reduce((a, b) => a + b.requests, 0) : null,
        errors: t ? t.buckets.reduce((a, b) => a + b.errors, 0) : null,
        worstP95: p95s.length ? Math.max(...p95s) : null,
        perBucket: (t?.buckets ?? []).map((b) => b.requests),
        p95PerBucket: (t?.buckets ?? []).map((b) => b.p95_ms),
        errorsPerBucket: (t?.buckets ?? []).map((b) => b.errors),
        slowBuckets: p95s.filter((v) => v >= SLOW_MS).length,
        codes: codes?.find((c) => c.binding_id === id)?.codes ?? null,
        longLived: own.length > 0 && own.every((r) => r.long_lived === r.requests),
      };
    });
}

/** Requests per minute across the environment, per bucket. */
export function requestsPerMinute(overview: Overview): (number | null)[] {
  const traffic = answered(overview.traffic);
  if (!traffic || !overview.bucket_seconds) return overview.buckets.map(() => null);
  const minutes = overview.bucket_seconds / 60;
  return overview.buckets.map((_, i) => traffic.reduce((a, s) => a + (s.buckets[i]?.requests ?? 0), 0) / minutes);
}

/** The slowest service's p95 per bucket. */
export function worstP95(overview: Overview): (number | null)[] {
  const traffic = answered(overview.traffic);
  return overview.buckets.map((_, i) => {
    const values = (traffic ?? []).map((s) => s.buckets[i]?.p95_ms ?? null).filter((v): v is number => v !== null);
    return values.length ? Math.max(...values) : null;
  });
}

export function errorsPerBucket(overview: Overview): (number | null)[] {
  const traffic = answered(overview.traffic);
  if (!traffic) return overview.buckets.map(() => null);
  return overview.buckets.map((_, i) => traffic.reduce((a, s) => a + (s.buckets[i]?.errors ?? 0), 0));
}

const HEALTHISH = /\/health|\/ready|\/live|kube-probe|healthz|readyz/i;

export function logMakeup(overview: Overview): {
  total: number | null;
  health: number;
  errors: number;
  app: number;
} {
  const lines = answered(overview.log_lines);
  const patterns = answered(overview.log_patterns) ?? [];
  const total = lines ? lines.reduce((a, b) => a + b.lines, 0) : null;
  let health = 0;
  let errors = 0;
  for (const p of patterns) {
    if (p.severity_number >= 17) errors += p.lines;
    else if (HEALTHISH.test(p.pattern)) health += p.lines;
  }
  const app = total === null ? 0 : Math.max(0, total - health - errors);
  return { total, health, errors, app };
}

/* ---------------- the traffic graph ---------------- */

export interface GraphNode {
  id: string;
  kind: "users" | "service" | "database";
  label: string;
  requests: number;
  p95: number | null;
  codes: Record<string, number> | null;
  series: number[];
  slow: boolean;
  longLived: boolean;
}
export interface GraphEdge {
  from: string;
  to: string;
  kind: "request" | "call" | "query";
  count: number;
  p95: number | null;
}

export function trafficGraph(lines: ServiceLine[], overview: Overview | null): {
  nodes: GraphNode[];
  edges: GraphEdge[];
} {
  const calls = answered(overview?.calls) ?? [];
  const dbs = answered(overview?.databases) ?? [];
  const nodes: GraphNode[] = [];
  const edges: GraphEdge[] = [];
  const total = lines.reduce((a, l) => a + (l.requests ?? 0), 0);
  nodes.push({
    id: "users", kind: "users", label: "users", requests: total, p95: null, codes: null,
    series: overview ? requestsPerMinute(overview).map((v) => v ?? 0) : [], slow: false, longLived: false,
  });
  for (const l of lines) {
    if (!l.requests) continue;
    nodes.push({
      id: l.bindingId, kind: "service", label: l.name, requests: l.requests, p95: l.worstP95,
      codes: l.codes, series: l.perBucket, slow: (l.worstP95 ?? 0) >= SLOW_MS && !l.longLived, longLived: l.longLived,
    });
    const inbound = calls.filter((c) => c.to_binding_id === l.bindingId).reduce((a, c) => a + c.calls, 0);
    const external = Math.max(0, l.requests - inbound);
    if (external > 0) edges.push({ from: "users", to: l.bindingId, kind: "request", count: external, p95: null });
  }
  const present = new Set(nodes.map((n) => n.id));
  for (const c of calls) {
    if (present.has(c.from_binding_id) && present.has(c.to_binding_id))
      edges.push({ from: c.from_binding_id, to: c.to_binding_id, kind: "call", count: c.calls, p95: c.p95_ms });
  }
  const byDb = new Map<string, GraphNode>();
  for (const d of dbs) {
    const id = `db:${d.system ?? "db"}:${d.database ?? ""}`;
    let node = byDb.get(id);
    if (!node) {
      node = {
        id, kind: "database", label: d.database ?? d.system ?? "database", requests: 0, p95: null,
        codes: null, series: [], slow: false, longLived: false,
      };
      byDb.set(id, node);
    }
    node.requests += d.calls;
    node.p95 = Math.max(node.p95 ?? 0, d.p95_ms ?? 0);
    const edge = edges.find((e) => e.from === d.binding_id && e.to === id);
    if (edge) edge.count += d.calls;
    else if (present.has(d.binding_id)) edges.push({ from: d.binding_id, to: id, kind: "query", count: d.calls, p95: d.p95_ms });
  }
  nodes.push(...byDb.values());
  return { nodes, edges };
}
