"use client";

/**
 * Presentational kit for the configuration screens (integrations,
 * notification routing, access control).
 *
 * Nothing here fetches or decides anything. The state cards keep the exact
 * `data-testid`s of the shared state primitives, so a designed empty state is
 * still the same honest state to every test and screen reader.
 */

import type { LucideIcon } from "lucide-react";
import { Ban, CircleSlash, Inbox, RefreshCw, XCircle } from "lucide-react";

import { Panel } from "@/components/ui/Panel";
import { toneSpec, type StatusTone } from "@/lib/design/status";

export const PILL_BUTTON =
  "inline-flex items-center justify-center gap-1.5 rounded-full border border-border bg-surface px-3.5 py-1.5 text-caption font-medium text-ink transition-colors hover:bg-surface-hover disabled:cursor-not-allowed disabled:opacity-50";

export const PILL_PRIMARY =
  "inline-flex items-center justify-center gap-1.5 rounded-full bg-accent px-5 py-2.5 text-body font-medium text-ink-inverse transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50";

export const PILL_FIELD =
  "h-11 w-full min-w-0 appearance-none rounded-full border border-border bg-surface px-4 text-body text-ink placeholder:text-ink-muted disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-accent";

export const FIELD_LABEL =
  "mb-1.5 block text-caption font-medium text-ink-secondary";

export const TABLE_HEAD =
  "text-micro font-medium uppercase tracking-[0.08em] text-ink-muted";

/** A round icon bubble, tone-tinted or neutral. */
export function IconBubble({
  icon: Icon,
  tone,
  size = "md",
}: {
  icon: LucideIcon;
  tone?: StatusTone;
  size?: "sm" | "md" | "lg" | "xl";
}) {
  const box = {
    sm: "h-8 w-8",
    md: "h-10 w-10",
    lg: "h-11 w-11",
    xl: "h-14 w-14",
  }[size];
  const glyph = {
    sm: "h-4 w-4",
    md: "h-[1.125rem] w-[1.125rem]",
    lg: "h-5 w-5",
    xl: "h-6 w-6",
  }[size];
  const colour = tone ? toneSpec(tone).chip : "bg-surface-2 text-ink-secondary";
  return (
    <span
      aria-hidden
      className={`flex shrink-0 items-center justify-center rounded-full ${box} ${colour}`}
    >
      <Icon className={glyph} />
    </span>
  );
}

/** Two-letter initials in a neutral bubble — for people and accounts. */
export function InitialsBubble({
  name,
  size = "md",
}: {
  name: string;
  size?: "sm" | "md";
}) {
  const initials =
    name
      .split(/[\s/_.-]+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase() ?? "")
      .join("") || "?";
  return (
    <span
      aria-hidden
      className={`flex shrink-0 items-center justify-center rounded-full bg-surface-3 font-semibold text-ink-secondary ${
        size === "sm" ? "h-8 w-8 text-micro" : "h-10 w-10 text-caption"
      }`}
    >
      {initials}
    </span>
  );
}

/** A KPI card: icon bubble, label, big number, and a visual underneath. */
export function KpiTile({
  icon,
  tone,
  label,
  value,
  suffix,
  children,
  "data-testid": testId,
}: {
  icon: LucideIcon;
  tone?: StatusTone;
  label: string;
  value: React.ReactNode;
  suffix?: React.ReactNode;
  children?: React.ReactNode;
  "data-testid"?: string;
}) {
  return (
    <Panel className="h-full !gap-5" data-testid={testId}>
      <div className="flex items-center gap-3">
        <IconBubble icon={icon} tone={tone} />
        <span className="text-caption font-medium text-ink-secondary">
          {label}
        </span>
      </div>
      <div className="flex items-baseline gap-2">
        <span
          data-tabular
          className="text-[2.25rem] leading-none font-semibold tracking-[-0.03em] text-ink"
        >
          {value}
        </span>
        {suffix ? (
          <span className="text-caption text-ink-muted">{suffix}</span>
        ) : null}
      </div>
      {children ? <div className="mt-auto min-w-0">{children}</div> : null}
    </Panel>
  );
}

/** A thin share bar: how much of a whole one count is. */
export function ShareBar({
  value,
  total,
  tone = "info",
  label,
}: {
  value: number;
  total: number;
  tone?: StatusTone;
  label: string;
}) {
  const share =
    total > 0 ? Math.min(100, Math.round((value / total) * 100)) : 0;
  return (
    <div className="min-w-0">
      <div
        role="img"
        aria-label={`${label}: ${value} of ${total}`}
        className="h-2 w-full overflow-hidden rounded-full bg-surface-3"
      >
        <span
          className={`block h-full rounded-full ${toneSpec(tone).dot}`}
          style={{ width: `${share}%` }}
        />
      </div>
      <p className="mt-2 text-micro text-ink-muted">
        <span data-tabular className="font-medium text-ink-secondary">
          {total > 0 ? `${share}%` : "—"}
        </span>{" "}
        {label}
      </p>
    </div>
  );
}

type StateKind =
  "empty" | "not-configured" | "no-data" | "permission-denied" | "error";

const STATE_DEFAULTS: Record<
  StateKind,
  { testId: string; icon: LucideIcon; tone: StatusTone; title: string }
