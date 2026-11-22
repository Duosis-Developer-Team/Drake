"use client";

/**
 * The Traces and Logs screens.
 *
 * They open on the platform as a whole — an overview of requests or log
 * lines by application, service, route and message — and then the app
 * launcher: an application, one of its environments, and that
 * environment's services ranked by what this screen is about (busiest first
 * for traces, errors first for logs). The chosen service's explorer opens
 * right below, so the path to a trace or a log line is three taps, all of
 * them visible.
 *
 * Every choice lives in the URL (`project_id`, `environment_id`,
 * `binding`), so a view can be shared, and a link that names only a
 * binding (from a service page) lands on its application and environment.
 * The previous service picker stays one tap away as the List view.
 */

import { ArrowUpRight, LayoutGrid, Rows3 } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useMemo, useRef, useState } from "react";

import { AppGrid, AppShelf, EnvCard, EnvGrid } from "@/components/apps/AppLauncher";
import { LoadGate, useApi } from "@/components/catalog/primitives";
import { HealthBadge } from "@/components/service-health/primitives";
import { LogExplorer } from "@/components/service-health/LogExplorer";
import { TraceExplorer } from "@/components/service-health/TraceExplorer";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";
import { DataState } from "@/components/state/DataState";
import { SegmentedControl } from "@/components/ui/controls";
import { Panel } from "@/components/ui/Panel";
import type { OverviewRange } from "@/lib/dashboards";
import { useFormat, useT } from "@/lib/i18n";
import { groupApps, pulseOf, usePlatformOverview, useProjectNames, type PlatformEnv } from "@/lib/platform";
import { serviceHealthListPath, type ServiceHealthPage, type ServiceHealthRow } from "@/lib/serviceHealth";
import { MAX_OVERVIEW_BINDINGS } from "@/lib/dashboards";
import { boundIds, signalsPath, type SignalItem, type SignalPage } from "@/lib/signals";
import { useResource } from "@/lib/useResource";

import { LogsOverview, TracesOverview } from "./ExploreOverview";
import { ServiceExplorer } from "./ServiceExplorer";

type Kind = "traces" | "logs";

/** Busiest first for traces; errors, then warnings, then volume for logs. */
function rankScore(kind: Kind, item: SignalItem | undefined): number {
  if (kind === "traces") return item?.traces?.requests ?? -1;
  const logs = item?.logs;
  return logs ? logs.errors * 1e9 + logs.warnings * 1e5 + logs.lines : -1;
}

