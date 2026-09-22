"use client";

/**
 * Protection display primitives.
 *
 * Two axes, two badges, deliberately never merged into one traffic light.
 * A backup that is fresh, checksummed and offsite but has never been
 * restored is genuinely different from one that has been — and a single
 * green tick would hide exactly that difference.
 */

import { AlertTriangle, CheckCircle2 } from "lucide-react";

import { StatusBadge, type HealthStatus } from "@/components/state/StatusBadge";
import type { StatusTone } from "@/lib/design/status";
import { useT } from "@/lib/i18n";
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

const OVERALL_TONE: Record<OverallState, StatusTone> = {
  recoverable_verified: "success",
  protected_unverified: "info",
  at_risk: "warning",
  overdue: "warning",
  failed: "critical",
  unknown: "unknown",
};

/** The overall verdict's tone, for an avatar or a rail. Same mapping as the badge. */
export function overallTone(state: OverallState): StatusTone {
  return OVERALL_TONE[state];
}

// The English label records in lib/protection stay the source of truth for
// non-React code; here they are only the fallback `t.dyn` shows for a token
// the catalogue does not know.
export function BackupBadge({ state }: { state: BackupState }) {
  const t = useT("protection");
  return (
    <StatusBadge
      status={BACKUP_BADGE[state]}
      label={t.dyn("backup", state, BACKUP_LABELS[state])}
    />
  );
}

export function RecoverabilityBadge({ state }: { state: RecoverabilityState }) {
  const t = useT("protection");
  return (
    <StatusBadge
      status={RECOVERABILITY_BADGE[state]}
      label={t.dyn("recoverability", state, RECOVERABILITY_LABELS[state])}
    />
  );
}

export function OverallBadge({ state }: { state: OverallState }) {
  const t = useT("protection");
  return (
    <StatusBadge
      status={OVERALL_BADGE[state]}
      label={t.dyn("overall", state, OVERALL_LABELS[state])}
    />
  );
}

export function ReasonList({ reasons }: { reasons: string[] }) {
  const t = useT("protection");
  if (reasons.length === 0) {
    return (
      <p className="flex items-center gap-2.5 rounded-2xl bg-healthy-soft px-4 py-3 text-caption text-healthy">
        <CheckCircle2 aria-hidden className="h-4 w-4 shrink-0" />
        {t("reasons.allMet")}
      </p>
    );
  }
  return (
    <ul className="flex flex-col gap-2" data-testid="protection-reasons">
      {reasons.map((reason) => (
        <li
          key={reason}
          className="flex items-center gap-3 rounded-2xl bg-surface-2 px-4 py-3 text-body text-ink"
        >
          <span
            aria-hidden
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-warning-soft text-warning"
          >
            <AlertTriangle className="h-4 w-4" />
          </span>
          <span className="font-medium">
            {t.dyn("reason", reason, REASON_LABELS[reason] ?? reason)}
          </span>
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
