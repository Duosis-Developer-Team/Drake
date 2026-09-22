"use client";

/**
 * The state primitives.
 *
 * Every data-bearing region in Drake resolves to exactly one of these, and
 * they are separate components rather than one `<Message>` because the
 * distinctions are the product:
 *
 *   EmptyState        the scope is real and contains nothing
 *   NoDataState       the source answered for this window with no samples
 *   ErrorState        the query did not complete — NOT the same as empty
 *   DeniedState       out of scope; says nothing about whether data exists
 *   NotConfiguredState no source is wired up yet
 *   NotApplicableState the question does not apply to this kind of thing
 *   StaleBanner       real values, last known good, with when and from where
 *   PartialBanner     the answer does not cover the whole scope
 *   LoadingSkeleton   shaped like the thing that is coming
 *
 * A denied state never says "no data": implying a scope is empty when the
 * reader simply cannot see it is both wrong and a small information leak in
 * the other direction.
 */

import { Clock, PieChart, RefreshCw } from "lucide-react";

import type { StatusTone } from "@/lib/design/status";
import { toneSpec } from "@/lib/design/status";
import { useFormat, useT } from "@/lib/i18n";

export type StateLayout = "inline" | "centered";

/**
 * The one shape every state takes: a tone bubble, a title, one line, and an
 * optional action.
 *
 * `inline` sits beside other content (a table slot, a list); `centered` owns
 * its region (a chart's plot area, a page body). A compact block defaults to
 * inline and a full one to centered.
 *
 * Several screens already draw their own illustration above a compact state
 * inside a centred card. The `in-[.text-center]` variants notice that parent:
 * the block centres itself and drops its own bubble, so the card never shows
 * two icons stacked on top of each other.
 */
