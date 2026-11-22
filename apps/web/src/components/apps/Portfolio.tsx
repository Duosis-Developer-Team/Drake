"use client";

/**
 * Projects, as a portfolio: every project the caller may see, as a card.
 *
 * The question this page answers is "which project?", so a project is the
 * thing you pick. On top, the portfolio in four figures (projects by
 * criticality, environments and how many are healthy, services and how many
 * are bound, a day of traffic). Below, each project as a card: who owns it,
 * where its code lives, what is open against it (incidents, objectives,
 * today's deploys) and each environment with its services' health and its
 * traffic. The whole card opens the project.
 *
 * Reads: the project and service lists the page already has, plus three
 * small reads it can afford once per page — open incidents, objectives and
 * the last day's deploys. A read that did not answer shows a dash, never 0.
 */

import { ArrowRight, GitBranch } from "lucide-react";
import { useMemo } from "react";

import { ProjectLogo } from "@/components/ui/ProjectLogo";
import { deploymentListPath, type DeploymentPage } from "@/lib/deployments";
import type { Project } from "@/lib/catalog";
import { useFormat, useT } from "@/lib/i18n";
import type { IncidentSummary } from "@/lib/incidents";
import type { Slo } from "@/lib/alerting";
import { answered } from "@/lib/dashboards";
import { groupApps, pulseOf, usePlatformOverview, type PlatformApp } from "@/lib/platform";
import type { ServiceHealthRow, ServiceHealthStatus } from "@/lib/serviceHealth";
import { STATUS_LABELS, toneForServiceStatus } from "@/components/service-health/primitives";
import { toneSpec } from "@/lib/design/status";
import { useResource } from "@/lib/useResource";

import { OpenProjectLink, ServicesLauncher } from "./ServicesLauncher";

import { FillGrid, HealthBar, kindColour, Sparkline } from "./AppLauncher";

const CRITICALITY: Project["criticality"][] = ["critical", "high", "medium", "low"];
const CRITICALITY_COLOUR: Record<Project["criticality"], string> = {
  critical: "var(--status-critical)",
  high: "var(--status-warning)",
  medium: "var(--brand-accent)",
  low: "var(--text-muted)",
};

function Figure({ label, value, note, children }: { label: string; value: string; note: string; children?: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1 px-5 py-4">
      <span className="text-caption text-ink-muted">{label}</span>
      <span data-tabular className="font-[family-name:var(--font-display)] text-[2rem] leading-none font-bold tracking-[-0.03em] text-ink">
        {value}
      </span>
      <span className="truncate text-micro text-ink-muted">{note}</span>
      {children}
    </div>
  );
}

function Count({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="min-w-0 rounded-xl bg-surface-2 px-3 py-2">
      <span className="block truncate text-[11px] text-ink-muted">{label}</span>
      <span data-tabular className={`font-mono text-[1rem] font-semibold ${tone ?? "text-ink"}`}>
        {value}
      </span>
    </div>
  );
}

