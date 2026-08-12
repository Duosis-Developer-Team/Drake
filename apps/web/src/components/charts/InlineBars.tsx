"use client";

/**
 * The bars that are not charts.
 *
 * A composition bar and a capacity bar are a few divs. They live apart from
 * the ECharts components on purpose: importing them used to pull the whole
 * chart engine into the route chunk of every screen that shows a health
 * breakdown — the Command Center and Projects were paying ~250 kB for six
 * coloured rectangles.
 */

import { formatUnit } from "@/lib/design/format";
import type { StatusTone } from "@/lib/design/status";
import { toneSpec } from "@/lib/design/status";
import { SERIES_TOKENS } from "@/lib/design/tokens";

/**
 * One horizontal stacked bar: a composition, in a single row.
 *
 * Used where a donut would be reached for. It reads left-to-right like the
 * rest of the page, stacks into far less vertical space, and puts the counts
 * in a legend where they can be read exactly rather than estimated by angle.
 */
export function CompositionBar({
  segments,
  unit = "count",
  label,
}: {
  segments: { name: string; value: number; tone?: StatusTone; slot?: number }[];
  unit?: string;
  label: string;
}) {
  const total = segments.reduce((sum, entry) => sum + entry.value, 0);
  if (total === 0) {
    return (
      <p className="text-caption text-ink-muted" data-testid="composition-empty">
        Nothing to break down — every bucket is zero.
      </p>
    );
  }
  return (
    <div data-testid="composition-bar">
      <div
        role="img"
        aria-label={`${label}: ${segments
          .filter((entry) => entry.value > 0)
          .map((entry) => `${entry.name} ${entry.value}`)
          .join(", ")}`}
        className="flex h-2.5 w-full gap-0.5 overflow-hidden rounded-full bg-surface-3"
      >
        {segments
          .filter((entry) => entry.value > 0)
          .map((entry, index) => (
            <span
              key={entry.name}
              style={{
                width: `${(entry.value / total) * 100}%`,
                background: entry.tone
                  ? `var(${toneSpec(entry.tone).token})`
                  : `var(${SERIES_TOKENS[(entry.slot ?? index) % SERIES_TOKENS.length]})`,
              }}
            />
          ))}
      </div>
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
        {segments.map((entry, index) => (
          <li
            key={entry.name}
            className="flex items-center gap-1.5 text-micro text-ink-secondary"
          >
            <span
              aria-hidden
              className="h-2 w-2 shrink-0 rounded-full"
              style={{
                background: entry.tone
                  ? `var(${toneSpec(entry.tone).token})`
                  : `var(${SERIES_TOKENS[(entry.slot ?? index) % SERIES_TOKENS.length]})`,
              }}
            />
            {entry.name}
            <span data-tabular className="text-ink">
              {formatUnit(entry.value, unit)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * A single ratio against its capacity.
 *
 * Text first, bar second: the number is the answer and the bar is the
 * context. The threshold marks are drawn on the track so "how close are we"
 * is answerable without reading the axis.
 */
export function CapacityBar({
  label,
  used,
  total,
  unit,
  tone = "neutral",
  thresholdRatios,
}: {
  label: string;
  used: number | null;
  total: number | null;
  unit: string;
  tone?: StatusTone;
  /** Where to draw the warn/critical ticks, as fractions of the total. */
  thresholdRatios?: { warn?: number; critical?: number };
}) {
  const known = used !== null && total !== null && total > 0;
  const ratio = known ? Math.min(1, used / total) : 0;
  const spec = toneSpec(tone);
  return (
    <div data-testid="capacity-bar">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-caption text-ink-secondary">{label}</span>
        <span data-tabular className="text-caption text-ink">
          {known ? (
            <>
              {formatUnit(used, unit)} <span className="text-ink-muted">of {formatUnit(total, unit)}</span>
            </>
          ) : (
            <span className="text-ink-muted">not reported</span>
          )}
        </span>
      </div>
      <div className="relative mt-1 h-2 w-full overflow-hidden rounded-full bg-surface-3">
        {known ? (
          <span
            className={`block h-full rounded-full ${spec.dot}`}
            style={{ width: `${ratio * 100}%` }}
          />
        ) : null}
        {thresholdRatios?.warn ? (
          <span
            aria-hidden
            className="absolute top-0 h-full w-px bg-warning"
            style={{ left: `${thresholdRatios.warn * 100}%` }}
          />
        ) : null}
        {thresholdRatios?.critical ? (
          <span
            aria-hidden
            className="absolute top-0 h-full w-px bg-critical"
            style={{ left: `${thresholdRatios.critical * 100}%` }}
          />
        ) : null}
      </div>
    </div>
  );
}
