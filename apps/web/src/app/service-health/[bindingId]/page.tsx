"use client";

/**
 * Service health detail.
 *
 * Shows the verdict, the four sections behind it, and why each one says
 * what it says. Every number comes from the API and every status is the
 * API's; this screen never adds one up.
 */

import {
  ArrowLeft,
  ArrowRight,
  MemoryStick,
  Pencil,
  Repeat,
  RotateCcw,
  Route,
  ScrollText,
  SearchX,
  ShieldCheck,
  Siren,
} from "lucide-react";
import Link from "next/link";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";

import { useApi, type Loadable } from "@/components/catalog/primitives";
import {
  HealthTransitions,
  ServiceIncidents,
} from "@/components/incidents/ServiceIncidents";
import { LogExplorer } from "@/components/service-health/LogExplorer";
import { TimelineStrip } from "@/components/service-health/TimelineStrip";
import { ServiceMapPanel } from "@/components/service-map/ServiceMapPanel";
import { useBindingEnvironment } from "@/lib/serviceMap";
import { TraceExplorer } from "@/components/service-health/TraceExplorer";
import {
  RangeSelector,
  SignalChart,
} from "@/components/service-health/SignalChart";
import {
  BindingStateBadge,
  FreshnessNotice,
  HealthBadge,
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
import { Gauge } from "@/components/charts/visuals";
import { DataState } from "@/components/state/DataState";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import {
  toneForThreshold,
  toneSpec,
  type StatusTone,
} from "@/lib/design/status";
import { useFormat, useT, type Translator } from "@/lib/i18n";
import { useCrumbLabel } from "@/lib/crumbs";
import { durationSeconds, type IncidentSummary } from "@/lib/incidents";
import { percent, signalsPath, type SignalPage } from "@/lib/signals";
import { formatDuration } from "@/lib/traces";
import type { TimelineWindow } from "@/lib/timeline";
import { useResource } from "@/lib/useResource";
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


function signalLabel(t: Translator<"serviceHealth">, name: string): string {
  return t.dyn("signal", name, SIGNAL_LABELS[name] ?? name);
}

/**
 * One of the four sections behind the verdict, as a compact tile: the
 * section's name and its own status, one big number, its context, and the
 * section's reasons underneath when there are any.
 */
function SectionCard({
  id,
  title,
  status,
  reasons,
  children,
}: {
  /** The section's stable name — the test id, whatever language the title is in. */
  id: "availability" | "stability" | "resources" | "application";
  title: string;
  status: ServiceHealth["availability"]["status"];
  reasons: string[];
  children: React.ReactNode;
}) {
  const tone = toneForServiceStatus(status);
  return (
    <Panel
      data-testid={`section-${id}`}
      className={`h-full !gap-3 !p-4 ${tone === "critical" ? "!border-critical" : ""}`}
    >
      <div className="flex items-center justify-between gap-2">
        <h2 className="truncate text-caption font-medium text-ink-secondary">{title}</h2>
        <HealthBadge status={status} />
      </div>
      <div className="flex flex-1 flex-col gap-2.5">{children}</div>
      {reasons.length > 0 ? (
        <div className="border-t border-border pt-3">
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
  caption,
}: {
  label: string;
  used: number | null;
  limit: number | null;
  unit: string;
  ratio: number | null;
  thresholds: { warn: number; critical: number; direction: "above" } | null;
  /** Replaces the used-of-limit line for a share that has no limit. */
  caption?: string;
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
        {caption
          ? caption
          : limit !== null
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

const TABS = ["overview", "requests", "logs", "metrics", "incidents", "setup"] as const;
type Tab = (typeof TABS)[number];

/** The page's tabs. Error counts show only when there is something to count. */
function DetailTabs({
  value,
  onChange,
  requestErrors,
  errorLines,
  incidentOpen,
}: {
  value: Tab;
  onChange: (tab: Tab) => void;
  requestErrors: number;
  errorLines: number;
  incidentOpen: boolean;
}) {
  const t = useT("serviceHealth");
  const badge = (tab: Tab): { text: string; tone: StatusTone } | null => {
    if (tab === "requests" && requestErrors > 0)
      return { text: t("detail.nav.errors", { count: requestErrors }), tone: "critical" };
    if (tab === "logs" && errorLines > 0)
      return { text: t("detail.nav.errors", { count: errorLines }), tone: "warning" };
    if (tab === "incidents" && incidentOpen) return { text: "1", tone: "critical" };
    return null;
  };
  return (
    <div
      role="tablist"
      aria-label={t("detail.nav.label")}
      className="flex gap-1 overflow-x-auto border-b border-border [scrollbar-width:none]"
    >
      {TABS.map((tab) => {
        const selected = tab === value;
        const b = badge(tab);
        return (
          <button
            key={tab}
            type="button"
            role="tab"
            id={`sh-tab-${tab}`}
            aria-selected={selected}
            aria-controls={`sh-panel-${tab}`}
            onClick={() => onChange(tab)}
            className={`-mb-px inline-flex h-10 shrink-0 items-center gap-1.5 border-b-2 px-3 text-[0.84375rem] whitespace-nowrap transition-colors ${
              selected
                ? "border-brand font-semibold text-ink"
                : "border-transparent font-medium text-ink-muted hover:text-ink"
            }`}
          >
            {t(`detail.tabs.${tab}`)}
            {b ? (
              <span className={`rounded-full px-1.5 py-px font-mono text-[0.6875rem] ${toneSpec(b.tone).chip}`}>{b.text}</span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

/** The incident this service is in right now, above everything else. */
function ActiveIncidentBanner({ active }: { active: IncidentSummary | null }) {
  const t = useT("incidents");
  const fmt = useFormat();
  if (!active) return null;
  return (
    <Link
      href={`/incidents/${active.id}`}
      data-testid="active-incident-banner"
      className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-panel border border-critical bg-critical-soft px-4 py-3 text-critical hover:brightness-105"
    >
      <Siren className="h-4 w-4 shrink-0" aria-hidden />
      <span className="text-body font-semibold">{t("service.openIncident", { title: active.title })}</span>
      <span className="text-caption">
        {t("service.runningFor", { duration: fmt.duration(durationSeconds(active.opened_at, null)) })}
      </span>
      <ArrowRight className="ml-auto h-4 w-4" aria-hidden />
    </Link>
  );
}

/**
 * One line under the title: what this service served and wrote in the last
 * hour, from Drake's own telemetry. Silent when no store is configured or
 * the caller may read neither signal; says "unavailable" when the store did
 * not answer, never a reassuring zero.
 */
function TelemetryLine({ page }: { page: SignalPage | null }) {
  const t = useT("serviceHealth");
  if (!page) return null;
  if (page.logs.state === "not_configured" && page.traces.state === "not_configured") return null;
  if (page.logs.state === "unavailable" && page.traces.state === "unavailable") {
    return <span data-testid="telemetry-line">{t("detail.telemetry.unavailable")}</span>;
  }
  const item = page.items[0];
  if (!item) return null;
  const parts: React.ReactNode[] = [];
  const traces = item.traces;
  if (traces) {
    if (traces.requests === 0) {
      parts.push(t("detail.telemetry.noRequests"));
    } else {
      parts.push(t("detail.telemetry.requests", { count: traces.requests }));
      parts.push(
        <span key="failed" className={traces.errors > 0 ? "font-medium text-critical" : undefined}>
          {t("detail.telemetry.failed", { ratio: percent(traces.error_ratio) })}
        </span>,
      );
      parts.push(t("detail.telemetry.p95", { value: formatDuration(traces.p95_ms) }));
    }
  }
  if (item.logs) {
    parts.push(
      <span key="logs" className={item.logs.errors > 0 ? "font-medium text-warning" : undefined}>
        {t("detail.telemetry.errorLogs", { count: item.logs.errors })}
      </span>,
    );
  }
  if (parts.length === 0) return null;
  return (
    <span data-testid="telemetry-line" className="inline-flex flex-wrap items-center gap-x-1.5">
      <span>{t("detail.telemetry.lastHour")}</span>
      {parts.map((part, index) => (
        <span key={index} className="inline-flex items-center gap-x-1.5">
          <span aria-hidden>·</span>
          {part}
        </span>
      ))}
    </span>
  );
}

export default function ServiceHealthDetailPage() {
  const t = useT("serviceHealth");
  const tc = useT("common");
  const formatSignal = useSignalFormat();
  const age = useAge();
  const { bindingId } = useParams<{ bindingId: string }>();
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname() || `/service-health/${bindingId}`;
  const [health, retryHealth] = useApi<ServiceHealth>(
    `/v1/service-health/bindings/${bindingId}/health`,
  );
  const [summary] = useApi<MetricSummary>(
    `/v1/service-health/bindings/${bindingId}/metrics`,
  );
  const [range, setRange] = useState<SeriesRange>("1h");
  // This service's own telemetry over the last hour: what the header line
  // and the tab counts say. Absent or unreadable, they say nothing.
  const signals = useResource<SignalPage>(signalsPath([bindingId]), { refreshMs: 60_000 });
  const signal = signals.data?.items[0] ?? null;
  // A slice picked on the timeline narrows the request and log tabs.
  const [slice, setSlice] = useState<TimelineWindow | null>(null);
  const [incidentsPage] = useApi<{ items: IncidentSummary[]; total: number }>(
    `/v1/service-health/bindings/${bindingId}/incidents`,
  );
  const activeIncident =
    incidentsPage.state === "ready" ? (incidentsPage.data.items.find((i) => i.state !== "resolved") ?? null) : null;
  const requested = params.get("tab");
  // The tab is local state, mirrored into the URL so a link opens the same
  // view; the URL only seeds it.
  const [tab, setTabState] = useState<Tab>(
    (TABS as readonly string[]).includes(requested ?? "") ? (requested as Tab) : "overview",
  );
  const setTab = (next: Tab) => {
    setTabState(next);
    const query = new URLSearchParams(params.toString());
    if (next === "overview") query.delete("tab");
    else query.set("tab", next);
    const qs = query.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  };
  useCrumbLabel(bindingId, health.state === "ready" ? health.data.binding.service_key : null);

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
              <PageHeader
                title={binding.service_key}
                status={
                  <>
                    <HealthBadge status={data.status} />
                    {binding.lifecycle !== "active" || !binding.resolved ? (
                      <BindingStateBadge lifecycle={binding.lifecycle} resolved={binding.resolved} />
                    ) : null}
                  </>
                }
                meta={
                  <>
                    <span className="font-mono">
                      {binding.project_key}/{binding.environment_key}
                    </span>
                    <span className="font-mono">
                      {binding.workload_kind} {binding.cluster_ref}/{binding.namespace}/{binding.workload_name}
                    </span>
                    <span>
                      {t("detail.newestSample", { age: age(data.freshness_age_seconds) })}
                    </span>
                    <TelemetryLine page={signals.data} />
                  </>
                }
                actions={
                  <>
                    <button type="button" onClick={() => setTab("requests")} className={PILL_SECONDARY}>
                      <Route className="h-3.5 w-3.5" aria-hidden />
                      {t("detail.tabs.requests")}
                    </button>
                    <button type="button" onClick={() => setTab("logs")} className={PILL_SECONDARY}>
                      <ScrollText className="h-3.5 w-3.5" aria-hidden />
                      {t("detail.tabs.logs")}
                    </button>
                    <Link
                      href={`/service-health/bind?environment_service_id=${binding.environment_service_id}&binding_id=${binding.id}`}
                      className={PILL_SECONDARY}
                    >
                      <Pencil className="h-3.5 w-3.5" aria-hidden />
                      {t("detail.editBinding")}
                    </Link>
                  </>
                }
              />

              <div className="space-y-5">
                <ActiveIncidentBanner active={activeIncident} />
                <FreshnessNotice
                  partial={data.partial}
                  servedFromLastGood={data.served_from_last_good}
                  computedAt={data.computed_at}
                  servedAt={data.served_at}
                  newestSampleAt={data.newest_sample_at}
                />

                <DetailTabs
                  value={tab}
                  onChange={setTab}
                  requestErrors={signal?.traces?.errors ?? 0}
                  errorLines={signal?.logs?.errors ?? 0}
                  incidentOpen={activeIncident !== null}
                />

                {slice && (tab === "overview" || tab === "metrics") ? (
                  <div
                    data-testid="slice-bar"
                    className="flex flex-wrap items-center gap-3 rounded-panel border border-brand bg-brand-soft px-4 py-2.5 text-caption text-ink"
                  >
                    <span>
                      {t("detail.slice.picked", {
                        from: slice.start.slice(11, 16),
                        to: slice.end.slice(11, 16),
                      })}
                    </span>
                    <button type="button" onClick={() => setTab("requests")} className="font-semibold text-brand hover:underline">
                      {t("detail.slice.requests")}
                    </button>
                    <button type="button" onClick={() => setTab("logs")} className="font-semibold text-brand hover:underline">
                      {t("detail.slice.logs")}
                    </button>
                    <button type="button" onClick={() => setSlice(null)} className="ml-auto text-ink-muted hover:text-ink">
                      {t("detail.slice.clear")}
                    </button>
                  </div>
                ) : null}

                <div role="tabpanel" id={`sh-panel-${tab}`} aria-labelledby={`sh-tab-${tab}`} className="space-y-5">
                  {tab === "overview" ? (
                    <>
                      <div id="sh-overview" className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
                  <SectionCard
                    id="availability"
                    title={t("detail.section.availability")}
                    status={availability.status}
                    reasons={availability.reasons}
                  >
                    <div className="flex flex-1 flex-col items-center justify-center">
                      <Gauge
                        value={
                          availability.desired_replicas
                            ? (availability.ready_replicas ?? 0) / availability.desired_replicas
                            : null
                        }
                        unit="ratio"
                        tone={readyTone}
                        label={t("detail.availability.replicasReady")}
                        caption={
                          // Two exact numbers: a percentage alone would hide
                          // the difference between 0/0 and an unmeasured pair.
                          `${availability.ready_replicas ?? "—"} / ${availability.desired_replicas ?? "—"}`
                        }
                      />
                    </div>
                    {availability.scaled_to_zero ? (
                      <p className="rounded-control bg-surface-2 px-2.5 py-1 text-micro text-ink-secondary">
                        {t("detail.availability.scaledToZero")}
                      </p>
                    ) : null}
                  </SectionCard>

                  <SectionCard
                    id="stability"
                    title={t("detail.section.stability")}
                    status={stability.status}
                    reasons={stability.reasons}
                  >
                    {/* Restarts have no ceiling, so no gauge: a count, and the two
                        failure modes as tiles that light up when they happen. */}
                    <div className="flex flex-1 items-center">
                    <div className="grid w-full grid-cols-3 gap-2">
                      <span
                        className={`flex flex-col items-center justify-center gap-1 rounded-control border px-2 py-3 text-center ${
                          (stability.restarts_in_window ?? 0) > 0
                            ? "border-warning/40 bg-warning-soft"
                            : "border-border bg-surface-2"
                        }`}
                      >
                        <RotateCcw aria-hidden className="h-4 w-4 text-ink-muted" />
                        <span data-tabular className="text-[1.5rem] leading-none font-semibold text-ink">
                          {stability.restarts_in_window ?? "—"}
                        </span>
                        <span className="text-micro leading-tight text-ink-muted">
                          {t("detail.stability.restartsInWindow")}
                        </span>
                      </span>
                      {(
                        [
                          [t("detail.stability.crashLoop"), stability.crash_looping, Repeat],
                          [t("detail.stability.oomKilled"), stability.oom_killed, MemoryStick],
                        ] as const
                      ).map(([label, flagged, Icon]) => (
                        <span
                          key={label}
                          className={`flex flex-col items-center justify-center gap-1 rounded-control border px-2 py-3 text-center ${
                            flagged ? `${toneSpec("critical").chip}` : "border-border bg-surface-2"
                          }`}
                        >
                          <Icon aria-hidden className={`h-4 w-4 ${flagged ? "" : "text-ink-muted"}`} />
                          <span className={`text-caption font-semibold ${flagged ? "" : "text-healthy"}`}>
                            {flagged ? tc("state.yes") : tc("state.no")}
                          </span>
                          <span className="text-micro leading-tight text-ink-muted">{label}</span>
                        </span>
                      ))}
                    </div>
                    </div>
                  </SectionCard>

                  <SectionCard
                    id="resources"
                    title={t("detail.section.resources")}
                    status={resources.status}
                    reasons={resources.reasons}
                  >
                    <div className="grid grid-cols-2 gap-1">
                      <Gauge
                        size="compact"
                        value={resources.cpu_utilization}
                        unit="ratio"
                        thresholds={utilisation}
                        label={t("detail.resources.cpu")}
                        caption={
                          resources.cpu_limit_cores !== null
                            ? t("detail.resources.ofLimit", {
                                used: formatSignal(resources.cpu_cores_used, "cores"),
                                limit: formatSignal(resources.cpu_limit_cores, "cores"),
                              })
                            : `${formatSignal(resources.cpu_cores_used, "cores")} · ${t("detail.resources.noLimit")}`
                        }
                      />
                      <Gauge
                        size="compact"
                        value={resources.memory_utilization}
                        unit="ratio"
                        thresholds={utilisation}
                        label={t("detail.resources.memory")}
                        caption={
                          resources.memory_limit_bytes !== null
                            ? t("detail.resources.ofLimit", {
                                used: formatSignal(resources.memory_bytes_used, "bytes"),
                                limit: formatSignal(resources.memory_limit_bytes, "bytes"),
                              })
                            : `${formatSignal(resources.memory_bytes_used, "bytes")} · ${t("detail.resources.noLimit")}`
                        }
                      />
                    </div>
                    <div className="border-t border-border pt-3">
                      <UsageLine
                        label={t("detail.pressure.throttling")}
                        used={null}
                        limit={null}
                        unit="ratio"
                        ratio={resources.cpu_throttled_ratio}
                        thresholds={THROTTLE_THRESHOLDS}
                        caption={t("detail.pressure.throttledPeriods")}
                      />
                    </div>
                  </SectionCard>

                  <SectionCard
                    id="application"
                    title={t("detail.section.application")}
                    status={application.status}
                    reasons={application.reasons}
                  >
                    {application.metrics_present ? (
                      <>
                        <div className="flex flex-col items-center">
                          {/* The share that succeeded, so the gauge shows the
                              real number; the bands mark 99% and 95%. */}
                          <Gauge
                            size="compact"
                            value={application.error_ratio === null ? null : 1 - application.error_ratio}
                            unit="ratio"
                            thresholds={{ warn: 0.99, critical: 0.95, direction: "below" }}
                            label={t("detail.application.successRatio")}
                            caption={`${t("detail.application.errorRatio")} ${formatSignal(application.error_ratio, "ratio")}`}
                          />
                          {application.source === "traces" ? (
                            <span
                              data-testid="application-source"
                              className="mt-1 block text-center text-micro text-ink-muted"
                            >
                              {t("detail.application.fromTraces")}
                            </span>
                          ) : null}
                        </div>
                        <div className="grid grid-cols-1 gap-2">
                          <span className="flex items-center justify-between gap-2 rounded-control bg-surface-2 px-2.5 py-1 text-caption">
                            <span className="text-ink-secondary">
                              {t("detail.application.requestRate")}
                            </span>
                            <Measure
                              value={application.request_rate}
                              unit="requests_per_second"
                            />
                          </span>
                          <span className="flex items-center justify-between gap-2 rounded-control bg-surface-2 px-2.5 py-1 text-caption">
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
                      <div className="rounded-[0.625rem] border border-dashed border-border px-4 py-3.5">
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

                      <div id="sh-timeline">
                        <TimelineStrip
                          bindingId={binding.id}
                          environmentServiceId={binding.environment_service_id}
                          selected={slice}
                          onSelect={setSlice}
                        />
                      </div>

                      <div id="sh-why">
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
                      <div className="flex items-center gap-3 rounded-[0.625rem] bg-surface-2 px-4 py-3">
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

                      </div>

                      <div id="sh-map">
                        <BindingServiceMap bindingId={binding.id} fallbackLabel={binding.service_key} />
                      </div>
                    </>
                  ) : null}

                  {tab === "requests" ? (
                    <div id="sh-requests">
                      <TraceExplorer
                        bindingId={binding.id}
                        slice={slice}
                        onClearSlice={() => setSlice(null)}
                      />
                    </div>
                  ) : null}

                  {tab === "logs" ? (
                    <div id="sh-logs">
                      <LogExplorer
                        bindingId={binding.id}
                        slice={slice}
                        onClearSlice={() => setSlice(null)}
                      />
                    </div>
                  ) : null}

                  {tab === "metrics" ? (
                    <>
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
                      <div className="flex justify-center rounded-[0.625rem] bg-surface-2 px-2 py-4">
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
                      <div className="flex justify-center rounded-[0.625rem] bg-surface-2 px-2 py-4">
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
                      <div className="flex justify-center rounded-[0.625rem] bg-surface-2 px-2 py-4">
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
                    <section id="sh-history" aria-label={t("detail.history.title")} className="space-y-4">
                      <div className="flex flex-wrap items-center justify-between gap-3">
                        <div>
                          <h2 className="text-[1rem] leading-6 font-bold tracking-[-0.01em] text-ink">
                            {t("detail.history.title")}
                          </h2>
                          <p className="text-caption text-ink-muted">{t("detail.history.description")}</p>
                        </div>
                        {chartable.length > 0 ? <RangeSelector value={range} onChange={setRange} /> : null}
                      </div>
                      {chartable.length > 0 ? (
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
                      ) : (
                        <Panel>
                          <DataState kind="no-data" title={t("detail.history.none")} />
                        </Panel>
                      )}
                    </section>
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
                    </>
                  ) : null}

                  {tab === "incidents" ? (
                    <div id="sh-incidents" className="grid grid-cols-1 items-stretch gap-5 lg:grid-cols-2">
                      <ServiceIncidents bindingId={binding.id} />
                      <HealthTransitions bindingId={binding.id} />
                    </div>
                  ) : null}

                  {tab === "setup" ? (
                    <div id="sh-setup">
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

                    </div>
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

/** This service, what it calls and what calls it — from its environment's traces. */
function BindingServiceMap({ bindingId, fallbackLabel }: { bindingId: string; fallbackLabel: string }) {
  const { environmentId, label, resolved } = useBindingEnvironment(bindingId);
  // Outside the reader's service list there is no environment to read.
  if (resolved && !environmentId) return null;
  return (
    <ServiceMapPanel
      environmentId={environmentId}
      focusBindingId={bindingId}
      focusLabel={label ?? fallbackLabel}
      compact
    />
  );
}
