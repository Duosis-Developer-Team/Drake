/**
 * Log reads for one bound service (ADR-0029).
 *
 * The API decides whose logs these are — the binding row names the cluster,
 * namespace and workload — so the only things this module ever sends are the
 * caller's bounded choices: a window, a minimum severity, a plain-text term
 * and a page cursor.
 */

export const LOG_RANGES = ["15m", "1h", "6h", "24h"] as const;
export type LogRange = (typeof LOG_RANGES)[number];

export const LOG_SEVERITIES = ["any", "debug", "info", "warn", "error", "fatal"] as const;
export type LogSeverity = (typeof LOG_SEVERITIES)[number];

export interface LogEntry {
  timestamp: string;
  severity: string | null;
  severity_number: number;
  body: string;
  trace_id: string | null;
  span_id: string | null;
  pod: string | null;
  container: string | null;
}

/**
 * The three answers the endpoint gives, kept apart because they read alike
 * and mean opposite things (ADR-0011).
 */
export interface LogPage {
  binding_id: string;
  range: LogRange;
  as_of: string;
  state: "ok" | "not_configured" | "unavailable";
  /** Bounded code when `unavailable`; never upstream text. */
  reason?: string;
  window?: { start: string; end: string };
  entries: LogEntry[];
  next_before: string | null;
}

export const PAGE_SIZE = 100;

export function logsPath(
  bindingId: string,
  options: { range: LogRange; severity: LogSeverity; term: string; before?: string | null },
): string {
  const params = new URLSearchParams({
    range: options.range,
    severity: options.severity,
    limit: String(PAGE_SIZE),
  });
  const term = options.term.trim();
  if (term) params.set("q", term);
  if (options.before) params.set("before", options.before);
  return `/v1/service-health/bindings/${encodeURIComponent(bindingId)}/logs?${params.toString()}`;
}

/**
 * Kubernetes liveness/readiness traffic, recognised by what the line says.
 *
 * These are most of what a quiet service logs, and they bury the lines
 * anyone opened this screen for. The match is deliberately narrow — the
 * probe user agent, or a GET to a conventional health path — so a real
 * request to `/healthcare` or an error mentioning "health" stays visible.
 */
const HEALTH_CHECK =
  /kube-probe\/|\bGET \/(?:health|healthz|livez|readyz|ready|live|ping|-\/healthy|-\/ready)(?:[\s?"/]|$)/i;

export function isHealthCheck(entry: Pick<LogEntry, "body">): boolean {
  return HEALTH_CHECK.test(entry.body);
}

/**
 * The visual weight of a line. OpenTelemetry severity numbers come in bands
 * of four; a line with no severity (0) — a plain container log — is neutral,
 * never assumed to be fine or assumed to be a problem.
 */
export type SeverityBand = "fatal" | "error" | "warn" | "info" | "debug" | "none";

export function severityBand(entry: Pick<LogEntry, "severity_number">): SeverityBand {
  const n = entry.severity_number;
  if (n >= 21) return "fatal";
  if (n >= 17) return "error";
  if (n >= 13) return "warn";
  if (n >= 9) return "info";
  if (n >= 1) return "debug";
  return "none";
}

/** `HH:MM:SS.mmm` in UTC — the form operators compare against pod logs. */
export function utcClock(timestamp: string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return timestamp;
  return date.toISOString().slice(11, 23);
}

/** `YYYY-MM-DD` in UTC, shown when a page crosses midnight. */
export function utcDay(timestamp: string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return "";
  return date.toISOString().slice(0, 10);
}

/**
 * A JSON log line, read for display.
 *
 * Many services (Drake's own API among them) write one JSON object per line.
 * Shown raw, the message is buried in punctuation; shown parsed, the level
 * and message lead and the rest are fields. Nothing here changes what was
 * stored or what the server filtered on — a line that does not parse is
 * simply shown as written.
 */
export interface StructuredBody {
  message: string | null;
  level: string | null;
  fields: [string, string][];
}

const MESSAGE_KEYS = ["message", "msg", "event", "log"];
const LEVEL_KEYS = ["level", "severity", "lvl", "levelname", "log.level"];
const MAX_FIELDS = 40;

export function structuredBody(body: string): StructuredBody | null {
  const trimmed = body.trim();
  if (!trimmed.startsWith("{") || !trimmed.endsWith("}")) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
  const record = parsed as Record<string, unknown>;
  const pick = (keys: string[]): [string, string] | null => {
    for (const key of keys) {
      const value = record[key];
      if (typeof value === "string" && value.trim()) return [key, value];
    }
    return null;
  };
  const message = pick(MESSAGE_KEYS);
  const level = pick(LEVEL_KEYS);
  const fields: [string, string][] = [];
  for (const [key, value] of Object.entries(record)) {
    if (key === message?.[0] || key === level?.[0]) continue;
    if (fields.length >= MAX_FIELDS) break;
    fields.push([key, typeof value === "string" ? value : JSON.stringify(value)]);
  }
  return { message: message?.[1] ?? null, level: level?.[1] ?? null, fields };
}

/**
 * The band to draw a line with: the stored severity when there is one,
 * otherwise the level a JSON line declares about itself. A display hint
 * only — the severity filter is the server's and uses the stored number.
 */
export function displayBand(entry: LogEntry, structured: StructuredBody | null): SeverityBand {
  const stored = severityBand(entry);
  if (stored !== "none" || !structured?.level) return stored;
  const level = structured.level.toLowerCase();
  if (level.startsWith("fatal") || level.startsWith("crit") || level.startsWith("panic")) return "fatal";
  if (level.startsWith("err")) return "error";
  if (level.startsWith("warn")) return "warn";
  if (level.startsWith("info") || level === "notice") return "info";
  if (level.startsWith("debug") || level.startsWith("trace")) return "debug";
  return "none";
}
