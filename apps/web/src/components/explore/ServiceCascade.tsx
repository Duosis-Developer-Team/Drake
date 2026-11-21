"use client";

/**
 * Choose a service the way people think about it: the application first,
 * then which of its environments, then the service.
 *
 * A flat "service · project/env" list grows with every environment and
 * reads as twenty near-identical lines; here each step only offers what the
 * step before allows. Applications carry their logo, environments are
 * ordered production first, and the choice that matters — the binding — is
 * still the one value the caller owns (it lives in the URL).
 */

import { useMemo, useState } from "react";

import { Select } from "@/components/ui/controls";
import { ProjectLogo } from "@/components/ui/ProjectLogo";
import { useT } from "@/lib/i18n";
import type { ServiceHealthRow } from "@/lib/serviceHealth";

const ENV_RANK: Record<string, number> = {
  prod: 0,
  production: 0,
  live: 0,
  staging: 1,
  stage: 1,
  preprod: 1,
  uat: 2,
  qa: 2,
  test: 2,
  dev: 3,
  development: 3,
};

/** Production first, then staging, test and development; others by name. */
export function compareEnvironments(a: string, b: string): number {
  const rank = (key: string) => ENV_RANK[key.toLowerCase()] ?? 4;
  return rank(a) - rank(b) || a.localeCompare(b);
}

export interface Cascade {
  projectId: string | null;
  environmentId: string | null;
}

/** The rows the cascade's current choice still admits. */
export function admitted(
  rows: ServiceHealthRow[],
  choice: Cascade,
): ServiceHealthRow[] {
  return rows.filter(
    (row) =>
      (!choice.projectId || row.project_id === choice.projectId) &&
      (!choice.environmentId || row.environment_id === choice.environmentId),
  );
}

const CHIP =
  "inline-flex h-8 items-center gap-2 rounded-full border px-3 text-caption font-medium transition-colors"; // i18n-ignore: CSS
const CHIP_ON = "border-brand bg-brand-soft text-brand"; // i18n-ignore: CSS
const CHIP_OFF =
  "border-border bg-surface text-ink-secondary hover:bg-surface-hover hover:text-ink"; // i18n-ignore: CSS

function Step({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-2">
      <span className="w-24 shrink-0 text-micro font-semibold tracking-[0.08em] text-ink-muted uppercase">
        {label}
      </span>
      {children}
    </div>
  );
}

