"use client";

/**
 * Service health list.
 *
 * Every service in scope appears, bound or not. A list that quietly dropped
 * unbound services would make an unobserved estate look like a healthy one,
 * which is the single most expensive way for a dashboard to be wrong.
 */

import {
  Activity,
  AlertTriangle,
  ChevronRight,
  HeartPulse,
  Layers,
  Link2,
  Plus,
  ServerCog,
  Unplug,
} from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";

import { LoadGate, useApi } from "@/components/catalog/primitives";
import {
  BindingStateBadge,
  HealthBadge,
  IconBubble,
  MiniMeter,
  STATUS_LABELS,
  STATUS_ORDER,
  StateCard,
  toneForServiceStatus,
  useAge,
} from "@/components/service-health/primitives";
import { RingProgress, ValueChip } from "@/components/charts/visuals";
import { DataState } from "@/components/state/DataState";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import {
  toneForThreshold,
  toneSpec,
  type StatusTone,
} from "@/lib/design/status";
import { useT, type Translator } from "@/lib/i18n";
import {
  serviceHealthListPath,
  type ServiceHealthPage,
  type ServiceHealthRow,
  type ServiceHealthStatus,
} from "@/lib/serviceHealth";

/** How many services sit in each verdict, from the rows the page already
 *  has — never a second query, and never a bucket invented beyond the ones
 *  the API itself reports. Worst first. */
function tally(items: ServiceHealthRow[], t: Translator<"serviceHealth">) {
  const counts = new Map<ServiceHealthStatus, number>();
  for (const row of items) {
    counts.set(row.health.status, (counts.get(row.health.status) ?? 0) + 1);
  }
  return [...counts.entries()]
    .sort(([a], [b]) => STATUS_ORDER.indexOf(a) - STATUS_ORDER.indexOf(b))
    .map(([status, count]) => ({
      status,
      label: t.dyn("status", status, STATUS_LABELS[status] ?? status.replace(/_/g, " ")),
      count,
      tone: toneForServiceStatus(status),
    }));
}

/** The pressure bands the platform already judges utilisation by. */
const UTILISATION = { warn: 0.8, critical: 0.9, direction: "above" as const };

const BIG_NUMBER =
  "text-[2.25rem] leading-none font-semibold tracking-[-0.03em] text-ink";

/** One KPI card: icon bubble, label, big number, and a visual underneath. */
function KpiCard({
  icon,
  tone,
  label,
  value,
  suffix,
  visual,
  footnote,
}: {
  icon: React.ComponentType<{ className?: string }>;
  tone?: StatusTone;
  label: string;
  value: React.ReactNode;
  suffix?: React.ReactNode;
  visual?: React.ReactNode;
  footnote?: React.ReactNode;
}) {
  return (
    <Panel className="h-full">
      <div className="flex items-center gap-3">
        <IconBubble icon={icon} tone={tone} />
        <span className="text-caption font-medium text-ink-secondary">
          {label}
        </span>
      </div>
      <div className="flex items-end justify-between gap-3">
        <p className="min-w-0">
          <span data-tabular className={BIG_NUMBER}>
            {value}
          </span>
          {suffix ? (
            <span className="ml-1.5 text-caption text-ink-muted">{suffix}</span>
          ) : null}
        </p>
        {visual}
      </div>
      {footnote ? <div className="-mt-2 min-w-0">{footnote}</div> : null}
    </Panel>
  );
}

