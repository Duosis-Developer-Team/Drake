"use client";

/**
 * Project Overview metrics: the generic environment-overview dashboard
 * rendered for ONE selected authorized environment. The selector offers
 * only environments the caller can already see; selection and time range
 * live in the URL (`?env=`, `?range=`). No multi-environment fan-out.
 */

import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { DashboardRenderer } from "@/components/telemetry/DashboardRenderer";
import type { Environment } from "@/lib/catalog";
import { parseRangePreset } from "@/lib/telemetry";

export function ProjectMetricsSection({ environments }: { environments: Environment[] }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const active = environments.filter((environment) => environment.lifecycle === "active");
  const requested = searchParams.get("env");
  const selected =
    active.find((environment) => environment.id === requested) ?? active[0] ?? null;
  const preset = parseRangePreset(searchParams.get("range"));

  if (!selected) {
    return (
      <section aria-label="Metrics">
        <h2 className="mb-3 text-sm font-semibold text-ink">Metrics</h2>
        <p className="text-sm italic text-ink-muted">
          No authorized environments to show metrics for.
        </p>
      </section>
    );
  }

  const selectEnvironment = (id: string) => {
    const params = new URLSearchParams(searchParams.toString());
    params.set("env", id);
    router.replace(`${pathname}?${params.toString()}`, { scroll: false });
  };

  return (
    <section aria-label="Metrics" className="space-y-3" data-testid="project-metrics">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-ink">Metrics</h2>
        {active.length > 1 ? (
          <label className="flex items-center gap-2 text-xs text-ink-muted">
            Environment
            <select
              value={selected.id}
              onChange={(event) => selectEnvironment(event.target.value)}
              className="h-8 rounded-lg border border-border bg-surface px-2 text-xs text-ink"
            >
              {active.map((environment) => (
                <option key={environment.id} value={environment.id}>
                  {environment.environment_key}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <span className="font-mono text-xs text-ink-muted">
            {selected.environment_key}
          </span>
        )}
      </div>
      <DashboardRenderer
        templateKey="project-environment-overview-v1"
        scopeType="environment"
        scopeId={selected.id}
        preset={preset}
      />
    </section>
  );
}
