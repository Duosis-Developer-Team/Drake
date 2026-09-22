/**
 * The one status vocabulary.
 *
 * Every health-ish value in Drake — a cluster's connection, an inventory
 * freshness, an SLO verdict, a rollout, an alert severity — is mapped onto a
 * single small set of tones here, so the same meaning gets the same colour,
 * the same icon and the same word on every screen.
 *
 * The distinctions this file exists to protect:
 *
 *   `unknown` is not `healthy`. Nothing was measured.
 *   `stale` is not `healthy`. Something was measured, a while ago, and the
 *     source has not answered since.
 *   `not-applicable` is not `unknown`. The question does not apply — an
 *     externally hosted project has no Kubernetes health to be unsure about.
 *   `zero` is not `no-data`. The source answered, and the answer was 0.
 *
 * `critical` is the only tone allowed to be red, and it is reserved for a
 * real breach, failure or destructive action.
 */

import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  CircleDashed,
  CircleSlash,
  Clock,
  HelpCircle,
  Info,
  Loader,
  MinusCircle,
  XCircle,
  type LucideIcon,
} from "lucide-react";

import { getTranslator, type Locale } from "@/lib/i18n";

export type StatusTone =
  | "success"
  | "info"
  | "warning"
  | "critical"
  | "neutral"
  | "unknown"
  | "stale"
  | "pending"
  | "not-applicable"
  | "denied";

export interface ToneSpec {
  /** Icon so the state is never carried by colour alone. */
  icon: LucideIcon;
  /** Foreground colour utility. */
  text: string;
  /** Chip: tinted background plus the same foreground. */
  chip: string;
  /** Solid dot / bar, for legends and severity rails. */
  dot: string;
  /** Left rail on a row or panel. */
  rail: string;
  /** The CSS custom property a chart should use for this tone. */
  token: string;
  /** Default word, when the caller has no better one from the API. */
  label: string;
}

/**
 * `chip`'s soft fill sits close in lightness to the panels it's drawn on —
 * darkening it further to "pop" would cross under the 4.5:1 text-on-chip
 * floor `design-system.test.ts` enforces, and there is no headroom left to
 * spend. A tone-coloured border (3:1 territory, not 4.5:1) buys the same
 * "this is a pill, not plain text" definition without touching that budget.
 */
export const TONES: Record<StatusTone, ToneSpec> = {
  success: {
    icon: CheckCircle2,
    text: "text-healthy",
    chip: "bg-healthy-soft text-healthy border border-healthy/35",
    dot: "bg-healthy",
    rail: "border-healthy",
    token: "--status-success",
    label: "Healthy", // i18n-ignore: English default; toneSpec(tone, locale) localizes
  },
  info: {
    icon: Info,
    text: "text-info",
    chip: "bg-info-soft text-info border border-info/35",
    dot: "bg-info",
    rail: "border-info",
    token: "--status-info",
    label: "Info", // i18n-ignore: English default; toneSpec(tone, locale) localizes
  },
  warning: {
    icon: AlertTriangle,
    text: "text-warning",
    chip: "bg-warning-soft text-warning border border-warning/35",
    dot: "bg-warning",
    rail: "border-warning",
    token: "--status-warning",
    label: "Warning", // i18n-ignore: English default; toneSpec(tone, locale) localizes
  },
  critical: {
    icon: XCircle,
    text: "text-critical",
    chip: "bg-critical-soft text-critical border border-critical/35",
    dot: "bg-critical",
    rail: "border-critical",
    token: "--status-critical",
    label: "Critical", // i18n-ignore: English default; toneSpec(tone, locale) localizes
  },
  neutral: {
    icon: MinusCircle,
    text: "text-neutral",
    chip: "bg-neutral-soft text-neutral border border-neutral/35",
    dot: "bg-neutral",
    rail: "border-neutral",
    token: "--status-neutral",
    label: "Neutral", // i18n-ignore: English default; toneSpec(tone, locale) localizes
  },
  unknown: {
    icon: HelpCircle,
    text: "text-unknown",
    chip: "bg-unknown-soft text-unknown border border-unknown/35",
    dot: "bg-unknown",
    rail: "border-unknown",
    token: "--status-unknown",
    label: "Unknown", // i18n-ignore: English default; toneSpec(tone, locale) localizes
  },
  stale: {
    icon: Clock,
    text: "text-stale",
    chip: "bg-stale-soft text-stale border border-stale/35",
    dot: "bg-stale",
    rail: "border-stale",
    token: "--status-stale",
    label: "Stale", // i18n-ignore: English default; toneSpec(tone, locale) localizes
  },
  pending: {
    icon: Loader,
    text: "text-info",
    chip: "bg-info-soft text-info border border-info/35",
    dot: "bg-info",
    rail: "border-info",
    token: "--status-info",
    label: "Pending", // i18n-ignore: English default; toneSpec(tone, locale) localizes
  },
  "not-applicable": {
    icon: CircleSlash,
    text: "text-ink-muted",
    chip: "bg-surface-3 text-ink-muted border border-border-strong/30",
    dot: "bg-ink-muted",
    rail: "border-border",
    token: "--text-muted",
    label: "Not applicable", // i18n-ignore: English default; toneSpec(tone, locale) localizes
  },
  denied: {
    icon: Ban,
    text: "text-ink-muted",
    chip: "bg-surface-3 text-ink-muted border border-border-strong/30",
    dot: "bg-ink-muted",
    rail: "border-border",
    token: "--text-muted",
    label: "Permission required", // i18n-ignore: English default; toneSpec(tone, locale) localizes
  },
};

