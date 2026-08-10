"use client";

/**
 * Deployment display primitives.
 *
 * Every badge renders a state the backend decided. Nothing here derives a
 * rollout state from replica counts or an evidence grade from what happens
 * to be present — that arithmetic belongs to the server, where it can be
 * tested against a database.
 */

import { StatusBadge, type HealthStatus } from "@/components/state/StatusBadge";
import {
  EVIDENCE_DESCRIPTIONS,
  EVIDENCE_LABELS,
  ROLLOUT_LABELS,
  VERDICT_LABELS,
  type ComparisonVerdict,
  type EvidenceState,
  type RolloutState,
} from "@/lib/deployments";

const ROLLOUT_BADGE: Record<RolloutState, HealthStatus> = {
  pending: "unknown",
  progressing: "maintenance",
  healthy: "healthy",
  degraded: "warning",
  failed: "critical",
  stalled: "warning",
  unknown: "unknown",
};

// `unverified` is not a failure — it is an absence of evidence, and
// colouring it red would train people to ignore the ones that are real.
const EVIDENCE_BADGE: Record<EvidenceState, HealthStatus> = {
  verified: "healthy",
  partial: "maintenance",
  unverified: "unknown",
  conflict: "critical",
};

const VERDICT_BADGE: Record<ComparisonVerdict, HealthStatus> = {
  improved: "healthy",
  stable: "maintenance",
  regressed: "critical",
  insufficient_data: "unknown",
};

export function RolloutBadge({ state }: { state: RolloutState }) {
  return <StatusBadge status={ROLLOUT_BADGE[state]} label={ROLLOUT_LABELS[state]} />;
}

export function EvidenceBadge({ state }: { state: EvidenceState }) {
  return (
    <span title={EVIDENCE_DESCRIPTIONS[state]}>
      <StatusBadge status={EVIDENCE_BADGE[state]} label={EVIDENCE_LABELS[state]} />
    </span>
  );
}

export function VerdictBadge({ verdict }: { verdict: ComparisonVerdict }) {
  return <StatusBadge status={VERDICT_BADGE[verdict]} label={VERDICT_LABELS[verdict]} />;
}

/** A digest or commit, shortened. The full value is on the detail record;
 * 64 hex characters in a table is noise, not information. */
export function ShortRef({
  value,
  label,
}: {
  value: string | null;
  label: string;
}) {
  if (!value) {
    return <span className="text-xs italic text-ink-muted">no {label}</span>;
  }
  return (
    <span className="font-mono text-[11px] text-ink-secondary" title={label}>
      {value}
    </span>
  );
}
