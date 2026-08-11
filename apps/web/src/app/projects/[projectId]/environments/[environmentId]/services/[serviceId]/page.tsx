"use client";

/**
 * Service detail.
 *
 * The golden signals come first, because that is the question — is this
 * service well right now. Identity and binding configuration sit underneath:
 * they are what you read when the answer is "no" and you need to know what
 * Drake is even measuring.
 *
 * The dashboard's own section titles are the headings here; the page does not
 * print "Golden signals" above a dashboard whose first section is called
 * Golden signals.
 */

import { useParams, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { PageFrame, PageHeader } from "@/components/shell/AppShell";
import { DashboardRenderer } from "@/components/telemetry/DashboardRenderer";
import { Panel, PanelHeader, SectionHeader } from "@/components/ui/Panel";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { InlineCode, RelativeTime } from "@/components/ui/identifiers";
import {
  DeniedState,
  ErrorState,
  LoadingSkeleton,
  NotConfiguredState,
  NotFoundState,
} from "@/components/ui/states";
import type { ServiceDetail } from "@/lib/catalog";
import { useCrumbLabel } from "@/lib/crumbs";
import { humanize, toneForHealth } from "@/lib/design/status";
import { parseRangePreset } from "@/lib/telemetry";
import { useResource } from "@/lib/useResource";

const CAPABILITY_LABELS: Record<string, string> = {
  metrics: "Golden signals",
  logs: "Logs",
  traces: "Traces",
  deployments: "Deploy history",
};

function MetaRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-border py-2 last:border-b-0">
      <dt className="text-caption text-ink-muted">{label}</dt>
      <dd className="text-body text-ink">{children}</dd>
    </div>
  );
}

function ServiceDetailInner() {
  const { projectId, environmentId, serviceId } = useParams<{
    projectId: string;
    environmentId: string;
    serviceId: string;
  }>();
  const resource = useResource<ServiceDetail>(
    `/v1/projects/${projectId}/environments/${environmentId}/services/${serviceId}`,
  );
  const preset = parseRangePreset(useSearchParams().get("range"));
  // `scope.ref` is "project/environment" — the two ancestors in the trail.
  const [projectKey, environmentKey] = (resource.data?.scope.ref ?? "").split("/");
  useCrumbLabel(projectId, projectKey);
  useCrumbLabel(environmentId, environmentKey);
  useCrumbLabel(serviceId, resource.data?.service_key);

  if (resource.loading && !resource.data) {
    return (
      <PageFrame width="wide">
        <LoadingSkeleton rows={4} label="Loading service" />
      </PageFrame>
    );
  }
  if (resource.notFound) {
    return (
      <PageFrame width="wide">
        <NotFoundState description="This service does not exist in your authorized scope." />
      </PageFrame>
    );
  }
  if (resource.denied) {
    return (
      <PageFrame width="wide">
        <DeniedState />
      </PageFrame>
    );
  }
  if (!resource.data) {
    return (
      <PageFrame width="wide">
        <ErrorState
          description={resource.error ?? undefined}
          correlationId={resource.correlationId}
          onRetry={resource.reload}
        />
      </PageFrame>
    );
  }

  const service = resource.data;
  const selector = Object.entries(service.workload_selector);
  const probes = Object.entries(service.health);

  return (
    <PageFrame width="wide">
      <PageHeader
        title={service.display_name || service.service_key}
        status={
          <StatusBadge
            status={service.lifecycle === "active" ? "success" : "neutral"}
            label={humanize(service.lifecycle)}
          />
        }
        meta={
          <>
            <span className="font-mono">{service.scope.ref}</span>
            <span>
              component <InlineCode>{service.component}</InlineCode>
            </span>
            <span>
              profile <InlineCode>{service.metrics_profile}</InlineCode>
            </span>
            <span>
              catalog record accepted <RelativeTime value={service.source.accepted_at} />
            </span>
          </>
        }
      />

      <Suspense fallback={<LoadingSkeleton variant="chart" label="Loading signals" />}>
        <DashboardRenderer
          templateKey="service-golden-signals-v1"
          scopeType="service"
          scopeId={serviceId}
          preset={preset}
          profile={service.metrics_profile}
        />
      </Suspense>

      <div className="mt-6">
        <SectionHeader
          title="What Drake measures here"
          description="The binding and the capabilities behind the signals above."
        />
        <div className="mt-3 grid grid-cols-1 gap-4 lg:grid-cols-3">
          <Panel>
            <PanelHeader title="Workload binding" level={3} />
            <dl>
              <MetaRow label="Selector">
                {selector.length > 0 ? (
                  <span className="font-mono text-micro">
                    {selector.map(([key, value]) => `${key}=${value}`).join(", ")}
                  </span>
                ) : (
                  <span className="text-caption text-ink-muted italic">not configured</span>
                )}
              </MetaRow>
              <MetaRow label="Health paths">
                {probes.length > 0 ? (
                  <span className="font-mono text-micro">
                    {probes.map(([key, value]) => `${key}: ${value}`).join(" · ")}
                  </span>
                ) : (
                  <span className="text-caption text-ink-muted italic">not configured</span>
                )}
              </MetaRow>
              <MetaRow label="Runtime">
                <InlineCode>{service.runtime}</InlineCode>
              </MetaRow>
              <MetaRow label="Catalog version">
                <span data-tabular>v{service.version}</span>
              </MetaRow>
            </dl>
          </Panel>

          <Panel className="lg:col-span-2">
            <PanelHeader
              title="Capabilities"
              description="Each one reports the state its own source is in — an unconfigured capability is an absence, not a failure."
              level={3}
            />
            <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {Object.entries(CAPABILITY_LABELS).map(([key, label]) => {
                const state = service.operational?.[key] ?? "unknown";
                return (
                  <li
                    key={key}
                    className="flex items-center justify-between gap-3 rounded-control border border-border px-3 py-2"
                  >
                    <span className="text-body text-ink">{label}</span>
                    <StatusBadge
                      status={toneForHealth(state === "ok" ? "healthy" : state)}
                      label={humanize(state)}
                      size="compact"
                    />
                  </li>
                );
              })}
            </ul>
            {Object.values(service.operational ?? {}).every(
              (state) => state === "not_configured",
            ) ? (
              <NotConfiguredState
                compact
                title="No capability is connected yet"
                description="Signals appear on this page as their sources are configured for this environment."
              />
            ) : null}
          </Panel>
        </div>
      </div>

      <p className="mt-4 text-micro text-ink-muted">
        Catalog source <InlineCode>{service.source.kind}:{service.source.ref}</InlineCode> ·
        revision <InlineCode>{service.source.revision}</InlineCode> · accepted{" "}
        <RelativeTime value={service.source.accepted_at} />
      </p>
    </PageFrame>
  );
}

export default function ServiceDetailPage() {
  return (
    <Suspense
      fallback={
        <PageFrame width="wide">
          <LoadingSkeleton rows={4} label="Loading service" />
        </PageFrame>
      }
    >
      <ServiceDetailInner />
    </Suspense>
  );
}
