"use client";

/**
 * Environment detail.
 *
 * A quieter sibling of the project and service pages: an environment has no
 * verdict of its own to lead with (a Kubernetes environment's health lives on
 * the service-health path; an external one gets a health badge here only
 * because it has nowhere else to report it). So the lead is identity —
 * runtime, branch, cluster/namespace — and the page's real job is the list of
 * services bound underneath it, which is where an operator goes next.
 *
 * Same rule as its siblings: a runtime concept that does not exist for this
 * environment (a namespace on an external app) renders "Not applicable", and
 * that is a different claim from "Not recorded", which means Kubernetes has
 * one and nobody captured it.
 */

import Link from "next/link";
import { useParams } from "next/navigation";

import { PageFrame, PageHeader } from "@/components/shell/AppShell";
import { Provenance } from "@/components/provenance/Provenance";
import { Panel, PanelBody, PanelFooter, PanelHeader } from "@/components/ui/Panel";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { InlineCode, RelativeTime } from "@/components/ui/identifiers";
import {
  DeniedState,
  EmptyState,
  ErrorState,
  LoadingSkeleton,
  NotFoundState,
} from "@/components/ui/states";
import type { Environment, ServiceSummary } from "@/lib/catalog";
import { useCrumbLabel } from "@/lib/crumbs";
import { humanize, toneForHealth, type StatusTone } from "@/lib/design/status";
import { useResource } from "@/lib/useResource";

const CRITICALITY_TONE: Record<string, StatusTone> = {
  critical: "critical",
  high: "warning",
  medium: "info",
  low: "neutral",
};

/** Same four capabilities the project page reports, scoped to this
 * environment. A capability that is not configured is an absence, not a
 * fault, and never renders as anything healthier than what was observed. */
const CAPABILITY_LABELS: Record<string, string> = {
  workloads: "Workloads & pods",
  targets: "Scrape targets",
  quotas: "Resource quotas",
  drift: "Config drift",
};

function MetaRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-border py-2 last:border-b-0">
      <dt className="text-caption text-ink-muted">{label}</dt>
      <dd className="text-body text-ink">{children}</dd>
    </div>
  );
}

