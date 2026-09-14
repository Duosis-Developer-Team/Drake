"use client";

/**
 * Projects.
 *
 * Composition: a KPI strip (how many projects, how they spread across
 * environments and services, how much of the portfolio has health evidence),
 * a pill toolbar, then the project list beside the portfolio risk map.
 *
 * The list is still a table underneath — rows and cells in the accessibility
 * tree — because the question is a comparison across rows, criticality
 * against criticality and size against size. It is drawn as rich rows so the
 * comparison reads at a glance rather than as a spreadsheet.
 *
 * Filters live in the URL so a filtered view is shareable and the back button
 * works, and they apply as you change them.
 *
 * Criticality and health are two different axes, never merged into one
 * score: the Criticality cell is a recorded judgement, the Health cell is what
 * service-health evidence actually observed, and the risk map plots both
 * together. Neither collection auto-loads past the backend's page size —
 * every extra page is a `Load more` a person asked for, and the summary copy
 * says so plainly while either collection is still partial.
 */

import {
  Boxes,
  ChevronRight,
  FolderGit2,
  FolderKanban,
  Layers,
  Radar,
} from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";

import { toneCounts } from "@/components/catalog/EnvironmentCards";
import {
  IconBubble,
  MiniBars,
  StatTile,
  TileState,
  ToneBar,
} from "@/components/catalog/visuals";
import { ProjectRiskMap } from "@/components/data-viz/ProjectRiskMap";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { StatusBadge } from "@/components/ui/StatusBadge";
import {
  Button,
  SearchInput,
  SegmentedControl,
  Select,
} from "@/components/ui/controls";
import { RelativeTime } from "@/components/ui/identifiers";
import {
  DeniedState,
  ErrorState,
  LoadingSkeleton,
} from "@/components/ui/states";
import type { Project } from "@/lib/catalog";
import { humanize, type StatusTone } from "@/lib/design/status";
import {
  serviceHealthListPath,
  type ServiceHealthPage,
  type ServiceHealthRow,
} from "@/lib/serviceHealth";
import {
  useProgressiveCollection,
  type PageSlice,
} from "@/lib/useProgressiveCollection";
import {
  buildPortfolioRisk,
  type ProjectRiskItem,
} from "@/lib/view-models/portfolio-risk";

/** Criticality is an ordered business judgement, not a health state. */
const CRITICALITY_TONE: Record<string, StatusTone> = {
  critical: "critical",
  high: "warning",
  medium: "info",
  low: "neutral",
};

const LIFECYCLE_OPTIONS = [
  { value: "active", label: "Active" },
  { value: "archived", label: "Archived" },
  { value: "all", label: "All" },
];

const CRITICALITY_OPTIONS = [
  { value: "critical", label: "Critical" },
  { value: "high", label: "High" },
  { value: "medium", label: "Medium" },
  { value: "low", label: "Low" },
];

const PAGE_SIZE = 100;

/** One grid for the header row and every project row, so columns line up. */
const ROW_GRID =
  "lg:grid lg:grid-cols-[minmax(0,1fr)_11rem_3.5rem_4rem_1rem] lg:items-center lg:gap-4";

interface ProjectsPageResponse {
  projects: Project[];
  next_cursor: string | null;
}

function projectsPath(
  filters: { lifecycle: string; search: string; criticality: string },
  cursor?: string,
): string {
  const params = new URLSearchParams({
    lifecycle: filters.lifecycle,
    limit: String(PAGE_SIZE),
  });
  if (filters.search.length >= 2) params.set("search", filters.search);
  if (filters.criticality) params.set("criticality", filters.criticality);
  if (cursor) params.set("cursor", cursor);
  return `/v1/projects?${params.toString()}`;
}

function serviceHealthSlice(
  page: ServiceHealthPage,
  loadedCount: number,
): PageSlice<ServiceHealthRow> {
  const loaded = loadedCount + page.items.length;
  return {
    items: page.items,
    nextPath:
      loaded < page.total
        ? serviceHealthListPath({ limit: PAGE_SIZE, offset: loaded })
        : null,
    total: page.total,
  };
}

/** `risk` matches either a shared status tone or one of the two evidence
 * states the tone vocabulary alone cannot distinguish (`unassessed` and
 * `incomplete` both display as the `unknown` tone). */
function matchesRisk(item: ProjectRiskItem, risk: string): boolean {
  if (risk === "unassessed" || risk === "incomplete")
    return item.evidence === risk;
  return item.tone === risk;
}