export function ServiceCascade({
  rows,
  bindingId,
  onPick,
  onScope,
}: {
  /** Bound services only. */
  rows: ServiceHealthRow[];
  /** The chosen binding, or "" for none. */
  bindingId: string;
  onPick: (bindingId: string) => void;
  /** The application/environment narrowed to, for lists beside the picker. */
  onScope?: (choice: Cascade) => void;
}) {
  const t = useT("explore");
  const current = rows.find((row) => row.binding?.id === bindingId) ?? null;
  const [choice, setChoice] = useState<Cascade>(() => ({
    projectId: current?.project_id ?? null,
    environmentId: current?.environment_id ?? null,
  }));
  // A binding chosen elsewhere (the quick list, a link) moves the cascade.
  const effective: Cascade = current
    ? { projectId: current.project_id, environmentId: current.environment_id }
    : choice;

  const projects = useMemo(() => {
    const seen = new Map<
      string,
      { id: string; key: string; services: number }
    >();
    for (const row of rows) {
      const entry = seen.get(row.project_id) ?? {
        id: row.project_id,
        key: row.project_key,
        services: 0,
      };
      entry.services += 1;
      seen.set(row.project_id, entry);
    }
    return [...seen.values()].sort((a, b) => a.key.localeCompare(b.key));
  }, [rows]);

  const environments = useMemo(() => {
    if (!effective.projectId) return [];
    const seen = new Map<
      string,
      { id: string; key: string; services: number }
    >();
    for (const row of rows) {
      if (row.project_id !== effective.projectId) continue;
      const entry = seen.get(row.environment_id) ?? {
        id: row.environment_id,
        key: row.environment_key,
        services: 0,
      };
      entry.services += 1;
      seen.set(row.environment_id, entry);
    }
    return [...seen.values()].sort((a, b) => compareEnvironments(a.key, b.key));
  }, [rows, effective.projectId]);

  const { projectId, environmentId } = effective;
  const services = useMemo(
    () =>
      projectId && environmentId
        ? admitted(rows, { projectId, environmentId })
            .map((row) => ({ value: row.binding!.id, label: row.display_name || row.service_key }))
            .sort((a, b) => a.label.localeCompare(b.label))
        : [],
    [rows, projectId, environmentId],
  );

  const choose = (next: Cascade) => {
    setChoice(next);
    onScope?.(next);
    if (bindingId) onPick("");
  };

  const pickProject = (projectId: string) => {
    if (projectId === effective.projectId) {
      choose({ projectId: null, environmentId: null });
      return;
    }
    const envs = new Set(
      rows
        .filter((row) => row.project_id === projectId)
        .map((row) => row.environment_id),
    );
    // One environment: nothing to ask. Otherwise keep the same environment
    // key if the new application has one (prod → prod).
    let environmentId: string | null = envs.size === 1 ? [...envs][0] : null;
    if (!environmentId && effective.environmentId) {
      const key = rows.find(
        (row) => row.environment_id === effective.environmentId,
      )?.environment_key;
      environmentId =
        rows.find(
          (row) => row.project_id === projectId && row.environment_key === key,
        )?.environment_id ?? null;
    }
    choose({ projectId, environmentId });
  };

  return (
    <div className="space-y-2.5" data-testid="service-cascade">
      <Step label={t("cascade.application")}>
        <div
          role="radiogroup"
          aria-label={t("cascade.application")}
          className="flex flex-wrap gap-2"
        >
          {projects.map((project) => {
            const on = project.id === effective.projectId;
            return (
              <button
                key={project.id}
                type="button"
                role="radio"
                aria-checked={on}
                onClick={() => pickProject(project.id)}
                className={`${CHIP} pl-1.5 ${on ? CHIP_ON : CHIP_OFF}`}
              >
                <ProjectLogo
                  projectId={project.id}
                  name={project.key}
                  size="sm"
                  variant="mark"
                />
                {project.key}
                <span className="text-micro text-ink-muted" data-tabular>
                  {project.services}
                </span>
              </button>
            );
          })}
        </div>
      </Step>
      <Step label={t("cascade.environment")}>
        {environments.length === 0 ? (
          <span className="text-caption text-ink-muted">
            {t("cascade.pickApplication")}
          </span>
        ) : (
          <div
            role="radiogroup"
            aria-label={t("cascade.environment")}
            className="flex flex-wrap gap-2"
          >
            {environments.map((environment) => {
              const on = environment.id === effective.environmentId;
              return (
                <button
                  key={environment.id}
                  type="button"
                  role="radio"
                  aria-checked={on}
                  onClick={() =>
                    choose({
                      projectId: effective.projectId,
                      environmentId: on ? null : environment.id,
                    })
                  }
                  className={`${CHIP} ${on ? CHIP_ON : CHIP_OFF}`}
                >
                  {environment.key}
                  <span className="text-micro text-ink-muted" data-tabular>
                    {environment.services}
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </Step>
      <Step label={t("pick")}>
        {services.length === 0 ? (
          <span className="text-caption text-ink-muted">
            {effective.projectId
              ? t("cascade.pickEnvironment")
              : t("cascade.pickApplication")}
          </span>
        ) : (
          <Select
            label={t("pick")}
            hideLabel
            value={current ? bindingId : ""}
            placeholder={t("choose")}
            options={services}
            onChange={onPick}
            data-testid="explore-service-picker"
          />
        )}
      </Step>
    </div>
  );
}