> = {
  empty: {
    testId: "state-empty",
    icon: Inbox,
    tone: "neutral",
    title: "Nothing here yet",
  },
  "not-configured": {
    testId: "state-not-configured",
    icon: CircleSlash,
    tone: "not-applicable",
    title: "Not configured",
  },
  "no-data": {
    testId: "state-no-data",
    icon: Inbox,
    tone: "neutral",
    title: "No data",
  },
  "permission-denied": {
    testId: "state-permission-denied",
    icon: Ban,
    tone: "denied",
    title: "Permission required",
  },
  error: {
    testId: "state-error",
    icon: XCircle,
    tone: "critical",
    title: "Query failed",
  },
};

/**
 * A designed state: icon bubble, title, one line, optional action. Centred
 * for a card of its own, or `inline` (left-aligned row) inside a card.
 */
export function StateCard({
  kind,
  title,
  description,
  icon,
  tone: toneOverride,
  action,
  onRetry,
  inline = false,
  children,
}: {
  kind: StateKind;
  title?: string;
  description?: React.ReactNode;
  icon?: LucideIcon;
  tone?: StatusTone;
  action?: React.ReactNode;
  onRetry?: () => void;
  inline?: boolean;
  children?: React.ReactNode;
}) {
  const spec = STATE_DEFAULTS[kind];
  const Icon = icon ?? spec.icon;
  const retry = onRetry ? (
    <button type="button" onClick={onRetry} className={PILL_BUTTON}>
      <RefreshCw aria-hidden className="h-3.5 w-3.5" />
      Retry
    </button>
  ) : null;
  const tone =
    toneOverride ??
    (kind === "empty" || kind === "no-data" ? undefined : spec.tone);

  if (inline) {
    return (
      <div
        data-testid={spec.testId}
        role="status"
        className="flex items-start gap-4"
      >
        <IconBubble icon={Icon} tone={tone} size="lg" />
        <div className="min-w-0 flex-1">
          <p className="text-body font-semibold text-ink">
            {title ?? spec.title}
          </p>
          {description ? (
            <p className="mt-0.5 text-caption text-ink-muted">{description}</p>
          ) : null}
          {children}
          {action || retry ? (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              {action}
              {retry}
            </div>
          ) : null}
        </div>
      </div>
    );
  }

  return (
    <div
      data-testid={spec.testId}
      role="status"
      className="flex flex-1 flex-col items-center justify-center px-6 py-10 text-center"
    >
      <span
        aria-hidden
        className="flex h-16 w-16 items-center justify-center rounded-full bg-surface-2 ring-8 ring-surface-2/40"
      >
        <IconBubble icon={Icon} tone={tone} size="lg" />
      </span>
      <p className="mt-5 text-[1.0625rem] font-semibold text-ink">
        {title ?? spec.title}
      </p>
      {description ? (
        <p className="mt-1 max-w-md text-caption text-ink-muted">
          {description}
        </p>
      ) : null}
      {children}
      {action || retry ? (
        <div className="mt-5 flex flex-wrap items-center justify-center gap-2">
          {action}
          {retry}
        </div>
      ) : null}
    </div>
  );
}

/** Pill tabs with real tab semantics. */
export function PillTabs<T extends string>({
  label,
  value,
  tabs,
  onChange,
}: {
  label: string;
  value: T;
  tabs: { key: T; label: string; icon?: LucideIcon }[];
  onChange: (key: T) => void;
}) {
  return (
    <div
      role="tablist"
      aria-label={label}
      className="inline-flex items-center gap-1 rounded-full border border-border bg-surface p-1 shadow-panel"
    >
      {tabs.map((tab) => {
        const selected = tab.key === value;
        const Icon = tab.icon;
        return (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={selected}
            onClick={() => onChange(tab.key)}
            className={`inline-flex h-9 items-center gap-2 rounded-full px-4 text-body font-medium transition-colors ${
              selected
                ? "bg-accent text-ink-inverse"
                : "text-ink-secondary hover:bg-surface-hover hover:text-ink"
            }`}
          >
            {Icon ? <Icon aria-hidden className="h-4 w-4" /> : null}
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}

/** Pill filter chips (single choice) with radio semantics. */
export function FilterChips<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: { value: T; label: string; count?: number }[];
  onChange: (value: T) => void;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className="flex flex-wrap items-center gap-2"
    >
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={() => onChange(option.value)}
            className={`inline-flex h-9 items-center gap-2 rounded-full border px-4 text-caption font-medium transition-colors ${
              selected
                ? "border-transparent bg-accent text-ink-inverse"
                : "border-border bg-surface text-ink-secondary hover:bg-surface-hover hover:text-ink"
            }`}
          >
            {option.label}
            {option.count !== undefined ? (
              <span
                data-tabular
                className={`rounded-full px-1.5 text-micro ${
                  selected ? "bg-surface/20" : "bg-surface-3 text-ink-muted"
                }`}
              >
                {option.count}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

/** Metadata as a clean definition grid. */
export function MetaGrid({
  items,
  columns = 2,
}: {
  items: { label: string; value: React.ReactNode }[];
  columns?: 2 | 3;
}) {
  return (
    <dl
      className={`grid grid-cols-1 gap-3 ${columns === 3 ? "sm:grid-cols-3" : "sm:grid-cols-2"}`}
    >
      {items.map((item) => (
        <div
          key={item.label}
          className="min-w-0 rounded-2xl bg-surface-2 px-4 py-3"
        >
          <dt className="text-micro text-ink-muted">{item.label}</dt>
          <dd className="mt-1 min-w-0 truncate text-caption font-medium text-ink">
            {item.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}
