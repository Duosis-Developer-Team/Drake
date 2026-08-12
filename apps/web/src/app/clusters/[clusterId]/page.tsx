"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { PageFrame } from "@/components/shell/AppShell";
import { DashboardRenderer } from "@/components/telemetry/DashboardRenderer";
import { parseRangePreset } from "@/lib/telemetry";

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
  formatUtc,
} from "@/components/inventory/primitives";
import { Provenance } from "@/components/provenance/Provenance";
import { Countdown, Donut, ToneCounters } from "@/components/charts/visuals";
import type { HealthRollup } from "@/lib/inventory";
import { DataState } from "@/components/state/DataState";
import { StatusBadge } from "@/components/state/StatusBadge";
import { Card } from "@/components/ui/Card";
import type { Cluster } from "@/lib/catalog";
import type { InventorySummary } from "@/lib/inventory";

/** One rollup, as donut slices. The unknown bucket is always present. */
function healthSlices(rollup: HealthRollup) {
  return [
    { name: "Healthy", value: rollup.healthy, tone: "success" as const },
    { name: "Degraded", value: rollup.degraded, tone: "warning" as const },
    { name: "Unhealthy", value: rollup.unhealthy, tone: "critical" as const },
    { name: "Unknown", value: rollup.unknown, tone: "unknown" as const },
  ];
}

export default function ClusterDetailPage() {
  const { clusterId } = useParams<{ clusterId: string }>();
  // The same range control the service boards use, read from the URL,
  // so a link to a cluster at 7d opens at 7d.
  const preset = parseRangePreset(useSearchParams().get("range"));
  const [cluster, retry] = useApi<Cluster>(`/v1/clusters/${clusterId}`);
  const [summary, retrySummary] = useApi<InventorySummary>(
    `/v1/clusters/${clusterId}/inventory/summary`,
  );

  return (
    <PageFrame>
      <div className="space-y-5">
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
                <h1 className="mt-1 text-title font-semibold text-ink">
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
              {/* Capacity is what the HOST can give, and it comes from the
                  metrics backend rather than from inventory — which is why it
                  sits above the agent's own view rather than inside it. An
                  agent can be perfectly healthy on a node that is full. */}
              <DashboardRenderer
                templateKey="cluster-capacity-v1"
                scopeType="cluster"
                scopeId={clusterId}
                preset={preset}
                profile="kubernetes-service-v1"
              />

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
                        {/* A deadline, not a measurement — so it drains toward
                            the near end rather than filling toward a limit.
                            The server owns the warning; this only draws how
                            much runway is left. */}
                        <div className="mt-3 border-t border-border pt-3">
                          <Countdown
                            label="Certificate runway"
                            deadline={inventory.agent.certificate_not_after}
                          />
                        </div>
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
                        <Donut
                          size={108}
                          thickness={12}
                          label="Nodes by health"
                          centerLabel={`${inventory.nodes.total}`}
                          slices={healthSlices(inventory.nodes)}
                        />
                      </Card>
                      <Card title="Namespaces">
                        <Donut
                          size={108}
                          thickness={12}
                          label="Namespaces by health"
                          centerLabel={`${inventory.namespaces.total}`}
                          slices={healthSlices(inventory.namespaces)}
                        />
                      </Card>
                      <Card title="Workloads">
                        <Donut
                          size={108}
                          thickness={12}
                          label="Workloads by health"
                          centerLabel={`${inventory.workloads.total}`}
                          slices={healthSlices(inventory.workloads)}
                        />
                      </Card>
                      <Card title="Pods" data-testid="pods-card">
                        <div className="space-y-3">
                          <Donut
                            size={108}
                            thickness={12}
                            label="Pods by health"
                            centerLabel={`${inventory.pods.total}`}
                            slices={healthSlices(inventory.pods)}
                          />
                          {/* Failure modes, not a composition: these do not
                              add up to the pod total, so they are counters
                              rather than wedges. */}
                          <div className="border-t border-border pt-2">
                            <ToneCounters
                              size="compact"
                              items={[
                                {
                                  label: "restarts",
                                  count: inventory.pods.restarts,
                                  tone: "warning",
                                },
                                {
                                  label: "CrashLoop",
                                  count: inventory.pods.crashloop,
                                  tone: "critical",
                                },
                                {
                                  label: "OOM killed",
                                  count: inventory.pods.oom_killed,
                                  tone: "critical",
                                },
                              ]}
                            />
                          </div>
                        </div>
                      </Card>
                      <Card title="Persistent volume claims">
                        <Donut
                          size={108}
                          thickness={12}
                          label="Persistent volume claims by health"
                          centerLabel={`${inventory.persistent_volume_claims.total}`}
                          slices={healthSlices(inventory.persistent_volume_claims)}
                        />
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
                          <table className="w-full text-left text-sm">
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
    </PageFrame>
  );
}
