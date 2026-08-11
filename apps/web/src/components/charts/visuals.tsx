"use client";

/**
 * The visual primitives — the ones that turn a number into something you read
 * at a glance instead of parsing.
 *
 * Deliberately hand-drawn SVG rather than ECharts. These appear on nearly
 * every screen, including ones that draw no time series at all, and routing
 * them through the chart engine would pull ~250 kB into a route whose only
 * "chart" is a 40px ring. They are arcs and rectangles; the arithmetic is
 * twenty lines and it costs nothing.
 *
 * Every one of them keeps the same rules the rest of the product follows:
 *
 *   `null` is not zero. A missing measurement draws an empty track and says
 *   why — it never renders as a full green ring at 0%.
 *
 *   Thresholds come from the caller. Nothing here invents a limit, and with
 *   no thresholds the fill is neutral rather than "good".
 *
 *   Colour is never alone. Every gauge, ring and donut carries its number,
 *   its unit and a legend or label beside it.
 */

import { formatUnit, MISSING } from "@/lib/design/format";
import type { StatusTone, Thresholds } from "@/lib/design/status";
import { toneForThreshold, toneSpec } from "@/lib/design/status";

/** A point on a circle, in SVG coordinates, from an angle in degrees. */
function polar(cx: number, cy: number, radius: number, degrees: number) {
  const radians = (degrees * Math.PI) / 180;
  return { x: cx + radius * Math.cos(radians), y: cy + radius * Math.sin(radians) };
}

/** Arc path between two angles. 180° is the left end, 360° the right. */
function arc(cx: number, cy: number, radius: number, from: number, to: number): string {
  const start = polar(cx, cy, radius, from);
  const end = polar(cx, cy, radius, to);
  const large = Math.abs(to - from) > 180 ? 1 : 0;
  return `M ${start.x} ${start.y} A ${radius} ${radius} 0 ${large} 1 ${end.x} ${end.y}`;
}

/**
 * A half-circle gauge.
 *
 * For a bounded ratio that has a limit worth seeing — utilisation against
 * capacity, an error budget, a fill level. The threshold bands are drawn on
 * the track, so "how close am I" is answerable without reading the number,
 * and the number is there anyway.
 *
 * NOT for an unbounded quantity: a gauge implies a maximum, and a request
 * rate has none. Those get a stat and a sparkline.
 */
export function Gauge({
  value,
  unit = "percent",
  label,
  caption,
  thresholds,
  tone: explicitTone,
  missingReason,
  size = "default",
}: {
  /** 0–100 for `percent`, 0–1 for `ratio`. Null renders as an empty track. */
  value: number | null | undefined;
  unit?: string;
  label: string;
  caption?: React.ReactNode;
  thresholds?: Thresholds | null;
  tone?: StatusTone;
  missingReason?: string;
  size?: "compact" | "default";
}) {
  const width = size === "compact" ? 132 : 168;
  const height = size === "compact" ? 78 : 96;
  const cx = width / 2;
  const cy = height - (size === "compact" ? 10 : 14);
  const radius = cx - (size === "compact" ? 12 : 16);
  const track = size === "compact" ? 8 : 10;

  const missing = value === null || value === undefined || Number.isNaN(value);
  // Both units normalise to a 0–1 fraction of the sweep.
  const fraction = missing ? 0 : Math.max(0, Math.min(1, unit === "ratio" ? value : value / 100));
  const tone = explicitTone ?? (missing ? "unknown" : toneForThreshold(value, thresholds));
  const spec = toneSpec(tone);

  // Threshold bands, as fractions of the sweep. Only drawn when the caller
  // supplied real limits — an invented band is an invented promise.
  const bands = thresholds
    ? (() => {
        const scale = (limit: number) =>
          Math.max(0, Math.min(1, unit === "ratio" ? limit : limit / 100));
        const warn = scale(thresholds.warn);
        const critical = scale(thresholds.critical);
        return thresholds.direction === "above"
          ? [
              { from: 0, to: warn, token: "--status-success" },
              { from: warn, to: critical, token: "--status-warning" },
              { from: critical, to: 1, token: "--status-critical" },
            ]
          : [
              { from: 0, to: critical, token: "--status-critical" },
              { from: critical, to: warn, token: "--status-warning" },
              { from: warn, to: 1, token: "--status-success" },
            ];
      })()
    : null;

  const angle = (f: number) => 180 + f * 180;

  return (
    <figure className="flex min-w-0 flex-col items-center" data-testid="gauge">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width={width}
        height={height}
        role="img"
        aria-label={`${label}: ${missing ? "not measured" : formatUnit(value, unit)}`}
        className="overflow-visible"
      >
        {/* The empty track. Always drawn, so a missing value has a shape. */}
        <path
          d={arc(cx, cy, radius, 180, 360)}
          fill="none"
          stroke="var(--surface-3)"
          strokeWidth={track}
          strokeLinecap="round"
        />

        {/* Threshold zones, thin, outside the value arc. */}
        {bands?.map((band) => (
          <path
            key={band.token + band.from}
            d={arc(cx, cy, radius + track / 2 + 3, angle(band.from), angle(band.to))}
            fill="none"
            stroke={`var(${band.token})`}
            strokeWidth={2.5}
            opacity={0.55}
          />
        ))}

        {/* The value. */}
        {!missing && fraction > 0 ? (
          <path
            d={arc(cx, cy, radius, 180, angle(fraction))}
            fill="none"
            stroke={`var(${spec.token})`}
            strokeWidth={track}
            strokeLinecap="round"
            className="transition-[d] duration-[var(--duration-surface)]"
          />
        ) : null}

        <text
          x={cx}
          y={cy - (size === "compact" ? 6 : 10)}
          textAnchor="middle"
          className={`fill-ink font-semibold ${size === "compact" ? "text-[15px]" : "text-[19px]"}`}
          style={{ fontVariantNumeric: "tabular-nums" }}
        >
          {missing ? MISSING : formatUnit(value, unit)}
        </text>
      </svg>
      <figcaption className="mt-0.5 text-center">
        <span className="block text-caption font-medium text-ink-secondary">{label}</span>
        {missing && missingReason ? (
          <span className="block text-micro text-ink-muted">{missingReason}</span>
        ) : caption ? (
          <span className="block text-micro text-ink-muted">{caption}</span>
        ) : null}
      </figcaption>
    </figure>
  );
}

