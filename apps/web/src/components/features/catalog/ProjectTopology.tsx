"use client";

/**
 * One drill-down lane per environment (design spec §6.2): a heading, its
 * placement, and the environment's own service rows underneath, worst
 * lane-evidence surfaced only where the service rows themselves cannot say
 * it — a `complete` lane already speaks for itself through its worst row,
 * so it carries no extra badge.
 */

import Link from "next/link";

import { ServiceHealthLane } from "@/components/features/catalog/ServiceHealthLane";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { useT } from "@/lib/i18n";
import type { EnvironmentLaneModel } from "@/lib/view-models/scope-health";

function LaneBadge({ lane }: { lane: EnvironmentLaneModel }) {
  const t = useT("catalog");
  if (lane.evidence === "complete") return null;
  return <StatusBadge status={lane.tone} label={t(`evidence.${lane.evidence}`)} size="compact" />;
}

export function ProjectTopology({ lanes }: { lanes: EnvironmentLaneModel[] }) {
  const t = useT("catalog");
  if (lanes.length === 0) {
    return <p className="px-1 py-2 text-caption text-ink-muted">{t("topology.noEnvironments")}</p>;
  }

  return (
    <div className="space-y-4">
      {lanes.map((lane) => (
        <section
          key={lane.id}
          data-testid={`environment-lane-${lane.id}`}
          className="rounded-panel border border-border bg-surface p-4"
        >
          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
            <Link href={lane.href} className="min-w-0">
              <h3 className="truncate text-section font-semibold text-ink hover:text-brand">
                {lane.key}
              </h3>
            </Link>
            <span className="flex items-center gap-2">
              {lane.runtime !== "external" ? (
                <span className="font-mono text-micro text-ink-muted">{lane.placement}</span>
              ) : null}
              <LaneBadge lane={lane} />
            </span>
          </div>
          <div className="mt-2">
            <ServiceHealthLane services={lane.services} />
          </div>
        </section>
      ))}
    </div>
  );
}
