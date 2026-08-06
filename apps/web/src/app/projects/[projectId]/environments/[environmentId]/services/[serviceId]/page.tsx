"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import {
  LifecycleBadge,
  LoadGate,
  MetaRow,
  OperationalGrid,
  provenanceProps,
  useApi,
} from "@/components/catalog/primitives";
import { Provenance } from "@/components/provenance/Provenance";
import { DashboardRenderer } from "@/components/telemetry/DashboardRenderer";
import { Card } from "@/components/ui/Card";
import type { ServiceDetail } from "@/lib/catalog";
import { parseRangePreset } from "@/lib/telemetry";

const OPERATIONAL_LABELS = {
  metrics: "Golden signals",
  logs: "Logs",
  traces: "Traces",
  deployments: "Deploy history",
};

export default function ServiceDetailPage() {
  const { projectId, environmentId, serviceId } = useParams<{
    projectId: string;
    environmentId: string;
    serviceId: string;
  }>();
  const [service, retry] = useApi<ServiceDetail>(
    `/v1/projects/${projectId}/environments/${environmentId}/services/${serviceId}`,
  );
  const searchParams = useSearchParams();
  const preset = parseRangePreset(searchParams.get("range"));

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <LoadGate value={service} retry={retry}>
        {(data) => {
          const [projectKey, environmentKey] = data.scope.ref.split("/");
          return (
            <>
              <div className="flex flex-wrap items-end justify-between gap-3">
                <div>
                  <p className="text-xs text-ink-muted">
                    <Link href="/projects" className="hover:text-ink">
                      Projects
                    </Link>{" "}
                    /{" "}
                    <Link href={`/projects/${projectId}`} className="hover:text-ink">
                      <span className="font-mono">{projectKey}</span>
                    </Link>{" "}
                    /{" "}
                    <Link
                      href={`/projects/${projectId}/environments/${environmentId}`}
                      className="hover:text-ink"
                    >
                      <span className="font-mono">{environmentKey}</span>
                    </Link>{" "}
                    / <span className="font-mono">{data.service_key}</span>
                  </p>
                  <h1 className="mt-1 text-xl font-semibold tracking-tight text-ink">
                    {data.display_name || data.service_key}
                  </h1>
                </div>
                <LifecycleBadge lifecycle={data.lifecycle} />
              </div>

              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                <Card
                  title="Service metadata"
                  footer={<Provenance {...provenanceProps(data.source, data.as_of)} />}
                >
                  <dl className="divide-y divide-border">
                    <MetaRow label="Component">
                      <span className="font-mono text-xs">{data.component}</span>
                    </MetaRow>
                    <MetaRow label="Runtime">
                      <span className="font-mono text-xs">{data.runtime}</span>
                    </MetaRow>
                    <MetaRow label="Metrics profile">
                      <span className="font-mono text-xs">{data.metrics_profile}</span>
                    </MetaRow>
                    <MetaRow label="Catalog version">
                      <span className="font-mono text-xs">v{data.version}</span>
                    </MetaRow>
                  </dl>
                </Card>

                <Card title="Bindings & probes">
                  <dl className="divide-y divide-border">
                    <MetaRow label="Workload selector">
                      {Object.keys(data.workload_selector).length > 0 ? (
                        <span className="font-mono text-[11px]">
                          {Object.entries(data.workload_selector)
                            .map(([key, value]) => `${key}=${value}`)
                            .join(", ")}
                        </span>
                      ) : (
                        <span className="text-xs italic text-ink-muted">not configured</span>
                      )}
                    </MetaRow>
                    <MetaRow label="Health paths">
                      {Object.keys(data.health).length > 0 ? (
                        <span className="font-mono text-[11px]">
                          {Object.entries(data.health)
                            .map(([key, value]) => `${key}: ${value}`)
                            .join(" · ")}
                        </span>
                      ) : (
                        <span className="text-xs italic text-ink-muted">not configured</span>
                      )}
                    </MetaRow>
                  </dl>
                </Card>
              </div>

              <section aria-label="Golden signals" className="space-y-3">
                <h2 className="text-sm font-semibold text-ink">Golden signals</h2>
                <Suspense fallback={null}>
                  <DashboardRenderer
                    templateKey="service-golden-signals-v1"
                    scopeType="service"
                    scopeId={serviceId}
                    preset={preset}
                    profile={data.metrics_profile}
                  />
                </Suspense>
              </section>

              <section aria-label="Operational capabilities">
                <h2 className="mb-3 text-sm font-semibold text-ink">
                  Operational capabilities
                </h2>
                <OperationalGrid states={data.operational} labels={OPERATIONAL_LABELS} />
              </section>
            </>
          );
        }}
      </LoadGate>
    </div>
  );
}
