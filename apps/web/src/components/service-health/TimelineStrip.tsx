"use client";

/**
 * "When did it start?" — one service's last hour (or 15 minutes, 6 hours, a
 * day) as four thin lanes on one time axis: requests, the share that failed,
 * p95, and error log lines, with deployments and incidents marked on a rail
 * underneath. Hovering reads a slice out; clicking it narrows the request
 * and log panels below to that slice.
 *
 * Each lane says its own state. A lane the store did not answer for, or the
 * caller may not read, says so in words: an empty lane is never drawn as a
 * flat, healthy line.
 */

import { CalendarClock } from "lucide-react";
import { useMemo, useState } from "react";

import { useApi } from "@/components/catalog/primitives";
import { IconBubble } from "@/components/service-health/primitives";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { SegmentedControl } from "@/components/ui/controls";
import { ErrorState, LoadingSkeleton } from "@/components/ui/states";
import { deploymentListPath, type DeploymentPage } from "@/lib/deployments";
import { useT } from "@/lib/i18n";
import type { IncidentSummary } from "@/lib/incidents";
import { utcClock } from "@/lib/logs";
import { percent } from "@/lib/signals";
import {
  TIMELINE_RANGES,
  lanePaths,
  positionIn,
  timelinePath,
  type LaneState,
  type TimelinePage,
  type TimelineRange,
  type TimelineWindow,
} from "@/lib/timeline";
import { formatDuration } from "@/lib/traces";
import { useResource } from "@/lib/useResource";

const LANE_HEIGHT = "h-14";

function hhmm(timestamp: string): string {
  return utcClock(timestamp).slice(0, 5);
}

