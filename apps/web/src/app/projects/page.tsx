"use client";

/**
 * Projects.
 *
 * A scannable table, not a grid of cards. The question this screen answers is
 * "which of my projects should I look at", and that is a comparison across
 * rows — criticality against criticality, size against size. Cards make every
 * comparison a saccade.
 *
 * Filters live in the URL so a filtered view is shareable and the back button
 * works. They apply as you change them rather than behind an Apply button:
 * the previous version made you press Apply for the selects too, so a filter
 * you had chosen was not the filter you were looking at.
 *
 * Repository provenance is present on every row but deliberately quiet — it
 * is how you verify a project is what it claims, not the headline.
 *
 * Criticality and health are two different axes, never merged into one
 * score: the table's Criticality column is a recorded judgement, the Health
 * column is what service-health evidence actually observed, and the
 * portfolio risk map above the table plots both together. Neither
 * collection auto-loads past the backend's page size — every extra page is
 * a `Load more` a person asked for, and the filter and summary copy say so
 * plainly while either collection is still partial.
 */

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";

import { ProjectRiskMap } from "@/components/data-viz/ProjectRiskMap";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { Button, FilterBar, SearchInput, Select } from "@/components/ui/controls";
import { RelativeTime } from "@/components/ui/identifiers";
import {
  DeniedState,
  EmptyState,
  ErrorState,
  LoadingSkeleton,
} from "@/components/ui/states";
import type { Project } from "@/lib/catalog";
import { humanize, type StatusTone } from "@/lib/design/status";
import { serviceHealthListPath, type ServiceHealthPage, type ServiceHealthRow } from "@/lib/serviceHealth";
import { useProgressiveCollection, type PageSlice } from "@/lib/useProgressiveCollection";
import { buildPortfolioRisk, type ProjectRiskItem } from "@/lib/view-models/portfolio-risk";

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

interface ProjectsPageResponse {
  projects: Project[];
  next_cursor: string | null;
}

function projectsPath(
  filters: { lifecycle: string; search: string; criticality: string },
  cursor?: string,
): string {
  const params = new URLSearchParams({ lifecycle: filters.lifecycle, limit: String(PAGE_SIZE) });
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
    nextPath: loaded < page.total ? serviceHealthListPath({ limit: PAGE_SIZE, offset: loaded }) : null,
    total: page.total,
  };
}

/** `risk` matches either a shared status tone or one of the two evidence
 * states the tone vocabulary alone cannot distinguish (`unassessed` and
 * `incomplete` both display as the `unknown` tone). */
