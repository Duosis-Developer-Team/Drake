"use client";

/**
 * A project's environments as cards: one card per environment, its health
 * composition drawn as a bar, and every service inside it as its own tile —
 * worst first, exactly as the topology view model ordered them.
 *
 * The same honesty rules as the lane view this replaces: an environment with
 * no observed services is "Unassessed", never healthy; an external runtime is
 * "Not applicable", which is a different answer from unknown; and a service
 * with no binding still gets its own tile rather than disappearing.
 */

import { ArrowUpRight, Boxes, GitBranch, Globe, Layers, Server } from "lucide-react";
import Link from "next/link";

import {
  FactPill,
  IconBubble,
  TileState,
  ToneBar,
  capabilityLabel,
  capabilityTone,
  type ToneCount,
} from "@/components/catalog/visuals";
import { StatusBadge, ToneAvatar } from "@/components/ui/StatusBadge";
import type { Environment } from "@/lib/catalog";
import { humanize, toneSpec, type StatusTone } from "@/lib/design/status";
import type { EnvironmentLaneModel, ServiceLaneModel } from "@/lib/view-models/scope-health";

const EVIDENCE_LABEL: Record<Exclude<EnvironmentLaneModel["evidence"], "complete">, string> = {
  unassessed: "Unassessed",
  incomplete: "Evidence incomplete",
  "not-applicable": "Not applicable",
};

const ENV_CAPABILITY_LABELS: Record<string, string> = {
  workloads: "Workloads",
  targets: "Targets",
  quotas: "Quotas",
  drift: "Drift",
};

/** Service tones folded into ordered counts, worst first. */
export function toneCounts<T>(items: T[], toneOf: (item: T) => StatusTone): ToneCount[] {
  const map = new Map<StatusTone, number>();
  for (const item of items) map.set(toneOf(item), (map.get(toneOf(item)) ?? 0) + 1);
  const order: StatusTone[] = [
    "critical",
    "warning",
    "stale",
    "pending",
    "unknown",
    "info",
    "neutral",
    "not-applicable",
    "denied",
    "success",
  ];
  return order
    .filter((tone) => map.has(tone))
    .map((tone) => ({ tone, label: toneSpec(tone).label, count: map.get(tone) ?? 0 }));
}

function Meter({ label, fraction, text }: { label: string; fraction: number | null; text: string }) {
  return (
    <div className="min-w-0">
      <div className="flex items-baseline justify-between gap-2 text-micro">
        <span className="text-ink-muted">{label}</span>
        <span data-tabular className="font-medium text-ink">
          {text}
        </span>
      </div>
      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-surface-3">
        {fraction !== null ? (
          <span
            className="block h-full rounded-full bg-ink-secondary/70"
            style={{ width: `${Math.max(0, Math.min(1, fraction)) * 100}%` }}
          />
        ) : null}
      </div>
    </div>
  );
}

function ServiceMeasurements({ service }: { service: ServiceLaneModel }) {
  const { ready, desired, restarts, cpu, memory } = service.measurements;
  const meters: React.ReactNode[] = [];
  if (ready !== null && desired !== null) {
    meters.push(
      <Meter
        key="replicas"
        label="Replicas"
        fraction={desired > 0 ? ready / desired : null}
        text={`${ready}/${desired}`}
      />,
    );
  }
  if (cpu !== null) {
    meters.push(<Meter key="cpu" label="CPU" fraction={cpu} text={`${Math.round(cpu * 100)}%`} />);
  }
  if (memory !== null) {
    meters.push(
      <Meter key="memory" label="Memory" fraction={memory} text={`${Math.round(memory * 100)}%`} />,
    );
  }
  if (meters.length === 0 && restarts === null) return null;
  return (
    <div className="mt-3 space-y-2 border-t border-border pt-3">
      {meters.length > 0 ? <div className="grid grid-cols-1 gap-2">{meters}</div> : null}
      {restarts !== null ? (
        <p className="text-micro text-ink-muted">
          <span data-tabular className="font-semibold text-ink">
            {restarts}
          </span>{" "}
          restart{restarts === 1 ? "" : "s"} in window
        </p>
      ) : null}
    </div>
  );
}

