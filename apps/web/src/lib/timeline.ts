/**
 * The detail page's timeline strip: one service's requests, failures, p95
 * and error log lines per time bucket (GET …/timeline), plus the window a
 * reader picks on it, which the request and log panels then read.
 */

export const TIMELINE_RANGES = ["15m", "1h", "6h", "24h"] as const;
export type TimelineRange = (typeof TIMELINE_RANGES)[number];

export type LaneState = "ok" | "not_configured" | "denied" | "unavailable";

export interface TimelineBucket {
  start: string;
  end: string;
  /** The newest bucket, still filling: a short bar there is not a drop. */
  partial: boolean;
}

export interface RequestBucket {
  requests: number;
  errors: number;
  error_ratio: number | null;
  p95_ms: number | null;
}

export interface LogBucket {
  lines: number;
  errors: number;
  warnings: number;
}

export interface TimelinePage {
  binding_id: string;
  range: TimelineRange;
  as_of: string;
  bucket_seconds: number;
  window: { start: string; end: string };
  buckets: TimelineBucket[];
  requests: { state: LaneState; reason?: string; buckets: RequestBucket[] };
  logs: { state: LaneState; reason?: string; buckets: LogBucket[] };
}

/** A slice the reader picked. `range` is the range it was picked in, which
 *  the request and log reads use so the slice is always inside it. */
export interface TimelineWindow {
  start: string;
  end: string;
  range: TimelineRange;
}

export function timelinePath(bindingId: string, range: TimelineRange): string {
  return `/v1/service-health/bindings/${encodeURIComponent(bindingId)}/timeline?range=${range}`;
}

/**
 * SVG path data for one lane in a 1000×100 box: a line through the values
 * and the area under it. A null value (no requests, so no ratio or p95)
 * breaks the line instead of being drawn as zero.
 */
export function lanePaths(
  values: (number | null)[],
  max: number,
): { line: string; area: string } {
  const n = values.length;
  if (n === 0) return { line: "", area: "" };
  const top = max > 0 ? max : 1;
  const x = (i: number) => ((i + 0.5) * 1000) / n;
  const y = (v: number) => 100 - (Math.min(Math.max(v, 0), top) / top) * 94;
  let line = "";
  let area = "";
  let run: [number, number][] = [];
  const flush = () => {
    if (run.length === 0) return;
    const d = run.map(([px, py], i) => `${i === 0 ? "M" : "L"} ${px.toFixed(1)} ${py.toFixed(1)}`).join(" ");
    line += `${d} `;
    area += `${d} L ${run[run.length - 1][0].toFixed(1)} 100 L ${run[0][0].toFixed(1)} 100 Z `;
    run = [];
  };
  values.forEach((value, i) => {
    if (value === null || !Number.isFinite(value)) flush();
    else run.push([x(i), y(value)]);
  });
  flush();
  return { line: line.trim(), area: area.trim() };
}

/** Where a moment falls across the strip, as a fraction, or null when it
 *  is outside the window (an event that should not be drawn). */
export function positionIn(window: { start: string; end: string }, at: string): number | null {
  const start = Date.parse(window.start);
  const end = Date.parse(window.end);
  const t = Date.parse(at);
  if (!Number.isFinite(t) || t < start || t > end || end <= start) return null;
  return (t - start) / (end - start);
}
