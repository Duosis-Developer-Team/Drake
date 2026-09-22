/**
 * The Operational verdict — one sentence and a handful of contextual facts,
 * replacing the five-equal-KPI-card triage strip (brief §9.3 forbids it
 * outright).
 *
 * Every number here is derived from `AttentionItem`s the page already built
 * from real endpoint responses, or from the `sources` list's own
 * loading/data state. Nothing is computed that the API did not report, and
 * nothing here invents a rate, a percentage or a cause — nothing about *why*
 * paths are failing, only how many and where.
 */
import type { Resource } from "@/lib/useResource";
import type { AttentionItem } from "@/lib/overview";
import { tallyByTone } from "@/lib/overview";

export interface OperationalVerdict {
  /** English rendering of `headlineKind`; `VerdictPanel` translates the kind. */
  headline: string;
  /** Which sentence the headline is: the counts it needs are on the model. */
  headlineKind: "critical" | "warning" | "clear";
  criticalCount: number;
  warningCount: number;
  affectedScope: { projects: number; services: number; clusters: number };
  sourcesAnswered: number;
  sourcesTotal: number;
  oldestSuspectEvidence: { label: string; asOf: string | null } | null;
}

/** The project key is the first segment of an incident/service context
 *  ("alpha / production" -> "alpha"); a context with no "/" is not a
 *  project-scoped fact and contributes nothing rather than a guess. */
function projectKeyOf(item: AttentionItem): string | null {
  const [project] = item.context.split(" / ");
  return project && project !== item.context ? project : null;
}

export function buildVerdict(
  attention: AttentionItem[],
  sources: readonly { label: string; resource: Resource<unknown> }[],
): OperationalVerdict {
  const flagged = attention.filter((item) => item.tone === "critical" || item.tone === "warning");
  const tally = tallyByTone(flagged, (item) => item.tone);
  const criticalCount = tally.find((entry) => entry.tone === "critical")?.count ?? 0;
  const warningCount = tally.find((entry) => entry.tone === "warning")?.count ?? 0;

  const projects = new Set<string>();
  const services = new Set<string>();
  const clusters = new Set<string>();
  for (const item of flagged) {
    if (item.origin === "cluster") {
      clusters.add(item.subject);
    } else if (item.origin === "service") {
      services.add(item.subject);
      const project = projectKeyOf(item);
      if (project) projects.add(project);
    } else if (item.origin === "incident") {
      const project = projectKeyOf(item);
      if (project) projects.add(project);
    }
    // Alerts carry no per-entity scope in the summary the API returns
    // (`AlertSummary` is aggregate counts, not a list) — they contribute to
    // the counts above but not to affected-scope, rather than guess a scope.
  }

  const sourcesAnswered = sources.filter(({ resource }) => resource.data !== null).length;

  // Every tone `attention` can carry (critical/warning/stale/unknown) is
  // already "needs a look" — a stale or unknown reading can be the oldest,
  // most suspect evidence even when nothing has crossed into critical yet.
  let oldest: AttentionItem | null = null;
  for (const item of attention) {
    if (!item.asOf) continue;
    if (!oldest || !oldest.asOf || item.asOf < oldest.asOf) oldest = item;
  }

  const headlineKind = criticalCount > 0 ? "critical" : warningCount > 0 ? "warning" : "clear";
  const headline =
    headlineKind === "critical"
      ? `${criticalCount} critical path${criticalCount === 1 ? "" : "s"} need${criticalCount === 1 ? "s" : ""} attention`
      : headlineKind === "warning"
        ? `${warningCount} warning${warningCount === 1 ? "" : "s"} need attention`
        : `Nothing is currently flagged, ${sourcesAnswered} of ${sources.length} sources answered`;

  return {
    headline,
    headlineKind,
    criticalCount,
    warningCount,
    affectedScope: { projects: projects.size, services: services.size, clusters: clusters.size },
    sourcesAnswered,
    sourcesTotal: sources.length,
    oldestSuspectEvidence: oldest ? { label: oldest.subject, asOf: oldest.asOf ?? null } : null,
  };
}