/**
 * Attention order, worst first.
 *
 * This is the sort key everywhere a mixed list has to be triaged, and it puts
 * `stale` and `unknown` ABOVE `success` on purpose: an operator needs to see
 * "we do not know" before "we are fine", not filed underneath it.
 */
export const TONE_SEVERITY: Record<StatusTone, number> = {
  critical: 0,
  warning: 1,
  stale: 2,
  unknown: 3,
  denied: 4,
  pending: 5,
  info: 6,
  neutral: 7,
  "not-applicable": 8,
  success: 9,
};

export function compareTone(a: StatusTone, b: StatusTone): number {
  return TONE_SEVERITY[a] - TONE_SEVERITY[b];
}

/**
 * The spec for a tone. With a `locale` the `label` is the catalogue's word
 * for it (`ui.tone.*`); without one it is the English default, so every
 * existing `toneSpec(tone).label` keeps reading exactly as it did.
 */
export function toneSpec(tone: StatusTone, locale: Locale = "en"): ToneSpec {
  const spec = TONES[tone] ?? TONES.unknown;
  if (locale === "en") return spec;
  return { ...spec, label: getTranslator("ui", locale).dyn("tone", tone, spec.label) };
}

/**
 * A backend health word, mapped once.
 *
 * Anything unrecognised becomes `unknown` rather than being guessed into a
 * neighbouring state — a status Drake does not know is not a status Drake can
 * colour green.
 */
const HEALTH_TONES: Record<string, StatusTone> = {
  ok: "success",
  healthy: "success",
  fresh: "success",
  connected: "success",
  active: "success",
  verified: "success",
  resolved: "success",
  succeeded: "success",
  improved: "success",
  mapped: "success",

  degraded: "warning",
  warning: "warning",
  disconnected: "warning",
  reconcile_required: "warning",
  stalled: "warning",
  partial: "warning",
  unmapped: "warning",
  ambiguous: "warning",
  acknowledged: "warning",

  critical: "critical",
  unhealthy: "critical",
  failed: "critical",
  breached: "critical",
  exhausted: "critical",
  revoked: "critical",
  conflict: "critical",
  regressed: "critical",
  open: "critical",
  firing: "critical",
  query_failed: "critical",

  stale: "stale",

  unknown: "unknown",
  insufficient_data: "unknown",
  unverified: "unknown",
  empty: "unknown",

  pending: "pending",
  progressing: "pending",
  reconciling: "pending",
  enrolled: "pending",

  info: "info",
  maintenance: "info",
  silenced: "info",
  stable: "info",

  not_configured: "not-applicable",
  not_applicable: "not-applicable",
  not_collected: "not-applicable",
  disabled: "not-applicable",
  external: "not-applicable",
};

export function toneForHealth(value: string | null | undefined): StatusTone {
  if (!value) return "unknown";
  return HEALTH_TONES[value.toLowerCase()] ?? "unknown";
}

/**
 * A raw backend token, as a word a person reads.
 *
 * Known tokens come from the catalogue (`ui.token.*`, whose English entries
 * equal the spaced-and-capitalised form below, so the default is unchanged);
 * a token the catalogue does not know is still shown, spaced, rather than
 * hidden — an unexpected backend word is information.
 */
export function humanize(value: string | null | undefined, locale: Locale = "en"): string {
  if (!value) return getTranslator("common", locale)("state.unknown");
  const spaced = value.replace(/[_-]+/g, " ").trim();
  const english = spaced.charAt(0).toUpperCase() + spaced.slice(1);
  if (locale === "en") return english;
  return getTranslator("ui", locale).dyn("token", value.toLowerCase(), english);
}

/**
 * Where a measurement sits against its thresholds.
 *
 * The thresholds are the caller's — they come from the API or from configured
 * policy. This function only compares; it never invents a limit, and with no
 * thresholds supplied the answer is `neutral`, not "fine".
 */
export interface Thresholds {
  warn: number;
  critical: number;
  direction: "above" | "below";
}

export function toneForThreshold(
  value: number | null | undefined,
  thresholds: Thresholds | null | undefined,
): StatusTone {
  if (value === null || value === undefined || Number.isNaN(value)) return "unknown";
  if (!thresholds) return "neutral";
  const breached = (limit: number) =>
    thresholds.direction === "above" ? value >= limit : value <= limit;
  if (breached(thresholds.critical)) return "critical";
  if (breached(thresholds.warn)) return "warning";
  return "success";
}

export function thresholdLabel(
  tone: StatusTone,
  hasThresholds: boolean,
  locale: Locale = "en",
): string {
  const t = getTranslator("ui", locale);
  if (!hasThresholds) return t("threshold.none");
  switch (tone) {
    case "critical":
      return t("threshold.critical");
    case "warning":
      return t("threshold.warning");
    case "success":
      return t("threshold.success");
    default:
      return t("threshold.unmeasured");
  }
}

// Referenced so the icon set stays explicit for variants added later.
void CircleDashed;
