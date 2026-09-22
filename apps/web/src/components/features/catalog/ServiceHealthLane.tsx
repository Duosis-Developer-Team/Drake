"use client";

/**
 * One environment's service rows, worst-first. Presentational and reusable
 * as-is: the Project topology view stacks one of these per environment, and
 * the Environment detail route (Wave 3B) reuses it unchanged for its own
 * single-environment scope — neither fetches, both trust the model to have
 * already ordered and evaluated the rows.
 *
 * A service with no binding still renders its own row: dropping it would
 * make an unbound service invisible instead of visibly unbound.
 */

import Link from "next/link";

import { useServiceStatusLabel } from "@/components/catalog/visuals";
import { StatusBadge, ToneAvatar } from "@/components/ui/StatusBadge";
import { useT } from "@/lib/i18n";
import type { ServiceLaneModel } from "@/lib/view-models/scope-health";

function BindingSummary({ binding }: { binding: ServiceLaneModel["binding"] }) {
  const t = useT("catalog");
  if (!binding) {
    return <span className="text-micro text-ink-muted">{t("card.notBound")}</span>;
  }
  return (
    <span className="font-mono text-micro text-ink-muted">
      {binding.cluster.cluster_ref}/{binding.namespace}/{binding.workload_name}
    </span>
  );
}

export function ServiceHealthLane({ services }: { services: ServiceLaneModel[] }) {
  const t = useT("catalog");
  const statusLabel = useServiceStatusLabel();
  if (services.length === 0) {
    return (
      <p className="px-1 py-2 text-caption text-ink-muted">{t("topology.noServices")}</p>
    );
  }

  return (
    <ul className="divide-y divide-border" data-testid="service-lane-list">
      {services.map((service) => (
        <li key={service.id}>
          <Link
            href={service.href}
            className="flex flex-wrap items-center justify-between gap-3 rounded-control px-2 py-2 transition-colors hover:bg-surface-hover"
          >
            <span className="flex min-w-0 items-center gap-3">
              <ToneAvatar status={service.tone} size="compact" />
              <span className="min-w-0">
                <span className="block truncate text-body font-medium text-ink">
                  {service.displayName}
                </span>
                <BindingSummary binding={service.binding} />
              </span>
            </span>
            <span className="flex items-center gap-1.5">
              {service.partial ? (
                <span className="text-micro italic text-ink-muted">{t("card.partial")}</span>
              ) : null}
              <StatusBadge
                status={service.tone}
                label={statusLabel(service.statusLabel)}
                size="compact"
              />
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}