export function TimelineStrip({
  bindingId,
  environmentServiceId,
  selected,
  onSelect,
}: {
  bindingId: string;
  environmentServiceId: string;
  selected: TimelineWindow | null;
  onSelect: (window: TimelineWindow | null) => void;
}) {
  const t = useT("serviceHealth");
  const [range, setRange] = useState<TimelineRange>("1h");
  const [hover, setHover] = useState<number | null>(null);
  const timeline = useResource<TimelinePage>(timelinePath(bindingId, range), { refreshMs: 60_000 });
  const deployments = useResource<DeploymentPage>(
    deploymentListPath({ environmentServiceId, startedWithin: "24h" }),
  );
  const [incidents] = useApi<{ items: IncidentSummary[] }>(
    `/v1/service-health/bindings/${bindingId}/incidents`,
  );

  const page = timeline.data;
  const buckets = page?.buckets ?? [];
  const requests = useMemo(
    () => (page?.requests.state === "ok" ? page.requests.buckets : []),
    [page],
  );
  const logs = useMemo(() => (page?.logs.state === "ok" ? page.logs.buckets : []), [page]);

  const lanes = useMemo(() => {
    const count = (values: number[]) => lanePaths(values, Math.max(1, ...values));
    const ratio = requests.map((b) => (b.error_ratio === null ? null : b.error_ratio * 100));
    const p95 = requests.map((b) => b.p95_ms);
    return {
      requests: count(requests.map((b) => b.requests)),
      ratio: lanePaths(ratio, Math.max(10, ...ratio.map((v) => v ?? 0))),
      p95: lanePaths(p95, Math.max(1, ...p95.map((v) => v ?? 0)) * 1.1),
      errorLines: count(logs.map((b) => b.errors)),
    };
  }, [requests, logs]);

  // The slice the readout describes: the hovered one, else the selected
  // one, else the newest complete bucket.
  const focus = hover ?? (selected ? buckets.findIndex((b) => b.start === selected.start) : -1);
  const readoutIndex =
    focus >= 0 ? focus : Math.max(0, buckets.length - (buckets.at(-1)?.partial ? 2 : 1));
  const readout = buckets[readoutIndex];
  const rq = requests[readoutIndex];
  const lg = logs[readoutIndex];

  const events = useMemo(() => {
    if (!page) return [];
    const out: { key: string; at: number; label: string; tone: "deploy" | "incident" }[] = [];
    for (const row of deployments.data?.items ?? []) {
      const at = positionIn(page.window, row.rollout_started_at);
      if (at !== null) {
        out.push({
          key: `d-${row.id}`,
          at,
          tone: "deploy",
          label: t("detail.timeline.deployed", { commit: row.short_commit ?? row.short_digest ?? "—" }),
        });
      }
    }
    if (incidents.state === "ready") {
      for (const incident of incidents.data.items) {
        const at = positionIn(page.window, incident.opened_at);
        if (at !== null) {
          out.push({ key: `i-${incident.id}`, at, tone: "incident", label: incident.title });
        }
      }
    }
    return out.sort((a, b) => a.at - b.at);
  }, [page, deployments.data, incidents, t]);

  // No traces.view and no logs.view here: the API answers 404, and the page
  // shows no strip rather than an error about a signal the reader may not see.
  const hidden = timeline.notFound || timeline.denied;

  const pick = (index: number) => {
    const bucket = buckets[index];
    if (!bucket) return;
    if (selected?.start === bucket.start) onSelect(null);
    else onSelect({ start: bucket.start, end: bucket.end, range });
  };

  if (hidden) return null;

  return (
    <Panel data-testid="timeline-strip">
      <PanelHeader
        title={
          <span className="flex items-center gap-3">
            <IconBubble icon={CalendarClock} />
            {t("detail.timeline.title")}
          </span>
        }
        description={t("detail.timeline.description")}
        actions={
          <SegmentedControl
            label={t("detail.timeline.range")}
            value={range}
            onChange={(next) => {
              setRange(next);
              setHover(null);
              onSelect(null);
            }}
            size="compact"
            options={TIMELINE_RANGES.map((value) => ({ value, label: value }))}
          />
        }
      />

      {!page && timeline.loading ? <LoadingSkeleton variant="chart" height={240} /> : null}
      {!page && !timeline.loading ? (
        <ErrorState compact description={timeline.error ?? undefined} onRetry={timeline.reload} />
      ) : null}

      {page ? (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2 text-micro">
            <span data-testid="timeline-readout" className="rounded-[0.6rem] bg-surface-2 px-3 py-1.5 font-mono text-ink-secondary">
              {readout
                ? `${hhmm(readout.start)}–${hhmm(readout.end)} UTC${readout.partial ? ` · ${t("detail.timeline.filling")}` : ""}`
                : "—"}
            </span>
            {selected ? (
              <button
                type="button"
                onClick={() => onSelect(null)}
                className="rounded-[0.6rem] border border-brand-accent px-3 py-1.5 text-brand-accent"
                data-testid="timeline-clear"
              >
                {t("detail.timeline.selected", { from: hhmm(selected.start), to: hhmm(selected.end) })}
              </button>
            ) : (
              <span className="text-ink-muted">{t("detail.timeline.hint")}</span>
            )}
          </div>

          <div className="relative grid grid-cols-[7.5rem_minmax(0,1fr)] gap-x-3" onMouseLeave={() => setHover(null)}>
            <Lane
              label={t("detail.timeline.lanes.requests")}
              value={rq ? rq.requests.toLocaleString() : null}
              state={page.requests.state}
              paths={lanes.requests}
              stroke="var(--color-brand-accent)"
              fill="var(--color-info-soft)"
            />
            <Lane
              label={t("detail.timeline.lanes.failed")}
              value={rq ? percent(rq.error_ratio) : null}
              valueClass={rq && rq.errors > 0 ? "text-critical" : undefined}
              state={page.requests.state}
              paths={lanes.ratio}
              stroke="var(--color-critical)"
              fill="var(--color-critical-soft)"
            />
            <Lane
              label={t("detail.timeline.lanes.p95")}
              value={rq ? formatDuration(rq.p95_ms) : null}
              state={page.requests.state}
              paths={lanes.p95}
              stroke="var(--series-2)"
              fill="transparent"
            />
            <Lane
              label={t("detail.timeline.lanes.errorLogs")}
              value={lg ? lg.errors.toLocaleString() : null}
              valueClass={lg && lg.errors > 0 ? "text-warning" : undefined}
              state={page.logs.state}
              paths={lanes.errorLines}
              stroke="var(--color-warning)"
              fill="var(--color-warning-soft)"
            />
            <div className="flex items-center border-t border-border py-2 text-micro text-ink-secondary">
              {t("detail.timeline.lanes.events")}
            </div>
            <div className="relative h-9 border-t border-border" data-testid="timeline-events">
              {events.map((event) => (
                <span
                  key={event.key}
                  title={event.label}
                  className={`absolute top-2 max-w-[11rem] -translate-x-1/2 truncate rounded-full px-2 py-0.5 text-micro ${
                    event.tone === "incident" ? "bg-critical-soft text-critical" : "bg-surface-2 text-ink-secondary"
                  }`}
                  style={{ left: `${event.at * 100}%` }}
                >
                  {event.label}
                </span>
              ))}
            </div>

            {/* Slices: one button per bucket over the plot column. */}
            <div className="pointer-events-none absolute inset-y-0 right-0 left-[calc(7.5rem+0.75rem)] flex">
              {buckets.map((bucket, index) => {
                const isSelected = selected?.start === bucket.start;
                return (
                  <button
                    key={bucket.start}
                    type="button"
                    onClick={() => pick(index)}
                    onMouseEnter={() => setHover(index)}
                    onFocus={() => setHover(index)}
                    aria-pressed={isSelected}
                    aria-label={t("detail.timeline.slice", {
                      from: hhmm(bucket.start),
                      to: hhmm(bucket.end),
                      requests: requests[index]?.requests ?? 0,
                      errors: requests[index]?.errors ?? 0,
                    })}
                    className={`pointer-events-auto min-w-0 flex-1 cursor-pointer rounded-[0.3rem] ${
                      isSelected
                        ? "bg-info-soft/60 ring-1 ring-brand-accent"
                        : hover === index
                          ? "bg-surface-hover/70"
                          : ""
                    }`}
                  />
                );
              })}
            </div>
          </div>

          <div className="ml-[calc(7.5rem+0.75rem)] flex justify-between font-mono text-micro text-ink-muted">
            <span>{hhmm(page.window.start)}</span>
            <span>{buckets.length > 2 ? hhmm(buckets[Math.floor(buckets.length / 2)].start) : ""}</span>
            <span>{hhmm(page.window.end)}</span>
          </div>
        </div>
      ) : null}
    </Panel>
  );
}

