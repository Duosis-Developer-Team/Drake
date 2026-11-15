/**
 * Trace reads for one bound service (ADR-0029).
 *
 * Spans come from OBI, recorded at the kernel. The API decides whose spans
 * they are from the binding row; this module only sends a window, an
 * errors-only switch, a sort and a page size, and lays out what comes back.
 */

export const TRACE_RANGES = ["15m", "1h", "6h", "24h"] as const;
export type TraceRange = (typeof TRACE_RANGES)[number];
export type TraceSort = "recent" | "slowest";

export interface TraceTotals {
  requests: number;
  errors: number;
  error_ratio: number | null;
  p95_ms: number | null;
}

export interface RouteSummary {
  route: string;
  requests: number;
  errors: number;
  error_ratio: number | null;
  p50_ms: number | null;
  p95_ms: number | null;
}

export interface RequestSummary {
  timestamp: string;
  trace_id: string;
  route: string;
  duration_ms: number | null;
  error: boolean;
  method: string | null;
  http_status: string | null;
  path: string | null;
  pod: string | null;
}

export interface TracePage {
  binding_id: string;
  range: TraceRange;
  as_of: string;
  state: "ok" | "not_configured" | "unavailable";
  reason?: string;
  window?: { start: string; end: string };
  totals: TraceTotals | null;
  routes: RouteSummary[];
  requests: RequestSummary[];
}

export interface Span {
  timestamp: string;
  span_id: string;
  parent_span_id: string | null;
  name: string;
  kind: string;
  duration_ms: number | null;
  error: boolean;
  status_message: string | null;
  attributes: Record<string, string>;
  pod: string | null;
}

export interface TraceDetail {
  binding_id: string;
  trace_id: string;
  state: "ok" | "not_configured" | "unavailable";
  reason?: string;
  spans: Span[];
}

export const TRACE_PAGE_SIZE = 50;

export function tracesPath(
  bindingId: string,
  options: {
    range: TraceRange;
    errorsOnly: boolean;
    sort: TraceSort;
    /** A slice picked on the timeline; the server keeps it inside `range`. */
    window?: { start: string; end: string } | null;
  },
): string {
  const params = new URLSearchParams({
    range: options.range,
    status: options.errorsOnly ? "error" : "any",
    sort: options.sort,
    limit: String(TRACE_PAGE_SIZE),
  });
  if (options.window) {
    params.set("start", options.window.start);
    params.set("end", options.window.end);
  }
  return `/v1/service-health/bindings/${encodeURIComponent(bindingId)}/traces?${params.toString()}`;
}

export function traceDetailPath(bindingId: string, traceId: string): string {
  return `/v1/service-health/bindings/${encodeURIComponent(bindingId)}/traces/${encodeURIComponent(traceId)}`;
}

/** One row of the waterfall: where the bar starts and how wide it is, as a
 * fraction of the whole trace, and how deep the span sits. */
export interface WaterfallRow {
  span: Span;
  depth: number;
  offset: number;
  width: number;
}

/**
 * Lay a trace out as a waterfall.
 *
 * Children follow their parent in start order; a span whose parent is not
 * in the list (cut at the service boundary, or never recorded) is drawn as
 * a root rather than dropped. Offsets and widths are fractions of the span
 * from the earliest start to the latest end, clamped to [0, 1] so a
 * malformed duration can never draw outside the track.
 */
export function layoutWaterfall(spans: Span[]): WaterfallRow[] {
  if (spans.length === 0) return [];
  const startOf = (span: Span) => new Date(span.timestamp).getTime();
  const endOf = (span: Span) => startOf(span) + (span.duration_ms ?? 0);
  const t0 = Math.min(...spans.map(startOf));
  const t1 = Math.max(...spans.map(endOf));
  const total = Math.max(t1 - t0, 1e-6);

  const ids = new Set(spans.map((span) => span.span_id));
  const children = new Map<string, Span[]>();
  const roots: Span[] = [];
  for (const span of spans) {
    const parent = span.parent_span_id;
    if (parent && ids.has(parent) && parent !== span.span_id) {
      const list = children.get(parent) ?? [];
      list.push(span);
      children.set(parent, list);
    } else {
      roots.push(span);
    }
  }
  const byStart = (a: Span, b: Span) => startOf(a) - startOf(b);

  const rows: WaterfallRow[] = [];
  const seen = new Set<string>();
  const clamp = (value: number) => Math.min(1, Math.max(0, value));
  const visit = (span: Span, depth: number) => {
    // A cycle in parent ids would otherwise recurse forever.
    if (seen.has(span.span_id)) return;
    seen.add(span.span_id);
    const offset = clamp((startOf(span) - t0) / total);
    const width = clamp((span.duration_ms ?? 0) / total);
    rows.push({ span, depth, offset, width: Math.min(width, 1 - offset) });
    for (const child of (children.get(span.span_id) ?? []).sort(byStart)) visit(child, depth + 1);
  };
  for (const root of roots.sort(byStart)) visit(root, 0);
  return rows;
}

/**
 * Where one request's time went, in a sentence's worth of facts.
 *
 * Read from the trace itself: the share of the root span taken by the
 * longest outgoing call, or by waiting in the queue, or else by the
 * service's own work. Thresholds are deliberately plain (an outgoing call
 * that took 40% or more, a queue wait of 30% or more) so the sentence only
 * ever states what the waterfall below it plainly shows.
 */
export type TraceSummary =
  | { kind: "call"; name: string; share: number; durationMs: number; failed: boolean; status: string | null }
  | { kind: "queue"; share: number; durationMs: number; failed: boolean; status: string | null }
  | { kind: "self"; callsShare: number; calls: number; failed: boolean; status: string | null };

export const CALL_SHARE = 0.4;
export const QUEUE_SHARE = 0.3;

export function summarizeTrace(rows: WaterfallRow[]): TraceSummary | null {
  const root = rows.find((row) => row.depth === 0)?.span;
  const total = root?.duration_ms ?? null;
  if (!root || total === null || !(total > 0)) return null;
  const failed = root.error;
  const status = root.attributes["http.response.status_code"] ?? null;
  const calls = rows.filter((row) => row.span.kind === "Client" && row.span !== root).map((row) => row.span);
  const longest = calls.reduce<Span | null>(
    (best, span) => ((span.duration_ms ?? 0) > (best?.duration_ms ?? -1) ? span : best),
    null,
  );
  if (longest && (longest.duration_ms ?? 0) / total >= CALL_SHARE) {
    return {
      kind: "call",
      name: longest.name,
      share: (longest.duration_ms ?? 0) / total,
      durationMs: longest.duration_ms ?? 0,
      failed: failed || longest.error,
      status,
    };
  }
  const queue = rows.find((row) => row.span.kind === "Internal" && row.span.name === "in queue")?.span;
  if (queue && (queue.duration_ms ?? 0) / total >= QUEUE_SHARE) {
    return { kind: "queue", share: (queue.duration_ms ?? 0) / total, durationMs: queue.duration_ms ?? 0, failed, status };
  }
  const callsMs = calls.reduce((sum, span) => sum + (span.duration_ms ?? 0), 0);
  return { kind: "self", callsShare: Math.min(1, callsMs / total), calls: calls.length, failed, status };
}

/** A duration for people: sub-millisecond, milliseconds, or seconds. */
export function formatDuration(ms: number | null): string {
  if (ms === null || !Number.isFinite(ms)) return "—";
  if (ms < 1) return `${(ms * 1000).toFixed(0)} µs`;
  if (ms < 1000) return `${ms < 10 ? ms.toFixed(1) : Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`;
}