export default function EnvironmentDetailPage() {
  const { projectId, environmentId } = useParams<{
    projectId: string;
    environmentId: string;
  }>();
  const environment = useResource<Environment>(
    `/v1/projects/${projectId}/environments/${environmentId}`,
  );
  const services = useResource<{ services: ServiceSummary[]; next_cursor: string | null }>(
    `/v1/projects/${projectId}/environments/${environmentId}/services`,
  );

  // `scope.ref` is "project_key/environment_key" — the ancestor the shell
  // breadcrumb needs a name for.
  const projectKey = environment.data?.scope.ref.split("/")[0];
  useCrumbLabel(projectId, projectKey);
  useCrumbLabel(environmentId, environment.data?.environment_key);

  if (environment.loading && !environment.data) {
    return (
      <PageFrame width="wide">
        <LoadingSkeleton rows={4} label="Loading environment" />
      </PageFrame>
    );
  }
  if (environment.notFound) {
    return (
      <PageFrame width="wide">
        <NotFoundState description="This environment does not exist in your authorized scope." />
      </PageFrame>
    );
  }
  if (environment.denied) {
    return (
      <PageFrame width="wide">
        <DeniedState />
      </PageFrame>
    );
  }
  if (!environment.data) {
    return (
      <PageFrame width="wide">
        <ErrorState
          description={environment.error ?? undefined}
          correlationId={environment.correlationId}
          onRetry={environment.reload}
        />
      </PageFrame>
    );
  }

  const data = environment.data;
  const serviceList = services.data?.services ?? [];
  const isExternal = data.runtime === "external";

  return (
    <PageFrame width="wide">
      <PageHeader
        title={data.environment_key}
        status={
          <>
            {data.health ? (
              <>
                <span className="text-caption text-ink-muted">Measured health</span>
                <StatusBadge
                  status={toneForHealth(data.health.status)}
                  label={humanize(data.health.status)}
                />
              </>
            ) : null}
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
            <span>
              runtime <InlineCode>{data.runtime}</InlineCode>
            </span>
            <span>
              branch <InlineCode>{data.branch || "—"}</InlineCode>
            </span>
            <span>
              <InlineCode>v{data.version}</InlineCode> catalog version
            </span>
            <span>
              catalog record accepted <RelativeTime value={data.source.accepted_at} />
            </span>
          </>
        }
      />

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,22rem)]">
        <Panel
          flush
          data-testid="services-panel"
          className="motion-safe:animate-[fade-in_360ms_var(--ease-entrance)_backwards]"
        >
          <PanelHeader
            flush
            title="Services"
            description="Every service bound to this environment."
            meta={
              services.data ? (
                <span>
                  {serviceList.length} service{serviceList.length === 1 ? "" : "s"}
                </span>
              ) : undefined
            }
          />
          {services.loading && !services.data ? (
            <div className="px-7 py-5">
              <LoadingSkeleton variant="table" rows={3} />
            </div>
          ) : services.denied ? (
            <div className="px-7 py-5">
              <DeniedState compact />
            </div>
          ) : !services.data ? (
            <div className="px-7 py-5">
              <ErrorState
                compact
                description={services.error ?? undefined}
                correlationId={services.correlationId}
                onRetry={services.reload}
              />
            </div>
          ) : serviceList.length === 0 ? (
            <div className="px-7 py-5">
              <EmptyState compact title="No service bindings" />
            </div>
          ) : (
            <ul className="divide-y divide-border" data-testid="service-list">
              {serviceList.map((service) => (
                <li key={service.id}>
                  <Link
                    href={`/projects/${projectId}/environments/${environmentId}/services/${service.id}`}
                    className="flex items-center justify-between gap-3 px-7 py-4 transition-colors hover:bg-surface-hover"
                  >
                    <span className="flex min-w-0 items-center gap-3">
                      <span
                        aria-hidden
                        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-surface-2 text-caption font-semibold text-ink-secondary"
                      >
                        {(service.display_name || service.service_key).slice(0, 1).toUpperCase()}
                      </span>
                      <span className="min-w-0">
                        <span className="block truncate text-body font-medium text-ink">
                          {service.display_name || service.service_key}
                        </span>
                        <span className="block truncate font-mono text-micro text-ink-muted">
                          {service.component ?? "—"} · {service.runtime}
                        </span>
                      </span>
                    </span>
                    <span className="shrink-0 font-mono text-micro text-ink-muted">
                      {service.metrics_profile} · v{service.version}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <div className="flex flex-col gap-6">
          <Panel
            data-testid="operational-grid"
            className="motion-safe:animate-[fade-in_400ms_var(--ease-entrance)_backwards] [animation-delay:60ms]"
          >
            <PanelHeader
              title="Capabilities"
              description="What Drake can currently observe for this environment."
              level={3}
            />
            <ul className="grid grid-cols-2 gap-2">
              {Object.entries(CAPABILITY_LABELS).map(([key, label]) => {
                const state = data.operational?.[key] ?? "unknown";
                return (
                  <li
                    key={key}
                    className="flex h-full flex-col gap-1.5 rounded-control border border-border px-3 py-2"
                  >
                    <span className="text-caption text-ink-secondary">{label}</span>
                    <StatusBadge
                      status={toneForHealth(state === "ok" ? "healthy" : state)}
                      label={humanize(state)}
                      size="compact"
                    />
                  </li>
                );
              })}
            </ul>
          </Panel>

          <Panel
            flush
            className="motion-safe:animate-[fade-in_400ms_var(--ease-entrance)_backwards] [animation-delay:120ms]"
          >
            <PanelHeader flush title="Environment metadata" level={3} />
            <PanelBody>
              <dl>
                <MetaRow label="Runtime">
                  <InlineCode>{data.runtime}</InlineCode>
                </MetaRow>
                <MetaRow label="Branch">
                  <InlineCode>{data.branch || "—"}</InlineCode>
                </MetaRow>
                <MetaRow label="Cluster / namespace">
                  {data.cluster ? (
                    <InlineCode>{`${data.cluster.ref} / ${data.namespace}`}</InlineCode>
                  ) : data.not_applicable?.includes("cluster") ? (
                    // The runtime has no such concept. This used to render for
                    // ANY environment without a cluster, so a Kubernetes
                    // environment that had genuinely lost its cluster was
                    // described as "external runtime" and looked fine.
                    <span className="text-caption text-ink-muted italic">Not applicable</span>
                  ) : (
                    <span className="text-caption text-ink-muted italic">Not recorded</span>
                  )}
                </MetaRow>
                {isExternal ? (
                  <>
                    <MetaRow label="Hosting provider">
                      <InlineCode>{data.hosting_provider ?? "unknown"}</InlineCode>
                    </MetaRow>
                    <MetaRow label="Agent">
                      <span className="text-caption text-ink-muted italic">Not applicable</span>
                    </MetaRow>
                    <MetaRow label="Health source">
                      <InlineCode>{data.health?.source.status ?? "not_configured"}</InlineCode>
                    </MetaRow>
                    <MetaRow label="Freshness">
                      <InlineCode>{data.health?.freshness ?? "unavailable"}</InlineCode>
                    </MetaRow>
                    {data.health?.last_observed_at ? (
                      <MetaRow label="Last observed">
                        <RelativeTime value={data.health.last_observed_at} />
                      </MetaRow>
                    ) : null}
                  </>
                ) : null}
                <MetaRow label="Catalog version">
                  <span data-tabular>v{data.version}</span>
                </MetaRow>
              </dl>
            </PanelBody>
            <PanelFooter>
              <Provenance
                source={`${data.source.kind}:${data.source.ref || "-"}`}
                asOf={data.as_of}
                freshness="catalog"
                measurementMethod="catalog_record"
                confidence="exact"
              />
            </PanelFooter>
          </Panel>
        </div>
      </div>
    </PageFrame>
  );
}
