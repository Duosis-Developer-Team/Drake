"use client";

/**
 * What the Traces and Logs screens show before anything is picked: the
 * platform's requests or log lines over the window, broken down the way
 * people ask about them — which application, which service, which route,
 * which message — in the environment dashboards' own visual language.
 *
 * Everything comes from reads the product already makes: one overview read
 * over every bound service (the same one a dashboard makes for its
 * environment) and the last hour's per-service signals. A section that did
 * not answer says so; nothing becomes zero, and a ranked list never ranks
 * a service it has no number for.
 */

import { ChevronRight } from "lucide-react";
import { useMemo } from "react";

import s from "@/components/features/dashboards/dashboards.module.css";
import { TipProvider, useTip, type TipLine } from "@/components/features/dashboards/tip";
import { HourTicks, colourOf, useN } from "@/components/features/dashboards/widgets";
import { kindColour } from "@/components/apps/AppLauncher";
import { ProjectLogo } from "@/components/ui/ProjectLogo";
import { SLOW_MS, answered, apdex, type Overview, type OverviewRange } from "@/lib/dashboards";
import { useT } from "@/lib/i18n";
import type { PlatformApp } from "@/lib/platform";
import type { ServiceHealthRow } from "@/lib/serviceHealth";
import type { SignalPage } from "@/lib/signals";
import { useMediaQuery } from "@/lib/useMediaQuery";

type T = ReturnType<typeof useT<"explore">>;

/** Bound rows by binding id, for naming what a read answered about. */
function useByBinding(rows: ServiceHealthRow[]) {
  return useMemo(
    () => new Map(rows.flatMap((row) => (row.binding ? [[row.binding.id, row] as const] : []))),
    [rows],
  );
}

function bucketWord(overview: Overview, t: T): string {
  const seconds = overview.bucket_seconds ?? 3600;
  return seconds === 3600 ? t("bucket.hour") : seconds === 900 ? t("bucket.quarter") : t("bucket.other");
}

/* ---------------- shared pieces ---------------- */

function Card({
  title,
  sub,
  tools,
  children,
  className = "",
  testId,
}: {
  title: string;
  sub?: string;
  tools?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  testId?: string;
}) {
  return (
    <section className={`${s.card} ${className}`} aria-label={title} data-testid={testId}>
      <header>
        <div>
          <h2>{title}</h2>
          {sub ? <p>{sub}</p> : null}
        </div>
        {tools}
      </header>
      {children}
    </section>
  );
}

function RangePills({ range, onRange }: { range: OverviewRange; onRange: (range: OverviewRange) => void }) {
  const td = useT("dashboards");
  return (
    <div className={s.pills} role="group" aria-label={td("rangeLabel")}>
      {(["6h", "24h"] as const).map((r) => (
        <button key={r} type="button" aria-pressed={range === r} onClick={() => onRange(r)}>
          {td(`range.${r}`)}
        </button>
      ))}
    </div>
  );
}

function Kpi({ label, value, note, tone }: { label: string; value: string; note?: string; tone?: "ok" | "warn" | "bad" }) {
  return (
    <div className={`${s.kpi} ${tone ? s[tone] : ""}`}>
      <span>{label}</span>
      <b>{value}</b>
      {note ? <em>{note}</em> : null}
    </div>
  );
}

interface Segment {
  key: string;
  value: number;
  colour: string;
  label: string;
}

/**
 * Stacked columns, one per bucket. Heights share one scale (the tallest
 * bucket), so a quiet hour looks quiet; a bucket with nothing is a stub,
 * never a missing column.
 */