function KpiStrip({ items }: { items: ServiceHealthRow[] }) {
  const t = useT("serviceHealth");
  const total = items.length;
  const bound = items.filter((row) => row.binding).length;
  const healthy = items.filter((row) => row.health.status === "healthy").length;
  const attention = items.filter(
    (row) =>
      row.health.status === "critical" || row.health.status === "degraded",
  ).length;
  const unobserved = items.filter((row) =>
    ["unknown", "stale", "not_configured"].includes(row.health.status),
  ).length;
  const sorted = [...items].sort(
    (a, b) =>
      STATUS_ORDER.indexOf(a.health.status) -
      STATUS_ORDER.indexOf(b.health.status),
  );

  return (
    <div className="page-grid">
      <KpiCard
        icon={Layers}
        label={t("list.kpi.inScope")}
        value={total}
        suffix={t("list.kpi.services")}
        footnote={
          /* One tile per service in its own reported colour: at this scale a
             count you can see beats a pie. */
          <ul
            aria-label={t("list.kpi.byState")}
            className="grid grid-cols-[repeat(auto-fill,minmax(0.875rem,1fr))] gap-1"
          >
            {sorted.map((row) => (
              <li
                key={row.environment_service_id}
                title={t("list.kpi.tileTitle", {
                  name: row.display_name || row.service_key,
                  scope: `${row.project_key}/${row.environment_key}`,
                  status: t.dyn("status", row.health.status, STATUS_LABELS[row.health.status]),
                })}
                className={`aspect-square rounded-[0.3rem] ${toneSpec(toneForServiceStatus(row.health.status)).chip}`}
              />
            ))}
          </ul>
        }
      />
      <KpiCard
        icon={Link2}
        tone={bound === 0 ? undefined : "info"}
        label={t("list.kpi.bound")}
        value={bound}
        suffix={t("list.kpi.ofTotal", { total })}
        visual={
          <RingProgress
            size={52}
            label={t("list.kpi.boundRing")}
            value={total > 0 ? (bound / total) * 100 : null}
            tone={
              bound === total ? "success" : bound === 0 ? "unknown" : "info"
            }
          />
        }
        footnote={
          <p className="text-micro text-ink-muted">
            {total - bound === 0
              ? t("list.kpi.allBound")
              : t("list.kpi.notObserved", { count: total - bound })}
          </p>
        }
      />
      <KpiCard
        icon={HeartPulse}
        tone={healthy > 0 ? "success" : undefined}
        label={t("list.kpi.healthy")}
        value={healthy}
        suffix={t("list.kpi.ofTotal", { total })}
        footnote={
          <div className="space-y-2">
            <MiniMeter
              fraction={total > 0 ? healthy / total : null}
              tone="success"
            />
            <p className="text-micro text-ink-muted">{t("list.kpi.healthyNote")}</p>
          </div>
        }
      />
      <KpiCard
        icon={AlertTriangle}
        tone={attention > 0 ? "critical" : undefined}
        label={t("list.kpi.attention")}
        value={attention}
        suffix={t("list.kpi.attentionSuffix")}
        footnote={
          <p data-tabular className="text-micro text-ink-muted">
            {t("list.kpi.unobserved", { count: unobserved })}
          </p>
        }
      />
    </div>
  );
}

/** Ready over desired: two exact numbers, plus a bar only when both exist. */
function ReadyCell({ row }: { row: ServiceHealthRow }) {
  const t = useT("serviceHealth");
  const ready = row.health.availability?.ready_replicas ?? null;
  const desired = row.health.availability?.desired_replicas ?? null;
  const fraction = ready !== null && desired ? ready / desired : null;
  const tone: StatusTone =
    fraction === null
      ? "unknown"
      : fraction >= 1
        ? "success"
        : ready === 0
          ? "critical"
          : "warning";
  return (
    <div className="min-w-0">
      <span className="block text-micro text-ink-muted @min-[48rem]/table:hidden">
        {t("list.row.ready")}
      </span>
      {/* Ready/desired stays two numbers: a single percentage would hide
          the difference between 0/0 and an unmeasured pair. */}
      <span
        data-tabular
        className={`text-body ${ready === null ? "text-ink-muted" : "font-semibold text-ink"}`}
      >
        {ready ?? "—"}
        <span className="font-normal text-ink-muted">/{desired ?? "—"}</span>
      </span>
      {fraction !== null ? (
        <MiniMeter fraction={fraction} tone={tone} className="mt-1.5 w-14" />
      ) : null}
    </div>
  );
}

function UtilisationCell({
  label,
  value,
}: {
  label: string;
  value: number | null;
}) {
  return (
    <div className="min-w-0">
      <span className="block text-micro text-ink-muted @min-[48rem]/table:hidden">
        {label}
      </span>
      {/* The chip keeps exact digits; its colour says which row to read first. */}
      <ValueChip value={value} unit="ratio" thresholds={UTILISATION} />
      {value !== null ? (
        <MiniMeter
          fraction={value}
          tone={toneForThreshold(value, UTILISATION)}
          className="mt-1.5 w-16"
        />
      ) : null}
    </div>
  );
}

