"use client";

/**
 * Presentational building blocks for the catalog screens (projects,
 * environments, services): stat tiles, tone composition bars, per-item mini
 * bars, capability tiles and a definition grid.
 *
 * Nothing here fetches or derives a fact. Every number is handed in by the
 * page, and an absent value stays visibly absent — a bar with nothing to
 * measure renders an empty track, never a zero-width "healthy" one.
 */

import type { LucideIcon } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { humanize, toneForHealth, toneSpec, type StatusTone } from "@/lib/design/status";
import { useFormat, useT } from "@/lib/i18n";
import type { OperationalState } from "@/lib/catalog";

/**
 * A design tone as the word a person reads, in the current locale.
 * `toneSpec(tone).label` is the English fallback the catalogue mirrors.
 */
export function useToneLabel(): (tone: StatusTone) => string {
  const t = useT("catalog");
  return useCallback((tone: StatusTone) => t.dyn("tone", tone, toneSpec(tone).label), [t]);
}

/**
 * A service-health status word in the current locale.
 *
 * The lane view model hands components an already-humanized English label
 * (`"Insufficient data"`), not the backend token, so the token is recovered
 * from it and looked up in the service-health area's `status.*` keys; the
 * English label stays the fallback until that catalogue has the word.
 */
export function useServiceStatusLabel(): (label: string) => string {
  const t = useT("serviceHealth");
  return useCallback(
    (label: string) => t.dyn("status", label.trim().toLowerCase().replace(/\s+/g, "_"), label),
    [t],
  );
}

/**
 * A relative time for use inside a sentence (`"accepted {when}"`), safe to
 * hydrate: the server has no clock the client agrees with, so the first
 * render prints the UTC instant and the mounted client swaps in "4m ago".
 * Standalone timestamps keep using `RelativeTime`, which does the same.
 */
export function useWhen(): (value: string | null | undefined) => string {
  const fmt = useFormat();
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    setNow(new Date());
  }, []);
  return useCallback(
    (value: string | null | undefined) => (now ? fmt.relative(value, now) : fmt.utc(value)),
    [fmt, now],
  );
}

export const BIG_NUMBER =
  "text-[2.25rem] leading-none font-semibold tracking-[-0.03em] text-ink";