export function ServiceTile({ service }: { service: ServiceLaneModel }) {
  return (
    <li className="min-w-0">
      <Link
        href={service.href}
        className="group flex h-full min-w-0 flex-col rounded-[1.125rem] border border-border bg-surface-2/40 p-4 transition-colors hover:border-border-strong hover:bg-surface-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        <span className="flex min-w-0 items-start gap-3">
          <ToneAvatar status={service.tone} />
          <span className="min-w-0 flex-1">
            <span className="block truncate text-body font-semibold text-ink">
              {service.displayName}
            </span>
            {service.binding ? (
              <span className="block truncate font-mono text-micro text-ink-muted">
                {service.binding.workload_kind.toLowerCase()}/{service.binding.workload_name}
              </span>
            ) : (
              <span className="block text-micro text-ink-muted">Not bound to a workload</span>
            )}
          </span>
          <ArrowUpRight
            aria-hidden
            className="h-4 w-4 shrink-0 text-ink-muted opacity-0 transition-opacity group-hover:opacity-100"
          />
        </span>
        <span className="mt-3 flex flex-wrap items-center gap-1.5">
          <StatusBadge status={service.tone} label={service.statusLabel} size="compact" />
          {service.partial ? (
            <span className="rounded-full border border-border px-2 py-0.5 text-micro text-ink-muted italic">
              partial
            </span>
          ) : null}
        </span>
        <ServiceMeasurements service={service} />
      </Link>
    </li>
  );
}

export function EnvironmentCard({
  lane,
  environment,
}: {
  lane: EnvironmentLaneModel;
  environment?: Environment;
}) {
  const external = lane.runtime === "external";
  const counts = toneCounts(lane.services, (service) => service.tone);
  const capabilities = environment?.operational
    ? Object.entries(ENV_CAPABILITY_LABELS).filter(([key]) => environment.operational?.[key])
    : [];

  return (
    <section
      data-testid={`environment-lane-${lane.id}`}
      className="flex h-full min-w-0 flex-col rounded-[1.5rem] border border-border bg-surface shadow-panel"
    >
      <div className="flex items-start gap-4 px-6 pt-6">
        <IconBubble icon={external ? Globe : Layers} tone={lane.tone} size="large" />
        <div className="min-w-0 flex-1">
          <Link href={lane.href} className="block min-w-0 rounded">
            <h3 className="truncate text-[1.375rem] leading-7 font-semibold tracking-[-0.02em] text-ink hover:text-brand">
              {lane.key}
            </h3>
          </Link>
          <span className="mt-0.5 flex min-w-0 items-center gap-1.5 text-micro text-ink-muted">
            {external ? (
              <span>External runtime</span>
            ) : (
              <>
                <Server aria-hidden className="h-3 w-3 shrink-0" />
                <span className="truncate font-mono">{lane.placement}</span>
              </>
            )}
          </span>
        </div>
        {lane.evidence !== "complete" ? (
          <StatusBadge
            status={lane.tone}
            label={EVIDENCE_LABEL[lane.evidence]}
            size="compact"
          />
        ) : (
          <StatusBadge status={lane.tone} label={toneSpec(lane.tone).label} size="compact" />
        )}
      </div>

      <div className="px-6 pt-5">
        <div className="flex items-baseline justify-between gap-3">
          <p className="flex items-baseline gap-2">
            <span data-tabular className="text-[1.75rem] leading-none font-semibold tracking-[-0.03em] text-ink">
              {lane.services.length}
            </span>
            <span className="text-caption text-ink-muted">
              service{lane.services.length === 1 ? "" : "s"} observed
            </span>
          </p>
        </div>
        <div className="mt-3">
          <ToneBar
            counts={counts}
            label={`${lane.key} service health`}
            emptyLabel={external ? "No Kubernetes health applies here" : "No service evidence yet"}
          />
        </div>
      </div>

      <div className="flex-1 px-6 pt-5">
        {lane.services.length === 0 ? (
          <div className="rounded-[1.125rem] border border-dashed border-border px-4 py-4">
            <TileState
              icon={Boxes}
              title="No services observed"
              description="Service tiles appear once a service here is bound to a workload."
            />
          </div>
        ) : (
          <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2" data-testid="service-lane-list">
            {lane.services.map((service) => (
              <ServiceTile key={service.id} service={service} />
            ))}
          </ul>
        )}
      </div>

      <div className="mt-6 flex flex-wrap items-center gap-2 border-t border-border px-6 py-4">
        {environment ? (
          <>
            <FactPill icon={GitBranch}>{`branch ${environment.branch || "—"}`}</FactPill>
            {/* Deliberately "Criticality · High", not "High criticality": the
                project's own criticality chip in the page header owns that
                phrase, and an environment's is a separate record. */}
            <FactPill>{`Criticality · ${humanize(environment.criticality)}`}</FactPill>
          </>
        ) : null}
        {capabilities.map(([key, label]) => {
          const state = environment?.operational?.[key] ?? "unknown";
          return (
            <span
              key={key}
              title={`${label}: ${capabilityLabel(state)}`}
              className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-micro ${toneSpec(capabilityTone(state)).chip}`}
            >
              {label}
            </span>
          );
        })}
        <Link
          href={lane.href}
          className="ml-auto inline-flex items-center gap-1 rounded-full border border-border px-3 py-1.5 text-caption font-medium text-ink transition-colors hover:bg-surface-hover"
        >
          Open environment
          <ArrowUpRight aria-hidden className="h-3.5 w-3.5" />
        </Link>
      </div>
    </section>
  );
}
