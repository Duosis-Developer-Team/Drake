"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";

import {
  CriticalityBadge,
  LifecycleBadge,
  LoadGate,
  useApi,
} from "@/components/catalog/primitives";
import { DataState } from "@/components/state/DataState";
import { Card } from "@/components/ui/Card";
import type { Project } from "@/lib/catalog";

function ProjectsInner() {
  const router = useRouter();
  const params = useSearchParams();
  const search = params.get("search") ?? "";
  const lifecycle = params.get("lifecycle") ?? "active";
  const criticality = params.get("criticality") ?? "";
  const [searchDraft, setSearchDraft] = useState(search);

  const query = useMemo(() => {
    const q = new URLSearchParams({ lifecycle });
    if (search.length >= 2) q.set("search", search);
    if (criticality) q.set("criticality", criticality);
    return q.toString();
  }, [search, lifecycle, criticality]);

  const [projects, retry] = useApi<{ projects: Project[]; next_cursor: string | null }>(
    `/v1/projects?${query}`,
  );

  const updateParams = (updates: Record<string, string>) => {
    const next = new URLSearchParams(params.toString());
    for (const [key, value] of Object.entries(updates)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    router.replace(`/projects?${next.toString()}`);
  };

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">Projects</h1>
        <p className="mt-1 text-sm text-ink-secondary">
          Your authorized project catalog. Operational signals attach as
          integrations are connected.
        </p>
      </div>

      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          updateParams({ search: searchDraft.length >= 2 ? searchDraft : "" });
        }}
      >
        <div className="min-w-48 flex-1">
          <label htmlFor="project-search" className="mb-1 block text-xs font-medium text-ink-secondary">
            Search
          </label>
          <input
            id="project-search"
            value={searchDraft}
            onChange={(event) => setSearchDraft(event.target.value)}
            placeholder="Key or name…"
            className="h-9 w-full rounded-lg border border-border bg-surface px-3 text-sm text-ink placeholder:text-ink-muted focus-visible:outline-2 focus-visible:outline-accent"
          />
        </div>
        <div>
          <label htmlFor="project-lifecycle" className="mb-1 block text-xs font-medium text-ink-secondary">
            Lifecycle
          </label>
          <select
            id="project-lifecycle"
            value={lifecycle}
            onChange={(event) => updateParams({ lifecycle: event.target.value })}
            className="h-9 rounded-lg border border-border bg-surface px-2 text-sm text-ink"
          >
            <option value="active">Active</option>
            <option value="archived">Archived</option>
            <option value="all">All</option>
          </select>
        </div>
        <div>
          <label htmlFor="project-criticality" className="mb-1 block text-xs font-medium text-ink-secondary">
            Criticality
          </label>
          <select
            id="project-criticality"
            value={criticality}
            onChange={(event) => updateParams({ criticality: event.target.value })}
            className="h-9 rounded-lg border border-border bg-surface px-2 text-sm text-ink"
          >
            <option value="">Any</option>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
            <option value="critical">Critical</option>
          </select>
        </div>
        <button
          type="submit"
          className="h-9 rounded-lg border border-border px-4 text-sm text-ink-secondary hover:bg-surface-sunken"
        >
          Apply
        </button>
      </form>

      <Card>
        <LoadGate value={projects} retry={retry}>
          {(body) =>
            body.projects.length === 0 ? (
              <DataState
                kind="empty"
                title="No projects in your scope"
                description="Projects you are authorized to see will appear here."
              />
            ) : (
              <ul className="divide-y divide-border" data-testid="project-list">
                {body.projects.map((project) => (
                  <li key={project.id}>
                    <Link
                      href={`/projects/${project.id}`}
                      className="flex flex-wrap items-center gap-x-4 gap-y-2 px-1 py-3 hover:bg-surface-sunken focus-visible:outline-2 focus-visible:outline-accent"
                    >
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium text-ink">
                          {project.display_name}
                        </span>
                        <span className="block font-mono text-xs text-ink-muted">
                          {project.project_key} · {project.repository.owner}/
                          {project.repository.name}
                        </span>
                      </span>
                      <span className="hidden text-xs text-ink-secondary sm:block">
                        {project.counts.environments} env ·{" "}
                        {project.counts.services} services
                      </span>
                      <span className="flex items-center gap-1.5">
                        <CriticalityBadge criticality={project.criticality} />
                        <LifecycleBadge lifecycle={project.lifecycle} />
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )
          }
        </LoadGate>
      </Card>
    </div>
  );
}

export default function ProjectsPage() {
  return (
    <Suspense fallback={<DataState kind="loading" />}>
      <ProjectsInner />
    </Suspense>
  );
}