function ProjectCard({
  project,
  app,
  overview,
  incidents,
  objectives,
  deploys,
  index,
  selected,
  onSelect,
}: {
  selected: boolean;
  onSelect: () => void;
  project: Project;
  app: PlatformApp | null;
  overview: Parameters<typeof pulseOf>[0];
  incidents: number | null;
  objectives: { total: number; meeting: number } | null;
  deploys: number | null;
  index: number;
}) {
  const t = useT("boards");
  const tc = useT("catalog");
  const ts = useT("serviceHealth");
  const fmt = useFormat();
  const bound = app ? app.envs.reduce((a, e) => a + e.bindingIds.length, 0) : 0;
  const total = app ? app.services : project.counts.services;
  return (
    <li className="min-w-0">
      <button
        type="button"
        aria-pressed={selected}
        onClick={onSelect}
        data-testid={`portfolio-${project.project_key}`}
        style={{ animationDelay: `${index * 60}ms` }}
        className={`group relative flex h-full w-full animate-[rise-in_.45s_cubic-bezier(.2,.8,.2,1)_both] flex-col overflow-hidden rounded-2xl border bg-surface text-left outline-none transition-[transform,border-color,box-shadow] duration-200 hover:-translate-y-0.5 hover:shadow-[0_18px_40px_-24px_rgb(0_0_0/0.45)] focus-visible:ring-2 focus-visible:ring-focus ${
          selected ? "border-brand-accent/70 ring-1 ring-brand-accent/40" : "border-border hover:border-border-strong"
        }`}
      >
        <span aria-hidden className="absolute inset-x-0 top-0 h-[3px]" style={{ background: CRITICALITY_COLOUR[project.criticality] }} />
        <div className="flex items-start gap-4 px-5 pt-5">
          <span
            className="grid h-16 w-16 shrink-0 place-items-center rounded-[1.1rem] border border-border bg-gradient-to-b from-surface to-surface-2"
            style={{ boxShadow: "inset 0 1px 0 rgb(255 255 255 / 0.07), 0 10px 22px -14px rgb(0 0 0 / 0.45)" }}
          >
            <ProjectLogo projectId={project.id} name={project.display_name} size="lg" variant="mark" />
          </span>
          <span className="min-w-0 flex-1">
            <span className="flex flex-wrap items-center gap-2">
              <span className="truncate font-[family-name:var(--font-display)] text-[1.25rem] font-bold tracking-[-0.02em] text-ink group-hover:text-brand-accent">
                {project.display_name}
              </span>
              <span
                className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-semibold"
                style={{ color: CRITICALITY_COLOUR[project.criticality], borderColor: `color-mix(in srgb, ${CRITICALITY_COLOUR[project.criticality]} 40%, transparent)` }}
              >
                {t("portfolio.criticalityLabel", { level: tc(`criticality.${project.criticality}`) })}
              </span>
            </span>
            <span className="mt-1 flex min-w-0 items-center gap-1.5 font-mono text-[11px] text-ink-muted">
              <GitBranch aria-hidden className="h-3 w-3 shrink-0" />
              <span className="truncate">
                {project.repository.owner}/{project.repository.name}@{project.repository.default_branch}
              </span>
            </span>
            <span className="mt-2 flex flex-wrap gap-1">
              {project.owners?.length ? (
                project.owners.map((o) => (
                  <span key={`${o.team}-${o.role}`} className="rounded-full bg-surface-2 px-2 py-0.5 text-[11px] text-ink-secondary">
                    {o.team}
                  </span>
                ))
              ) : (
                <span className="text-[11px] text-ink-muted">{t("portfolio.noOwner")}</span>
              )}
            </span>
          </span>
        </div>

        <div className="mt-4 grid grid-cols-3 gap-2 px-5">
          <Count
            label={t("portfolio.openIncidents")}
            value={incidents === null ? "—" : fmt.number(incidents)}
            tone={incidents ? "text-critical" : undefined}
          />
          <Count
            label={t("portfolio.objectives")}
            value={objectives === null ? "—" : objectives.total ? `${objectives.meeting}/${objectives.total}` : "0"}
            tone={objectives && objectives.total && objectives.meeting < objectives.total ? "text-warning" : undefined}
          />
          <Count label={t("portfolio.deploys")} value={deploys === null ? "—" : fmt.number(deploys)} />
        </div>

        <div className="mt-4 flex-1 border-t border-border">
          {app && app.envs.length ? (
            <ul>
              {app.envs.map((env) => {
                const pulse = pulseOf(overview, env.bindingIds);
                return (
                  <li key={env.environmentId} className="grid grid-cols-[5rem_minmax(0,1fr)_auto] items-center gap-3 border-b border-border px-5 py-2.5 last:border-b-0">
                    <span className="flex min-w-0 flex-col gap-1.5">
                      <span className="flex items-center gap-2">
                        <span aria-hidden className="h-3 w-[3px] rounded-full" style={{ background: kindColour(env.key) }} />
                        <span className="truncate text-body font-semibold text-ink">{env.key}</span>
                      </span>
                      <HealthBar tally={env.tally} />
                    </span>
                    <span className="min-w-0">
                      {pulse && pulse.perBucket.length > 1 && pulse.requests ? (
                        <Sparkline values={pulse.perBucket} height={28} colour={env.key.startsWith("prod") ? "var(--gold)" : "var(--brand-accent)"} />
                      ) : (
                        <span className="block text-[11px] text-ink-muted">{t("portfolio.noTraffic")}</span>
                      )}
                    </span>
                    <span className="text-right">
                      <span data-tabular className="block font-mono text-caption font-semibold text-ink">
                        {pulse?.requests == null ? "—" : fmt.number(pulse.requests)}
                      </span>
                      {env.worst ? (
                        <span className={`text-[10.5px] ${toneSpec(toneForServiceStatus(env.worst)).text}`}>
                          {ts.dyn("status", env.worst, STATUS_LABELS[env.worst])}
                        </span>
                      ) : null}
                    </span>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="px-5 py-4 text-caption text-ink-muted">{t("portfolio.noEnvs")}</p>
          )}
        </div>

        <div className="flex items-center justify-between gap-3 border-t border-border px-5 py-3 text-micro">
          <span className="text-ink-muted">{t("portfolio.servicesCount", { bound, total })}</span>
          <span className="inline-flex items-center gap-1 font-semibold text-brand-accent">
            {t("portfolio.pickEnv")}
            <ArrowRight aria-hidden className={`h-3.5 w-3.5 transition-transform ${selected ? "rotate-90" : "group-hover:translate-x-0.5"}`} />
          </span>
        </div>
      </button>
    </li>
  );
}

export function Portfolio({
  projects,
  rows,
  project,
  environment,
  onProject,
  onEnvironment,
}: {
  projects: Project[];
  rows: ServiceHealthRow[];
  /** The chosen project and environment: URL state the page owns. */
  project: string;
  environment: string;
  onProject: (project: string) => void;
  onEnvironment: (environment: string) => void;
}) {
  const t = useT("boards");
  const tc = useT("catalog");
  const fmt = useFormat();
  const names = useMemo(() => new Map(projects.map((p) => [p.id, p.display_name])), [projects]);
  const apps = useMemo(() => groupApps(rows, names), [rows, names]);
  const { overview } = usePlatformOverview(rows);
  const traffic = answered(overview?.traffic);

  const incidents = useResource<{ items: IncidentSummary[] }>("/v1/incidents?state=open&limit=50");
  const slos = useResource<{ items: Slo[] }>("/v1/slo");
  const deploys = useResource<DeploymentPage>(deploymentListPath({ startedWithin: "24h" }));

  const perKey = <T,>(items: T[] | undefined, key: (item: T) => string) => {
    if (!items) return null;
    const out = new Map<string, T[]>();
    for (const item of items) out.set(key(item), [...(out.get(key(item)) ?? []), item]);
    return out;
  };
  const incidentsBy = perKey(incidents.data?.items, (i) => i.project_key);
  const slosBy = perKey(slos.data?.items, (s) => s.project_key);
  const deploysBy = perKey(deploys.data?.items, (d) => d.project_key ?? "");

  // The portfolio in four figures.
  const byCriticality = new Map(CRITICALITY.map((c) => [c, projects.filter((p) => p.criticality === c).length]));
  const envs = apps.flatMap((a) => a.envs);
  const healthyEnvs = envs.filter((e) => e.worst === "healthy").length;
  const bound = rows.filter((r) => r.binding).length;
  const dayTotal = traffic ? traffic.reduce((a, x) => a + x.buckets.reduce((b, y) => b + y.requests, 0), 0) : null;
  const daySeries = traffic && overview ? overview.buckets.map((_, i) => traffic.reduce((a, x) => a + (x.buckets[i]?.requests ?? 0), 0)) : [];
  const envTally = new Map<ServiceHealthStatus, number>();
  for (const e of envs) if (e.worst) envTally.set(e.worst, (envTally.get(e.worst) ?? 0) + 1);

  // One project: its environments are open from the start.
  const chosen = projects.some((p) => p.id === project) ? project : projects.length === 1 ? projects[0].id : "";

  const ordered = [...projects].sort(
    (a, b) => CRITICALITY.indexOf(a.criticality) - CRITICALITY.indexOf(b.criticality) || a.display_name.localeCompare(b.display_name),
  );

  return (
    <div className="flex flex-col gap-6" data-testid="portfolio">
      <section className="overflow-hidden rounded-panel border border-border bg-surface" aria-label={t("portfolio.projects")}>
        <div className="grid divide-y divide-border sm:grid-cols-2 sm:divide-y-0 xl:grid-cols-4 xl:divide-x">
          <Figure
            label={t("portfolio.projects")}
            value={fmt.number(projects.length)}
            note={t("portfolio.projectsNote", { active: projects.filter((p) => p.lifecycle === "active").length })}
          >
            <span className="mt-2 flex h-2 w-full gap-[2px] overflow-hidden rounded-full" role="img" aria-label={t("portfolio.criticality")}>
              {CRITICALITY.filter((c) => byCriticality.get(c)).map((c) => (
                <span key={c} className="h-full" style={{ flexGrow: byCriticality.get(c), background: CRITICALITY_COLOUR[c] }} />
              ))}
            </span>
            <span className="mt-1.5 flex flex-wrap gap-x-3 text-[11px] text-ink-secondary">
              {CRITICALITY.filter((c) => byCriticality.get(c)).map((c) => (
                <span key={c} className="inline-flex items-center gap-1">
                  <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: CRITICALITY_COLOUR[c] }} />
                  <span data-tabular>{byCriticality.get(c)}</span> {tc(`criticality.${c}`).toLocaleLowerCase()}
                </span>
              ))}
            </span>
          </Figure>
          <Figure label={t("portfolio.environments")} value={fmt.number(envs.length)} note={t("portfolio.environmentsNote", { healthy: healthyEnvs })}>
            <HealthBar tally={envTally} className="mt-2" />
          </Figure>
          <Figure label={t("portfolio.services")} value={fmt.number(rows.length)} note={t("portfolio.servicesNote", { bound })}>
            <span className="mt-2 flex h-2 w-full overflow-hidden rounded-full bg-surface-3">
              <span className="h-full rounded-full bg-brand-accent" style={{ width: `${rows.length ? (bound / rows.length) * 100 : 0}%` }} />
            </span>
          </Figure>
          <Figure label={t("portfolio.traffic")} value={dayTotal === null ? "—" : fmt.number(dayTotal)} note={t("portfolio.trafficNote")}>
            {daySeries.some((v) => v > 0) ? <Sparkline values={daySeries} height={30} className="mt-1" /> : null}
          </Figure>
        </div>
      </section>

      <FillGrid count={ordered.length} max={3}>
        {ordered.map((project, i) => (
          <ProjectCard
            key={project.id}
            project={project}
            index={i}
            app={apps.find((a) => a.projectId === project.id) ?? null}
            overview={overview}
            incidents={incidentsBy ? (incidentsBy.get(project.project_key)?.length ?? 0) : null}
            objectives={
              slosBy
                ? {
                    total: slosBy.get(project.project_key)?.length ?? 0,
                    meeting: (slosBy.get(project.project_key) ?? []).filter((s) => s.evaluation?.status === "healthy").length,
                  }
                : null
            }
            deploys={deploysBy ? (deploysBy.get(project.project_key)?.length ?? 0) : null}
            selected={chosen === project.id}
            onSelect={() => onProject(chosen === project.id ? "" : project.id)}
          />
        ))}
      </FillGrid>

      {/* The chosen project's environments, sized to fill the row, then the
          chosen environment's services — the launcher without its own grid. */}
      {chosen ? (
        <ServicesLauncher
          rows={rows}
          hideGrid
          project={chosen}
          environment={environment}
          onProject={onProject}
          onEnvironment={onEnvironment}
          shelfAction={(id) => <OpenProjectLink projectId={id} />}
          envHref={(projectId, environmentId) => `/projects/${projectId}/environments/${environmentId}`}
        />
      ) : null}
    </div>
  );
}
