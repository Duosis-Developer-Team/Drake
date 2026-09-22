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

import { formattersFor, getTranslator, type Locale } from "@/lib/i18n";
import { LOCALE_TAGS } from "@/lib/i18n/locale";

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

/**
 * Fixed decimals. English keeps `toFixed` — the exact digits every existing
 * caller and test expects; another locale goes through `Intl` so the decimal
 * mark is its own ("4,20" in Turkish).
 */
function fixed(value: number, digits: number, locale: Locale): string {
  if (locale === "en") return value.toFixed(digits);
  return new Intl.NumberFormat(LOCALE_TAGS[locale], {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
    useGrouping: false,
  }).format(value);
}

function significant(value: number, digits = 2, locale: Locale = "en"): string {
  const abs = Math.abs(value);
  if (abs === 0) return "0";
  if (abs >= 100) return fixed(value, 0, locale);
  if (abs >= 10) return fixed(value, 1, locale);
  return fixed(value, digits, locale);
}

function thousands(value: number, compact: boolean, locale: Locale = "en"): string {
  const tag = locale === "en" ? "en-US" : LOCALE_TAGS[locale];
  if (!compact) return value.toLocaleString(tag, { maximumFractionDigits: 2 });
  const abs = Math.abs(value);
  // The magnitude suffixes are symbols, the same in both languages.
  if (abs >= 1e9) return `${significant(value / 1e9, 2, locale)}B`;
  if (abs >= 1e6) return `${significant(value / 1e6, 2, locale)}M`;
  if (abs >= 1e4) return `${significant(value / 1e3, 2, locale)}k`;
  return value.toLocaleString(tag, { maximumFractionDigits: 2 });
}

function bytes(value: number, compact: boolean, locale: Locale = "en"): string {
  let scaled = Math.abs(value);
  let index = 0;
  while (scaled >= 1024 && index < BYTE_UNITS.length - 1) {
    scaled /= 1024;
    index += 1;
  }
  const signed = value < 0 ? -scaled : scaled;
  return `${index === 0 ? Math.round(signed) : significant(signed, compact ? 1 : 2, locale)} ${
    BYTE_UNITS[index]
  }`;
}

/**
 * The unit words. English is literal (and byte-identical to what it always
 * was); another locale reads the short forms from `common.time.*` /
 * `common.unit.*`, the same words `useFormat().duration` uses.
 */
function unitWords(locale: Locale) {
  if (locale === "en") {
    return {
      ms: (n: string) => `${n} ms`,
      s: (n: string | number) => `${n}s`,
      m: (n: string | number) => `${n}m`,
      h: (n: string | number) => `${n}h`,
      d: (n: string | number) => `${n}d`,
      secondsExact: (n: string) => `${n} s`,
      core: "core",
      cores: "cores",
      requestsPerSecond: "req/s",
    };
  }
  const common = getTranslator("common", locale);
  return {
    ms: (n: string) => common("time.millisecondsShort", { count: n }),
    s: (n: string | number) => common("time.secondsShort", { count: n }),
    m: (n: string | number) => common("time.minutesShort", { count: n }),
    h: (n: string | number) => common("time.hoursShort", { count: n }),
    d: (n: string | number) => common("time.daysShort", { count: n }),
    secondsExact: (n: string) => common("time.secondsShort", { count: n }),
    core: common("unit.core"),
    cores: common("unit.cores"),
    requestsPerSecond: common("unit.requestsPerSecond"),
  };
}

function duration(seconds: number, compact: boolean, locale: Locale = "en"): string {
  const w = unitWords(locale);
  const abs = Math.abs(seconds);
  if (abs < 1) return w.ms(significant(seconds * 1000, compact ? 0 : 1, locale));
  if (abs < 60) return w.secondsExact(significant(seconds, compact ? 1 : 2, locale));
  if (abs < 3600) {
    const minutes = Math.floor(abs / 60);
    const rest = Math.round(abs % 60);
    return compact ? w.m(minutes) : `${w.m(minutes)} ${w.s(rest)}`;
  }
  if (abs < 86400) {
    const hours = Math.floor(abs / 3600);
    const rest = Math.round((abs % 3600) / 60);
    return compact ? w.h(hours) : `${w.h(hours)} ${w.m(rest)}`;
  }
  const days = Math.floor(abs / 86400);
  const rest = Math.round((abs % 86400) / 3600);
  return compact ? w.d(days) : `${w.d(days)} ${w.h(rest)}`;
}

/**
 * `locale` is a trailing optional so every existing call is unchanged; a
 * component passes `useLocale().locale` to get "4,20 sn" instead of "4.20 s".
 */
export function formatUnit(
  value: number | null | undefined,
  unit: Unit | string,
  options: { compact?: boolean } = {},
  locale: Locale = "en",
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return MISSING;
  const compact = options.compact ?? false;
  switch (unit) {
    case "bytes":
      return bytes(value, compact, locale);
    case "cores": {
      const w = unitWords(locale);
      return `${significant(value, compact ? 1 : 2, locale)} ${Math.abs(value) === 1 ? w.core : w.cores}`;
    }
    case "currency_usd":
      return value.toLocaleString(locale === "en" ? "en-US" : LOCALE_TAGS[locale], {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: compact && Math.abs(value) >= 1000 ? 0 : 2,
      });
    case "duration_seconds":
    case "seconds":
      return duration(value, compact, locale);
    case "milliseconds":
      return duration(value / 1000, compact, locale);
    case "percent":
      return `${significant(value, compact ? 0 : 1, locale)}%`;
    case "ratio":
      return `${significant(value * 100, compact ? 0 : 2, locale)}%`;
    case "requests_per_second":
      return `${thousands(value, compact, locale)} ${unitWords(locale).requestsPerSecond}`;
    case "restarts":
    case "count":
      return thousands(Math.round(value), compact, locale);
    default:
      return thousands(value, compact, locale);
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

/** `UNIT_LABELS`, keyed onto the shared `common.unit.*` words. */
const UNIT_KEYS = {
  bytes: "unit.bytes",
  cores: "unit.cores",
  count: "unit.count",
  currency_usd: "unit.usd",
  duration_seconds: "unit.duration",
  milliseconds: "unit.duration",
  percent: "unit.percent",
  ratio: "unit.percent",
  requests_per_second: "unit.requestsPerSecond",
  restarts: "unit.restarts",
  seconds: "unit.duration",
} as const;

/**
 * `UNIT_LABELS[unit]` in the reader's language. A unit the table does not
 * know comes back as itself — it is an identifier from the API, not copy.
 */
export function unitLabel(unit: Unit | string, locale: Locale = "en"): string {
  const key = (UNIT_KEYS as Record<string, (typeof UNIT_KEYS)[keyof typeof UNIT_KEYS]>)[unit];
  if (!key) return unit;
  if (locale === "en") return UNIT_LABELS[unit];
  return getTranslator("common", locale)(key);
}

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
  locale: Locale = "en",
): string {
  if (locale !== "en") return formattersFor(locale).relative(value, now);
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
