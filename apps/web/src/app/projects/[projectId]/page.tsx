"use client";

/**
 * Project detail.
 *
 * Order follows what an operator wants: what is this and is it well
 * (identity + capabilities), then the live signals, then the standing
 * inventory of environments and dependencies.
 *
 * Two distinctions this page exists to keep:
 *
 *   Managed dependencies are not workloads. A provider-run database has no
 *   Deployment, no replicas and nothing to restart, and listing it among
 *   workloads invites somebody to ask why it will not roll. Its
 *   `workload_applicability: not_applicable` renders as "not applicable" —
 *   which is a different answer from "unknown".
 *
 *   Verification is not health. `repository_intent` means somebody declared a
 *   dependency in source; it is evidence about a repository, not about a
 *   running system, and it never renders in the healthy colour.
 */

import Link from "next/link";
import { useParams } from "next/navigation";
import { Suspense } from "react";

import { PageFrame, PageHeader } from "@/components/shell/AppShell";
import { ProjectMetricsSection } from "@/components/telemetry/ProjectMetricsSection";
import { Panel, PanelHeader, SectionHeader } from "@/components/ui/Panel";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { InlineCode, RelativeTime } from "@/components/ui/identifiers";
import {
  DeniedState,
  EmptyState,
  ErrorState,
  LoadingSkeleton,
  NotFoundState,
} from "@/components/ui/states";
import type { Environment, Project, ProjectDependency } from "@/lib/catalog";
import { useCrumbLabel } from "@/lib/crumbs";
import { humanize, toneForHealth, type StatusTone } from "@/lib/design/status";
import { useResource } from "@/lib/useResource";

const CAPABILITY_LABELS: Record<string, string> = {
  telemetry: "Telemetry",
  inventory: "Cluster inventory",
  deployment: "Deployments",
  protection: "Backup & restore",
};

/**
 * Where each capability's evidence actually lives.
 *
 * Telemetry stays on this page — the signals it feeds are rendered further
 * down — so it anchors rather than navigating away from what it describes.
 */
const CAPABILITY_HREF: Record<string, (projectId: string) => string> = {
  telemetry: () => "#signals",
  inventory: () => "/clusters",
  deployment: () => "/deployments",
  protection: () => "/protection",
};

const CRITICALITY_TONE: Record<string, StatusTone> = {
  critical: "critical",
  high: "warning",
  medium: "info",
  low: "neutral",
};

/**
 * How a dependency's evidence was obtained.
 *
 * Deliberately never a health tone: `provider_observed` is the strongest of
 * the three and still only means "the provider told us something", which is
 * not the same claim as "this is working".
 */
const VERIFICATION_LABELS: Record<string, string> = {
  repository_intent: "declared in repository",
  owner_confirmed: "confirmed by owner",
  provider_observed: "observed from provider",
};

function MetaRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-border py-2 last:border-b-0">
      <dt className="text-caption text-ink-muted">{label}</dt>
      <dd className="text-body text-ink">{children}</dd>
    </div>
  );
}

function DependencyRow({ dependency }: { dependency: ProjectDependency }) {
  const notApplicable = dependency.workload_applicability === "not_applicable";
  return (
    <li className="px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-body font-medium text-ink">{dependency.display_name}</span>
        <span className="flex items-center gap-1.5">
          {dependency.health ? (
            <StatusBadge
              status={toneForHealth(dependency.health.status)}
              label={dependency.health.status}
              size="compact"
            />
          ) : null}
          {dependency.health ? (
            <StatusBadge
              status={
                dependency.health.freshness === "fresh"
                  ? "neutral"
                  : dependency.health.freshness === "stale"
                    ? "stale"
                    : "unknown"
              }
              label={dependency.health.freshness}
              size="compact"
            />
          ) : null}
        </span>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-micro text-ink-muted">
        <span>
          <InlineCode>{dependency.provider}</InlineCode>{" "}
          <InlineCode>{dependency.dependency_class}</InlineCode>
        </span>
        <span>
          <InlineCode>{dependency.verification}</InlineCode>{" "}
          {VERIFICATION_LABELS[dependency.verification] ?? ""}
        </span>
        <span className={notApplicable ? "" : "text-ink-secondary"}>
          Workload: {notApplicable ? "Not applicable" : dependency.workload_applicability}
        </span>
        {dependency.health?.last_observed_at ? (
          <span>
            observed <RelativeTime value={dependency.health.last_observed_at} />
          </span>
        ) : null}
      </div>
    </li>
  );
}

