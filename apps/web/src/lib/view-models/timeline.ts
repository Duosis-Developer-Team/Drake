/**
 * The correlation timeline.
 *
 * Events on one axis from sources that already exist: incidents opening and
 * resolving, alerts firing and resolving, deployments landing. Nothing here
 * asserts that one event CAUSED another — the brief is explicit that a
 * correlation is "related in time", never a causality claim (§9.3) — so a
 * `TimelineEvent` carries only what happened and when, and drawing several
 * lanes on a shared axis is the reader's own job of spotting a pattern.
 *
 * The service/cluster health lane is always present, even with zero events:
 * the API has no state-transition history endpoint for that lane today, and
 * silently omitting the lane (or worse, drawing an empty flat line that
 * reads as "nothing happened") would fabricate a history Drake does not
 * have. `historyAvailable: false` says so instead.
 */
import type { AlertInstance, Severity } from "@/lib/alerting";
import type { DeploymentRow, RolloutState } from "@/lib/deployments";
import type { IncidentSummary } from "@/lib/incidents";
import type { StatusTone } from "@/lib/design/status";

export type TimelineEventKind =
  | "incident_opened"
  | "incident_resolved"
  | "alert_firing"
  | "alert_resolved"
  | "deployment";

export interface TimelineEvent {
  id: string;
  kind: TimelineEventKind;
  tone: StatusTone;
  at: string;
  /** English rendering of `kind` + `subject`; a component prefers the parts. */
  label: string;
  href: string;
  /** What the event is about — an incident's service, an alert's name, a
   *  deployment's workload. `OperationalTimeline` renders
   *  `commandCenter.timeline.event.<kind>` from it when present. */
  subject?: string;
  /** A deployment's short commit or digest; `null` when neither was reported. */
  revision?: string | null;
}

export interface TimelineLane {
  /** Also the catalogue key: `commandCenter.timeline.lane.<key>`. */
  key: string;
  /** English rendering of `key`, the fallback when the catalogue has no entry. */
  label: string;
  events: TimelineEvent[];
  historyAvailable: boolean;
}

function bySeverity(severity: Severity): StatusTone {
  switch (severity) {
    case "critical":
      return "critical";
    case "high":
    case "medium":
      return "warning";
    case "info":
      return "info";
    default:
      return "unknown";
  }
}

function byRolloutState(state: RolloutState): StatusTone {
  switch (state) {
    case "healthy":
      return "success";
    case "degraded":
    case "stalled":
      return "warning";
    case "failed":
      return "critical";
    case "pending":
    case "progressing":
      return "info";
    default:
      return "unknown";
  }
}

export function incidentEvents(incidents: IncidentSummary[]): TimelineEvent[] {
  const events: TimelineEvent[] = [];
  for (const incident of incidents) {
    const subject = incident.title || incident.service_key || incident.id;
    events.push({
      id: `incident-opened:${incident.id}`,
      kind: "incident_opened",
      // The open moment is always the incident at its worst-known severity;
      // whether it has since been resolved is the separate event below.
      tone: "critical",
      at: incident.opened_at,
      label: `Incident opened: ${subject}`,
      href: `/incidents/${incident.id}`,
      subject,
    });
    if (incident.resolved_at) {
      events.push({
        id: `incident-resolved:${incident.id}`,
        kind: "incident_resolved",
        tone: "success",
        at: incident.resolved_at,
        label: `Incident resolved: ${subject}`,
        href: `/incidents/${incident.id}`,
        subject,
      });
    }
  }
  return events;
}

export function alertEvents(alerts: AlertInstance[]): TimelineEvent[] {
  const events: TimelineEvent[] = [];
  for (const alert of alerts) {
    events.push({
      id: `alert-firing:${alert.id}`,
      kind: "alert_firing",
      tone: bySeverity(alert.severity),
      at: alert.starts_at,
      label: `Alert firing: ${alert.alert_name}`,
      href: `/alerts/${alert.id}`,
      subject: alert.alert_name,
    });
    if (alert.resolved_at) {
      events.push({
        id: `alert-resolved:${alert.id}`,
        kind: "alert_resolved",
        tone: "success",
        at: alert.resolved_at,
        label: `Alert resolved: ${alert.alert_name}`,
        href: `/alerts/${alert.id}`,
        subject: alert.alert_name,
      });
    }
  }
  return events;
}

export function deploymentEvents(deployments: DeploymentRow[]): TimelineEvent[] {
  return deployments.map((deployment) => {
    const revision = deployment.short_commit ?? deployment.short_digest ?? null;
    return {
      id: `deployment:${deployment.id}`,
      kind: "deployment" as const,
      tone: byRolloutState(deployment.rollout_state),
      at: deployment.rollout_started_at,
      label: `Deployment: ${deployment.workload_name} (${revision ?? "unknown revision"})`,
      href: `/deployments/${deployment.id}`,
      subject: deployment.workload_name,
      revision,
    };
  });
}

function ascending(events: TimelineEvent[]): TimelineEvent[] {
  return [...events].sort((a, b) => a.at.localeCompare(b.at));
}

export function buildTimeline(
  incidents: IncidentSummary[],
  alerts: AlertInstance[],
  deployments: DeploymentRow[],
): TimelineLane[] {
  return [
    {
      key: "incidents",
      label: "Incidents",
      events: ascending(incidentEvents(incidents)),
      historyAvailable: true,
    },
    {
      key: "alerts",
      label: "Alerts",
      events: ascending(alertEvents(alerts)),
      historyAvailable: true,
    },
    {
      key: "deployments",
      label: "Deployments",
      events: ascending(deploymentEvents(deployments)),
      historyAvailable: true,
    },
    {
      key: "cluster-service-health",
      label: "Service & cluster health",
      events: [],
      historyAvailable: false,
    },
  ];
}