function StackedColumns({
  buckets,
  segments,
  height = 128,
  tipTitle,
  totalLabel,
}: {
  buckets: number[];
  /** One list per bucket, bottom segment first. */
  segments: Segment[][];
  height?: number;
  tipTitle: (i: number) => string;
  totalLabel: string;
}) {
  const tip = useTip();
  const { n } = useN();
  const totals = segments.map((list) => list.reduce((a, x) => a + x.value, 0));
  const peak = Math.max(...totals, 1e-9);
  return (
    <div className="flex items-end gap-[3px]" style={{ height }} role="img" aria-label={totalLabel}>
      {buckets.map((_, i) => {
        const lines: TipLine[] = [
          [tipTitle(i)],
          ...segments[i].filter((x) => x.value > 0).map((x): TipLine => [x.label, n(x.value)]),
          [totalLabel, n(totals[i])],
        ];
        return (
          <div
            key={i}
            className="flex h-full min-w-0 flex-1 flex-col-reverse"
            onMouseMove={(e) => tip.show(e, lines)}
            onMouseLeave={tip.hide}
          >
            {totals[i] === 0 ? (
              <i className="block h-[3px] rounded-[2px] bg-surface-3" />
            ) : (
              segments[i].map((x, k) =>
                x.value > 0 ? (
                  <i
                    key={x.key}
                    className={`block origin-bottom animate-[rise-in_.5s_cubic-bezier(.2,.8,.2,1)_both] ${k === segments[i].length - 1 || segments[i].slice(k + 1).every((y) => y.value === 0) ? "rounded-t-[3px]" : ""}`}
                    style={{ height: `${(x.value / peak) * 100}%`, background: x.colour, minHeight: 2, animationDelay: `${i * 10}ms` }}
                  />
                ) : null,
              )
            )}
          </div>
        );
      })}
    </div>
  );
}

/** A ranked row: what, where, a bar for its share of the leader, and a value. */
function RankRow({
  row,
  label,
  sub,
  share,
  value,
  colour = "var(--brand-accent)",
  valueTone = "text-ink",
  onOpen,
}: {
  row: ServiceHealthRow | undefined;
  label: string;
  sub: string;
  share: number;
  value: string;
  colour?: string;
  valueTone?: string;
  onOpen?: () => void;
}) {
  const content = (
    <>
      {row ? (
        <ProjectLogo projectId={row.project_id} name={row.project_key} size="md" variant="mark" />
      ) : (
        <span className="h-7 w-7" />
      )}
      <span className="min-w-0">
        <span className="flex items-baseline justify-between gap-3">
          <span className="truncate text-[0.875rem] font-semibold text-ink">{label}</span>
          <span data-tabular className={`shrink-0 font-mono text-[0.8125rem] font-semibold ${valueTone}`}>
            {value}
          </span>
        </span>
        <span className="mt-1 flex items-center gap-2.5">
          <span className="block h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-surface-3">
            <span className="block h-full rounded-full" style={{ width: `${Math.max(2, Math.min(1, share) * 100)}%`, background: colour }} />
          </span>
          <span className="shrink-0 truncate font-mono text-[10.5px] text-ink-muted">{sub}</span>
        </span>
      </span>
      {onOpen ? (
        <ChevronRight aria-hidden className="h-4 w-4 text-ink-muted transition-transform group-hover:translate-x-0.5" />
      ) : (
        <span />
      )}
    </>
  );
  const className =
    "group grid w-full grid-cols-[1.75rem_minmax(0,1fr)_1rem] items-center gap-3 px-[22px] py-2.5 text-left transition-colors";
  return (
    <li>
      {onOpen ? (
        <button type="button" onClick={onOpen} className={`${className} hover:bg-surface-hover`}>
          {content}
        </button>
      ) : (
        <div className={className}>{content}</div>
      )}
    </li>
  );
}

function Empty({ text }: { text: string }) {
  return <p className={s.state}>{text}</p>;
}

