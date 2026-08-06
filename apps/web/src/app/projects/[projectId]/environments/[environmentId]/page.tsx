"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import {
  CriticalityBadge,
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
import type { Environment, ServiceSummary } from "@/lib/catalog";

const OPERATIONAL_LABELS = {
  workloads: "Workloads & pods",
  targets: "Scrape targets",
  quotas: "Resource quotas",
  drift: "Config drift",
};

export default function EnvironmentDetailPage() {
  const { projectId, environmentId } = useParams<{
    projectId: string;
    environmentId: string;
  }>();
  const [environment, retryEnvironment] = useApi<Environment>(
    `/v1/projects/${projectId}/environments/${environmentId}`,
  );
  const [services, retryServices] = useApi<{ services: ServiceSummary[] }>(
    `/v1/projects/${projectId}/environments/${environmentId}/services`,
  );

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <LoadGate value={environment} retry={retryEnvironment}>
        {(data) => (
          <>
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div>
                <p className="text-xs text-ink-muted">
                  <Link href="/projects" className="hover:text-ink">
                    Projects
                  </Link>{" "}
                  /{" "}
                  <Link href={`/projects/${projectId}`} className="hover:text-ink">
                    <span className="font-mono">{data.scope.ref.split("/")[0]}</span>
                  </Link>{" "}
                  / <span className="font-mono">{data.environment_key}</span>
                </p>
                <h1 className="mt-1 text-xl font-semibold tracking-tight text-ink">
                  {data.environment_key}
                </h1>
              </div>
              <div className="flex items-center gap-1.5">
                <CriticalityBadge criticality={data.criticality} />
                <LifecycleBadge lifecycle={data.lifecycle} />
              </div>
            </div>

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <Card
                title="Environment metadata"
                footer={<Provenance {...provenanceProps(data.source, data.as_of)} />}
              >
                <dl className="divide-y divide-border">
                  <MetaRow label="Runtime">
                    <span className="font-mono text-xs">{data.runtime}</span>
                  </MetaRow>
                  <MetaRow label="Branch">
                    <span className="font-mono text-xs">{data.branch || "—"}</span>
                  </MetaRow>
                  <MetaRow label="Cluster / namespace">
                    {data.cluster ? (
                      <span className="font-mono text-xs">
                        {data.cluster.ref} / {data.namespace}
                      </span>
                    ) : (
                      <span className="text-xs italic text-ink-muted">
                        external runtime — no cluster binding
                      </span>
                    )}
                  </MetaRow>
                  <MetaRow label="Catalog version">
                    <span className="font-mono text-xs">v{data.version}</span>
                  </MetaRow>
                </dl>
              </Card>

              <Card title="Services">
                <LoadGate value={services} retry={retryServices}>
                  {(body) =>
                    body.services.length === 0 ? (
                      <DataState kind="empty" title="No service bindings" />
                    ) : (
                      <ul className="divide-y divide-border" data-testid="service-list">
                        {body.services.map((service) => (
                          <li key={service.id}>
                            <Link
                              href={`/projects/${projectId}/environments/${environmentId}/services/${service.id}`}
                              className="flex items-center justify-between gap-3 px-1 py-2.5 hover:bg-surface-sunken"
                            >
                              <span>
                                <span className="block text-sm font-medium text-ink">
                                  {service.display_name || service.service_key}
                                </span>
                                <span className="block font-mono text-xs text-ink-muted">
                                  {service.component} · {service.runtime}
                                </span>
                              </span>
                              <span className="font-mono text-[11px] text-ink-muted">
                                {service.metrics_profile}
                              </span>
                            </Link>
                          </li>
                        ))}
                      </ul>
                    )
                  }
                </LoadGate>
              </Card>
            </div>

            <section aria-label="Operational capabilities">
              <h2 className="mb-3 text-sm font-semibold text-ink">
                Operational capabilities
              </h2>
              <OperationalGrid
                states={data.operational ?? {}}
                labels={OPERATIONAL_LABELS}
              />
            </section>
          </>
        )}
      </LoadGate>
    </div>
  );
}
