"use client";

/**
 * Incident display primitives.
 *
 * Every badge and label here is a rendering of a value the API sent. None
 * of them derives a state from a timestamp or a status from a reason —
 * that arithmetic belongs to the processor, which is the only place it can
 * be tested against a database.
 */

import {
  EVENT_DESCRIPTIONS,
  EVENT_LABELS,
  STATE_LABELS,
  type IncidentEvent,
  type IncidentSeverity,
  type IncidentState,
} from "@/lib/incidents";
import { REASON_LABELS } from "@/lib/serviceHealth";
import { StatusBadge, type HealthStatus } from "@/components/state/StatusBadge";

/** Lifecycle state → badge vocabulary. A plain renaming: `acknowledged` is
 * not "less severe", it is "someone is on it", so it keeps a warning tone
 * rather than borrowing the healthy one. */
const STATE_BADGE: Record<IncidentState, HealthStatus> = {
  open: "critical",
  acknowledged: "warning",
  resolved: "healthy",
};

export function IncidentStateBadge({ state }: { state: IncidentState }) {
  return <StatusBadge status={STATE_BADGE[state]} label={STATE_LABELS[state]} />;
}

export function SeverityBadge({ severity }: { severity: IncidentSeverity }) {
  return <StatusBadge status="critical" label={severity} />;
}

export function ReasonLabel({ reason }: { reason: string }) {
  return <span>{REASON_LABELS[reason] ?? reason}</span>;
}

export function ReasonList({ reasons }: { reasons: string[] }) {
  if (reasons.length === 0) {
    return <p className="text-xs italic text-ink-muted">No reason codes recorded.</p>;
  }
  return (
    <ul className="space-y-1" data-testid="incident-reasons">
      {reasons.map((reason) => (
        <li key={reason} className="text-xs text-ink-secondary">
          <ReasonLabel reason={reason} />
        </li>
      ))}
    </ul>
  );
}

/**
 * The lifecycle timeline.
 *
 * Append-only on the server and read-only here: there is no control on
 * this screen that edits or removes an entry, because a timeline someone
 * can tidy up afterwards is not evidence of anything.
 */
export function IncidentTimeline({ events }: { events: IncidentEvent[] }) {
  if (events.length === 0) {
    return <p className="text-sm text-ink-secondary">No lifecycle events recorded yet.</p>;
  }
  return (
    <ol className="space-y-3" data-testid="incident-timeline">
      {events.map((event, index) => (
        <li key={`${event.event_type}-${event.occurred_at}-${index}`} className="flex gap-3">
          <div className="flex flex-col items-center pt-1">
            <span aria-hidden className="h-2 w-2 rounded-full bg-accent" />
            {index < events.length - 1 ? (
              <span aria-hidden className="mt-1 w-px flex-1 bg-border" />
            ) : null}
          </div>
          <div className="min-w-0 pb-1">
            <p className="text-sm font-medium text-ink">{EVENT_LABELS[event.event_type]}</p>
            <p className="text-xs text-ink-secondary">
              {EVENT_DESCRIPTIONS[event.event_type]}
            </p>
            <p className="mt-0.5 text-[11px] text-ink-muted">
              <time className="font-mono">{event.occurred_at}</time>
              {event.actor ? <> · {event.actor}</> : null}
            </p>
          </div>
        </li>
      ))}
    </ol>
  );
}
