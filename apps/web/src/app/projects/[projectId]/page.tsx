"use client";

/**
 * Project detail.
 *
 * Composition, top to bottom: a KPI strip that answers "is this project
 * well, and how big is it"; the environments as cards, each carrying its own
 * health composition and one tile per service (the primary visual of the
 * page); the live signals for one environment; then the standing record —
 * capabilities, dependencies, catalog metadata — that changes far less often.
 *
 * Three distinctions this page exists to keep:
 *
 *   Criticality is not health. The measured-health chip names what Drake has
 *   actually observed across this project's environments; the criticality
 *   chip next to it is a recorded judgement that does not move with it.
 *
 *   Managed dependencies are not workloads. A provider-run database has no
 *   Deployment and nothing to roll, so its `workload_applicability:
 *   not_applicable` renders as "Not applicable" — a different answer from
 *   "unknown".
 *
 *   Verification is not health. `repository_intent` means somebody declared a
 *   dependency in source; it is evidence about a repository, not about a
 *   running system, and it never renders in the healthy colour.
 */

import {
  Activity,
  Boxes,
  Cloud,
  Database,
  FolderGit2,
  HardDrive,
  Layers,
  LifeBuoy,
  Radar,
  Rocket,
  ShieldCheck,
  Users,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Suspense } from "react";

import {
  EnvironmentCard,
  toneCounts,
} from "@/components/catalog/EnvironmentCards";
import {
  CapabilityTile,
  DefinitionGrid,
  IconBubble,
  MiniBars,
  StatTile,
  TileState,
  ToneBar,
  capabilityTone,
} from "@/components/catalog/visuals";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";
import { ProjectMetricsSection } from "@/components/telemetry/ProjectMetricsSection";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { InlineCode, RelativeTime } from "@/components/ui/identifiers";
import {
  DeniedState,
  ErrorState,
  LoadingSkeleton,
  NotFoundState,
} from "@/components/ui/states";
import type { Environment, Project, ProjectDependency } from "@/lib/catalog";
import { useCrumbLabel } from "@/lib/crumbs";
import {
  compareTone,
  humanize,
  toneForHealth,
  toneSpec,
  type StatusTone,
} from "@/lib/design/status";
import {
  serviceHealthListPath,
  type ServiceHealthPage,
} from "@/lib/serviceHealth";
import { useResource } from "@/lib/useResource";
import { buildProjectTopology } from "@/lib/view-models/scope-health";

const CAPABILITY_LABELS: Record<string, string> = {
  telemetry: "Telemetry",
  inventory: "Cluster inventory",
  deployment: "Deployments",
  protection: "Backup & restore",
};

const CAPABILITY_ICONS: Record<string, typeof Activity> = {
  telemetry: Activity,
  inventory: Boxes,
  deployment: Rocket,
  protection: LifeBuoy,
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

function SectionTitle({
  title,
  description,
  aside,
}: {
  title: string;
  description?: string;
  aside?: React.ReactNode;
}) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
      <div className="min-w-0">
        <h2 className="text-[1.25rem] leading-7 font-semibold tracking-[-0.015em] text-ink">
          {title}
        </h2>
        {description ? (
          <p className="mt-0.5 text-caption text-ink-muted">{description}</p>
        ) : null}
      </div>
      {aside ? (
        <div className="flex shrink-0 items-center gap-2">{aside}</div>
      ) : null}
    </div>
  );
}

