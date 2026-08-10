"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { Suspense } from "react";

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
import { ProjectMetricsSection } from "@/components/telemetry/ProjectMetricsSection";
import { Card } from "@/components/ui/Card";
import type { Environment, Project } from "@/lib/catalog";

const OPERATIONAL_LABELS = {
  telemetry: "Telemetry",
  inventory: "Cluster inventory",
  deployment: "Deployments",
  protection: "Backup & restore",
};

export default function ProjectOverviewPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const [project, retryProject] = useApi<Project>(`/v1/projects/${projectId}`);
  const [environments, retryEnvironments] = useApi<{ environments: Environment[]; next_cursor: string | null }>(
    `/v1/projects/${projectId}/environments`,
  );

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <LoadGate value={project} retry={retryProject}>
        {(data) => (
          <>
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div>
                <p className="text-xs text-ink-muted">
                  <Link href="/projects" className="hover:text-ink">
                    Projects
                  </Link>{" "}
                  / <span className="font-mono">{data.project_key}</span>
                </p>
                <h1 className="mt-1 text-xl font-semibold tracking-tight text-ink">
                  {data.display_name}
                </h1>
              </div>
              <div className="flex items-center gap-1.5">
                <CriticalityBadge criticality={data.criticality} />
                <LifecycleBadge lifecycle={data.lifecycle} />
              </div>
            </div>

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <Card
                title="Project metadata"
                footer={<Provenance {...provenanceProps(data.source, data.as_of)} />}
              >
                <dl className="divide-y divide-border">
                  <MetaRow label="Repository">
                    <span className="font-mono text-xs">
                      {data.repository.provider}:{data.repository.owner}/
                      {data.repository.name}
                      {data.repository.default_branch
                        ? ` @ ${data.repository.default_branch}`
                        : ""}
                    </span>
                  </MetaRow>
                  <MetaRow label="Tenant model">
                    <span className="font-mono text-xs">{data.tenant_model}</span>
                  </MetaRow>
                  <MetaRow label="Owners">
                    {data.owners && data.owners.length > 0
                      ? data.owners
                          .map((owner) => `${owner.team} (${owner.role})`)
                          .join(", ")
                      : "—"}
                  </MetaRow>
                  <MetaRow label="Catalog version">
                    <span className="font-mono text-xs">v{data.version}</span>
                  </MetaRow>
                </dl>
              </Card>

              {data.dependencies && data.dependencies.length > 0 ? (
                <Card title="Managed dependencies">
                  {/* Deliberately its OWN section, not the workload list. A
                      provider-managed platform has no Deployment, no replicas
                      and nothing to restart, and putting it among workloads
                      invites somebody to ask why it will not restart. */}
                  <ul className="divide-y divide-border" data-testid="dependency-list">
                    {data.dependencies.map((dependency) => (
                      <li key={dependency.id} className="px-1 py-2.5">
                        <span className="block text-sm font-medium text-ink">
                          {dependency.display_name}
                        </span>
                        <dl className="mt-1">
                          <MetaRow label="Class">
                            <span className="font-mono text-xs">
                              {dependency.dependency_class}
                            </span>
                          </MetaRow>
                          <MetaRow label="Provider">
                            <span className="font-mono text-xs">{dependency.provider}</span>
                          </MetaRow>
                          <MetaRow label="Verification">
                            {/* Never styled as a verified/healthy state:
                                repository intent is evidence about source
                                code, not about a running system. */}
                            <span className="font-mono text-xs">
                              {dependency.verification}
                            </span>
                          </MetaRow>
                          <MetaRow label="Workload">
                            <span className="text-xs italic text-ink-muted">
                              {dependency.workload_applicability === "not_applicable"
                                ? "Not applicable"
                                : dependency.workload_applicability}
                            </span>
                          </MetaRow>
                          <MetaRow label="Health">
                            <span className="font-mono text-xs">
                              {dependency.health.status}
                            </span>
                          </MetaRow>
                          <MetaRow label="Freshness">
                            <span className="font-mono text-xs">
                              {dependency.health.freshness}
                            </span>
                          </MetaRow>
                        </dl>
                      </li>
                    ))}
                  </ul>
                </Card>
              ) : null}

              <Card title="Environments">
                <LoadGate value={environments} retry={retryEnvironments}>
                  {(body) =>
                    body.environments.length === 0 ? (
                      <DataState
                        kind="empty"
                        title="No environments in your scope"
                      />
                    ) : (
                      <ul className="divide-y divide-border" data-testid="environment-list">
                        {body.environments.map((environment) => (
                          <li key={environment.id}>
                            <Link
                              href={`/projects/${projectId}/environments/${environment.id}`}
                              className="flex items-center justify-between gap-3 px-1 py-2.5 hover:bg-surface-sunken"
                            >
                              <span>
                                <span className="block text-sm font-medium text-ink">
                                  {environment.environment_key}
                                </span>
                                <span className="block text-xs text-ink-muted">
                                  {environment.runtime}
                                  {environment.cluster
                                    ? ` · ${environment.cluster.ref}/${environment.namespace}`
                                    : ""}
                                </span>
                              </span>
                              <CriticalityBadge criticality={environment.criticality} />
                            </Link>
                          </li>
                        ))}
                      </ul>
                    )
                  }
                </LoadGate>
              </Card>
            </div>

            <LoadGate value={environments} retry={retryEnvironments}>
              {(body) => (
                <Suspense fallback={null}>
                  <ProjectMetricsSection environments={body.environments} />
                </Suspense>
              )}
            </LoadGate>

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