function matchesRisk(item: ProjectRiskItem, risk: string): boolean {
  if (risk === "unassessed" || risk === "incomplete") return item.evidence === risk;
  return item.tone === risk;
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
  const projectsCollection = useProgressiveCollection<ProjectsPageResponse, Project>({
    firstPath: projectsPath(filters),
    pageToSlice: (page): PageSlice<Project> => ({
      items: page.projects,
      nextPath: page.next_cursor ? projectsPath(filters, page.next_cursor) : null,
    }),
  });

  const servicesCollection = useProgressiveCollection<ServiceHealthPage, ServiceHealthRow>({
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
  const filtered = search.length > 0 || criticality !== "" || lifecycle !== "active" || Boolean(risk);

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

  const columns: Column<Project>[] = [
    {
      key: "project",
      header: "Project",
      cell: (project) => (
        <>
          <Link
            href={`/projects/${project.id}`}
            className="rounded font-medium text-ink hover:text-brand"
          >
            {project.display_name}
          </Link>
          <span className="block font-mono text-micro text-ink-muted">{project.project_key}</span>
        </>
      ),
    },
    {
      key: "criticality",
      header: "Criticality",
      cell: (project) => (
        <StatusBadge
          status={CRITICALITY_TONE[project.criticality] ?? "neutral"}
          label={`${humanize(project.criticality)} criticality`}
          size="compact"
        />
      ),
    },
    {
      key: "health",
      header: "Health",
      cell: (project) => {
        const item = riskByProjectId.get(project.id);
        if (!item) return <span className="text-micro text-ink-muted">—</span>;
        return (
          <StatusBadge status={item.tone} label={`${item.healthLabel} health`} size="compact" />
        );
      },
    },
    {
      key: "lifecycle",
      header: "Lifecycle",
      priority: "low",
      cell: (project) => (
        <StatusBadge
          status={project.lifecycle === "active" ? "success" : "neutral"}
          label={humanize(project.lifecycle)}
          size="compact"
        />
      ),
    },
    {
      key: "environments",
      header: "Envs",
      align: "right",
      cell: (project) => project.counts.environments,
    },
    {
      key: "services",
      header: "Services",
      align: "right",
      cell: (project) => project.counts.services,
    },
    {
      key: "repository",
      header: "Repository",
      priority: "low",
      cell: (project) => (
        <span className="font-mono text-micro text-ink-secondary">
          {project.repository.owner}/{project.repository.name}
          <span className="ml-1.5 text-ink-muted">#{project.repository.default_branch}</span>
        </span>
      ),
    },
    {
      key: "source",
      header: "Record accepted",
      priority: "low",
      align: "right",
      cell: (project) => (
        <span className="text-micro text-ink-muted">
          <RelativeTime value={project.source.accepted_at} />
        </span>
      ),
    },
  ];

  return (
    <PageFrame>
      <PageHeader
        title="Projects"
        description="Your authorized project catalog. Operational signals attach as integrations are connected."
        meta={
          projects.length > 0 ? (
            <>
              <span>
                {projects.reduce((sum, project) => sum + project.counts.environments, 0)}{" "}
                environments
              </span>
              <span>
                {projects.reduce((sum, project) => sum + project.counts.services, 0)} services
              </span>
              {!projectsCollection.complete ? (
                <StatusBadge status="unknown" label="Partial view" size="compact" />
              ) : null}
            </>
          ) : undefined
        }
      />

      <div className="space-y-4">
        {model.items.length > 0 ? (
          <Panel data-testid="projects-risk">
            <PanelHeader
              title="Portfolio risk"
              description="Criticality is a recorded judgement; health is what service-health evidence has actually observed. A project with no observed services is unassessed, never healthy."
              level={2}
            />
            <ProjectRiskMap
              model={model}
              activeTone={risk}
              onToneChange={(tone) => updateParams({ risk: tone ?? "" })}
            />
          </Panel>
        ) : null}

        <Panel flush>
          <div className="border-b border-border px-4 py-3">
            <FilterBar
              summary={
                projectsCollection.loading && projects.length === 0
                  ? undefined
                  : risk && !model.complete
                    ? `Filtered within ${projects.length} loaded project${projects.length === 1 ? "" : "s"}`
                    : `${visibleProjects.length} project${visibleProjects.length === 1 ? "" : "s"}${
                        filtered ? " matching" : ""
                      }`
              }
              onReset={
                filtered
                  ? () => {
                      setDraft("");
                      router.replace("/projects", { scroll: false });
                    }
                  : undefined
              }
            >
              <SearchInput
                label="Search projects by key or name"
                placeholder="Key or name…"
                value={draft}
                onChange={setDraft}
                className="w-full sm:w-64"
              />
              <Select
                label="Lifecycle"
                value={lifecycle}
                options={LIFECYCLE_OPTIONS}
                onChange={(value) => updateParams({ lifecycle: value })}
              />
              <Select
                label="Criticality"
                value={criticality}
                placeholder="Any"
                options={CRITICALITY_OPTIONS}
                onChange={(value) => updateParams({ criticality: value })}
              />
            </FilterBar>
          </div>

          {projectsCollection.loading && projects.length === 0 ? (
            <div className="px-4 py-4">
              <LoadingSkeleton variant="table" rows={4} label="Loading projects" />
            </div>
          ) : projectsCollection.denied ? (
            <div className="px-4 py-2">
              <DeniedState />
            </div>
          ) : projects.length === 0 && projectsCollection.error ? (
            <div className="px-4 py-2">
              <ErrorState
                description={projectsCollection.error ?? undefined}
                correlationId={projectsCollection.correlationId}
                onRetry={projectsCollection.reload}
              />
            </div>
          ) : (
            <>
              <div data-testid="project-list">
                <DataTable
                  caption="Projects in your authorized scope"
                  rows={visibleProjects}
                  columns={columns}
                  rowKey={(project) => project.id}
                  emptyState={
                    <EmptyState
                      title={filtered ? "No projects match these filters" : "No projects in your scope"}
                      description={
                        filtered
                          ? "Clear the filters to see everything you are authorized for."
                          : "Projects you are authorized to see will appear here once they are onboarded."
                      }
                    />
                  }
                />
              </div>
              {!projectsCollection.complete || !servicesCollection.complete ? (
                <div className="flex flex-wrap gap-2 border-t border-border px-4 py-2">
                  {!projectsCollection.complete ? (
                    <Button
                      onClick={projectsCollection.loadMore}
                      disabled={projectsCollection.loadingMore}
                      size="compact"
                    >
                      {projectsCollection.loadingMore ? "Loading…" : "Load more projects"}
                    </Button>
                  ) : null}
                  {!servicesCollection.complete ? (
                    <Button
                      onClick={servicesCollection.loadMore}
                      disabled={servicesCollection.loadingMore}
                      size="compact"
                      variant="secondary"
                    >
                      {servicesCollection.loadingMore ? "Loading…" : "Load more evidence"}
                    </Button>
                  ) : null}
                </div>
              ) : null}
            </>
          )}
        </Panel>
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
