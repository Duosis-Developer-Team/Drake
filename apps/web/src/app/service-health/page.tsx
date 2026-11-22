"use client";

/**
 * Services.
 *
 * Every service in scope appears, bound or not. A list that quietly dropped
 * unbound services would make an unobserved estate look like a healthy one,
 * which is the single most expensive way for a dashboard to be wrong.
 *
 * One table, grouped by project and environment, worst first. Each row
 * carries what the old page spread over four cards and a separate board:
 * the reported state, last-hour requests / failures / p95 from traces, and
 * the workload's own numbers (ready, restarts, CPU, memory). A dash is
 * "not measured", never zero. Filters live in the URL so a view can be
 * shared; "Map" draws the chosen environment's service map.
 */

import { Activity, ChevronRight, LayoutGrid, Layers, Map as MapIcon, Plus, Rows3 } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useMemo } from "react";

import { AppGrid, AppShelf, EnvCard, EnvGrid } from "@/components/apps/AppLauncher";
import { ServiceCard } from "@/components/apps/ServiceCard";
import { LoadGate, useApi } from "@/components/catalog/primitives";
import {
  BindingStateBadge,
  HealthBadge,
  STATUS_LABELS,
  STATUS_ORDER,
  StateCard,
  toneForServiceStatus,
  useAge,
} from "@/components/service-health/primitives";
import { ServiceMapPanel } from "@/components/service-map/ServiceMapPanel";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";
import { DataState } from "@/components/state/DataState";
import { SearchInput, SegmentedControl, Select } from "@/components/ui/controls";
import { Panel } from "@/components/ui/Panel";
import { toneForThreshold, toneSpec, type StatusTone } from "@/lib/design/status";
import { useFormat, useT } from "@/lib/i18n";
import {
  serviceHealthListPath,
  type ServiceHealthPage,
  type ServiceHealthRow,
  type ServiceHealthStatus,
} from "@/lib/serviceHealth";
import {
  boundIds,
  judgeSignal,
  signalsPath,
  type SignalItem,
  type SignalPage,
  type SignalVerdict,
} from "@/lib/signals";
import { answered } from "@/lib/dashboards";
import { groupApps, pulseOf, usePlatformOverview, useProjectNames } from "@/lib/platform";
import { useResource } from "@/lib/useResource";
import { ProjectLogo } from "@/components/ui/ProjectLogo";

/** The pressure bands the platform already judges utilisation by. */
const UTILISATION = { warn: 0.8, critical: 0.9, direction: "above" as const };
const RESTARTS = { warn: 1, critical: 5, direction: "above" as const };
const SLOW_P95_MS = 1000;
const WARN_P95_MS = 500;

/* Keyed to the table's own width. Under 60rem a row stacks; from 60rem it is
 * one aligned line per service. */
const ROW_GRID =
  "@min-[60rem]/table:grid @min-[60rem]/table:grid-cols-[minmax(9rem,1fr)_8.5rem_4.5rem_4rem_4.5rem_3.5rem_4.75rem_3.5rem_4.5rem_2rem] @min-[60rem]/table:items-center @min-[60rem]/table:gap-2.5";

type Filters = {
  q: string;
  project: string;
  environment: string;
  status: ServiceHealthStatus | "";
  view: "apps" | "list" | "map";
};

function useFilters(): [Filters, (patch: Partial<Filters>) => void] {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname() || "/service-health";
  const filters: Filters = {
    q: params.get("q") ?? "",
    project: params.get("project_id") ?? "",
    environment: params.get("environment_id") ?? "",
    status: (params.get("status") as ServiceHealthStatus | null) ?? "",
    view: params.get("view") === "map" ? "map" : params.get("view") === "list" ? "list" : "apps",
  };
  const update = useCallback(
    (patch: Partial<Filters>) => {
      const next = new URLSearchParams(params.toString());
      const set = (key: string, value: string | undefined) => {
        if (value) next.set(key, value);
        else next.delete(key);
      };
      if ("q" in patch) set("q", patch.q);
      if ("project" in patch) set("project_id", patch.project);
      if ("environment" in patch) set("environment_id", patch.environment);
      if ("status" in patch) set("status", patch.status);
      if ("view" in patch) set("view", patch.view === "apps" ? undefined : patch.view);
      const query = next.toString();
      router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
    },
    [params, pathname, router],
  );
  return [filters, update];
}