export interface Slice {
  name: string;
  value: number;
  tone?: StatusTone;
  /** Explicit colour token when the slice is not a status. */
  token?: string;
}

/**
 * A donut, for composition.
 *
 * Only where the parts genuinely make a whole and there are few of them —
 * health across four buckets, protection across five. A long-tailed
 * distribution goes to `SortedBarChart` instead: nobody can rank fourteen
 * wedges, and the bar ranks them for you.
 *
 * The centre carries the total, because "how many altogether" is the question
 * a reader asks immediately after "how are they split".
 */
export function Donut({
  slices,
  label,
  centerLabel,
  size = 132,
  thickness = 14,
  legend = true,
  emptyMessage = "Nothing to break down — every bucket is zero.",
}: {
  slices: Slice[];
  label: string;
  centerLabel?: string;
  size?: number;
  thickness?: number;
  legend?: boolean;
  emptyMessage?: string;
}) {
  const total = slices.reduce((sum, slice) => sum + slice.value, 0);
  const radius = (size - thickness) / 2;
  const circumference = 2 * Math.PI * radius;

  if (total === 0) {
    return (
      <p className="text-caption text-ink-muted" data-testid="donut-empty">
        {emptyMessage}
      </p>
    );
  }

  let offset = 0;
  const drawn = slices.filter((slice) => slice.value > 0);

  return (
    <figure className="flex min-w-0 flex-wrap items-center gap-4" data-testid="donut">
      <svg
        viewBox={`0 0 ${size} ${size}`}
        width={size}
        height={size}
        role="img"
        aria-label={`${label}: ${drawn.map((s) => `${s.name} ${s.value}`).join(", ")}`}
        className="shrink-0 -rotate-90"
      >
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="var(--surface-3)"
          strokeWidth={thickness}
        />
        {drawn.map((slice) => {
          const length = (slice.value / total) * circumference;
          // A 2px surface gap so adjacent fills never blend into one another.
          const gap = drawn.length > 1 ? Math.min(2, length / 2) : 0;
          const dash = `${Math.max(0, length - gap)} ${circumference - length + gap}`;
          const element = (
            <circle
              key={slice.name}
              cx={size / 2}
              cy={size / 2}
              r={radius}
              fill="none"
              stroke={
                slice.token
                  ? `var(${slice.token})`
                  : slice.tone
                    ? `var(${toneSpec(slice.tone).token})`
                    : "var(--series-1)"
              }
              strokeWidth={thickness}
              strokeDasharray={dash}
              strokeDashoffset={-offset}
            />
          );
          offset += length;
          return element;
        })}
      </svg>

      <figcaption className="min-w-0">
        {centerLabel ? (
          <span className="block text-title font-semibold text-ink" data-tabular>
            {centerLabel}
          </span>
        ) : (
          <span className="block text-title font-semibold text-ink" data-tabular>
            {total}
          </span>
        )}
        {legend ? (
          <ul className="mt-1 space-y-0.5">
            {slices.map((slice) => (
              <li
                key={slice.name}
                className="flex items-center gap-1.5 text-micro text-ink-secondary"
              >
                <span
                  aria-hidden
                  className="h-2 w-2 shrink-0 rounded-full"
                  style={{
                    background: slice.token
                      ? `var(${slice.token})`
                      : slice.tone
                        ? `var(${toneSpec(slice.tone).token})`
                        : "var(--series-1)",
                    opacity: slice.value === 0 ? 0.35 : 1,
                  }}
                />
                <span className="truncate">{slice.name}</span>
                <span data-tabular className="ml-auto pl-2 font-medium text-ink">
                  {slice.value}
                </span>
              </li>
            ))}
          </ul>
        ) : null}
      </figcaption>
    </figure>
  );
}

