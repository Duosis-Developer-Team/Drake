"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import {
  LifecycleBadge,
  LoadGate,
  MetaRow,
  provenanceProps,
  useApi,
} from "@/components/catalog/primitives";
import {
  AgentBadge,
  HealthBadge,
  InventoryStateBadge,
  RollupCounts,
  formatUtc,
} from "@/components/inventory/primitives";
import { Provenance } from "@/components/provenance/Provenance";
import { DataState } from "@/components/state/DataState";
import { StatusBadge } from "@/components/state/StatusBadge";
import { Card } from "@/components/ui/Card";
import type { Cluster } from "@/lib/catalog";
import type { InventorySummary } from "@/lib/inventory";

export default function ClusterDetailPage() {
  const { clusterId } = useParams<{ clusterId: string }>();
  const [cluster, retry] = useApi<Cluster>(`/v1/clusters/${clusterId}`);
  const [summary, retrySummary] = useApi<InventorySummary>(
    `/v1/clusters/${clusterId}/inventory/summary`,
  );

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <LoadGate value={cluster} retry={retry}>
        {(data) => (
          <>
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div>
                <p className="text-xs text-ink-muted">
                  <Link href="/clusters" className="hover:text-ink">
                    Clusters
                  </Link>{" "}
                  / <span className="font-mono">{data.cluster_ref}</span>
                </p>
                <h1 className="mt-1 text-xl font-semibold tracking-tight text-ink">
                  {data.display_name}
                </h1>
              </div>
              <LifecycleBadge lifecycle={data.lifecycle} />
            </div>

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <Card
                title="Cluster metadata"
                footer={<Provenance {...provenanceProps(data.source, data.as_of)} />}
              >
                <dl className="divide-y divide-border">
                  <MetaRow label="Reference">
                    <span className="font-mono text-xs">{data.cluster_ref}</span>
                  </MetaRow>
                  <MetaRow label="Site">
                    {data.site || <span className="italic text-ink-muted">—</span>}
                  </MetaRow>
                  <MetaRow label="Catalog version">
                    <span className="font-mono text-xs">v{data.version}</span>
                  </MetaRow>
                </dl>
              </Card>

              <Card title="Referenced environments (authorized)">
                {data.referenced_environments &&
                data.referenced_environments.length > 0 ? (
                  <ul className="divide-y divide-border">
                    {data.referenced_environments.map((environment) => (
                      <li
                        key={`${environment.project_key}/${environment.environment_key}`}
                        className="flex items-center justify-between px-1 py-2 text-sm"
                      >
                        <span className="font-mono text-xs text-ink">
                          {environment.project_key}/{environment.environment_key}
                        </span>
                        <span className="font-mono text-xs text-ink-muted">
                          {environment.namespace}
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <DataState
                    kind="empty"
                    title="No authorized environments"
                    description="Environments you can see that run on this cluster will appear here."
                  />
                )}
              </Card>
            </div>

            <section aria-label="Cluster agent and inventory">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <h2 className="text-sm font-semibold text-ink">Agent &amp; inventory</h2>
                <Link
                  href={`/clusters/${clusterId}/inventory`}
                  className="rounded-lg border border-border px-2.5 py-1 text-xs font-medium text-ink-secondary hover:bg-surface-sunken"
                >
                  Browse resources
                </Link>
              </div>
              <LoadGate value={summary} retry={retrySummary}>
                {(inventory) => (
                  <div className="space-y-4">
                    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                      <Card title="Cluster agent" data-testid="agent-card">
                        <dl className="divide-y divide-border">
                          <MetaRow label="Connectivity">
                            <AgentBadge status={inventory.agent.status} />
                          </MetaRow>
                          <MetaRow label="Agent version">
                            <span className="font-mono text-xs">
                              {inventory.agent.agent_version || "—"}
                            </span>
                          </MetaRow>
                          <MetaRow label="Last heartbeat">
                            <span className="font-mono text-xs">
                              {formatUtc(inventory.agent.last_heartbeat_at)}
                            </span>
                          </MetaRow>
                          <MetaRow label="Certificate expires">
                            <span className="flex items-center gap-2">
                              <span className="font-mono text-xs">
                                {formatUtc(inventory.agent.certificate_not_after)}
                              </span>
                              {inventory.agent.certificate_expiry_warning ? (
                                <StatusBadge status="warning" label="expires soon" />
                              ) : null}
                            </span>
                          </MetaRow>
                        </dl>
                      </Card>

                      <Card title="Inventory freshness" data-testid="freshness-card">
                        <dl className="divide-y divide-border">
                          <MetaRow label="State">
                            <InventoryStateBadge state={inventory.inventory.state} />
                          </MetaRow>
                          <MetaRow label="Last full reconcile">
                            <span className="font-mono text-xs">
                              {formatUtc(inventory.inventory.last_reconcile_at)}
                            </span>
                          </MetaRow>
                          <MetaRow label="Last change applied">
                            <span className="font-mono text-xs">
                              {formatUtc(inventory.inventory.last_event_at)}
                            </span>
                          </MetaRow>
                          <MetaRow label="Resources (active / missing)">
                            <span className="font-mono text-xs">
                              {inventory.inventory.active_resources ?? 0} /{" "}
                              {inventory.inventory.missing_resources ?? 0}
                            </span>
                          </MetaRow>
                        </dl>
                      </Card>
                    </div>

                    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
                      <Card title="Nodes">
                        <RollupCounts rollup={inventory.nodes} />
                      </Card>
                      <Card title="Namespaces">
                        <RollupCounts rollup={inventory.namespaces} />
                      </Card>
                      <Card title="Workloads">
                        <RollupCounts rollup={inventory.workloads} />
                      </Card>
                      <Card title="Pods" data-testid="pods-card">
                        <div className="space-y-2">
                          <RollupCounts rollup={inventory.pods} />
                          <dl className="flex flex-wrap gap-x-4 gap-y-1 border-t border-border pt-2 text-sm">
                            <div className="flex items-baseline gap-1.5">
                              <dt className="text-xs text-ink-muted">restarts</dt>
                              <dd className="font-mono text-sm text-ink">
                                {inventory.pods.restarts}
                              </dd>
                            </div>
                            <div className="flex items-baseline gap-1.5">
                              <dt className="text-xs text-critical">CrashLoop</dt>
                              <dd className="font-mono text-sm text-ink">
                                {inventory.pods.crashloop}
                              </dd>
                            </div>
                            <div className="flex items-baseline gap-1.5">
                              <dt className="text-xs text-critical">OOM killed</dt>
                              <dd className="font-mono text-sm text-ink">
                                {inventory.pods.oom_killed}
                              </dd>
                            </div>
                          </dl>
                        </div>
                      </Card>
                      <Card title="Persistent volume claims">
                        <RollupCounts rollup={inventory.persistent_volume_claims} />
                      </Card>
                    </div>

                    <Card title="Resources by kind">
                      {Object.keys(inventory.by_kind).length === 0 ? (
                        <DataState
                          kind="empty"
                          title="No inventory yet"
                          description="No completed snapshot has been received from this cluster."
                        />
                      ) : (
                        <div className="overflow-x-auto">
                          <table className="w-full min-w-md text-left text-sm">
                            <thead>
                              <tr className="border-b border-border text-xs text-ink-muted">
                                <th scope="col" className="py-2 pr-3 font-medium">
                                  Kind
                                </th>
                                <th scope="col" className="py-2 pr-3 font-medium">
                                  Total
                                </th>
                                <th scope="col" className="py-2 pr-3 font-medium">
                                  Worst health
                                </th>
                                <th scope="col" className="py-2 font-medium">
                                  <span className="sr-only">Browse</span>
                                </th>
                              </tr>
                            </thead>
                            <tbody className="divide-y divide-border">
                              {Object.entries(inventory.by_kind).map(([kind, rollup]) => (
                                <tr key={kind}>
                                  <td className="py-2 pr-3 font-mono text-xs">{kind}</td>
                                  <td className="py-2 pr-3 font-mono text-xs">
                                    {rollup.total}
                                  </td>
                                  <td className="py-2 pr-3">
                                    <HealthBadge
                                      health={
                                        rollup.unhealthy > 0
                                          ? "unhealthy"
                                          : rollup.degraded > 0
                                            ? "degraded"
                                            : rollup.unknown > 0
                                              ? "unknown"
                                              : "healthy"
                                      }
                                    />
                                  </td>
                                  <td className="py-2 text-right">
                                    <Link
                                      href={`/clusters/${clusterId}/inventory?kind=${kind}`}
                                      className="text-xs text-ink-secondary underline-offset-2 hover:underline"
                                    >
                                      Browse
                                    </Link>
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                    </Card>
                  </div>
                )}
              </LoadGate>
            </section>
          </>
        )}
      </LoadGate>
    </div>
  );
}
