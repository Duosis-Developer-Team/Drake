/**
 * Project topology: one lane per environment, one row per environment
 * service — worst-first, and never hiding a healthy/unknown/stale/unbound
 * service to make the lane shorter. Joins happen only on the opaque
 * `environment_id`/`environment_service_id` the API assigns; never on a
 * project, environment or service *name*, since names are not guaranteed
 * unique across scopes and a name-join would silently merge the wrong rows.
 *
 * An external-runtime environment has no Kubernetes workloads to bind a
 * service-health row to, so its lane reads `not-applicable` — a different
 * answer from `unknown`, which means Drake could have measured something
 * and didn't.
 */
import type { Environment } from "@/lib/catalog";
import { compareTone, humanize, toneForHealth, type StatusTone } from "@/lib/design/status";
import type { ServiceHealthRow } from "@/lib/serviceHealth";

export interface ServiceLaneModel {
  id: string;
  serviceKey: string;
  displayName: string;
  component: string | null;
  tone: StatusTone;
  statusLabel: string;
  freshnessAgeSeconds: number | null;
  partial: boolean;
  binding: ServiceHealthRow["binding"];
  measurements: {
    ready: number | null;
    desired: number | null;
    restarts: number | null;
    cpu: number | null;
    memory: number | null;
  };
  href: string;
}

export interface EnvironmentLaneModel {
  id: string;
  key: string;
  runtime: Environment["runtime"];
  placement: string;
  tone: StatusTone;
  evidence: "complete" | "incomplete" | "unassessed" | "not-applicable";
  services: ServiceLaneModel[];
  href: string;
}

export function buildServiceLane(row: ServiceHealthRow): ServiceLaneModel {
  return {
    id: row.environment_service_id,
    serviceKey: row.service_key,
    displayName: row.display_name || row.service_key,
    component: row.component,
    tone: toneForHealth(row.health.status),
    statusLabel: humanize(row.health.status),
    freshnessAgeSeconds: row.health.freshness_age_seconds,
    partial: row.health.partial,
    binding: row.binding,
    measurements: {
      ready: row.health.availability?.ready_replicas ?? null,
      desired: row.health.availability?.desired_replicas ?? null,
      restarts: row.health.stability?.restarts_in_window ?? null,
      cpu: row.health.resources?.cpu_utilization ?? null,
      memory: row.health.resources?.memory_utilization ?? null,
    },
    href: `/projects/${row.project_id}/environments/${row.environment_id}/services/${row.environment_service_id}`,
  };
}

function placementFor(environment: Environment): string {
  if (environment.runtime === "external") return "Not applicable";
  if (!environment.cluster) return "Unknown placement";
  return environment.namespace
    ? `${environment.cluster.ref}/${environment.namespace}`
    : environment.cluster.ref;
}

export function buildProjectTopology(
  projectId: string,
  environments: Environment[],
  rows: ServiceHealthRow[],
  servicesComplete: boolean,
): EnvironmentLaneModel[] {
  return environments.map((environment) => {
    const lanes = rows
      .filter((row) => row.environment_id === environment.id)
      .map(buildServiceLane)
      .sort((a, b) => compareTone(a.tone, b.tone));

    const href = `/projects/${projectId}/environments/${environment.id}`;

    if (environment.runtime === "external") {
      return {
        id: environment.id,
        key: environment.environment_key,
        runtime: environment.runtime,
        placement: "Not applicable",
        tone: "not-applicable",
        evidence: "not-applicable",
        services: lanes,
        href,
      };
    }

    const evidence: EnvironmentLaneModel["evidence"] =
      lanes.length > 0
        ? servicesComplete
          ? "complete"
          : "incomplete"
        : servicesComplete
          ? "unassessed"
          : "incomplete";

    let tone: StatusTone;
    if (evidence === "unassessed") {
      tone = "unknown";
    } else if (evidence === "incomplete") {
      const worst = lanes.length > 0 ? lanes[0].tone : "unknown";
      tone = worst === "success" ? "unknown" : worst;
    } else {
      tone = lanes[0].tone;
    }

    return {
      id: environment.id,
      key: environment.environment_key,
      runtime: environment.runtime,
      placement: placementFor(environment),
      tone,
      evidence,
      services: lanes,
      href,
    };
  });
}
