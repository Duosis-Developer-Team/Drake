"use client";

/**
 * The Respond lists' pictures: what happened day by day, which app it
 * happened in, and how the recent window compares.
 *
 * Every chart here is drawn from a bounded SAMPLE of the list read — the
 * newest rows inside one window, paged up to a ceiling — and says so when the
 * window held more than the ceiling. Counts that matter (the tiles) still come
 * from the server's own totals; a sample only ever draws a shape.
 */

import { useEffect, useMemo, useState, type ReactNode } from "react";

import { kindColour } from "@/components/apps/AppLauncher";
import { ProjectLogo } from "@/components/ui/ProjectLogo";
import { apiGet } from "@/lib/api";
import { useFormat } from "@/lib/i18n";
import { useResource } from "@/lib/useResource";

/* ---------------- data ---------------- */

export interface Sample<T> {
  items: T[] | null;
  /** Every row in the window was read; false when the ceiling cut it. */
  complete: boolean;
  failed: boolean;
}

/**
 * The newest rows behind `path`, paged by cursor or offset up to `max`.
 * Null `path` reads nothing.
 */
export function useSample<T>(
  path: string | null,
  { paging = "cursor", max = 200 }: { paging?: "cursor" | "offset"; max?: number } = {},
): Sample<T> {
  const [state, setState] = useState<{ path: string | null; items: T[] | null; complete: boolean; failed: boolean }>({
    path: null,
    items: null,
    complete: false,
    failed: false,
  });
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    const separator = path.includes("?") ? "&" : "?";
    (async () => {
      const items: T[] = [];
      let next: string | null = null;
      let complete = false;
      for (let page = 0; page < Math.ceil(max / 50); page += 1) {
        const query: string =
          paging === "offset"
            ? `${separator}limit=50&offset=${items.length}`
            : `${separator}limit=50${next ? `&cursor=${encodeURIComponent(next)}` : ""}`;
        const body: { items: T[]; next_cursor?: string | null; total?: number } = await apiGet<{ items: T[]; next_cursor?: string | null; total?: number }>(
          `${path}${query}`,
          controller.signal,
        );
        items.push(...body.items);
        if (paging === "offset") {
          complete = body.items.length < 50 || (typeof body.total === "number" && items.length >= body.total);
        } else {
          next = body.next_cursor ?? null;
          complete = !next;
        }
        if (complete || items.length >= max) break;
      }
      if (!controller.signal.aborted) setState({ path, items, complete, failed: false });
    })().catch(() => {
      if (!controller.signal.aborted) setState({ path, items: null, complete: false, failed: true });
    });
    return () => controller.abort();
  }, [path, paging, max]);
  const current = state.path === path;
  return {
    items: current ? state.items : null,
    complete: current && state.complete,
    failed: current && state.failed,
  };
}

export interface ProjectRef {
  id: string;
  name: string;
}

/** Projects by key, so a list that only carries `project_key` can draw logos. */
export function useProjectIndex(): Map<string, ProjectRef> {
  const projects = useResource<{ projects: { id: string; project_key: string; display_name: string }[] }>(
    "/v1/projects?lifecycle=all&limit=100",
  );
  return useMemo(
    () =>
      new Map(
        (projects.data?.projects ?? []).map((p) => [p.project_key, { id: p.id, name: p.display_name }]),
      ),
    [projects.data],
  );
}

/** Where a namespace lives in the catalog: its app and environment. */
export interface Place {
  projectKey: string;
  environmentKey: string;
}

/**
 * Namespace → app and environment, per cluster, from each cluster's own
 * detail read (the environments that reference it). Lets a row that carries
 * no binding — a rollout of a workload nobody bound to a service — still be
 * drawn under the app it belongs to. Keys are `${clusterId}/${namespace}`.
 */
export function useNamespaceIndex(clusterIds: string[]): Map<string, Place> {
  const key = [...new Set(clusterIds)].sort().join(",");
  const [index, setIndex] = useState<{ key: string; map: Map<string, Place> }>({ key: "", map: new Map() });
  useEffect(() => {
    if (!key) return;
    const controller = new AbortController();
    Promise.all(
      key.split(",").map((id) =>
        apiGet<{ referenced_environments?: { project_key: string; environment_key: string; namespace: string | null }[] }>(
          `/v1/clusters/${encodeURIComponent(id)}`,
          controller.signal,
        )
          .then((body) => [id, body.referenced_environments ?? []] as const)
          .catch(() => [id, []] as const),
      ),
    ).then((pairs) => {
      if (controller.signal.aborted) return;
      const map = new Map<string, Place>();
      for (const [id, envs] of pairs)
        for (const env of envs)
          if (env.namespace) map.set(`${id}/${env.namespace}`, { projectKey: env.project_key, environmentKey: env.environment_key });
      setIndex({ key, map });
    });
    return () => controller.abort();
  }, [key]);
  return index.key === key ? index.map : NO_PLACES;
}
const NO_PLACES = new Map<string, Place>();

