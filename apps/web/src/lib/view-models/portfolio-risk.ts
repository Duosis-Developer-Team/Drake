/**
 * The Projects portfolio's risk map: criticality (a recorded judgment) ×
 * observed health (what service-health evidence actually says) — kept as
 * two separate axes on purpose, because criticality is never a stand-in for
 * health and a project's importance does not change when it goes quiet.
 *
 * A project with zero matched services is `unassessed`, never `healthy`:
 * Drake has not observed anything to call healthy. And when the underlying
 * collection (projects or service-health rows) is still paginated, every
 * item is `incomplete` rather than `unassessed` — there may be services out
 * there Drake has not loaded yet, so the honest answer is "don't know yet",
 * not "none exist".
 */
import type { Project } from "@/lib/catalog";
import { compareTone, toneSpec, toneForHealth, type StatusTone } from "@/lib/design/status";
import type { ServiceHealthRow } from "@/lib/serviceHealth";

export type PortfolioEvidence = "complete" | "incomplete" | "unassessed";

export interface ProjectRiskItem {
  projectId: string;
  projectKey: string;
  displayName: string;
  criticality: Project["criticality"];
  tone: StatusTone;
  healthLabel: string;
  servicesObserved: number;
  evidence: PortfolioEvidence;
  href: string;
}

export interface PortfolioRiskModel {
  items: ProjectRiskItem[];
  complete: boolean;
  projectsLoaded: number;
  servicesLoaded: number;
  servicesTotal: number | null;
}

const CRITICALITY_ORDER: Record<Project["criticality"], number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
};

function worstToneOf(services: ServiceHealthRow[]): StatusTone {
  let worst: StatusTone = toneForHealth(services[0]?.health.status);
  for (const row of services.slice(1)) {
    const tone = toneForHealth(row.health.status);
    if (compareTone(tone, worst) < 0) worst = tone;
  }
  return worst;
}

export function buildPortfolioRisk(
  projects: Project[],
  services: ServiceHealthRow[],
  completeness: { projects: boolean; services: boolean; servicesTotal: number | null },
): PortfolioRiskModel {
  const items = projects.map((project) => {
    const owned = services.filter((row) => row.project_id === project.id);
    const observed = owned.length;

    let evidence: PortfolioEvidence;
    if (observed > 0) evidence = completeness.services ? "complete" : "incomplete";
    else evidence = completeness.services ? "unassessed" : "incomplete";

    let tone: StatusTone;
    let healthLabel: string;
    if (evidence === "unassessed") {
      tone = "unknown";
      healthLabel = "Unassessed";
    } else if (evidence === "incomplete") {
      const observedWorst = observed > 0 ? worstToneOf(owned) : "unknown";
      tone = observedWorst === "success" ? "unknown" : observedWorst;
      healthLabel = "Evidence incomplete";
    } else {
      tone = worstToneOf(owned);
      healthLabel = toneSpec(tone).label;
    }

    return {
      projectId: project.id,
      projectKey: project.project_key,
      displayName: project.display_name,
      criticality: project.criticality,
      tone,
      healthLabel,
      servicesObserved: observed,
      evidence,
      href: `/projects/${project.id}`,
    };
  });

  items.sort(
    (a, b) =>
      CRITICALITY_ORDER[a.criticality] - CRITICALITY_ORDER[b.criticality] ||
      compareTone(a.tone, b.tone),
  );

  return {
    items,
    complete: completeness.projects && completeness.services,
    projectsLoaded: projects.length,
    servicesLoaded: services.length,
    servicesTotal: completeness.servicesTotal,
  };
}
