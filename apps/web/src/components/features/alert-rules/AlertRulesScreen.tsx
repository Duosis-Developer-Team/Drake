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

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { PageFrame } from "@/components/shell/AppShell";
import { DataState } from "@/components/state/DataState";
import {
  deleteRule,
  fetchRuleHistory,
  fetchRuleOptions,
  fetchRules,
  formatSignal,
  muteRule,
  updateRule,
  type AlertRule,
  type RuleHistory,
  type RuleList,
  type RuleOptions,
} from "@/lib/alertRules";
import { useFormat, useT } from "@/lib/i18n";
import { useSession } from "@/lib/session";

import s from "./alertRules.module.css";
import {
  AlarmClock,
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
  useDuration,
  type TemplateKey,
} from "./RuleDrawer";

const REFRESH_MS = 60_000;
const MAX_RINGS = 9;
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

function scopeText(rule: AlertRule, all: { env: string; svc: string }): string {
  return [rule.target.project, rule.target.environment ?? all.env, rule.target.service ?? all.svc].join(
    " / ",
  );
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
  const [hovered, setHovered] = useState<string | null>(null);
  const [tip, setTip] = useState<{ x: number; y: number; text: string } | null>(null);
  const [drawer, setDrawer] = useState<{ rule: AlertRule | null; template: TemplateKey | null } | null>(
    null,
  );
  const [mutingAll, setMutingAll] = useState<"idle" | "busy" | "done">("idle");
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const rowRefs = useRef<Record<string, HTMLButtonElement | null>>({});

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

  const ringRules = [...rules]
    .sort((a, b) => Number(b.status === "firing") - Number(a.status === "firing") || b.episodes.length - a.episodes.length)
    .slice(0, MAX_RINGS);
  const counts = {
    all: rules.length,
    firing: rules.filter((r) => matches(r, "firing")).length,
    ok: rules.filter((r) => matches(r, "ok")).length,
    muted: rules.filter((r) => matches(r, "muted")).length,
  };
  const manageable = rules.some(
    (rule) => rule.can_manage && rule.status !== "muted" && rule.status !== "disabled",
  );

  return (
    <PageFrame>
      <div className={s.root}>
        <section className={s.title}>
          <div>
            <h1 className={s.h1}>
              {t("title")} <em>{t("titleWindow")}</em>
            </h1>
            <p className={s.lede}>{t("lede")}</p>
          </div>
          <div className={s.cta}>
            {manageable && csrfToken ? (
              <button
                type="button"
                className={s.btn}
                disabled={mutingAll !== "idle"}
                onClick={() => void muteAll()}
              >
                {mutingAll === "done" ? t("mutedAll") : t("muteAll")}
              </button>
            ) : null}
            {canCreate ? (
              <button
                type="button"
                className={`${s.btn} ${s.go}`}
                onClick={() => setDrawer({ rule: null, template: null })}
              >
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
            <section className={s.empty}>
              <h2>{t("empty.title")}</h2>
              <p>{t("empty.body")}</p>
              {canCreate ? (
                <div className={s.tpls}>
                  {TEMPLATE_KEYS.map((key) => (
                    <button
                      key={key}
                      type="button"
                      className={s.tpl}
                      onClick={() => setDrawer({ rule: null, template: key })}
                    >
                      {t(`templates.${key}.name`)}
                      <small>{t(`templates.${key}.detail`)}</small>
                    </button>
                  ))}
                </div>
              ) : null}
            </section>
          ) : (
            <>
              <section className={s.console} aria-label={t("now.caption")}>
                <div className={s.clock}>
                  <div className={s.clockHead}>
                    <span className={s.cap}>{t("clock.caption")}</span>
                    <span className={`${s.cap} ${s.d}`}>{t("clock.now", { time: time(now) })}</span>
                  </div>
                  <div
                    onMouseLeave={() => {
                      setHovered(null);
                      setTip(null);
                    }}
                  >
                    <ClockWithDimming
                      rules={ringRules}
                      now={now}
                      firing={firing.length}
                      hovered={hovered}
                      labels={{
                        center: t("clock.center", { total: rules.length }),
                        firing: t("clock.firing"),
                      }}
                      onPick={(id) => open(id, true)}
                      onHover={(rule, event) => {
                        setHovered(rule?.id ?? null);
                        if (rule && event) showTip(event, `${rule.name} · ${scopeText(rule, all)}`);
                        else setTip(null);
                      }}
                    />
                  </div>
                  <div className={s.clockFoot}>
                    <span style={{ ["--c" as string]: "var(--crit)" }}>
                      <i className={s.swatch} />
                      {t("clock.legendCritical")}
                    </span>
                    <span style={{ ["--c" as string]: "var(--warn)" }}>
                      <i className={s.swatch} />
                      {t("clock.legendWarning")}
                    </span>
                    <span style={{ ["--c" as string]: "var(--track)" }}>
                      <i className={s.swatch} />
                      {t("clock.legendQuiet")}
                    </span>
                    <span>
                      {rules.length > MAX_RINGS
                        ? t("clock.more", { count: rules.length - MAX_RINGS })
                        : t("clock.hint")}
                    </span>
                  </div>
                </div>

                <div className={s.now}>
                  <div className={s.nowHead}>
                    <span className={s.cap}>{t("now.caption")}</span>
                    <span className={`${s.cap} ${s.d}`}>{t("now.count", { count: firing.length })}</span>
                  </div>
                  {firing.length === 0 ? (
                    <div className={s.quietNow}>
                      <b>{t("now.none")}</b>
                      <span>{t("now.noneHint")}</span>
                    </div>
                  ) : (
                    firing.map((rule) => {
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
                          className={s.readout}
                          style={{ ["--c" as string]: color }}
                          onClick={() => open(rule.id, true)}
                        >
                          <span style={{ display: "block" }}>
                            <span className={s.since} style={{ display: "block" }}>
                              {t("now.since", {
                                severity: t.dyn("severity", rule.severity),
                                duration: since ? duration((now - since) / 1000) : "—",
                              })}
                            </span>
                            <span className={s.rn} style={{ display: "block" }}>
                              {rule.name}
                            </span>
                            <span className={s.rs} style={{ display: "block" }}>
                              {scopeText(rule, all)}
                            </span>
                          </span>
                          <span className={s.rv}>
                            <b>{show(rule, worstValue(rule))}</b>
                            <span>{t("now.threshold", { value: show(rule, rule.threshold) })}</span>
                          </span>
                          <Spark
                            points={series?.series ?? []}
                            threshold={rule.threshold}
                            color={color}
                            tall={firing.length <= 2}
                          />
                        </button>
                      );
                    })
                  )}
                  <div className={s.calm}>
                    <span>
                      <b>{t("now.calm", { count: counts.ok })}</b>
                      {counts.muted ? ` · ${t("now.mutedCount", { count: counts.muted })}` : ""}
                    </span>
                    <span className={s.d}>{t("now.fired24h", { count: list?.summary.fired_24h ?? 0 })}</span>
                  </div>
                </div>
              </section>

              <section className={s.reg} aria-label={t("register.title")}>
                <div className={s.regHead}>
                  <h2>{t("register.title")}</h2>
                  <div className={s.tabs} role="group">
                    {(["all", "firing", "ok", "muted"] as Filter[]).map((key) => (
                      <button
                        key={key}
                        type="button"
                        className={s.tab}
                        aria-pressed={filter === key}
                        onClick={() => setFilter(key)}
                      >
                        {t(`tabs.${key}`)}
                        <span className={s.tabN}>{counts[key]}</span>
                      </button>
                    ))}
                  </div>
                </div>
                <div className={s.cols} aria-hidden="true">
                  <span />
                  <span className={s.cap}>{t("register.rule")}</span>
                  <span className={`${s.cap} ${s.tl}`}>
                    <span>{time(now - DAY_MS)}</span>
                    <span>{time(now - 18 * 3600_000)}</span>
                    <span>{time(now - 12 * 3600_000)}</span>
                    <span>{time(now - 6 * 3600_000)}</span>
                    <span>{t("register.timelineNow")}</span>
                  </span>
                  <span className={s.cap} style={{ textAlign: "right" }}>
                    {t("register.nowThreshold")}
                  </span>
                  <span />
                </div>
                {rules.filter((rule) => matches(rule, filter)).length === 0 ? (
                  <div className={s.emptyRow}>{t("register.empty")}</div>
                ) : null}
                {rules
                  .filter((rule) => matches(rule, filter))
                  .map((rule) => {
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
                    return (
                      <div key={rule.id}>
                        <button
                          type="button"
                          ref={(element) => {
                            rowRefs.current[rule.id] = element;
                          }}
                          className={s.row}
                          aria-expanded={expanded}
                          style={{ ["--c" as string]: color }}
                          onClick={() => open(expanded ? null : rule.id, false)}
                        >
                          <span
                            className={`${s.st} ${rule.status === "firing" ? s.firingSt : ""}`}
                            title={t.dyn("status", rule.status)}
                          />
                          <span className={s.nm}>
                            <b>{rule.name}</b>
                            <span>
                              {filteredName(t.dyn("signal", rule.signal), rule.log_contains, rule.route)} · {scopeText(rule, all)}
                            </span>
                          </span>
                          <span className={s.lane}>
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
                          </span>
                          <span
                            className={s.v}
                            style={{
                              ["--c2" as string]:
                                rule.status === "firing" ? severityTone(rule.severity) : "var(--ink)",
                            }}
                          >
                            <b>{show(rule, current)}</b>
                            <span>{sub}</span>
                          </span>
                          <svg className={s.chev} width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
                            <path d="M5 3l4 4-4 4" fill="none" stroke="currentColor" strokeWidth="1.6" />
                          </svg>
                        </button>
                        {expanded ? (
                          <Inspector
                            rule={rule}
                            history={histories[rule.id] ?? null}
                            now={now}
                            csrfToken={csrfToken}
                            confirmDelete={confirmDelete === rule.id}
                            actionError={actionError}
                            onClose={() => open(null, false)}
                            onEdit={() => setDrawer({ rule, template: null })}
                            onMute={(minutes) =>
                              void act(() => muteRule(csrfToken as string, rule.id, minutes))
                            }
                            onToggle={() =>
                              void act(() =>
                                updateRule(csrfToken as string, rule.id, {
                                  enabled: !rule.enabled,
                                  expected_version: rule.version,
                                }),
                              )
                            }
                            onAskDelete={() => setConfirmDelete(rule.id)}
                            onDelete={() =>
                              void act(async () => {
                                await deleteRule(csrfToken as string, rule.id);
                                setSelected(null);
                              })
                            }
                          />
                        ) : null}
                      </div>
                    );
                  })}
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

function ClockWithDimming(props: React.ComponentProps<typeof AlarmClock> & { hovered: string | null }) {
  const { hovered, ...rest } = props;
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const rings = ref.current?.querySelectorAll<SVGGElement>("[data-ring]") ?? [];
    rings.forEach((ring) => {
      ring.classList.toggle(s.ringDim, hovered !== null && ring.dataset.ring !== hovered);
    });
  }, [hovered]);
  return (
    <div ref={ref}>
      <AlarmClock {...rest} />
    </div>
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
              <button type="button" className={s.btn} onClick={onClose}>
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
