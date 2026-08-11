"use client";

/**
 * Service health detail.
 *
 * Shows the verdict, the four sections behind it, and why each one says
 * what it says. Every number comes from the API and every status is the
 * API's; this screen never adds one up.
 */

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { PageFrame } from "@/components/shell/AppShell";

import { LoadGate, MetaRow, useApi } from "@/components/catalog/primitives";
import {
  HealthTransitions,
  ServiceIncidents,
} from "@/components/incidents/ServiceIncidents";
import { RangeSelector, SignalChart } from "@/components/service-health/SignalChart";
import {
  BindingStateBadge,
  FreshnessNotice,
  HealthBadge,
  Measure,
  MissingSignals,
  ReasonList,
  SignalCell,
} from "@/components/service-health/primitives";
import { Gauge, RingProgress, ToneCounters } from "@/components/charts/visuals";
import { DataState } from "@/components/state/DataState";
import { Card } from "@/components/ui/Card";
import {
  SIGNAL_LABELS,
  formatAge,
  type MetricSummary,
  type SeriesRange,
  type ServiceHealth,
} from "@/lib/serviceHealth";

const CHARTABLE: { signal: string; unit: string }[] = [
  { signal: "ready_replicas", unit: "count" },
  { signal: "restarts", unit: "count" },
  { signal: "cpu_usage", unit: "cores" },
  { signal: "memory_usage", unit: "bytes" },
  { signal: "error_ratio", unit: "ratio" },
  { signal: "latency_p95", unit: "seconds" },
];

