"use client";

/**
 * Presentational building blocks for the cluster and inventory screens.
 *
 * Nothing in here fetches or derives facts — every value arrives as a prop
 * from the page, which owns the API calls. These exist so the four cluster
 * screens share one visual vocabulary with the Command Center: icon bubbles,
 * big tabular numbers, pill chips and bars instead of printed counts.
 */

import type { LucideIcon } from "lucide-react";

import { LoadGate, type Loadable } from "@/components/catalog/primitives";
import { Panel } from "@/components/ui/Panel";
import type { StatusTone } from "@/lib/design/status";
import { toneSpec } from "@/lib/design/status";

/** A round icon holder. Tinted by tone when the icon carries a state. */
export function IconBubble({
  icon: Icon,
  tone,
  size = "default",
}: {
  icon: LucideIcon;
  tone?: StatusTone | null;
  size?: "default" | "large" | "small";
}) {
  const box = size === "large" ? "h-12 w-12" : size === "small" ? "h-8 w-8" : "h-10 w-10";
  const glyph = size === "large" ? "h-5 w-5" : size === "small" ? "h-3.5 w-3.5" : "h-[1.125rem] w-[1.125rem]";
  return (
    <span
      aria-hidden
      className={`flex shrink-0 items-center justify-center rounded-full ${box} ${
        tone ? toneSpec(tone).chip : "border border-border bg-surface-2 text-ink-secondary"
      }`}
    >
      <Icon className={glyph} />
    </span>
  );
}

/** The big number every KPI tile leads with. */
export function BigNumber({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <span
      data-tabular
      className={`block text-[2.25rem] leading-none font-semibold tracking-[-0.03em] text-ink ${className}`}
    >
      {children}
    </span>
  );
}

/**
 * One KPI card: icon bubble and label on top, the value in the middle, and
 * a small visual (a share bar, a chip, a breakdown) pinned to the bottom so
 * tiles in a row line their visuals up regardless of the value's shape.
 */
export function StatTile({
  icon,
  tone,
  label,
  value,
  suffix,
  children,
  "data-testid": testId,
}: {
  icon: LucideIcon;
  tone?: StatusTone | null;
  label: string;
  value: React.ReactNode;
  suffix?: React.ReactNode;
  children?: React.ReactNode;
  "data-testid"?: string;
}) {
  return (
    <Panel data-testid={testId} className="h-full">
      <div className="flex items-center gap-3">
        <IconBubble icon={icon} tone={tone} />
        <span className="text-caption font-medium text-ink-secondary">{label}</span>
      </div>
      <div className="flex min-w-0 items-baseline gap-2">
        {typeof value === "string" || typeof value === "number" ? <BigNumber>{value}</BigNumber> : value}
        {suffix ? <span className="truncate text-caption text-ink-muted">{suffix}</span> : null}
      </div>
      {children ? <div className="mt-auto min-w-0">{children}</div> : null}
    </Panel>
  );
}

/** A value as a share of a whole, as a thin bar with the fraction beside it. */
export function ShareBar({
  value,
  total,
  tone,
  label,
}: {
  value: number;
  total: number;
  tone: StatusTone;
  label: string;
}) {
  const share = total > 0 ? value / total : 0;
  return (
    <div className="flex items-center gap-3">
      <span
        role="img"
        aria-label={`${label}: ${value} of ${total}`}
        className="block h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-surface-3"
      >
        <span
          className={`block h-full rounded-full ${value > 0 ? toneSpec(tone).dot : ""}`}
          style={{ width: `${share * 100}%` }}
        />
      </span>
      <span data-tabular className="shrink-0 text-micro text-ink-muted">
        {total > 0 ? `${Math.round(share * 100)}%` : "—"}
      </span>
    </div>
  );
}

export interface Segment {
  key: string;
  label: string;
  value: number;
  tone: StatusTone;
}

/**
 * A segmented bar with a pill legend. Zero buckets stay in the legend (dimmed)
 * so an "unknown: 0" is still visibly a bucket, not an omission.
 */
