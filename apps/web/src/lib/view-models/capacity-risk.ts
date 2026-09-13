/**
 * Capacity risk — certificate expiry and PVC health, per cluster.
 *
 * Both facts already exist in `/v1/clusters/{id}/inventory/summary`; nothing
 * here invents a threshold. A certificate's risk is exactly the backend's own
 * `certificate_expiry_warning` flag, not a client-computed days-remaining
 * cutoff, and a PVC's risk is exactly its own `degraded`/`unhealthy` counts.
 */
import type { StatusTone } from "@/lib/design/status";
import type { Cluster } from "@/lib/catalog";
import type { InventorySummary } from "@/lib/inventory";

export interface CapacityRiskItem {
  key: string;
  clusterId: string;
  clusterName: string;
  kind: "certificate" | "pvc";
  label: string;
  tone: StatusTone;
  detail: string;
  deadline?: string | null;
  href: string;
}

function nameOf(cluster: Cluster): string {
  return cluster.display_name || cluster.cluster_ref;
}

export function certificateRiskItems(
  clusters: Cluster[],
  summaries: Map<string, InventorySummary>,
): CapacityRiskItem[] {
  const items: CapacityRiskItem[] = [];
  for (const cluster of clusters) {
    const summary = summaries.get(cluster.id);
    if (!summary?.agent.certificate_expiry_warning) continue;
    items.push({
      key: `certificate:${cluster.id}`,
      clusterId: cluster.id,
      clusterName: nameOf(cluster),
      kind: "certificate",
      label: "Agent certificate expiring",
      tone: "warning",
      detail: "The cluster agent's own report flags its certificate as nearing expiry.",
      deadline: summary.agent.certificate_not_after ?? null,
      href: `/clusters/${cluster.id}`,
    });
  }
  return items;
}

export function pvcRiskItems(
  clusters: Cluster[],
  summaries: Map<string, InventorySummary>,
): CapacityRiskItem[] {
  const items: CapacityRiskItem[] = [];
  for (const cluster of clusters) {
    const summary = summaries.get(cluster.id);
    const pvcs = summary?.persistent_volume_claims;
    if (!pvcs || pvcs.degraded + pvcs.unhealthy === 0) continue;
    items.push({
      key: `pvc:${cluster.id}`,
      clusterId: cluster.id,
      clusterName: nameOf(cluster),
      kind: "pvc",
      label: "Persistent volume claims at risk",
      tone: pvcs.unhealthy > 0 ? "critical" : "warning",
      detail: `${pvcs.unhealthy} unhealthy, ${pvcs.degraded} degraded of ${pvcs.total} total`,
      href: `/clusters/${cluster.id}/inventory`,
    });
  }
  return items;
}