/** A round icon bubble. `tone` tints it; without one it is a quiet surface. */
export function IconBubble({
  icon: Icon,
  tone,
  size = "default",
}: {
  icon: LucideIcon;
  tone?: StatusTone;
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

/**
 * A KPI tile: icon bubble and label, the big number, then a visual pinned to
 * the bottom so a row of tiles lines its visuals up.
 */
export function StatTile({
  icon,
  label,
  value,
  suffix,
  tone,
  aside,
  children,
  "data-testid": testId,
}: {
  icon: LucideIcon;
  label: string;
  value: React.ReactNode;
  suffix?: React.ReactNode;
  tone?: StatusTone;
  aside?: React.ReactNode;
  children?: React.ReactNode;
  "data-testid"?: string;
}) {
  return (
    <div
      data-testid={testId}
      className="flex h-full min-w-0 flex-col gap-5 rounded-[1.5rem] border border-border bg-surface p-6 shadow-panel"
    >
      <div className="flex items-center justify-between gap-3">
        <span className="flex min-w-0 items-center gap-3">
          <IconBubble icon={icon} tone={tone} />
          <span className="truncate text-caption font-medium text-ink-secondary">{label}</span>
        </span>
        {aside}
      </div>
      <p className="flex min-w-0 items-baseline gap-2">
        <span data-tabular className={BIG_NUMBER}>
          {value}
        </span>
        {suffix ? <span className="truncate text-caption text-ink-muted">{suffix}</span> : null}
      </p>
      {children ? <div className="mt-auto min-w-0">{children}</div> : null}
    </div>
  );
}

/** Absence tones draw as a quiet fill, so "not applicable" never reads as
 *  a heavy dark bar competing with real states. */
function barFill(tone: StatusTone): string {
  return tone === "not-applicable" || tone === "denied" ? "bg-ink-muted/45" : toneSpec(tone).dot;
}

export interface ToneCount {
  tone: StatusTone;
  label: string;
  count: number;
}

/**
 * A segmented bar of tone counts with a pill legend. Zero buckets are left
 * out of the bar but a fully empty set still draws its track.
 */
export function ToneBar({
  counts,
  label,
  legend = true,
  emptyLabel: emptyOverride,
}: {
  counts: ToneCount[];
  label: string;
  legend?: boolean;
  emptyLabel?: string;
}) {
  const t = useT("catalog");
  const emptyLabel = emptyOverride ?? t("toneBar.empty");
  const drawn = counts.filter((entry) => entry.count > 0);
  const total = drawn.reduce((sum, entry) => sum + entry.count, 0);
  return (
    <div className="min-w-0">
      <div
        role="img"
        aria-label={
          total === 0
            ? `${label}: ${emptyLabel}`
            : `${label}: ${drawn.map((entry) => `${entry.label} ${entry.count}`).join(", ")}`
        }
        className="flex h-2.5 w-full gap-1 overflow-hidden rounded-full bg-surface-3"
      >
        {drawn.map((entry) => (
          <span
            key={`${entry.tone}-${entry.label}`}
            className={`h-full rounded-full ${barFill(entry.tone)}`}
            style={{ width: `${(entry.count / total) * 100}%` }}
          />
        ))}
      </div>
      {legend ? (
        total === 0 ? (
          <p className="mt-2.5 text-micro text-ink-muted">{emptyLabel}</p>
        ) : (
          <ul className="mt-2.5 flex flex-wrap gap-x-3 gap-y-1">
            {drawn.map((entry) => (
              <li
                key={`${entry.tone}-${entry.label}`}
                className="inline-flex items-center gap-1.5 text-micro text-ink-secondary"
              >
                <span aria-hidden className={`h-2 w-2 rounded-full ${barFill(entry.tone)}`} />
                {entry.label}
                <span data-tabular className="font-semibold text-ink">
                  {entry.count}
                </span>
              </li>
            ))}
          </ul>
        )
      ) : null}
    </div>
  );
}

/**
 * One slim bar per item, scaled to the largest — "how is this spread across
 * my projects" at a glance. Shows the first `limit` and says how many more.
 */
export function MiniBars({
  items,
  label,
  limit = 3,
}: {
  items: { key: string; label: string; value: number }[];
  label: string;
  limit?: number;
}) {
  const common = useT("common");
  const max = Math.max(1, ...items.map((item) => item.value));
  const shown = [...items].sort((a, b) => b.value - a.value).slice(0, limit);
  return (
    <ul aria-label={label} className="flex flex-col gap-2">
      {shown.map((item) => (
        <li key={item.key} className="grid grid-cols-[minmax(0,5.5rem)_minmax(0,1fr)_auto] items-center gap-3">
          <span className="truncate text-micro text-ink-secondary">{item.label}</span>
          <span className="h-1.5 overflow-hidden rounded-full bg-surface-3">
            <span
              className="block h-full rounded-full bg-ink-secondary/70"
              style={{ width: `${(item.value / max) * 100}%` }}
            />
          </span>
          <span data-tabular className="text-micro font-semibold text-ink">
            {item.value}
          </span>
        </li>
      ))}
      {items.length > limit ? (
        <li className="text-micro text-ink-muted">{common("count.more", { count: items.length - limit })}</li>
      ) : null}
    </ul>
  );
}

/** A capability state as a tone and a word. `ok` reads "Reporting": wired
 *  and answering, which is not the same claim as healthy. */
export function capabilityTone(state: OperationalState | string): StatusTone {
  return toneForHealth(state === "ok" ? "healthy" : state);
}

export function capabilityLabel(state: OperationalState | string): string {
  return state === "ok" ? "Reporting" : humanize(state);
}

/** `capabilityLabel` in the current locale; the English word is the fallback. */
export function useCapabilityLabel(): (state: OperationalState | string) => string {
  const t = useT("catalog");
  return useCallback(
    (state: OperationalState | string) => t.dyn("capability.state", state, capabilityLabel(state)),
    [t],
  );
}

/**
 * One capability: icon bubble, name, state chip. Linked only when there is
 * somewhere to go — an absence has nothing to open.
 */
export function CapabilityTile({
  icon,
  label,
  state,
  href,
}: {
  icon: LucideIcon;
  label: string;
  state: OperationalState | string;
  href?: string | null;
}) {
  const stateLabel = useCapabilityLabel();
  const tone = capabilityTone(state);
  const spec = toneSpec(tone);
  const StateIcon = spec.icon;
  const configured = tone !== "not-applicable";
  const body = (
    <>
      <IconBubble icon={icon} tone={configured ? tone : undefined} />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-body font-semibold text-ink">{label}</span>
        <span className={`mt-0.5 inline-flex items-center gap-1 text-micro font-medium ${configured ? spec.text : "text-ink-muted"}`}>
          <StateIcon aria-hidden className="h-3 w-3" />
          {stateLabel(state)}
        </span>
      </span>
    </>
  );
  const className =
    "flex h-full min-w-0 items-center gap-3 rounded-[1.125rem] border border-border bg-surface-2/40 px-4 py-3.5";
  return href ? (
    <Link
      href={href}
      className={`${className} transition-colors hover:border-border-strong hover:bg-surface-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent`}
    >
      {body}
    </Link>
  ) : (
    <div className={className}>{body}</div>
  );
}

/** A clean two-column definition grid for record metadata. */
export function DefinitionGrid({
  items,
  columns = 2,
}: {
  items: { label: string; value: React.ReactNode; wide?: boolean }[];
  columns?: 2 | 3;
}) {
  return (
    <dl
      className={`grid grid-cols-1 gap-x-8 gap-y-5 sm:grid-cols-2 ${
        columns === 3 ? "xl:grid-cols-3" : ""
      }`}
    >
      {items.map((item) => (
        <div key={item.label} className={`min-w-0 ${item.wide ? "sm:col-span-2" : ""}`}>
          <dt className="text-micro font-medium tracking-[0.08em] text-ink-muted uppercase">
            {item.label}
          </dt>
          <dd className="mt-1.5 min-w-0 text-body break-words text-ink">{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** A small neutral pill for identity facts (runtime, branch, profile). */
export function FactPill({
  icon: Icon,
  children,
  mono = false,
}: {
  icon?: LucideIcon;
  children: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <span
      className={`inline-flex max-w-full items-center gap-1.5 rounded-full border border-border bg-surface-2 px-2.5 py-1 text-micro text-ink-secondary ${
        mono ? "font-mono" : ""
      }`}
    >
      {Icon ? <Icon aria-hidden className="h-3 w-3 shrink-0 text-ink-muted" /> : null}
      <span className="truncate">{children}</span>
    </span>
  );
}

/**
 * A designed empty/absent state for inside a card: icon bubble, title, one
 * line. `testId` lets a caller keep the shared state test ids.
 */
export function TileState({
  icon,
  tone,
  title,
  description,
  testId,
  children,
}: {
  icon: LucideIcon;
  tone?: StatusTone;
  title: string;
  description?: React.ReactNode;
  testId?: string;
  children?: React.ReactNode;
}) {
  return (
    <div data-testid={testId} role="status" className="flex items-start gap-3">
      <IconBubble icon={icon} tone={tone} size="small" />
      <div className="min-w-0 pt-1">
        <p className="text-body font-medium text-ink">{title}</p>
        {description ? <p className="mt-0.5 text-caption text-ink-muted">{description}</p> : null}
        {children ? <div className="mt-2">{children}</div> : null}
      </div>
    </div>
  );
}
