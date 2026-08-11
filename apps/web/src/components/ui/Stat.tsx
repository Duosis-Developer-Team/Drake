"use client";

/**
 * Stat — one measured number, with everything needed to trust it.
 *
 * A KPI tile is where fabricated data usually enters a dashboard, so this one
 * is built to make that hard:
 *
 *   A delta renders only when the caller supplies a real comparison period,
 *   and the period is printed next to it. There is no way to show "+12%"
 *   without saying what it is 12% of, and no arrow appears without one.
 *
 *   `value === null` is not zero and not an empty tile: it renders the em
 *   dash plus the reason it is missing.
 *
 *   The threshold tone comes from thresholds the caller was given. With none,
 *   the tone is neutral — never green.
 */

import { ArrowDownRight, ArrowRight, ArrowUpRight } from "lucide-react";

import { formatUnit } from "@/lib/design/format";
import type { StatusTone, Thresholds } from "@/lib/design/status";
import { thresholdLabel, toneForThreshold, toneSpec } from "@/lib/design/status";

export interface Comparison {
  /** Same measurement over the previous window of equal length. */
  previous: number;
  /** What that window was, in words: "previous 24h". Always shown. */
  periodLabel: string;
  /** Which direction is good. Omit when the metric has no polarity. */
  goodDirection?: "up" | "down";
}

export function Stat({
  label,
  value,
  unit,
  missingReason,
  thresholds,
  comparison,
  tone: explicitTone,
  detail,
  href,
  size = "default",
  bare = false,
}: {
  label: React.ReactNode;
  value: number | null | undefined;
  unit: string;
  /** Why the value is absent. Required reading when it is. */
  missingReason?: string;
  thresholds?: Thresholds | null;
  comparison?: Comparison | null;
  /** Overrides the threshold tone when the API already decided the state. */
  tone?: StatusTone;
  detail?: React.ReactNode;
  href?: string;
  size?: "default" | "large";
  /** Drop the frame — for a Stat that is already inside a panel, where the
   *  extra border reads as a box inside a box. */
  bare?: boolean;
}) {
  const tone = explicitTone ?? toneForThreshold(value, thresholds);
  const spec = toneSpec(tone);
  const Icon = spec.icon;
  const missing = value === null || value === undefined || Number.isNaN(value);

  const delta =
    !missing && comparison && comparison.previous !== 0
      ? ((value - comparison.previous) / Math.abs(comparison.previous)) * 100
      : null;
  const DeltaIcon =
    delta === null ? ArrowRight : delta > 0 ? ArrowUpRight : delta < 0 ? ArrowDownRight : ArrowRight;
  const deltaTone =
    delta === null || delta === 0 || !comparison?.goodDirection
      ? "text-ink-muted"
      : (delta > 0) === (comparison.goodDirection === "up")
        ? "text-healthy"
        : "text-critical";

  const body = (
    <>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-caption font-medium text-ink-secondary">{label}</span>
        {thresholds || explicitTone ? (
          <Icon aria-hidden className={`h-4 w-4 shrink-0 ${spec.text}`} />
        ) : null}
      </div>
      <div className="mt-1 flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span
          data-tabular
          className={`font-semibold text-ink ${
            size === "large" ? "text-metric" : "text-title"
          } ${missing ? "text-ink-muted" : ""}`}
        >
          {formatUnit(value, unit)}
        </span>
        {delta !== null && comparison ? (
          <span className={`inline-flex items-center gap-0.5 text-caption ${deltaTone}`}>
            <DeltaIcon aria-hidden className="h-3.5 w-3.5" />
            <span data-tabular>{`${delta > 0 ? "+" : ""}${delta.toFixed(1)}%`}</span>
            <span className="text-ink-muted">vs {comparison.periodLabel}</span>
          </span>
        ) : null}
      </div>
      {missing && missingReason ? (
        <p className="mt-1 text-micro text-ink-muted">{missingReason}</p>
      ) : null}
      {!missing && (thresholds || explicitTone) ? (
        <p className={`mt-1 text-micro ${spec.text}`}>
          {explicitTone ? spec.label : thresholdLabel(tone, Boolean(thresholds))}
        </p>
      ) : null}
      {detail ? <div className="mt-1 text-micro text-ink-muted">{detail}</div> : null}
    </>
  );

  const className = bare
    ? "flex min-w-0 flex-col"
    : "flex min-w-0 flex-col rounded-control border border-border bg-surface px-3 py-2.5";

  if (href) {
    return (
      <a
        href={href}
        data-testid="stat"
        className={`${className} transition-colors hover:border-border-strong hover:bg-surface-hover`}
      >
        {body}
      </a>
    );
  }
  return (
    <div data-testid="stat" className={className}>
      {body}
    </div>
  );
}

/**
 * A row of counts that together make a whole.
 *
 * Used instead of a donut wherever the question is "how many of each", which
 * a row of numbers answers faster than any wedge. Zero renders as `0`: a
 * measured zero is information, and blanking it loses that.
 */
export function CountRow({
  items,
  total,
  onSelect,
  selected,
}: {
  items: { key: string; label: string; count: number; tone: StatusTone }[];
  total?: { label: string; count: number };
  onSelect?: (key: string | null) => void;
  selected?: string | null;
}) {
  return (
    <div className="flex flex-wrap items-stretch gap-2" data-testid="count-row">
      {total ? (
        <div className="flex min-w-20 flex-col rounded-control border border-border bg-surface-2 px-3 py-1.5">
          <span data-tabular className="text-title font-semibold text-ink">
            {total.count}
          </span>
          <span className="text-micro text-ink-muted">{total.label}</span>
        </div>
      ) : null}
      {items.map((item) => {
        const spec = toneSpec(item.tone);
        const isSelected = selected === item.key;
        const inner = (
          <>
            <span data-tabular className="text-title font-semibold text-ink">
              {item.count}
            </span>
            <span className="mt-0.5 inline-flex items-center gap-1.5 text-micro text-ink-secondary">
              <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${spec.dot}`} />
              {item.label}
            </span>
          </>
        );
        const base = `flex min-w-20 flex-col rounded-control border px-3 py-1.5 text-left ${
          isSelected ? "border-brand bg-surface-selected" : "border-border bg-surface"
        }`;
        return onSelect ? (
          <button
            key={item.key}
            type="button"
            aria-pressed={isSelected}
            onClick={() => onSelect(isSelected ? null : item.key)}
            className={`${base} transition-colors hover:bg-surface-hover`}
          >
            {inner}
          </button>
        ) : (
          <div key={item.key} className={base}>
            {inner}
          </div>
        );
      })}
    </div>
  );
}