function StateBlock({
  tone,
  title,
  description,
  children,
  testId,
  compact = false,
  layout,
}: {
  tone: StatusTone;
  title: string;
  description?: React.ReactNode;
  children?: React.ReactNode;
  testId: string;
  compact?: boolean;
  layout?: StateLayout;
}) {
  const spec = toneSpec(tone);
  const Icon = spec.icon;
  const resolved = layout ?? (compact ? "inline" : "centered");

  if (resolved === "centered") {
    return (
      <div
        data-testid={testId}
        role="status"
        className={`flex flex-col items-center text-center ${compact ? "py-2" : "px-6 py-10"}`}
      >
        <span
          aria-hidden
          className={`flex shrink-0 items-center justify-center rounded-full ${spec.chip} ${
            compact ? "h-11 w-11" : "h-14 w-14"
          } in-[.text-center]:hidden`}
        >
          <Icon className={compact ? "h-5 w-5" : "h-6 w-6"} />
        </span>
        <p
          className={`font-semibold tracking-[-0.01em] text-ink in-[.text-center]:mt-0 ${
            compact ? "mt-3 text-body" : "mt-4 text-section"
          }`}
        >
          {title}
        </p>
        {description ? (
          <p className="mt-1 max-w-md text-caption text-ink-muted">{description}</p>
        ) : null}
        {children ? (
          <div className="mt-4 flex flex-wrap items-center justify-center gap-3">{children}</div>
        ) : null}
      </div>
    );
  }

  return (
    <div
      data-testid={testId}
      role="status"
      className={`flex items-start gap-3.5 in-[.text-center]:flex-col in-[.text-center]:items-center in-[.text-center]:gap-0 ${
        compact ? "py-1" : "px-1 py-6"
      }`}
    >
      <span
        aria-hidden
        className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full ${spec.chip} in-[.text-center]:hidden`}
      >
        <Icon className="h-[1.125rem] w-[1.125rem]" />
      </span>
      <div className="min-w-0 pt-0.5 in-[.text-center]:pt-0">
        <p className="text-body font-semibold text-ink">{title}</p>
        {description ? (
          <p className="mt-0.5 max-w-prose text-caption text-ink-muted in-[.text-center]:mx-auto">
            {description}
          </p>
        ) : null}
        {children ? (
          <div className="mt-3 flex flex-wrap items-center gap-3 in-[.text-center]:justify-center">
            {children}
          </div>
        ) : null}
      </div>
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
  compact,
  layout,
}: {
  title?: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
  compact?: boolean;
  layout?: StateLayout;
}) {
  const t = useT("ui");
  const common = useT("common");
  return (
    <StateBlock
      tone="neutral"
      testId="state-empty"
      title={title ?? common("state.empty")}
      description={description ?? t("state.empty.description")}
      compact={compact}
      layout={layout}
    >
      {action}
    </StateBlock>
  );
}

export function NoDataState({
  title,
  description,
  compact,
  layout,
}: {
  title?: string;
  description?: React.ReactNode;
  compact?: boolean;
  layout?: StateLayout;
}) {
  const t = useT("ui");
  return (
    <StateBlock
      tone="neutral"
      testId="state-no-data"
      title={title ?? t("state.noData.title")}
      description={description ?? t("state.noData.description")}
      compact={compact}
      layout={layout}
    />
  );
}

export function ErrorState({
  title,
  description,
  correlationId,
  onRetry,
  compact,
  layout,
}: {
  title?: string;
  description?: React.ReactNode;
  correlationId?: string;
  onRetry?: () => void;
  compact?: boolean;
  layout?: StateLayout;
}) {
  const t = useT("ui");
  const common = useT("common");
  return (
    <StateBlock
      tone="critical"
      testId="state-error"
      title={title ?? t("state.error.title")}
      description={description ?? t("state.error.description")}
      compact={compact}
      layout={layout}
    >
      {onRetry || correlationId ? (
      <div className="flex flex-wrap items-center gap-3">
          {onRetry ? (
            <button
              type="button"
              onClick={onRetry}
              className="inline-flex h-8 items-center gap-1.5 rounded-full border border-border bg-surface px-3.5 text-caption font-medium text-ink transition-colors hover:bg-surface-hover"
            >
              <RefreshCw className="h-3.5 w-3.5" aria-hidden />
              {common("action.retry")}
            </button>
          ) : null}
          {correlationId ? (
            <span className="inline-flex h-6 items-center rounded-full bg-surface-2 px-2.5 font-mono text-micro text-ink-muted">
              {t("state.error.ref", { id: correlationId })}
            </span>
          ) : null}
        </div>
      ) : null}
    </StateBlock>
  );
}

export function DeniedState({
  title,
  description,
  compact,
  layout,
}: {
  title?: string;
  description?: React.ReactNode;
  compact?: boolean;
  layout?: StateLayout;
}) {
  const t = useT("ui");
  return (
    <StateBlock
      tone="denied"
      testId="state-permission-denied"
      title={title ?? t("state.denied.title")}
      description={description ?? t("state.denied.description")}
      compact={compact}
      layout={layout}
    />
  );
}

/**
 * The resource is not there — or is not yours.
 *
 * Deliberately one state for both. Drake's API answers 404 for a resource
 * outside the caller's scope precisely so that probing an id cannot tell you
 * whether it exists, and a UI that rendered "permission denied" here would
 * hand that distinction straight back.
 */
export function NotFoundState({
  title,
  description,
  compact,
  layout,
}: {
  title?: string;
  description?: React.ReactNode;
  compact?: boolean;
  layout?: StateLayout;
}) {
  const t = useT("ui");
  const common = useT("common");
  return (
    <StateBlock
      tone="not-applicable"
      testId="state-not-found"
      title={title ?? common("state.notFound")}
      description={description ?? t("state.notFound.description")}
      compact={compact}
      layout={layout}
    />
  );
}

export function NotConfiguredState({
  title,
  description,
  action,
  compact,
  layout,
}: {
  title?: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
  compact?: boolean;
  layout?: StateLayout;
}) {
  const t = useT("ui");
  return (
    <StateBlock
      tone="not-applicable"
      testId="state-not-configured"
      title={title ?? t("state.notConfigured.title")}
      description={description ?? t("state.notConfigured.description")}
      compact={compact}
      layout={layout}
    >
      {action}
    </StateBlock>
  );
}

export function NotApplicableState({
  title,
  description,
  compact,
  layout,
}: {
  title?: string;
  description?: React.ReactNode;
  compact?: boolean;
  layout?: StateLayout;
}) {
  const t = useT("ui");
  const common = useT("common");
  return (
    <StateBlock
      tone="not-applicable"
      testId="state-not-applicable"
      title={title ?? common("state.notApplicable")}
      description={description ?? t("state.notApplicable.description")}
      compact={compact}
      layout={layout}
    />
  );
}

export function UnknownState({
  title,
  description,
  compact,
  layout,
}: {
  title?: string;
  description?: React.ReactNode;
  compact?: boolean;
  layout?: StateLayout;
}) {
  const t = useT("ui");
  const common = useT("common");
  return (
    <StateBlock
      tone="unknown"
      testId="state-unknown"
      title={title ?? common("state.unknown")}
      description={description ?? t("state.unknown.description")}
      compact={compact}
      layout={layout}
    />
  );
}

/**
 * The banner above last-known-good values.
 *
 * Above, not below: a reader who takes the numbers at face value and stops
 * reading has still been told what they are looking at.
 */
export function StaleBanner({
  asOf,
  description,
  source,
}: {
  asOf?: string | null;
  description?: React.ReactNode;
  source?: React.ReactNode;
}) {
  const t = useT("ui");
  const fmt = useFormat();
  return (
    <div
      role="status"
      data-testid="state-stale"
      className="flex items-start gap-3 rounded-[1.125rem] border border-stale/30 bg-stale-soft px-4 py-3 text-caption text-stale"
    >
      <span
        aria-hidden
        className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-stale/15"
      >
        <Clock className="h-4 w-4" />
      </span>
      <span className="min-w-0 pt-1">
        <span className="font-semibold">{t("state.stale.lead")}</span>{" "}
        {description ?? t("state.stale.description")}
        {asOf ? (
          <>
            {" "}
            {t("state.stale.lastUpdate")}{" "}
            <time dateTime={asOf} className="font-mono">
              {fmt.utc(asOf)}
            </time>{" "}
            ({fmt.relative(asOf)}).
          </>
        ) : null}
        {source ? (
          <>
            {" "}
            {t("state.stale.source")} {source}.
          </>
        ) : null}
      </span>
    </div>
  );
}

export function PartialBanner({ description }: { description?: React.ReactNode }) {
  const t = useT("ui");
  return (
    <div
      role="status"
      data-testid="state-partial"
      className="flex items-start gap-3 rounded-[1.125rem] border border-warning/30 bg-warning-soft px-4 py-3 text-caption text-warning"
    >
      <span
        aria-hidden
        className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-warning/15"
      >
        <PieChart className="h-4 w-4" />
      </span>
      <span className="min-w-0 pt-1">
        <span className="font-semibold">{t("state.partial.lead")}</span>{" "}
        {description ?? t("state.partial.description")}
      </span>
    </div>
  );
}

/**
 * Loading, shaped like the answer.
 *
 * `rows`/`variant` exist so the skeleton occupies roughly the space the real
 * content will: a spinner in a table slot moves everything below it when the
 * data lands, and that shift is the thing the skeleton is there to prevent.
 */
const SHIMMER = "animate-pulse bg-surface-3 motion-reduce:animate-none";
/** Fixed, not random: a skeleton that re-rolls on every render flickers. */
const BAR_HEIGHTS = [38, 52, 44, 66, 58, 72, 48, 62, 80, 56, 68, 46, 60, 74, 54, 64];

export function LoadingSkeleton({
  variant = "text",
  rows = 3,
  label,
  height,
}: {
  variant?: "text" | "table" | "chart" | "tiles";
  rows?: number;
  label?: string;
  /** Chart variant only: the height of the plot it stands in for. */
  height?: number;
}) {
  const common = useT("common");
  return (
    <div data-testid="state-loading" aria-busy="true" className="min-w-0">
      <span className="sr-only">{label ?? common("state.loading")}</span>
      {variant === "chart" ? (
        <div
          aria-hidden
          className="relative flex w-full flex-col justify-between overflow-hidden rounded-[1.125rem] bg-surface-2/60 px-5 pt-5 pb-4"
          style={{ height: height ?? 192 }}
        >
          {/* Faint grid, then a row of bars: the silhouette of a plot. */}
          {[0, 1, 2, 3].map((line) => (
            <span key={line} className="block border-t border-dashed border-border" />
          ))}
          <div className="absolute inset-x-5 bottom-4 flex h-[70%] items-end gap-[3%]">
            {BAR_HEIGHTS.map((bar, index) => (
              <span
                key={index}
                className={`${SHIMMER} flex-1 rounded-t-md`}
                style={{ height: `${bar}%`, animationDelay: `${index * 60}ms` }}
              />
            ))}
          </div>
        </div>
      ) : variant === "tiles" ? (
        <div aria-hidden className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {Array.from({ length: rows }).map((_, index) => (
            <div key={index} className="rounded-[1.125rem] border border-border bg-surface p-5">
              <div className="flex items-center gap-3">
                <span className={`${SHIMMER} h-9 w-9 rounded-full`} />
                <span className={`${SHIMMER} h-3 w-20 rounded-full`} />
              </div>
              <span className={`${SHIMMER} mt-5 block h-8 w-16 rounded-lg`} />
              <span className={`${SHIMMER} mt-4 block h-1.5 w-full rounded-full`} />
            </div>
          ))}
        </div>
      ) : variant === "table" ? (
        <div aria-hidden className="divide-y divide-border">
          {Array.from({ length: rows }).map((_, index) => (
            <div key={index} className="flex h-14 items-center gap-4 px-1">
              <span className={`${SHIMMER} h-9 w-9 shrink-0 rounded-full`} />
              <div className="min-w-0 flex-1 space-y-2">
                <span
                  className={`${SHIMMER} block h-3 rounded-full`}
                  style={{ width: `${[42, 34, 48, 38][index % 4]}%` }}
                />
                <span
                  className={`${SHIMMER} block h-2.5 rounded-full opacity-70`}
                  style={{ width: `${[24, 30, 20, 27][index % 4]}%` }}
                />
              </div>
              <span className={`${SHIMMER} h-6 w-20 shrink-0 rounded-full`} />
            </div>
          ))}
        </div>
      ) : (
        <div aria-hidden className="space-y-3 py-1">
          {Array.from({ length: rows }).map((_, index) => (
            <div
              key={index}
              className={`${SHIMMER} h-3 rounded-full`}
              style={{ width: `${[80, 60, 70, 50][index % 4]}%` }}
            />
          ))}
        </div>
      )}
    </div>
  );
}
