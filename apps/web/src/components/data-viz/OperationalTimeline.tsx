"use client";

/**
 * The correlation timeline's track.
 *
 * DOM, not ECharts — matching `InlineBars.tsx`'s reasoning: a handful of
 * dots on a line does not need the chart engine's ~250 kB. One row per
 * lane; a lane with no history source (the service/cluster lane, today)
 * renders a stated "unavailable" reason rather than an empty or flat track,
 * which would otherwise read as "nothing happened" when the true answer is
 * "Drake cannot say". The `<details>` table repeats every plotted point as
 * a row — the same numbers, per `ChartFrame`'s own rule that a chart's
 * table is the relief for whatever the drawing cannot make legible.
 *
 * A dot's horizontal position is not the only way to read its time: the
 * axis row states the window's real start and end, and hovering or
 * keyboard-focusing a dot shows its event name and clock time in a visible
 * label, not only in the accessible name a mouse user never sees.
 *
 * Drawn as a timeline rather than a row of dots: hour gridlines, an
 * incident or alert as a span from opening to resolution (striped to "now"
 * while still open), deployments as pins. With nothing to plot it shrinks
 * to one strip that still names every lane.
 */

import { Bell, HeartPulse, Rocket, Siren, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { useId, useState } from "react";

import { toneSpec } from "@/lib/design/status";
import { useFormat, useT, type Locale } from "@/lib/i18n";
import type { TimelineEvent, TimelineLane } from "@/lib/view-models/timeline";

function laneIcon(key: string): LucideIcon {
  if (key.includes("incident")) return Siren;
  if (key.includes("alert")) return Bell;
  if (key.includes("deploy")) return Rocket;
  return HeartPulse;
}

/** A UTC clock time. `en-GB` for the 24-hour, zero-padded digits — the
 *  digits are the same in every locale, so this is not a translation. */
function formatClock(at: string): string {
  const date = new Date(at);
  if (Number.isNaN(date.getTime())) return at;
  return `${date.toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
  })} UTC`;
}

/** The day-month for an axis endpoint: "10 Aug" / "10 Ağu". The month is a
 *  word, so it follows the locale; the day stays the UTC day. `en` maps to
 *  `en-GB` for day-before-month order. */
function formatDay(epochMs: number, locale: Locale): string {
  return new Date(epochMs).toLocaleDateString(locale === "en" ? "en-GB" : locale, {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });
}

/** The UTC calendar day, so the axis can tell "different day" from "earlier
 *  today" — a bare clock time reads as going backward once the window
 *  crosses midnight. */
function utcDateKey(epochMs: number): string {
  return new Date(epochMs).toISOString().slice(0, 10);
}


/** Keeps a dot's tooltip inside the track instead of centering blindly:
 *  a dot near either edge anchors the tooltip to that same edge, so the
 *  tooltip extends inward rather than off the side of a narrow container. */
function tooltipAlignmentClass(position: number): string {
  if (position >= 80) return "right-0 left-auto translate-x-0";
  if (position <= 20) return "left-0 translate-x-0";
  return "left-1/2 -translate-x-1/2";
}

/** An event's partner on the other end of its span: an incident's
 *  resolution, an alert's resolution. */
function partnerId(event: TimelineEvent): string | null {
  const [kind, ...rest] = event.id.split(":");
  const id = rest.join(":");
  if (kind === "incident-opened") return `incident-resolved:${id}`;
  if (kind === "alert-firing") return `alert-resolved:${id}`;
  return null;
}

const SPAN_FILL: Record<string, string> = {
  critical: "bg-critical/25 border-critical/50",
  warning: "bg-warning/25 border-warning/50",
  info: "bg-info/20 border-info/45",
  success: "bg-healthy/20 border-healthy/45",
};

export function OperationalTimeline({
  lanes,
  window: frame,
}: {
  lanes: TimelineLane[];
  /** A fixed window (epoch ms) to draw against — the Overview's last 24
   *  hours. Without it the axis spans the events themselves. */
  window?: { start: number; end: number };
}) {
  const t = useT("commandCenter");
  const fmt = useFormat();
  const [tableOpen, setTableOpen] = useState(false);
  const tableId = useId();
  const allEvents = lanes.flatMap((lane) => lane.events);

  /** An event's name: from its kind and subject when the view-model carried
   *  them, otherwise the label it was given. */
  const eventLabel = (event: TimelineEvent): string =>
    event.subject === undefined
      ? event.label
      : t(`timeline.event.${event.kind}`, {
          subject: event.subject,
          revision: event.revision ?? t("timeline.unknownRevision"),
        });
  const laneLabel = (lane: TimelineLane): string => t.dyn("timeline.lane", lane.key, lane.label);

  /** An axis endpoint: a bare clock time within one day, or a dated one once
   *  the window's start and end fall on different UTC days. */
  const axisPoint = (epochMs: number, includeDate: boolean): string => {
    const time = formatClock(new Date(epochMs).toISOString());
    return includeDate ? t("timeline.axisPoint", { day: formatDay(epochMs, fmt.locale), time }) : time;
  };

  const times = allEvents
    .map((event) => new Date(event.at).getTime())
    .filter((value) => !Number.isNaN(value));
  // A fixed window still widens to an event older than its start (an
  // incident opened two days ago and still open), so nothing is clipped.
  const min = times.length ? (frame ? Math.min(frame.start, ...times) : Math.min(...times)) : null;
  const max = times.length ? (frame ? Math.max(frame.end, ...times) : Math.max(...times)) : null;
  const span = min !== null && max !== null && max > min ? max - min : null;

  function positionOf(at: string | number): number {
    const value = typeof at === "number" ? at : new Date(at).getTime();
    if (min === null || span === null || Number.isNaN(value)) return 50;
    return ((value - min) / span) * 100;
  }

  const axisSpansMultipleDays = min !== null && max !== null && utcDateKey(min) !== utcDateKey(max);
  // Hour gridlines: every 4 hours on a day-long window, every hour on a
  // short one; never more than seven.
  const ticks: number[] = [];
  if (min !== null && max !== null && span !== null) {
    const hours = span / 3_600_000;
    const step = (hours > 12 ? 4 : hours > 4 ? 2 : 1) * 3_600_000;
    for (let at = Math.ceil(min / step) * step; at < max && ticks.length < 7; at += step) {
      if (at > min) ticks.push(at);
    }
  }

  const byId = new Map(allEvents.map((event) => [event.id, event]));
  const grid = (
    <span aria-hidden className="pointer-events-none absolute inset-0">
      {ticks.map((at) => (
        <span key={at} className="absolute inset-y-1 w-px bg-border" style={{ left: `${positionOf(at)}%` }} />
      ))}
      {frame ? <span className="absolute inset-y-0 right-0 w-px bg-brand-accent/60" /> : null}
    </span>
  );

  if (allEvents.length === 0) {
    // Nothing to plot: one compact strip that still names every lane and
    // which ones Tracesis cannot see, rather than four empty tracks.
    return (
      <div data-testid="operational-timeline" className="flex flex-col gap-3">
        <p className="flex items-center gap-2 text-caption text-ink-secondary">
          <span aria-hidden className="h-2 w-2 rounded-full bg-healthy" />
          {frame ? t("timeline.quiet") : t("timeline.emptyWindow")}
        </p>
        <ul className="flex flex-wrap gap-2">
          {lanes.map((lane) => {
            const Icon = laneIcon(lane.key);
            return lane.historyAvailable ? (
              <li key={lane.key} className="inline-flex items-center gap-2 rounded-full border border-border bg-surface-2 px-3 py-1.5 text-micro text-ink-secondary">
                <Icon aria-hidden className="h-3.5 w-3.5" />
                {laneLabel(lane)}
                <span data-tabular className="font-semibold text-ink">0</span>
              </li>
            ) : (
              <li
                key={lane.key}
                data-testid={`lane-unavailable-${lane.key}`}
                title={t("timeline.historyUnavailable")}
                className="inline-flex max-w-full items-center gap-2 rounded-full border border-dashed border-border px-3 py-1.5 text-micro text-ink-muted"
              >
                <Icon aria-hidden className="h-3.5 w-3.5 shrink-0" />
                <span className="truncate">{t("timeline.historyUnavailable")}</span>
              </li>
            );
          })}
        </ul>
      </div>
    );
  }

  return (
    <div data-testid="operational-timeline">
      {min !== null && max !== null ? (
        <div className="mb-2 flex flex-col gap-1 sm:flex-row sm:items-end sm:gap-3">
          <span className="sm:w-44 sm:shrink-0" aria-hidden />
          <div className="relative flex-1">
            {ticks.length ? (
              <div aria-hidden className="relative mb-1 hidden h-4 sm:block">
                {ticks.map((at) => (
                  <span
                    key={at}
                    className="absolute -translate-x-1/2 font-mono text-[10px] text-ink-muted"
                    style={{ left: `${positionOf(at)}%` }}
                  >
                    {new Date(at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" })}
                  </span>
                ))}
              </div>
            ) : null}
            <div className="flex items-center justify-between text-micro text-ink-muted" data-testid="timeline-axis">
              <span>{axisPoint(min, axisSpansMultipleDays)}</span>
              <span>{frame && max === frame.end ? `${axisPoint(max, axisSpansMultipleDays)} · ${t("timeline.now")}` : axisPoint(max, axisSpansMultipleDays)}</span>
            </div>
          </div>
        </div>
      ) : null}
      <div className="space-y-2">
        {lanes.map((lane) => {
          const Icon = laneIcon(lane.key);
          const deploys = lane.key.includes("deploy");
          return (
            <div
              key={lane.key}
              className="flex flex-col gap-2 rounded-xl border border-border bg-surface-2/50 p-2.5 sm:flex-row sm:items-center sm:gap-3"
            >
              <span className="flex items-center gap-3 sm:w-44 sm:shrink-0">
                <span
                  aria-hidden
                  className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface text-ink-secondary"
                >
                  <Icon className="h-4 w-4" />
                </span>
                <span className="min-w-0 truncate text-caption font-medium text-ink" title={laneLabel(lane)}>
                  {laneLabel(lane)}
                </span>
                <span
                  data-tabular
                  className="ml-auto rounded-full bg-surface px-2 py-0.5 text-micro font-semibold text-ink-secondary"
                >
                  {lane.historyAvailable ? lane.events.length : "—"}
                </span>
              </span>
              {!lane.historyAvailable ? (
                <span
                  className="block h-9 min-w-0 flex-1 truncate rounded-lg border border-dashed border-border px-4 text-micro leading-[2.125rem] text-ink-muted"
                  title={t("timeline.historyUnavailable")}
                  data-testid={`lane-unavailable-${lane.key}`}
                >
                  {t("timeline.historyUnavailable")}
                </span>
              ) : lane.events.length === 0 ? (
                <span className="relative flex h-9 min-w-0 flex-1 items-center overflow-hidden rounded-lg bg-surface px-4 text-micro text-ink-muted">
                  {grid}
                  <span
                    aria-hidden
                    className="absolute inset-x-4 top-1/2 h-px -translate-y-1/2 bg-[repeating-linear-gradient(90deg,var(--border-subtle)_0_6px,transparent_6px_12px)]"
                  />
                  <span className="relative truncate bg-surface pr-3">{t("timeline.emptyWindow")}</span>
                </span>
              ) : (
                <div className="relative h-9 flex-1 rounded-lg bg-surface">
                  {grid}
                  {/* Spans first, under the markers: open → resolved, or
                      open → now when it is still open. */}
                  {lane.events.map((event) => {
                    const partner = partnerId(event);
                    if (!partner) return null;
                    const end = byId.get(partner);
                    if (!end && !frame) return null;
                    const from = positionOf(event.at);
                    const to = end ? positionOf(end.at) : 100;
                    return (
                      <span
                        key={`span:${event.id}`}
                        aria-hidden
                        className={`absolute top-1/2 h-2.5 -translate-y-1/2 rounded-full border ${SPAN_FILL[event.tone] ?? SPAN_FILL.info} ${
                          end ? "" : "bg-[repeating-linear-gradient(135deg,transparent_0_4px,var(--surface-3)_4px_7px)]"
                        }`}
                        style={{ left: `${from}%`, width: `${Math.max(0.8, to - from)}%` }}
                      />
                    );
                  })}
                  {lane.events.map((event) => {
                    const spec = toneSpec(event.tone);
                    const position = positionOf(event.at);
                    return (
                      <span
                        key={event.id}
                        className="group absolute top-1/2 -translate-x-1/2 -translate-y-1/2"
                        style={{ left: `clamp(0.75rem, ${position}%, calc(100% - 0.75rem))` }}
                      >
                        {deploys ? (
                          <span aria-hidden className={`absolute left-1/2 h-7 w-px -translate-x-1/2 -translate-y-1/2 top-1/2 ${spec.dot} opacity-60`} />
                        ) : null}
                        <Link
                          href={event.href}
                          aria-label={t("timeline.eventAt", { event: eventLabel(event), time: formatClock(event.at) })}
                          className={`relative block ring-4 ring-surface transition-transform hover:scale-125 focus-visible:scale-125 ${spec.dot} ${
                            deploys ? "h-3 w-3 rotate-45 rounded-[2px]" : "h-3.5 w-3.5 rounded-full"
                          }`}
                        />
                        {/* Visible on hover AND keyboard focus; anchored to the
                            near edge so it never runs off a narrow track. */}
                        <span
                          role="tooltip"
                          className={`pointer-events-none absolute bottom-full z-10 mb-2 max-w-[12rem] whitespace-normal rounded-control bg-ink px-2 py-1 text-micro text-ink-inverse opacity-0 shadow-overlay transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 ${tooltipAlignmentClass(position)}`}
                        >
                          {t("timeline.eventTooltip", { event: eventLabel(event), time: formatClock(event.at) })}
                        </span>
                      </span>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-micro text-ink-muted">
        <span className="inline-flex items-center gap-1.5">
          <span aria-hidden className="h-2 w-5 rounded-full border border-critical/50 bg-critical/25" />
          {t("timeline.legend.span")}
        </span>
        {frame ? (
          <span className="inline-flex items-center gap-1.5">
            <span aria-hidden className="h-2 w-5 rounded-full border border-warning/50 bg-[repeating-linear-gradient(135deg,transparent_0_4px,var(--surface-3)_4px_7px)]" />
            {t("timeline.legend.ongoing")}
          </span>
        ) : null}
        <span className="inline-flex items-center gap-1.5">
          <span aria-hidden className="h-2.5 w-2.5 rotate-45 rounded-[2px] bg-healthy" />
          {t("timeline.legend.deploy")}
        </span>
      </div>

      <details
        className="mt-4 border-t border-border pt-3"
        open={tableOpen}
        onToggle={(event) => setTableOpen(event.currentTarget.open)}
      >
        <summary className="cursor-pointer text-caption font-medium text-brand">
          {t("timeline.viewAsTable", { count: allEvents.length })}
        </summary>
        <div className="mt-2 overflow-x-auto">
          <table className="w-full text-caption" id={tableId}>
            <thead>
              <tr className="border-b border-border text-left text-micro uppercase tracking-wide text-ink-muted">
                <th className="py-1.5 pr-3 font-medium">{t("timeline.column.lane")}</th>
                <th className="py-1.5 pr-3 font-medium">{t("timeline.column.event")}</th>
                <th className="py-1.5 font-medium">{t("timeline.column.when")}</th>
              </tr>
            </thead>
            <tbody>
              {lanes.flatMap((lane) =>
                lane.events.map((event) => (
                  <tr key={event.id} className="border-b border-border last:border-0">
                    <td className="py-1.5 pr-3 text-ink-secondary">{laneLabel(lane)}</td>
                    <td className="py-1.5 pr-3">
                      <Link href={event.href} className="text-ink hover:underline">
                        {eventLabel(event)}
                      </Link>
                    </td>
                    <td className="py-1.5 text-ink-muted">{formatClock(event.at)}</td>
                  </tr>
                )),
              )}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
