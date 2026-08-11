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
 */

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";

import { CompositionBar } from "@/components/charts/InlineBars";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { FilterBar, SearchInput, Select } from "@/components/ui/controls";
import { RelativeTime } from "@/components/ui/identifiers";
import {
  DeniedState,
  EmptyState,
  ErrorState,
  LoadingSkeleton,
} from "@/components/ui/states";
import type { Project } from "@/lib/catalog";
import { humanize, type StatusTone } from "@/lib/design/status";
import { useResource } from "@/lib/useResource";

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

function ProjectsInner() {
  const router = useRouter();
  const params = useSearchParams();
  const search = params.get("search") ?? "";
  const lifecycle = params.get("lifecycle") ?? "active";
  const criticality = params.get("criticality") ?? "";
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

  const query = useMemo(() => {
    const next = new URLSearchParams({ lifecycle });
    if (search.length >= 2) next.set("search", search);
    if (criticality) next.set("criticality", criticality);
    return next.toString();
  }, [search, lifecycle, criticality]);

  const resource = useResource<{ projects: Project[]; next_cursor: string | null }>(
    `/v1/projects?${query}`,
  );

  function updateParams(updates: Record<string, string>) {
    const next = new URLSearchParams(params.toString());
    for (const [key, value] of Object.entries(updates)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    router.replace(`/projects?${next.toString()}`, { scroll: false });
  }

  const projects = resource.data?.projects ?? [];
  const filtered = search.length > 0 || criticality !== "" || lifecycle !== "active";

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
          label={humanize(project.criticality)}
          size="compact"
        />
      ),
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

  const byCriticality = CRITICALITY_OPTIONS.map((option) => ({
    name: option.label,
    value: projects.filter((project) => project.criticality === option.value).length,
    tone: CRITICALITY_TONE[option.value],
  })).filter((entry) => entry.value > 0);

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
            </>
          ) : undefined
        }
      />

      <div className="space-y-4">
        {projects.length > 1 ? (
          <Panel data-testid="projects-summary">
            <PanelHeader
              title="Criticality mix"
              description="How the projects in this view are classified. Criticality is a recorded judgement, not a measured state."
              level={2}
            />
            <CompositionBar label="Projects by criticality" segments={byCriticality} />
          </Panel>
        ) : null}

        <Panel flush>
          <div className="border-b border-border px-4 py-3">
            <FilterBar
              summary={
                resource.data
                  ? `${projects.length} project${projects.length === 1 ? "" : "s"}${
                      filtered ? " matching" : ""
                    }`
                  : undefined
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

          {resource.loading && !resource.data ? (
            <div className="px-4 py-4">
              <LoadingSkeleton variant="table" rows={4} label="Loading projects" />
            </div>
          ) : resource.denied ? (
            <div className="px-4 py-2">
              <DeniedState />
            </div>
          ) : !resource.data ? (
            <div className="px-4 py-2">
              <ErrorState
                description={resource.error ?? undefined}
                correlationId={resource.correlationId}
                onRetry={resource.reload}
              />
            </div>
          ) : (
            <div data-testid="project-list">
              <DataTable
                caption="Projects in your authorized scope"
                rows={projects}
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
