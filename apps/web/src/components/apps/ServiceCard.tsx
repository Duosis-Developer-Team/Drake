"use client";

/**
 * One service as a card: its state, its traffic over the day, the last
 * hour's requests, failures and p95 from its traces, and the workload's own
 * readiness and pressure. A dash is "not measured", never zero; a service
 * with no workload bound says so and offers to bind one.
 */

import { ArrowUpRight, Plus } from "lucide-react";
import Link from "next/link";

import { HealthBadge } from "@/components/service-health/primitives";
import { useFormat, useT } from "@/lib/i18n";
import type { ServiceHealthRow } from "@/lib/serviceHealth";
import type { SignalItem } from "@/lib/signals";

import { Sparkline } from "./AppLauncher";

function Meter({ label, ratio }: { label: string; ratio: number | null }) {
  const fmt = useFormat();
  const tone =
    ratio === null ? "bg-surface-3" : ratio >= 0.9 ? "bg-critical" : ratio >= 0.8 ? "bg-warning" : "bg-brand-accent";
  return (
    <div className="min-w-0">
      <div className="flex items-baseline justify-between gap-2 text-[11px] text-ink-muted">
        <span>{label}</span>
        <span data-tabular className="font-mono text-ink-secondary">
          {ratio === null ? "—" : `${fmt.number(ratio * 100, { maximumFractionDigits: 0 })}%`}
        </span>
      </div>
      <span className="mt-1 block h-1 overflow-hidden rounded-full bg-surface-3">
        <span className={`block h-full rounded-full ${tone}`} style={{ width: `${Math.min(1, ratio ?? 0) * 100}%` }} />
      </span>
    </div>
  );
}

export function ServiceCard({
  row,
  item,
  traffic,
  index = 0,
}: {
  row: ServiceHealthRow;
  item: SignalItem | undefined;
  /** Requests per bucket over the day, when the overview read answered. */
  traffic: number[] | null;
  index?: number;
}) {
  const t = useT("apps");
  const fmt = useFormat();
  const { binding, health } = row;
  const name = row.display_name || row.service_key;
  const traces = item?.traces ?? null;
  const p95 = traces?.p95_ms ?? null;
  const failed = traces && traces.requests > 0 ? traces.error_ratio : null;
  const ready = health.availability?.ready_replicas ?? null;
  const desired = health.availability?.desired_replicas ?? null;
  const critical = health.status === "critical";

  return (
    <li
      data-testid={`service-card-${row.service_key}`}
      style={{ animationDelay: `${index * 35}ms` }}
      className={`group relative flex animate-[rise-in_.4s_cubic-bezier(.2,.8,.2,1)_both] flex-col rounded-2xl border p-4 transition-[transform,border-color,box-shadow] duration-200 ${
        binding
          ? `bg-surface hover:-translate-y-0.5 hover:border-border-strong hover:shadow-[0_16px_36px_-24px_rgb(0_0_0/0.45)] ${critical ? "border-critical/50" : "border-border"}`
          : "self-start border-dashed border-border-strong bg-transparent"
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          {binding ? (
            <Link
              href={`/service-health/${binding.id}`}
              className="block truncate text-[0.9375rem] font-semibold tracking-[-0.01em] text-ink outline-none after:absolute after:inset-0 after:rounded-2xl focus-visible:after:ring-2 focus-visible:after:ring-focus"
            >
              {name}
            </Link>
          ) : (
            <p className="truncate text-[0.9375rem] font-semibold text-ink">{name}</p>
          )}
          <p className="mt-0.5 truncate font-mono text-[11px] text-ink-muted">
            {binding ? `${binding.workload_kind} ${binding.workload_name}` : t("unbound")}
          </p>
        </div>
        <HealthBadge status={health.status} />
      </div>

      {binding ? (
        <>
          <div className="relative mt-3 -mx-1">
            {traffic && traffic.some((v) => v > 0) ? (
              <Sparkline values={traffic} height={36} />
            ) : (
              <div className="grid h-9 place-items-center text-[11px] text-ink-muted">{t("noTraffic")}</div>
            )}
          </div>
          <dl className="mt-3 grid grid-cols-4 gap-2">
            {[
              [t("stat.lastHour"), traces ? fmt.number(traces.requests) : "—", ""],
              [
                t("stat.failed"),
                failed === null ? "—" : `${fmt.number(failed * 100, { maximumFractionDigits: failed < 0.1 ? 1 : 0 })}%`,
                failed ? "text-critical" : "",
              ],
              ["p95", fmt.latency(p95), p95 !== null && p95 >= 1000 ? "text-warning" : ""],
              [
                t("stat.ready"),
                ready === null ? "—" : `${ready}/${desired ?? "—"}`,
                ready !== null && desired && ready < desired ? (ready === 0 ? "text-critical" : "text-warning") : "",
              ],
            ].map(([label, value, tone]) => (
              <div key={label} className="min-w-0">
                <dt className="truncate text-[11px] text-ink-muted">{label}</dt>
                <dd data-tabular className={`mt-0.5 truncate font-mono text-[0.8125rem] font-semibold ${tone || (value === "—" ? "text-ink-muted" : "text-ink")}`}>
                  {value}
                </dd>
              </div>
            ))}
          </dl>
          <div className="mt-3 grid grid-cols-2 gap-3 border-t border-border pt-3">
            <Meter label={t("stat.cpu")} ratio={health.resources?.cpu_utilization ?? null} />
            <Meter label={t("stat.memory")} ratio={health.resources?.memory_utilization ?? null} />
          </div>
          <ArrowUpRight
            aria-hidden
            className="pointer-events-none absolute right-3 bottom-3 h-3.5 w-3.5 text-ink-muted opacity-0 transition-opacity group-hover:opacity-100"
          />
        </>
      ) : (
        <Link
          href={`/service-health/bind?environment_service_id=${row.environment_service_id}`}
          className="mt-4 inline-flex h-8 w-fit items-center gap-1.5 rounded-full border border-border bg-surface px-3 text-caption font-medium text-ink transition-colors hover:bg-surface-hover"
        >
          <Plus aria-hidden className="h-3.5 w-3.5" />
          {t("bind")}
        </Link>
      )}
    </li>
  );
}