export function SegmentBar({
  segments,
  label,
  height = "h-2.5",
  legend = true,
}: {
  segments: Segment[];
  label: string;
  height?: string;
  legend?: boolean;
}) {
  const total = segments.reduce((sum, segment) => sum + segment.value, 0);
  const drawn = segments.filter((segment) => segment.value > 0);
  return (
    <div className="min-w-0">
      <span
        role="img"
        aria-label={`${label}: ${
          drawn.length > 0 ? drawn.map((s) => `${s.label} ${s.value}`).join(", ") : "nothing recorded"
        }`}
        className={`flex w-full gap-1 overflow-hidden rounded-full bg-surface-3 ${height}`}
      >
        {drawn.map((segment) => (
          <span
            key={segment.key}
            className={`block h-full rounded-full ${toneSpec(segment.tone).dot}`}
            style={{ width: `${(segment.value / total) * 100}%` }}
          />
        ))}
      </span>
      {legend ? (
        <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5">
          {segments.map((segment) => (
            <li
              key={segment.key}
              className={`flex items-center gap-1.5 text-micro ${
                segment.value === 0 ? "text-ink-muted" : "text-ink-secondary"
              }`}
            >
              <span
                aria-hidden
                className={`h-2 w-2 shrink-0 rounded-full ${toneSpec(segment.tone).dot} ${
                  segment.value === 0 ? "opacity-35" : ""
                }`}
              />
              {/* One text node on purpose: "1 stale", never a bare "Stale"
                  that reads like a status badge of its own. */}
              <span data-tabular>{`${segment.value} ${segment.label.toLowerCase()}`}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/**
 * A designed absence: icon bubble, a title, one line, an optional action.
 * Carries the same `state-*` test ids and `role="status"` as the shared state
 * primitives, because it answers the same question.
 */
export function StateCard({
  icon,
  tone,
  title,
  description,
  action,
  testId,
  compact = false,
}: {
  icon: LucideIcon;
  tone?: StatusTone | null;
  title: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
  testId: string;
  compact?: boolean;
}) {
  return (
    <div
      data-testid={testId}
      role="status"
      className={`flex flex-col items-center text-center ${compact ? "gap-2 px-6 py-6" : "gap-3 px-7 py-12"}`}
    >
      <IconBubble icon={icon} tone={tone} size={compact ? "default" : "large"} />
      <div className="max-w-md">
        <p className="text-body font-semibold text-ink">{title}</p>
        {description ? <p className="mt-1 text-caption text-ink-muted">{description}</p> : null}
      </div>
      {action ? <div className="mt-1">{action}</div> : null}
    </div>
  );
}

/** A clean two-column definition grid: small muted label above each value. */
export function DefinitionGrid({
  items,
  columns = 2,
}: {
  items: { label: string; value: React.ReactNode }[];
  columns?: 1 | 2;
}) {
  return (
    <dl className={`grid grid-cols-1 gap-x-8 gap-y-5 ${columns === 2 ? "sm:grid-cols-2" : ""}`}>
      {items.map((item) => (
        <div key={item.label} className="min-w-0">
          <dt className="text-micro tracking-[0.08em] text-ink-muted uppercase">{item.label}</dt>
          <dd className="mt-1.5 min-w-0 text-body break-words text-ink">{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** A pill-shaped link, primary (filled) or quiet (outlined). */
export function PillLink({
  href,
  children,
  icon: Icon,
  variant = "quiet",
  LinkComponent,
}: {
  href: string;
  children: React.ReactNode;
  icon?: LucideIcon;
  variant?: "primary" | "quiet";
  LinkComponent: React.ComponentType<{ href: string; className?: string; children: React.ReactNode }>;
}) {
  return (
    <LinkComponent
      href={href}
      className={`inline-flex items-center gap-2 rounded-full px-4 py-2 text-caption font-medium transition-colors ${
        variant === "primary"
          ? "bg-ink text-ink-inverse hover:opacity-90"
          : "border border-border bg-surface text-ink hover:bg-surface-hover"
      }`}
    >
      {Icon ? <Icon aria-hidden className="h-4 w-4" /> : null}
      {children}
    </LinkComponent>
  );
}

/**
 * `LoadGate`, with the not-found answer drawn as a designed state instead of
 * a bare sentence. Loading and error still go through the shared gate.
 */
export function ScreenGate<T>({
  value,
  retry,
  notFound,
  children,
}: {
  value: Loadable<T>;
  retry: () => void;
  notFound: React.ReactNode;
  children: (data: T) => React.ReactNode;
}) {
  if (value.state === "error" && value.notFound) {
    return <Panel flush>{notFound}</Panel>;
  }
  return (
    <LoadGate value={value} retry={retry}>
      {children}
    </LoadGate>
  );
}
