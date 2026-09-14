"use client";

/**
 * The service × environment health map (brief §9.4).
 *
 * A grid, not a donut: one degraded service among fifty healthy ones must
 * stay visible as its own cell rather than disappear into an aggregate
 * ring. At 1280px and above it renders as a real `<table>` (rows =
 * environments, columns = projects); below that width — 1024px included —
 * a horizontally-scrolling table is the wrong trade, so it renders as one
 * `<details>` per project instead.
 *
 * Both variants render into the DOM together, one hidden by a breakpoint —
 * the same dual-render pattern `AppShell`'s mobile nav already uses — except
 * when the caller passes `compact` explicitly, which renders only the
 * disclosure list. jsdom does not evaluate media queries, so tests reach for
 * `compact` instead of faking a viewport.
 *
 * A cell is never color-only: the worst status is also named as visible
 * text, and a populated cell drills down into the same authorized
 * service-health list, scoped to that project/environment — not a new
 * surface, just the existing one filtered tighter.
 */

import Link from "next/link";

import { toneSpec } from "@/lib/design/status";
import { DeniedState, ErrorState, LoadingSkeleton, NotConfiguredState } from "@/components/ui/states";
import { worstToneOf, type HealthMatrixCell } from "@/lib/view-models/health-matrix";

type ResourceStatus = "loading" | "denied" | "not-found" | "error" | "ready";

function cellKey(projectKey: string, environmentKey: string): string {
  return `${projectKey}\u0000${environmentKey}`;
}

function Swatch({
  cell,
  projectKey,
  environmentKey,
}: {
  cell: HealthMatrixCell | undefined;
  projectKey: string;
  environmentKey: string;
}) {
  if (!cell) {
    return (
      <span
        className="text-micro text-ink-muted"
        aria-label={`${projectKey} / ${environmentKey}: no services here`}
      >
        —
      </span>
    );
  }
  const spec = toneSpec(worstToneOf(cell));
  const count = cell.services.length;
  const href = `/service-health?project_id=${encodeURIComponent(cell.projectId)}&environment_id=${encodeURIComponent(cell.environmentId)}`;
  return (
    <Link
      href={href}
      aria-label={`${projectKey} / ${environmentKey}: worst status ${spec.label}, ${count} service${count === 1 ? "" : "s"} — open service health`}
      className={`inline-flex w-full items-center justify-between gap-2 rounded-lg px-2.5 py-1.5 text-caption font-medium transition-opacity hover:opacity-80 ${spec.chip}`}
    >
      <span className="flex min-w-0 items-center gap-1.5">
        <span aria-hidden className={`h-2 w-2 shrink-0 rounded-full ${spec.dot}`} />
        <span className="truncate">{spec.label}</span>
      </span>
      <span className="text-micro opacity-70">({count})</span>
    </Link>
  );
}

function Grid({ cells }: { cells: HealthMatrixCell[] }) {
  const projects = [...new Set(cells.map((cell) => cell.projectKey))];
  const environments = [...new Set(cells.map((cell) => cell.environmentKey))];
  const byKey = new Map(cells.map((cell) => [cellKey(cell.projectKey, cell.environmentKey), cell]));

  return (
    <div className="hidden overflow-x-auto xl:block" data-testid="health-matrix-grid">
      <table className="w-full border-collapse text-caption">
        <caption className="sr-only">Service health by project and environment</caption>
        <thead>
          <tr>
            <th scope="col" className="w-32" />
            {projects.map((project) => (
              <th
                key={project}
                scope="col"
                className="border-b border-border px-3 py-1.5 text-left font-medium text-ink-secondary"
              >
                {project}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {environments.map((environment) => (
            <tr key={environment}>
              <th scope="row" className="py-1.5 pr-3 text-left font-medium text-ink-secondary">
                {environment}
              </th>
              {projects.map((project) => (
                <td key={project} className="px-1.5 py-1.5">
                  <Swatch
                    cell={byKey.get(cellKey(project, environment))}
                    projectKey={project}
                    environmentKey={environment}
                  />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Disclosure({ cells, compact }: { cells: HealthMatrixCell[]; compact: boolean }) {
  const projects = [...new Set(cells.map((cell) => cell.projectKey))];
  return (
    <div className={compact ? "" : "xl:hidden"} data-testid="health-matrix-disclosure">
      <ul className="divide-y divide-border">
        {projects.map((project) => (
          <li key={project}>
            <details open>
              <summary className="cursor-pointer px-1 py-2 text-caption font-medium text-ink">
                {project}
              </summary>
              <ul className="space-y-1.5 py-1.5 pl-3">
                {cells
                  .filter((cell) => cell.projectKey === project)
                  .map((cell) => (
                    <li key={cell.environmentKey} className="flex items-center justify-between gap-3">
                      <span className="text-caption text-ink-secondary">{cell.environmentKey}</span>
                      <Swatch cell={cell} projectKey={project} environmentKey={cell.environmentKey} />
                    </li>
                  ))}
              </ul>
            </details>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function HealthMatrix({
  cells,
  status,
  compact = false,
}: {
  cells: HealthMatrixCell[];
  status: ResourceStatus;
  compact?: boolean;
}) {
  if (status === "loading") {
    return <LoadingSkeleton variant="table" rows={3} label="Loading service health" />;
  }
  if (status === "denied") {
    return <DeniedState compact />;
  }
  if (status === "error") {
    return <ErrorState compact />;
  }
  if (cells.length === 0) {
    return (
      <NotConfiguredState
        compact
        title="No services to map"
        description="No service in your authorized scope has reported health yet."
      />
    );
  }

  return (
    <div data-testid="health-matrix">
      {!compact ? <Grid cells={cells} /> : null}
      <Disclosure cells={cells} compact={compact} />
    </div>
  );
}
