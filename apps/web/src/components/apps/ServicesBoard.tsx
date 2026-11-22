"use client";

/**
 * Services, as a board: every service the caller may see, across apps.
 *
 * The question this page answers is "which service?", so a service is the
 * thing you pick — not the third step of a drill. On top, how the whole
 * estate is doing (health distribution, a day of traffic, what is failing,
 * what is slowest) and three rankings; below, every bound service as a card
 * with its app and environment, filtered by app and environment chips that
 * keep the URL. Unbound services are listed compactly at the end: they are
 * real, but there is nothing to measure on them.
 *
 * Reads: the service list the page already has, the last hour's signals
 * (requests, failures, p95, error logs) and one 24h overview read for the
 * sparklines. A number the stores did not answer is a dash, never zero.
 */

import { Plus, Search, X } from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";

import { STATUS_LABELS, toneForServiceStatus } from "@/components/service-health/primitives";
import { ProjectLogo } from "@/components/ui/ProjectLogo";
import { answered } from "@/lib/dashboards";
import { toneSpec } from "@/lib/design/status";
import { useFormat, useT } from "@/lib/i18n";
import { envRank, usePlatformOverview, useProjectNames } from "@/lib/platform";
import type { ServiceHealthRow, ServiceHealthStatus } from "@/lib/serviceHealth";
import { judgeSignal, type SignalItem } from "@/lib/signals";

import { kindColour, Sparkline } from "./AppLauncher";
import { ServiceCard } from "./ServiceCard";
import { sortServiceRows } from "./ServicesLauncher";

const ORDER: ServiceHealthStatus[] = ["critical", "degraded", "stale", "unknown", "healthy", "not_configured"];

export interface BoardFilters {
  q: string;
  project: string;
  environment: string;
}

function Tile({ label, value, note, tone, children }: { label: string; value: string; note: string; tone?: string; children?: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1 px-5 py-4">
      <span className="text-caption text-ink-muted">{label}</span>
      <span data-tabular className={`font-[family-name:var(--font-display)] text-[2rem] leading-none font-bold tracking-[-0.03em] ${tone ?? "text-ink"}`}>
        {value}
      </span>
      <span className="truncate text-micro text-ink-muted">{note}</span>
      {children}
    </div>
  );
}

interface Ranked {
  row: ServiceHealthRow;
  value: number;
  label: string;
}

