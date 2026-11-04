/**
 * Error signals across services: "where is the problem?" (ADR-0029).
 *
 * One read for every bound service on a screen — error and warning log
 * lines, requests, failed requests and p95 — so a list can rank what is
 * actually going wrong instead of what is merely configured. The API decides
 * which bindings the caller may read each signal for; this module only asks
 * for ids and a window, and judges what comes back.
 */

import type { StatusTone } from "@/lib/design/status";
import type { ServiceHealthRow } from "@/lib/serviceHealth";

export const SIGNAL_RANGE = "1h";
/** The API's ceiling on ids per read. */
export const MAX_SIGNAL_BINDINGS = 100;

export type SignalState = "ok" | "not_configured" | "unavailable";

export interface LogSignal {
  lines: number;
  errors: number;
  warnings: number;
  newest_at: string | null;
}

export interface TraceSignal {
  requests: number;
  errors: number;
  error_ratio: number | null;
  p95_ms: number | null;
  newest_at: string | null;
}

export interface SignalItem {
  binding_id: string;
  logs: LogSignal | null;
  traces: TraceSignal | null;
}

export interface SignalPage {
  range: string;
  as_of: string;
  logs: { state: SignalState; reason?: string };
  traces: { state: SignalState; reason?: string };
  items: SignalItem[];
}

/** A ratio as a percentage: one decimal under 10%, so 0.4% is not "0%". */
export function percent(ratio: number | null): string {
  if (ratio === null) return "—";
  const value = ratio * 100;
  return `${value < 10 && value > 0 ? value.toFixed(1) : Math.round(value)}%`;
}

/** The read for a set of bindings, or null when there is nothing to ask. */
export function signalsPath(bindingIds: string[], range: string = SIGNAL_RANGE): string | null {
  const ids = [...new Set(bindingIds)].sort().slice(0, MAX_SIGNAL_BINDINGS);
  if (ids.length === 0) return null;
  const query = new URLSearchParams();
  for (const id of ids) query.append("binding_id", id);
  query.set("range", range);
  return `/v1/service-health/signals?${query.toString()}`;
}

export function boundIds(rows: ServiceHealthRow[]): string[] {
  return rows.flatMap((row) => (row.binding ? [row.binding.id] : []));
}

/*
 * When a number is worth a look. Deliberately conservative: a single failed
 * request in a quiet hour is not an incident, and a ratio over three
 * requests is noise. Log severity is only as good as the application's own
 * levels, so error lines never raise more than a warning on their own.
 */
export const MIN_REQUESTS_FOR_RATIO = 20;
export const REQUEST_ERROR_WARN = 0.05;
export const REQUEST_ERROR_CRITICAL = 0.25;
export const ERROR_LINES_WARN = 10;

export type SignalCause = "requests" | "logs";

export interface SignalVerdict {
  tone: StatusTone;
  cause: SignalCause | null;
}

export function judgeSignal(item: SignalItem): SignalVerdict {
  const traces = item.traces;
  if (
    traces &&
    traces.error_ratio !== null &&
    traces.requests >= MIN_REQUESTS_FOR_RATIO &&
    traces.error_ratio >= REQUEST_ERROR_WARN
  ) {
    return {
      tone: traces.error_ratio >= REQUEST_ERROR_CRITICAL ? "critical" : "warning",
      cause: "requests",
    };
  }
  if (item.logs && item.logs.errors >= ERROR_LINES_WARN) {
    return { tone: "warning", cause: "logs" };
  }
  return { tone: "success", cause: null };
}

export interface RankedSignal {
  row: ServiceHealthRow;
  item: SignalItem;
  verdict: SignalVerdict;
}

const TONE_RANK: Record<string, number> = { critical: 0, warning: 1, success: 2 };

/**
 * Bound services that have any signal, worst first: by verdict, then by
 * failed requests, then by error lines. A service the caller may read no
 * signal for is not in the page and so not here.
 */
export function rankSignals(rows: ServiceHealthRow[], page: SignalPage): RankedSignal[] {
  const byId = new Map(page.items.map((item) => [item.binding_id, item]));
  const ranked: RankedSignal[] = [];
  for (const row of rows) {
    const item = row.binding ? byId.get(row.binding.id) : undefined;
    if (!item) continue;
    ranked.push({ row, item, verdict: judgeSignal(item) });
  }
  return ranked.sort(
    (a, b) =>
      (TONE_RANK[a.verdict.tone] ?? 3) - (TONE_RANK[b.verdict.tone] ?? 3) ||
      (b.item.traces?.errors ?? 0) - (a.item.traces?.errors ?? 0) ||
      (b.item.logs?.errors ?? 0) - (a.item.logs?.errors ?? 0) ||
      (b.item.traces?.requests ?? 0) - (a.item.traces?.requests ?? 0),
  );
}

/** Whether either signal is readable at all, so a screen can stay silent
 *  rather than show a board of dashes when no store is configured. */
export function anySignalConfigured(page: SignalPage): boolean {
  return page.logs.state !== "not_configured" || page.traces.state !== "not_configured";
}