const TONE_RANK: Record<string, number> = { critical: 0, warning: 1, success: 2 };

function sortRows(rows: ServiceHealthRow[], signals: Map<string, SignalItem>): ServiceHealthRow[] {
  const verdict = (row: ServiceHealthRow) => {
    const item = row.binding ? signals.get(row.binding.id) : undefined;
    return item ? (TONE_RANK[judgeSignal(item).tone] ?? 3) : 3;
  };
  return [...rows].sort(
    (a, b) =>
      STATUS_ORDER.indexOf(a.health.status) - STATUS_ORDER.indexOf(b.health.status) ||
      verdict(a) - verdict(b) ||
      (a.display_name || a.service_key).localeCompare(b.display_name || b.service_key),
  );
}

function Cell({ label, children, align = "right" }: { label: string; children: React.ReactNode; align?: "left" | "right" }) {
  return (
    <div className={`min-w-0 ${align === "right" ? "@min-[60rem]/table:text-right" : ""}`}>
      <span className="block text-micro text-ink-muted @min-[60rem]/table:hidden">{label}</span>
      {children}
    </div>
  );
}

function Num({ value, tone }: { value: string; tone?: StatusTone | null }) {
  const missing = value === "—";
  const colour =
    missing ? "text-ink-muted" : tone === "critical" ? "font-semibold text-critical" : tone === "warning" ? "font-semibold text-warning" : "text-ink";
  return (
    <span data-tabular className={`font-mono text-[0.8125rem] ${colour}`}>
      {value}
    </span>
  );
}

function ServiceRow({
  row,
  item,
  verdict,
}: {
  row: ServiceHealthRow;
  item: SignalItem | undefined;
  verdict: SignalVerdict | null;
}) {
  const t = useT("serviceHealth");
  const ts = useT("signals");
  const fmt = useFormat();
  const age = useAge();
  const { binding, health } = row;
  const name = row.display_name || row.service_key;
  const traces = item?.traces ?? null;
  const ready = health.availability?.ready_replicas ?? null;
  const desired = health.availability?.desired_replicas ?? null;
  const readyTone: StatusTone | undefined =
    ready === null || !desired ? undefined : ready >= desired ? undefined : ready === 0 ? "critical" : "warning";
  const p95 = traces?.p95_ms ?? null;
  const failedRatio = traces && traces.requests > 0 ? traces.error_ratio : null;
  const critical = health.status === "critical";
  const restarts = health.stability?.restarts_in_window ?? null;
  const cpu = health.resources?.cpu_utilization ?? null;
  const memory = health.resources?.memory_utilization ?? null;
  const pct = (ratio: number | null) => (ratio === null ? "—" : `${fmt.number(ratio * 100, { maximumFractionDigits: 0 })}%`);

  return (
    <li
      data-testid={`service-row-${row.service_key}`}
      className={`relative flex flex-col gap-3 px-5 py-3 transition-colors hover:bg-surface-hover ${critical ? "bg-critical-soft/60" : ""} ${ROW_GRID}`}
    >
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
            <span className="truncate text-body font-semibold text-ink">{name}</span>
          )}
          {health.served_from_last_good ? (
            <span className="text-micro text-stale">{t("list.row.lastKnown")}</span>
          ) : health.partial ? (
            <span className="text-micro text-ink-muted">{t("list.row.partial")}</span>
          ) : null}
        </div>
        <div className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-micro text-ink-muted">
          {binding ? (
            <>
              {/* The binding's own state only when it needs attention: a
                  resolved, active binding is the normal case. */}
              {binding.lifecycle !== "active" || !binding.resolved ? (
                <BindingStateBadge lifecycle={binding.lifecycle} resolved={binding.resolved} />
              ) : null}
              <span className="truncate font-mono">
                {binding.workload_kind} {binding.workload_name}
              </span>
            </>
          ) : (
            <span>{t("list.row.noWorkload")}</span>
          )}
          {verdict?.cause ? (
            <span className={toneSpec(verdict.tone).text}>· {ts(`cause.${verdict.cause}`)}</span>
          ) : null}
          {health.freshness_age_seconds !== null ? <span>· {age(health.freshness_age_seconds)}</span> : null}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 @min-[60rem]/table:block">
        <HealthBadge status={health.status} />
      </div>

      <div className="grid grid-cols-4 gap-3 @min-[60rem]/table:contents">
        <Cell label={t("list.table.requests")}>
          <Num value={traces ? fmt.number(traces.requests) : "—"} />
        </Cell>
        <Cell label={t("list.table.failed")}>
          <Num
            value={failedRatio === null ? "—" : `${fmt.number(failedRatio * 100, { maximumFractionDigits: failedRatio < 0.1 ? 1 : 0 })}%`}
            tone={verdict?.cause === "requests" ? verdict.tone : undefined}
          />
        </Cell>
        <Cell label={t("list.table.p95")}>
          <Num
            value={fmt.latency(p95)}
            tone={p95 === null ? undefined : p95 >= SLOW_P95_MS ? "critical" : p95 >= WARN_P95_MS ? "warning" : undefined}
          />
        </Cell>
        <Cell label={t("list.row.ready")}>
          <Num value={ready === null ? "—" : `${ready}/${desired ?? "—"}`} tone={readyTone} />
        </Cell>
      </div>

      <div className="grid grid-cols-4 gap-3 @min-[60rem]/table:contents">
        <Cell label={t("list.row.restarts")}>
          <Num value={restarts === null ? "—" : fmt.number(restarts)} tone={toneForThreshold(restarts, RESTARTS)} />
        </Cell>
        <Cell label={t("list.row.cpu")}>
          <Num value={pct(cpu)} tone={toneForThreshold(cpu, UTILISATION)} />
        </Cell>
        <Cell label={t("list.row.memory")}>
          <Num value={pct(memory)} tone={toneForThreshold(memory, UTILISATION)} />
        </Cell>
        <div className="relative z-10 flex justify-end">
          {binding ? (
            <ChevronRight aria-hidden className="h-4 w-4 text-ink-muted" />
          ) : (
            <Link
              href={`/service-health/bind?environment_service_id=${row.environment_service_id}`}
              aria-label={t("list.row.bind")}
              title={t("list.row.bind")}
              className="inline-flex h-7 items-center gap-1 whitespace-nowrap rounded-control border border-border bg-surface px-2 text-caption font-medium text-ink transition-colors hover:bg-surface-hover"
            >
              <Plus className="h-3.5 w-3.5" aria-hidden />
            </Link>
          )}
        </div>
      </div>
    </li>
  );
}