/**
 * A compact progress ring.
 *
 * The dense-table form of a gauge: small enough for a cell, still readable at
 * a glance because the fill angle carries the value before the digits do.
 */
export function RingProgress({
  value,
  unit = "percent",
  label,
  thresholds,
  tone: explicitTone,
  size = 44,
}: {
  value: number | null | undefined;
  unit?: string;
  label: string;
  thresholds?: Thresholds | null;
  tone?: StatusTone;
  size?: number;
}) {
  const thickness = Math.max(4, size / 10);
  const radius = (size - thickness) / 2;
  const circumference = 2 * Math.PI * radius;
  const missing = value === null || value === undefined || Number.isNaN(value);
  const fraction = missing ? 0 : Math.max(0, Math.min(1, unit === "ratio" ? value : value / 100));
  const tone = explicitTone ?? (missing ? "unknown" : toneForThreshold(value, thresholds));

  return (
    <span className="inline-flex items-center gap-2" data-testid="ring-progress">
      <svg
        viewBox={`0 0 ${size} ${size}`}
        width={size}
        height={size}
        role="img"
        aria-label={`${label}: ${missing ? "not measured" : formatUnit(value, unit)}`}
        className="shrink-0 -rotate-90"
      >
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="var(--surface-3)"
          strokeWidth={thickness}
        />
        {!missing && fraction > 0 ? (
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke={`var(${toneSpec(tone).token})`}
            strokeWidth={thickness}
            strokeLinecap="round"
            strokeDasharray={`${fraction * circumference} ${circumference}`}
          />
        ) : null}
        <text
          x={size / 2}
          y={size / 2}
          textAnchor="middle"
          dominantBaseline="central"
          className="fill-ink text-[10px] font-semibold"
          style={{ fontVariantNumeric: "tabular-nums", transform: "rotate(90deg)", transformOrigin: "center" }}
        >
          {missing ? "—" : `${Math.round(fraction * 100)}`}
        </text>
      </svg>
    </span>
  );
}

/**
 * A numeric cell that carries its own severity.
 *
 * The pattern that turns a wall of numbers into something scannable: the
 * value keeps its exact digits, and the chip behind it says which column to
 * look at first. Without thresholds it is deliberately plain — a coloured
 * chip with no limit behind it is decoration pretending to be information.
 */
export function ValueChip({
  value,
  unit,
  thresholds,
  tone: explicitTone,
  missingLabel = MISSING,
}: {
  value: number | null | undefined;
  unit: string;
  thresholds?: Thresholds | null;
  tone?: StatusTone;
  missingLabel?: string;
}) {
  const missing = value === null || value === undefined || Number.isNaN(value);
  if (missing) {
    return <span className="text-caption text-ink-muted">{missingLabel}</span>;
  }
  const tone = explicitTone ?? toneForThreshold(value, thresholds);
  const spec = toneSpec(tone);
  const plain = !thresholds && !explicitTone;
  return (
    <span
      data-testid="value-chip"
      data-tabular
      className={`inline-flex rounded px-1.5 py-0.5 text-caption font-medium ${
        plain ? "text-ink" : spec.chip
      }`}
    >
      {formatUnit(value, unit)}
    </span>
  );
}

/**
 * Used against free, in one bar, with both sides labelled inside.
 *
 * Better than two numbers and a percentage: the split IS the answer, and the
 * eye gets it before it reads anything.
 */
