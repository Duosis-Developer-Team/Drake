/**
 * The Command Center's aggregation.
 *
 * Composed in the browser from endpoints that already exist rather than by
 * adding a `/v1/overview` — every one of these is already authorized, already
 * scope-filtered, and already the authority on its own state. A new endpoint
 * would have to re-derive all of that, and a second implementation of "is
 * this cluster stale" is exactly how two screens start disagreeing.
 *
 * Nothing here computes health. Each item's tone comes from the word the API
 * used, mapped through the single vocabulary in `lib/design/status`. Where an
 * endpoint is denied, its section says so; it never contributes a zero, which
 * would read as "nothing wrong here".
 */

import type { StatusTone } from "@/lib/design/status";
import { compareTone, humanize, toneForHealth } from "@/lib/design/status";
import type { AlertSummary } from "@/lib/alerting";
import type { Cluster, IntegrationHealth } from "@/lib/catalog";
import type { IncidentSummary } from "@/lib/incidents";
import type { ServiceHealthRow } from "@/lib/serviceHealth";

/** One row in "needs attention". */
export interface AttentionItem {
  key: string;
  tone: StatusTone;
  /** What is wrong, in the API's own vocabulary. */
  state: string;
  /** What it is wrong with. */
  subject: string;
  /** Where it sits — project/environment, cluster, scope. */
  context: string;
  href: string;
  /** When this was last observed, if the source reported it. */
  asOf?: string | null;
  /** Which surface it came from, so the list is traceable. */
  origin: "incident" | "alert" | "cluster" | "service" | "integration";
}

/** Tones that mean "somebody should look at this". */
const NEEDS_ATTENTION: StatusTone[] = ["critical", "warning", "stale", "unknown"];

export function needsAttention(tone: StatusTone): boolean {
  return NEEDS_ATTENTION.includes(tone);
}

export function incidentItems(incidents: IncidentSummary[]): AttentionItem[] {
  return incidents
    .filter((incident) => incident.state !== "resolved")
    .map((incident) => ({
      key: `incident:${incident.id}`,
      tone: incident.state === "open" ? ("critical" as const) : ("warning" as const),
      state: incident.state === "open" ? "Open incident" : "Acknowledged incident",
      subject: incident.service_key || incident.title || incident.id,
      context: [incident.project_key, incident.environment_key].filter(Boolean).join(" / "),
      href: `/incidents/${incident.id}`,
      asOf: incident.last_critical_at || incident.opened_at,
      origin: "incident" as const,
    }));
}

/**
 * Clusters, with connection and health kept apart.
 *
 * A connected agent is not a healthy cluster and a disconnected one is not an
 * unhealthy cluster — it is a cluster Drake cannot see. Both facts get their
 * own row rather than being folded into one verdict.
 */
export function clusterItems(clusters: Cluster[]): AttentionItem[] {
  const items: AttentionItem[] = [];
  for (const cluster of clusters) {
    const agent = cluster.operational?.agent ?? "unknown";
    const inventory = cluster.operational?.inventory ?? "unknown";
    const name = cluster.display_name || cluster.cluster_ref;

    const agentTone = toneForHealth(agent === "ok" ? "connected" : agent);
    if (needsAttention(agentTone)) {
      items.push({
        key: `cluster-agent:${cluster.id}`,
        tone: agentTone,
        state: `Agent ${humanize(agent).toLowerCase()}`,
        subject: name,
        context: "cluster connection",
        href: `/clusters/${cluster.id}`,
        asOf: cluster.as_of,
        origin: "cluster",
      });
    }
    const inventoryTone = toneForHealth(inventory === "ok" ? "fresh" : inventory);
    if (needsAttention(inventoryTone)) {
      items.push({
        key: `cluster-inventory:${cluster.id}`,
        tone: inventoryTone,
        state: `Inventory ${humanize(inventory).toLowerCase()}`,
        subject: name,
        context: "cluster inventory",
        href: `/clusters/${cluster.id}/inventory`,
        asOf: cluster.as_of,
        origin: "cluster",
      });
    }
  }
  return items;
}

