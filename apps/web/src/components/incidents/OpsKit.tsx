"use client";

/**
 * Shared presentational kit for the operations screens — incidents, alerts,
 * deployments and objectives.
 *
 * Nothing here fetches, derives or decides: every component renders exactly
 * the numbers and words its caller hands it. What it adds is shape — KPI
 * tiles with an icon bubble and a share bar, pill filters, a composed empty
 * state, a zero-safe breakdown ring and a two-column definition grid — so
 * the four operations screens read as one family, and a quiet estate (all
 * zeros) looks deliberate rather than unfinished.
 */

import { ChevronDown, type LucideIcon } from "lucide-react";
import { useId } from "react";

import { Panel } from "@/components/ui/Panel";
import { toneSpec, type StatusTone } from "@/lib/design/status";
import { useT } from "@/lib/i18n";

/* ------------------------------------------------------------------ */
/* KPI tile                                                            */
/* ------------------------------------------------------------------ */

/**
 * A KPI tile: icon bubble + label, the count at display size, and a share
 * bar underneath. `share` is `null` when there is nothing to divide by — the
 * track then renders empty and muted rather than as an invented 0%.
 */
export function KpiTile({
  label,
  value,
  icon: Icon,
  tone,
  share,
  caption,
  "data-testid": testId,
}: {
  label: string;
  value: React.ReactNode;
  icon: LucideIcon;
  tone: StatusTone;
  /** 0..1, or null when there is no reference total. Omit for no bar. */
  share?: number | null;
  caption?: React.ReactNode;
  "data-testid"?: string;
}) {
  const spec = toneSpec(tone);
  const lit = typeof value === "number" ? value > 0 : true;
  return (
    <Panel flush data-testid={testId} className="h-full gap-5 p-7">
      <div className="flex items-center gap-3">
        <span
          aria-hidden
          className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full ${
            lit ? spec.chip : "border border-border bg-surface-2 text-ink-muted"
          }`}
        >
          <Icon className="h-[1.125rem] w-[1.125rem]" />
        </span>
        <p className="min-w-0 truncate text-caption font-medium text-ink-secondary">{label}</p>
      </div>
      <div>
        <p
          data-tabular
          className={`text-[2.25rem] leading-none font-semibold tracking-[-0.03em] ${
            lit && typeof value === "number" && tone === "critical" ? spec.text : "text-ink"
          }`}
        >
          {value}
        </p>
        {share !== undefined ? (
          <ShareBar share={share} tone={tone} className="mt-4" />
        ) : null}
        {caption ? <p className="mt-2.5 truncate text-micro text-ink-muted">{caption}</p> : null}
      </div>
    </Panel>
  );
}

/** A thin share bar. `null` renders the bare track — nothing measured. */
export function ShareBar({
  share,
  tone,
  className = "",
}: {
  share: number | null;
  tone: StatusTone;
  className?: string;
}) {
  const width = share === null ? 0 : Math.max(0, Math.min(1, share)) * 100;
  return (
    <span aria-hidden className={`block h-1.5 overflow-hidden rounded-full bg-surface-3 ${className}`}>
      <span
        className={`block h-full rounded-full ${toneSpec(tone).dot} transition-[width] duration-500`}
        style={{ width: `${width}%`, minWidth: width > 0 ? 6 : 0 }}
      />
    </span>
  );
}

/* ------------------------------------------------------------------ */
/* Filters                                                             */
/* ------------------------------------------------------------------ */

/**
 * A pill-shaped native select with its label inside the pill. Still a real
 * `<label for>` + `<select>`, so it is announced and queried by its label
 * exactly as the plain control was.
 */
export function PillSelect<T extends string>({
  label,
  value,
  options,
  onChange,
  placeholder,
}: {
  label: string;
  value: T | "";
  options: { value: T; label: string }[];
  onChange: (value: T | "") => void;
  placeholder: string;
}) {
  const id = useId();
  const active = value !== "";
  return (
    <div
      className={`relative inline-flex h-10 min-w-0 items-center rounded-full border pr-9 pl-4 focus-within:ring-2 focus-within:ring-focus shadow-panel transition-colors ${
        active ? "border-border-strong bg-surface-selected" : "border-border bg-surface hover:bg-surface-hover"
      }`}
    >
      <label htmlFor={id} className="shrink-0 pr-1.5 text-caption text-ink-muted">
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value as T | "")}
        className="min-w-0 cursor-pointer appearance-none truncate bg-transparent text-caption font-semibold text-ink focus:outline-none"
      >
        <option value="">{placeholder}</option>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      <ChevronDown
        aria-hidden
        className="pointer-events-none absolute top-1/2 right-3.5 h-4 w-4 -translate-y-1/2 text-ink-muted"
      />
    </div>
  );
}

/** The card-less toolbar row above a list: pills left, a count right. */
export function Toolbar({
  children,
  summary,
  "data-testid": testId,
}: {
  children: React.ReactNode;
  summary?: React.ReactNode;
  "data-testid"?: string;
}) {
  const t = useT("incidents");
  return (
    <div data-testid={testId} className="flex flex-wrap items-center gap-2.5">
      <div role="group" aria-label={t("toolbar.filters")} className="flex flex-wrap items-center gap-2.5">
        {children}
      </div>
      {summary ? (
        <span className="ml-auto text-caption text-ink-muted" data-tabular>
          {summary}
        </span>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Empty state                                                         */
/* ------------------------------------------------------------------ */

/**
 * A composed empty state: a layered icon medallion, a title, one line, and
 * optional chips/actions under it. Carries `data-testid="state-empty"` so it
 * is the same state the shared `EmptyState` renders, just dressed for a
 * full card.
 */
export function EmptyHero({
  icon: Icon,
  title,
  description,
  children,
  className = "",
}: {
  icon: LucideIcon;
  title: string;
  description?: React.ReactNode;
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      data-testid="state-empty"
      role="status"
      className={`flex flex-1 flex-col items-center justify-center px-8 py-14 text-center ${className}`}
    >
      <span
        aria-hidden
        className="relative flex h-24 w-24 items-center justify-center rounded-full bg-surface-2"
      >
        <span className="absolute inset-3 rounded-full border border-dashed border-border-strong/60" />
        <span className="relative flex h-14 w-14 items-center justify-center rounded-full border border-border bg-surface text-ink-secondary shadow-panel">
          <Icon className="h-6 w-6" />
        </span>
      </span>
      <p className="mt-6 text-[1.0625rem] leading-6 font-semibold tracking-[-0.01em] text-ink">
        {title}
      </p>
      {description ? (
        <p className="mt-1.5 max-w-md text-caption text-ink-muted" style={{ textWrap: "balance" }}>
          {description}
        </p>
      ) : null}
      {children ? <div className="mt-5 flex flex-wrap justify-center gap-2">{children}</div> : null}
    </div>
  );
}

/** A small neutral pill — for "Status: firing" style filter echoes. */
export function FactPill({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface-2 px-3 py-1 text-micro font-medium text-ink-secondary">
      {children}
    </span>
  );
}

/** A padded wrapper for loading / error / denied states inside a card. */
export function StatePad({ children }: { children: React.ReactNode }) {
  return <div className="px-7 py-8">{children}</div>;
}

/* ------------------------------------------------------------------ */
/* Breakdown ring                                                      */
/* ------------------------------------------------------------------ */

export interface BreakdownSlice {
  name: string;
  value: number;
  tone: StatusTone;
}

/**
 * A composition ring with a per-bucket legend. Unlike `Donut`, a zero total
 * still draws: the bare track, `0` in the centre, and every bucket listed at
 * zero — "the scope answered and holds nothing" is a real, readable answer.
 */
export function BreakdownRing({
  slices,
  label,
  centerCaption,
  size = 148,
}: {
  slices: BreakdownSlice[];
  label: string;
  centerCaption?: string;
  size?: number;
}) {
  const t = useT("incidents");
  const center = centerCaption ?? t("ring.total");
  const total = slices.reduce((sum, slice) => sum + slice.value, 0);
  const thickness = 14;
  const radius = (size - thickness) / 2;
  const circumference = 2 * Math.PI * radius;
  const drawn = slices.filter((slice) => slice.value > 0);
  let offset = 0;
  return (
    <figure className="flex flex-col items-center gap-6" data-testid="breakdown-ring">
      <div className="relative shrink-0" style={{ width: size, height: size }}>
        <svg
          viewBox={`0 0 ${size} ${size}`}
          width={size}
          height={size}
          role="img"
          aria-label={`${label}: ${slices.map((s) => `${s.name} ${s.value}`).join(", ")}`}
          className="-rotate-90"
        >
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke="var(--color-surface-3)"
            strokeWidth={thickness}
          />
          {drawn.map((slice) => {
            const length = (slice.value / total) * circumference;
            const gap = drawn.length > 1 ? Math.min(5, length / 2) : 0;
            const element = (
              <circle
                key={slice.name}
                cx={size / 2}
                cy={size / 2}
                r={radius}
                fill="none"
                stroke={`var(${toneSpec(slice.tone).token})`} // i18n-ignore
                strokeWidth={thickness}
                strokeDasharray={`${Math.max(0, length - gap)} ${circumference - length + gap}`}
                strokeDashoffset={-offset}
                strokeLinecap={drawn.length > 1 ? "butt" : "round"}
              />
            );
            offset += length;
            return element;
          })}
        </svg>
        <span className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span
            data-tabular
            className="text-[2rem] leading-none font-semibold tracking-[-0.03em] text-ink"
          >
            {total}
          </span>
          <span className="mt-1 text-micro text-ink-muted">{center}</span>
        </span>
      </div>
      <figcaption className="w-full">
        <ul className="space-y-3">
          {slices.map((slice) => {
            const share = total > 0 ? slice.value / total : null;
            return (
              <li key={slice.name}>
                <span className="flex items-center gap-2.5 text-caption">
                  <span
                    aria-hidden
                    className={`h-2.5 w-2.5 shrink-0 rounded-full ${toneSpec(slice.tone).dot} ${
                      slice.value === 0 ? "opacity-35" : ""
                    }`}
                  />
                  <span className="min-w-0 flex-1 truncate text-ink-secondary">{slice.name}</span>
                  <span data-tabular className="font-semibold text-ink">
                    {slice.value}
                  </span>
                  <span aria-hidden data-tabular className="w-9 text-right text-micro text-ink-muted">
                    {share === null ? "—" : `${Math.round(share * 100)}%`}
                  </span>
                </span>
              </li>
            );
          })}
        </ul>
      </figcaption>
    </figure>
  );
}

/* ------------------------------------------------------------------ */
/* Rows, bubbles, definitions                                           */
/* ------------------------------------------------------------------ */

/** The 40px leading bubble on a list row. */
export function RowBubble({ icon: Icon, tone }: { icon: LucideIcon; tone: StatusTone }) {
  return (
    <span
      aria-hidden
      className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full ${toneSpec(tone).chip}`}
    >
      <Icon className="h-[1.125rem] w-[1.125rem]" />
    </span>
  );
}

