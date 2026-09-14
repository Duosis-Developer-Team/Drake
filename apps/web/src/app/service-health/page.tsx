"use client";

/**
 * Service health list.
 *
 * Every service in scope appears, bound or not. A list that quietly dropped
 * unbound services would make an unobserved estate look like a healthy one,
 * which is the single most expensive way for a dashboard to be wrong.
 */

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";

import { LoadGate, useApi } from "@/components/catalog/primitives";
import {
  BindingStateBadge,
  HealthBadge,
} from "@/components/service-health/primitives";
import { ValueChip } from "@/components/charts/visuals";
import { DataState } from "@/components/state/DataState";
import { Panel } from "@/components/ui/Panel";
import {
  formatAge,
  serviceHealthListPath,
  type ServiceHealthPage,
  type ServiceHealthRow,
} from "@/lib/serviceHealth";

/** The pressure bands the platform already judges utilisation by. */
const UTILISATION = { warn: 0.8, critical: 0.9, direction: "above" as const };

/**
 * One service, as a card: identity and binding lead, health and its
 * freshness/partial caveat trail directly under it, and the four measured
 * numbers (ready/desired, restarts, CPU, memory) sit as a scannable strip
 * on the right rather than four more table columns each a few characters
 * wide.
 */
function ServiceRow({ row }: { row: ServiceHealthRow }) {
  const { binding, health } = row;
  return (
    <li
      data-testid={`service-row-${row.service_key}`}
      className="flex flex-wrap items-start gap-4 px-6 py-5 transition-colors hover:bg-surface-hover"
    >
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-body font-semibold text-ink">
            {row.display_name || row.service_key}
          </span>
          <HealthBadge status={health.status} />
          {health.served_from_last_good ? (
            <span className="text-micro italic text-stale">last known values</span>
          ) : health.partial ? (
            <span className="text-micro italic text-ink-muted">partial</span>
          ) : null}
        </div>
        <span className="mt-1 block font-mono text-micro text-ink-muted">
          {row.project_key}/{row.environment_key}/{row.service_key}
        </span>
        <div className="mt-2">
          {binding ? (
            <div className="flex flex-wrap items-center gap-2">
              <BindingStateBadge lifecycle={binding.lifecycle} resolved={binding.resolved} />
              <span className="font-mono text-micro text-ink-muted">
                {binding.cluster.cluster_ref}/{binding.namespace}/{binding.workload_name}
              </span>
              <Link
                href={`/service-health/${binding.id}`}
                className="text-caption font-medium text-ink-secondary underline hover:text-ink"
              >
                Details
              </Link>
            </div>
          ) : (
            <Link
              href={`/service-health/bind?environment_service_id=${row.environment_service_id}`}
              className="text-caption font-medium text-ink-secondary underline hover:text-ink"
            >
              Bind a workload
            </Link>
          )}
        </div>
      </div>
      <div className="flex shrink-0 flex-wrap items-start gap-5 text-right">
        <div>
          {/* Ready/desired stays two numbers: a single percentage would hide
              the difference between 0/0 and an unmeasured pair. */}
          <span className="block font-mono text-body text-ink" data-tabular>
            {health.availability?.ready_replicas ?? "—"} /{" "}
            {health.availability?.desired_replicas ?? "—"}
          </span>
          <span className="text-micro text-ink-muted">ready / desired</span>
        </div>
        <div>
          {/* A restart count has no ceiling, so it carries severity rather
              than a percentage: any restarts are worth a look, many are
              worth acting on. */}
          <ValueChip
            value={health.stability?.restarts_in_window ?? null}
            unit="count"
            thresholds={{ warn: 1, critical: 5, direction: "above" }}
          />
          <span className="mt-0.5 block text-micro text-ink-muted">restarts</span>
        </div>
        <div>
          {/* The chip is what makes this scannable: exact digits, with the
              colour saying which row to read first. */}
          <ValueChip
            value={health.resources?.cpu_utilization ?? null}
            unit="ratio"
            thresholds={UTILISATION}
          />
          <span className="mt-0.5 block text-micro text-ink-muted">CPU</span>
        </div>
        <div>
          <ValueChip
            value={health.resources?.memory_utilization ?? null}
            unit="ratio"
            thresholds={UTILISATION}
          />
          <span className="mt-0.5 block text-micro text-ink-muted">memory</span>
        </div>
        <span className="text-micro text-ink-muted">
          {formatAge(health.freshness_age_seconds)}
        </span>
      </div>
    </li>
  );
}

function ServiceHealthTable() {
  const params = useSearchParams();
  const [page, retry] = useApi<ServiceHealthPage>(
    serviceHealthListPath({
      projectId: params.get("project_id") ?? undefined,
      environmentId: params.get("environment_id") ?? undefined,
    }),
  );

  return (
    <LoadGate value={page} retry={retry}>
      {(data) =>
        data.items.length === 0 ? (
          <Panel>
            <DataState
              kind="empty"
              title="No services in scope"
              description="Nothing here is a statement about your grants, not about your estate."
            />
          </Panel>
        ) : (
          <Panel
            flush
            className="motion-safe:animate-[fade-in_400ms_var(--ease-entrance)_backwards]"
          >
            <ul className="divide-y divide-border" data-testid="service-health-table">
              {data.items.map((row) => (
                <ServiceRow key={row.environment_service_id} row={row} />
              ))}
            </ul>
            <p className="border-t border-border px-6 py-4 text-micro text-ink-muted">
              Showing {data.items.length} of {data.total} services in your authorized scope.
            </p>
          </Panel>
        )
      }
    </LoadGate>
  );
}

export default function ServiceHealthPage() {
  return (
    <PageFrame>
      <PageHeader
        title="Service health"
        description="Computed by Drake from curated queries against each service's bound workload. A dash means nothing was measured — never that the value is zero."
      />
      <div className="space-y-5">
        <Suspense fallback={<DataState kind="loading" />}>
          <ServiceHealthTable />
        </Suspense>
      </div>
    </PageFrame>
  );
}