function ServiceStrip({
  kind,
  env,
  signals,
  selected,
  onPick,
}: {
  kind: Kind;
  env: PlatformEnv;
  signals: Map<string, SignalItem>;
  selected: string;
  onPick: (bindingId: string) => void;
}) {
  const t = useT("explore");
  const fmt = useFormat();
  const bound = env.rows
    .filter((row) => row.binding)
    .map((row) => ({ row, item: signals.get(row.binding!.id) }))
    .sort((a, b) => rankScore(kind, b.item) - rankScore(kind, a.item));
  const unbound = env.rows.length - bound.length;
  const peak = Math.max(
    ...bound.map(({ item }) => (kind === "traces" ? (item?.traces?.requests ?? 0) : (item?.logs?.errors || item?.logs?.lines || 0))),
    1,
  );
  if (bound.length === 0) {
    return <p className="rounded-xl border border-dashed border-border px-4 py-6 text-center text-caption text-ink-muted">{t("launcher.noBound")}</p>;
  }
  return (
    <div>
      <ul className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4" data-testid="explore-services">
        {bound.map(({ row, item }) => {
          const id = row.binding!.id;
          const active = id === selected;
          const traces = item?.traces;
          const logs = item?.logs;
          const volume = kind === "traces" ? (traces?.requests ?? 0) : (logs?.errors || logs?.lines || 0);
          const stats: [string, string, string][] =
            kind === "traces"
              ? [
                  [t("launcher.reqHour"), traces ? fmt.number(traces.requests) : "—", ""],
                  [
                    t("launcher.failed"),
                    traces && traces.requests > 0 ? `${fmt.number((traces.error_ratio ?? 0) * 100, { maximumFractionDigits: 1 })}%` : "—",
                    traces?.errors ? "text-critical" : "",
                  ],
                  [t("launcher.p95"), fmt.latency(traces?.p95_ms ?? null), traces?.p95_ms != null && traces.p95_ms >= 1000 ? "text-warning" : ""],
                ]
              : [
                  [t("launcher.errorLines"), logs ? fmt.number(logs.errors) : "—", logs?.errors ? "text-critical" : ""],
                  [t("launcher.warnings"), logs ? fmt.number(logs.warnings) : "—", logs?.warnings ? "text-warning" : ""],
                  [t("launcher.lines"), logs ? fmt.number(logs.lines) : "—", ""],
                ];
          const barColour =
            kind === "logs" && logs?.errors ? "var(--status-critical)" : kind === "logs" && !logs?.lines ? "var(--surface-3)" : "var(--brand-accent)";
          return (
            <li key={row.environment_service_id} className="min-w-0">
              <button
                type="button"
                aria-pressed={active}
                onClick={() => onPick(id)}
                data-testid={`explore-service-${row.service_key}`}
                className={`group flex h-full w-full flex-col rounded-xl border bg-surface px-3.5 py-3 text-left transition-[border-color,box-shadow] duration-150 outline-none focus-visible:ring-2 focus-visible:ring-focus ${
                  active ? "border-brand-accent/70 ring-1 ring-brand-accent/40" : "border-border hover:border-border-strong"
                }`}
              >
                <span className="flex items-center justify-between gap-2">
                  <span className="truncate text-[0.875rem] font-semibold text-ink">{row.display_name || row.service_key}</span>
                  <HealthBadge status={row.health.status} />
                </span>
                <span className="mt-2 block h-1 overflow-hidden rounded-full bg-surface-3">
                  <span className="block h-full rounded-full" style={{ width: `${Math.max(3, (volume / peak) * 100)}%`, background: barColour }} />
                </span>
                <span className="mt-2.5 grid grid-cols-3 gap-2">
                  {stats.map(([label, value, tone]) => (
                    <span key={label} className="min-w-0">
                      <span className="block truncate text-[10.5px] text-ink-muted">{label}</span>
                      <span data-tabular className={`block truncate font-mono text-[0.8125rem] font-semibold ${tone || (value === "—" ? "text-ink-muted" : "text-ink")}`}>
                        {value}
                      </span>
                    </span>
                  ))}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      {unbound > 0 ? <p className="mt-2 text-micro text-ink-muted">{t("launcher.unbound", { count: unbound })}</p> : null}
    </div>
  );
}

function AppsView({ kind, rows }: { kind: Kind; rows: ServiceHealthRow[] }) {
  const t = useT("explore");
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname() || `/${kind}`;
  const fmt = useFormat();
  const names = useProjectNames();
  const apps = useMemo(() => groupApps(rows, names), [rows, names]);
  const [range, setRange] = useState<OverviewRange>("24h");
  const { overview, failed, truncated } = usePlatformOverview(rows, range);
  const signalRead = useResource<SignalPage>(signalsPath(boundIds(rows)), { refreshMs: 60_000 });
  const signals = useMemo(
    () => new Map((signalRead.data?.items ?? []).map((item) => [item.binding_id, item])),
    [signalRead.data],
  );
  const launcherRef = useRef<HTMLDivElement>(null);
  const explorerRef = useRef<HTMLDivElement>(null);

  const set = useCallback(
    (patch: Record<string, string>) => {
      const next = new URLSearchParams(params.toString());
      for (const [key, value] of Object.entries(patch)) {
        if (value) next.set(key, value);
        else next.delete(key);
      }
      const query = next.toString();
      router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
    },
    [params, pathname, router],
  );

  // A link that names only a binding lands on its application and environment.
  const binding = params.get("binding") ?? "";
  const linked = binding ? rows.find((row) => row.binding?.id === binding) : undefined;
  const projectId = params.get("project_id") || linked?.project_id || "";
  const environmentId = params.get("environment_id") || linked?.environment_id || "";

  const app = apps.find((a) => a.projectId === projectId) ?? (apps.length === 1 ? apps[0] : null);
  const env =
    app?.envs.find((e) => e.environmentId === environmentId) ?? (app && app.envs.length === 1 ? app.envs[0] : null);
  const ranked = env
    ? env.rows
        .filter((row) => row.binding)
        .sort((a, b) => rankScore(kind, signals.get(b.binding!.id)) - rankScore(kind, signals.get(a.binding!.id)))
    : [];
  const current = ranked.find((row) => row.binding!.id === binding) ?? ranked[0] ?? null;

  const scrollTo = (ref: React.RefObject<HTMLDivElement | null>) =>
    requestAnimationFrame(() => ref.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
  const open = (bindingId: string) => {
    const row = rows.find((r) => r.binding?.id === bindingId);
    if (!row) return;
    set({ project_id: row.project_id, environment_id: row.environment_id, binding: bindingId });
    scrollTo(explorerRef);
  };
  const pickApp = (id: string) => {
    set({ project_id: id, environment_id: "", binding: "" });
    scrollTo(launcherRef);
  };

  const logStats = (e: PlatformEnv) => {
    const own = e.bindingIds.map((id) => signals.get(id)?.logs).filter((x): x is NonNullable<typeof x> => Boolean(x));
    if (own.length === 0) return [
      { label: t("launcher.errorLines"), value: "—" },
      { label: t("launcher.warnings"), value: "—" },
      { label: t("launcher.lines"), value: "—" },
    ];
    const sum = (k: "errors" | "warnings" | "lines") => own.reduce((a, x) => a + x[k], 0);
    const fmtN = (v: number) => fmt.number(v);
    return [
      { label: t("launcher.errorLines"), value: fmtN(sum("errors")), tone: sum("errors") ? "text-critical" : undefined },
      { label: t("launcher.warnings"), value: fmtN(sum("warnings")), tone: sum("warnings") ? "text-warning" : undefined },
      { label: t("launcher.lines"), value: fmtN(sum("lines")) },
    ];
  };

  const overviewProps = {
    rows,
    apps,
    overview,
    failed,
    truncated: truncated ? MAX_OVERVIEW_BINDINGS : null,
    range,
    onRange: setRange,
    onOpen: open,
    onPickApp: pickApp,
  };

  return (
    <div className="flex flex-col gap-8">
      {kind === "traces" ? (
        <TracesOverview {...overviewProps} />
      ) : (
        <LogsOverview {...overviewProps} signals={signalRead.data} />
      )}

      <div ref={launcherRef} className="flex scroll-mt-20 flex-col gap-4" data-testid="explore-launcher">
        <h2 className="px-0.5 text-[1.125rem] font-semibold tracking-[-0.02em] text-ink">{t("launcher.pickApp")}</h2>
        <AppGrid apps={apps} selectedId={app?.projectId ?? null} onSelect={(id) => set({ project_id: id ?? "", environment_id: "", binding: "" })} />
        {app ? (
          <AppShelf key={app.projectId} app={app} onClose={() => set({ project_id: "", environment_id: "", binding: "" })} title={t("launcher.pickEnv")}>
            <EnvGrid count={app.envs.length}>
              {app.envs.map((e) => (
                <EnvCard
                  key={e.environmentId}
                  env={e}
                  pulse={pulseOf(overview, e.bindingIds)}
                  pending={!overview && !failed}
                  active={env?.environmentId === e.environmentId}
                  onSelect={() =>
                    set({ project_id: app.projectId, environment_id: env?.environmentId === e.environmentId ? "" : e.environmentId, binding: "" })
                  }
                  cta={t("launcher.pickService")}
                  stats={kind === "logs" ? logStats(e) : undefined}
                />
              ))}
            </EnvGrid>
            {env ? (
              <div className="mt-6" key={env.environmentId}>
                <h3 className="mb-3 flex items-baseline gap-2 text-[0.9375rem] font-semibold text-ink">
                  {t("launcher.pickService")}
                  <span className="font-mono text-micro font-normal text-ink-muted">
                    {app.projectKey}/{env.key} · {kind === "traces" ? t("launcher.busiestFirst") : t("launcher.loudestFirst")}
                  </span>
                </h3>
                <ServiceStrip
                  kind={kind}
                  env={env}
                  signals={signals}
                  selected={current?.binding?.id ?? ""}
                  onPick={(id) => set({ project_id: app.projectId, environment_id: env.environmentId, binding: id })}
                />
              </div>
            ) : null}
          </AppShelf>
        ) : null}
      </div>

      {env && current ? (
        <div ref={explorerRef} className="flex scroll-mt-20 flex-col gap-3" data-testid="explore-explorer">
          <div className="flex flex-wrap items-center justify-between gap-3 px-0.5">
            <h2 className="text-[1.125rem] font-semibold tracking-[-0.02em] text-ink">
              {kind === "traces"
                ? t("launcher.tracesOf", { service: current.display_name || current.service_key })
                : t("launcher.logsOf", { service: current.display_name || current.service_key })}
              <span className="ml-2 font-mono text-micro font-normal text-ink-muted">
                {current.project_key}/{current.environment_key}
              </span>
            </h2>
            <Link
              href={`/service-health/${current.binding!.id}`}
              className="inline-flex items-center gap-1 text-caption font-medium text-brand-accent hover:underline"
            >
              {t("openService")}
              <ArrowUpRight className="h-3.5 w-3.5" aria-hidden />
            </Link>
          </div>
          {kind === "traces" ? (
            <TraceExplorer key={current.binding!.id} bindingId={current.binding!.id} />
          ) : (
            <LogExplorer key={current.binding!.id} bindingId={current.binding!.id} />
          )}
        </div>
      ) : null}
    </div>
  );
}

function AppsScreen({ kind }: { kind: Kind }) {
  const t = useT("explore");
  const [page, retry] = useApi<ServiceHealthPage>(serviceHealthListPath({}));
  return (
    <LoadGate value={page} retry={retry}>
      {(data) =>
        data.items.some((row) => row.binding) ? (
          <AppsView kind={kind} rows={data.items} />
        ) : (
          <Panel>
            <DataState kind="empty" title={t("none")} />
          </Panel>
        )
      }
    </LoadGate>
  );
}

export function ExploreScreen({ kind }: { kind: Kind }) {
  const t = useT("explore");
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname() || `/${kind}`;
  const view = params.get("view") === "list" ? "list" : "apps";
  const setView = (next: string) => {
    const query = new URLSearchParams(params.toString());
    if (next === "list") query.set("view", "list");
    else query.delete("view");
    const qs = query.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  };
  return (
    <PageFrame width="wide">
      <PageHeader
        title={t(`${kind}.title`)}
        description={t(`${kind}.description`)}
        actions={
          <SegmentedControl
            label={t("view.label")}
            value={view}
            options={[
              { value: "apps", label: t("view.apps"), icon: LayoutGrid },
              { value: "list", label: t("view.list"), icon: Rows3 },
            ]}
            onChange={setView}
          />
        }
      />
      {view === "list" ? <ServiceExplorer kind={kind} /> : <AppsScreen kind={kind} />}
    </PageFrame>
  );
}