/** Start of the local day, as a millisecond timestamp. */
function dayStart(ms: number): number {
  const d = new Date(ms);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

/** The last `days` local days, oldest first, as day-start timestamps. */
export function dayAxis(days: number, now = Date.now()): number[] {
  const today = dayStart(now);
  return Array.from({ length: days }, (_, i) => {
    const d = new Date(today);
    d.setDate(d.getDate() - (days - 1 - i));
    return d.getTime();
  });
}

/** Index of `iso`'s day on `axis`, or -1 outside it. */
export function dayIndex(axis: number[], iso: string | null | undefined): number {
  if (!iso) return -1;
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return -1;
  return axis.indexOf(dayStart(ms));
}

/**
 * `count` buckets of `hours` each, ending with the one that holds now, as
 * bucket-start timestamps (aligned to local midnight).
 */
export function bucketAxis(count: number, hours: number, now = Date.now()): number[] {
  const size = hours * 3_600_000;
  const today = dayStart(now);
  const current = today + Math.floor((now - today) / size) * size;
  return Array.from({ length: count }, (_, i) => current - (count - 1 - i) * size);
}

/** Index of `iso`'s bucket on an even `axis` of `hours`-long buckets, or -1. */
export function bucketIndex(axis: number[], hours: number, iso: string | null | undefined): number {
  if (!iso || axis.length === 0) return -1;
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return -1;
  const i = Math.floor((ms - axis[0]) / (hours * 3_600_000));
  return i >= 0 && i < axis.length ? i : -1;
}

export function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

/* ---------------- marks ---------------- */

export interface Series {
  key: string;
  label: string;
  colour: string;
}

/**
 * One stacked bar per day. A day with nothing draws a hairline, so an empty
 * day reads as "measured, none" next to the days that had some.
 */
export function DayBars({
  axis,
  series,
  counts,
  height = 112,
  label,
  hours = 24,
}: {
  axis: number[];
  series: Series[];
  /** Per bucket on `axis`, per series key. */
  counts: Record<string, number>[];
  height?: number;
  label: string;
  /** Bucket size; under a day the labels carry the hour too. */
  hours?: number;
}) {
  const fmt = useFormat();
  const totals = counts.map((c) => series.reduce((sum, s) => sum + (c[s.key] ?? 0), 0));
  const peak = Math.max(1, ...totals);
  const day = (ms: number) =>
    new Date(ms).toLocaleString(
      fmt.tag,
      hours < 24 ? { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" } : { day: "numeric", month: "short" },
    );
  const ticks = [0, Math.floor((axis.length - 1) / 2), axis.length - 1];
  return (
    <figure className="m-0" aria-label={label}>
      <div className="flex items-end gap-[3px]" style={{ height }} role="img" aria-label={label}>
        {axis.map((ms, i) => {
          const total = totals[i];
          const tip = [
            `${day(ms)} · ${fmt.number(total)}`,
            ...series.filter((s) => counts[i][s.key]).map((s) => `${s.label}: ${fmt.number(counts[i][s.key])}`),
          ].join("\n");
          return (
            <div key={ms} title={tip} className="group flex h-full min-w-0 flex-1 flex-col justify-end">
              {total === 0 ? (
                <span className="block h-[2px] rounded-full bg-surface-3" />
              ) : (
                <span
                  className="flex flex-col-reverse overflow-hidden rounded-[3px] transition-opacity group-hover:opacity-80"
                  style={{
                    height: `${Math.max(6, (total / peak) * 100)}%`,
                    animation: `rise-in .5s cubic-bezier(.2,.8,.2,1) ${i * 12}ms both`,
                  }}
                >
                  {series.map((s) =>
                    counts[i][s.key] ? (
                      <span key={s.key} style={{ flexGrow: counts[i][s.key], background: s.colour }} className="min-h-[2px]" />
                    ) : null,
                  )}
                </span>
              )}
            </div>
          );
        })}
      </div>
      <div className="mt-2 flex justify-between font-mono text-[10.5px] text-ink-muted">
        {ticks.map((i) => (
          <span key={i}>{day(axis[i])}</span>
        ))}
      </div>
    </figure>
  );
}

/**
 * Rows by days: one row per place (an app's environment), one cell per day,
 * its depth the count, with a ring on a day that went wrong. A row with no
 * activity is not drawn: the grid shows where things happened.
 */
export function HeatGrid({
  axis,
  rows,
  label,
}: {
  axis: number[];
  rows: { key: string; label: ReactNode; counts: number[]; bad: boolean[]; meta?: ReactNode }[];
  label: string;
}) {
  const fmt = useFormat();
  const peak = Math.max(1, ...rows.flatMap((r) => r.counts));
  const withMeta = rows.some((r) => r.meta !== undefined);
  const day = (ms: number) => new Date(ms).toLocaleDateString(fmt.tag, { day: "numeric", month: "short" });
  const ticks = [0, Math.floor((axis.length - 1) / 2), axis.length - 1];
  return (
    <figure className="m-0" aria-label={label}>
      <div
        className={`grid items-center gap-x-3 gap-y-1.5 ${withMeta ? "grid-cols-[minmax(7rem,12rem)_minmax(0,1fr)_auto]" : "grid-cols-[minmax(7rem,12rem)_minmax(0,1fr)]"}`}
        role="img"
        aria-label={label}
      >
        {rows.map((row, r) => (
          <div key={row.key} className="contents">
            <div className="min-w-0 truncate">{row.label}</div>
            <div className="flex gap-[3px]">
              {row.counts.map((count, i) => (
                <span
                  key={axis[i]}
                  title={`${day(axis[i])} · ${fmt.number(count)}`}
                  className={`h-5 min-w-0 flex-1 rounded-[4px] ${row.bad[i] ? "ring-2 ring-[var(--status-critical)] ring-inset" : ""}`}
                  style={{
                    background:
                      count === 0
                        ? "var(--surface-3)"
                        : `color-mix(in srgb, var(--brand-accent) ${Math.round(28 + (count / peak) * 72)}%, transparent)`,
                    animation: `fade-in .4s ease-out ${r * 40 + i * 6}ms both`,
                  }}
                />
              ))}
            </div>
            {withMeta ? (
              <div className="min-w-[2rem] text-right font-mono text-[11px] font-semibold text-ink" data-tabular>
                {row.meta}
              </div>
            ) : null}
          </div>
        ))}
        <div />
        <div className="mt-1 flex justify-between font-mono text-[10.5px] text-ink-muted">
          {ticks.map((i) => (
            <span key={i}>{day(axis[i])}</span>
          ))}
        </div>
        {withMeta ? <div /> : null}
      </div>
    </figure>
  );
}

/* ---------------- spans over time ---------------- */

export interface Span {
  id: string;
  start: number;
  /** Null while it is still going on: the bar runs to now, hatched. */
  end: number | null;
  colour: string;
  title: string;
}

export interface Lane {
  key: string;
  label: ReactNode;
  spans: Span[];
  /** Short right-hand summary, e.g. "3 · longest 2h". */
  meta?: ReactNode;
}

const MAX_TRACKS = 3;

/** Spans into tracks so that overlapping ones sit on separate rows. */
function pack(spans: Span[], now: number, gap: number): { span: Span; track: number }[] {
  const ends: number[] = [];
  return [...spans]
    .sort((a, b) => a.start - b.start)
    .map((span) => {
      let track = ends.findIndex((end) => end + gap <= span.start);
      if (track < 0) track = ends.length < MAX_TRACKS ? ends.length : MAX_TRACKS - 1;
      ends[track] = Math.max(ends[track] ?? 0, span.end ?? now);
      return { span, track };
    });
}

/**
 * One lane per place (an app's environment), each event drawn from when it
 * started to when it ended — so a lane shows how often AND how long, and a
 * week with three alerts reads as three marks where they happened rather
 * than one lonely bar in an empty histogram. Still-open spans run to the
 * right edge (now), hatched.
 */
export function SpanTimeline({
  from,
  to,
  lanes,
  tickDays,
  label,
}: {
  from: number;
  to: number;
  lanes: Lane[];
  /** A day label every N days. */
  tickDays: number;
  label: string;
}) {
  const fmt = useFormat();
  const range = Math.max(1, to - from);
  const pct = (ms: number) => Math.min(100, Math.max(0, ((ms - from) / range) * 100));
  const days: number[] = [];
  const first = new Date(from);
  first.setHours(0, 0, 0, 0);
  for (let d = new Date(first); d.getTime() <= to; d.setDate(d.getDate() + 1)) if (d.getTime() >= from) days.push(d.getTime());
  // A label every `tickDays`, counted back from today; none hugging the left
  // edge, where a partial first day would crowd the next label.
  const ticks = days.filter((d, i) => (days.length - 1 - i) % tickDays === 0 && pct(d) > 8);
  const when = (ms: number) =>
    new Date(ms).toLocaleString(fmt.tag, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  return (
    <figure className="m-0" aria-label={label}>
      <div className="grid grid-cols-[minmax(6.5rem,11rem)_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-2">
        {lanes.map((lane, l) => {
          const placed = pack(lane.spans, to, range * 0.006);
          const tracks = Math.max(1, ...placed.map((p) => p.track + 1));
          return (
            <div key={lane.key} className="contents">
              <div className="min-w-0 truncate">{lane.label}</div>
              <div
                className="relative rounded-md bg-surface-2/70"
                style={{ height: tracks * 10 + 8 }}
                role="group"
                aria-label={lane.spans.map((s) => s.title).join("; ")}
              >
                {days.map((d) => (
                  <span key={d} aria-hidden className="absolute inset-y-0 w-px bg-border/60" style={{ left: `${pct(d)}%` }} />
                ))}
                {placed.map(({ span, track }, i) => {
                  const left = pct(span.start);
                  const right = pct(span.end ?? to);
                  const open = span.end === null;
                  return (
                    <span
                      key={span.id}
                      aria-hidden
                      title={`${span.title}\n${when(span.start)} → ${open ? "…" : when(span.end!)}`}
                      className="absolute h-2 rounded-full transition-[filter] hover:brightness-125"
                      style={{
                        left: `${left}%`,
                        width: `max(8px, ${right - left}%)`,
                        top: 4 + track * 10,
                        background: open
                          ? `repeating-linear-gradient(135deg, ${span.colour} 0 5px, color-mix(in srgb, ${span.colour} 45%, transparent) 5px 10px)`
                          : span.colour,
                        boxShadow: `0 0 0 1px color-mix(in srgb, ${span.colour} 35%, transparent)`,
                        animation: `fade-in .4s ease-out ${l * 60 + i * 20}ms both`,
                      }}
                    />
                  );
                })}
                <span aria-hidden className="absolute inset-y-0 right-0 w-0.5 rounded-full bg-ink-muted/50" />
              </div>
              <div className="min-w-[2.5rem] text-right text-micro text-ink-muted" data-tabular>
                {lane.meta}
              </div>
            </div>
          );
        })}
        <div />
        <div className="relative h-4 font-mono text-[10.5px] text-ink-muted">
          {ticks.map((d) => (
            <span key={d} className="absolute -translate-x-1/2 whitespace-nowrap first:translate-x-0" style={{ left: `${pct(d)}%` }}>
              {new Date(d).toLocaleDateString(fmt.tag, { day: "numeric", month: "short" })}
            </span>
          ))}
        </div>
        <div />
      </div>
    </figure>
  );
}

/** Duration edges, in seconds, for "how long did they last". */
const DURATION_EDGES = [900, 3600, 4 * 3600, 86400];

/**
 * How long things lasted, in five plain buckets — under 15 minutes up to over
 * a day. Ongoing ones are counted separately, never folded into a bucket
 * they have not finished.
 */
export function DurationBuckets({
  seconds,
  ongoing,
  colour = "var(--brand-accent)",
  ongoingLabel,
}: {
  seconds: number[];
  ongoing: number;
  colour?: string;
  ongoingLabel: string;
}) {
  const fmt = useFormat();
  const d = (s: number) => fmt.duration(s, { compact: true });
  const labels = [
    `< ${d(DURATION_EDGES[0])}`,
    `${d(DURATION_EDGES[0])}–${d(DURATION_EDGES[1])}`,
    `${d(DURATION_EDGES[1])}–${d(DURATION_EDGES[2])}`,
    `${d(DURATION_EDGES[2])}–${d(DURATION_EDGES[3])}`,
    `> ${d(DURATION_EDGES[3])}`,
  ];
  const counts = labels.map(() => 0);
  for (const s of seconds) {
    const i = DURATION_EDGES.findIndex((edge) => s < edge);
    counts[i < 0 ? labels.length - 1 : i] += 1;
  }
  const peak = Math.max(1, ...counts, ongoing);
  const rows = [...labels.map((label, i) => ({ label, count: counts[i], hatched: false })), ...(ongoing ? [{ label: ongoingLabel, count: ongoing, hatched: true }] : [])];
  return (
    <ul className="flex flex-col gap-1.5">
      {rows.map((row) => (
        <li key={row.label} className="grid grid-cols-[5.5rem_minmax(0,1fr)_2rem] items-center gap-2">
          <span className="truncate font-mono text-[10.5px] text-ink-muted">{row.label}</span>
          <span className="block h-2 overflow-hidden rounded-full bg-surface-3">
            {row.count ? (
              <span
                className="block h-full rounded-full"
                style={{
                  width: `${Math.max(6, (row.count / peak) * 100)}%`,
                  background: row.hatched
                    ? `repeating-linear-gradient(135deg, ${colour} 0 5px, color-mix(in srgb, ${colour} 45%, transparent) 5px 10px)`
                    : colour,
                }}
              />
            ) : null}
          </span>
          <span data-tabular className={`text-right font-mono text-[11px] ${row.count ? "font-semibold text-ink" : "text-ink-muted"}`}>
            {fmt.number(row.count)}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** One bar split by share — "where it came from" — with its legend. */
export function ShareBar({ segments }: { segments: { key: string; label: ReactNode; colour: string; count: number }[] }) {
  const fmt = useFormat();
  const shown = segments.filter((s) => s.count > 0);
  const total = shown.reduce((a, s) => a + s.count, 0);
  if (!total) return null;
  return (
    <div className="flex flex-col gap-2.5">
      <span className="flex h-2.5 gap-[2px] overflow-hidden rounded-full">
        {shown.map((s) => (
          <span key={s.key} className="h-full first:rounded-l-full last:rounded-r-full" style={{ flexGrow: s.count, background: s.colour }} />
        ))}
      </span>
      <ul className="flex flex-col gap-1.5">
        {shown.map((s) => (
          <li key={s.key} className="flex min-w-0 items-center gap-2 text-caption">
            <span aria-hidden className="h-2 w-2 shrink-0 rounded-[2px]" style={{ background: s.colour }} />
            <span className="min-w-0 flex-1 truncate text-ink">{s.label}</span>
            <span data-tabular className="font-mono text-[11px] font-semibold text-ink">{fmt.number(s.count)}</span>
            <span data-tabular className="w-9 text-right font-mono text-[10.5px] text-ink-muted">
              {fmt.number((s.count / total) * 100, { maximumFractionDigits: 0 })}%
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** The app palette: one stable colour per app, by order of appearance. */
export const APP_PALETTE = ["var(--series-1)", "var(--series-2)", "var(--series-4)", "var(--series-3)", "var(--series-5)", "var(--series-6)"];

export function Legend({ items }: { items: { label: string; colour: string; value?: ReactNode }[] }) {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1.5 text-micro text-ink-secondary">
      {items.map((item) => (
        <li key={item.label} className="inline-flex items-center gap-1.5">
          <span aria-hidden className="h-2 w-2 rounded-[2px]" style={{ background: item.colour }} />
          {item.label}
          {item.value !== undefined ? <b className="font-mono font-semibold text-ink" data-tabular>{item.value}</b> : null}
        </li>
      ))}
    </ul>
  );
}

/** A labelled number in the analytics band. */
export function Figure({
  label,
  value,
  note,
  tone,
}: {
  label: string;
  value: string;
  note?: string;
  tone?: "critical" | "warning" | "success";
}) {
  const colour =
    value === "—" ? "text-ink-muted" : tone === "critical" ? "text-critical" : tone === "warning" ? "text-warning" : tone === "success" ? "text-healthy" : "text-ink";
  return (
    <div className="min-w-0">
      <p className="truncate text-[11px] font-medium tracking-[0.02em] text-ink-muted">{label}</p>
      <p data-tabular className={`mt-1 font-[family-name:var(--font-display)] text-[1.625rem] leading-none font-bold tracking-[-0.03em] ${colour}`}>
        {value}
      </p>
      {note ? <p className="mt-1 line-clamp-2 text-[11px] leading-snug text-ink-muted">{note}</p> : null}
    </div>
  );
}

/** A ranked row: label, a bar for its share of the largest, and its count. */
export function RankBar({
  label,
  value,
  peak,
  colour = "var(--brand-accent)",
}: {
  label: ReactNode;
  value: number;
  peak: number;
  colour?: string;
}) {
  const fmt = useFormat();
  return (
    <li className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1">
      <span className="min-w-0 truncate text-caption text-ink">{label}</span>
      <span data-tabular className="font-mono text-caption font-semibold text-ink">
        {fmt.number(value)}
      </span>
      <span className="col-span-2 block h-1.5 overflow-hidden rounded-full bg-surface-3">
        <span className="block h-full rounded-full" style={{ width: `${Math.max(4, (value / Math.max(1, peak)) * 100)}%`, background: colour }} />
      </span>
    </li>
  );
}

/**
 * Which app and environment a row belongs to: the project's logo, its name,
 * and the environment as a chip in its kind's colour. `null` project reads
 * as outside the catalog.
 */
export function AppTag({
  projectKey,
  environmentKey,
  projects,
  outside,
}: {
  projectKey: string | null;
  environmentKey: string | null;
  projects: Map<string, ProjectRef>;
  /** The words for "not in the catalog". */
  outside: string;
}) {
  if (!projectKey) return <span className="text-micro text-ink-muted">{outside}</span>;
  const project = projects.get(projectKey);
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5">
      {project ? (
        <ProjectLogo projectId={project.id} name={project.name} size="sm" variant="mark" />
      ) : null}
      <span className="truncate text-caption font-medium text-ink">{project?.name ?? projectKey}</span>
      {environmentKey ? (
        <span className="inline-flex items-center gap-1 rounded-full border border-border bg-surface-2 px-1.5 py-px font-mono text-[10.5px] text-ink-secondary">
          <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: kindColour(environmentKey) }} />
          {environmentKey}
        </span>
      ) : null}
    </span>
  );
}

/** The analytics band: a dashboard card with a title, a chart and figures. */
export function BandCard({
  title,
  note,
  children,
  "data-testid": testId,
}: {
  title: string;
  note?: ReactNode;
  children: ReactNode;
  "data-testid"?: string;
}) {
  return (
    <section
      data-testid={testId}
      aria-label={title}
      className="animate-[rise-in_.45s_cubic-bezier(.2,.8,.2,1)_both] rounded-panel border border-border bg-surface p-5 sm:p-6"
    >
      <header className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-[0.9375rem] font-semibold tracking-[-0.01em] text-ink">{title}</h2>
        {note ? <p className="text-micro text-ink-muted">{note}</p> : null}
      </header>
      {children}
    </section>
  );
}

/** A heading row inside a list: groups the rows under it, is not a row itself. */
export function ListDivider({ label, count }: { label: string; count?: string }) {
  return (
    <li role="presentation" className="flex items-baseline gap-2 bg-surface-2/60 px-7 py-2">
      <span className="text-[11px] font-semibold tracking-[0.08em] text-ink-secondary uppercase">{label}</span>
      {count ? <span className="text-[11px] text-ink-muted">{count}</span> : null}
    </li>
  );
}

/** "Today", "Yesterday" or the date, for a day divider. */
export function useDayName() {
  const fmt = useFormat();
  return (iso: string, today: string, yesterday: string): string => {
    const ms = Date.parse(iso);
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    const day = new Date(ms);
    day.setHours(0, 0, 0, 0);
    const diff = Math.round((start.getTime() - day.getTime()) / 86_400_000);
    if (diff === 0) return today;
    if (diff === 1) return yesterday;
    return day.toLocaleDateString(fmt.tag, { weekday: "long", day: "numeric", month: "long" });
  };
}

/** A quick filter as a chip; pressed when its filter is the active one. */
export function Chip({
  active,
  onClick,
  children,
  dot,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
  dot?: string;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={`inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-caption font-medium transition-colors ${
        active ? "border-border-strong bg-surface-selected text-ink" : "border-border bg-surface text-ink-secondary hover:bg-surface-hover"
      }`}
    >
      {dot ? <span aria-hidden className="h-2 w-2 rounded-full" style={{ background: dot }} /> : null}
      {children}
    </button>
  );
}