export function serviceItems(services: ServiceHealthRow[]): AttentionItem[] {
  return services
    .map((row) => ({ row, tone: toneForHealth(row.health.status) }))
    // `not_configured` is not an incident: it means nobody has bound this
    // service to a workload yet, which belongs on the service-health screen
    // rather than in an operator's triage queue.
    .filter(({ row, tone }) => needsAttention(tone) && row.health.status !== "not_configured")
    .map(({ row, tone }) => ({
      key: `service:${row.environment_service_id}`,
      tone,
      state: `Service ${row.health.status}`,
      subject: row.display_name || row.service_key,
      context: `${row.project_key} / ${row.environment_key}`,
      href: row.binding ? `/service-health/${row.binding.id}` : "/service-health",
      asOf: row.health.newest_sample_at ?? row.health.computed_at,
      origin: "service" as const,
    }));
}

/**
 * Integrations that are configured and unwell.
 *
 * A NOT-configured integration is deliberately excluded: an operator who has
 * not connected a backup reporter does not have a problem, they have an
 * absence, and putting fourteen of those in the triage list buries the one
 * integration that is actually failing.
 */
export function integrationItems(integrations: IntegrationHealth[]): AttentionItem[] {
  return integrations
    .filter((integration) => integration.configuration_state === "configured")
    .map((integration) => ({ integration, tone: toneForHealth(integration.observed_state) }))
    .filter(({ tone }) => needsAttention(tone))
    .map(({ integration, tone }) => ({
      key: `integration:${integration.integration_type}:${integration.scope.ref}`,
      tone,
      state: `${humanize(integration.integration_type)} ${integration.observed_state}`,
      subject: humanize(integration.integration_type),
      context: `${integration.scope.type} ${integration.scope.ref}`,
      href: "/integrations",
      asOf: integration.last_success_at ?? integration.as_of,
      origin: "integration" as const,
    }));
}

export function alertItems(summary: AlertSummary): AttentionItem[] {
  const items: AttentionItem[] = [];
  if (summary.p1 > 0) {
    items.push({
      key: "alerts:p1",
      tone: "critical",
      state: `${summary.p1} P1 alert${summary.p1 === 1 ? "" : "s"} firing`,
      subject: "Priority 1",
      context: "alerting",
      href: "/alerts?priority=P1",
      origin: "alert",
    });
  }
  if (summary.p2 > 0) {
    items.push({
      key: "alerts:p2",
      tone: "warning",
      state: `${summary.p2} P2 alert${summary.p2 === 1 ? "" : "s"} firing`,
      subject: "Priority 2",
      context: "alerting",
      href: "/alerts?priority=P2",
      origin: "alert",
    });
  }
  if (summary.unmapped > 0) {
    items.push({
      key: "alerts:unmapped",
      tone: "unknown",
      state: `${summary.unmapped} firing alert${summary.unmapped === 1 ? "" : "s"} map to no service`,
      subject: "Unmapped alerts",
      context: "alerting",
      href: "/alerts?mapping=unmapped",
      origin: "alert",
    });
  }
  return items;
}

/** Worst first, then newest first inside a tone. */
export function sortAttention(items: AttentionItem[]): AttentionItem[] {
  return [...items].sort(
    (a, b) => compareTone(a.tone, b.tone) || (b.asOf ?? "").localeCompare(a.asOf ?? ""),
  );
}

/** Counts by tone, for the composition bar over a health column. */
export function tallyByTone<T>(
  rows: T[],
  toneOf: (row: T) => StatusTone,
): { tone: StatusTone; count: number }[] {
  const counts = new Map<StatusTone, number>();
  for (const row of rows) {
    const tone = toneOf(row);
    counts.set(tone, (counts.get(tone) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([tone, count]) => ({ tone, count }))
    .sort((a, b) => compareTone(a.tone, b.tone));
}
