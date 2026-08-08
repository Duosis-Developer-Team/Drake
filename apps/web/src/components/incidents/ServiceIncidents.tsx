"use client";

/**
 * Recent incidents and health transitions for one bound service.
 *
 * Lives on the service health detail screen, where someone looking at a
 * red service needs to know two things immediately: is this already an
 * open incident, and how long has it been like this.
 */

import Link from "next/link";

import { useApi } from "@/components/catalog/primitives";
import { IncidentStateBadge, ReasonLabel } from "@/components/incidents/primitives";
import { DataState } from "@/components/state/DataState";
import { StatusBadge } from "@/components/state/StatusBadge";
import { Card } from "@/components/ui/Card";
import {
  formatDuration,
  type HealthTransition,
  type IncidentSummary,
} from "@/lib/incidents";

export function ServiceIncidents({ bindingId }: { bindingId: string }) {
  const [page, retry] = useApi<{ items: IncidentSummary[]; total: number }>(
    `/v1/service-health/bindings/${bindingId}/incidents`,
  );

  const active = page.state === "ready" ? page.data.items.find((i) => i.state !== "resolved") : null;

  return (
    <Card title="Incidents" data-testid="service-incidents">
      {active ? (
        // The one thing worth surfacing above everything else: this is a
        // live incident, and here is where to go.
        <div className="mb-3 rounded-lg border border-critical/40 bg-critical-soft px-3 py-2">
          <Link
            href={`/incidents/${active.id}`}
            className="text-sm font-medium text-critical hover:underline"
          >
            Open incident: {active.title}
          </Link>
          <p className="mt-0.5 text-[11px] text-critical">
            Running for {formatDuration(active.opened_at, null)}
          </p>
        </div>
      ) : null}

      {page.state === "loading" ? <DataState kind="loading" /> : null}
      {page.state === "error" ? (
        <DataState kind="error" description={page.message} onRetry={retry} />
      ) : null}
      {page.state === "ready" && page.data.items.length === 0 ? (
        <DataState
          kind="empty"
          title="No incidents"
          description="This service has not had an incident opened for it."
        />
      ) : null}
      {page.state === "ready" && page.data.items.length > 0 ? (
        <ul className="space-y-2" data-testid="recent-incidents">
          {page.data.items.map((incident) => (
            <li key={incident.id} className="flex flex-wrap items-center gap-2">
              <IncidentStateBadge state={incident.state} />
              <Link
                href={`/incidents/${incident.id}`}
                className="text-xs font-medium text-ink hover:underline"
              >
                {incident.title}
              </Link>
              <span className="text-[11px] text-ink-muted">
                <time className="font-mono">{incident.opened_at}</time> ·{" "}
                {formatDuration(incident.opened_at, incident.resolved_at)}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </Card>
  );
}

/** Status badge vocabulary for a transition row. A pure renaming of the
 * status the API recorded — nothing here re-derives one. */
const TRANSITION_BADGE = {
  healthy: "healthy",
  degraded: "warning",
  critical: "critical",
  unknown: "unknown",
  stale: "stale",
  not_configured: "maintenance",
} as const;

export function HealthTransitions({ bindingId }: { bindingId: string }) {
  const [history, retry] = useApi<{ transitions: HealthTransition[] }>(
    `/v1/service-health/bindings/${bindingId}/transitions`,
  );

  return (
    <Card title="Recent health changes" data-testid="health-transitions">
      {history.state === "loading" ? <DataState kind="loading" /> : null}
      {history.state === "error" ? (
        <DataState kind="error" description={history.message} onRetry={retry} />
      ) : null}
      {history.state === "ready" && history.data.transitions.length === 0 ? (
        <DataState
          kind="empty"
          title="No recorded changes"
          description="Drake records a row when the status or its reasons change, not on every evaluation."
        />
      ) : null}
      {history.state === "ready" && history.data.transitions.length > 0 ? (
        <ul className="space-y-2">
          {history.data.transitions.map((entry) => (
            <li
              key={`${entry.computed_at}-${entry.new_status}`}
              className="flex flex-wrap items-center gap-2"
            >
              {entry.previous_status ? (
                <>
                  <StatusBadge
                    status={TRANSITION_BADGE[entry.previous_status]}
                    label={entry.previous_status}
                  />
                  <span aria-hidden className="text-ink-muted">
                    →
                  </span>
                </>
              ) : (
                <span className="text-[11px] italic text-ink-muted">first observation</span>
              )}
              <StatusBadge
                status={TRANSITION_BADGE[entry.new_status]}
                label={entry.new_status}
              />
              <span className="text-[11px] text-ink-secondary">
                {entry.reasons.map((reason, index) => (
                  <span key={reason}>
                    {index > 0 ? ", " : ""}
                    <ReasonLabel reason={reason} />
                  </span>
                ))}
              </span>
              <time className="font-mono text-[11px] text-ink-muted">{entry.computed_at}</time>
            </li>
          ))}
        </ul>
      ) : null}
    </Card>
  );
}