function Legend({ items }: { items: { key: string; colour: string; label: string; sub?: string; projectId?: string; onPick?: () => void }[] }) {
  return (
    <ul className="flex flex-wrap gap-2 px-[26px] pb-5">
      {items.map((item) => {
        const body = (
          <>
            {item.projectId ? (
              <ProjectLogo projectId={item.projectId} name={item.label} size="sm" variant="mark" />
            ) : null}
            <span aria-hidden className="h-2.5 w-2.5 shrink-0 rounded-[3px]" style={{ background: item.colour }} />
            <span className="font-medium text-ink">{item.label}</span>
            {item.sub ? <span data-tabular className="font-mono text-ink-muted">{item.sub}</span> : null}
          </>
        );
        const cls = "inline-flex items-center gap-2 rounded-full border border-border bg-surface-2 px-3 py-1.5 text-[12px]";
        return (
          <li key={item.key}>
            {item.onPick ? (
              <button type="button" onClick={item.onPick} className={`${cls} transition-colors hover:border-border-strong hover:bg-surface-hover`}>
                {body}
              </button>
            ) : (
              <span className={cls}>{body}</span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

const rowName = (row: ServiceHealthRow | undefined, fallback: string) => row?.display_name || row?.service_key || fallback;
const rowWhere = (row: ServiceHealthRow | undefined) => (row ? `${row.project_key}/${row.environment_key}` : "");

/* ---------------- traces ---------------- */

export function TracesOverview({
  rows,
  apps,
  overview,
  failed,
  truncated,
  range,
  onRange,
  onOpen,
  onPickApp,
}: {
  rows: ServiceHealthRow[];
  apps: PlatformApp[];
  overview: Overview | null;
  failed: boolean;
  truncated: number | null;
  range: OverviewRange;
  onRange: (range: OverviewRange) => void;
  onOpen: (bindingId: string) => void;
  onPickApp: (projectId: string) => void;
}) {
  const t = useT("explore");
  const td = useT("dashboards");
  const { n, ms, pct, hour } = useN();
  const byBinding = useByBinding(rows);
  const rangeWord = td(`range.${range}`);
  const narrow = useMediaQuery("(max-width: 640px)");
  const tickEvery = range === "6h" ? (narrow ? 2 : 1) : narrow ? 6 : 3;

  const model = useMemo(() => {
    const traffic = answered(overview?.traffic);
    if (!overview || !traffic) return null;
    const perBinding = traffic.map((x) => {
      const p95s = x.buckets.map((b) => b.p95_ms).filter((v): v is number => v !== null);
      return {
        id: x.binding_id,
        requests: x.buckets.reduce((a, b) => a + b.requests, 0),
        errors: x.buckets.reduce((a, b) => a + b.errors, 0),
        worst: p95s.length ? Math.max(...p95s) : null,
        buckets: x.buckets,
      };
    });
    const series = apps
      .map((app, i) => {
        const ids = new Set(app.envs.flatMap((e) => e.bindingIds));
        const own = perBinding.filter((b) => ids.has(b.id));
        const perBucket = overview.buckets.map((_, k) => own.reduce((a, b) => a + (b.buckets[k]?.requests ?? 0), 0));
        return { app, colour: colourOf(i), perBucket, total: perBucket.reduce((a, b) => a + b, 0) };
      })
      .filter((x) => x.total > 0)
      .sort((a, b) => b.total - a.total);
    const total = perBinding.reduce((a, b) => a + b.requests, 0);
    const errors = perBinding.reduce((a, b) => a + b.errors, 0);
    const ap = apdex(answered(overview.latency));
    const routes = (answered(overview.routes) ?? []).filter((r) => r.p95_ms !== null && r.requests >= 10);
    return {
      series,
      total,
      errors,
      slow: answered(overview.latency) ? ap.tolerable + ap.slow : null,
      busiest: [...perBinding].filter((b) => b.requests > 0).sort((a, b) => b.requests - a.requests).slice(0, 6),
      slowest: [...perBinding]
        .filter((b) => b.worst !== null && b.requests >= 10)
        .sort((a, b) => (b.worst ?? 0) - (a.worst ?? 0))
        .slice(0, 6),
      endpoints: [...routes].sort((a, b) => (b.p95_ms ?? 0) - (a.p95_ms ?? 0)).slice(0, 6),
    };
  }, [overview, apps]);

  const tools = <RangePills range={range} onRange={onRange} />;
  const subNote = truncated ? ` · ${t("overview.truncated", { count: truncated })}` : "";

  if (!overview || !model) {
    return (
      <div className={s.root} data-testid="traces-overview">
        <Card title={t("traffic.title")} tools={tools}>
          <Empty text={failed ? t("overview.failed") : t("overview.pending")} />
        </Card>
      </div>
    );
  }
  const bucket = bucketWord(overview, t);
  const lead = model.series[0];
  const p95Tone = (v: number | null) => (v === null ? "text-ink-muted" : v >= 3 * SLOW_MS ? "text-critical" : v >= SLOW_MS ? "text-warning" : "text-ink");
  const p95Colour = (v: number | null) =>
    v === null ? "var(--surface-3)" : v >= 3 * SLOW_MS ? "var(--status-critical)" : v >= SLOW_MS ? "var(--status-warning)" : "var(--brand-accent)";

  return (
    <div className={`${s.root} flex flex-col gap-5`} data-testid="traces-overview">
      <TipProvider>
        <Card title={t("traffic.title")} sub={t("traffic.sub", { bucket, range: rangeWord }) + subNote} tools={tools}>
          <div className={s.kpis}>
            <Kpi label={t("traffic.requests")} value={n(model.total)} note={t("traffic.requestsNote")} />
            <Kpi
              label={t("traffic.failed")}
              value={n(model.errors)}
              note={t("traffic.failedNote", { ratio: model.total ? pct(model.errors / model.total, 1) : "—" })}
              tone={model.errors ? "bad" : "ok"}
            />
            <Kpi label={t("traffic.slow")} value={n(model.slow)} note={t("traffic.slowNote")} tone={model.slow ? "warn" : "ok"} />
            <Kpi
              label={t("traffic.busiest")}
              value={lead ? lead.app.name : "—"}
              note={lead && model.total ? t("traffic.busiestNote", { share: pct(lead.total / model.total) }) : undefined}
            />
          </div>
          {model.total === 0 ? (
            <Empty text={t("overview.noTraces")} />
          ) : (
            <>
              <div className="px-[26px] pt-5 pb-2">
                <StackedColumns
                  buckets={overview.buckets}
                  segments={overview.buckets.map((_, i) =>
                    model.series.map((x) => ({ key: x.app.projectId, value: x.perBucket[i], colour: x.colour, label: x.app.name })),
                  )}
                  tipTitle={(i) => hour(overview.buckets[i])}
                  totalLabel={t("traffic.all")}
                />
                <HourTicks buckets={overview.buckets} every={tickEvery} />
              </div>
              <Legend
                items={model.series.map((x) => ({
                  key: x.app.projectId,
                  colour: x.colour,
                  label: x.app.name,
                  sub: `${n(x.total)} · ${pct(x.total / model.total)}`,
                  projectId: x.app.projectId,
                  onPick: () => onPickApp(x.app.projectId),
                }))}
              />
            </>
          )}
        </Card>
      </TipProvider>

      <div className="grid gap-5 xl:grid-cols-3">
        <Card title={t("busiest.title")} sub={t("busiest.sub", { range: rangeWord })} testId="busiest-services">
          {model.busiest.length === 0 ? (
            <Empty text={t("overview.noTraces")} />
          ) : (
            <ul className="pb-3">
              {model.busiest.map((b) => {
                const row = byBinding.get(b.id);
                return (
                  <RankRow
                    key={b.id}
                    row={row}
                    label={rowName(row, b.id)}
                    sub={rowWhere(row)}
                    share={b.requests / (model.busiest[0]?.requests || 1)}
                    value={n(b.requests)}
                    onOpen={() => onOpen(b.id)}
                  />
                );
              })}
            </ul>
          )}
        </Card>
        <Card title={t("slowest.title")} sub={t("slowest.sub", { bucket, range: rangeWord })} testId="slowest-services">
          {model.slowest.length === 0 ? (
            <Empty text={t("overview.noTraces")} />
          ) : (
            <ul className="pb-3">
              {model.slowest.map((b) => {
                const row = byBinding.get(b.id);
                return (
                  <RankRow
                    key={b.id}
                    row={row}
                    label={rowName(row, b.id)}
                    sub={rowWhere(row)}
                    share={(b.worst ?? 0) / (model.slowest[0]?.worst || 1)}
                    value={ms(b.worst)}
                    colour={p95Colour(b.worst)}
                    valueTone={p95Tone(b.worst)}
                    onOpen={() => onOpen(b.id)}
                  />
                );
              })}
            </ul>
          )}
        </Card>
        <Card title={t("endpoints.title")} sub={t("endpoints.sub", { range: rangeWord })} testId="slowest-endpoints">
          {model.endpoints.length === 0 ? (
            <Empty text={t("overview.noTraces")} />
          ) : (
            <ul className="pb-3">
              {model.endpoints.map((r) => {
                const row = byBinding.get(r.binding_id);
                return (
                  <RankRow
                    key={`${r.binding_id}-${r.route}`}
                    row={row}
                    label={r.route}
                    sub={`${rowName(row, "")} · ${t("endpoints.calls", { count: n(r.requests) })}`}
                    share={(r.p95_ms ?? 0) / (model.endpoints[0]?.p95_ms || 1)}
                    value={ms(r.p95_ms)}
                    colour={p95Colour(r.p95_ms)}
                    valueTone={p95Tone(r.p95_ms)}
                    onOpen={() => onOpen(r.binding_id)}
                  />
                );
              })}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}

/* ---------------- logs ---------------- */

const SEVERITY_COLOUR = {
  errors: "var(--status-critical)",
  warnings: "var(--status-warning)",
  other: "color-mix(in srgb, var(--brand-accent) 55%, transparent)",
};

export function LogsOverview({
  rows,
  apps,
  overview,
  failed,
  truncated,
  signals,
  range,
  onRange,
  onOpen,
  onPickApp,
}: {
  rows: ServiceHealthRow[];
  apps: PlatformApp[];
  overview: Overview | null;
  failed: boolean;
  truncated: number | null;
  signals: SignalPage | null;
  range: OverviewRange;
  onRange: (range: OverviewRange) => void;
  onOpen: (bindingId: string) => void;
  onPickApp: (projectId: string) => void;
}) {
  const t = useT("explore");
  const td = useT("dashboards");
  const { n, pct, hour } = useN();
  const byBinding = useByBinding(rows);
  const rangeWord = td(`range.${range}`);
  const narrow = useMediaQuery("(max-width: 640px)");
  const tickEvery = range === "6h" ? (narrow ? 2 : 1) : narrow ? 6 : 3;

  const volume = answered(overview?.log_lines);
  const patterns = answered(overview?.log_patterns);
  const totals = useMemo(() => {
    if (!volume) return null;
    const lines = volume.reduce((a, b) => a + b.lines, 0);
    const errors = volume.reduce((a, b) => a + b.errors, 0);
    const warnings = volume.reduce((a, b) => a + b.warnings, 0);
    return { lines, errors, warnings };
  }, [volume]);

  // The last hour per service, from the signals read.
  const logItems = useMemo(
    () => (signals && signals.logs.state === "ok" ? signals.items.flatMap((item) => (item.logs ? [{ id: item.binding_id, ...item.logs }] : [])) : null),
    [signals],
  );
  const loudest = useMemo(
    () =>
      (logItems ?? [])
        .filter((x) => x.lines > 0)
        .sort((a, b) => b.errors - a.errors || b.warnings - a.warnings || b.lines - a.lines)
        .slice(0, 6),
    [logItems],
  );
  const perApp = useMemo(() => {
    if (!logItems) return null;
    return apps
      .map((app, i) => {
        const ids = new Set(app.envs.flatMap((e) => e.bindingIds));
        const own = logItems.filter((x) => ids.has(x.id));
        const envs = app.envs
          .map((env) => {
            const mine = logItems.filter((x) => env.bindingIds.includes(x.id));
            return {
              env,
              reported: mine.length > 0,
              errors: mine.reduce((a, x) => a + x.errors, 0),
              warnings: mine.reduce((a, x) => a + x.warnings, 0),
            };
          })
          .filter((x) => x.reported);
        return {
          app,
          envs,
          colour: colourOf(i),
          errors: own.reduce((a, x) => a + x.errors, 0),
          warnings: own.reduce((a, x) => a + x.warnings, 0),
          lines: own.reduce((a, x) => a + x.lines, 0),
          reported: own.length > 0,
        };
      })
      .filter((x) => x.reported)
      .sort((a, b) => b.errors - a.errors || b.warnings - a.warnings || b.lines - a.lines);
  }, [logItems, apps]);

  const tools = <RangePills range={range} onRange={onRange} />;
  const subNote = truncated ? ` · ${t("overview.truncated", { count: truncated })}` : "";
  const bucket = overview ? bucketWord(overview, t) : t("bucket.hour");

  return (
    <div className={`${s.root} flex flex-col gap-5`} data-testid="logs-overview">
      <TipProvider>
        <Card title={t("logVolume.title")} sub={t("logVolume.sub", { bucket, range: rangeWord }) + subNote} tools={tools}>
          {!overview || !volume || !totals ? (
            <Empty text={failed || (overview && !volume) ? t("overview.failed") : t("overview.pending")} />
          ) : (
            <>
              <div className={s.kpis}>
                <Kpi label={t("logVolume.lines")} value={n(totals.lines)} note={t("logVolume.linesNote")} />
                <Kpi
                  label={t("logVolume.errors")}
                  value={n(totals.errors)}
                  note={t("logVolume.errorsNote", { ratio: totals.lines ? pct(totals.errors / totals.lines, 1) : "—" })}
                  tone={totals.errors ? "bad" : "ok"}
                />
                <Kpi
                  label={t("logVolume.warnings")}
                  value={n(totals.warnings)}
                  note={t("logVolume.warningsNote", { ratio: totals.lines ? pct(totals.warnings / totals.lines, 1) : "—" })}
                  tone={totals.warnings ? "warn" : "ok"}
                />
                <Kpi
                  label={t("logVolume.mostErrors")}
                  value={perApp && perApp[0] && perApp[0].errors > 0 ? perApp[0].app.name : "—"}
                  note={perApp && perApp[0] && perApp[0].errors > 0 ? t("logVolume.mostErrorsNote", { count: n(perApp[0].errors) }) : undefined}
                />
              </div>
              {totals.lines === 0 ? (
                <Empty text={t("overview.noLogs")} />
              ) : (
                <>
                  <div className="px-[26px] pt-5 pb-2">
                    <StackedColumns
                      buckets={overview.buckets}
                      segments={volume.map((b) => [
                        { key: "other", value: Math.max(0, b.lines - b.errors - b.warnings), colour: SEVERITY_COLOUR.other, label: t("logVolume.keyOther") },
                        { key: "warnings", value: b.warnings, colour: SEVERITY_COLOUR.warnings, label: t("logVolume.keyWarnings") },
                        { key: "errors", value: b.errors, colour: SEVERITY_COLOUR.errors, label: t("logVolume.keyErrors") },
                      ])}
                      tipTitle={(i) => hour(overview.buckets[i])}
                      totalLabel={t("logVolume.lines")}
                    />
                    <HourTicks buckets={overview.buckets} every={tickEvery} />
                  </div>
                  <Legend
                    items={[
                      { key: "errors", colour: SEVERITY_COLOUR.errors, label: t("logVolume.keyErrors"), sub: n(totals.errors) },
                      { key: "warnings", colour: SEVERITY_COLOUR.warnings, label: t("logVolume.keyWarnings"), sub: n(totals.warnings) },
                      { key: "other", colour: SEVERITY_COLOUR.other, label: t("logVolume.keyOther"), sub: n(Math.max(0, totals.lines - totals.errors - totals.warnings)) },
                    ]}
                  />
                </>
              )}
            </>
          )}
        </Card>
      </TipProvider>

      <div className="grid gap-5 xl:grid-cols-3">
        <Card title={t("byApp.title")} sub={t("byApp.sub")} testId="logs-by-app">
          {perApp === null ? (
            <Empty text={signals ? t("overview.failed") : t("overview.pending")} />
          ) : perApp.length === 0 ? (
            <Empty text={t("overview.noLogs")} />
          ) : (
            <ul className="flex flex-col gap-3 px-[22px] pt-1 pb-5">
              {perApp.map((x) => {
                const peak = Math.max(...perApp.map((y) => y.errors + y.warnings), 1);
                return (
                  <li key={x.app.projectId}>
                    <button
                      type="button"
                      onClick={() => onPickApp(x.app.projectId)}
                      className="group grid w-full grid-cols-[1.75rem_minmax(0,1fr)] items-center gap-3 rounded-lg text-left"
                    >
                      <ProjectLogo projectId={x.app.projectId} name={x.app.name} size="md" variant="mark" />
                      <span className="min-w-0">
                        <span className="flex items-baseline justify-between gap-3">
                          <span className="truncate text-[0.875rem] font-semibold text-ink group-hover:text-brand-accent">{x.app.name}</span>
                          <span data-tabular className="shrink-0 font-mono text-[12px] text-ink-secondary">
                            <span className={x.errors ? "font-semibold text-critical" : ""}>{n(x.errors)}</span>
                            {" · "}
                            <span className={x.warnings ? "text-warning" : ""}>{n(x.warnings)}</span>
                          </span>
                        </span>
                        <span className="mt-1.5 flex h-2 overflow-hidden rounded-full bg-surface-3">
                          <span style={{ width: `${(x.errors / peak) * 100}%`, background: SEVERITY_COLOUR.errors }} />
                          <span style={{ width: `${(x.warnings / peak) * 100}%`, background: SEVERITY_COLOUR.warnings }} />
                        </span>
                        <span className="mt-1 block font-mono text-[10.5px] text-ink-muted">{t("patterns.lines", { count: n(x.lines) })}</span>
                      </span>
                    </button>
                    {x.envs.length > 0 ? (
                      <ul className="mt-2 ml-10 flex flex-col gap-1 border-l border-border pl-3">
                        {x.envs.map((e) => (
                          <li key={e.env.environmentId} className="flex items-center gap-2 text-[11.5px]">
                            <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: kindColour(e.env.key) }} />
                            <span className="font-mono text-ink-secondary">{e.env.key}</span>
                            <span data-tabular className="ml-auto font-mono text-ink-muted">
                              <span className={e.errors ? "font-semibold text-critical" : ""}>{n(e.errors)}</span>
                              {" · "}
                              <span className={e.warnings ? "text-warning" : ""}>{n(e.warnings)}</span>
                            </span>
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
        <Card title={t("loudest.title")} sub={t("loudest.sub")} testId="loudest-services">
          {logItems === null ? (
            <Empty text={signals ? t("overview.failed") : t("overview.pending")} />
          ) : loudest.length === 0 ? (
            <Empty text={t("overview.noLogs")} />
          ) : (
            <ul className="pb-3">
              {loudest.map((x) => {
                const row = byBinding.get(x.id);
                const lead = loudest[0];
                const leadScore = lead.errors || lead.warnings || lead.lines || 1;
                const score = lead.errors ? x.errors : lead.warnings ? x.warnings : x.lines;
                return (
                  <RankRow
                    key={x.id}
                    row={row}
                    label={rowName(row, x.id)}
                    sub={rowWhere(row)}
                    share={score / leadScore}
                    value={x.errors ? n(x.errors) : x.warnings ? n(x.warnings) : n(x.lines)}
                    colour={x.errors ? SEVERITY_COLOUR.errors : x.warnings ? SEVERITY_COLOUR.warnings : SEVERITY_COLOUR.other}
                    valueTone={x.errors ? "text-critical" : x.warnings ? "text-warning" : "text-ink"}
                    onOpen={() => onOpen(x.id)}
                  />
                );
              })}
            </ul>
          )}
        </Card>
        <Card title={t("patterns.title")} sub={t("patterns.sub", { range: rangeWord })} testId="log-patterns">
          {!overview ? (
            <Empty text={failed ? t("overview.failed") : t("overview.pending")} />
          ) : !patterns ? (
            <Empty text={t("overview.failed")} />
          ) : patterns.length === 0 ? (
            <Empty text={t("patterns.none")} />
          ) : (
            <ul className="flex flex-col gap-2 px-[22px] pt-1 pb-5">
              {patterns.slice(0, 6).map((p, i) => {
                const severe = p.severity_number >= 17;
                const warn = !severe && p.severity_number >= 13;
                const first = byBinding.get(p.binding_ids[0] ?? "");
                return (
                  <li key={`${p.pattern}-${i}`}>
                    <button
                      type="button"
                      disabled={!p.binding_ids[0]}
                      onClick={() => p.binding_ids[0] && onOpen(p.binding_ids[0])}
                      className="group w-full rounded-lg border border-border bg-surface-2 px-3 py-2 text-left transition-colors hover:border-border-strong disabled:cursor-default"
                    >
                      <span className="flex items-center gap-2">
                        <span
                          className={`shrink-0 rounded px-1.5 py-px font-mono text-[10px] font-semibold ${
                            severe ? "bg-critical-soft text-critical" : warn ? "bg-warning-soft text-warning" : "bg-surface-3 text-ink-secondary"
                          }`}
                        >
                          {p.severity_text ?? (severe ? "ERROR" : warn ? "WARN" : "INFO")}
                        </span>
                        <span data-tabular className="ml-auto shrink-0 font-mono text-[11px] text-ink-secondary">
                          {t("patterns.lines", { count: n(p.lines) })}
                        </span>
                      </span>
                      <span className="mt-1.5 block truncate font-mono text-[12px] text-ink" title={p.pattern}>
                        {p.pattern}
                      </span>
                      <span className="mt-1 block truncate font-mono text-[10.5px] text-ink-muted">
                        {first ? `${rowName(first, "")} · ${rowWhere(first)}` : ""}
                        {p.binding_ids.length > 1 ? ` +${p.binding_ids.length - 1}` : ""}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