export default function ProjectOverviewPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const project = useResource<Project>(`/v1/projects/${projectId}`);
  const environments = useResource<{ environments: Environment[]; next_cursor: string | null }>(
    `/v1/projects/${projectId}/environments`,
  );
  useCrumbLabel(projectId, project.data?.project_key);

  if (project.loading && !project.data) {
    return (
      <PageFrame width="wide">
        <LoadingSkeleton rows={4} label="Loading project" />
      </PageFrame>
    );
  }
  if (project.notFound) {
    return (
      <PageFrame width="wide">
        <NotFoundState description="This project does not exist in your authorized scope." />
      </PageFrame>
    );
  }
  if (project.denied) {
    return (
      <PageFrame width="wide">
        <DeniedState />
      </PageFrame>
    );
  }
  if (!project.data) {
    return (
      <PageFrame width="wide">
        <ErrorState
          description={project.error ?? undefined}
          correlationId={project.correlationId}
          onRetry={project.reload}
        />
      </PageFrame>
    );
  }

  const data = project.data;
  const managed = data.dependencies?.filter((d) => d.dependency_class !== "in_cluster") ?? [];
  const inCluster = data.dependencies?.filter((d) => d.dependency_class === "in_cluster") ?? [];

  return (
    <PageFrame width="wide">
      <PageHeader
        title={data.display_name}
        status={
          <>
            <StatusBadge
              status={CRITICALITY_TONE[data.criticality] ?? "neutral"}
              label={`${humanize(data.criticality)} criticality`}
            />
            <StatusBadge
              status={data.lifecycle === "active" ? "success" : "neutral"}
              label={humanize(data.lifecycle)}
            />
          </>
        }
        meta={
          <>
            <span className="font-mono">{data.project_key}</span>
            <span>
              {/* Provenance: present on every project, quiet by design. It is
                  how you verify a project is what it claims, not a headline. */}
              <InlineCode>
                {`${data.repository.provider}:${data.repository.owner}/${data.repository.name}${
                  data.repository.default_branch ? ` @ ${data.repository.default_branch}` : ""
                }`}
              </InlineCode>
            </span>
            <span>
              {data.counts.environments} environments · {data.counts.services} services
            </span>
            <span>
              catalog record accepted <RelativeTime value={data.source.accepted_at} />
            </span>
          </>
        }
      />

      <Panel data-testid="operational-grid" className="mb-5">
        <PanelHeader
          title="Capabilities"
          description="What Drake can currently observe for this project. A capability that is not configured is an absence, not a fault."
          level={2}
        />
        <ul className="grid grid-cols-2 gap-2 lg:grid-cols-4">
          {Object.entries(CAPABILITY_LABELS).map(([key, label]) => {
            const state = data.operational?.[key] ?? "unknown";
            // A capability that reports something has somewhere to report it,
            // and saying "Ok" without a way through is a dead end. Absences
            // stay unlinked on purpose: there is nothing to go and look at.
            const href = state === "not_configured" ? null : CAPABILITY_HREF[key]?.(projectId);
            const body = (
              <>
                <span className="text-caption text-ink-secondary">{label}</span>
                <StatusBadge
                  status={toneForHealth(state === "ok" ? "healthy" : state)}
                  label={humanize(state)}
                  size="compact"
                />
              </>
            );
            return (
              <li key={key}>
                {href ? (
                  <Link
                    href={href}
                    className="flex h-full flex-col gap-1.5 rounded-control border border-border px-3 py-2 transition-colors hover:border-border-strong hover:bg-surface-raised focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                  >
                    {body}
                  </Link>
                ) : (
                  <div className="flex h-full flex-col gap-1.5 rounded-control border border-border px-3 py-2">
                    {body}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      </Panel>

      <Suspense fallback={<LoadingSkeleton variant="chart" label="Loading metrics" />}>
        {/* The Telemetry capability card links here, so the target has to exist. */}
        <div id="signals" className="scroll-mt-4">
          <ProjectMetricsSection environments={environments.data?.environments ?? []} />
        </div>
      </Suspense>

      <div className="mt-6">
        <SectionHeader
          title="Composition"
          description="The environments Drake runs for this project, and the dependencies it does not."
        />
        <div className="mt-3 grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Panel flush>
            <PanelHeader flush title="Environments" level={3} />
            {environments.loading && !environments.data ? (
              <div className="px-4 py-3">
                <LoadingSkeleton variant="table" rows={3} />
              </div>
            ) : environments.denied ? (
              <div className="px-4 py-2">
                <DeniedState compact />
              </div>
            ) : !environments.data ? (
              <div className="px-4 py-2">
                <ErrorState
                  compact
                  description={environments.error ?? undefined}
                  onRetry={environments.reload}
                />
              </div>
            ) : environments.data.environments.length === 0 ? (
              <div className="px-4 py-2">
                <EmptyState compact title="No environments in your scope" />
              </div>
            ) : (
              <ul className="divide-y divide-border" data-testid="environment-list">
                {environments.data.environments.map((environment) => (
                  <li key={environment.id}>
                    <Link
                      href={`/projects/${projectId}/environments/${environment.id}`}
                      className="flex items-center justify-between gap-3 px-4 py-2.5 transition-colors hover:bg-surface-hover"
                    >
                      <span className="min-w-0">
                        <span className="block text-body font-medium text-ink">
                          {environment.environment_key}
                        </span>
                        <span className="block truncate font-mono text-micro text-ink-muted">
                          {environment.runtime}
                          {environment.cluster
                            ? ` · ${environment.cluster.ref}/${environment.namespace}`
                            : environment.hosting_provider
                              ? ` · ${environment.hosting_provider}`
                              : ""}
                        </span>
                      </span>
                      <StatusBadge
                        status={CRITICALITY_TONE[environment.criticality] ?? "neutral"}
                        label={humanize(environment.criticality)}
                        size="compact"
                      />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          <div className="flex flex-col gap-4">
            {managed.length > 0 ? (
              <Panel flush>
                <PanelHeader
                  flush
                  title="Managed dependencies"
                  description="Run by a provider, not by Drake, so there is no in-cluster workload behind them."
                  level={3}
                />
                <ul className="divide-y divide-border" data-testid="dependency-list">
                  {managed.map((dependency) => (
                    <DependencyRow key={dependency.id} dependency={dependency} />
                  ))}
                </ul>
              </Panel>
            ) : null}

            {inCluster.length > 0 ? (
              <Panel flush>
                <PanelHeader
                  flush
                  title="In-cluster datastores"
                  description="Drake runs these, so they keep workload semantics and their health comes from the workload path."
                  level={3}
                />
                <ul className="divide-y divide-border" data-testid="in-cluster-dependency-list">
                  {inCluster.map((dependency) => (
                    <li key={dependency.id} className="px-4 py-3">
                      <span className="block text-body font-medium text-ink">
                        {dependency.display_name}
                      </span>
                      <span className="mt-0.5 block text-micro text-ink-muted">
                        <InlineCode>{dependency.engine}</InlineCode> · scope{" "}
                        <InlineCode>{dependency.scope}</InlineCode>
                      </span>
                    </li>
                  ))}
                </ul>
              </Panel>
            ) : null}

            <Panel>
              <PanelHeader title="Catalog record" level={3} />
              <dl>
                <MetaRow label="Tenant model">
                  <InlineCode>{data.tenant_model}</InlineCode>
                </MetaRow>
                <MetaRow label="Owners">
                  {data.owners && data.owners.length > 0
                    ? data.owners.map((owner) => `${owner.team} (${owner.role})`).join(", ")
                    : "—"}
                </MetaRow>
                <MetaRow label="Source">
                  <InlineCode>
                    {data.source.kind}:{data.source.ref}
                  </InlineCode>
                </MetaRow>
                <MetaRow label="Revision">
                  <InlineCode>{data.source.revision}</InlineCode>
                </MetaRow>
                <MetaRow label="Catalog version">
                  <span data-tabular>v{data.version}</span>
                </MetaRow>
              </dl>
            </Panel>
          </div>
        </div>
      </div>
    </PageFrame>
  );
}
