import { Provenance } from "@/components/provenance/Provenance";
import { DataState } from "@/components/state/DataState";
import { StatusBadge } from "@/components/state/StatusBadge";
import { Card } from "@/components/ui/Card";

/**
 * Command Center — Sprint 0 foundation.
 *
 * No sources are connected yet, so every domain card honestly reports
 * "not configured" with an empty provenance footer. No fabricated KPIs,
 * no hard-coded healthy states.
 */

const DOMAIN_CARDS = [
  {
    title: "Fleet health",
    description: "Cluster and workload health arrives with the inventory sprint.",
  },
  {
    title: "Active incidents",
    description: "Alert-to-incident projection arrives with the incidents sprint.",
  },
  {
    title: "Capacity risk",
    description: "Node and volume capacity signals arrive with the telemetry sprint.",
  },
  {
    title: "Backup & RPO",
    description: "Backup freshness and restore evidence arrive with the protection sprint.",
  },
  {
    title: "Tenants",
    description: "Tenant plans and usage snapshots arrive with the metering sprint.",
  },
  {
    title: "Recent deployments",
    description: "Commit-to-rollout correlation arrives with the GitHub sprint.",
  },
] as const;

export default function CommandCenterPage() {
  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-ink">Command Center</h1>
          <p className="mt-1 text-sm text-ink-secondary">
            Unified operations view. Sources appear here as integrations are connected.
          </p>
        </div>
        <StatusBadge status="unknown" label="No sources connected" />
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
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
