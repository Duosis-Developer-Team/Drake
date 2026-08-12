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

export const TONES: Record<StatusTone, ToneSpec> = {
  success: {
    icon: CheckCircle2,
    text: "text-healthy",
    chip: "bg-healthy-soft text-healthy",
    dot: "bg-healthy",
    rail: "border-healthy",
    token: "--status-success",
    label: "Healthy",
  },
  info: {
    icon: Info,
    text: "text-info",
    chip: "bg-info-soft text-info",
    dot: "bg-info",
    rail: "border-info",
    token: "--status-info",
    label: "Info",
  },
  warning: {
    icon: AlertTriangle,
    text: "text-warning",
    chip: "bg-warning-soft text-warning",
    dot: "bg-warning",
    rail: "border-warning",
    token: "--status-warning",
    label: "Warning",
  },
  critical: {
    icon: XCircle,
    text: "text-critical",
    chip: "bg-critical-soft text-critical",
    dot: "bg-critical",
    rail: "border-critical",
    token: "--status-critical",
    label: "Critical",
  },
  neutral: {
    icon: MinusCircle,
    text: "text-neutral",
    chip: "bg-neutral-soft text-neutral",
    dot: "bg-neutral",
    rail: "border-neutral",
    token: "--status-neutral",
    label: "Neutral",
  },
  unknown: {
    icon: HelpCircle,
    text: "text-unknown",
    chip: "bg-unknown-soft text-unknown",
    dot: "bg-unknown",
    rail: "border-unknown",
    token: "--status-unknown",
    label: "Unknown",
  },
  stale: {
    icon: Clock,
    text: "text-stale",
    chip: "bg-stale-soft text-stale",
    dot: "bg-stale",
    rail: "border-stale",
    token: "--status-stale",
    label: "Stale",
  },
  pending: {
    icon: Loader,
    text: "text-info",
    chip: "bg-info-soft text-info",
    dot: "bg-info",
    rail: "border-info",
    token: "--status-info",
    label: "Pending",
  },
  "not-applicable": {
    icon: CircleSlash,
    text: "text-ink-muted",
    chip: "bg-surface-3 text-ink-muted",
    dot: "bg-ink-muted",
    rail: "border-border",
    token: "--text-muted",
    label: "Not applicable",
  },
  denied: {
    icon: Ban,
    text: "text-ink-muted",
    chip: "bg-surface-3 text-ink-muted",
    dot: "bg-ink-muted",
    rail: "border-border",
    token: "--text-muted",
    label: "Permission required",
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

export function toneSpec(tone: StatusTone): ToneSpec {
  return TONES[tone] ?? TONES.unknown;
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

/** A raw backend token, as a word a person reads. */
export function humanize(value: string | null | undefined): string {
  if (!value) return "Unknown";
  const spaced = value.replace(/[_-]+/g, " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
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

export function thresholdLabel(tone: StatusTone, hasThresholds: boolean): string {
  if (!hasThresholds) return "no threshold set";
  switch (tone) {
    case "critical":
      return "critical threshold breached";
    case "warning":
      return "warning threshold breached";
    case "success":
      return "within threshold";
    default:
      return "not measured";
  }
}

// Referenced so the icon set stays explicit for variants added later.
void CircleDashed;