function Ranking({
  title,
  note,
  items,
  empty,
  colour,
  names,
}: {
  title: string;
  note: string;
  items: Ranked[];
  empty: string;
  colour: string;
  names: Map<string, string>;
}) {
  const top = items[0]?.value ?? 0;
  return (
    <section className="flex min-w-0 flex-col rounded-panel border border-border bg-surface">
      <header className="px-5 pt-4 pb-3">
        <h3 className="text-[0.9375rem] font-semibold tracking-[-0.01em] text-ink">{title}</h3>
        <p className="text-micro text-ink-muted">{note}</p>
      </header>
      {items.length === 0 ? (
        <p className="mx-5 mb-5 rounded-control border border-dashed border-border px-3 py-4 text-center text-caption text-ink-muted">{empty}</p>
      ) : (
        <ol className="flex flex-col pb-2">
          {items.map(({ row, value, label }) => (
            <li key={row.environment_service_id}>
              <Link
                href={`/service-health/${row.binding!.id}`}
                className="group flex items-center gap-3 px-5 py-2 transition-colors hover:bg-surface-hover"
              >
                <ProjectLogo projectId={row.project_id} name={names.get(row.project_id) ?? row.project_key} size="sm" variant="mark" />
                <span className="min-w-0 flex-1">
                  <span className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-body font-medium text-ink group-hover:text-brand-accent">{row.display_name || row.service_key}</span>
                    <span data-tabular className="shrink-0 font-mono text-caption font-semibold text-ink">{label}</span>
                  </span>
                  <span className="mt-1 flex items-center gap-2">
                    <span className="h-1 flex-1 overflow-hidden rounded-full bg-surface-3">
                      <span className="block h-full rounded-full" style={{ width: `${top ? Math.max(4, (value / top) * 100) : 0}%`, background: colour }} />
                    </span>
                    <span className="shrink-0 font-mono text-[10.5px] text-ink-muted">
                      {row.project_key}/{row.environment_key}
                    </span>
                  </span>
                </span>
              </Link>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function Chip({
  active,
  onClick,
  children,
  testId,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
  testId?: string;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      data-testid={testId}
      className={`inline-flex h-9 items-center gap-2 rounded-full border px-3 text-caption font-medium transition-colors ${
        active ? "border-brand-accent/60 bg-surface-selected text-ink" : "border-border bg-surface text-ink-secondary hover:bg-surface-hover"
      }`}
    >
      {children}
    </button>
  );
}

export function ServicesBoard({
  rows,
  signalById,
  filters,
  onFilters,
}: {
  rows: ServiceHealthRow[];
  signalById: Map<string, SignalItem>;
  filters: BoardFilters;
  onFilters: (patch: Partial<BoardFilters>) => void;
}) {
  const t = useT("boards");
  const ts = useT("serviceHealth");
  const ta = useT("apps");
  const fmt = useFormat();
  const names = useProjectNames();
  const { overview } = usePlatformOverview(rows);
  const traffic = answered(overview?.traffic);

  const nameOf = (row: ServiceHealthRow) => names.get(row.project_id) ?? row.project_key;

  // Filters: apps (with logos), then the environment keys that app has.
  const apps = useMemo(() => {
    const seen = new Map<string, { id: string; key: string; count: number }>();
    for (const row of rows) {
      const app = seen.get(row.project_id) ?? { id: row.project_id, key: row.project_key, count: 0 };
      app.count += 1;
      seen.set(row.project_id, app);
    }
    return [...seen.values()].sort((a, b) => (names.get(a.id) ?? a.key).localeCompare(names.get(b.id) ?? b.key));
  }, [rows, names]);
  const envKeys = useMemo(() => {
    const keys = new Map<string, number>();
    for (const row of rows) {
      if (filters.project && row.project_id !== filters.project) continue;
      keys.set(row.environment_key, (keys.get(row.environment_key) ?? 0) + 1);
    }
    return [...keys.entries()].sort((a, b) => envRank(a[0]) - envRank(b[0]) || a[0].localeCompare(b[0]));
  }, [rows, filters.project]);

  const q = filters.q.trim().toLocaleLowerCase();
  const scoped = rows.filter(
    (row) =>
      (!filters.project || row.project_id === filters.project) &&
      (!filters.environment || row.environment_key === filters.environment) &&
      (!q || `${row.display_name ?? ""} ${row.service_key} ${row.project_key}`.toLocaleLowerCase().includes(q)),
  );
  const bound = sortServiceRows(scoped.filter((r) => r.binding), signalById);
  const unbound = scoped.filter((r) => !r.binding);
  // A bound service that sent no trace all day gets a line, not a card of
  // dashes. Until the traffic read answers, nothing is called quiet.
  const hasTraffic = (row: ServiceHealthRow) => {
    const id = row.binding!.id;
    if ((signalById.get(id)?.traces?.requests ?? 0) > 0) return true;
    if (!traffic) return true;
    return traffic.find((x) => x.binding_id === id)?.buckets.some((b) => b.requests > 0) ?? false;
  };
  const active = bound.filter(hasTraffic);
  const quiet = bound.filter((row) => !hasTraffic(row));

  // The estate at a glance, over the scoped services.
  const tally = new Map<ServiceHealthStatus, number>();
  for (const row of scoped) tally.set(row.health.status, (tally.get(row.health.status) ?? 0) + 1);
  const scopedIds = new Set(scoped.flatMap((r) => (r.binding ? [r.binding.id] : [])));
  const dayTraffic = traffic?.filter((x) => scopedIds.has(x.binding_id)) ?? null;
  const dayTotal = dayTraffic ? dayTraffic.reduce((a, x) => a + x.buckets.reduce((b, y) => b + y.requests, 0), 0) : null;
  const daySeries =
    dayTraffic && overview ? overview.buckets.map((_, i) => dayTraffic.reduce((a, x) => a + (x.buckets[i]?.requests ?? 0), 0)) : [];
  const withTraces = dayTraffic ? dayTraffic.filter((x) => x.buckets.some((b) => b.requests > 0)).length : 0;

  const measured = bound.flatMap((row) => {
    const item = signalById.get(row.binding!.id);
    return item ? [{ row, item }] : [];
  });
  const failing = measured.filter(({ item }) => judgeSignal(item).tone !== "success");
  const busiest: Ranked[] = measured
    .filter(({ item }) => (item.traces?.requests ?? 0) > 0)
    .sort((a, b) => (b.item.traces!.requests ?? 0) - (a.item.traces!.requests ?? 0))
    .slice(0, 5)
    .map(({ row, item }) => ({ row, value: item.traces!.requests, label: fmt.number(item.traces!.requests) }));
  const slowest: Ranked[] = measured
    .filter(({ item }) => item.traces?.p95_ms != null)
    .sort((a, b) => (b.item.traces!.p95_ms ?? 0) - (a.item.traces!.p95_ms ?? 0))
    .slice(0, 5)
    .map(({ row, item }) => ({ row, value: item.traces!.p95_ms!, label: fmt.latency(item.traces!.p95_ms) }));
  const failingMost: Ranked[] = measured
    .filter(({ item }) => (item.traces?.errors ?? 0) > 0 && item.traces?.error_ratio != null)
    .sort((a, b) => (b.item.traces!.error_ratio ?? 0) - (a.item.traces!.error_ratio ?? 0))
    .slice(0, 5)
    .map(({ row, item }) => {
      const r = item.traces!.error_ratio!;
      return { row, value: r, label: `${fmt.number(r * 100, { maximumFractionDigits: r < 0.1 ? 1 : 0 })}%` };
    });
  const slowestOne = slowest[0] ?? null;
  const filtered = Boolean(filters.q || filters.project || filters.environment);

  return (
    <div className="flex flex-col gap-6" data-testid="services-board">
      {/* The estate at a glance. */}
      <section className="overflow-hidden rounded-panel border border-border bg-surface" aria-label={t("services.health")}>
        <div className="grid divide-y divide-border sm:grid-cols-2 sm:divide-y-0 xl:grid-cols-4 xl:divide-x">
          <Tile
            label={t("services.health")}
            value={fmt.number(scoped.length)}
            note={t("services.healthNote", { bound: bound.length, total: scoped.length })}
          >
            <span className="mt-2 flex h-2 w-full gap-[2px] overflow-hidden rounded-full" role="img" aria-label={ORDER.filter((s) => tally.get(s)).map((s) => `${tally.get(s)} ${ts.dyn("status", s, STATUS_LABELS[s])}`).join(", ")}>
              {ORDER.filter((s) => tally.get(s)).map((s) => (
                <span key={s} className={`h-full ${toneSpec(toneForServiceStatus(s)).dot}`} style={{ flexGrow: tally.get(s) }} />
              ))}
            </span>
            <span className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-ink-secondary">
              {ORDER.filter((s) => tally.get(s)).map((s) => (
                <span key={s} className="inline-flex items-center gap-1">
                  <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${toneSpec(toneForServiceStatus(s)).dot}`} />
                  <span data-tabular>{tally.get(s)}</span> {ts.dyn("status", s, STATUS_LABELS[s]).toLocaleLowerCase()}
                </span>
              ))}
            </span>
          </Tile>
          <Tile
            label={t("services.requests")}
            value={dayTotal === null ? "—" : fmt.number(dayTotal)}
            note={t("services.requestsNote", { count: withTraces })}
          >
            {daySeries.some((v) => v > 0) ? <Sparkline values={daySeries} height={30} className="mt-1" /> : null}
          </Tile>
          <Tile
            label={t("services.failing")}
            value={measured.length ? fmt.number(failing.length) : "—"}
            note={t("services.failingNote")}
            tone={failing.length ? "text-critical" : "text-ink"}
          >
            {failing.length ? (
              <span className="mt-1 flex flex-wrap gap-1">
                {failing.slice(0, 3).map(({ row }) => (
                  <Link
                    key={row.environment_service_id}
                    href={`/service-health/${row.binding!.id}`}
                    className="truncate rounded-full bg-critical-soft px-2 py-0.5 text-[11px] font-medium text-critical hover:underline"
                  >
                    {row.display_name || row.service_key}
                  </Link>
                ))}
              </span>
            ) : null}
          </Tile>
          <Tile
            label={t("services.slowest")}
            value={slowestOne ? slowestOne.label : "—"}
            note={slowestOne ? t("services.slowestNote", { service: slowestOne.row.display_name || slowestOne.row.service_key }) : t("services.none")}
            tone={slowestOne && slowestOne.value >= 1000 ? "text-warning" : "text-ink"}
          />
        </div>
      </section>

      {/* Three rankings: where the traffic is, where it is slow, where it fails. */}
      <div className="grid gap-4 lg:grid-cols-3">
        <Ranking title={t("services.busiest")} note={t("services.busiestNote")} items={busiest} empty={t("services.noTraffic")} colour="var(--brand-accent)" names={names} />
        <Ranking title={t("services.slow")} note={t("services.slowNote")} items={slowest} empty={t("services.noTraffic")} colour="var(--status-warning)" names={names} />
        <Ranking title={t("services.failingList")} note={t("services.failingListNote")} items={failingMost} empty={t("services.noneFailing")} colour="var(--status-critical)" names={names} />
      </div>

      {/* Filters: the app and environment narrow the board; nothing forces a drill. */}
      <div className="flex flex-col gap-3" data-testid="services-board-filters">
        <div className="flex flex-wrap items-center gap-2">
          <label className="relative w-full sm:w-64">
            <span className="sr-only">{ts("list.search")}</span>
            <Search aria-hidden className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-ink-muted" />
            <input
              type="search"
              value={filters.q}
              onChange={(e) => onFilters({ q: e.target.value })}
              placeholder={ts("list.search")}
              className="h-9 w-full rounded-full border border-border bg-surface pr-3 pl-9 text-caption text-ink placeholder:text-ink-muted"
            />
          </label>
          <span className="text-micro text-ink-muted">{t("services.filterApps")}</span>
          <Chip active={!filters.project} onClick={() => onFilters({ project: "", environment: "" })}>
            {t("services.all")}
            <span data-tabular className="font-mono text-micro text-ink-muted">{rows.length}</span>
          </Chip>
          {apps.map((app) => (
            <Chip
              key={app.id}
              active={filters.project === app.id}
              testId={`board-app-${app.key}`}
              onClick={() => onFilters({ project: filters.project === app.id ? "" : app.id, environment: "" })}
            >
              <ProjectLogo projectId={app.id} name={names.get(app.id) ?? app.key} size="sm" variant="mark" />
              {names.get(app.id) ?? app.key}
              <span data-tabular className="font-mono text-micro text-ink-muted">{app.count}</span>
            </Chip>
          ))}
        </div>
        {envKeys.length > 1 ? (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-micro text-ink-muted">{t("services.filterEnvs")}</span>
            <Chip active={!filters.environment} onClick={() => onFilters({ environment: "" })}>
              {t("services.all")}
            </Chip>
            {envKeys.map(([key, count]) => (
              <Chip
                key={key}
                active={filters.environment === key}
                testId={`board-env-${key}`}
                onClick={() => onFilters({ environment: filters.environment === key ? "" : key })}
              >
                <span aria-hidden className="h-2 w-2 rounded-full" style={{ background: kindColour(key) }} />
                <span className="font-mono">{key}</span>
                <span data-tabular className="font-mono text-micro text-ink-muted">{count}</span>
              </Chip>
            ))}
          </div>
        ) : null}
      </div>

      {/* Every bound service: pick one. */}
      <section aria-labelledby="board-bound" className="flex flex-col gap-3">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div>
            <h2 id="board-bound" className="text-[1.0625rem] font-semibold tracking-[-0.015em] text-ink">
              {t("services.bound")}
            </h2>
            <p className="text-caption text-ink-muted">{t("services.boundNote")}</p>
          </div>
          <span className="flex items-center gap-3 text-micro text-ink-muted">
            {t("services.shown", { shown: scoped.length, total: rows.length })}
            {filtered ? (
              <button
                type="button"
                onClick={() => onFilters({ q: "", project: "", environment: "" })}
                className="inline-flex items-center gap-1 font-medium text-brand-accent hover:underline"
              >
                <X aria-hidden className="h-3 w-3" />
                {t("services.clear")}
              </button>
            ) : null}
          </span>
        </div>
        {bound.length === 0 && unbound.length === 0 ? (
          <p className="rounded-panel border border-dashed border-border px-4 py-8 text-center text-caption text-ink-muted">{t("services.noMatch")}</p>
        ) : null}
        {active.length ? (
          <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
            {active.map((row, i) => (
              <ServiceCard
                key={row.environment_service_id}
                row={row}
                index={i}
                item={signalById.get(row.binding!.id)}
                context={{ projectId: row.project_id, projectName: nameOf(row) }}
                traffic={traffic?.find((x) => x.binding_id === row.binding!.id)?.buckets.map((b) => b.requests) ?? null}
              />
            ))}
          </ul>
        ) : null}
      </section>

      {/* Running, measured by the cluster, but silent in the traces: one line each. */}
      {quiet.length ? (
        <section aria-labelledby="board-quiet" className="rounded-panel border border-border bg-surface" data-testid="services-board-quiet">
          <header className="px-5 pt-4 pb-2">
            <h2 id="board-quiet" className="flex items-baseline gap-2 text-[0.9375rem] font-semibold text-ink">
              {t("services.quiet")}
              <span data-tabular className="font-mono text-micro font-normal text-ink-muted">{quiet.length}</span>
            </h2>
            <p className="text-micro text-ink-muted">{t("services.quietNote")}</p>
          </header>
          <ul className="grid gap-x-6 px-5 pb-3 md:grid-cols-2 2xl:grid-cols-3">
            {quiet.map((row) => {
              const ready = row.health.availability?.ready_replicas ?? null;
              const desired = row.health.availability?.desired_replicas ?? null;
              const cpu = row.health.resources?.cpu_utilization ?? null;
              const memory = row.health.resources?.memory_utilization ?? null;
              const pct = (v: number | null) => (v === null ? "—" : `${fmt.number(v * 100, { maximumFractionDigits: 0 })}%`);
              return (
                <li key={row.environment_service_id} data-testid={`service-quiet-${row.service_key}`} className="border-t border-border">
                  <Link href={`/service-health/${row.binding!.id}`} className="group flex items-center gap-3 py-2.5">
                    <ProjectLogo projectId={row.project_id} name={nameOf(row)} size="sm" variant="mark" />
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-2">
                        <span className="truncate text-body font-medium text-ink group-hover:text-brand-accent">{row.display_name || row.service_key}</span>
                        <span aria-hidden className={`h-1.5 w-1.5 shrink-0 rounded-full ${toneSpec(toneForServiceStatus(row.health.status)).dot}`} />
                      </span>
                      <span className="flex flex-wrap items-center gap-x-2 font-mono text-[10.5px] text-ink-muted">
                        <span>
                          {row.project_key}/{row.environment_key}
                        </span>
                        {ready !== null ? <span>· {t("services.ready", { ready, desired: desired ?? "—" })}</span> : null}
                      </span>
                    </span>
                    <span data-tabular className="shrink-0 text-right font-mono text-[10.5px] text-ink-muted">
                      {ta("stat.cpu")} {pct(cpu)}
                      <br />
                      {ta("stat.memory")} {pct(memory)}
                    </span>
                  </Link>
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}

      {/* Declared, not running anywhere Tracesis can see: compact, still honest. */}
      {unbound.length ? (
        <section aria-labelledby="board-unbound" className="rounded-panel border border-dashed border-border-strong" data-testid="services-board-unbound">
          <header className="px-5 pt-4 pb-2">
            <h2 id="board-unbound" className="flex items-baseline gap-2 text-[0.9375rem] font-semibold text-ink">
              {t("services.unbound")}
              <span data-tabular className="font-mono text-micro font-normal text-ink-muted">{unbound.length}</span>
            </h2>
            <p className="text-micro text-ink-muted">{t("services.unboundNote")}</p>
          </header>
          <ul className="grid gap-x-6 px-5 pb-3 sm:grid-cols-2 xl:grid-cols-3">
            {unbound.map((row) => (
              <li key={row.environment_service_id} data-testid={`service-unbound-${row.service_key}`} className="flex items-center gap-2.5 border-t border-border py-2.5">
                <ProjectLogo projectId={row.project_id} name={nameOf(row)} size="sm" variant="mark" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-body font-medium text-ink">{row.display_name || row.service_key}</span>
                  <span className="block font-mono text-[10.5px] text-ink-muted">
                    {row.project_key}/{row.environment_key}
                  </span>
                </span>
                <Link
                  href={`/service-health/bind?environment_service_id=${row.environment_service_id}`}
                  className="inline-flex h-7 shrink-0 items-center gap-1 rounded-full border border-border bg-surface px-2.5 text-micro font-medium text-ink transition-colors hover:bg-surface-hover"
                >
                  <Plus aria-hidden className="h-3 w-3" />
                  {ts("list.row.bind")}
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