/** Shown in place of a panel's own range control while a timeline slice
 *  is selected: what the panel is reading, and a way back. */
export function WindowChip({ slice, onClear }: { slice: TimelineWindow; onClear?: () => void }) {
  const t = useT("serviceHealth");
  return (
    <button
      type="button"
      onClick={onClear}
      data-testid="window-chip"
      className="inline-flex items-center gap-2 rounded-full border border-brand-accent bg-info-soft px-3 py-1 text-caption text-brand-accent"
    >
      {t("detail.timeline.selected", { from: hhmm(slice.start), to: hhmm(slice.end) })}
    </button>
  );
}

function Lane({
  label,
  value,
  valueClass,
  state,
  paths,
  stroke,
  fill,
}: {
  label: string;
  value: string | null;
  valueClass?: string;
  state: LaneState;
  paths: { line: string; area: string };
  stroke: string;
  fill: string;
}) {
  const t = useT("serviceHealth");
  return (
    <>
      <div className={`flex flex-col justify-center border-t border-border ${LANE_HEIGHT}`}>
        <span className="text-micro text-ink-secondary">{label}</span>
        <span data-tabular className={`text-caption font-semibold ${valueClass ?? "text-ink"}`}>
          {state === "ok" ? (value ?? "—") : "—"}
        </span>
      </div>
      <div className={`relative border-t border-border ${LANE_HEIGHT}`}>
        {state === "ok" ? (
          <svg viewBox="0 0 1000 100" preserveAspectRatio="none" className="absolute inset-x-0 top-1.5 h-[calc(100%-0.75rem)] w-full" aria-hidden>
            <path d={paths.area} fill={fill} />
            <path d={paths.line} fill="none" stroke={stroke} strokeWidth={2} vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
          </svg>
        ) : (
          <span className="absolute inset-0 flex items-center text-micro text-ink-muted">
            {t.dyn("detail.timeline.laneState", state, state)}
          </span>
        )}
      </div>
    </>
  );
}
