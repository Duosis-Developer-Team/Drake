import {
  AlertTriangle,
  Ban,
  CircleDashed,
  CircleOff,
  CircleSlash,
  Clock,
  HelpCircle,
  Inbox,
  PieChart,
  Sigma,
  XCircle,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

/**
 * Data-state primitives. These are semantically distinct and must never be
 * conflated: "no data", "zero", and "query failed" are three different truths.
 * Unknown/stale/partial/estimated are never rendered as healthy or zero.
 */
export type DataStateKind =
  | "loading"
  | "empty"
  | "error"
  | "no-data"
  | "zero"
  | "stale"
  | "partial"
  | "estimated"
  | "unknown"
  | "not-configured"
  | "permission-denied";

interface KindSpec {
  icon: LucideIcon;
  defaultTitle: string;
  defaultDescription: string;
  tone: "neutral" | "warning" | "critical" | "stale";
}

const KIND_SPECS: Record<Exclude<DataStateKind, "loading">, KindSpec> = {
  empty: {
    icon: Inbox,
    defaultTitle: "Nothing here yet",
    defaultDescription: "This collection has no entries.",
    tone: "neutral",
  },
  error: {
    icon: XCircle,
    defaultTitle: "Query failed",
    defaultDescription: "The request did not complete. This is not the same as empty data.",
    tone: "critical",
  },
  "no-data": {
    icon: CircleOff,
    defaultTitle: "No data",
    defaultDescription: "The source returned no datapoints for this window.",
    tone: "neutral",
  },
  zero: {
    icon: Sigma,
    defaultTitle: "Zero",
    defaultDescription: "The source reported an actual value of 0.",
    tone: "neutral",
  },
  stale: {
    icon: Clock,
    defaultTitle: "Stale data",
    defaultDescription: "Showing the last good value; the source has not refreshed in time.",
    tone: "stale",
  },
  partial: {
    icon: PieChart,
    defaultTitle: "Partial data",
    defaultDescription: "Only part of the expected scope is covered.",
    tone: "warning",
  },
  estimated: {
    icon: Sigma,
    defaultTitle: "Estimated",
    defaultDescription: "Derived from a documented estimation method, not an exact measurement.",
    tone: "warning",
  },
  unknown: {
    icon: HelpCircle,
    defaultTitle: "Unknown",
    defaultDescription: "The state cannot be determined and is reported as its own state.",
    tone: "warning",
  },
  "not-configured": {
    icon: CircleDashed,
    defaultTitle: "Not configured",
    defaultDescription: "This capability has no configured source yet.",
    tone: "neutral",
  },
  "permission-denied": {
    icon: Ban,
    defaultTitle: "Permission required",
    defaultDescription: "Your current scope does not include this data.",
    tone: "neutral",
  },
};

const TONE_CLASSES: Record<KindSpec["tone"], string> = {
  neutral: "text-ink-muted",
  warning: "text-warning",
  critical: "text-critical",
  stale: "text-stale",
};

export function DataState({
  kind,
  title,
  description,
  lastSuccessAt,
  onRetry,
}: {
  kind: DataStateKind;
  title?: string;
  description?: string;
  /** For stale states: when the last good value was produced. */
  lastSuccessAt?: string;
  onRetry?: () => void;
}) {
  if (kind === "loading") {
    return (
      <div data-testid="state-loading" aria-busy="true" className="space-y-2.5">
        <div className="h-4 w-2/5 animate-pulse rounded bg-surface-sunken" />
        <div className="h-4 w-4/5 animate-pulse rounded bg-surface-sunken" />
        <div className="h-4 w-3/5 animate-pulse rounded bg-surface-sunken" />
        <span className="sr-only">Loading</span>
      </div>
    );
  }

  const spec = KIND_SPECS[kind];
  const Icon = spec.icon;

  return (
    <div data-testid={`state-${kind}`} role="status" className="flex items-start gap-3 py-1">
      <Icon aria-hidden className={`mt-0.5 h-5 w-5 shrink-0 ${TONE_CLASSES[spec.tone]}`} />
      <div className="min-w-0">
        <p className={`text-sm font-medium ${kind === "error" ? "text-critical" : "text-ink"}`}>
          {title ?? spec.defaultTitle}
        </p>
        <p className="mt-0.5 text-sm text-ink-secondary">{description ?? spec.defaultDescription}</p>
        {kind === "stale" && lastSuccessAt ? (
          <p className="mt-1 text-xs text-stale">
            Last successful update: <time className="font-mono">{lastSuccessAt}</time>
          </p>
        ) : null}
        {kind === "error" && onRetry ? (
          <button
            type="button"
            onClick={onRetry}
            className="mt-2 inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-xs font-medium text-ink-secondary hover:bg-surface-sunken"
          >
            <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
            Retry
          </button>
        ) : null}
      </div>
    </div>
  );
}

/** Small helper used by tests and future screens to assert distinctness. */
export const DISTINCT_STATE_KINDS: DataStateKind[] = [
  "loading",
  "empty",
  "error",
  "no-data",
  "zero",
  "stale",
  "partial",
  "estimated",
  "unknown",
  "not-configured",
  "permission-denied",
];

// Referenced to keep the icon set explicit for future variants.
void CircleSlash;
