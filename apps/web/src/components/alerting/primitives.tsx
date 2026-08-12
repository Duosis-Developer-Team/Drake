"use client";

/**
 * Alerting and SLO display primitives.
 *
 * Colour carries meaning here, so it is assigned once rather than at each
 * call site. Two choices worth stating:
 *
 * `insufficient_data` and `not_configured` are NOT green. Nothing was
 * measured, and a green tick for an unmeasured objective is the single most
 * misleading thing this screen could show.
 *
 * A pending silence is NOT the same colour as an active one. It suppresses
 * nothing until Alertmanager confirms it, and an operator who reads it as
 * active will stop watching an alert that is still notifying.
 */

import { StatusBadge, type HealthStatus } from "@/components/state/StatusBadge";
import {
  MAPPING_LABELS,
  PRIORITY_LABELS,
  SEVERITY_LABELS,
  SILENCE_LABELS,
  SLO_LABELS,
  formatBurn,
  type BurnRate,
  type MappingState,
  type Priority,
  type Severity,
  type SilenceState,
  type SloStatus,
} from "@/lib/alerting";

const SEVERITY_BADGE: Record<Severity, HealthStatus> = {
  critical: "critical",
  high: "warning",
  medium: "maintenance",
  info: "maintenance",
  // A severity label Drake did not recognise. Shown as unknown rather than
  // quietly promoted or demoted.
  unknown: "unknown",
};

const PRIORITY_BADGE: Record<Priority, HealthStatus> = {
  P1: "critical",
  P2: "warning",
  P3: "maintenance",
  P4: "maintenance",
};

const MAPPING_BADGE: Record<MappingState, HealthStatus> = {
  mapped: "healthy",
  unmapped: "warning",
  ambiguous: "warning",
};

const SLO_BADGE: Record<SloStatus, HealthStatus> = {
  healthy: "healthy",
  warning: "warning",
  critical: "critical",
  exhausted: "critical",
  // Neither of these is healthy: nothing was measured.
  insufficient_data: "unknown",
  not_configured: "unknown",
  stale: "stale",
  query_failed: "critical",
};

const SILENCE_BADGE: Record<SilenceState, HealthStatus> = {
  pending: "unknown",
  active: "maintenance",
  expired: "unknown",
  failed: "critical",
  cancel_pending: "unknown",
  cancelled: "unknown",
};

export function SeverityBadge({ severity }: { severity: Severity }) {
  return <StatusBadge status={SEVERITY_BADGE[severity]} label={SEVERITY_LABELS[severity]} />;
}

export function PriorityBadge({ priority }: { priority: Priority }) {
  return <StatusBadge status={PRIORITY_BADGE[priority]} label={PRIORITY_LABELS[priority]} />;
}

export function StatusPill({ status }: { status: "firing" | "resolved" }) {
  return (
    <StatusBadge
      status={status === "firing" ? "critical" : "healthy"}
      label={status === "firing" ? "Firing" : "Resolved"}
    />
  );
}

export function MappingBadge({ state }: { state: MappingState }) {
  return <StatusBadge status={MAPPING_BADGE[state]} label={MAPPING_LABELS[state]} />;
}

export function SloBadge({ status }: { status: SloStatus }) {
  return <StatusBadge status={SLO_BADGE[status]} label={SLO_LABELS[status]} />;
}

export function SilenceBadge({ state }: { state: SilenceState }) {
  return <StatusBadge status={SILENCE_BADGE[state]} label={SILENCE_LABELS[state]} />;
}

/**
 * The multi-window burn table.
 *
 * Both windows are shown for every level, because "active" here means both
 * exceeded the factor — showing only a combined verdict would hide why a
 * level did or did not fire, which is the question an operator actually has.
 */
export function BurnTable({ rates }: { rates: BurnRate[] }) {
  if (rates.length === 0) {
    return (
      <p className="text-xs text-ink-secondary">
        No burn-rate profile has been evaluated for this objective yet.
      </p>
    );
  }
  return (
    <div className="w-full min-w-0 max-w-full overflow-x-auto [contain:paint]">
    <table className="w-full text-left text-xs" data-testid="burn-table">
      <thead className="text-ink-muted">
        <tr>
          <th className="pb-1.5 pr-3 font-medium">Level</th>
          <th className="pb-1.5 pr-3 font-medium">Threshold</th>
          <th className="pb-1.5 pr-3 font-medium">Long window</th>
          <th className="pb-1.5 pr-3 font-medium">Short window</th>
          <th className="pb-1.5 font-medium">State</th>
        </tr>
      </thead>
      <tbody>
        {rates.map((rate) => (
          <tr key={rate.name} className="border-t border-border">
            <td className="py-1.5 pr-3 font-mono text-ink">{rate.name}</td>
            <td className="py-1.5 pr-3 text-ink-secondary">{rate.factor}×</td>
            <td className="py-1.5 pr-3 text-ink-secondary">
              {formatBurn(rate.long_burn_rate)}
            </td>
            <td className="py-1.5 pr-3 text-ink-secondary">
              {formatBurn(rate.short_burn_rate)}
            </td>
            <td className="py-1.5">
              {rate.active ? (
                <StatusBadge
                  status={rate.severity === "critical" ? "critical" : "warning"}
                  label="Active"
                />
              ) : (
                <span className="text-ink-muted">Not active</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
    </div>
  );
}

/** A count chip. Zero renders as zero — a measured count, not a missing one. */
export function CountChip({
  label,
  count,
  tone,
}: {
  label: string;
  count: number;
  tone: HealthStatus;
}) {
  return (
    <div
      className="flex min-w-24 flex-col rounded-lg border border-border px-3 py-2"
      data-testid={`count-${label.toLowerCase().replace(/\s+/g, "-")}`}
    >
      <span className="text-lg font-semibold text-ink">{count}</span>
      <span className="mt-0.5">
        <StatusBadge status={tone} label={label} />
      </span>
    </div>
  );
}

/**
 * Allowlisted alert labels, rendered as key/value chips.
 *
 * The backend already dropped everything outside its allowlist and every
 * value carrying a URL scheme, so there is nothing here to sanitize — but
 * these are still rendered as TEXT and never as links.
 */
export function LabelChips({ labels }: { labels: Record<string, string> }) {
  const entries = Object.entries(labels);
  if (entries.length === 0) {
    return <p className="text-xs text-ink-secondary">This alert carries no safe labels.</p>;
  }
  return (
    <div className="flex flex-wrap gap-1.5" data-testid="alert-labels">
      {entries.map(([key, value]) => (
        <span
          key={key}
          className="rounded border border-border px-1.5 py-0.5 font-mono text-[11px] text-ink-secondary"
        >
          {key}={value}
        </span>
      ))}
    </div>
  );
}
