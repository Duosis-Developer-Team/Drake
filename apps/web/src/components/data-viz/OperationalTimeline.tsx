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
 */

import Link from "next/link";
import { useId, useState } from "react";

import { toneSpec } from "@/lib/design/status";
import type { TimelineLane } from "@/lib/view-models/timeline";

function formatClock(at: string): string {
  const date = new Date(at);
  if (Number.isNaN(date.getTime())) return at;
  return `${date.toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
  })} UTC`;
}

/** The UTC calendar day, so the axis can tell "different day" from "earlier
 *  today" — a bare clock time reads as going backward once the window
 *  crosses midnight. */
function utcDateKey(epochMs: number): string {
  return new Date(epochMs).toISOString().slice(0, 10);
}

/** An axis endpoint: a bare clock time within one day, or a dated one once
 *  the window's start and end fall on different UTC days. */
function formatAxisPoint(epochMs: number, includeDate: boolean): string {
  const iso = new Date(epochMs).toISOString();
  if (!includeDate) return formatClock(iso);
  const day = new Date(epochMs).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });
  return `${day}, ${formatClock(iso)}`;
}

/** Keeps a dot's tooltip inside the track instead of centering blindly:
 *  a dot near either edge anchors the tooltip to that same edge, so the
 *  tooltip extends inward rather than off the side of a narrow container. */
function tooltipAlignmentClass(position: number): string {
  if (position >= 80) return "right-0 left-auto translate-x-0";
  if (position <= 20) return "left-0 translate-x-0";
  return "left-1/2 -translate-x-1/2";
}

export function OperationalTimeline({ lanes }: { lanes: TimelineLane[] }) {
  const [tableOpen, setTableOpen] = useState(false);
  const tableId = useId();
  const allEvents = lanes.flatMap((lane) => lane.events);

  const times = allEvents
    .map((event) => new Date(event.at).getTime())
    .filter((value) => !Number.isNaN(value));
  const min = times.length ? Math.min(...times) : null;
  const max = times.length ? Math.max(...times) : null;
  const span = min !== null && max !== null && max > min ? max - min : null;

  function positionOf(at: string): number {
    const t = new Date(at).getTime();
    if (min === null || span === null || Number.isNaN(t)) return 50;
    return ((t - min) / span) * 100;
  }

  const axisSpansMultipleDays = min !== null && max !== null && utcDateKey(min) !== utcDateKey(max);

  return (
    <div data-testid="operational-timeline">
      {min !== null && max !== null ? (
        <div className="mb-2 flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-3">
          <span className="sm:w-44 sm:shrink-0" aria-hidden />
          <div
            className="flex flex-1 items-center justify-between text-micro text-ink-muted"
            data-testid="timeline-axis"
          >
            <span>{formatAxisPoint(min, axisSpansMultipleDays)}</span>
            <span>{formatAxisPoint(max, axisSpansMultipleDays)}</span>
          </div>
        </div>
      ) : null}
      <div className="space-y-3 sm:space-y-2.5">
        {lanes.map((lane) => (
          <div key={lane.key} className="flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-3">
            <span className="text-caption text-ink-secondary sm:w-44 sm:shrink-0 sm:truncate" title={lane.label}>
              {lane.label}
            </span>
            {!lane.historyAvailable ? (
              <span
                className="flex-1 text-micro text-ink-muted"
                data-testid={`lane-unavailable-${lane.key}`}
              >
                History unavailable — Drake has no state-transition source for this lane yet.
              </span>
            ) : lane.events.length === 0 ? (
              <span className="flex-1 text-micro text-ink-muted">
                No events in the selected window.
              </span>
            ) : (
              <div className="relative h-6 flex-1 rounded-full bg-surface-3">
                {lane.events.map((event) => {
                  const spec = toneSpec(event.tone);
                  const position = positionOf(event.at);
                  return (
                    <span
                      key={event.id}
                      className="group absolute top-1/2 -translate-x-1/2 -translate-y-1/2"
                      style={{ left: `${position}%` }}
                    >
                      <Link
                        href={event.href}
                        aria-label={`${event.label}, ${formatClock(event.at)}`}
                        className={`block h-3 w-3 rounded-full ring-2 ring-surface transition-transform hover:scale-125 focus-visible:scale-125 ${spec.dot}`}
                      />
                      {/* Visible on hover AND keyboard focus — the accessible
                          name above covers screen readers, but a sighted
                          mouse or keyboard user reading the track needs the
                          same "what and when" without guessing from position.
                          A dot near either edge anchors the tooltip to that
                          edge instead of centering it, so it extends inward
                          rather than off the side of a narrow track; a
                          bounded width with normal wrapping is the fallback
                          for whatever that still doesn't fit. */}
                      <span
                        role="tooltip"
                        className={`pointer-events-none absolute bottom-full z-10 mb-1.5 max-w-[12rem] whitespace-normal rounded-control bg-ink px-2 py-1 text-micro text-ink-inverse opacity-0 shadow-overlay transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 ${tooltipAlignmentClass(position)}`}
                      >
                        {event.label} — {formatClock(event.at)}
                      </span>
                    </span>
                  );
                })}
              </div>
            )}
          </div>
        ))}
      </div>

      <details
        className="mt-3 border-t border-border pt-2"
        open={tableOpen}
        onToggle={(event) => setTableOpen(event.currentTarget.open)}
      >
        <summary className="cursor-pointer text-caption font-medium text-brand">
          View as table ({allEvents.length} event{allEvents.length === 1 ? "" : "s"})
        </summary>
        <div className="mt-2 overflow-x-auto">
          <table className="w-full text-caption" id={tableId}>
            <thead>
              <tr className="border-b border-border text-left text-micro uppercase tracking-wide text-ink-muted">
                <th className="py-1.5 pr-3 font-medium">Lane</th>
                <th className="py-1.5 pr-3 font-medium">Event</th>
                <th className="py-1.5 font-medium">When</th>
              </tr>
            </thead>
            <tbody>
              {allEvents.length === 0 ? (
                <tr>
                  <td colSpan={3} className="py-3 text-center text-ink-muted">
                    No events to list.
                  </td>
                </tr>
              ) : (
                lanes.flatMap((lane) =>
                  lane.events.map((event) => (
                    <tr key={event.id} className="border-b border-border last:border-0">
                      <td className="py-1.5 pr-3 text-ink-secondary">{lane.label}</td>
                      <td className="py-1.5 pr-3">
                        <Link href={event.href} className="text-ink hover:underline">
                          {event.label}
                        </Link>
                      </td>
                      <td className="py-1.5 text-ink-muted">{formatClock(event.at)}</td>
                    </tr>
                  )),
                )
              )}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