interface Group {
  key: string;
  projectId: string;
  projectKey: string;
  environmentKey: string;
  environmentId: string;
  rows: ServiceHealthRow[];
}

function groupRows(rows: ServiceHealthRow[]): Group[] {
  const groups = new Map<string, Group>();
  for (const row of rows) {
    const key = `${row.project_key}/${row.environment_key}`;
    const group =
      groups.get(key) ??
      { key, projectId: row.project_id, projectKey: row.project_key, environmentKey: row.environment_key, environmentId: row.environment_id, rows: [] };
    group.rows.push(row);
    groups.set(key, group);
  }
  // Groups follow their worst row, so the page reads top-down by urgency.
  const worst = (g: Group) => Math.min(...g.rows.map((r) => STATUS_ORDER.indexOf(r.health.status)));
  return [...groups.values()].sort((a, b) => worst(a) - worst(b) || a.key.localeCompare(b.key));
}

function GroupHeader({ group, onMap }: { group: Group; onMap: () => void }) {
  const t = useT("serviceHealth");
  const counts = new Map<ServiceHealthStatus, number>();
  for (const row of group.rows) counts.set(row.health.status, (counts.get(row.health.status) ?? 0) + 1);
  const bound = group.rows.filter((r) => r.binding).length;
  const first = group.rows.find((r) => r.binding)?.binding;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border bg-surface-2 px-5 py-2.5">
      <h3 className="flex items-center gap-2.5 text-[0.84375rem] font-semibold text-ink">
        <ProjectLogo projectId={group.projectId} name={group.projectKey} size="sm" />
        <span>
          {group.projectKey} <span className="text-ink-muted">/</span> {group.environmentKey}
        </span>
      </h3>
      <ul className="flex flex-wrap items-center gap-x-3 gap-y-1 text-micro text-ink-secondary">
        {STATUS_ORDER.filter((s) => counts.get(s)).map((status) => (
          <li key={status} className="inline-flex items-center gap-1.5">
            <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${toneSpec(toneForServiceStatus(status)).dot}`} />
            {counts.get(status)} {t.dyn("status", status, STATUS_LABELS[status]).toLocaleLowerCase()}
          </li>
        ))}
      </ul>
      <span className="ml-auto flex items-center gap-3 text-micro text-ink-muted">
        {first ? (
          <span className="hidden font-mono md:inline">
            {t("list.group.cluster", { cluster: first.cluster.cluster_ref, namespace: first.namespace })}
          </span>
        ) : null}
        <span data-tabular>{t("list.group.bound", { bound, total: group.rows.length })}</span>
        {bound > 0 ? (
          <button
            type="button"
            onClick={onMap}
            className="inline-flex items-center gap-1 rounded-control px-1.5 py-0.5 font-medium text-brand hover:bg-brand-soft"
          >
            <MapIcon className="h-3.5 w-3.5" aria-hidden />
            {t("list.group.openMap")}
          </button>
        ) : null}
      </span>
    </div>
  );
}

function StatusChips({
  rows,
  value,
  onChange,
}: {
  rows: ServiceHealthRow[];
  value: ServiceHealthStatus | "";
  onChange: (next: ServiceHealthStatus | "") => void;
}) {
  const t = useT("serviceHealth");
  const tc = useT("common");
  const counts = new Map<ServiceHealthStatus, number>();
  for (const row of rows) counts.set(row.health.status, (counts.get(row.health.status) ?? 0) + 1);
  const chip = (active: boolean) =>
    `inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-caption font-medium transition-colors ${
      active ? "border-border-strong bg-surface-selected text-ink" : "border-border bg-surface text-ink-secondary hover:bg-surface-hover"
    }`;
  return (
    <div role="group" aria-label={t("list.filter.label")} className="flex flex-wrap items-center gap-1.5">
      <button type="button" aria-pressed={value === ""} className={chip(value === "")} onClick={() => onChange("")}>
        {tc("count.all")}
        <span data-tabular className="font-mono text-micro text-ink-muted">{rows.length}</span>
      </button>
      {STATUS_ORDER.filter((s) => counts.get(s)).map((status) => (
        <button
          key={status}
          type="button"
          aria-pressed={value === status}
          className={chip(value === status)}
          onClick={() => onChange(value === status ? "" : status)}
        >
          <span aria-hidden className={`h-2 w-2 rounded-full ${toneSpec(toneForServiceStatus(status)).dot}`} />
          {t.dyn("status", status, STATUS_LABELS[status])}
          <span data-tabular className="font-mono text-micro text-ink-muted">{counts.get(status)}</span>
        </button>
      ))}
    </div>
  );
}

function ServicesBody({ data }: { data: ServiceHealthPage }) {
  const t = useT("serviceHealth");
  const ts = useT("signals");
  const [filters, update] = useFilters();
  const signals = useResource<SignalPage>(signalsPath(boundIds(data.items)), { refreshMs: 60_000 });
  const signalById = useMemo(
    () => new Map((signals.data?.items ?? []).map((item) => [item.binding_id, item])),
    [signals.data],
  );

  const projects = useMemo(() => {
    const seen = new Map<string, string>();
    for (const row of data.items) seen.set(row.project_id, row.project_key);
    return [...seen.entries()].map(([value, label]) => ({ value, label })).sort((a, b) => a.label.localeCompare(b.label));
  }, [data.items]);
  const environments = useMemo(() => {
    const seen = new Map<string, string>();
    for (const row of data.items) {
      if (filters.project && row.project_id !== filters.project) continue;
      seen.set(row.environment_id, `${row.project_key}/${row.environment_key}`);
    }
    return [...seen.entries()].map(([value, label]) => ({ value, label })).sort((a, b) => a.label.localeCompare(b.label));
  }, [data.items, filters.project]);

  const scoped = useMemo(
    () =>
      data.items.filter(
        (row) =>
          (!filters.project || row.project_id === filters.project) &&
          (!filters.environment || row.environment_id === filters.environment),
      ),
    [data.items, filters.project, filters.environment],
  );
  const visible = useMemo(() => {
    const q = filters.q.trim().toLocaleLowerCase();
    return sortRows(
      scoped.filter(
        (row) =>
          (!filters.status || row.health.status === filters.status) &&
          (!q || `${row.display_name ?? ""} ${row.service_key}`.toLocaleLowerCase().includes(q)),
      ),
      signalById,
    );
  }, [scoped, filters.status, filters.q, signalById]);
  const groups = useMemo(() => groupRows(visible), [visible]);

  const projectCount = new Set(scoped.map((r) => r.project_id)).size;
  const environmentCount = new Set(scoped.map((r) => r.environment_id)).size;
  const tracesOff = signals.data?.traces.state === "not_configured";
  const filtered = Boolean(filters.q || filters.status || filters.project || filters.environment);

  const mapEnvironments = environments.filter((env) =>
    data.items.some((row) => row.environment_id === env.value && row.binding),
  );
  const mapEnvironment =
    mapEnvironments.find((env) => env.value === filters.environment)?.value ?? mapEnvironments[0]?.value ?? "";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-caption text-ink-muted">
          {t("list.summary", { projects: projectCount, environments: environmentCount, services: scoped.length })}
        </p>
        <SegmentedControl
          label={t("list.view.label")}
          value={filters.view}
          options={[
            { value: "apps", label: t("list.view.apps"), icon: LayoutGrid },
            { value: "list", label: t("list.view.list"), icon: Rows3 },
            { value: "map", label: t("list.view.map"), icon: MapIcon },
          ]}
          onChange={(view) => update({ view })}
        />
      </div>

      {filters.view === "apps" ? (
        <ServicesLauncher
          rows={data.items}
          signalById={signalById}
          project={filters.project}
          environment={filters.environment}
          onProject={(project) => update({ project, environment: "" })}
          onEnvironment={(environment) => update({ environment })}
        />
      ) : null}

      <div className={`flex flex-wrap items-center gap-2 ${filters.view === "apps" ? "hidden" : ""}`}>
        <SearchInput
          label={t("list.search")}
          placeholder={t("list.search")}
          value={filters.q}
          onChange={(q) => update({ q })}
          className="w-full sm:w-64"
        />
        <Select
          label={t("list.project")}
          value={filters.project}
          placeholder={t("list.anyScope")}
          options={projects}
          onChange={(project) => update({ project, environment: "" })}
        />
        <Select
          label={t("list.environment")}
          value={filters.environment}
          placeholder={t("list.anyScope")}
          options={environments}
          onChange={(environment) => update({ environment })}
        />
        {filters.view === "list" ? (
          <StatusChips rows={scoped} value={filters.status} onChange={(status) => update({ status })} />
        ) : null}
      </div>

      {filters.view === "apps" ? null : filters.view === "map" ? (
        mapEnvironment ? (
          <div className="space-y-3">
            {mapEnvironments.length > 1 ? (
              <Select
                label={t("list.mapPick")}
                value={mapEnvironment}
                options={mapEnvironments}
                onChange={(environment) => update({ environment })}
              />
            ) : null}
            <ServiceMapPanel
              environmentId={mapEnvironment}
              environmentLabel={mapEnvironments.find((e) => e.value === mapEnvironment)?.label}
            />
          </div>
        ) : (
          <Panel>
            <DataState kind="empty" title={t("list.mapNone")} description={t("list.mapNoneHint")} />
          </Panel>
        )
      ) : (
        <Panel flush className="@container/table">
          <div
            aria-hidden
            className={`hidden border-b border-border px-5 py-2.5 font-mono text-[0.6875rem] font-medium tracking-[0.06em] text-ink-muted uppercase ${ROW_GRID}`}
          >
            <span>{t("list.table.service")}</span>
            <span>{t("list.table.status")}</span>
            <span className="text-right">{t("list.table.requests")}</span>
            <span className="text-right">{t("list.table.failed")}</span>
            <span className="text-right">{t("list.table.p95")}</span>
            <span className="text-right">{t("list.table.ready")}</span>
            <span className="text-right">{t("list.table.restarts")}</span>
            <span className="text-right">{t("list.table.cpu")}</span>
            <span className="text-right">{t("list.table.memory")}</span>
            <span />
          </div>
          {groups.length === 0 ? (
            <div className="p-5">
              <DataState kind="empty" title={t("list.noMatch")} description={t("list.noMatchHint")} />
            </div>
          ) : (
            <div data-testid="service-health-table">
              {groups.map((group) => (
                <section key={group.key} aria-label={group.key}>
                  <GroupHeader
                    group={group}
                    onMap={() => update({ view: "map", environment: group.environmentId })}
                  />
                  <ul className="divide-y divide-border">
                    {group.rows.map((row) => {
                      const item = row.binding ? signalById.get(row.binding.id) : undefined;
                      return (
                        <ServiceRow
                          key={row.environment_service_id}
                          row={row}
                          item={item}
                          verdict={item ? judgeSignal(item) : null}
                        />
                      );
                    })}
                  </ul>
                </section>
              ))}
            </div>
          )}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-border px-5 py-3 text-micro text-ink-muted">
            <Activity className="h-3.5 w-3.5" aria-hidden />
            <span>{t("list.table.showing", { shown: data.items.length, total: data.total })}</span>
            <span>{t("list.table.dashNote")}</span>
            {tracesOff ? <span>{t("list.tracesOff")}</span> : null}
            {signals.data?.traces.state === "unavailable" ? <span>{ts("partial.traces")}</span> : null}
            {filtered ? (
              <button
                type="button"
                onClick={() => update({ q: "", status: "", project: "", environment: "" })}
                className="ml-auto font-medium text-brand hover:underline"
              >
                {t("list.clearFilters")}
              </button>
            ) : null}
          </div>
        </Panel>
      )}
    </div>
  );
}

/**
 * Apps, then the chosen app's environments, then that environment's
 * services as cards. Project and environment are the same URL filters the
 * list uses, so switching views keeps the place.
 */
function ServicesLauncher({
  rows,
  signalById,
  project,
  environment,
  onProject,
  onEnvironment,
}: {
  rows: ServiceHealthRow[];
  signalById: Map<string, SignalItem>;
  project: string;
  environment: string;
  onProject: (project: string) => void;
  onEnvironment: (environment: string) => void;
}) {
  const ta = useT("apps");
  const names = useProjectNames();
  const apps = useMemo(() => groupApps(rows, names), [rows, names]);
  const { overview, failed } = usePlatformOverview(rows);
  const traffic = answered(overview?.traffic);

  const selected = apps.find((a) => a.projectId === project) ?? (apps.length === 1 ? apps[0] : null);
  const env =
    selected?.envs.find((e) => e.environmentId === environment) ??
    (selected && selected.envs.length === 1 ? selected.envs[0] : null);
  const services = env ? sortRows(env.rows, signalById) : [];

  return (
    <div className="flex flex-col gap-6" data-testid="services-launcher">
      <AppGrid apps={apps} selectedId={selected?.projectId ?? null} onSelect={(id) => onProject(id ?? "")} />
      {selected ? (
        <AppShelf key={selected.projectId} app={selected} onClose={() => onProject("")} title={ta("pickEnv")}>
          <EnvGrid>
            {selected.envs.map((e) => (
              <EnvCard
                key={e.environmentId}
                env={e}
                pulse={pulseOf(overview, e.bindingIds)}
                pending={!overview && !failed}
                active={env?.environmentId === e.environmentId}
                onSelect={() => onEnvironment(env?.environmentId === e.environmentId ? "" : e.environmentId)}
                cta={ta("pickService")}
              />
            ))}
          </EnvGrid>
          {env ? (
            <div className="mt-6" key={env.environmentId}>
              <h3 className="mb-3 flex items-baseline gap-2 text-[0.9375rem] font-semibold text-ink">
                {ta("pickService")}
                <span className="font-mono text-micro font-normal text-ink-muted">
                  {selected.projectKey}/{env.key} · {ta("services", { count: services.length })}
                </span>
              </h3>
              <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
                {services.map((row, i) => (
                  <ServiceCard
                    key={row.environment_service_id}
                    row={row}
                    index={i}
                    item={row.binding ? signalById.get(row.binding.id) : undefined}
                    traffic={
                      row.binding
                        ? (traffic?.find((x) => x.binding_id === row.binding!.id)?.buckets.map((b) => b.requests) ?? null)
                        : null
                    }
                  />
                ))}
              </ul>
            </div>
          ) : null}
        </AppShelf>
      ) : null}
    </div>
  );
}

function ServicesScreen() {
  const t = useT("serviceHealth");
  // The whole authorized list in one read; project and environment narrow it
  // on the client, so the pickers always offer every scope there is.
  const [page, retry] = useApi<ServiceHealthPage>(serviceHealthListPath({}));
  return (
    <LoadGate value={page} retry={retry}>
      {(data) =>
        data.items.length === 0 ? (
          <Panel>
            <StateCard icon={Layers} title={t("list.empty.title")} description={t("list.empty.description")} />
          </Panel>
        ) : (
          <ServicesBody data={data} />
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
        <ServicesScreen />
      </Suspense>
    </PageFrame>
  );
}