/** An icon bubble for a card header. */
export function HeaderBubble({ icon: Icon, tone }: { icon: LucideIcon; tone?: StatusTone }) {
  return (
    <span
      aria-hidden
      className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full ${
        tone ? toneSpec(tone).chip : "border border-border bg-surface-2 text-ink-secondary"
      }`}
    >
      <Icon className="h-4 w-4" />
    </span>
  );
}

/** A card title row with a leading icon bubble. */
export function CardTitle({
  icon,
  tone,
  title,
  description,
  actions,
  flush = false,
}: {
  icon: LucideIcon;
  tone?: StatusTone;
  title: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  flush?: boolean;
}) {
  return (
    <div
      className={`flex items-center justify-between gap-4 ${
        flush ? "border-b border-border px-7 pt-6 pb-5" : ""
      }`}
    >
      <div className="flex min-w-0 items-center gap-3">
        <HeaderBubble icon={icon} tone={tone} />
        <div className="min-w-0">
          <h2 className="text-[1.0625rem] leading-6 font-semibold tracking-[-0.01em] text-ink">
            {title}
          </h2>
          {description ? (
            <p className="truncate text-caption text-ink-muted" title={typeof description === "string" ? description : undefined}>
              {description}
            </p>
          ) : null}
        </div>
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  );
}

/** A two-column definition grid — metadata as label over value. */
export function DefinitionGrid({
  items,
  columns = 2,
}: {
  items: { label: string; value: React.ReactNode; wide?: boolean }[];
  columns?: 2 | 3;
}) {
  return (
    <dl
      className={`grid grid-cols-1 gap-x-8 gap-y-5 ${
        columns === 3 ? "sm:grid-cols-2 xl:grid-cols-3" : "sm:grid-cols-2"
      }`}
    >
      {items.map((item) => (
        <div key={item.label} className={`min-w-0 ${item.wide ? "sm:col-span-full" : ""}`}>
          <dt className="text-micro font-medium tracking-[0.08em] text-ink-muted uppercase">
            {item.label}
          </dt>
          <dd className="mt-1 min-w-0 text-body break-words text-ink">{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** A hero stat tile for detail pages: icon bubble, label, a value, a caption. */
export function HeroStat({
  icon: Icon,
  label,
  value,
  caption,
  tone,
  visual,
  "data-testid": testId,
}: {
  icon: LucideIcon;
  label: string;
  value: React.ReactNode;
  caption?: React.ReactNode;
  tone?: StatusTone;
  visual?: React.ReactNode;
  "data-testid"?: string;
}) {
  return (
    <Panel flush data-testid={testId} className="h-full gap-5 p-7">
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <HeaderBubble icon={Icon} tone={tone} />
          <p className="min-w-0 truncate text-caption font-medium text-ink-secondary">{label}</p>
        </div>
        {visual ? <div className="shrink-0">{visual}</div> : null}
      </div>
      <div className="min-w-0">
        <div className="truncate text-[1.875rem] leading-none font-semibold tracking-[-0.03em] text-ink">
          {value}
        </div>
        {caption ? <div className="mt-2 truncate text-micro text-ink-muted">{caption}</div> : null}
      </div>
    </Panel>
  );
}

/**
 * A horizontal stepper. Each step's `done` and `tone` are the caller's
 * rendering of facts the API sent — this only draws them.
 */
export function Stepper({
  steps,
  "data-testid": testId,
}: {
  steps: {
    key: string;
    label: string;
    done: boolean;
    tone: StatusTone;
    caption?: React.ReactNode;
    icon: LucideIcon;
  }[];
  "data-testid"?: string;
}) {
  return (
    <ol className="flex items-start" data-testid={testId}>
      {steps.map((step, index) => {
        const Icon = step.icon;
        const nextDone = index < steps.length - 1 ? steps[index + 1].done : false;
        const spec = toneSpec(step.tone);
        return (
          <li key={step.key} className="flex min-w-0 flex-1 items-start last:flex-none">
            <div className="flex w-28 shrink-0 flex-col items-center text-center">
              <span
                aria-hidden
                className={`flex h-12 w-12 items-center justify-center rounded-full ${
                  step.done
                    ? `${spec.chip} ring-4 ring-surface`
                    : "border border-dashed border-border-strong bg-surface-2 text-ink-muted"
                }`}
              >
                <Icon className="h-5 w-5" />
              </span>
              <span
                className={`mt-3 text-caption font-semibold ${step.done ? "text-ink" : "text-ink-muted"}`}
              >
                {step.label}
              </span>
              {step.caption ? (
                <span className="mt-0.5 max-w-full truncate text-micro text-ink-muted">
                  {step.caption}
                </span>
              ) : null}
            </div>
            {index < steps.length - 1 ? (
              <span
                aria-hidden
                className={`mt-6 h-[3px] min-w-6 flex-1 rounded-full ${
                  step.done && nextDone ? spec.dot : "bg-surface-3"
                } ${step.done && nextDone ? "opacity-60" : ""}`}
              />
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
