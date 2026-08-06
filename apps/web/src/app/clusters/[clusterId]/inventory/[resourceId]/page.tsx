"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import { LoadGate, MetaRow, useApi } from "@/components/catalog/primitives";
import {
  HealthBadge,
  InventoryStateBadge,
  formatUtc,
} from "@/components/inventory/primitives";
import { DataState } from "@/components/state/DataState";
import { StatusBadge } from "@/components/state/StatusBadge";
import { Card } from "@/components/ui/Card";
import type { InventoryResourceDetail } from "@/lib/inventory";

function BoundedMap({ entries }: { entries: Record<string, string> }) {
  const pairs = Object.entries(entries);
  if (pairs.length === 0) {
    return <p className="text-sm italic text-ink-muted">None recorded.</p>;
  }
  return (
    <ul className="space-y-1">
      {pairs.map(([key, value]) => (
        <li key={key} className="break-all font-mono text-xs text-ink">
          <span className="text-ink-muted">{key}=</span>
          {value}
        </li>
      ))}
    </ul>
  );
}

export default function InventoryResourcePage() {
  const { clusterId, resourceId } = useParams<{
    clusterId: string;
    resourceId: string;
  }>();
  const [resource, retry] = useApi<InventoryResourceDetail>(
    `/v1/clusters/${clusterId}/inventory/resources/${resourceId}`,
  );

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <LoadGate value={resource} retry={retry}>
        {(data) => (
          <>
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div>
                <p className="text-xs text-ink-muted">
                  <Link href="/clusters" className="hover:text-ink">
                    Clusters
                  </Link>{" "}
                  /{" "}
                  <Link href={`/clusters/${clusterId}`} className="hover:text-ink">
                    Detail
                  </Link>{" "}
                  /{" "}
                  <Link
                    href={`/clusters/${clusterId}/inventory`}
                    className="hover:text-ink"
                  >
                    Inventory
                  </Link>{" "}
                  / <span className="font-mono">{data.kind}</span>
                </p>
                <h1 className="mt-1 break-all text-xl font-semibold tracking-tight text-ink">
                  {data.name}
                </h1>
                <p className="mt-1 font-mono text-xs text-ink-muted">
                  {data.namespace ? `${data.namespace} · ` : ""}
                  {data.kind}
                  {data.api_group ? ` · ${data.api_group}/${data.api_version}` : ""}
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <HealthBadge health={data.health} />
                {data.lifecycle === "missing" ? (
                  <StatusBadge status="stale" label="missing" />
                ) : null}
              </div>
            </div>

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <Card title="Health" data-testid="health-card">
                <div className="space-y-2">
                  <HealthBadge health={data.health} />
                  {data.health_reasons.length > 0 ? (
                    <ul className="space-y-1" data-testid="health-reasons">
                      {data.health_reasons.map((reason) => (
                        <li key={reason} className="font-mono text-xs text-ink-secondary">
                          {reason}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-sm text-ink-secondary">
                      No adverse signals in the observed status.
                    </p>
                  )}
                </div>
              </Card>

              <Card title="Observation" data-testid="observation-card">
                <dl className="divide-y divide-border">
                  <MetaRow label="Inventory state">
                    <InventoryStateBadge state={data.inventory.state} />
                  </MetaRow>
                  <MetaRow label="First seen">
                    <span className="font-mono text-xs">{formatUtc(data.first_seen_at)}</span>
                  </MetaRow>
                  <MetaRow label="Last seen">
                    <span className="font-mono text-xs">{formatUtc(data.last_seen_at)}</span>
                  </MetaRow>
                  <MetaRow label="Observed at (source)">
                    <span className="font-mono text-xs">{formatUtc(data.observed_at)}</span>
                  </MetaRow>
                  <MetaRow label="UID">
                    <span className="break-all font-mono text-xs">{data.uid}</span>
                  </MetaRow>
                  <MetaRow label="Source">
                    <span className="font-mono text-xs">{data.provenance.source}</span>
                  </MetaRow>
                </dl>
              </Card>
            </div>

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <Card title="Spec summary (bounded)">
                {Object.keys(data.spec_summary).length === 0 ? (
                  <p className="text-sm italic text-ink-muted">No summarized spec fields.</p>
                ) : (
                  <dl className="divide-y divide-border">
                    {Object.entries(data.spec_summary).map(([key, value]) => (
                      <MetaRow key={key} label={key}>
                        <span className="font-mono text-xs">{String(value)}</span>
                      </MetaRow>
                    ))}
                  </dl>
                )}
              </Card>
              <Card title="Status summary (bounded)">
                {Object.keys(data.status_summary).length === 0 ? (
                  <p className="text-sm italic text-ink-muted">
                    No summarized status fields.
                  </p>
                ) : (
                  <dl className="divide-y divide-border">
                    {Object.entries(data.status_summary).map(([key, value]) => (
                      <MetaRow key={key} label={key}>
                        <span className="font-mono text-xs">{String(value)}</span>
                      </MetaRow>
                    ))}
                  </dl>
                )}
              </Card>
            </div>

            <Card title="Conditions">
              {data.conditions.length === 0 ? (
                <DataState
                  kind="no-data"
                  title="No conditions reported"
                  description="The source object carries no status conditions."
                />
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-xl text-left text-sm">
                    <thead>
                      <tr className="border-b border-border text-xs text-ink-muted">
                        <th scope="col" className="py-2 pr-3 font-medium">
                          Type
                        </th>
                        <th scope="col" className="py-2 pr-3 font-medium">
                          Status
                        </th>
                        <th scope="col" className="py-2 pr-3 font-medium">
                          Reason
                        </th>
                        <th scope="col" className="py-2 font-medium">
                          Message
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                      {data.conditions.map((condition) => (
                        <tr key={condition.type}>
                          <td className="py-2 pr-3 font-mono text-xs">{condition.type}</td>
                          <td className="py-2 pr-3 font-mono text-xs">
                            {condition.status}
                          </td>
                          <td className="py-2 pr-3 font-mono text-xs">
                            {condition.reason ?? "—"}
                          </td>
                          <td className="py-2 text-xs text-ink-secondary">
                            {condition.message ?? "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <Card title="Labels (allowlisted)">
                <BoundedMap entries={data.labels} />
              </Card>
              <Card title="Annotations (allowlisted)">
                <BoundedMap entries={data.annotations} />
              </Card>
            </div>

            {data.owners.length > 0 ? (
              <Card title="Owners">
                <ul className="divide-y divide-border">
                  {data.owners.map((owner) => (
                    <li
                      key={owner.uid}
                      className="flex flex-wrap items-center justify-between gap-2 px-1 py-2"
                    >
                      <span className="font-mono text-xs text-ink">
                        {owner.kind}/{owner.name}
                      </span>
                      <span className="break-all font-mono text-xs text-ink-muted">
                        {owner.uid}
                      </span>
                    </li>
                  ))}
                </ul>
              </Card>
            ) : null}
          </>
        )}
      </LoadGate>
    </div>
  );
}