export function SplitBar({
  used,
  total,
  unit,
  label,
  thresholds,
}: {
  used: number | null;
  total: number | null;
  unit: string;
  label: string;
  thresholds?: Thresholds | null;
}) {
  const known = used !== null && total !== null && total > 0;
  const ratio = known ? Math.min(1, used / total) : 0;
  const percent = ratio * 100;
  const tone = known ? toneForThreshold(percent, thresholds) : "unknown";
  const spec = toneSpec(tone);

  return (
    <div className="min-w-0" data-testid="split-bar">
      <div className="flex items-baseline justify-between gap-2">
        <span className="truncate text-caption text-ink-secondary">{label}</span>
        <span data-tabular className="shrink-0 text-caption font-medium text-ink">
          {known ? `${percent.toFixed(1)}%` : MISSING}
        </span>
      </div>
      <div
        role="img"
        aria-label={
          known
            ? `${label}: ${formatUnit(used, unit)} used of ${formatUnit(total, unit)}`
            : `${label}: not measured`
        }
        className="mt-1 flex h-5 w-full overflow-hidden rounded bg-surface-3"
      >
        {known ? (
          <>
            <span
              className={`flex items-center justify-end px-1.5 text-micro font-medium ${spec.chip}`}
              style={{ width: `${Math.max(percent, 0)}%` }}
            >
              {percent > 22 ? formatUnit(used, unit) : ""}
            </span>
            <span className="flex flex-1 items-center px-1.5 text-micro text-ink-muted">
              {100 - percent > 22 ? `${formatUnit(total - used, unit)} free` : ""}
            </span>
          </>
        ) : (
          <span className="flex flex-1 items-center px-1.5 text-micro text-ink-muted">
            not reported
          </span>
        )}
      </div>
    </div>
  );
}

/**
 * A sparkline.
 *
 * The shape of a series next to the number it belongs to — enough to see
 * "rising", "flat", "spiky" without opening the full chart. A `null` sample
 * breaks the line, exactly as it does at full size: a sparkline that bridges
 * a gap is a sparkline that lies about a scrape that never happened.
 */
export function Sparkline({
  points,
  tone = "info",
  width = 96,
  height = 28,
  label,
}: {
  /** Values in order. `null` is a gap. */
  points: (number | null)[];
  tone?: StatusTone;
  width?: number;
  height?: number;
  label: string;
}) {
  const values = points.filter((point): point is number => point !== null);
  if (values.length < 2) {
    return (
      <span className="text-micro text-ink-muted" data-testid="sparkline-empty">
        no series
      </span>
    );
  }

  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const step = width / Math.max(1, points.length - 1);
  const y = (value: number) => height - 2 - ((value - min) / span) * (height - 4);

  const runs: string[] = [];
  let current: string[] = [];
  points.forEach((point, index) => {
    if (point === null) {
      if (current.length > 1) runs.push(current.join(" "));
      current = [];
      return;
    }
    current.push(`${current.length === 0 ? "M" : "L"}${(index * step).toFixed(1)},${y(point).toFixed(1)}`);
  });
  if (current.length > 1) runs.push(current.join(" "));

  const colour = `var(${toneSpec(tone).token})`;
  const last = [...points].reverse().find((point) => point !== null);

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      role="img"
      aria-label={`${label}: ${values.length} samples, latest ${last}`}
      className="shrink-0 overflow-visible"
      data-testid="sparkline"
    >
      {runs.map((path, index) => (
        <path
          key={index}
          d={path}
          fill="none"
          stroke={colour}
          strokeWidth={1.5}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      ))}
      {last !== undefined && last !== null ? (
        <circle
          cx={width}
          cy={y(last)}
          r={2}
          fill={colour}
        />
      ) : null}
    </svg>
  );
}

/**
 * A row of tone-coloured counters.
 *
 * The bridge between a donut and a table: each bucket keeps its exact count
 * and its colour, in a form dense enough to sit inside a panel header.
 */
export function ToneCounters({
  items,
  size = "default",
}: {
  items: { label: string; count: number; tone: StatusTone }[];
  size?: "compact" | "default";
}) {
  return (
    <div className="flex flex-wrap gap-1.5" data-testid="tone-counters">
      {items.map((item) => {
        const spec = toneSpec(item.tone);
        return (
          <span
            key={item.label}
            className={`inline-flex items-baseline gap-1.5 rounded px-2 py-0.5 ${
              item.count === 0 ? "bg-surface-3 text-ink-muted" : spec.chip
            } ${size === "compact" ? "text-micro" : "text-caption"}`}
          >
            <span data-tabular className="font-semibold">
              {item.count}
            </span>
            {item.label}
          </span>
        );
      })}
    </div>
  );
}
