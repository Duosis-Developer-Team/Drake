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

export default function ServiceHealthDetailPage() {
  const { bindingId } = useParams<{ bindingId: string }>();
  const [health, retryHealth] = useApi<ServiceHealth>(
    `/v1/service-health/bindings/${bindingId}/health`,
  );
  const [summary] = useApi<MetricSummary>(`/v1/service-health/bindings/${bindingId}/metrics`);
  const [range, setRange] = useState<SeriesRange>("1h");

  return (
    <div className="mx-auto max-w-6xl space-y-5">
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
                  <h1 className="mt-1 text-xl font-semibold tracking-tight text-ink">
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
                  <MetaRow label="Ready / desired">
                    <span className="font-mono text-xs">
                      {data.availability.ready_replicas ?? "—"} /{" "}
                      {data.availability.desired_replicas ?? "—"}
                    </span>
                  </MetaRow>
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
                  <MetaRow label="Restarts in window">
                    <Measure value={data.stability.restarts_in_window} unit="count" />
                  </MetaRow>
                  <MetaRow label="Crash looping">
                    <span className="text-xs">{data.stability.crash_looping ? "Yes" : "No"}</span>
                  </MetaRow>
                  <MetaRow label="OOM killed">
                    <span className="text-xs">{data.stability.oom_killed ? "Yes" : "No"}</span>
                  </MetaRow>
                </SectionCard>

                <SectionCard
                  title="Resources"
                  status={data.resources.status}
                  reasons={data.resources.reasons}
                >
                  <MetaRow label="CPU">
                    <span className="font-mono text-xs">
                      <Measure value={data.resources.cpu_cores_used} unit="cores" /> /{" "}
                      <Measure value={data.resources.cpu_limit_cores} unit="cores" />
                    </span>
                  </MetaRow>
                  <MetaRow label="CPU utilization">
                    <Measure value={data.resources.cpu_utilization} unit="ratio" />
                  </MetaRow>
                  <MetaRow label="Memory">
                    <span className="font-mono text-xs">
                      <Measure value={data.resources.memory_bytes_used} unit="bytes" /> /{" "}
                      <Measure value={data.resources.memory_limit_bytes} unit="bytes" />
                    </span>
                  </MetaRow>
                  <MetaRow label="Memory utilization">
                    <Measure value={data.resources.memory_utilization} unit="ratio" />
                  </MetaRow>
                  <MetaRow label="CPU throttling">
                    <Measure value={data.resources.cpu_throttled_ratio} unit="ratio" />
                  </MetaRow>
                  {data.resources.limits_configured === false ? (
                    <MetaRow label="Limits">
                      <span className="text-xs italic text-ink-muted">
                        none configured — usage is reported, pressure is not judged
                      </span>
                    </MetaRow>
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
  );
}