function SectionCard({
  title,
  status,
  reasons,
  children,
}: {
  title: string;
  status: ServiceHealth["availability"]["status"];
  reasons: string[];
  children: React.ReactNode;
}) {
  return (
    <Card data-testid={`section-${title.toLowerCase()}`}>
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
        <HealthBadge status={status} />
      </div>
      <dl className="divide-y divide-border">{children}</dl>
      {reasons.length > 0 ? (
        <div className="mt-3 border-t border-border pt-3">
          <ReasonList reasons={reasons} />
        </div>
      ) : null}
    </Card>
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
const THROTTLE_THRESHOLDS = { warn: 0.05, critical: 0.25, direction: "above" as const };

export default function ServiceHealthDetailPage() {
  const { bindingId } = useParams<{ bindingId: string }>();
  const [health, retryHealth] = useApi<ServiceHealth>(
    `/v1/service-health/bindings/${bindingId}/health`,
  );
  const [summary] = useApi<MetricSummary>(`/v1/service-health/bindings/${bindingId}/metrics`);
  const [range, setRange] = useState<SeriesRange>("1h");

  return (
    <PageFrame>
      <div className="space-y-5">
      <LoadGate value={health} retry={retryHealth}>
        {(data) => {
          const binding = data.binding;
          const chartable =
            summary.state === "ready"
              ? CHARTABLE.filter((entry) =>
                  summary.data.readable_signals.includes(entry.signal),
                )
              : [];
          return (
            <>
              <div className="flex flex-wrap items-end justify-between gap-3">
                <div>
                  <p className="text-xs text-ink-muted">
                    <Link href="/service-health" className="hover:text-ink">
                      Service health
                    </Link>{" "}
                    / <span className="font-mono">{binding.project_key}</span> /{" "}
                    <span className="font-mono">{binding.environment_key}</span> /{" "}
                    <span className="font-mono">{binding.service_key}</span>
                  </p>
                  <h1 className="mt-1 text-title font-semibold text-ink">
                    {binding.service_key}
                  </h1>
                </div>
                <div className="flex items-center gap-2">
                  <BindingStateBadge
                    lifecycle={binding.lifecycle}
                    resolved={binding.resolved}
                  />
                  <HealthBadge status={data.status} />
                </div>
              </div>

              <FreshnessNotice
                partial={data.partial}
                servedFromLastGood={data.served_from_last_good}
                computedAt={data.computed_at}
                servedAt={data.served_at}
                newestSampleAt={data.newest_sample_at}
              />

              <Card title="Why">
                {data.reasons.length === 0 ? (
                  <p className="text-sm text-ink-secondary">
                    Every signal this policy reads is within its thresholds.
                  </p>
                ) : (
                  <ReasonList reasons={data.reasons} messages={data.messages} />
                )}
                <div className="mt-3 space-y-1 border-t border-border pt-3">
                  <MissingSignals missing={data.missing_signals} />
                  <p className="text-[11px] text-ink-muted">
                    Computed <time className="font-mono">{data.computed_at}</time> · newest
                    sample {formatAge(data.freshness_age_seconds)} · policy{" "}
                    <span className="font-mono">{data.policy_key}</span>
                    {data.cached ? " · from cache" : null}
                  </p>
                </div>
              </Card>

              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                <SectionCard
                  title="Availability"
                  status={data.availability.status}
                  reasons={data.availability.reasons}
                >
                  <div className="flex items-center gap-3 py-1">
                    <RingProgress
                      size={52}
                      label="Replicas ready"
                      value={
                        data.availability.desired_replicas
                          ? ((data.availability.ready_replicas ?? 0) /
                              data.availability.desired_replicas) *
                            100
                          : null
                      }
                      tone={
                        data.availability.ready_replicas === null ||
                        data.availability.desired_replicas === null
                          ? "unknown"
                          : data.availability.ready_replicas >= data.availability.desired_replicas
                            ? "success"
                            : data.availability.ready_replicas === 0
                              ? "critical"
                              : "warning"
                      }
                    />
                    <span>
                      <span data-tabular className="block text-title font-semibold text-ink">
                        {data.availability.ready_replicas ?? "—"}
                        <span className="text-ink-muted">
                          /{data.availability.desired_replicas ?? "—"}
                        </span>
                      </span>
                      <span className="text-caption text-ink-secondary">replicas ready</span>
                    </span>
                  </div>
                  {data.availability.scaled_to_zero ? (
                    <MetaRow label="Scaled to zero">
                      <span className="text-xs text-ink-secondary">
                        Deliberately not running
                      </span>
                    </MetaRow>
                  ) : null}
                </SectionCard>

                <SectionCard
                  title="Stability"
                  status={data.stability.status}
                  reasons={data.stability.reasons}
                >
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-2 py-1">
                    <span>
                      <span data-tabular className="block text-title font-semibold text-ink">
                        {data.stability.restarts_in_window ?? "—"}
                      </span>
                      <span className="text-caption text-ink-secondary">restarts in window</span>
                    </span>
                    <ToneCounters
                      size="compact"
                      items={[
                        {
                          label: "crash-looping",
                          count: data.stability.crash_looping ? 1 : 0,
                          tone: "critical",
                        },
                        {
                          label: "OOM-killed",
                          count: data.stability.oom_killed ? 1 : 0,
                          tone: "critical",
                        },
                      ]}
                    />
                  </div>
                </SectionCard>

                <SectionCard
                  title="Resources"
                  status={data.resources.status}
                  reasons={data.resources.reasons}
                >
                  {/* Utilisation against a limit is exactly what a gauge is
                      for: bounded, and the distance to the limit is the
                      point. The bands only appear when limits are configured
                      — without them Drake reports usage and judges nothing,
                      so a coloured zone would be an invented promise. */}
                  <div className="flex flex-wrap justify-around gap-2 py-1">
                    <Gauge
                      size="compact"
                      label="CPU"
                      unit="ratio"
                      value={data.resources.cpu_utilization}
                      thresholds={UTILISATION_THRESHOLDS(data.resources.limits_configured)}
                      missingReason="not measured"
                      caption={
                        data.resources.cpu_limit_cores !== null ? (
                          <>
                            <Measure value={data.resources.cpu_cores_used} unit="cores" /> of{" "}
                            <Measure value={data.resources.cpu_limit_cores} unit="cores" />
                          </>
                        ) : (
                          "no limit set"
                        )
                      }
                    />
                    <Gauge
                      size="compact"
                      label="Memory"
                      unit="ratio"
                      value={data.resources.memory_utilization}
                      thresholds={UTILISATION_THRESHOLDS(data.resources.limits_configured)}
                      missingReason="not measured"
                      caption={
                        data.resources.memory_limit_bytes !== null ? (
                          <>
                            <Measure value={data.resources.memory_bytes_used} unit="bytes" /> of{" "}
                            <Measure value={data.resources.memory_limit_bytes} unit="bytes" />
                          </>
                        ) : (
                          "no limit set"
                        )
                      }
                    />
                    <Gauge
                      size="compact"
                      label="CPU throttling"
                      unit="ratio"
                      value={data.resources.cpu_throttled_ratio}
                      thresholds={THROTTLE_THRESHOLDS}
                      missingReason="not measured"
                      caption="share of periods throttled"
                    />
                  </div>
                  {data.resources.limits_configured === false ? (
                    <p className="text-caption text-ink-muted italic">
                      No limits configured — usage is reported, pressure is not judged, and the
                      gauges carry no threshold bands.
                    </p>
                  ) : null}
                </SectionCard>

                <SectionCard
                  title="Application"
                  status={data.application.status}
                  reasons={data.application.reasons}
                >
                  {data.application.metrics_present ? (
                    <>
                      <MetaRow label="Request rate">
                        <Measure
                          value={data.application.request_rate}
                          unit="requests_per_second"
                        />
                      </MetaRow>
                      <MetaRow label="Error ratio">
                        <Measure value={data.application.error_ratio} unit="ratio" />
                      </MetaRow>
                      <MetaRow label="Latency (p95)">
                        <Measure
                          value={data.application.latency_p95_seconds}
                          unit="seconds"
                        />
                      </MetaRow>
                    </>
                  ) : (
                    <DataState
                      kind="not-configured"
                      title="No application metrics"
                      description="This service publishes no request metrics, so golden signals are unavailable. That is not counted against its health."
                    />
                  )}
                </SectionCard>
              </div>

              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                <ServiceIncidents bindingId={binding.id} />
                <HealthTransitions bindingId={binding.id} />
              </div>

              <Card title="Binding">
                <dl className="divide-y divide-border">
                  <MetaRow label="Workload">
                    <span className="font-mono text-xs">
                      {binding.cluster_ref}/{binding.namespace}/{binding.workload_kind}/
                      {binding.workload_name}
                    </span>
                  </MetaRow>
                  <MetaRow label="Metric preset">
                    <span className="font-mono text-xs">{binding.preset_key}</span>
                  </MetaRow>
                  <MetaRow label="Health policy">
                    <span className="font-mono text-xs">{binding.health_policy_key}</span>
                  </MetaRow>
                  <MetaRow label="Datasource">
                    <span className="text-xs">
                      {binding.datasource_configured ? "Configured" : "Not configured"}
                    </span>
                  </MetaRow>
                  <MetaRow label="Resolved">
                    <span className="text-xs">
                      {binding.resolved
                        ? binding.resolved_at ?? "yes"
                        : "not seen in cluster inventory"}
                    </span>
                  </MetaRow>
                </dl>
                <div className="mt-3 border-t border-border pt-3">
                  <Link
                    href={`/service-health/bind?environment_service_id=${binding.environment_service_id}&binding_id=${binding.id}`}
                    className="text-xs font-medium text-ink-secondary underline hover:text-ink"
                  >
                    Edit binding
                  </Link>
                </div>
              </Card>

              {summary.state === "ready" ? (
                <Card title="Signals">
                  <dl className="divide-y divide-border">
                    <MetaRow label={SIGNAL_LABELS.desired_replicas}>
                      <SignalCell
                        signal={summary.data.metrics.availability.desired_replicas}
                        unit="count"
                      />
                    </MetaRow>
                    <MetaRow label={SIGNAL_LABELS.ready_replicas}>
                      <SignalCell
                        signal={summary.data.metrics.availability.ready_replicas}
                        unit="count"
                      />
                    </MetaRow>
                    <MetaRow label={SIGNAL_LABELS.restarts}>
                      <SignalCell signal={summary.data.metrics.stability.restarts} unit="count" />
                    </MetaRow>
                    <MetaRow label={SIGNAL_LABELS.cpu_usage}>
                      <SignalCell signal={summary.data.metrics.resources.cpu_usage} unit="cores" />
                    </MetaRow>
                    <MetaRow label={SIGNAL_LABELS.memory_usage}>
                      <SignalCell
                        signal={summary.data.metrics.resources.memory_usage}
                        unit="bytes"
                      />
                    </MetaRow>
                    <MetaRow label={SIGNAL_LABELS.request_rate}>
                      <SignalCell
                        signal={summary.data.metrics.application.request_rate}
                        unit="requests_per_second"
                      />
                    </MetaRow>
                    <MetaRow label={SIGNAL_LABELS.error_ratio}>
                      <SignalCell
                        signal={summary.data.metrics.application.error_ratio}
                        unit="ratio"
                      />
                    </MetaRow>
                    <MetaRow label={SIGNAL_LABELS.freshness}>
                      <SignalCell signal={summary.data.metrics.freshness} unit="ratio" />
                    </MetaRow>
                  </dl>
                </Card>
              ) : null}

              {chartable.length > 0 ? (
                <section aria-label="Signal history" className="space-y-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <h2 className="text-sm font-semibold text-ink">Signal history</h2>
                    <RangeSelector value={range} onChange={setRange} />
                  </div>
                  <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                    {chartable.map((entry) => (
                      <Card
                        key={entry.signal}
                        title={SIGNAL_LABELS[entry.signal] ?? entry.signal}
                      >
                        <SignalChart
                          bindingId={binding.id}
                          signal={entry.signal}
                          unit={entry.unit}
                          range={range}
                        />
                      </Card>
                    ))}
                  </div>
                </section>
              ) : null}
            </>
          );
        }}
      </LoadGate>
      </div>
    </PageFrame>
  );
}
