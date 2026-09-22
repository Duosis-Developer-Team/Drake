"use client";

/**
 * Deployment display primitives.
 *
 * Every badge renders a state the backend decided. Nothing here derives a
 * rollout state from replica counts or an evidence grade from what happens
 * to be present — that arithmetic belongs to the server, where it can be
 * tested against a database.
 *
 * Labels come from the `deployments` catalogue via `t.dyn`; the English
 * records in lib/deployments.ts are the fallback for a token the catalogue
 * does not know.
 */

import { PackageSearch } from "lucide-react";

import { StatusBadge, type HealthStatus } from "@/components/state/StatusBadge";
import { RingProgress } from "@/components/charts/visuals";
import { DataState } from "@/components/state/DataState";
import { Panel } from "@/components/ui/Panel";
import type { StatusTone } from "@/lib/design/status";
import { useT } from "@/lib/i18n";
import {
  EVIDENCE_DESCRIPTIONS,
  EVIDENCE_LABELS,
  ROLLOUT_LABELS,
  VERDICT_LABELS,
  type ComparisonVerdict,
  type EvidenceState,
  type RolloutState,
} from "@/lib/deployments";

/**
 * One KPI tile for the deployments summary row: a big honest count with a
 * ring showing its share of the page. A `null` share (an empty page) renders
 * as an empty, muted track rather than a fabricated 0%.
 */
export function DeploymentKpiTile({
  label,
  count,
  total,
  tone,
}: {
  label: string;
  count: number;
  total: number;
  tone: StatusTone;
}) {
  const t = useT("deployments");
  const share = total > 0 ? count / total : null;
  return (
    <Panel data-testid={`deployment-kpi-${label.toLowerCase().replace(/\s+/g, "-")}`}>
      <div className="flex items-center justify-between gap-4">
        <div className="min-w-0">
          <p className="text-caption text-ink-muted">{label}</p>
          <p
            data-tabular
            className="mt-1 text-[2rem] leading-none font-semibold tracking-[-0.03em] text-ink"
          >
            {count}
          </p>
        </div>
        <RingProgress
          value={share}
          unit="ratio"
          label={t("kpi.share", { label })}
          tone={tone}
          size={48}
        />
      </div>
    </Panel>
  );
}

/** A calm, intentional empty state — a muted medallion above the honest
 *  explanation, for a screen with little or no data. */
export function DeploymentsEmptyCard({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <Panel className="items-center gap-4 py-12 text-center">
      <span
        aria-hidden
        className="flex h-14 w-14 items-center justify-center rounded-full bg-surface-3 ring-8 ring-surface-2"
      >
        <PackageSearch className="h-6 w-6 text-ink-muted" />
      </span>
      <DataState kind="empty" title={title} description={description} />
    </Panel>
  );
}

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

/** The tone to draw an evidence ring in — the same meaning as
 *  `EVIDENCE_BADGE`, in the vocabulary `RingProgress` and other charts take. */
export const EVIDENCE_TONE: Record<EvidenceState, StatusTone> = {
  verified: "success",
  partial: "info",
  unverified: "unknown",
  conflict: "critical",
};

/** The tone to draw a rollout ring or rail in — the same meaning as
 *  `ROLLOUT_BADGE`, in the vocabulary `RingProgress` and other charts take. */
export const ROLLOUT_TONE: Record<RolloutState, StatusTone> = {
  pending: "unknown",
  progressing: "info",
  healthy: "success",
  degraded: "warning",
  failed: "critical",
  stalled: "warning",
  unknown: "unknown",
};

const DIRECTION_BADGE: Record<"improved" | "regressed" | "stable" | "unknown", HealthStatus> = {
  improved: "healthy",
  regressed: "critical",
  stable: "maintenance",
  unknown: "unknown",
};

/** A before/after signal's direction, as a small tone-coloured badge instead
 *  of plain text — `unknown` keeps saying "not measured" rather than
 *  claiming a direction nobody observed. */
export function SignalDirectionBadge({
  direction,
}: {
  direction: "improved" | "regressed" | "stable" | "unknown";
}) {
  const t = useT("deployments");
  return (
    <StatusBadge
      status={DIRECTION_BADGE[direction]}
      label={t.dyn("direction", direction, direction)}
      size="compact"
    />
  );
}

export function RolloutBadge({ state }: { state: RolloutState }) {
  const t = useT("deployments");
  return (
    <StatusBadge status={ROLLOUT_BADGE[state]} label={t.dyn("rollout", state, ROLLOUT_LABELS[state])} />
  );
}

export function EvidenceBadge({ state }: { state: EvidenceState }) {
  const t = useT("deployments");
  return (
    <span title={t.dyn("evidenceDescription", state, EVIDENCE_DESCRIPTIONS[state])}>
      <StatusBadge
        status={EVIDENCE_BADGE[state]}
        label={t.dyn("evidence", state, EVIDENCE_LABELS[state])}
      />
    </span>
  );
}

export function VerdictBadge({ verdict }: { verdict: ComparisonVerdict }) {
  const t = useT("deployments");
  return (
    <StatusBadge
      status={VERDICT_BADGE[verdict]}
      label={t.dyn("verdict", verdict, VERDICT_LABELS[verdict])}
    />
  );
}

/** A digest or commit, shortened. The full value is on the detail record;
 * 64 hex characters in a table is noise, not information. `label` is the
 * already-translated noun ("image digest"). */
export function ShortRef({
  value,
  label,
}: {
  value: string | null;
  label: string;
}) {
  const t = useT("deployments");
  if (!value) {
    return <span className="text-xs italic text-ink-muted">{t("ref.missing", { label })}</span>;
  }
  return (
    <span className="font-mono text-[11px] text-ink-secondary" title={label}>
      {value}
    </span>
  );
}
