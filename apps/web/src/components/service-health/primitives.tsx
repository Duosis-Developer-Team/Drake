"use client";

/**
 * Service-health display primitives.
 *
 * Everything here renders a decision the API already made. There is no
 * threshold, no comparison against a limit, and no place where a status is
 * inferred from a number — if this file could compute "healthy", the
 * backend and the browser could disagree, and the screen would be the one
 * people believe.
 */

import {
  REASON_LABELS,
  SIGNAL_LABELS,
  formatSignal,
  type ServiceHealthStatus,
  type SignalValue,
} from "@/lib/serviceHealth";
import { StatusBadge, type HealthStatus } from "@/components/state/StatusBadge";

/** API status → badge vocabulary. A pure renaming: no status is merged
 * into another, and `not_configured` never borrows the healthy colour. */
const BADGE_STATUS: Record<ServiceHealthStatus, HealthStatus> = {
  healthy: "healthy",
  degraded: "warning",
  critical: "critical",
  unknown: "unknown",
  stale: "stale",
  not_configured: "maintenance",
};

const STATUS_LABELS: Record<ServiceHealthStatus, string> = {
  healthy: "Healthy",
  degraded: "Degraded",
  critical: "Critical",
  unknown: "Unknown",
  stale: "Stale",
  not_configured: "Not configured",
};

export function HealthBadge({ status }: { status: ServiceHealthStatus }) {
  return <StatusBadge status={BADGE_STATUS[status]} label={STATUS_LABELS[status]} />;
}

/** What happened to one signal, in words, when it is not a plain value. */
const SIGNAL_STATE_LABELS: Record<string, string> = {
  empty: "no data",
  failed: "query failed",
  not_configured: "no datasource",
  not_collected: "not collected",
  stale: "stale",
};

/**
 * One measurement. An absent value renders as a dash plus the reason it is
 * absent — never as `0`, and never as a blank cell that reads like zero.
 */
export function SignalCell({ signal, unit }: { signal: SignalValue; unit: string }) {
  const note = SIGNAL_STATE_LABELS[signal.state];
  return (
    <span className="inline-flex items-baseline gap-1.5" data-testid={`signal-${signal.state}`}>
      <span className="font-mono text-xs text-ink">{formatSignal(signal.value, unit)}</span>
      {note ? <span className="text-[11px] italic text-ink-muted">{note}</span> : null}
      {signal.state === "ok" && signal.from_cache ? (
        <span className="text-[11px] italic text-ink-muted">cached</span>
      ) : null}
    </span>
  );
}

/** A bare number that may be null. Same rule: a dash, never a zero. */
export function Measure({ value, unit }: { value: number | null | undefined; unit: string }) {
  return (
    <span className="font-mono text-xs text-ink">
      {formatSignal(value ?? null, unit)}
    </span>
  );
}

export function ReasonList({ reasons, messages }: { reasons: string[]; messages?: string[] }) {
  if (reasons.length === 0) return null;
  return (
    <ul className="space-y-1" data-testid="reason-list">
      {reasons.map((reason, index) => (
        <li key={reason} className="text-xs text-ink-secondary">
          <span className="font-medium text-ink">{REASON_LABELS[reason] ?? reason}</span>
          {messages?.[index] ? <span> — {messages[index]}</span> : null}
        </li>
      ))}
    </ul>
  );
}

export function MissingSignals({ missing }: { missing: string[] }) {
  if (missing.length === 0) return null;
  return (
    <p className="text-xs text-ink-secondary" data-testid="missing-signals">
      <span className="font-medium text-ink">Not measured:</span>{" "}
      {missing.map((name) => SIGNAL_LABELS[name] ?? name).join(", ")}
    </p>
  );
}

/**
 * The banner above a partial or last-good answer.
 *
 * Shown before the numbers, not after them: a reader who takes the values
 * at face value and stops has still been told what they are looking at.
 */
export function FreshnessNotice({
  partial,
  servedFromLastGood,
  computedAt,
  servedAt,
  newestSampleAt,
}: {
  partial: boolean;
  servedFromLastGood: boolean;
  computedAt: string;
  servedAt?: string;
  newestSampleAt?: string | null;
}) {
  if (!partial && !servedFromLastGood) return null;
  return (
    <div
      role="status"
      data-testid="freshness-notice"
      className="rounded-lg border border-stale/40 bg-stale-soft px-3 py-2 text-xs text-stale"
    >
      {servedFromLastGood ? (
        <p>
          <span className="font-medium">Last known values.</span> The datasource could not
          be reached, so this is the most recent successful reading — computed{" "}
          <time className="font-mono">{computedAt}</time>
          {servedAt ? (
            <>
              {" "}
              and served <time className="font-mono">{servedAt}</time>
            </>
          ) : null}
          .
        </p>
      ) : (
        <p>
          <span className="font-medium">Partial result.</span> Some signals were
          unavailable, so this answer does not cover everything.
        </p>
      )}
      {newestSampleAt ? (
        <p className="mt-1">
          Newest sample: <time className="font-mono">{newestSampleAt}</time>
        </p>
      ) : null}
    </div>
  );
}

/** How a binding itself stands, separately from what it measured. */
export function BindingStateBadge({
  lifecycle,
  resolved,
}: {
  lifecycle: string;
  resolved: boolean;
}) {
  if (lifecycle !== "active") {
    return <StatusBadge status="maintenance" label="Disabled" />;
  }
  return resolved ? (
    <StatusBadge status="healthy" label="Resolved" />
  ) : (
    <StatusBadge status="unknown" label="Unresolved" />
  );
}
