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
import { useT } from "@/lib/i18n";
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
  const t = useT("commandCenter");
  if (!cell) {
    return (
      <span
        className="flex w-full items-center justify-center rounded-[0.875rem] border border-dashed border-border py-3 text-micro text-ink-muted"
        aria-label={t("matrix.emptyCell", { project: projectKey, environment: environmentKey })}
      >
        —
      </span>
    );
  }
  const worst = worstToneOf(cell);
  const spec = toneSpec(worst);
  const status = t(`tone.${worst}`);
  const count = cell.services.length;
  const href = `/service-health?project_id=${encodeURIComponent(cell.projectId)}&environment_id=${encodeURIComponent(cell.environmentId)}`;
  return (
    <Link
      href={href}
      aria-label={t("matrix.cell", { project: projectKey, environment: environmentKey, status, count })}
      className={`flex w-full items-center justify-between gap-3 rounded-[0.875rem] px-3.5 py-3 text-caption font-medium transition-[opacity,transform] hover:-translate-y-px hover:opacity-90 ${spec.chip}`}
    >
      <span className="flex min-w-0 items-center gap-2">
        <span aria-hidden className={`h-2.5 w-2.5 shrink-0 rounded-full ${spec.dot}`} />
        <span className="truncate">{status}</span>
      </span>
      <span className="flex items-baseline gap-1">
        <span data-tabular className="rounded-full bg-surface/70 px-2 py-0.5 text-micro font-semibold">({count})</span>
      </span>
    </Link>
  );
}

function Grid({ cells }: { cells: HealthMatrixCell[] }) {
  const t = useT("commandCenter");
  const projects = [...new Set(cells.map((cell) => cell.projectKey))];
  const environments = [...new Set(cells.map((cell) => cell.environmentKey))];
  const byKey = new Map(cells.map((cell) => [cellKey(cell.projectKey, cell.environmentKey), cell]));

  return (
    <div className="hidden overflow-x-auto xl:block" data-testid="health-matrix-grid">
      <table className="w-full border-separate border-spacing-0 text-caption">
        <caption className="sr-only">{t("matrix.caption")}</caption>
        <thead>
          <tr>
            <th scope="col" className="w-32" />
            {projects.map((project) => (
              <th
                key={project}
                scope="col"
                className="px-2 pb-2 text-left text-micro font-semibold tracking-[0.08em] text-ink-muted uppercase"
              >
                {project}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {environments.map((environment) => (
            <tr key={environment}>
              <th scope="row" className="py-1.5 pr-4 text-left font-medium text-ink">
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
    <div className={compact ? "" : "xl:hidden" /* i18n-ignore: CSS */} data-testid="health-matrix-disclosure">
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
  const t = useT("commandCenter");
  if (status === "loading") {
    return <LoadingSkeleton variant="table" rows={3} label={t("matrix.loading")} />;
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
        title={t("matrix.emptyTitle")}
        description={t("matrix.emptyDescription")}
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
