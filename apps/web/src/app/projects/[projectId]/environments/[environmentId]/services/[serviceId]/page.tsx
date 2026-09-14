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

import {
  Activity,
  FileText,
  GitCommitHorizontal,
  HeartPulse,
  Network,
  Rocket,
  ScanSearch,
  ShieldCheck,
  Waypoints,
} from "lucide-react";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import {
  CapabilityTile,
  DefinitionGrid,
  FactPill,
  StatTile,
  TileState,
  capabilityTone,
} from "@/components/catalog/visuals";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";
import { DashboardRenderer } from "@/components/telemetry/DashboardRenderer";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { InlineCode, RelativeTime } from "@/components/ui/identifiers";
import {
  DeniedState,
  ErrorState,
  LoadingSkeleton,
  NotFoundState,
} from "@/components/ui/states";
import type { ServiceDetail } from "@/lib/catalog";
import { useCrumbLabel } from "@/lib/crumbs";
import { humanize, toneSpec } from "@/lib/design/status";
import { parseRangePreset } from "@/lib/telemetry";
import { useResource } from "@/lib/useResource";

const CAPABILITY_LABELS: Record<string, string> = {
  metrics: "Golden signals",
  logs: "Logs",
  traces: "Traces",
  deployments: "Deploy history",
};

const CAPABILITY_ICONS: Record<string, typeof Activity> = {
  metrics: Activity,
  logs: FileText,
  traces: Waypoints,
  deployments: Rocket,
};

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
  const [projectKey, environmentKey] = (resource.data?.scope.ref ?? "").split(
    "/",
  );
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

  const capabilityEntries = Object.entries(CAPABILITY_LABELS).map(
    ([key, label]) => ({
      key,
      label,
      state: service.operational?.[key] ?? "unknown",
    }),
  );
  const connected = capabilityEntries.filter(
    (entry) => entry.state === "ok",
  ).length;
  const noneConnected = Object.values(service.operational ?? {}).every(
    (state) => state === "not_configured",
  );

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
              catalog record accepted{" "}
              <RelativeTime value={service.source.accepted_at} />
            </span>
          </>
        }
      />

      <div className="flex flex-col gap-10">
        <div className="page-grid motion-safe:animate-[fade-in_320ms_var(--ease-entrance)_backwards]">
          <StatTile
            icon={ShieldCheck}
            label="Capabilities"
            value={connected}
            suffix={`of ${capabilityEntries.length} reporting`}
          >
            <ul
              className="grid grid-cols-4 gap-1.5"
              aria-label="Capability states"
            >
              {capabilityEntries.map((entry) => (
                <li
                  key={entry.key}
                  title={`${entry.label}: ${entry.state === "ok" ? "Reporting" : humanize(entry.state)}`}
                  className={`h-2 rounded-full ${
                    entry.state === "ok"
                      ? toneSpec(capabilityTone(entry.state)).dot
                      : "bg-surface-3"
                  }`}
                />
              ))}
            </ul>
          </StatTile>
          <StatTile
            icon={ScanSearch}
            label="Workload selector"
            value={selector.length}
            suffix={selector.length === 1 ? "label" : "labels"}
          >
            {selector.length > 0 ? (
              <div className="flex flex-wrap gap-1.5">
                {selector.map(([key]) => (
                  <FactPill key={key} mono>
                    {key}
                  </FactPill>
                ))}
              </div>
            ) : (
              <p className="text-micro text-ink-muted">Not configured</p>
            )}
          </StatTile>
          <StatTile
            icon={HeartPulse}
            label="Health paths"
            value={probes.length}
            suffix={probes.length === 1 ? "probe" : "probes"}
          >
            {probes.length > 0 ? (
              <div className="flex flex-wrap gap-1.5">
                {probes.map(([key]) => (
                  <FactPill key={key} mono>
                    {key}
                  </FactPill>
                ))}
              </div>
            ) : (
              <p className="text-micro text-ink-muted">Not configured</p>
            )}
          </StatTile>
          <StatTile
            icon={GitCommitHorizontal}
            label="Catalog record"
            value={`v${service.version}`}
          >
            <div className="flex flex-wrap gap-1.5">
              <FactPill mono>{service.runtime}</FactPill>
              <FactPill mono>{service.metrics_profile}</FactPill>
            </div>
          </StatTile>
        </div>

        <Suspense
          fallback={<LoadingSkeleton variant="chart" label="Loading signals" />}
        >
          <DashboardRenderer
            templateKey="service-golden-signals-v1"
            scopeType="service"
            scopeId={serviceId}
            preset={preset}
            profile={service.metrics_profile}
          />
        </Suspense>

        <section aria-label="What Drake measures here">
          <div className="mb-5">
            <h2 className="text-[1.25rem] leading-7 font-semibold tracking-[-0.015em] text-ink">
              What Drake measures here
            </h2>
            <p className="mt-0.5 text-caption text-ink-muted">
              The binding and the capabilities behind the signals above.
            </p>
          </div>
          <div className="grid grid-cols-1 items-stretch gap-6 xl:grid-cols-2">
            <Panel>
              <PanelHeader title="Workload binding" level={3} />
              <DefinitionGrid
                items={[
                  {
                    label: "Selector",
                    wide: true,
                    value:
                      selector.length > 0 ? (
                        <span className="font-mono text-caption">
                          {selector
                            .map(([key, value]) => `${key}=${value}`)
                            .join(", ")}
                        </span>
                      ) : (
                        <span className="text-caption text-ink-muted italic">
                          not configured
                        </span>
                      ),
                  },
                  {
                    label: "Health paths",
                    wide: true,
                    value:
                      probes.length > 0 ? (
                        <span className="font-mono text-caption">
                          {probes
                            .map(([key, value]) => `${key}: ${value}`)
                            .join(" · ")}
                        </span>
                      ) : (
                        <span className="text-caption text-ink-muted italic">
                          not configured
                        </span>
                      ),
                  },
                  {
                    label: "Runtime",
                    value: <InlineCode>{service.runtime}</InlineCode>,
                  },
                  {
                    label: "Catalog version",
                    value: <span data-tabular>v{service.version}</span>,
                  },
                ]}
              />
            </Panel>

            <Panel>
              <PanelHeader
                title="Capabilities"
                description="An unconfigured capability is an absence, not a failure."
                level={3}
              />
              <ul
                data-testid="operational-grid"
                className="grid grid-cols-1 gap-3 sm:grid-cols-2"
              >
                {capabilityEntries.map(({ key, label, state }) => (
                  <li key={key} className="min-w-0">
                    <CapabilityTile
                      icon={CAPABILITY_ICONS[key] ?? Activity}
                      label={label}
                      state={state}
                    />
                  </li>
                ))}
              </ul>
              {noneConnected ? (
                <TileState
                  icon={Network}
                  testId="state-not-configured"
                  title="No capability is connected yet"
                  description="Signals appear here as their sources are configured for this environment."
                />
              ) : null}
            </Panel>
          </div>
        </section>

        <div className="flex flex-wrap items-center gap-2 text-micro text-ink-muted">
          <span className="font-medium tracking-[0.08em] uppercase">
            Catalog source
          </span>
          <FactPill
            mono
          >{`${service.source.kind}:${service.source.ref}`}</FactPill>
          <FactPill mono>{`revision ${service.source.revision}`}</FactPill>
          <span>
            accepted <RelativeTime value={service.source.accepted_at} />
          </span>
        </div>
      </div>
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
