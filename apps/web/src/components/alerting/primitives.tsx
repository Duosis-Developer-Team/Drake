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

import { Inbox } from "lucide-react";

import { StatusBadge, type HealthStatus } from "@/components/state/StatusBadge";
import { RingProgress } from "@/components/charts/visuals";
import { DataState } from "@/components/state/DataState";
import { Panel } from "@/components/ui/Panel";
import { toneSpec, type StatusTone } from "@/lib/design/status";
import {
  MAPPING_LABELS,
  PRIORITY_LABELS,
  SEVERITY_LABELS,
  SILENCE_LABELS,
  SLO_LABELS,
  formatAge,
  formatBurn,
  type AlertEvent,
  type BurnRate,
  type MappingState,
  type Priority,
  type Severity,
  type SilenceState,
  type SloStatus,
} from "@/lib/alerting";

/**
 * One KPI tile for the alerts/SLO summary rows: a big honest count with a
 * ring showing its share of the reference total. A `null` share (nothing to
 * divide by) renders as an empty, muted track rather than an invented 0%.
 */
export function AlertingKpiTile({
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
  const share = total > 0 ? count / total : null;
  return (
    <Panel data-testid={`alerting-kpi-${label.toLowerCase().replace(/\s+/g, "-")}`}>
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
        <RingProgress value={share} unit="ratio" label={`${label} share`} tone={tone} size={48} />
      </div>
    </Panel>
  );
}

/** A calm, intentional empty state — a muted medallion above the honest
 *  explanation, for a screen with little or no data rather than a bare
 *  sentence. */
export function AlertingEmptyCard({
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
        <Inbox className="h-6 w-6 text-ink-muted" />
      </span>
      <DataState kind="empty" title={title} description={description} />
    </Panel>
  );
}

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
/**
 * The alert's chain, as a horizontal stepper: firing → mapped into the
 * catalog → an incident opened → still notifying. Each step's tone is a
 * rendering of a fact the API already sent, never a derived judgement —
 * an unmapped alert or a P3 with no incident are not "broken" steps, they
 * are honest, expected states, so their tone stays neutral rather than
 * warning.
 */
export function AlertChain({
  steps,
}: {
  steps: { key: string; label: string; done: boolean; tone: StatusTone; caption?: string }[];
}) {
  return (
    <div className="flex items-start" data-testid="alert-chain">
      {steps.map((step, index) => {
        const nextDone = index < steps.length - 1 ? steps[index + 1].done : false;
        return (
          <div key={step.key} className="flex min-w-0 flex-1 items-start last:flex-none">
            <div className="flex shrink-0 flex-col items-center gap-1.5 text-center">
              <span
                aria-hidden
                className={`flex h-8 w-8 items-center justify-center rounded-full text-caption font-semibold ${
                  step.done ? `text-ink-inverse ${toneSpec(step.tone).dot}` : "bg-surface-3 text-ink-muted"
                }`}
              >
                {index + 1}
              </span>
              <span className="text-micro font-medium text-ink">{step.label}</span>
              {step.caption ? (
                <span className="max-w-24 text-micro text-ink-muted">{step.caption}</span>
              ) : null}
            </div>
            {index < steps.length - 1 ? (
              <span
                aria-hidden
                className={`mt-4 h-0.5 flex-1 rounded-full ${
                  nextDone ? "bg-ink-muted" : "bg-surface-3"
                }`}
              />
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

const ALERT_EVENT_LABELS: Record<string, string> = {
  firing: "Started firing",
  resolved: "Alertmanager reported resolved",
  reopened: "Started firing again",
  suppressed: "Suppressed",
  silenced: "Silenced",
  inhibited: "Inhibited by another alert",
};

/** The alert's event history as a dot timeline, matching the incident
 *  timeline's shape rather than a column of plain text lines. */
export function AlertTimeline({ events }: { events: AlertEvent[] }) {
  if (events.length === 0) {
    return <p className="text-caption text-ink-secondary">No transitions recorded.</p>;
  }
  return (
    <ol className="space-y-3" data-testid="alert-timeline">
      {events.map((event, index) => (
        <li
          key={`${event.source_event_at}-${event.event_type}-${event.occurrence}`}
          className="flex gap-3"
        >
          <div className="flex flex-col items-center pt-1">
            <span aria-hidden className="h-2 w-2 rounded-full bg-accent" />
            {index < events.length - 1 ? (
              <span aria-hidden className="mt-1 w-px flex-1 bg-border" />
            ) : null}
          </div>
          <div className="min-w-0 pb-1">
            <p className="text-caption font-medium text-ink">
              {ALERT_EVENT_LABELS[event.event_type] ?? event.event_type}
            </p>
            <p className="mt-0.5 text-micro text-ink-muted">
              <time className="font-mono">{formatAge(event.source_event_at)}</time> · episode{" "}
              {event.occurrence}
            </p>
          </div>
        </li>
      ))}
    </ol>
  );
}

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
