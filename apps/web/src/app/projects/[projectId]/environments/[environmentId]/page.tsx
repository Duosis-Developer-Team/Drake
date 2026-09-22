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

import {
  ArrowUpRight,
  Boxes,
  Box,
  Cpu,
  Gauge,
  GitCompare,
  Radar,
  ServerCog,
  ShieldCheck,
  Tag,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";

import {
  CapabilityTile,
  DefinitionGrid,
  FactPill,
  IconBubble,
  StatTile,
  TileState,
  capabilityTone,
  useCapabilityLabel,
  useWhen,
} from "@/components/catalog/visuals";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";
import { Provenance } from "@/components/provenance/Provenance";
import { Panel, PanelFooter, PanelHeader } from "@/components/ui/Panel";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { InlineCode, RelativeTime } from "@/components/ui/identifiers";
import {
  DeniedState,
  ErrorState,
  LoadingSkeleton,
  NotFoundState,
} from "@/components/ui/states";
import type { Environment, ServiceSummary } from "@/lib/catalog";
import { useCrumbLabel } from "@/lib/crumbs";
import {
  humanize,
  toneForHealth,
  toneSpec,
  type StatusTone,
} from "@/lib/design/status";
import { useT } from "@/lib/i18n";
import { useResource } from "@/lib/useResource";

const CRITICALITY_TONE: Record<string, StatusTone> = {
  critical: "critical",
  high: "warning",
  medium: "info",
  low: "neutral",
};

/** Same four capabilities the project page reports, scoped to this
 * environment; their names are `catalog.environment.capability.*`. A
 * capability that is not configured is an absence, not a fault, and never
 * renders as anything healthier than what was observed. */
const CAPABILITY_KEYS = ["workloads", "targets", "quotas", "drift"] as const;

const CAPABILITY_ICONS: Record<string, typeof Boxes> = {
  workloads: Boxes,
  targets: Radar,
  quotas: Gauge,
  drift: GitCompare,
};

export default function EnvironmentDetailPage() {
  const t = useT("catalog");
  const health = useT("serviceHealth");
  const capabilityLabel = useCapabilityLabel();
  const when = useWhen();
  const { projectId, environmentId } = useParams<{
    projectId: string;
    environmentId: string;
  }>();
  const environment = useResource<Environment>(
    `/v1/projects/${projectId}/environments/${environmentId}`,
  );
  const services = useResource<{
    services: ServiceSummary[];
    next_cursor: string | null;
  }>(`/v1/projects/${projectId}/environments/${environmentId}/services`);

  // `scope.ref` is "project_key/environment_key" — the ancestor the shell
  // breadcrumb needs a name for.
  const projectKey = environment.data?.scope.ref.split("/")[0];
  useCrumbLabel(projectId, projectKey);
  useCrumbLabel(environmentId, environment.data?.environment_key);

  if (environment.loading && !environment.data) {
    return (
      <PageFrame width="wide">
        <LoadingSkeleton rows={4} label={t("load.environment")} />
      </PageFrame>
    );
  }
  if (environment.notFound) {
    return (
      <PageFrame width="wide">
        <NotFoundState description={t("environment.notFound")} />
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

  const capabilityEntries = CAPABILITY_KEYS.map((key) => ({
    key,
    label: t(`environment.capability.${key}`),
    state: data.operational?.[key] ?? "unknown",
  }));
  const reporting = capabilityEntries.filter(
    (entry) => entry.state === "ok",
  ).length;
  const components = [
    ...new Set(serviceList.map((service) => service.component ?? "—")),
  ];

  return (
    <PageFrame width="wide">
      <PageHeader
        title={data.environment_key}
        status={
          <>
            {data.health ? (
              <>
                <span className="text-caption text-ink-muted">
                  {t("measuredHealth")}
                </span>
                <StatusBadge
                  status={toneForHealth(data.health.status)}
                  label={health.dyn("status", data.health.status, humanize(data.health.status))}
                />
              </>
            ) : null}
            <StatusBadge
              status={CRITICALITY_TONE[data.criticality] ?? "neutral"}
              label={t("criticality.badge", {
                level: t.dyn("criticality", data.criticality, humanize(data.criticality)),
              })}
            />
            <StatusBadge
              status={data.lifecycle === "active" ? "success" : "neutral"}
              label={t.dyn("lifecycle", data.lifecycle, humanize(data.lifecycle))}
            />
          </>
        }
        meta={
          <>
            <span>
              {t("environment.meta.runtime")} <InlineCode>{data.runtime}</InlineCode>
            </span>
            <span>
              {t("environment.meta.branch")} <InlineCode>{data.branch || "—"}</InlineCode>
            </span>
            <span title={data.source.accepted_at}>
              {t("record.accepted", { when: when(data.source.accepted_at) })}
            </span>
          </>
        }
      />

      <div className="flex flex-col gap-8">
        <div className="page-grid motion-safe:animate-[fade-in_320ms_var(--ease-entrance)_backwards]">
          <StatTile
            icon={Boxes}
            label={t("environment.kpi.services")}
            value={services.data ? serviceList.length : "—"}
            suffix={services.data ? t("environment.kpi.boundHere") : t("environment.kpi.couldNotLoad")}
          >
            {serviceList.length > 0 ? (
              <div className="flex flex-wrap gap-1.5">
                {components.map((component) => (
                  <FactPill key={component} icon={Tag}>
                    {`${component} · ${serviceList.filter((service) => (service.component ?? "—") === component).length}`}
                  </FactPill>
                ))}
              </div>
            ) : (
              <p className="text-micro text-ink-muted">
                {t("environment.kpi.noBindings")}
              </p>
            )}
          </StatTile>
          <StatTile
            icon={ShieldCheck}
            label={t("capability.title")}
            value={reporting}
            suffix={t("capability.reporting", { total: capabilityEntries.length })}
          >
            <ul
              className="grid grid-cols-4 gap-1.5"
              aria-label={t("capability.states")}
            >
              {capabilityEntries.map((entry) => (
                <li
                  key={entry.key}
                  title={`${entry.label}: ${capabilityLabel(entry.state)}`}
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
            icon={ServerCog}
            label={t("environment.kpi.runtime")}
            value={t.dyn("runtime", data.runtime, humanize(data.runtime))}
          >
            <div className="flex flex-wrap gap-1.5">
              <FactPill mono>{t("environment.kpi.branch", { branch: data.branch || "—" })}</FactPill>
              {data.cluster ? (
                <FactPill mono>
                  {data.cluster.display_name || data.cluster.ref}
                </FactPill>
              ) : null}
            </div>
          </StatTile>
          <StatTile
            icon={Cpu}
            label={t("record.title")}
            value={`v${data.version}`}
          >
            <p className="text-micro text-ink-muted" title={data.source.accepted_at}>
              {t("record.acceptedShort", { when: when(data.source.accepted_at) })}
            </p>
          </StatTile>
        </div>

        <div className="page-split">
          <div className="page-main">
            <Panel
              flush
              data-testid="services-panel"
              className="motion-safe:animate-[fade-in_360ms_var(--ease-entrance)_backwards]"
            >
              <PanelHeader
                flush
                title={t("environment.services.title")}
                description={t("environment.services.description")}
                meta={
                  services.data ? (
                    <span>{t("environment.services.count", { count: serviceList.length })}</span>
                  ) : undefined
                }
              />
              {services.loading && !services.data ? (
                <div className="px-7 py-6">
                  <LoadingSkeleton variant="table" rows={3} />
                </div>
              ) : services.denied ? (
                <div className="px-7 py-6">
                  <DeniedState compact />
                </div>
              ) : !services.data ? (
                <div className="px-7 py-6">
                  <ErrorState
                    compact
                    description={services.error ?? undefined}
                    correlationId={services.correlationId}
                    onRetry={services.reload}
                  />
                </div>
              ) : serviceList.length === 0 ? (
                <div className="px-7 py-8">
                  <TileState
                    icon={Boxes}
                    testId="state-empty"
                    title={t("environment.services.emptyTitle")}
                    description={t("environment.services.emptyBody")}
                  />
                </div>
              ) : (
                <ul
                  className="grid grid-cols-1 gap-4 p-7 sm:grid-cols-2 2xl:grid-cols-3"
                  data-testid="service-list"
                >
                  {serviceList.map((service) => (
                    <li key={service.id} className="min-w-0">
                      <Link
                        href={`/projects/${projectId}/environments/${environmentId}/services/${service.id}`}
                        className="group flex h-full min-w-0 flex-col gap-4 rounded-[1.25rem] border border-border bg-surface-2/40 p-5 transition-colors hover:border-border-strong hover:bg-surface-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                      >
                        <span className="flex min-w-0 items-start gap-3">
                          <IconBubble icon={Box} />
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-body font-semibold text-ink">
                              {service.display_name || service.service_key}
                            </span>
                            <span className="block truncate font-mono text-micro text-ink-muted">
                              {service.component ?? "—"} · {service.runtime}
                            </span>
                          </span>
                          <ArrowUpRight
                            aria-hidden
                            className="h-4 w-4 shrink-0 text-ink-muted transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5"
                          />
                        </span>
                        <span className="mt-auto flex flex-wrap items-center gap-1.5">
                          <FactPill mono>{service.metrics_profile}</FactPill>
                          <FactPill mono>{`v${service.version}`}</FactPill>
                          <StatusBadge
                            status={
                              service.lifecycle === "active"
                                ? "success"
                                : "neutral"
                            }
                            label={t.dyn("lifecycle", service.lifecycle, humanize(service.lifecycle))}
                            size="compact"
                          />
                        </span>
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          </div>

          <div className="page-aside">
            <Panel
              data-testid="operational-grid"
              className="motion-safe:animate-[fade-in_400ms_var(--ease-entrance)_backwards] [animation-delay:60ms]"
            >
              <PanelHeader
                title={t("capability.title")}
                description={t("environment.capabilities.description")}
                level={3}
              />
              {/* The aside is one KPI track wide: let long capability names wrap. */}
              <ul className="grid grid-cols-1 gap-3 [&_.truncate]:whitespace-normal">
                {capabilityEntries.map(({ key, label, state }) => (
                  <li key={key} className="min-w-0">
                    <CapabilityTile
                      icon={CAPABILITY_ICONS[key] ?? Boxes}
                      label={label}
                      state={state}
                    />
                  </li>
                ))}
              </ul>
            </Panel>

            <Panel
              flush
              className="motion-safe:animate-[fade-in_400ms_var(--ease-entrance)_backwards] [animation-delay:120ms]"
            >
              <PanelHeader flush title={t("environment.metadata.title")} level={3} />
              <div className="px-7 py-6 [&>dl]:grid-cols-1 [&>dl>div]:col-span-1">
                <DefinitionGrid
                  items={[
                    {
                      label: t("environment.metadata.runtime"),
                      value: <InlineCode>{data.runtime}</InlineCode>,
                    },
                    {
                      label: t("environment.metadata.branch"),
                      value: <InlineCode>{data.branch || "—"}</InlineCode>,
                    },
                    {
                      label: t("environment.metadata.clusterNamespace"),
                      wide: true,
                      value: data.cluster ? (
                        <InlineCode>{`${data.cluster.ref} / ${data.namespace}`}</InlineCode>
                      ) : data.not_applicable?.includes("cluster") ? (
                        // The runtime has no such concept. This used to render
                        // for ANY environment without a cluster, so a
                        // Kubernetes environment that had genuinely lost its
                        // cluster was described as "external runtime".
                        <span className="text-caption text-ink-muted italic">
                          {t("environment.metadata.notApplicable")}
                        </span>
                      ) : (
                        <span className="text-caption text-ink-muted italic">
                          {t("environment.metadata.notRecorded")}
                        </span>
                      ),
                    },
                    ...(isExternal
                      ? [
                          {
                            label: t("environment.metadata.hostingProvider"),
                            value: (
                              <InlineCode>
                                {data.hosting_provider ?? "unknown"}
                              </InlineCode>
                            ),
                          },
                          {
                            label: t("environment.metadata.agent"),
                            value: (
                              <span className="text-caption text-ink-muted italic">
                                {t("environment.metadata.notApplicable")}
                              </span>
                            ),
                          },
                          {
                            label: t("environment.metadata.healthSource"),
                            value: (
                              <InlineCode>
                                {data.health?.source.status ?? "not_configured"}
                              </InlineCode>
                            ),
                          },
                          {
                            label: t("environment.metadata.freshness"),
                            value: (
                              <InlineCode>
                                {data.health?.freshness ?? "unavailable"}
                              </InlineCode>
                            ),
                          },
                          ...(data.health?.last_observed_at
                            ? [
                                {
                                  label: t("environment.metadata.lastObserved"),
                                  value: (
                                    <RelativeTime
                                      value={data.health.last_observed_at}
                                    />
                                  ),
                                },
                              ]
                            : []),
                        ]
                      : []),
                    {
                      label: t("record.version"),
                      value: <span data-tabular>v{data.version}</span>,
                    },
                  ]}
                />
              </div>
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
      </div>
    </PageFrame>
  );
}
