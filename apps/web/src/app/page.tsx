"use client";

import { LoadGate, useApi } from "@/components/catalog/primitives";
import type { Loadable } from "@/components/catalog/primitives";
import { Provenance } from "@/components/provenance/Provenance";
import { DataState } from "@/components/state/DataState";
import { StatusBadge } from "@/components/state/StatusBadge";
import { Card } from "@/components/ui/Card";
import type { CatalogContext, Cluster } from "@/lib/catalog";
import type { InventorySummary } from "@/lib/inventory";

/**
 * Command Center.
 *
 * Fleet health reads the cluster agent's own inventory, because that is a
 * source Drake actually has. The rest still report "not configured", and
 * they say what would configure them rather than naming a sprint — a card
 * that promised "arrives with the inventory sprint" was still saying it
 * after the inventory shipped, which reads as a product that forgot.
 *
 * Nothing here is derived from a source that does not exist. Capacity is
 * the one worth naming: node and volume pressure need a signal Drake does
 * not collect yet, and a plausible number assembled from what it does have
 * would be the most dangerous card on the page.
 */

const DOMAIN_CARDS = [
  {
    title: "Active incidents",
    description: "No alert source is registered, so there is nothing to project into incidents.",
  },
  {
    title: "Capacity risk",
    description:
      "Needs node and volume pressure signals. Drake collects workload metrics, not host capacity.",
  },
  {
    title: "Backup & RPO",
    description: "No backup reporter is configured for any project in scope.",
  },
  {
    title: "Tenants",
    description: "No tenant plan or usage source is connected.",
  },
  {
    title: "Recent deployments",
    description: "No deployment source is connected for any project in scope.",
  },
] as const;

export default function CommandCenterPage() {
  // One fetch, two readers: the badge and the fleet card answer the same
  // question, and asking twice would let them disagree on screen.
  const [clusters, retry] = useApi<{ clusters: Cluster[] }>("/v1/clusters");
  // Both halves, in the API's own words: an agent that answers and an
  // inventory that is current. `stale` deliberately does not count — the
  // badge claims someone is looking now, not that someone once looked.
  const connected =
    clusters.state === "ready" &&
    clusters.data.clusters.some(
      (cluster) =>
        cluster.operational?.agent === "connected" &&
        cluster.operational?.inventory === "fresh",
    );
  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-ink">Command Center</h1>
          <p className="mt-1 text-sm text-ink-secondary">
            Unified operations view. Sources appear here as integrations are connected.
          </p>
        </div>
        <StatusBadge
          status={connected ? "healthy" : "unknown"}
          label={connected ? "Cluster inventory connected" : "No operational sources connected"}
        />
      </div>

      <CatalogCounts />

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        <FleetHealth clusters={clusters} retry={retry} />
        {DOMAIN_CARDS.map((card) => (
          <Card key={card.title} title={card.title} footer={<Provenance />}>
            <DataState kind="not-configured" description={card.description} />
          </Card>
        ))}
      </div>

      <Card title="Foundation status">
        <ul className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm text-ink-secondary sm:grid-cols-2">
          <li className="flex items-center gap-2">
            <StatusBadge status="healthy" label="Ready" /> Design tokens & dark mode
          </li>
          <li className="flex items-center gap-2">
            <StatusBadge status="healthy" label="Ready" /> Data-state primitives
          </li>
          <li className="flex items-center gap-2">
            <StatusBadge status="healthy" label="Ready" /> Provenance footer contract
          </li>
          <li className="flex items-center gap-2">
            <StatusBadge status="healthy" label="Ready" /> Navigation & responsive shell
          </li>
        </ul>
        <p className="mt-3 text-xs text-ink-muted">
          These describe this UI foundation itself, not any monitored system.
        </p>
      </Card>
    </div>
  );
}

/**
 * Fleet health, from the cluster agent's inventory.
 *
 * One row per cluster rather than one aggregated number: two clusters, one
 * healthy and one unreachable, average to something that describes neither,
 * and the operator's next question is always "which one".
 */
function FleetHealth({
  clusters,
  retry,
}: {
  clusters: Loadable<{ clusters: Cluster[] }>;
  retry: () => void;
}) {
  return (
    <Card title="Fleet health" footer={<Provenance />}>
      <LoadGate value={clusters} retry={retry}>
        {(body) =>
          body.clusters.length === 0 ? (
            <DataState
              kind="not-configured"
              description="No cluster is registered, so there is no fleet to report on."
            />
          ) : (
            <ul className="space-y-3" data-testid="fleet-health">
              {body.clusters.map((cluster) => (
                <ClusterFleetRow key={cluster.id} cluster={cluster} />
              ))}
            </ul>
          )
        }
      </LoadGate>
    </Card>
  );
}

function ClusterFleetRow({ cluster }: { cluster: Cluster }) {
  const [summary, retry] = useApi<InventorySummary>(
    `/v1/clusters/${cluster.id}/inventory/summary`,
  );
  return (
    <li>
      <p className="text-sm font-medium text-ink">{cluster.display_name}</p>
      <LoadGate value={summary} retry={retry}>
        {(data) =>
          data.agent.status !== "connected" ? (
            // An agent that is not connected has no current view to report.
            // Saying so beats showing its last numbers as if they were now.
            <DataState
              kind="not-configured"
              description={`Agent ${data.agent.status}; inventory ${data.inventory.state}.`}
            />
          ) : (
            <dl className="mt-1 grid grid-cols-3 gap-3 text-center">
              {(
                [
                  ["Nodes", data.nodes],
                  ["Workloads", data.workloads],
                  ["Pods", data.pods],
                ] as const
              ).map(([label, rollup]) => (
                <div key={label}>
                  <dd className="text-lg font-semibold tabular-nums text-ink">
                    {rollup.healthy}
                    <span className="text-sm font-normal text-ink-muted">/{rollup.total}</span>
                  </dd>
                  <dt className="text-xs text-ink-muted">{label} healthy</dt>
                </div>
              ))}
            </dl>
          )
        }
      </LoadGate>
    </li>
  );
}

/** Exact, authorized catalog counts — real PostgreSQL data, nothing more. */
function CatalogCounts() {
  const [context, retry] = useApi<CatalogContext>("/v1/catalog/context");
  return (
    <Card title="Your catalog">
      <LoadGate value={context} retry={retry}>
        {(data) => (
          <div
            className="grid grid-cols-3 gap-4 text-center"
            data-testid="catalog-counts"
          >
            {(
              [
                ["Projects", data.projects],
                ["Environments", data.environments],
                ["Clusters", data.clusters],
              ] as const
            ).map(([label, count]) => (
              <div key={label}>
                <p className="text-2xl font-semibold tabular-nums text-ink">{count}</p>
                <p className="text-xs text-ink-muted">{label}</p>
              </div>
            ))}
          </div>
        )}
      </LoadGate>
    </Card>
  );
}
