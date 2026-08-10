"use client";

/**
 * Protection display primitives.
 *
 * Two axes, two badges, deliberately never merged into one traffic light.
 * A backup that is fresh, checksummed and offsite but has never been
 * restored is genuinely different from one that has been — and a single
 * green tick would hide exactly that difference.
 */

import { StatusBadge, type HealthStatus } from "@/components/state/StatusBadge";
import {
  BACKUP_LABELS,
  OVERALL_LABELS,
  RECOVERABILITY_LABELS,
  REASON_LABELS,
  type BackupState,
  type OverallState,
  type RecoverabilityState,
} from "@/lib/protection";

const BACKUP_BADGE: Record<BackupState, HealthStatus> = {
  protected: "healthy",
  at_risk: "warning",
  overdue: "warning",
  failed: "critical",
  unknown: "unknown",
};

// `unverified` is not a failure: nobody has tried yet. Colouring it red
// would make the ones that actually failed harder to see.
const RECOVERABILITY_BADGE: Record<RecoverabilityState, HealthStatus> = {
  verified: "healthy",
  unverified: "maintenance",
  failed: "critical",
  unknown: "unknown",
};

const OVERALL_BADGE: Record<OverallState, HealthStatus> = {
  recoverable_verified: "healthy",
  protected_unverified: "maintenance",
  at_risk: "warning",
  overdue: "warning",
  failed: "critical",
  unknown: "unknown",
};

export function BackupBadge({ state }: { state: BackupState }) {
  return <StatusBadge status={BACKUP_BADGE[state]} label={BACKUP_LABELS[state]} />;
}

export function RecoverabilityBadge({ state }: { state: RecoverabilityState }) {
  return (
    <StatusBadge
      status={RECOVERABILITY_BADGE[state]}
      label={RECOVERABILITY_LABELS[state]}
    />
  );
}

export function OverallBadge({ state }: { state: OverallState }) {
  return <StatusBadge status={OVERALL_BADGE[state]} label={OVERALL_LABELS[state]} />;
}

export function ReasonList({ reasons }: { reasons: string[] }) {
  if (reasons.length === 0) {
    return (
      <p className="text-xs text-ink-secondary">
        Every requirement this policy states is currently met.
      </p>
    );
  }
  return (
    <ul className="space-y-1" data-testid="protection-reasons">
      {reasons.map((reason) => (
        <li key={reason} className="text-xs text-ink-secondary">
          {REASON_LABELS[reason] ?? reason}
        </li>
      ))}
    </ul>
  );
}

/** A count chip for the summary strip. Zero renders as zero — that is a
 * measured count, not a missing one. */
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
