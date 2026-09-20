"use client";

/**
 * Project Overview metrics: the generic environment-overview dashboard
 * rendered for ONE selected authorized environment. The selector offers
 * only environments the caller can already see; selection and time range
 * live in the URL (`?env=`, `?range=`). No multi-environment fan-out.
 */

import { Activity } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { IconBubble, TileState } from "@/components/catalog/visuals";
import { DashboardRenderer } from "@/components/telemetry/DashboardRenderer";
import { Select } from "@/components/ui/controls";
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
        <SignalsHeading description="Telemetry for one environment at a time." />
        <div className="rounded-[1.5rem] border border-border bg-surface p-7 shadow-panel">
          <TileState
            icon={Activity}
            testId="state-not-configured"
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
      <SignalsHeading
        description={`Telemetry for ${selected.environment_key} — one environment at a time, range in the top bar.`}
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
            <span className="rounded-full border border-border bg-surface-2 px-3 py-1.5 font-mono text-micro text-ink-secondary">
              {selected.environment_key}
            </span>
          )
        }
      />
      <div>
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

function SignalsHeading({
  description,
  actions,
}: {
  description: string;
  actions?: React.ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-center justify-between gap-x-4 gap-y-3">
      <div className="flex min-w-0 items-center gap-3">
        <IconBubble icon={Activity} />
        <div className="min-w-0">
          <h2 className="text-[1.25rem] leading-7 font-semibold tracking-[-0.015em] text-ink">Signals</h2>
          <p className="text-caption text-ink-muted">{description}</p>
        </div>
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  );
}
