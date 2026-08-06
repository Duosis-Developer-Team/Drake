"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import {
  LifecycleBadge,
  LoadGate,
  MetaRow,
  OperationalGrid,
  provenanceProps,
  useApi,
} from "@/components/catalog/primitives";
import { Provenance } from "@/components/provenance/Provenance";
import { DataState } from "@/components/state/DataState";
import { Card } from "@/components/ui/Card";
import type { Cluster } from "@/lib/catalog";

const OPERATIONAL_LABELS = {
  agent: "Cluster agent",
  inventory: "Inventory",
};

export default function ClusterDetailPage() {
  const { clusterId } = useParams<{ clusterId: string }>();
  const [cluster, retry] = useApi<Cluster>(`/v1/clusters/${clusterId}`);

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

            <section aria-label="Operational capabilities">
              <h2 className="mb-3 text-sm font-semibold text-ink">
                Operational capabilities
              </h2>
              <OperationalGrid states={data.operational} labels={OPERATIONAL_LABELS} />
            </section>
          </>
        )}
      </LoadGate>
    </div>
  );
}
