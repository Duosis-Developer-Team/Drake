/**
 * Unit-aware value formatting, shared by tiles, tables, axes and tooltips.
 *
 * One implementation so a latency reads the same in a KPI, a chart tooltip
 * and a CSV-shaped table. Two rules run through all of it:
 *
 *   `null` is not zero. A missing measurement renders as an em dash, never as
 *   `0`, and never as a blank cell that reads like zero.
 *
 *   Axis labels are not values. An axis is scanned, so it gets the short form
 *   (`1.2k`, `4 GiB`); a tooltip is read, so it gets the exact one. `compact`
 *   selects between them rather than each caller rounding its own way.
 */

export type Unit =
  | "bytes"
  | "cores"
  | "count"
  | "currency_usd"
  | "duration_seconds"
  | "milliseconds"
  | "percent"
  | "ratio"
  | "requests_per_second"
  | "restarts"
  | "seconds";

export const MISSING = "—";

const BYTE_UNITS = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];

function significant(value: number, digits = 2): string {
  const abs = Math.abs(value);
  if (abs === 0) return "0";
  if (abs >= 100) return value.toFixed(0);
  if (abs >= 10) return value.toFixed(1);
  return value.toFixed(digits);
}

function thousands(value: number, compact: boolean): string {
  if (!compact) return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
  const abs = Math.abs(value);
  if (abs >= 1e9) return `${significant(value / 1e9)}B`;
  if (abs >= 1e6) return `${significant(value / 1e6)}M`;
  if (abs >= 1e4) return `${significant(value / 1e3)}k`;
  return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

function bytes(value: number, compact: boolean): string {
  let scaled = Math.abs(value);
  let index = 0;
  while (scaled >= 1024 && index < BYTE_UNITS.length - 1) {
    scaled /= 1024;
    index += 1;
  }
  const signed = value < 0 ? -scaled : scaled;
  return `${index === 0 ? Math.round(signed) : significant(signed, compact ? 1 : 2)} ${
    BYTE_UNITS[index]
  }`;
}

function duration(seconds: number, compact: boolean): string {
  const abs = Math.abs(seconds);
  if (abs < 1) return `${significant(seconds * 1000, compact ? 0 : 1)} ms`;
  if (abs < 60) return `${significant(seconds, compact ? 1 : 2)} s`;
  if (abs < 3600) {
    const minutes = Math.floor(abs / 60);
    const rest = Math.round(abs % 60);
    return compact ? `${minutes}m` : `${minutes}m ${rest}s`;
  }
  if (abs < 86400) {
    const hours = Math.floor(abs / 3600);
    const rest = Math.round((abs % 3600) / 60);
    return compact ? `${hours}h` : `${hours}h ${rest}m`;
  }
  const days = Math.floor(abs / 86400);
  const rest = Math.round((abs % 86400) / 3600);
  return compact ? `${days}d` : `${days}d ${rest}h`;
}

export function formatUnit(
  value: number | null | undefined,
  unit: Unit | string,
  options: { compact?: boolean } = {},
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return MISSING;
  const compact = options.compact ?? false;
  switch (unit) {
    case "bytes":
      return bytes(value, compact);
    case "cores":
      return `${significant(value, compact ? 1 : 2)} ${Math.abs(value) === 1 ? "core" : "cores"}`;
    case "currency_usd":
      return value.toLocaleString("en-US", {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: compact && Math.abs(value) >= 1000 ? 0 : 2,
      });
    case "duration_seconds":
    case "seconds":
      return duration(value, compact);
    case "milliseconds":
      return duration(value / 1000, compact);
    case "percent":
      return `${significant(value, compact ? 0 : 1)}%`;
    case "ratio":
      return `${significant(value * 100, compact ? 0 : 2)}%`;
    case "requests_per_second":
      return `${thousands(value, compact)} req/s`;
    case "restarts":
    case "count":
      return thousands(Math.round(value), compact);
    default:
      return thousands(value, compact);
  }
}

/** The unit on its own, for an axis title or a column header. */
export const UNIT_LABELS: Record<string, string> = {
  bytes: "bytes",
  cores: "cores",
  count: "count",
  currency_usd: "USD",
  duration_seconds: "duration",
  milliseconds: "duration",
  percent: "%",
  ratio: "%",
  requests_per_second: "req/s",
  restarts: "restarts",
  seconds: "duration",
};

/**
 * An absolute instant, in UTC.
 *
 * Drake's API speaks UTC and its operators compare timestamps against pod
 * logs and audit records, so the product does too — everywhere, with the zone
 * printed. A local-time rendering of an incident start is a different number
 * to every reader.
 */
export function formatUtc(value: string | number | Date | null | undefined): string {
  if (value === null || value === undefined || value === "") return MISSING;
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return `${date.toISOString().slice(0, 19).replace("T", " ")} UTC`;
}

/** Short form for a dense column: no year when it is the current one. */
export function formatUtcShort(value: string | number | Date | null | undefined, now = new Date()): string {
  if (value === null || value === undefined || value === "") return MISSING;
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const iso = date.toISOString();
  return date.getUTCFullYear() === now.getUTCFullYear()
    ? `${iso.slice(5, 10)} ${iso.slice(11, 16)}`
    : `${iso.slice(0, 10)} ${iso.slice(11, 16)}`;
}

/**
 * "4m ago", "in 2h", "just now".
 *
 * Always paired with the exact timestamp in a tooltip: relative time is for
 * scanning, and it is not evidence.
 */
export function formatRelative(
  value: string | number | Date | null | undefined,
  now: Date = new Date(),
): string {
  if (value === null || value === undefined || value === "") return MISSING;
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const deltaSeconds = (date.getTime() - now.getTime()) / 1000;
  const abs = Math.abs(deltaSeconds);
  if (abs < 45) return "just now";
  const suffix = (text: string) => (deltaSeconds < 0 ? `${text} ago` : `in ${text}`);
  if (abs < 3600) return suffix(`${Math.round(abs / 60)}m`);
  if (abs < 86400) return suffix(`${Math.round(abs / 3600)}h`);
  if (abs < 2592000) return suffix(`${Math.round(abs / 86400)}d`);
  return suffix(`${Math.round(abs / 2592000)}mo`);
}

/** Axis tick for a time series, chosen from the window it has to cover. */
export function formatTimeAxis(timestampMs: number, windowSeconds: number): string {
  const iso = new Date(timestampMs).toISOString();
  if (windowSeconds <= 6 * 3600) return iso.slice(11, 16);
  if (windowSeconds <= 3 * 86400) return `${iso.slice(5, 10)} ${iso.slice(11, 16)}`;
  return iso.slice(5, 10);
}
