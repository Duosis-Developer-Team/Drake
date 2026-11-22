"use client";

/**
 * Alert rules (ADR-0031): what is firing now, how every rule behaved over
 * the last day, and where a rule is written.
 *
 * The list refreshes on the runner's cadence (each minute). Every number on
 * the page is the API's: states and episodes from the runner's own rows,
 * charts from the samples it stored. The browser decides nothing about
 * whether a rule fired.
 */

import {
  BellOff,
  ChevronDown,
  Gauge,
  ListChecks,
  Plus,
  ScrollText,
  ShieldCheck,
  Siren,
  TriangleAlert,
  Waypoints,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { kindColour } from "@/components/apps/AppLauncher";
import { PageFrame } from "@/components/shell/AppShell";
import { DataState } from "@/components/state/DataState";
import { ProjectLogo } from "@/components/ui/ProjectLogo";
import {
  deleteRule,
  fetchRuleHistory,
  fetchRuleOptions,
  fetchRules,
  formatSignal,
  muteRule,
  updateRule,
  SIGNAL_SOURCE,
  type AlertRule,
  type RuleHistory,
  type RuleList,
  type RuleOptions,
  type SignalSource,
} from "@/lib/alertRules";
import { useFormat, useT } from "@/lib/i18n";
import { useProjectNames } from "@/lib/platform";
import { useSession } from "@/lib/session";

import s from "./alertRules.module.css";
import {
  DAY_MS,
  Lane,
  SignalChart,
  Spark,
  hhmm,
  severityTone,
  tone,
  type Span,
} from "./instruments";
import {
  RuleDrawer,
  TEMPLATE_KEYS,
  filteredName,
  sentenceFor,
  templateSignal,
  useDuration,
  type TemplateKey,
} from "./RuleDrawer";

const SOURCE_ICON: Record<SignalSource, LucideIcon> = { metrics: Gauge, logs: ScrollText, traces: Waypoints };

const REFRESH_MS = 60_000;
type Filter = "all" | "firing" | "ok" | "muted";
const STATUS_ORDER: Record<AlertRule["status"], number> = {
  firing: 0,
  pending: 1,
  ok: 2,
  no_data: 3,
  muted: 4,
  disabled: 5,
};

function matches(rule: AlertRule, filter: Filter): boolean {
  if (filter === "all") return true;
  if (filter === "firing") return rule.status === "firing" || rule.status === "pending";
  if (filter === "muted") return rule.status === "muted" || rule.status === "disabled";
  return rule.status === "ok" || rule.status === "no_data";
}

/** The worst current value across a rule's bindings. */
function worstValue(rule: AlertRule): number | null {
  const values = rule.states
    .map((state) => state.last_value)
    .filter((value): value is number => value !== null);
  if (!values.length) return null;
  return rule.operator === "<" ? Math.min(...values) : Math.max(...values);
}

function firingSince(rule: AlertRule): number | null {
  const times = rule.states
    .filter((state) => state.state === "firing" && state.firing_since)
    .map((state) => Date.parse(state.firing_since as string));
  return times.length ? Math.min(...times) : null;
}

export function AlertRulesScreen() {
  const t = useT("alertRules");
  const fmt = useFormat();
  const duration = useDuration();
  const { state: session } = useSession();
  const csrfToken = session.status === "authenticated" ? session.me.csrf_token : null;

  const [list, setList] = useState<RuleList | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [options, setOptions] = useState<RuleOptions | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [selected, setSelected] = useState<string | null>(null);
  const [histories, setHistories] = useState<Record<string, RuleHistory>>({});
  const [tip, setTip] = useState<{ x: number; y: number; text: string } | null>(null);
  const [drawer, setDrawer] = useState<{ rule: AlertRule | null; template: TemplateKey | null } | null>(
    null,
  );
  const [mutingAll, setMutingAll] = useState<"idle" | "busy" | "done">("idle");
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const rowRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const names = useProjectNames();

  const load = useCallback(async () => {
    try {
      const next = await fetchRules();
      setList(next);
      setLoadError(null);
    } catch (error: unknown) {
      setLoadError(error);
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  useEffect(() => {
    fetchRuleOptions()
      .then(setOptions)
      .catch(() => setOptions({ signals: [], projects: [] }));
  }, []);

  const now = list ? Date.parse(list.as_of) : Date.now();
  const rules = useMemo(
    () =>
      [...(list?.rules ?? [])].sort(
        (a, b) => STATUS_ORDER[a.status] - STATUS_ORDER[b.status] || a.name.localeCompare(b.name),
      ),
    [list],
  );
  const firing = rules.filter((rule) => rule.status === "firing");
  const canCreate = Boolean(options && options.projects.length > 0 && csrfToken);

  // History for what is open and for the firing readouts' sparklines.
  const wanted = useMemo(() => {
    const ids = new Set(firing.slice(0, 4).map((rule) => rule.id));
    if (selected) ids.add(selected);
    return [...ids];
  }, [firing, selected]);
  const wantedKey = wanted.join(",") + (list?.as_of ?? "");
  useEffect(() => {
    let cancelled = false;
    for (const id of wanted) {
      fetchRuleHistory(id)
        .then((history) => {
          if (!cancelled) setHistories((current) => ({ ...current, [id]: history }));
        })
        .catch(() => undefined);
    }
    return () => {
      cancelled = true;
    };
    // wantedKey re-fetches on each list refresh.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wantedKey]);

  // N opens the drawer, as on the prototype.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (drawer || !canCreate) return;
      if (target && /INPUT|SELECT|TEXTAREA/.test(target.tagName)) return;
      if (event.key === "n" || event.key === "N") {
        event.preventDefault();
        setDrawer({ rule: null, template: null });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawer, canCreate]);

  const open = (id: string | null, scroll: boolean) => {
    setSelected(id);
    setConfirmDelete(null);
    setActionError(null);
    if (id) {
      const rule = rules.find((r) => r.id === id);
      if (rule && !matches(rule, filter)) setFilter("all");
      if (scroll) {
        window.setTimeout(() => {
          rowRefs.current[id]?.scrollIntoView({
            behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
            block: "center",
          });
        }, 0);
      }
    }
  };

  const showTip = (event: React.MouseEvent, text: string) =>
    setTip({ x: event.clientX, y: event.clientY, text });

  const act = async (work: () => Promise<unknown>) => {
    setActionError(null);
    try {
      await work();
      await load();
    } catch (error: unknown) {
      setActionError(error instanceof Error ? error.message : String(error));
    }
  };

  const muteAll = async () => {
    if (!csrfToken) return;
    setMutingAll("busy");
    const targets = rules.filter(
      (rule) => rule.can_manage && rule.status !== "muted" && rule.status !== "disabled",
    );
    for (const rule of targets) {
      try {
        await muteRule(csrfToken, rule.id, 60);
      } catch {
        // One refusal does not stop the rest; the list below shows the truth.
      }
    }
    await load();
    setMutingAll("done");
    window.setTimeout(() => setMutingAll("idle"), 1600);
  };

  const all = { env: t("scope.allEnvironments"), svc: t("scope.allServices") };
  const show = (rule: Pick<AlertRule, "signal">, value: number | null | undefined) =>
    formatSignal(rule.signal, value, fmt.number);
  const time = (value: number) => hhmm(new Date(value), fmt.tag);

  const counts = {
    all: rules.length,
    firing: rules.filter((r) => matches(r, "firing")).length,
    ok: rules.filter((r) => matches(r, "ok")).length,
    muted: rules.filter((r) => matches(r, "muted")).length,
  };
  const manageable = rules.some(
    (rule) => rule.can_manage && rule.status !== "muted" && rule.status !== "disabled",
  );

  const visible = rules.filter((rule) => matches(rule, filter));
  const groups = (() => {
    const byProject = new Map<string, AlertRule[]>();
    for (const rule of visible) {
      const list = byProject.get(rule.project_id) ?? [];
      list.push(rule);
      byProject.set(rule.project_id, list);
    }
    return [...byProject.entries()]
      .map(([projectId, items]) => ({
        projectId,
        key: items[0].target.project,
        name: names.get(projectId) ?? items[0].target.project,
        items,
        firing: items.filter((rule) => rule.status === "firing").length,
      }))
      .sort((a, b) => b.firing - a.firing || a.name.localeCompare(b.name));
  })();
  const stateSegments = (
    [
      ["firing", rules.filter((r) => r.status === "firing").length, "var(--crit)"],
      ["pending", rules.filter((r) => r.status === "pending").length, "var(--warn)"],
      ["ok", rules.filter((r) => r.status === "ok").length, "var(--ok)"],
      ["no_data", rules.filter((r) => r.status === "no_data").length, "var(--track)"],
      ["muted", rules.filter((r) => r.status === "muted" || r.status === "disabled").length, "var(--mute)"],
    ] as const
  ).filter(([, count]) => count > 0);
  const critical = rules.filter((r) => r.severity === "critical").length;
  const segmentsOf = (items: AlertRule[]) =>
    (
      [
        ["firing", items.filter((r) => r.status === "firing").length, "var(--crit)"],
        ["pending", items.filter((r) => r.status === "pending").length, "var(--warn)"],
        ["ok", items.filter((r) => r.status === "ok").length, "var(--ok)"],
        ["no_data", items.filter((r) => r.status === "no_data").length, "var(--track)"],
        ["muted", items.filter((r) => r.status === "muted" || r.status === "disabled").length, "var(--mute)"],
      ] as const
    ).filter(([, count]) => count > 0);
  // Every app's rules by state, from every rule (not the filtered view).
  const appRows = (() => {
    const byProject = new Map<string, AlertRule[]>();
    for (const rule of rules) byProject.set(rule.project_id, [...(byProject.get(rule.project_id) ?? []), rule]);
    return [...byProject.entries()]
      .map(([projectId, items]) => ({
        projectId,
        name: names.get(projectId) ?? items[0].target.project,
        total: items.length,
        firing: items.filter((r) => r.status === "firing").length,
        segments: segmentsOf(items),
      }))
      .sort((a, b) => b.firing - a.firing || b.total - a.total)
      .slice(0, 5);
  })();
  // The day's firings across every rule, newest first.
  const recent = rules
    .flatMap((rule) => rule.episodes.map((episode) => ({ rule, episode })))
    .sort((a, b) => Date.parse(b.episode.fired_at) - Date.parse(a.episode.fired_at))
    .slice(0, firing.length > 1 ? 3 : 5);

  return (
    <PageFrame width="wide">
      <div className={s.root}>
        <section className="mb-6 flex flex-wrap items-end justify-between gap-4">
          <div className="min-w-0">
            <h1 className={s.h1}>
              {t("title")} <em>{t("titleWindow")}</em>
            </h1>
            <p className="mt-1.5 max-w-[46rem] text-caption leading-relaxed text-ink-secondary">{t("lede")}</p>
          </div>
          <div className={s.cta}>
            {manageable && csrfToken ? (
              <button
                type="button"
                className={`${s.btn} inline-flex items-center gap-1.5`}
                disabled={mutingAll !== "idle"}
                onClick={() => void muteAll()}
              >
                <BellOff aria-hidden className="h-3.5 w-3.5" />
                {mutingAll === "done" ? t("mutedAll") : t("muteAll")}
              </button>
            ) : null}
            {canCreate ? (
              <button
                type="button"
                className={`${s.btn} ${s.go} inline-flex items-center gap-1.5`}
                onClick={() => setDrawer({ rule: null, template: null })}
              >
                <Plus aria-hidden className="h-3.5 w-3.5" />
                {t("create")}
                <span className={s.kbd} aria-hidden="true">
                  N
                </span>
              </button>
            ) : null}
          </div>
        </section>

        {list === null ? (
          loadError ? (
            <DataState kind="error" description={t("loadError")} onRetry={() => void load()} />
          ) : (
            <DataState kind="loading" />
          )
        ) : rules.length === 0 ? (
          <section className="rounded-2xl border border-dashed border-border-strong bg-surface px-6 py-10 text-center">
            <span className="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-brand-soft text-brand-accent">
              <Workflow aria-hidden className="h-7 w-7" />
            </span>
            <h2 className="mt-4 font-[family-name:var(--font-display)] text-[1.25rem] font-bold tracking-[-0.02em] text-ink">
              {t("empty.title")}
            </h2>
            <p className="mx-auto mt-1.5 max-w-[34rem] text-caption text-ink-secondary">{t("empty.body")}</p>
            {canCreate ? (
              <>
                <p className="mt-6 mb-3 font-mono text-[10.5px] font-medium tracking-[0.14em] text-ink-muted uppercase">
                  {t("board.starters")}
                </p>
                <div className="mx-auto grid max-w-[60rem] gap-2.5 text-left sm:grid-cols-2 lg:grid-cols-3">
                  {TEMPLATE_KEYS.map((key) => {
                    const StarterIcon = SOURCE_ICON[SIGNAL_SOURCE[templateSignal(key)]];
                    return (
                      <button
                        key={key}
                        type="button"
                        onClick={() => setDrawer({ rule: null, template: key })}
                        className="group flex items-start gap-3 rounded-xl border border-border bg-surface px-3.5 py-3 text-left transition-[transform,border-color] hover:-translate-y-0.5 hover:border-border-strong focus-visible:ring-2 focus-visible:ring-focus"
                      >
                        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-surface-2 text-brand-accent">
                          <StarterIcon aria-hidden className="h-4 w-4" />
                        </span>
                        <span className="min-w-0">
                          <span className="block text-caption font-semibold text-ink">{t(`templates.${key}.name`)}</span>
                          <small className="block font-mono text-[11px] text-ink-muted">{t(`templates.${key}.detail`)}</small>
                        </span>
                      </button>
                    );
                  })}
                </div>
              </>
            ) : null}
          </section>
        ) : (
          <>
            <section
              aria-label={t("now.caption")}
              className="mb-6 grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]"
            >
              <div className="rounded-2xl border border-border bg-surface p-5">
                <div className="flex items-baseline justify-between gap-3">
                  <p className="m-0 font-mono text-[10.5px] font-medium tracking-[0.14em] text-ink-muted uppercase">
                    {t("board.byState")}
                  </p>
                  <span className="font-mono text-[11px] text-ink-muted">
                    {t("now.fired24h", { count: list?.summary.fired_24h ?? 0 })}
                  </span>
                </div>
                <div className="mt-3 flex h-2.5 w-full gap-[3px] overflow-hidden rounded-full" aria-hidden>
                  {stateSegments.map(([key, count, color]) => (
                    <span key={key} style={{ flexGrow: count, background: color }} />
                  ))}
                </div>
                <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
                  {(
                    [
                      ["firing", counts.firing, "var(--crit)", Siren],
                      ["ok", counts.ok, "var(--ok)", ShieldCheck],
                      ["muted", counts.muted, "var(--mute)", BellOff],
                      ["all", counts.all, "var(--accent)", ListChecks],
                    ] as const
                  ).map(([key, count, color, TileIcon]) => (
                    <button
                      key={key}
                      type="button"
                      aria-pressed={filter === key}
                      onClick={() => setFilter(key)}
                      className={`rounded-xl border px-3 py-2.5 text-left transition-colors outline-none focus-visible:ring-2 focus-visible:ring-focus ${
                        filter === key ? "border-border-strong bg-surface-selected" : "border-border hover:bg-surface-hover"
                      }`}
                    >
                      <span className="flex items-center gap-1.5 text-micro text-ink-secondary">
                        <TileIcon aria-hidden className="h-3.5 w-3.5" style={{ color }} />
                        {t(`tabs.${key}`)}
                      </span>
                      <b className="mt-1 block font-[family-name:var(--font-display)] text-[1.625rem] leading-none font-bold tracking-[-0.02em] text-ink tabular-nums">
                        {count}
                      </b>
                    </button>
                  ))}
                </div>
                <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1.5 border-t border-border pt-3 text-micro text-ink-secondary">
                  <span className="font-mono text-[10.5px] tracking-[0.14em] text-ink-muted uppercase">
                    {t("board.bySeverity")}
                  </span>
                  <span className="inline-flex items-center gap-1.5">
                    <span aria-hidden className="h-2 w-2 rounded-full bg-critical" />
                    {critical} {t("board.critical")}
                  </span>
                  <span className="inline-flex items-center gap-1.5">
                    <span aria-hidden className="h-2 w-2 rounded-full bg-warning" />
                    {rules.length - critical} {t("board.warning")}
                  </span>
                  <span className="ml-auto">
                    <b className="text-ink">{t("now.calm", { count: counts.ok })}</b>
                    {counts.muted ? ` · ${t("now.mutedCount", { count: counts.muted })}` : ""}
                  </span>
                </div>
                <div className="mt-4 border-t border-border pt-3">
                  <p className="mb-2.5 font-mono text-[10.5px] font-medium tracking-[0.14em] text-ink-muted uppercase">
                    {t("board.byApp")}
                  </p>
                  <ul className="m-0 flex list-none flex-col gap-2.5 p-0">
                    {appRows.map((row) => (
                      <li key={row.projectId} className="grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)_auto] items-center gap-3">
                        <span className="flex min-w-0 items-center gap-2">
                          <ProjectLogo projectId={row.projectId} name={row.name} size="sm" variant="mark" />
                          <span className="truncate text-caption text-ink">{row.name}</span>
                        </span>
                        <span className="flex h-2 gap-[2px] overflow-hidden rounded-full" aria-hidden>
                          {row.segments.map(([key, count, color]) => (
                            <span key={key} style={{ flexGrow: count, background: color }} />
                          ))}
                        </span>
                        <span className="font-mono text-[11px] text-ink-muted">
                          {t("board.groupMeta", { count: row.total, firing: row.firing })}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              </div>

              <div className="rounded-2xl border border-border bg-surface p-5">
                <div className="flex items-baseline justify-between gap-3">
                  <p className="m-0 flex items-center gap-2 font-mono text-[10.5px] font-medium tracking-[0.14em] text-ink-muted uppercase">
                    {firing.length ? (
                      <span className="relative flex h-2 w-2">
                        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[var(--status-critical)] opacity-60 motion-reduce:hidden" />
                        <span className="relative inline-flex h-2 w-2 rounded-full bg-[var(--status-critical)]" />
                      </span>
                    ) : null}
                    {t("now.caption")}
                  </p>
                  <span className="font-mono text-[11px] text-ink-muted">{t("now.count", { count: firing.length })}</span>
                </div>
                {firing.length === 0 ? (
                  <div className="mt-4 flex items-center gap-3 rounded-xl bg-healthy-soft/60 px-4 py-4">
                    <ShieldCheck aria-hidden className="h-6 w-6 shrink-0 text-healthy" />
                    <span>
                      <b className="block text-body text-ink">{t("now.none")}</b>
                      <span className="text-caption text-ink-secondary">{t("now.noneHint")}</span>
                    </span>
                  </div>
                ) : (
                  <div className="mt-3 flex flex-col gap-2">
                    {firing.map((rule) => {
                      const since = firingSince(rule);
                      const history = histories[rule.id];
                      const series =
                        history?.bindings.find((b) =>
                          rule.states.some((st) => st.state === "firing" && st.binding_id === b.binding_id),
                        ) ?? history?.bindings[0];
                      const color = severityTone(rule.severity);
                      return (
                        <button
                          key={rule.id}
                          type="button"
                          onClick={() => open(rule.id, true)}
                          className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-2 rounded-xl border px-3.5 py-3 text-left transition-colors outline-none hover:bg-surface-hover focus-visible:ring-2 focus-visible:ring-focus sm:grid-cols-[minmax(0,1fr)_auto_9rem]"
                          style={{ borderColor: `color-mix(in srgb, ${color} 45%, transparent)` }}
                        >
                          <span className="flex min-w-0 items-center gap-3">
                            <ProjectLogo projectId={rule.project_id} name={rule.target.project} size="md" variant="mark" />
                            <span className="min-w-0">
                              <span className="block truncate text-caption font-semibold text-ink">{rule.name}</span>
                              <span className="block truncate font-mono text-[11px]" style={{ color }}>
                                {t("now.since", {
                                  severity: t.dyn("severity", rule.severity),
                                  duration: since ? duration((now - since) / 1000) : "—",
                                })}
                              </span>
                            </span>
                          </span>
                          <span className="text-right">
                            <b className="block font-mono text-[1.0625rem] tabular-nums" style={{ color }}>
                              {show(rule, worstValue(rule))}
                            </b>
                            <span className="block font-mono text-[10.5px] text-ink-muted">
                              {t("now.threshold", { value: show(rule, rule.threshold) })}
                            </span>
                          </span>
                          <span className="col-span-2 sm:col-span-1">
                            <Spark points={series?.series ?? []} threshold={rule.threshold} color={color} tall={false} />
                          </span>
                        </button>
                      );
                    })}
                  </div>
                )}
                {recent.length ? (
                  <div className="mt-4 border-t border-border pt-3">
                    <p className="mb-2 font-mono text-[10.5px] font-medium tracking-[0.14em] text-ink-muted uppercase">
                      {t("board.recent")}
                    </p>
                    <ul className="m-0 flex list-none flex-col gap-1 p-0">
                      {recent.map(({ rule, episode }) => {
                        const fired = Date.parse(episode.fired_at);
                        const ended = episode.resolved_at ? Date.parse(episode.resolved_at) : null;
                        return (
                          <li key={rule.id + episode.binding_id + episode.fired_at}>
                            <button
                              type="button"
                              onClick={() => open(rule.id, true)}
                              className="grid w-full grid-cols-[3rem_0.5rem_minmax(0,1fr)_auto] items-center gap-2.5 rounded-lg px-2 py-1.5 text-left text-caption transition-colors hover:bg-surface-hover"
                            >
                              <span className="font-mono text-[11px] text-ink-muted">{time(fired)}</span>
                              <span
                                aria-hidden
                                className="h-2 w-2 rounded-full"
                                style={{ background: severityTone(rule.severity) }}
                              />
                              <span className="min-w-0 truncate text-ink">
                                {rule.name}
                                <span className="text-ink-muted"> · {episode.service}</span>
                              </span>
                              <span className="font-mono text-[11px] text-ink-secondary">
                                {ended ? duration((ended - fired) / 1000) : t("board.ongoing")}
                              </span>
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                ) : null}
              </div>
            </section>

            <section aria-label={t("register.title")}>
              <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                <h2 className="m-0 font-[family-name:var(--font-display)] text-[1.125rem] font-bold tracking-[-0.02em] text-ink">
                  {t("register.title")}
                </h2>
                <div className="flex flex-wrap gap-1.5" role="group">
                  {(["all", "firing", "ok", "muted"] as Filter[]).map((key) => (
                    <button
                      key={key}
                      type="button"
                      aria-pressed={filter === key}
                      onClick={() => setFilter(key)}
                      className={`inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-caption font-medium transition-colors ${
                        filter === key
                          ? "border-border-strong bg-surface-selected text-ink"
                          : "border-border bg-surface text-ink-secondary hover:bg-surface-hover"
                      }`}
                    >
                      {t(`tabs.${key}`)}
                      <span className="font-mono text-micro text-ink-muted">{counts[key]}</span>
                    </button>
                  ))}
                </div>
              </div>

              {visible.length === 0 ? (
                <div className="rounded-2xl border border-dashed border-border px-5 py-8 text-center text-caption text-ink-muted">
                  {t("register.empty")}
                </div>
              ) : null}

              <div className="flex flex-col gap-6">
                {groups.map((group) => (
                  <div key={group.projectId}>
                    <div className="mb-3 flex items-center gap-3">
                      <ProjectLogo projectId={group.projectId} name={group.name} size="md" variant="mark" />
                      <span>
                        <b className="block text-body text-ink">{group.name}</b>
                        <span className="text-micro text-ink-muted">
                          {t("board.groupMeta", { count: group.items.length, firing: group.firing })}
                        </span>
                      </span>
                    </div>
                    <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-3">
                      {group.items.map((rule) => {
                        const color = tone(rule);
                        const expanded = selected === rule.id;
                        const current = worstValue(rule);
                        const mutedFor =
                          rule.muted_until && Date.parse(rule.muted_until) > now
                            ? duration((Date.parse(rule.muted_until) - now) / 1000)
                            : null;
                        const sub =
                          rule.status === "muted" && mutedFor
                            ? t("register.mutedFor", { duration: mutedFor })
                            : rule.status === "disabled"
                              ? t("register.disabled")
                              : rule.status === "no_data"
                                ? t("register.noData")
                                : `/ ${show(rule, rule.threshold)}`;
                        const lastFired = rule.episodes.length
                          ? Math.max(...rule.episodes.map((e) => Date.parse(e.fired_at)))
                          : null;
                        const env = rule.target.environment;
                        return (
                          <div key={rule.id} className={expanded ? "md:col-span-2 2xl:col-span-3" : ""}>
                            <button
                              type="button"
                              ref={(element) => {
                                rowRefs.current[rule.id] = element;
                              }}
                              aria-expanded={expanded}
                              onClick={() => open(expanded ? null : rule.id, false)}
                              className={`group relative flex w-full flex-col overflow-hidden rounded-2xl border bg-surface p-4 pl-5 text-left transition-[border-color,box-shadow] outline-none hover:border-border-strong focus-visible:ring-2 focus-visible:ring-focus ${
                                expanded ? "border-border-strong" : "border-border"
                              }`}
                            >
                              <span aria-hidden className="absolute inset-y-0 left-0 w-[3px]" style={{ background: color }} />
                              <span className="flex items-center gap-2">
                                <span
                                  className="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-micro font-semibold"
                                  style={{ color, background: `color-mix(in srgb, ${color} 14%, transparent)` }}
                                >
                                  <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: color }} />
                                  {t.dyn("status", rule.status)}
                                </span>
                                <span
                                  className={`rounded-full px-2 py-0.5 text-micro font-medium ${
                                    rule.severity === "critical" ? "bg-critical-soft text-critical" : "bg-warning-soft text-warning"
                                  }`}
                                >
                                  {t.dyn("severity", rule.severity)}
                                </span>
                                <ChevronDown
                                  aria-hidden
                                  className={`ml-auto h-4 w-4 text-ink-muted transition-transform ${expanded ? "rotate-180" : ""}`}
                                />
                              </span>
                              <b className="mt-2.5 block truncate text-body text-ink">{rule.name}</b>
                              <span className="mt-1 flex flex-wrap items-center gap-1.5 text-micro text-ink-secondary">
                                <span className="truncate">
                                  {filteredName(t.dyn("signal", rule.signal), rule.log_contains, rule.route)}
                                </span>
                                <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface-2 px-2 py-0.5 font-mono text-[10.5px]">
                                  {env ? (
                                    <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: kindColour(env) }} />
                                  ) : null}
                                  {env ?? all.env}
                                  {" / "}
                                  {rule.target.service ?? all.svc}
                                </span>
                              </span>
                              <span className="mt-3 flex items-end justify-between gap-3">
                                <span className="min-w-0">
                                  <b
                                    className="block font-[family-name:var(--font-display)] text-[1.5rem] leading-none font-bold tracking-[-0.02em] tabular-nums"
                                    style={{ color: rule.status === "firing" ? severityTone(rule.severity) : "var(--ink)" }}
                                  >
                                    {show(rule, current)}
                                  </b>
                                  <span className="mt-1 block font-mono text-[11px] text-ink-muted">{sub}</span>
                                </span>
                                <span className="text-right text-micro text-ink-muted">
                                  {lastFired
                                    ? t("board.lastFired", {
                                        when: t("board.ago", { duration: duration((now - lastFired) / 1000) }),
                                      })
                                    : t("board.neverFired")}
                                  <span className="block">{t("board.firings", { count: rule.episodes.length })}</span>
                                </span>
                              </span>
                              <span className="mt-3 block [&_svg]:block [&_svg]:h-7 [&_svg]:w-full">
                                <Lane
                                  rule={rule}
                                  now={now}
                                  onSegment={(span: Span | null, event) => {
                                    if (!span || !event) return setTip(null);
                                    const peak = span.episode.peak;
                                    showTip(
                                      event,
                                      `${time(span.start)}–${span.episode.resolved_at ? time(span.end) : t("register.timelineNow")} · ${duration((span.end - span.start) / 1000)} · ${span.episode.service}${peak !== null ? ` · ${show(rule, peak)}` : ""}`,
                                    );
                                  }}
                                />
                                <span className="mt-1 flex justify-between font-mono text-[10px] text-ink-muted">
                                  <span>{time(now - DAY_MS)}</span>
                                  <span>{t("register.timelineNow")}</span>
                                </span>
                              </span>
                              <span className="mt-3 flex items-center gap-1.5 border-t border-border pt-2.5 text-micro text-ink-secondary">
                                {rule.severity === "critical" ? (
                                  <Siren aria-hidden className="h-3.5 w-3.5 text-critical" />
                                ) : (
                                  <TriangleAlert aria-hidden className="h-3.5 w-3.5 text-warning" />
                                )}
                                {t.dyn("board.action", rule.severity)}
                                <span className="ml-auto font-mono text-[10.5px] text-ink-muted">
                                  {rule.for_seconds
                                    ? t("board.held", { duration: duration(rule.for_seconds) })
                                    : t("board.atOnce")}
                                </span>
                              </span>
                            </button>
                            {expanded ? (
                              <div className="mt-2 overflow-hidden rounded-2xl border border-border bg-surface">
                                <Inspector
                                  rule={rule}
                                  history={histories[rule.id] ?? null}
                                  now={now}
                                  csrfToken={csrfToken}
                                  confirmDelete={confirmDelete === rule.id}
                                  actionError={actionError}
                                  onClose={() => open(null, false)}
                                  onEdit={() => setDrawer({ rule, template: null })}
                                  onMute={(minutes) => void act(() => muteRule(csrfToken as string, rule.id, minutes))}
                                  onToggle={() =>
                                    void act(() =>
                                      updateRule(csrfToken as string, rule.id, {
                                        enabled: !rule.enabled,
                                        expected_version: rule.version,
                                      }),
                                    )
                                  }
                                  onAskDelete={() => setConfirmDelete(rule.id)}
                                  onCancelDelete={() => setConfirmDelete(null)}
                                  onDelete={() =>
                                    void act(async () => {
                                      await deleteRule(csrfToken as string, rule.id);
                                      setSelected(null);
                                    })
                                  }
                                />
                              </div>
                            ) : null}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          </>
        )}

        {tip ? (
          <div className={s.tip} style={{ left: tip.x, top: tip.y }} role="presentation">
            {tip.text}
          </div>
        ) : null}
      </div>

      <RuleDrawer
        open={drawer !== null}
        rule={drawer?.rule ?? null}
        initialTemplate={drawer?.template ?? null}
        options={options}
        csrfToken={csrfToken}
        defaultProjectId={null}
        onClose={() => setDrawer(null)}
        onSaved={() => void load()}
      />
    </PageFrame>
  );
}


function Inspector({
  rule,
  history,
  now,
  csrfToken,
  confirmDelete,
  actionError,
  onClose,
  onEdit,
  onMute,
  onToggle,
  onAskDelete,
  onCancelDelete,
  onDelete,
}: {
  rule: AlertRule;
  history: RuleHistory | null;
  now: number;
  csrfToken: string | null;
  confirmDelete: boolean;
  actionError: string | null;
  onClose: () => void;
  onEdit: () => void;
  onMute: (minutes: number) => void;
  onToggle: () => void;
  onAskDelete: () => void;
  /** Backs out of the delete confirmation only — the inspector stays open. */
  onCancelDelete: () => void;
  onDelete: () => void;
}) {
  const t = useT("alertRules");
  const fmt = useFormat();
  const duration = useDuration();
  const show = (value: number | null | undefined) => formatSignal(rule.signal, value, fmt.number);
  const time = (value: number) => hhmm(new Date(value), fmt.tag);

  const defaultBinding =
    rule.states.find((state) => state.state === "firing")?.binding_id ??
    rule.episodes[rule.episodes.length - 1]?.binding_id ??
    history?.bindings[0]?.binding_id ??
    null;
  const [bindingId, setBindingId] = useState<string | null>(defaultBinding);
  const binding = history?.bindings.find((b) => b.binding_id === bindingId) ?? history?.bindings[0];
  const episodes = rule.episodes.filter((e) => !binding || e.binding_id === binding.binding_id);

  const peaks = rule.episodes.map((e) => e.peak).filter((v): v is number => v !== null);
  const values = (binding?.series ?? []).map((p) => p.value).filter((v): v is number => v !== null);
  const peakSource = [...peaks, ...values];
  const peak = peakSource.length
    ? rule.operator === "<"
      ? Math.min(...peakSource)
      : Math.max(...peakSource)
    : null;
  const totalMinutes = Math.round(
    rule.episodes.reduce((sum, e) => {
      const end = e.resolved_at ? Date.parse(e.resolved_at) : now;
      return sum + Math.max(0, end - Math.max(now - DAY_MS, Date.parse(e.fired_at)));
    }, 0) / 60000,
  );

  const events: { at: number; color: string; body: React.ReactNode; key: string }[] = [];
  for (const episode of rule.episodes) {
    const fired = Date.parse(episode.fired_at);
    events.push({
      at: fired,
      color: severityTone(rule.severity),
      key: `f${episode.binding_id}${episode.fired_at}`,
      body: (
        <>
          {t("inspector.fired", { value: show(episode.peak) })} · {episode.service}
          {episode.incident_id ? (
            <>
              {" · "}
              <Link className={s.link} href={`/incidents/${episode.incident_id}`}>
                {t("inspector.openIncident")}
              </Link>
            </>
          ) : null}
        </>
      ),
    });
    if (episode.resolved_at) {
      const resolved = Date.parse(episode.resolved_at);
      events.push({
        at: resolved,
        color: "var(--ok)",
        key: `r${episode.binding_id}${episode.resolved_at}`,
        body: t("inspector.resolved", { duration: duration((resolved - fired) / 1000) }),
      });
    }
  }
  events.sort((a, b) => b.at - a.at);

  const sentenceKey = sentenceFor(rule.operator, rule.for_seconds);
  const ruleSignal = filteredName(t.dyn("signal", rule.signal), rule.log_contains, rule.route);
  const anomaly = rule.operator === "above_normal" || rule.operator === "below_normal";
  const muted = rule.muted_until !== null && Date.parse(rule.muted_until) > now;

  return (
    <div className={s.insp}>
      <div className={s.inspChart}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, marginBottom: 8, flexWrap: "wrap" }}>
          <span className={s.cap}>
            {t("inspector.chartCaption", { signal: t.dyn("signal", rule.signal) })}
          </span>
          {history && history.bindings.length > 1 ? (
            <select
              aria-label={t("inspector.binding")}
              className={s.select}
              value={binding?.binding_id ?? ""}
              onChange={(event) => setBindingId(event.target.value)}
            >
              {history.bindings.map((b) => (
                <option key={b.binding_id} value={b.binding_id}>
                  {b.environment} / {b.service}
                </option>
              ))}
            </select>
          ) : binding ? (
            <span className={`${s.cap} ${s.d}`}>
              {binding.environment} / {binding.service}
            </span>
          ) : null}
        </div>
        {binding && binding.series.length > 1 ? (
          <SignalChart
            points={binding.series}
            episodes={episodes}
            threshold={rule.threshold}
            operator={rule.operator}
            severity={rule.severity}
            now={now}
            format={show}
            timeLabel={time}
            thresholdLabel={anomaly ? t("now.normalEdge") : t("now.threshold", { value: show(rule.threshold) })}
            nowLabel={t("register.timelineNow")}
            label={t("inspector.chartCaption", { signal: t.dyn("signal", rule.signal) })}
          />
        ) : (
          <p className={s.note}>{history ? t("inspector.noSamples") : "…"}</p>
        )}
      </div>
      <div className={s.inspSide}>
        <p className={s.ruleLine}>
          {t(sentenceKey, {
            signal: ruleSignal,
            threshold: show(rule.threshold),
            duration: duration(rule.for_seconds),
          })}
          , <b>{t(rule.severity === "critical" ? "sentence.critical" : "sentence.warning")}</b>.{" "}
          {t("sentence.routing")}
        </p>
        <div className={s.kv}>
          <div>
            <span className={s.cap}>{t("inspector.peak")}</span>
            <b>{show(peak)}</b>
          </div>
          <div>
            <span className={s.cap}>{t("inspector.firings")}</span>
            <b>{rule.episodes.length}</b>
          </div>
          <div>
            <span className={s.cap}>{t("inspector.total")}</span>
            <b>{t("inspector.totalMinutes", { count: totalMinutes })}</b>
          </div>
        </div>
        <span className={s.cap}>{t("inspector.events")}</span>
        <ul className={s.log}>
          {events.length ? (
            events.slice(0, 6).map((event) => (
              <li key={event.key}>
                <time dateTime={new Date(event.at).toISOString()}>{time(event.at)}</time>
                <i className={s.dot} style={{ ["--c" as string]: event.color }} />
                <span>{event.body}</span>
              </li>
            ))
          ) : (
            <li>
              <time>—</time>
              <i className={s.dot} style={{ ["--c" as string]: "var(--ok)" }} />
              <span>{t("inspector.quietDay")}</span>
            </li>
          )}
        </ul>
        {actionError ? <p className={s.err}>{actionError}</p> : null}
        {rule.can_manage && csrfToken ? (
          confirmDelete ? (
            <div className={s.acts}>
              <span className={s.note} style={{ margin: 0, alignSelf: "center" }}>
                {t("inspector.confirmDelete")}
              </span>
              <button type="button" className={`${s.btn} ${s.danger}`} onClick={onDelete}>
                {t("inspector.confirmYes")}
              </button>
              <button type="button" className={s.btn} onClick={onCancelDelete}>
                {t("drawer.cancel")}
              </button>
            </div>
          ) : (
            <div className={s.acts}>
              <button type="button" className={s.btn} onClick={() => onMute(muted ? 0 : 60)}>
                {muted ? t("inspector.unmute") : t("inspector.mute")}
              </button>
              <button type="button" className={s.btn} onClick={onEdit}>
                {t("inspector.edit")}
              </button>
              <button type="button" className={s.btn} onClick={onToggle}>
                {rule.enabled ? t("inspector.disable") : t("inspector.enable")}
              </button>
              <button type="button" className={`${s.btn} ${s.danger}`} onClick={onAskDelete}>
                {t("inspector.delete")}
              </button>
              <button type="button" className={s.btn} onClick={onClose}>
                {t("inspector.close")}
              </button>
            </div>
          )
        ) : (
          <>
            <p className={s.note}>{t("inspector.readOnly")}</p>
            <div className={s.acts}>
              <button type="button" className={s.btn} onClick={onClose}>
                {t("inspector.close")}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
