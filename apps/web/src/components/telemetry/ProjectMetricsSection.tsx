"use client";

/**
 * Project Overview metrics: the generic environment-overview dashboard
 * rendered for ONE selected authorized environment. The selector offers
 * only environments the caller can already see; selection and time range
 * live in the URL (`?env=`, `?range=`). No multi-environment fan-out.
 */

import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { DashboardRenderer } from "@/components/telemetry/DashboardRenderer";
import { SectionHeader } from "@/components/ui/Panel";
import { Select } from "@/components/ui/controls";
import { NotConfiguredState } from "@/components/ui/states";
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
      <section aria-label="Signals">
        <SectionHeader title="Signals" />
        <div className="mt-3">
          <NotConfiguredState
            title="No environment to measure"
            description="Signals appear once this project has an active environment you are authorized to see."
          />
        </div>
      </section>
    );
  }

  const selectEnvironment = (id: string) => {
    const params = new URLSearchParams(searchParams.toString());
    params.set("env", id);
    router.replace(`${pathname}?${params.toString()}`, { scroll: false });
  };

  return (
    <section aria-label="Signals" data-testid="project-metrics">
      <SectionHeader
        title="Signals"
        description={`Telemetry for one environment at a time — currently ${selected.environment_key}. Selection and time range are in the URL.`}
        actions={
          active.length > 1 ? (
            <Select
              label="Environment"
              value={selected.id}
              options={active.map((environment) => ({
                value: environment.id,
                label: environment.environment_key,
              }))}
              onChange={(value) => selectEnvironment(value)}
            />
          ) : (
            <span className="font-mono text-micro text-ink-muted">
              {selected.environment_key}
            </span>
          )
        }
      />
      <div className="mt-3">
        <DashboardRenderer
          templateKey="project-environment-overview-v1"
          scopeType="environment"
          scopeId={selected.id}
          preset={preset}
        />
      </div>
    </section>
  );
}
