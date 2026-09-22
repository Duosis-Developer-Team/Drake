"use client";

/**
 * Incident display primitives.
 *
 * Every badge and label here is a rendering of a value the API sent. None
 * of them derives a state from a timestamp or a status from a reason —
 * that arithmetic belongs to the processor, which is the only place it can
 * be tested against a database.
 */

import { Inbox } from "lucide-react";

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
import { RingProgress } from "@/components/charts/visuals";
import { Panel } from "@/components/ui/Panel";
import { DataState } from "@/components/state/DataState";
import type { StatusTone } from "@/lib/design/status";
import { useT } from "@/lib/i18n";

/**
 * One KPI tile for a list screen's summary row: a big honest count with a
 * ring showing its share of the page. When the page has nothing at all, the
 * share is `null` rather than a fabricated 0% — the ring renders as an
 * empty, muted track instead of implying a measurement that never happened.
 */
export function IncidentKpiTile({
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
  const t = useT("incidents");
  const share = total > 0 ? count / total : null;
  return (
    <Panel data-testid={`incident-kpi-${label.toLowerCase().replace(/\s+/g, "-")}`}>
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
          label={t("kpi.shareLabel", { label })}
          tone={tone}
          size={48}
        />
      </div>
    </Panel>
  );
}

/**
 * A calm, intentional empty state for the incident list — a muted ring
 * medallion above the honest explanation, rather than a bare sentence
 * floating in a mostly-empty card.
 */
export function IncidentsEmptyCard({
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

/** Lifecycle state → badge vocabulary. A plain renaming: `acknowledged` is
 * not "less severe", it is "someone is on it", so it keeps a warning tone
 * rather than borrowing the healthy one. */
const STATE_BADGE: Record<IncidentState, HealthStatus> = {
  open: "critical",
  acknowledged: "warning",
  resolved: "healthy",
};

export function IncidentStateBadge({ state }: { state: IncidentState }) {
  const t = useT("incidents");
  return <StatusBadge status={STATE_BADGE[state]} label={t.dyn("state", state, STATE_LABELS[state])} />;
}

export function SeverityBadge({ severity }: { severity: IncidentSeverity }) {
  const t = useT("incidents");
  return <StatusBadge status="critical" label={t.dyn("severity", severity, severity)} />;
}

/** The reason vocabulary belongs to service health; its owner keys the
 * catalogue by the same codes, so this resolves there and falls back to the
 * English record until that lands. */
export function ReasonLabel({ reason }: { reason: string }) {
  const health = useT("serviceHealth");
  return <span>{health.dyn("reason", reason, REASON_LABELS[reason] ?? reason)}</span>;
}

export function ReasonList({ reasons }: { reasons: string[] }) {
  const t = useT("incidents");
  if (reasons.length === 0) {
    return <p className="text-caption italic text-ink-muted">{t("reasons.none")}</p>;
  }
  return (
    <ul className="flex flex-wrap gap-1.5" data-testid="incident-reasons">
      {reasons.map((reason) => (
        <li
          key={reason}
          className="rounded-full bg-surface-2 px-3 py-1 text-caption text-ink-secondary"
        >
          <ReasonLabel reason={reason} />
        </li>
      ))}
    </ul>
  );
}

/**
 * The three-stage lifecycle, as a horizontal progress stepper.
 *
 * Purely a rendering of state the API already sent — `state`,
 * `acknowledged_at` and `resolved_at` — as a shape instead of three lines of
 * a definition list. A step lights up only once its timestamp exists; an
 * open incident stops at step one with the rest visibly, honestly grey.
 */
export function IncidentLifecycle({
  openedAt,
  acknowledgedAt,
  resolvedAt,
}: {
  openedAt: string;
  acknowledgedAt: string | null;
  resolvedAt: string | null;
}) {
  const t = useT("incidents");
  const steps: { key: string; label: string; time: string | null; tone: StatusTone }[] = [
    { key: "opened", label: t("step.opened"), time: openedAt, tone: "critical" },
    {
      key: "acknowledged",
      label: t("step.acknowledged"),
      time: acknowledgedAt,
      tone: "warning",
    },
    { key: "resolved", label: t("step.resolved"), time: resolvedAt, tone: "success" },
  ];
  return (
    <div className="flex items-start" data-testid="incident-lifecycle">
      {steps.map((step, index) => {
        const done = Boolean(step.time);
        const nextDone = index < steps.length - 1 ? Boolean(steps[index + 1].time) : false;
        return (
          <div key={step.key} className="flex min-w-0 flex-1 items-start last:flex-none">
            <div className="flex shrink-0 flex-col items-center gap-1.5 text-center">
              <span
                aria-hidden
                className={`flex h-8 w-8 items-center justify-center rounded-full text-caption font-semibold ${
                  done ? `text-ink-inverse ${step.tone === "critical" ? "bg-critical" : step.tone === "warning" ? "bg-warning" : "bg-healthy"}` : "bg-surface-3 text-ink-muted"
                }`}
              >
                {index + 1}
              </span>
              <span className={`text-micro font-medium ${done ? "text-ink" : "text-ink-muted"}`}>
                {step.label}
              </span>
              <span className="font-mono text-micro text-ink-muted">{step.time ?? "—"}</span>
            </div>
            {index < steps.length - 1 ? (
              <span
                aria-hidden
                className={`mt-4 h-0.5 flex-1 rounded-full ${nextDone ? "bg-ink-muted" : "bg-surface-3"}`}
              />
            ) : null}
          </div>
        );
      })}
    </div>
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
  const t = useT("incidents");
  if (events.length === 0) {
    return <p className="text-sm text-ink-secondary">{t("timeline.empty")}</p>;
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
            <p className="text-sm font-medium text-ink">
              {t.dyn("event", event.event_type, EVENT_LABELS[event.event_type])}
            </p>
            <p className="text-xs text-ink-secondary">
              {t.dyn("eventDescription", event.event_type, EVENT_DESCRIPTIONS[event.event_type])}
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