function ProjectRow({
  project,
  risk,
  onOpen,
}: {
  project: Project;
  risk: ProjectRiskItem | undefined;
  onOpen: () => void;
}) {
  return (
    <div
      role="row"
      onClick={(event) => {
        // The name is the real link; the rest of the row is a convenience
        // that must not swallow a click on that link (or a text selection).
        if ((event.target as HTMLElement).closest("a")) return;
        if (window.getSelection()?.toString()) return;
        onOpen();
      }}
      className={`group relative flex cursor-pointer flex-wrap items-center gap-x-4 gap-y-3 px-7 py-4 transition-colors hover:bg-surface-hover ${ROW_GRID}`}
    >
      <div
        role="cell"
        className="flex min-w-0 basis-full items-center gap-4 lg:basis-auto"
      >
        <IconBubble icon={FolderKanban} tone={risk?.tone} />
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-2">
            <Link
              href={`/projects/${project.id}`}
              className="truncate rounded text-body font-semibold text-ink hover:text-brand"
            >
              {project.display_name}
            </Link>
            <StatusBadge
              status={project.lifecycle === "active" ? "success" : "neutral"}
              label={humanize(project.lifecycle)}
              size="compact"
            />
          </div>
          <div className="mt-0.5 flex min-w-0 items-center gap-2 text-micro text-ink-muted">
            <span className="font-mono">{project.project_key}</span>
            <span aria-hidden>·</span>
            <FolderGit2 aria-hidden className="h-3 w-3 shrink-0" />
            <span className="truncate font-mono">
              {project.repository.owner}/{project.repository.name}
              <span className="ml-1.5">
                #{project.repository.default_branch}
              </span>
            </span>
            <span aria-hidden className="hidden 2xl:inline">
              ·
            </span>
            <span className="hidden shrink-0 2xl:inline">
              <RelativeTime value={project.source.accepted_at} />
            </span>
          </div>
        </div>
      </div>
      <div
        role="cell"
        className="flex min-w-0 flex-wrap items-start gap-1.5 lg:flex-col"
      >
        <StatusBadge
          status={CRITICALITY_TONE[project.criticality] ?? "neutral"}
          label={`${humanize(project.criticality)} criticality`}
          size="compact"
        />
        {risk ? (
          <StatusBadge
            status={risk.tone}
            label={`${risk.healthLabel} health`}
            size="compact"
          />
        ) : (
          <span className="text-micro text-ink-muted">—</span>
        )}
      </div>
      <div role="cell" className="flex items-baseline gap-1.5 lg:justify-end">
        <span
          data-tabular
          className="text-[1.125rem] leading-none font-semibold text-ink"
        >
          {project.counts.environments}
        </span>
        <span className="text-micro text-ink-muted lg:hidden">
          environments
        </span>
      </div>
      <div role="cell" className="flex items-baseline gap-1.5 lg:justify-end">
        <span
          data-tabular
          className="text-[1.125rem] leading-none font-semibold text-ink"
        >
          {project.counts.services}
        </span>
        <span className="text-micro text-ink-muted lg:hidden">services</span>
      </div>
      <div role="cell" aria-hidden className="hidden text-ink-muted lg:block">
        <ChevronRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
      </div>
    </div>
  );
}

