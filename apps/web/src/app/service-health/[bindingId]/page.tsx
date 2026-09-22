"use client";

/**
 * Service health detail.
 *
 * Shows the verdict, the four sections behind it, and why each one says
 * what it says. Every number comes from the API and every status is the
 * API's; this screen never adds one up.
 */

import {
  Activity,
  ArrowLeft,
  Cpu,
  Gauge as GaugeIcon,
  Globe,
  Pencil,
  RotateCcw,
  SearchX,
  ServerCog,
  ShieldCheck,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";

import { useApi, type Loadable } from "@/components/catalog/primitives";
import {
  HealthTransitions,
  ServiceIncidents,
} from "@/components/incidents/ServiceIncidents";
import {
  RangeSelector,
  SignalChart,
} from "@/components/service-health/SignalChart";
import {
  BindingStateBadge,
  FreshnessNotice,
  HealthBadge,
  IconBubble,
  Measure,
  MiniMeter,
  MissingSignals,
  PILL_SECONDARY,
  ReasonList,
  SignalCell,
  STATUS_LABELS,
  StateCard,
  toneForServiceStatus,
  useAge,
  useSignalFormat,
} from "@/components/service-health/primitives";
import { Gauge, RingProgress } from "@/components/charts/visuals";
import { DataState } from "@/components/state/DataState";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import {
  toneForThreshold,
  toneSpec,
  type StatusTone,
} from "@/lib/design/status";
import { useT, type Translator } from "@/lib/i18n";
import {
  SIGNAL_LABELS,
  type MetricSummary,
  type SeriesRange,
  type ServiceHealth,
  type SignalValue,
} from "@/lib/serviceHealth";

const CHARTABLE: { signal: string; unit: string }[] = [
  { signal: "ready_replicas", unit: "count" },
  { signal: "restarts", unit: "count" },
  { signal: "cpu_usage", unit: "cores" },
  { signal: "memory_usage", unit: "bytes" },
  { signal: "error_ratio", unit: "ratio" },
  { signal: "latency_p95", unit: "seconds" },
];

const BIG_NUMBER =
  "text-[2.25rem] leading-none font-semibold tracking-[-0.03em] text-ink";

function signalLabel(t: Translator<"serviceHealth">, name: string): string {
  return t.dyn("signal", name, SIGNAL_LABELS[name] ?? name);
}

/**
 * One of the four sections behind the verdict, as a hero stat card: icon,
 * title and the section's own status chip; a big number and its visual;
 * the section's reasons as quiet pills underneath.
 */
function SectionCard({
  id,
  title,
  icon,
  status,
  reasons,
  children,
}: {
  /** The section's stable name — the test id, whatever language the title is in. */
  id: "availability" | "stability" | "resources" | "application";
  title: string;
  icon: React.ComponentType<{ className?: string }>;
  status: ServiceHealth["availability"]["status"];
  reasons: string[];
  children: React.ReactNode;
}) {
  const tone = toneForServiceStatus(status);
  return (
    <Panel data-testid={`section-${id}`} className="h-full">
      <div className="flex items-start justify-between gap-3">
        <IconBubble
          icon={icon}
          tone={tone === "not-applicable" ? undefined : tone}
        />
        <HealthBadge status={status} />
      </div>
      <h2 className="-mb-2 text-caption font-medium text-ink-secondary">
        {title}
      </h2>
      <div className="flex flex-1 flex-col gap-4">{children}</div>
      {reasons.length > 0 ? (
        <div className="border-t border-border pt-4">
          <ReasonList reasons={reasons} tone={tone} />
        </div>
      ) : null}
    </Panel>
  );
}

/** A labelled usage-of-limit line: text first, bar second. */
function UsageLine({
  label,
  used,
  limit,
  unit,
  ratio,
  thresholds,
}: {
  label: string;
  used: number | null;
  limit: number | null;
  unit: string;
  ratio: number | null;
  thresholds: { warn: number; critical: number; direction: "above" } | null;
}) {
  const t = useT("serviceHealth");
  const formatSignal = useSignalFormat();
  const tone: StatusTone =
    ratio === null
      ? "unknown"
      : thresholds
        ? toneForThreshold(ratio, thresholds)
        : "neutral";
  return (
    <div className="min-w-0">
      <div className="flex items-baseline justify-between gap-2 text-caption">
        <span className="font-medium text-ink-secondary">{label}</span>
        <span data-tabular className="text-ink">
          {formatSignal(ratio, "ratio")}
        </span>
      </div>
      <MiniMeter fraction={ratio} tone={tone} className="mt-1.5" />
      <span className="mt-1 block truncate text-micro text-ink-muted">
        {limit !== null
          ? t("detail.resources.ofLimit", {
              used: formatSignal(used, unit),
              limit: formatSignal(limit, unit),
            })
          : `${formatSignal(used, unit)} · ${t("detail.resources.noLimit")}`}
      </span>
    </div>
  );
}

/**
 * The bands the gauges draw.
 *
 * Only when limits are configured: without a limit there is nothing to be a
 * percentage OF, so the platform reports usage and judges nothing, and a
 * coloured zone would be a promise nobody made.
 */
const UTILISATION_THRESHOLDS = (limitsConfigured: boolean | undefined) =>
  limitsConfigured === false
    ? null
    : { warn: 0.8, critical: 0.9, direction: "above" as const };

/** Throttling is a symptom, not a budget: any sustained share is a problem. */
const THROTTLE_THRESHOLDS = {
  warn: 0.05,
  critical: 0.25,
  direction: "above" as const,
};

/** The signal state words, as pills. */
const SIGNAL_STATE_TONE: Record<SignalValue["state"], StatusTone> = {
  ok: "success",
  empty: "unknown",
  failed: "critical",
  stale: "stale",
  not_configured: "not-applicable",
  not_collected: "not-applicable",
};

function SignalRow({
  label,
  signal,
  unit,
}: {
  label: string;
  signal: SignalValue;
  unit: string;
}) {
  const spec = toneSpec(SIGNAL_STATE_TONE[signal.state] ?? "unknown");
  return (
    <li className="flex h-12 items-center gap-3 px-7">
      <span
        aria-hidden
        className={`h-2 w-2 shrink-0 rounded-full ${spec.dot}`}
      />
      <span className="min-w-0 flex-1 truncate text-caption text-ink-secondary">
        {label}
      </span>
      <SignalCell signal={signal} unit={unit} />
    </li>
  );
}

function DefinitionGrid({
  items,
}: {
  items: { label: string; value: React.ReactNode }[];
}) {
  return (
    <dl className="grid grid-cols-1 gap-x-8 gap-y-5 sm:grid-cols-2">
      {items.map((item) => (
        <div key={item.label} className="min-w-0">
          <dt className="text-micro font-medium tracking-[0.08em] text-ink-muted uppercase">
            {item.label}
          </dt>
          <dd className="mt-1 truncate text-caption text-ink">{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Loading / not-found / error, drawn as designed cards rather than bare lines. */
function Gate<T>({
  value,
  retry,
  children,
}: {
  value: Loadable<T>;
  retry: () => void;
  children: (data: T) => React.ReactNode;
}) {
  const t = useT("serviceHealth");
  const tc = useT("common");
  if (value.state === "loading") return <DataState kind="loading" />;
  if (value.state === "error") {
    const back = (
      <Link href="/service-health" className={PILL_SECONDARY}>
        <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
        {t("detail.back")}
      </Link>
    );
    return (
      <Panel className="mt-6">
        {value.notFound ? (
          // One rendering for "absent" and "outside your scope" — probing an
          // id must not reveal which it is.
          <div data-testid="state-not-found" role="status">
            <StateCard
              icon={SearchX}
              title={tc("state.notFound")}
              description={t("detail.notFound.description")}
              action={back}
            />
          </div>
        ) : (
          <StateCard
            icon={RotateCcw}
            tone="critical"
            title={t("detail.loadFailed")}
            description={value.message}
            action={
              <div className="flex flex-wrap items-center justify-center gap-2">
                <button
                  type="button"
                  onClick={retry}
                  className={PILL_SECONDARY}
                >
                  <RotateCcw className="h-3.5 w-3.5" aria-hidden />
                  {tc("action.retry")}
                </button>
                {back}
              </div>
            }
          >
            {value.correlationId ? (
              <p className="text-center text-micro text-ink-muted">
                {t("detail.correlationId")}{" "}
                <span className="font-mono">{value.correlationId}</span>
              </p>
            ) : null}
          </StateCard>
        )}
      </Panel>
    );
  }
  return <>{children(value.data)}</>;
}

export default function ServiceHealthDetailPage() {
  const t = useT("serviceHealth");
  const tc = useT("common");
  const formatSignal = useSignalFormat();
  const age = useAge();
  const { bindingId } = useParams<{ bindingId: string }>();
  const [health, retryHealth] = useApi<ServiceHealth>(
    `/v1/service-health/bindings/${bindingId}/health`,
  );
  const [summary] = useApi<MetricSummary>(
    `/v1/service-health/bindings/${bindingId}/metrics`,
  );
  const [range, setRange] = useState<SeriesRange>("1h");

  return (
    <PageFrame>
      <Gate value={health} retry={retryHealth}>
        {(data) => {
          const binding = data.binding;
          const verdictTone = toneForServiceStatus(data.status);
          const VerdictIcon = toneSpec(verdictTone).icon;
          const chartable =
            summary.state === "ready"
              ? CHARTABLE.filter((entry) =>
                  summary.data.readable_signals.includes(entry.signal),
                )
              : [];
          const { availability, stability, resources, application } = data;
          const readyTone: StatusTone =
            availability.ready_replicas === null ||
            availability.desired_replicas === null
              ? "unknown"
              : availability.ready_replicas >= availability.desired_replicas
                ? "success"
                : availability.ready_replicas === 0
                  ? "critical"
                  : "warning";
          const utilisation = UTILISATION_THRESHOLDS(
            resources.limits_configured,
          );

          return (
            <>
              <p className="mb-3 flex items-center gap-1.5 text-micro text-ink-muted">
                <Link href="/service-health" className="hover:text-ink">
                  {t("detail.crumb")}
                </Link>
                <span>/</span>
                <span className="font-mono text-ink-secondary">
                  {binding.project_key}/{binding.environment_key}/
                  {binding.service_key}
                </span>
              </p>

              <PageHeader
                title={binding.service_key}
                status={
                  <>
                    <HealthBadge status={data.status} />
                    <BindingStateBadge
                      lifecycle={binding.lifecycle}
                      resolved={binding.resolved}
                    />
                  </>
                }
                meta={
                  <>
                    <span className="font-mono">
                      {binding.project_key}/{binding.environment_key}
                    </span>
                    <span className="font-mono">
                      {binding.cluster_ref}/{binding.namespace}/
                      {binding.workload_name}
                    </span>
                    <span>
                      {t("detail.newestSample", { age: age(data.freshness_age_seconds) })}
                    </span>
                  </>
                }
                actions={
                  <Link
                    href={`/service-health/bind?environment_service_id=${binding.environment_service_id}&binding_id=${binding.id}`}
                    className={PILL_SECONDARY}
                  >
                    <Pencil className="h-3.5 w-3.5" aria-hidden />
                    {t("detail.editBinding")}
                  </Link>
                }
              />

              <div className="space-y-6">
                <FreshnessNotice
                  partial={data.partial}
                  servedFromLastGood={data.served_from_last_good}
                  computedAt={data.computed_at}
                  servedAt={data.served_at}
                  newestSampleAt={data.newest_sample_at}
                />

                {/* Hero row: the four sections behind the verdict. */}
                <div className="page-grid">
                  <SectionCard
                    id="availability"
                    title={t("detail.section.availability")}
                    icon={ServerCog}
                    status={availability.status}
                    reasons={availability.reasons}
                  >
                    <div className="flex items-center justify-between gap-4">
                      <span>
                        {/* Two exact numbers: a percentage alone would hide
                            the difference between 0/0 and an unmeasured pair. */}
                        <span data-tabular className={`block ${BIG_NUMBER}`}>
                          {availability.ready_replicas ?? "—"}
                          <span className="text-ink-muted">
                            /{availability.desired_replicas ?? "—"}
                          </span>
                        </span>
                        <span className="mt-1.5 block text-caption text-ink-muted">
                          {t("detail.availability.replicasReady")}
                        </span>
                      </span>
                      <RingProgress
                        size={60}
                        label={t("detail.availability.ring")}
                        value={
                          availability.desired_replicas
                            ? ((availability.ready_replicas ?? 0) /
                                availability.desired_replicas) *
                              100
                            : null
                        }
                        tone={readyTone}
                      />
                    </div>
                    {availability.scaled_to_zero ? (
                      <p className="rounded-full bg-surface-2 px-3 py-1.5 text-micro text-ink-secondary">
                        {t("detail.availability.scaledToZero")}
                      </p>
                    ) : null}
                  </SectionCard>

                  <SectionCard
                    id="stability"
                    title={t("detail.section.stability")}
                    icon={Activity}
                    status={stability.status}
                    reasons={stability.reasons}
                  >
                    <span>
                      <span data-tabular className={`block ${BIG_NUMBER}`}>
                        {stability.restarts_in_window ?? "—"}
                      </span>
                      <span className="mt-1.5 block text-caption text-ink-muted">
                        {t("detail.stability.restartsInWindow")}
                      </span>
                    </span>
                    <div className="grid grid-cols-1 gap-2">
                      {(
                        [
                          [t("detail.stability.crashLoop"), stability.crash_looping],
                          [t("detail.stability.oomKilled"), stability.oom_killed],
                        ] as const
                      ).map(([label, flagged]) => (
                        <span
                          key={label}
                          className={`flex items-center justify-between gap-2 rounded-full px-3.5 py-1.5 text-caption ${
                            flagged
                              ? toneSpec("critical").chip
                              : "bg-surface-2 text-ink-secondary"
                          }`}
                        >
                          {label}
                          <span className="font-semibold">
                            {flagged ? tc("state.yes") : tc("state.no")}
                          </span>
                        </span>
                      ))}
                    </div>
                  </SectionCard>

                  <SectionCard
                    id="resources"
                    title={t("detail.section.resources")}
                    icon={Cpu}
                    status={resources.status}
                    reasons={resources.reasons}
                  >
                    <span>
                      <span data-tabular className={`block ${BIG_NUMBER}`}>
                        {formatSignal(resources.cpu_utilization, "ratio")}
                      </span>
                      <span className="mt-1.5 block text-caption text-ink-muted">
                        {t("detail.resources.cpuOfLimit")}
                      </span>
                    </span>
                    <div className="space-y-3">
                      <UsageLine
                        label={t("detail.resources.cpu")}
                        used={resources.cpu_cores_used}
                        limit={resources.cpu_limit_cores}
                        unit="cores"
                        ratio={resources.cpu_utilization}
                        thresholds={utilisation}
                      />
                      <UsageLine
                        label={t("detail.resources.memory")}
                        used={resources.memory_bytes_used}
                        limit={resources.memory_limit_bytes}
                        unit="bytes"
                        ratio={resources.memory_utilization}
                        thresholds={utilisation}
                      />
                    </div>
                  </SectionCard>

                  <SectionCard
                    id="application"
                    title={t("detail.section.application")}
                    icon={Globe}
                    status={application.status}
                    reasons={application.reasons}
                  >
                    {application.metrics_present ? (
                      <>
                        <span>
                          <span data-tabular className={`block ${BIG_NUMBER}`}>
                            {formatSignal(application.error_ratio, "ratio")}
                          </span>
                          <span className="mt-1.5 block text-caption text-ink-muted">
                            {t("detail.application.errorRatio")}
                          </span>
                        </span>
                        <div className="grid grid-cols-1 gap-2">
                          <span className="flex items-center justify-between gap-2 rounded-full bg-surface-2 px-3.5 py-1.5 text-caption">
                            <span className="text-ink-secondary">
                              {t("detail.application.requestRate")}
                            </span>
                            <Measure
                              value={application.request_rate}
                              unit="requests_per_second"
                            />
                          </span>
                          <span className="flex items-center justify-between gap-2 rounded-full bg-surface-2 px-3.5 py-1.5 text-caption">
                            <span className="text-ink-secondary">
                              {t("detail.application.latencyP95")}
                            </span>
                            <Measure
                              value={application.latency_p95_seconds}
                              unit="seconds"
                            />
                          </span>
                        </div>
                      </>
                    ) : (
                      <div className="rounded-[1rem] border border-dashed border-border px-4 py-3.5">
                        <p className="text-body font-semibold text-ink">
                          {t("detail.application.noMetrics")}
                        </p>
                        <p className="mt-0.5 text-caption text-ink-muted">
                          {t("detail.application.noMetricsNote")}
                        </p>
                      </div>
                    )}
                  </SectionCard>
                </div>

                {/* Why + resource pressure. */}
                <div className="grid grid-cols-1 items-stretch gap-6 xl:grid-cols-2">
                  <Panel className="h-full">
                    <div className="flex items-center gap-4">
                      <span
                        aria-hidden
                        className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-full ${toneSpec(verdictTone).chip}`}
                      >
                        <VerdictIcon className="h-5 w-5" />
                      </span>
                      <div className="min-w-0">
                        <h2 className="text-[1.0625rem] leading-6 font-semibold tracking-[-0.01em] text-ink">
                          {t("detail.why.title")}
                        </h2>
                        <p className="text-caption text-ink-muted">
                          {t("detail.why.description", {
                            verdict: t
                              .dyn("status", data.status, STATUS_LABELS[data.status])
                              .toLocaleLowerCase(t.locale),
                            policy: data.policy_key,
                          })}
                        </p>
                      </div>
                    </div>
                    {data.reasons.length === 0 ? (
                      <div className="flex items-center gap-3 rounded-[1rem] bg-surface-2 px-4 py-3">
                        <ShieldCheck
                          className="h-4 w-4 text-healthy"
                          aria-hidden
                        />
                        <p className="text-caption text-ink-secondary">
                          {t("detail.why.allWithin")}
                        </p>
                      </div>
                    ) : (
                      <ReasonList
                        reasons={data.reasons}
                        messages={data.messages}
                        tone={verdictTone}
                        size="roomy"
                      />
                    )}
                    <div className="mt-auto space-y-3 border-t border-border pt-4">
                      <MissingSignals missing={data.missing_signals} />
                      <p className="flex flex-wrap gap-x-4 gap-y-1 text-micro text-ink-muted">
                        <span>{t("detail.why.computed", { when: data.computed_at })}</span>
                        <span>
                          {t("detail.why.newestSample", {
                            age: age(data.freshness_age_seconds),
                          })}
                        </span>
                        {data.cached ? <span>{t("detail.why.fromCache")}</span> : null}
                      </p>
                    </div>
                  </Panel>

                  <Panel className="h-full">
                    <PanelHeader
                      title={t("detail.pressure.title")}
                      description={
                        resources.limits_configured === false
                          ? t("detail.pressure.noLimits")
                          : t("detail.pressure.withLimits")
                      }
                    />
                    {/* Utilisation against a limit is exactly what a gauge is
                        for: bounded, and the distance to the limit is the
                        point. Bands only appear when limits are configured. */}
                    <div className="my-auto grid grid-cols-1 gap-4 sm:grid-cols-3">
                      <div className="flex justify-center rounded-[1.125rem] bg-surface-2 px-2 py-4">
                        <Gauge
                          size="compact"
                          label={t("detail.pressure.cpu")}
                          unit="ratio"
                          value={resources.cpu_utilization}
                          thresholds={utilisation}
                          missingReason={t("detail.pressure.notMeasured")}
                          caption={
                            resources.cpu_limit_cores !== null
                              ? t("detail.resources.ofLimit", {
                                  used: formatSignal(resources.cpu_cores_used, "cores"),
                                  limit: formatSignal(resources.cpu_limit_cores, "cores"),
                                })
                              : t("detail.resources.noLimit")
                          }
                        />
                      </div>
                      <div className="flex justify-center rounded-[1.125rem] bg-surface-2 px-2 py-4">
                        <Gauge
                          size="compact"
                          label={t("detail.pressure.memory")}
                          unit="ratio"
                          value={resources.memory_utilization}
                          thresholds={utilisation}
                          missingReason={t("detail.pressure.notMeasured")}
                          caption={
                            resources.memory_limit_bytes !== null
                              ? t("detail.resources.ofLimit", {
                                  used: formatSignal(resources.memory_bytes_used, "bytes"),
                                  limit: formatSignal(resources.memory_limit_bytes, "bytes"),
                                })
                              : t("detail.resources.noLimit")
                          }
                        />
                      </div>
                      <div className="flex justify-center rounded-[1.125rem] bg-surface-2 px-2 py-4">
                        <Gauge
                          size="compact"
                          label={t("detail.pressure.throttling")}
                          unit="ratio"
                          value={resources.cpu_throttled_ratio}
                          thresholds={THROTTLE_THRESHOLDS}
                          missingReason={t("detail.pressure.notMeasured")}
                          caption={t("detail.pressure.throttledPeriods")}
                        />
                      </div>
                    </div>
                  </Panel>
                </div>

                {chartable.length > 0 ? (
                  <section aria-label={t("detail.history.title")} className="space-y-4">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div className="flex items-center gap-3">
                        <IconBubble icon={GaugeIcon} />
                        <div>
                          <h2 className="text-[1.0625rem] leading-6 font-semibold tracking-[-0.01em] text-ink">
                            {t("detail.history.title")}
                          </h2>
                          <p className="text-caption text-ink-muted">
                            {t("detail.history.description")}
                          </p>
                        </div>
                      </div>
                      <RangeSelector value={range} onChange={setRange} />
                    </div>
                    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2 2xl:grid-cols-3">
                      {chartable.map((entry) => (
                        <Panel key={entry.signal}>
                          <PanelHeader
                            title={t.dyn("signal", entry.signal, SIGNAL_LABELS[entry.signal] ?? entry.signal)}
                            meta={<span>{t("detail.history.last", { range })}</span>}
                          />
                          <SignalChart
                            bindingId={binding.id}
                            signal={entry.signal}
                            unit={entry.unit}
                            range={range}
                          />
                        </Panel>
                      ))}
                    </div>
                  </section>
                ) : null}

                <div className="grid grid-cols-1 items-stretch gap-6 lg:grid-cols-2">
                  <ServiceIncidents bindingId={binding.id} />
                  <HealthTransitions bindingId={binding.id} />
                </div>

                {/* Metadata last. */}
                <div className="grid grid-cols-1 items-stretch gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
                  <Panel className="h-full">
                    <PanelHeader
                      title={t("detail.binding.title")}
                      description={t("detail.binding.description")}
                    />
                    <DefinitionGrid
                      items={[
                        {
                          label: t("detail.binding.workload"),
                          value: (
                            <span className="font-mono">
                              {binding.workload_kind}/{binding.workload_name}
                            </span>
                          ),
                        },
                        {
                          label: t("detail.binding.clusterNamespace"),
                          value: (
                            <span className="font-mono">
                              {binding.cluster_ref} · {binding.namespace}
                            </span>
                          ),
                        },
                        {
                          label: t("detail.binding.preset"),
                          value: (
                            <span className="font-mono">
                              {binding.preset_key}
                            </span>
                          ),
                        },
                        {
                          label: t("detail.binding.policy"),
                          value: (
                            <span className="font-mono">
                              {binding.health_policy_key}
                            </span>
                          ),
                        },
                        {
                          label: t("detail.binding.datasource"),
                          value: binding.datasource_configured
                            ? t("detail.binding.configured")
                            : t("detail.binding.notConfigured"),
                        },
                        {
                          label: t("detail.binding.resolved"),
                          value: binding.resolved
                            ? (binding.resolved_at ?? t("detail.binding.resolvedYes"))
                            : t("detail.binding.unresolved"),
                        },
                      ]}
                    />
                  </Panel>

                  {summary.state === "ready" ? (
                    <Panel flush className="h-full">
                      <PanelHeader
                        flush
                        title={t("detail.signals.title")}
                        description={t("detail.signals.description")}
                      />
                      <ul className="divide-y divide-border py-1">
                        <SignalRow
                          label={signalLabel(t, "desired_replicas")}
                          signal={
                            summary.data.metrics.availability.desired_replicas
                          }
                          unit="count"
                        />
                        <SignalRow
                          label={signalLabel(t, "ready_replicas")}
                          signal={
                            summary.data.metrics.availability.ready_replicas
                          }
                          unit="count"
                        />
                        <SignalRow
                          label={signalLabel(t, "restarts")}
                          signal={summary.data.metrics.stability.restarts}
                          unit="count"
                        />
                        <SignalRow
                          label={signalLabel(t, "cpu_usage")}
                          signal={summary.data.metrics.resources.cpu_usage}
                          unit="cores"
                        />
                        <SignalRow
                          label={signalLabel(t, "memory_usage")}
                          signal={summary.data.metrics.resources.memory_usage}
                          unit="bytes"
                        />
                        <SignalRow
                          label={signalLabel(t, "request_rate")}
                          signal={summary.data.metrics.application.request_rate}
                          unit="requests_per_second"
                        />
                        <SignalRow
                          label={signalLabel(t, "error_ratio")}
                          signal={summary.data.metrics.application.error_ratio}
                          unit="ratio"
                        />
                        <SignalRow
                          label={signalLabel(t, "freshness")}
                          signal={summary.data.metrics.freshness}
                          unit="ratio"
                        />
                      </ul>
                    </Panel>
                  ) : null}
                </div>
              </div>
            </>
          );
        }}
      </Gate>
    </PageFrame>
  );
}
