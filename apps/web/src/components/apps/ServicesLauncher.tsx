"use client";

/**
 * Apps, then the chosen app's environments, then that environment's
 * services as cards. Services and Projects both open on it. The chosen
 * project and environment are URL state the page owns, so switching views
 * keeps the place and a link shares it.
 */

import { ArrowUpRight } from "lucide-react";
import Link from "next/link";
import { useMemo, type ReactNode } from "react";

import { STATUS_ORDER } from "@/components/service-health/primitives";
import { answered } from "@/lib/dashboards";
import { useT } from "@/lib/i18n";
import { groupApps, pulseOf, usePlatformOverview, useProjectNames } from "@/lib/platform";
import type { ServiceHealthRow } from "@/lib/serviceHealth";
import { boundIds, judgeSignal, signalsPath, type SignalItem, type SignalPage } from "@/lib/signals";
import { useResource } from "@/lib/useResource";

import { AppGrid, AppShelf, EnvCard, EnvGrid } from "./AppLauncher";
import { ServiceCard } from "./ServiceCard";

const TONE_RANK: Record<string, number> = { critical: 0, warning: 1, success: 2 };

/** Worst first: reported state, then what the traces say, then by name. */
export function sortServiceRows(rows: ServiceHealthRow[], signals: Map<string, SignalItem>): ServiceHealthRow[] {
  const verdict = (row: ServiceHealthRow) => {
    const item = row.binding ? signals.get(row.binding.id) : undefined;
    return item ? (TONE_RANK[judgeSignal(item).tone] ?? 3) : 3;
  };
  return [...rows].sort(
    (a, b) =>
      STATUS_ORDER.indexOf(a.health.status) - STATUS_ORDER.indexOf(b.health.status) ||
      verdict(a) - verdict(b) ||
      (a.display_name || a.service_key).localeCompare(b.display_name || b.service_key),
  );
}

export function ServicesLauncher({
  rows,
  signalById,
  project,
  environment,
  onProject,
  onEnvironment,
  shelfAction,
  hideGrid = false,
}: {
  rows: ServiceHealthRow[];
  /** The page draws its own app picker (Projects' portfolio cards). */
  hideGrid?: boolean;
  /** The page's own signal read, when it already has one. */
  signalById?: Map<string, SignalItem>;
  project: string;
  environment: string;
  onProject: (project: string) => void;
  onEnvironment: (environment: string) => void;
  /** Extra control in the chosen app's header (Projects: open the project). */
  shelfAction?: (projectId: string) => ReactNode;
}) {
  const ta = useT("apps");
  const names = useProjectNames();
  const apps = useMemo(() => groupApps(rows, names), [rows, names]);
  const { overview, failed } = usePlatformOverview(rows);
  const traffic = answered(overview?.traffic);
  const ownSignals = useResource<SignalPage>(signalById ? null : signalsPath(boundIds(rows)), {
    refreshMs: 60_000,
  });
  const signals = useMemo(
    () => signalById ?? new Map((ownSignals.data?.items ?? []).map((item) => [item.binding_id, item])),
    [signalById, ownSignals.data],
  );

  const selected = apps.find((a) => a.projectId === project) ?? (apps.length === 1 ? apps[0] : null);
  const env =
    selected?.envs.find((e) => e.environmentId === environment) ??
    (selected && selected.envs.length === 1 ? selected.envs[0] : null);
  const services = env ? sortServiceRows(env.rows, signals) : [];

  return (
    <div className="flex flex-col gap-6" data-testid="services-launcher">
      {hideGrid ? null : (
        <AppGrid apps={apps} selectedId={selected?.projectId ?? null} onSelect={(id) => onProject(id ?? "")} />
      )}
      {selected ? (
        <AppShelf
          key={selected.projectId}
          app={selected}
          onClose={() => onProject("")}
          title={ta("pickEnv")}
          action={shelfAction?.(selected.projectId)}
        >
          <EnvGrid count={selected.envs.length}>
            {selected.envs.map((e) => (
              <EnvCard
                key={e.environmentId}
                env={e}
                pulse={pulseOf(overview, e.bindingIds)}
                pending={!overview && !failed}
                active={env?.environmentId === e.environmentId}
                onSelect={() => onEnvironment(env?.environmentId === e.environmentId ? "" : e.environmentId)}
                cta={ta("pickService")}
              />
            ))}
          </EnvGrid>
          {env ? (
            <div className="mt-6" key={env.environmentId}>
              <h3 className="mb-3 flex items-baseline gap-2 text-[0.9375rem] font-semibold text-ink">
                {ta("pickService")}
                <span className="font-mono text-micro font-normal text-ink-muted">
                  {selected.projectKey}/{env.key} · {ta("services", { count: services.length })}
                </span>
              </h3>
              <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
                {services.map((row, i) => (
                  <ServiceCard
                    key={row.environment_service_id}
                    row={row}
                    index={i}
                    item={row.binding ? signals.get(row.binding.id) : undefined}
                    traffic={
                      row.binding
                        ? (traffic?.find((x) => x.binding_id === row.binding!.id)?.buckets.map((b) => b.requests) ?? null)
                        : null
                    }
                  />
                ))}
              </ul>
            </div>
          ) : null}
        </AppShelf>
      ) : null}
    </div>
  );
}

/** The Projects page's shelf action: the project's own page. */
export function OpenProjectLink({ projectId }: { projectId: string }) {
  const ta = useT("apps");
  return (
    <Link
      href={`/projects/${projectId}`}
      className="inline-flex h-8 items-center gap-1.5 rounded-full border border-border bg-surface px-3 text-caption font-medium text-ink transition-colors hover:bg-surface-hover"
    >
      {ta("openProject")}
      <ArrowUpRight aria-hidden className="h-3.5 w-3.5" />
    </Link>
  );
}