/* Keyed to the table's own width (`@container/table` on its panel), not the
 * viewport: the table sits in the main column, which is far narrower than
 * the window. From 48rem the row is a grid whose last column stacks status
 * over the action; from 64rem status and action get columns of their own.
 * Both steps leave the service name at least 14rem. */
const ROW_GRID =
  "@min-[48rem]/table:grid @min-[48rem]/table:grid-cols-[minmax(14rem,1fr)_3.5rem_4.5rem_4.5rem_4.5rem_9.25rem] @min-[48rem]/table:items-center @min-[48rem]/table:gap-3 @min-[64rem]/table:grid-cols-[minmax(14rem,1fr)_3.5rem_4.5rem_4.5rem_4.5rem_8.5rem_9.25rem]";

/**
 * One service. Identity and binding lead; the four measured numbers sit in
 * aligned columns so rows compare down the page; status and the next action
 * trail on the right. A bound row opens its detail page from anywhere.
 */
function ServiceRow({ row }: { row: ServiceHealthRow }) {
  const t = useT("serviceHealth");
  const age = useAge();
  const { binding, health } = row;
  const tone = toneForServiceStatus(health.status);
  const name = row.display_name || row.service_key;
  return (
    <li
      data-testid={`service-row-${row.service_key}`}
      className={`relative flex flex-col gap-4 px-7 py-4 transition-colors hover:bg-surface-hover ${ROW_GRID}`}
    >
      <div className="flex min-w-0 items-center gap-4">
        <IconBubble
          icon={binding ? ServerCog : Unplug}
          tone={binding ? tone : undefined}
        />
        <div className="min-w-0">
          <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
            {binding ? (
              <Link
                href={`/service-health/${binding.id}`}
                className="truncate text-body font-semibold text-ink after:absolute after:inset-0 hover:text-brand"
              >
                {name}
              </Link>
            ) : (
              <span className="truncate text-body font-semibold text-ink">
                {name}
              </span>
            )}
            <span className="rounded-full bg-surface-2 px-2 py-0.5 font-mono text-micro text-ink-muted">
              {row.project_key}/{row.environment_key}
            </span>
            {health.served_from_last_good ? (
              <span className="text-micro italic text-stale">{t("list.row.lastKnown")}</span>
            ) : health.partial ? (
              <span className="text-micro italic text-ink-muted">{t("list.row.partial")}</span>
            ) : null}
          </div>
          <div className="mt-1 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-micro text-ink-muted">
            {binding ? (
              <>
                <BindingStateBadge
                  lifecycle={binding.lifecycle}
                  resolved={binding.resolved}
                />
                <span className="truncate font-mono">
                  {binding.cluster.cluster_ref}/{binding.namespace}/
                  {binding.workload_name}
                </span>
              </>
            ) : (
              <span>{t("list.row.noWorkload")}</span>
            )}
            {health.freshness_age_seconds !== null ? (
              <span>· {age(health.freshness_age_seconds)}</span>
            ) : null}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-4 gap-4 pl-14 @min-[48rem]/table:contents">
        <ReadyCell row={row} />
        <div className="min-w-0">
          <span className="block text-micro text-ink-muted @min-[48rem]/table:hidden">
            {t("list.row.restarts")}
          </span>
          {/* A restart count has no ceiling, so it carries severity rather
              than a percentage. */}
          <ValueChip
            value={health.stability?.restarts_in_window ?? null}
            unit="count"
            thresholds={{ warn: 1, critical: 5, direction: "above" }}
          />
        </div>
        <UtilisationCell
          label={t("list.row.cpu")}
          value={health.resources?.cpu_utilization ?? null}
        />
        <UtilisationCell
          label={t("list.row.memory")}
          value={health.resources?.memory_utilization ?? null}
        />
      </div>

      <div className="flex items-center justify-between gap-3 pl-14 @min-[48rem]/table:flex-col @min-[48rem]/table:items-end @min-[48rem]/table:gap-2 @min-[48rem]/table:pl-0 @min-[64rem]/table:contents">
        <div className="min-w-0">
          <HealthBadge status={health.status} />
        </div>
        <div className="relative z-10 flex justify-end">
          {binding ? (
            <span
              aria-hidden
              className="flex h-8 w-8 items-center justify-center rounded-full border border-border text-ink-muted"
            >
              <ChevronRight className="h-4 w-4" />
            </span>
          ) : (
            <Link
              href={`/service-health/bind?environment_service_id=${row.environment_service_id}`}
              className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border border-border bg-surface px-3 py-1.5 text-caption font-medium text-ink transition-colors hover:bg-surface-hover"
            >
              <Plus className="h-3.5 w-3.5" aria-hidden />
              {t("list.row.bind")}
            </Link>
          )}
        </div>
      </div>
    </li>
  );
}