function DependencyCard({ dependency }: { dependency: ProjectDependency }) {
  const notApplicable = dependency.workload_applicability === "not_applicable";
  return (
    <li className="flex min-w-0 flex-col gap-3 px-7 py-5">
      <div className="flex min-w-0 items-start gap-3">
        <IconBubble
          icon={
            dependency.dependency_class === "external_service"
              ? Cloud
              : Database
          }
        />
        <div className="min-w-0 flex-1">
          <span className="block truncate text-body font-semibold text-ink">
            {dependency.display_name}
          </span>
          <span className="mt-0.5 block truncate text-micro text-ink-muted">
            <InlineCode>{dependency.provider}</InlineCode>{" "}
            <InlineCode>{dependency.dependency_class}</InlineCode>
          </span>
        </div>
        <span className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
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
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 pl-[3.25rem] text-micro text-ink-muted">
        <span>
          <InlineCode>{dependency.verification}</InlineCode>{" "}
          {VERIFICATION_LABELS[dependency.verification] ?? ""}
        </span>
        <span className={notApplicable ? "" : "text-ink-secondary"}>
          Workload:{" "}
          {notApplicable ? "Not applicable" : dependency.workload_applicability}
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
  const environments = useResource<{
    environments: Environment[];
    next_cursor: string | null;
  }>(`/v1/projects/${projectId}/environments`);
  const services = useResource<ServiceHealthPage>(
    serviceHealthListPath({ projectId, limit: 100, offset: 0 }),
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
  const managed =
    data.dependencies?.filter((d) => d.dependency_class !== "in_cluster") ?? [];
  const inCluster =
    data.dependencies?.filter((d) => d.dependency_class === "in_cluster") ?? [];

  const environmentList = environments.data?.environments ?? [];
  const servicesComplete =
    Boolean(services.data) &&
    services.data!.total <= services.data!.items.length;
  const topology = buildProjectTopology(
    projectId,
    environmentList,
    services.data?.items ?? [],
    servicesComplete,
  );
  const worstTone: StatusTone =
    topology.length > 0
      ? topology.reduce(
          (worst, lane) =>
            compareTone(lane.tone, worst) < 0 ? lane.tone : worst,
          topology[0].tone,
        )
      : "unknown";
  const laneServices = topology.flatMap((lane) => lane.services);
  const serviceTones = toneCounts(laneServices, (service) => service.tone);

  const capabilityEntries = Object.entries(CAPABILITY_LABELS).map(
    ([key, label]) => ({
      key,
      label,
      state: data.operational?.[key] ?? "unknown",
    }),
  );
  const reporting = capabilityEntries.filter(
    (entry) => entry.state === "ok",
  ).length;
  const environmentById = new Map(
    environmentList.map((environment) => [environment.id, environment]),
  );

  return (
    <PageFrame width="wide">
      <PageHeader
        title={data.display_name}
        status={
          <>
            <span className="text-caption text-ink-muted">Measured health</span>
            <StatusBadge status={worstTone} label={toneSpec(worstTone).label} />
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
                  data.repository.default_branch
                    ? ` @ ${data.repository.default_branch}`
                    : ""
                }`}
              </InlineCode>
            </span>
            <span>
              catalog record accepted{" "}
              <RelativeTime value={data.source.accepted_at} />
            </span>
          </>
        }
      />

      <div className="flex flex-col gap-10">
        {/* KPI strip */}
        <div
          className="page-grid motion-safe:animate-[fade-in_320ms_var(--ease-entrance)_backwards]"
          data-testid="project-kpis"
        >
          <StatTile
            icon={Radar}
            tone={worstTone}
            label="Service health"
            value={services.data ? laneServices.length : "—"}
            suffix={
              services.data
                ? `service${laneServices.length === 1 ? "" : "s"} with evidence`
                : "evidence unavailable"
            }
          >
            <ToneBar
              counts={serviceTones}
              label="Service health across environments"
              emptyLabel={
                services.data
                  ? "No service in this project has reported yet"
                  : "Service evidence could not be loaded"
              }
            />
          </StatTile>

          <StatTile
            icon={Layers}
            label="Environments"
            value={
              environments.data
                ? environmentList.length
                : data.counts.environments
            }
            suffix={`${environmentList.filter((environment) => environment.lifecycle === "active").length} active`}
          >
            {topology.length > 0 ? (
              <ul
                className="flex flex-wrap gap-2"
                aria-label="Environments by measured health"
              >
                {topology.map((lane) => (
                  <li
                    key={lane.id}
                    className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-micro font-medium ${toneSpec(lane.tone).chip}`}
                    title={`${lane.key}: ${toneSpec(lane.tone).label}`}
                  >
                    <span
                      aria-hidden
                      className={`h-1.5 w-1.5 rounded-full ${toneSpec(lane.tone).dot}`}
                    />
                    {lane.key}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-micro text-ink-muted">
                No environments in your scope
              </p>
            )}
          </StatTile>

          <StatTile
            icon={Boxes}
            label="Services"
            value={data.counts.services}
            suffix="in catalog"
          >
            {topology.length > 0 ? (
              <MiniBars
                label="Services with evidence per environment"
                items={topology.map((lane) => ({
                  key: lane.id,
                  label: lane.key,
                  value: lane.services.length,
                }))}
              />
            ) : (
              <p className="text-micro text-ink-muted">
                No environment breakdown yet
              </p>
            )}
          </StatTile>

          <StatTile
            icon={ShieldCheck}
            label="Capabilities"
            value={reporting}
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
        </div>

        {/* Environments: the primary visual */}
        <section
          aria-label="Environments"
          className="motion-safe:animate-[fade-in_380ms_var(--ease-entrance)_backwards]"
        >
          <SectionTitle
            title="Environments"
            description="Every service inside each environment, worst first. No evidence reads as unassessed, never healthy."
            aside={
              services.data && !servicesComplete ? (
                <>
                  <StatusBadge
                    status="unknown"
                    label="Evidence incomplete"
                    size="compact"
                  />
                  <Link
                    href={`/service-health?project_id=${projectId}`}
                    className="rounded-full border border-border px-3 py-1.5 text-caption font-medium text-ink transition-colors hover:bg-surface-hover"
                  >
                    View full service health
                  </Link>
                </>
              ) : null
            }
          />
          <div data-testid="environment-list">
            {environments.loading && !environments.data ? (
              <LoadingSkeleton variant="table" rows={3} />
            ) : environments.denied ? (
              <Panel>
                <DeniedState compact />
              </Panel>
            ) : !environments.data ? (
              <Panel>
                <ErrorState
                  compact
                  description={environments.error ?? undefined}
                  onRetry={environments.reload}
                />
              </Panel>
            ) : environmentList.length === 0 ? (
              <Panel>
                <TileState
                  icon={Layers}
                  testId="state-empty"
                  title="No environments in your scope"
                  description="Environments you are authorized to see appear here as cards."
                />
              </Panel>
            ) : (
              <div className="grid grid-cols-1 items-stretch gap-6 lg:grid-cols-2 2xl:grid-cols-3">
                {topology.map((lane) => (
                  <EnvironmentCard
                    key={lane.id}
                    lane={lane}
                    environment={environmentById.get(lane.id)}
                  />
                ))}
              </div>
            )}
          </div>
        </section>

        <Suspense
          fallback={<LoadingSkeleton variant="chart" label="Loading metrics" />}
        >
          {/* The Telemetry capability card links here, so the target has to exist. */}
          <div id="signals" className="scroll-mt-4">
            <ProjectMetricsSection environments={environmentList} />
          </div>
        </Suspense>

        {/* Standing record */}
        <section aria-label="Standing state">
          <SectionTitle
            title="Standing state"
            description="Capabilities, dependencies and the catalog record behind this project."
          />
          <div className="page-grid" data-cols="2">
            {/* The column's last card grows so both columns end on one line. */}
            <div className="flex min-w-0 flex-col gap-6 [&>*:last-child]:flex-1">
              <Panel data-testid="operational-grid">
                <PanelHeader
                  title="Capabilities"
                  description="What Drake can observe here. Not configured is an absence, not a fault."
                  level={3}
                />
                <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  {capabilityEntries.map(({ key, label, state }) => (
                    <li key={key} className="min-w-0">
                      <CapabilityTile
                        icon={CAPABILITY_ICONS[key] ?? Activity}
                        label={label}
                        state={state}
                        // A capability that reports something has somewhere
                        // to report it; absences stay unlinked on purpose.
                        href={
                          state === "not_configured"
                            ? null
                            : CAPABILITY_HREF[key]?.(projectId)
                        }
                      />
                    </li>
                  ))}
                </ul>
              </Panel>

              {managed.length > 0 ? (
                <Panel flush>
                  <PanelHeader
                    flush
                    title="Managed dependencies"
                    description="Run by a provider, not by Drake — no in-cluster workload behind them."
                    level={3}
                  />
                  <ul
                    className="divide-y divide-border"
                    data-testid="dependency-list"
                  >
                    {managed.map((dependency) => (
                      <DependencyCard
                        key={dependency.id}
                        dependency={dependency}
                      />
                    ))}
                  </ul>
                </Panel>
              ) : null}

              {inCluster.length > 0 ? (
                <Panel flush>
                  <PanelHeader
                    flush
                    title="In-cluster datastores"
                    description="Drake runs these, so their health comes from the workload path."
                    level={3}
                  />
                  <ul
                    className="divide-y divide-border"
                    data-testid="in-cluster-dependency-list"
                  >
                    {inCluster.map((dependency) => (
                      <li
                        key={dependency.id}
                        className="flex items-center gap-3 px-7 py-4"
                      >
                        <IconBubble icon={HardDrive} />
                        <span className="min-w-0">
                          <span className="block truncate text-body font-semibold text-ink">
                            {dependency.display_name}
                          </span>
                          <span className="mt-0.5 block text-micro text-ink-muted">
                            <InlineCode>{dependency.engine}</InlineCode> · scope{" "}
                            <InlineCode>{dependency.scope}</InlineCode>
                          </span>
                        </span>
                      </li>
                    ))}
                  </ul>
                </Panel>
              ) : null}
            </div>

            <Panel>
              <PanelHeader title="Catalog record" level={3} />
              <div className="flex items-center gap-4 rounded-[1.125rem] border border-border bg-surface-2/40 p-4">
                <IconBubble icon={FolderGit2} size="large" />
                <div className="min-w-0 flex-1">
                  <span className="block truncate text-body font-semibold text-ink">
                    {data.repository.owner}/{data.repository.name}
                  </span>
                  <span className="mt-0.5 block text-micro text-ink-muted">
                    {humanize(data.repository.provider)} repository · default
                    branch{" "}
                    <InlineCode>
                      {data.repository.default_branch || "—"}
                    </InlineCode>
                  </span>
                </div>
              </div>
              <DefinitionGrid
                items={[
                  {
                    label: "Owners",
                    value:
                      data.owners && data.owners.length > 0 ? (
                        <span className="flex flex-wrap gap-2">
                          {data.owners.map((owner) => (
                            <span
                              key={`${owner.team}-${owner.role}`}
                              className="inline-flex items-center gap-1.5 rounded-full bg-surface-2 py-1 pr-3 pl-1 text-caption text-ink"
                            >
                              <span
                                aria-hidden
                                className="flex h-5 w-5 items-center justify-center rounded-full bg-surface-3 text-ink-secondary"
                              >
                                <Users className="h-3 w-3" />
                              </span>
                              {`${owner.team} (${owner.role})`}
                            </span>
                          ))}
                        </span>
                      ) : (
                        "—"
                      ),
                    wide: true,
                  },
                  {
                    label: "Tenant model",
                    value: <InlineCode>{data.tenant_model}</InlineCode>,
                  },
                  {
                    label: "Catalog version",
                    value: <span data-tabular>v{data.version}</span>,
                  },
                  {
                    label: "Source",
                    value: (
                      <InlineCode>
                        {data.source.kind}:{data.source.ref}
                      </InlineCode>
                    ),
                  },
                  {
                    label: "Revision",
                    value: (
                      <InlineCode className="break-all">
                        {data.source.revision}
                      </InlineCode>
                    ),
                  },
                  {
                    label: "Accepted",
                    value: <RelativeTime value={data.source.accepted_at} />,
                  },
                ]}
              />
            </Panel>
          </div>
        </section>
      </div>
    </PageFrame>
  );
}
