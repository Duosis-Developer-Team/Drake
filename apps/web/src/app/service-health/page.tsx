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

import { LoadGate, useApi } from "@/components/catalog/primitives";
import {
  BindingStateBadge,
  HealthBadge,
  Measure,
} from "@/components/service-health/primitives";
import { DataState } from "@/components/state/DataState";
import { Card } from "@/components/ui/Card";
import { formatAge, type ServiceHealthPage, type ServiceHealthRow } from "@/lib/serviceHealth";

function listPath(params: URLSearchParams): string {
  const query = new URLSearchParams();
  const environmentId = params.get("environment_id");
  const projectId = params.get("project_id");
  if (environmentId) query.set("environment_id", environmentId);
  if (projectId) query.set("project_id", projectId);
  const suffix = query.toString() ? `?${query}` : "";
  return `/v1/service-health/services${suffix}`;
}

function ServiceRow({ row }: { row: ServiceHealthRow }) {
  const { binding, health } = row;
  return (
    <tr className="border-t border-border align-top" data-testid={`service-row-${row.service_key}`}>
      <td className="py-2.5 pr-3">
        <div className="flex flex-col gap-1">
          <span className="text-sm font-medium text-ink">
            {row.display_name || row.service_key}
          </span>
          <span className="font-mono text-[11px] text-ink-muted">
            {row.project_key}/{row.environment_key}/{row.service_key}
          </span>
        </div>
      </td>
      <td className="py-2.5 pr-3">
        <HealthBadge status={health.status} />
        {health.served_from_last_good ? (
          <span className="mt-1 block text-[11px] italic text-stale">last known values</span>
        ) : health.partial ? (
          <span className="mt-1 block text-[11px] italic text-ink-muted">partial</span>
        ) : null}
      </td>
      <td className="py-2.5 pr-3">
        {binding ? (
          <div className="flex flex-col gap-1">
            <BindingStateBadge lifecycle={binding.lifecycle} resolved={binding.resolved} />
            <span className="font-mono text-[11px] text-ink-muted">
              {binding.cluster.cluster_ref}/{binding.namespace}/{binding.workload_name}
            </span>
          </div>
        ) : (
          <Link
            href={`/service-health/bind?environment_service_id=${row.environment_service_id}`}
            className="text-xs font-medium text-ink-secondary underline hover:text-ink"
          >
            Bind a workload
          </Link>
        )}
      </td>
      <td className="py-2.5 pr-3 whitespace-nowrap">
        {/* Ready/desired stays two numbers: a single percentage would hide
            the difference between 0/0 and an unmeasured pair. */}
        <span className="font-mono text-xs text-ink">
          {health.availability?.ready_replicas ?? "—"} /{" "}
          {health.availability?.desired_replicas ?? "—"}
        </span>
      </td>
      <td className="py-2.5 pr-3">
        <Measure value={health.stability?.restarts_in_window ?? null} unit="count" />
      </td>
      <td className="py-2.5 pr-3">
        <Measure value={health.resources?.cpu_utilization ?? null} unit="ratio" />
      </td>
      <td className="py-2.5 pr-3">
        <Measure value={health.resources?.memory_utilization ?? null} unit="ratio" />
      </td>
      <td className="py-2.5 pr-3 whitespace-nowrap text-xs text-ink-secondary">
        {formatAge(health.freshness_age_seconds)}
      </td>
      <td className="py-2.5">
        {binding ? (
          <Link
            href={`/service-health/${binding.id}`}
            className="text-xs font-medium text-ink-secondary underline hover:text-ink"
          >
            Details
          </Link>
        ) : null}
      </td>
    </tr>
  );
}

function ServiceHealthTable() {
  const params = useSearchParams();
  const [page, retry] = useApi<ServiceHealthPage>(listPath(params));

  return (
    <LoadGate value={page} retry={retry}>
      {(data) =>
        data.items.length === 0 ? (
          <Card>
            <DataState
              kind="empty"
              title="No services in scope"
              description="Nothing here is a statement about your grants, not about your estate."
            />
          </Card>
        ) : (
          <Card>
            <div className="overflow-x-auto">
              <table className="w-full text-left" data-testid="service-health-table">
                <thead>
                  <tr className="text-[11px] uppercase tracking-wide text-ink-muted">
                    <th className="pb-2 pr-3 font-medium">Service</th>
                    <th className="pb-2 pr-3 font-medium">Health</th>
                    <th className="pb-2 pr-3 font-medium">Binding</th>
                    <th className="pb-2 pr-3 font-medium">Ready / desired</th>
                    <th className="pb-2 pr-3 font-medium">Restarts</th>
                    <th className="pb-2 pr-3 font-medium">CPU</th>
                    <th className="pb-2 pr-3 font-medium">Memory</th>
                    <th className="pb-2 pr-3 font-medium">Last computed</th>
                    <th className="pb-2" />
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((row) => (
                    <ServiceRow key={row.environment_service_id} row={row} />
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-3 text-[11px] text-ink-muted">
              Showing {data.items.length} of {data.total} services in your authorized scope.
            </p>
          </Card>
        )
      }
    </LoadGate>
  );
}

export default function ServiceHealthPage() {
  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">Service health</h1>
        <p className="mt-1 text-sm text-ink-secondary">
          Computed by Drake from curated queries against each service&apos;s bound workload.
          A dash means nothing was measured — never that the value is zero.
        </p>
      </div>
      <Suspense fallback={<DataState kind="loading" />}>
        <ServiceHealthTable />
      </Suspense>
    </div>
  );
}