function StatusFilter({
  items,
  total,
  value,
  onChange,
}: {
  items: ReturnType<typeof tally>;
  total: number;
  value: ServiceHealthStatus | "all";
  onChange: (next: ServiceHealthStatus | "all") => void;
}) {
  const t = useT("serviceHealth");
  const tc = useT("common");
  const pill = (active: boolean) =>
    `inline-flex items-center gap-2 rounded-full px-3.5 py-1.5 text-caption font-medium transition-colors ${
      active
        ? "bg-ink text-canvas"
        : "border border-border bg-surface text-ink-secondary hover:bg-surface-hover"
    }`;
  return (
    <div
      role="group"
      aria-label={t("list.filter.label")}
      className="flex flex-wrap items-center gap-2"
    >
      <button
        type="button"
        aria-pressed={value === "all"}
        className={pill(value === "all")}
        onClick={() => onChange("all")}
      >
        {tc("count.all")}
        <span data-tabular className="opacity-70">
          {total}
        </span>
      </button>
      {items.map((item) => (
        <button
          key={item.status}
          type="button"
          aria-pressed={value === item.status}
          className={pill(value === item.status)}
          onClick={() => onChange(item.status)}
        >
          <span
            aria-hidden
            className={`h-2 w-2 rounded-full ${toneSpec(item.tone).dot}`}
          />
          {item.label}
          <span data-tabular className="opacity-70">
            {item.count}
          </span>
        </button>
      ))}
    </div>
  );
}