function ProjectsInner() {
  const router = useRouter();
  const params = useSearchParams();
  const search = params.get("search") ?? "";
  const lifecycle = params.get("lifecycle") ?? "active";
  const criticality = params.get("criticality") ?? "";
  const risk = params.get("risk");
  const [draft, setDraft] = useState(search);

  // Debounced: the search parameter is what drives the request, and typing
  // should not fire one per keystroke.
  useEffect(() => {
    if (draft === search) return;
    const timer = setTimeout(() => {
      updateParams({ search: draft.length >= 2 ? draft : "" });
    }, 300);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft]);

  const filters = { lifecycle, search, criticality };
  const projectsCollection = useProgressiveCollection<
    ProjectsPageResponse,
    Project
  >({
    firstPath: projectsPath(filters),
    pageToSlice: (page): PageSlice<Project> => ({
      items: page.projects,
      nextPath: page.next_cursor
        ? projectsPath(filters, page.next_cursor)
        : null,
    }),
  });

  const servicesCollection = useProgressiveCollection<
    ServiceHealthPage,
    ServiceHealthRow
  >({
    firstPath: serviceHealthListPath({ limit: PAGE_SIZE }),
    pageToSlice: serviceHealthSlice,
  });

  function updateParams(updates: Record<string, string>) {
    const next = new URLSearchParams(params.toString());
    for (const [key, value] of Object.entries(updates)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    router.replace(`/projects?${next.toString()}`, { scroll: false });
  }

  const projects = projectsCollection.items;
  const filtered =
    search.length > 0 ||
    criticality !== "" ||
    lifecycle !== "active" ||
    Boolean(risk);

  const model = useMemo(
    () =>
      buildPortfolioRisk(projects, servicesCollection.items, {
        projects: projectsCollection.complete,
        services: servicesCollection.complete,
        servicesTotal: servicesCollection.total,
      }),
    [
      projects,
      projectsCollection.complete,
      servicesCollection.items,
      servicesCollection.complete,
      servicesCollection.total,
    ],
  );
  const riskByProjectId = useMemo(
    () => new Map(model.items.map((item) => [item.projectId, item])),
    [model.items],
  );

  const visibleProjects = risk
    ? projects.filter((project) => {
        const item = riskByProjectId.get(project.id);
        return item ? matchesRisk(item, risk) : false;
      })
    : projects;

  const environmentTotal = projects.reduce(
    (sum, project) => sum + project.counts.environments,
    0,
  );
  const serviceTotal = projects.reduce(
    (sum, project) => sum + project.counts.services,
    0,
  );
  const assessed = model.items.filter(
    (item) => item.evidence === "complete",
  ).length;
  const firstLoad = projectsCollection.loading && projects.length === 0;

  const summary = firstLoad
    ? undefined
    : risk && !model.complete
      ? `Filtered within ${projects.length} loaded project${projects.length === 1 ? "" : "s"}`
      : `${visibleProjects.length} project${visibleProjects.length === 1 ? "" : "s"}${
          filtered ? " matching" : ""
        }`;

  const showRisk = model.items.length > 0;

  return (
    <PageFrame>
      <PageHeader
        title="Projects"
        description="Your authorized project catalog — criticality, footprint and observed health."
        meta={
          projects.length > 0 ? (
            <>
              <span>{environmentTotal} environments</span>
              <span>{serviceTotal} services</span>
              {!projectsCollection.complete ? (
                <StatusBadge
                  status="unknown"
                  label="Partial view"
                  size="compact"
                />
              ) : null}
            </>
          ) : undefined
        }
      />

      <div className="flex flex-col gap-8">
        {projects.length > 0 ? (
          <div className="page-grid motion-safe:animate-[fade-in_320ms_var(--ease-entrance)_backwards]">
            <StatTile
              icon={FolderKanban}
              label="Projects"
              value={projects.length}
              suffix="in scope"
            >
              <ToneBar
                label="Projects by criticality"
                counts={CRITICALITY_OPTIONS.map((option) => ({
                  tone: CRITICALITY_TONE[option.value],
                  label: option.label,
                  count: projects.filter(
                    (project) => project.criticality === option.value,
                  ).length,
                }))}
              />
            </StatTile>
            <StatTile
              icon={Layers}
              label="Environments"
              value={environmentTotal}
              suffix="across projects"
            >
              <MiniBars
                label="Environments per project"
                items={projects.map((project) => ({
                  key: project.id,
                  label: project.display_name,
                  value: project.counts.environments,
                }))}
              />
            </StatTile>
            <StatTile
              icon={Boxes}
              label="Services"
              value={serviceTotal}
              suffix="in catalog"
            >
              <MiniBars
                label="Services per project"
                items={projects.map((project) => ({
                  key: project.id,
                  label: project.display_name,
                  value: project.counts.services,
                }))}
              />
            </StatTile>
            <StatTile
              icon={Radar}
              label="Evidence"
              value={assessed}
              suffix={`of ${model.items.length} assessed`}
            >
              <ToneBar
                label="Projects by observed health"
                counts={toneCounts(model.items, (item) => item.tone)}
              />
            </StatTile>
          </div>
        ) : null}

        <div className={showRisk ? "page-split" : "flex flex-col"}>
          <div className="page-main">
            {/* Card-less pill toolbar */}
            <div
              className="flex flex-wrap items-center gap-3"
              data-testid="filter-bar"
            >
              <SearchInput
                label="Search projects by key or name"
                placeholder="Key or name…"
                value={draft}
                onChange={setDraft}
                className="w-full sm:w-56 [&_input]:h-10 [&_input]:rounded-full [&_input]:pl-9"
              />
              <SegmentedControl
                label="Lifecycle"
                value={lifecycle}
                options={LIFECYCLE_OPTIONS}
                onChange={(value) => updateParams({ lifecycle: value })}
              />
              <div className="[&_select]:h-10 [&_select]:rounded-full [&_select]:pl-4">
                <Select
                  label="Criticality"
                  hideLabel
                  value={criticality}
                  placeholder="Any criticality"
                  options={CRITICALITY_OPTIONS}
                  onChange={(value) => updateParams({ criticality: value })}
                />
              </div>
            </div>

            <Panel
              flush
              className="motion-safe:animate-[fade-in_420ms_var(--ease-entrance)_backwards] [animation-delay:70ms]"
            >
              {summary ? (
                <div className="flex items-center justify-between gap-3 border-b border-border px-7 py-4">
                  <span
                    className="text-body font-semibold text-ink"
                    data-tabular
                  >
                    {summary}
                  </span>
                  {filtered ? (
                    <Button
                      variant="ghost"
                      size="compact"
                      onClick={() => {
                        setDraft("");
                        router.replace("/projects", { scroll: false });
                      }}
                    >
                      Clear filters
                    </Button>
                  ) : null}
                </div>
              ) : null}
              {firstLoad ? (
                <div className="px-7 py-6">
                  <LoadingSkeleton
                    variant="table"
                    rows={4}
                    label="Loading projects"
                  />
                </div>
              ) : projectsCollection.denied ? (
                <div className="px-7 py-6">
                  <DeniedState />
                </div>
              ) : projects.length === 0 && projectsCollection.error ? (
                <div className="px-7 py-6">
                  <ErrorState
                    description={projectsCollection.error ?? undefined}
                    correlationId={projectsCollection.correlationId}
                    onRetry={projectsCollection.reload}
                  />
                </div>
              ) : (
                <>
                  <div data-testid="project-list">
                    {visibleProjects.length === 0 ? (
                      <div className="px-7 py-10">
                        <TileState
                          icon={FolderKanban}
                          testId="state-empty"
                          title={
                            filtered
                              ? "No projects match these filters"
                              : "No projects in your scope"
                          }
                          description={
                            filtered
                              ? "Clear the filters to see everything you are authorized for."
                              : "Projects you are authorized to see appear here once onboarded."
                          }
                        />
                      </div>
                    ) : (
                      <div
                        role="table"
                        aria-label="Projects in your authorized scope"
                      >
                        <div
                          role="rowgroup"
                          className="hidden border-b border-border lg:block"
                        >
                          <div
                            role="row"
                            className={`px-7 py-3 text-micro font-medium tracking-[0.08em] text-ink-muted uppercase ${ROW_GRID}`}
                          >
                            <span role="columnheader" className="pl-14">
                              Project
                            </span>
                            <span role="columnheader">
                              Criticality · health
                            </span>
                            <span role="columnheader" className="text-right">
                              Envs
                            </span>
                            <span role="columnheader" className="text-right">
                              Services
                            </span>
                            <span role="columnheader">
                              <span className="sr-only">Open</span>
                            </span>
                          </div>
                        </div>
                        <div role="rowgroup" className="divide-y divide-border">
                          {visibleProjects.map((project) => (
                            <ProjectRow
                              key={project.id}
                              project={project}
                              risk={riskByProjectId.get(project.id)}
                              onOpen={() =>
                                router.push(`/projects/${project.id}`)
                              }
                            />
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                  {!projectsCollection.complete ||
                  !servicesCollection.complete ? (
                    <div className="flex flex-wrap gap-2 border-t border-border px-7 py-4">
                      {!projectsCollection.complete ? (
                        <Button
                          onClick={projectsCollection.loadMore}
                          disabled={projectsCollection.loadingMore}
                          size="compact"
                        >
                          {projectsCollection.loadingMore
                            ? "Loading…"
                            : "Load more projects"}
                        </Button>
                      ) : null}
                      {!servicesCollection.complete ? (
                        <Button
                          onClick={servicesCollection.loadMore}
                          disabled={servicesCollection.loadingMore}
                          size="compact"
                          variant="secondary"
                        >
                          {servicesCollection.loadingMore
                            ? "Loading…"
                            : "Load more evidence"}
                        </Button>
                      ) : null}
                    </div>
                  ) : null}
                </>
              )}
            </Panel>
          </div>

          {showRisk ? (
            <div className="page-aside">
              <Panel
                data-testid="projects-risk"
                className="motion-safe:animate-[fade-in_360ms_var(--ease-entrance)_backwards] [animation-delay:120ms]"
              >
                <PanelHeader
                  title="Portfolio risk"
                  description="Recorded criticality against observed health."
                  level={2}
                />
                <ProjectRiskMap
                  model={model}
                  activeTone={risk}
                  onToneChange={(tone) => updateParams({ risk: tone ?? "" })}
                />
              </Panel>
            </div>
          ) : null}
        </div>
      </div>
    </PageFrame>
  );
}

export default function ProjectsPage() {
  return (
    <Suspense
      fallback={
        <PageFrame>
          <LoadingSkeleton variant="table" rows={5} label="Loading projects" />
        </PageFrame>
      }
    >
      <ProjectsInner />
    </Suspense>
  );
}