/** Status composition as one proportional bar with an exact legend. */
function BreakdownCard({ items }: { items: ServiceHealthRow[] }) {
  const t = useT("serviceHealth");
  const buckets = tally(items, t);
  const total = items.length;
  return (
    <Panel>
      <PanelHeader title={t("list.breakdown.title")} description={t("list.breakdown.description")} />
      <div
        role="img"
        aria-label={t("list.breakdown.aria", {
          items: buckets
            .map((b) => t("list.breakdown.item", { label: b.label, count: b.count }))
            .join(", "),
        })}
        className="flex h-3 w-full gap-1 overflow-hidden rounded-full"
      >
        {buckets.map((bucket) => (
          <span
            key={bucket.status}
            className={`h-full rounded-full ${toneSpec(bucket.tone).dot}`}
            style={{ width: `${(bucket.count / total) * 100}%` }}
          />
        ))}
      </div>
      <ul className="space-y-3">
        {STATUS_ORDER.map((status) => {
          const count = buckets.find((b) => b.status === status)?.count ?? 0;
          const spec = toneSpec(toneForServiceStatus(status));
          return (
            <li
              key={status}
              className={`flex min-w-0 items-center gap-2 text-caption ${count === 0 ? "opacity-50" : ""}`}
            >
              <span
                aria-hidden
                className={`h-2.5 w-2.5 shrink-0 rounded-full ${spec.dot}`}
              />
              <span className="min-w-0 truncate text-ink-secondary">
                {t.dyn("status", status, STATUS_LABELS[status])}
              </span>
              <span
                data-tabular
                className="ml-auto shrink-0 font-semibold text-ink"
              >
                {count}
              </span>
              <span
                data-tabular
                className="w-9 shrink-0 text-right text-micro text-ink-muted"
              >
                {total > 0 ? Math.round((count / total) * 100) : 0}%
              </span>
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}

/** Binding coverage per project/environment — where the blind spots are. */
function CoverageCard({ items }: { items: ServiceHealthRow[] }) {
  const t = useT("serviceHealth");
  const groups = new Map<string, { total: number; bound: number }>();
  for (const row of items) {
    const key = `${row.project_key}/${row.environment_key}`;
    const entry = groups.get(key) ?? { total: 0, bound: 0 };
    entry.total += 1;
    if (row.binding) entry.bound += 1;
    groups.set(key, entry);
  }
  const rows = [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
  return (
    <Panel>
      <PanelHeader title={t("list.coverage.title")} description={t("list.coverage.description")} />
      <ul className="space-y-4">
        {rows.map(([key, entry]) => (
          <li key={key} className="min-w-0">
            <div className="flex items-baseline justify-between gap-3 text-caption">
              <span className="truncate font-mono text-ink-secondary">
                {key}
              </span>
              <span data-tabular className="shrink-0 text-ink">
                <span className="font-semibold">{entry.bound}</span>
                <span className="text-ink-muted">/{entry.total}</span>
              </span>
            </div>
            <div className="mt-1.5 flex gap-1" aria-hidden>
              {Array.from({ length: entry.total }, (_, index) => (
                <span
                  key={index}
                  className={`h-2 flex-1 rounded-full ${index < entry.bound ? "bg-info" : "bg-surface-3"}`}
                />
              ))}
            </div>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function ServiceHealthBody({ data }: { data: ServiceHealthPage }) {
  const t = useT("serviceHealth");
  const [filter, setFilter] = useState<ServiceHealthStatus | "all">("all");
  const buckets = useMemo(() => tally(data.items, t), [data.items, t]);
  const visible = useMemo(
    () =>
      [...data.items]
        .filter((row) => filter === "all" || row.health.status === filter)
        .sort(
          (a, b) =>
            STATUS_ORDER.indexOf(a.health.status) -
            STATUS_ORDER.indexOf(b.health.status),
        ),
    [data.items, filter],
  );

  return (
    <div className="space-y-6 motion-safe:animate-[fade-in_400ms_var(--ease-entrance)_backwards]">
      <KpiStrip items={data.items} />

      <div className="page-grid" data-cols="2">
        <BreakdownCard items={data.items} />
        <CoverageCard items={data.items} />
      </div>

      <div className="flex flex-col gap-6">
        <StatusFilter
          items={buckets}
          total={data.items.length}
          value={filter}
          onChange={setFilter}
        />
        <Panel flush className="@container/table">
          <div
            aria-hidden
            className={`hidden border-b border-border px-7 py-3 text-micro font-medium tracking-[0.08em] text-ink-muted uppercase ${ROW_GRID}`}
          >
            <span>{t("list.table.service")}</span>
            <span>{t("list.table.ready")}</span>
            <span>{t("list.table.restarts")}</span>
            <span>{t("list.table.cpu")}</span>
            <span>{t("list.table.memory")}</span>
            <span className="@min-[48rem]/table:text-right @min-[64rem]/table:text-left">
              {t("list.table.status")}
            </span>
            <span className="hidden @min-[64rem]/table:block" />
          </div>
          <ul
            className="divide-y divide-border"
            data-testid="service-health-table"
          >
            {visible.map((row) => (
              <ServiceRow key={row.environment_service_id} row={row} />
            ))}
          </ul>
          <p className="flex items-center gap-2 border-t border-border px-7 py-4 text-micro text-ink-muted">
            <Activity className="h-3.5 w-3.5" aria-hidden />
            {t("list.table.showing", { shown: data.items.length, total: data.total })}
            <span className="ml-auto hidden sm:inline">{t("list.table.dashNote")}</span>
          </p>
        </Panel>
      </div>
    </div>
  );
}

function ServiceHealthTable() {
  const t = useT("serviceHealth");
  const params = useSearchParams();
  const [page, retry] = useApi<ServiceHealthPage>(
    serviceHealthListPath({
      projectId: params.get("project_id") ?? undefined,
      environmentId: params.get("environment_id") ?? undefined,
    }),
  );

  return (
    <LoadGate value={page} retry={retry}>
      {(data) =>
        data.items.length === 0 ? (
          <Panel>
            <StateCard
              icon={Layers}
              title={t("list.empty.title")}
              description={t("list.empty.description")}
            />
          </Panel>
        ) : (
          <ServiceHealthBody data={data} />
        )
      }
    </LoadGate>
  );
}

export default function ServiceHealthPage() {
  const t = useT("serviceHealth");
  return (
    <PageFrame>
      <PageHeader title={t("list.title")} description={t("list.description")} />
      <Suspense fallback={<DataState kind="loading" />}>
        <ServiceHealthTable />
      </Suspense>
    </PageFrame>
  );
}
