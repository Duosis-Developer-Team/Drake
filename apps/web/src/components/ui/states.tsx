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

import { Clock, RefreshCw } from "lucide-react";

import { formatRelative, formatUtc } from "@/lib/design/format";
import type { StatusTone } from "@/lib/design/status";
import { toneSpec } from "@/lib/design/status";

function StateBlock({
  tone,
  title,
  description,
  children,
  testId,
  compact = false,
}: {
  tone: StatusTone;
  title: string;
  description?: React.ReactNode;
  children?: React.ReactNode;
  testId: string;
  compact?: boolean;
}) {
  const spec = toneSpec(tone);
  const Icon = spec.icon;
  return (
    <div
      data-testid={testId}
      role="status"
      className={`flex items-start gap-3 ${compact ? "py-1" : "px-1 py-6"}`}
    >
      <Icon aria-hidden className={`mt-0.5 h-5 w-5 shrink-0 ${spec.text}`} />
      <div className="min-w-0">
        <p className="text-body font-medium text-ink">{title}</p>
        {description ? (
          <p className="mt-0.5 max-w-prose text-caption text-ink-secondary">{description}</p>
        ) : null}
        {children ? <div className="mt-2">{children}</div> : null}
      </div>
    </div>
  );
}

export function EmptyState({
  title = "Nothing here yet",
  description = "This collection has no entries in your authorized scope.",
  action,
  compact,
}: {
  title?: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
  compact?: boolean;
}) {
  return (
    <StateBlock
      tone="neutral"
      testId="state-empty"
      title={title}
      description={description}
      compact={compact}
    >
      {action}
    </StateBlock>
  );
}

export function NoDataState({
  title,
  description = "The source answered for this window and returned no samples. This is not a value of zero.",
  compact,
}: {
  title?: string;
  description?: React.ReactNode;
  compact?: boolean;
}) {
  return (
    <StateBlock
      tone="neutral"
      testId="state-no-data"
      title={title ?? "No data in this window"}
      description={description}
      compact={compact}
    />
  );
}

export function ErrorState({
  description,
  correlationId,
  onRetry,
  compact,
}: {
  description?: React.ReactNode;
  correlationId?: string;
  onRetry?: () => void;
  compact?: boolean;
}) {
  return (
    <StateBlock
      tone="critical"
      testId="state-error"
      title="Query failed"
      description={description ?? "The request did not complete. This is not the same as empty."}
      compact={compact}
    >
      <div className="flex flex-wrap items-center gap-3">
        {onRetry ? (
          <button
            type="button"
            onClick={onRetry}
            className="inline-flex items-center gap-1.5 rounded-control border border-border px-2.5 py-1 text-caption font-medium text-ink transition-colors hover:bg-surface-hover"
          >
            <RefreshCw className="h-3.5 w-3.5" aria-hidden />
            Retry
          </button>
        ) : null}
        {correlationId ? (
          <span className="font-mono text-micro text-ink-muted">ref {correlationId}</span>
        ) : null}
      </div>
    </StateBlock>
  );
}

export function DeniedState({
  title,
  description = "Your current scope does not include this. Whether anything exists here is not disclosed.",
  compact,
}: {
  title?: string;
  description?: React.ReactNode;
  compact?: boolean;
}) {
  return (
    <StateBlock
      tone="denied"
      testId="state-permission-denied"
      title={title ?? "Permission required"}
      description={description}
      compact={compact}
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
  title = "Not found",
  description = "This resource does not exist in your authorized scope.",
  compact,
}: {
  title?: string;
  description?: React.ReactNode;
  compact?: boolean;
}) {
  return (
    <StateBlock
      tone="not-applicable"
      testId="state-not-found"
      title={title}
      description={description}
      compact={compact}
    />
  );
}

export function NotConfiguredState({
  title,
  description = "No source has been connected for this yet.",
  action,
  compact,
}: {
  title?: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
  compact?: boolean;
}) {
  return (
    <StateBlock
      tone="not-applicable"
      testId="state-not-configured"
      title={title ?? "Not configured"}
      description={description}
      compact={compact}
    >
      {action}
    </StateBlock>
  );
}

export function NotApplicableState({
  title,
  description,
  compact,
}: {
  title?: string;
  description?: React.ReactNode;
  compact?: boolean;
}) {
  return (
    <StateBlock
      tone="not-applicable"
      testId="state-not-applicable"
      title={title ?? "Not applicable"}
      description={
        description ?? "This does not apply to this resource, so there is nothing to report."
      }
      compact={compact}
    />
  );
}

export function UnknownState({
  title,
  description = "The state cannot be determined. It is reported as unknown rather than assumed.",
  compact,
}: {
  title?: string;
  description?: React.ReactNode;
  compact?: boolean;
}) {
  return (
    <StateBlock
      tone="unknown"
      testId="state-unknown"
      title={title ?? "Unknown"}
      description={description}
      compact={compact}
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
  return (
    <div
      role="status"
      data-testid="state-stale"
      className="flex items-start gap-2 rounded-control border border-stale/40 bg-stale-soft px-3 py-2 text-caption text-stale"
    >
      <Clock aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
      <span className="min-w-0">
        <span className="font-medium">Last known values.</span>{" "}
        {description ?? "The source has not refreshed in time, so these are not current."}
        {asOf ? (
          <>
            {" "}
            Last successful update{" "}
            <time dateTime={asOf} className="font-mono">
              {formatUtc(asOf)}
            </time>{" "}
            ({formatRelative(asOf)}).
          </>
        ) : null}
        {source ? <> Source: {source}.</> : null}
      </span>
    </div>
  );
}

export function PartialBanner({ description }: { description?: React.ReactNode }) {
  return (
    <div
      role="status"
      data-testid="state-partial"
      className="rounded-control border border-warning/40 bg-warning-soft px-3 py-2 text-caption text-warning"
    >
      <span className="font-medium">Partial result.</span>{" "}
      {description ?? "Part of the requested scope could not be read, so this does not cover all of it."}
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
export function LoadingSkeleton({
  variant = "text",
  rows = 3,
  label = "Loading",
}: {
  variant?: "text" | "table" | "chart" | "tiles";
  rows?: number;
  label?: string;
}) {
  const shimmer = "animate-pulse rounded bg-surface-3 motion-reduce:animate-none";
  return (
    <div data-testid="state-loading" aria-busy="true" className="min-w-0">
      <span className="sr-only">{label}</span>
      {variant === "chart" ? (
        <div className={`${shimmer} h-48 w-full rounded-control`} />
      ) : variant === "tiles" ? (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {Array.from({ length: rows }).map((_, index) => (
            <div key={index} className={`${shimmer} h-20 rounded-control`} />
          ))}
        </div>
      ) : variant === "table" ? (
        <div className="space-y-px">
          {Array.from({ length: rows }).map((_, index) => (
            <div key={index} className={`${shimmer} h-9 w-full`} />
          ))}
        </div>
      ) : (
        <div className="space-y-2">
          {Array.from({ length: rows }).map((_, index) => (
            <div
              key={index}
              className={`${shimmer} h-4`}
              style={{ width: `${[80, 60, 70, 50][index % 4]}%` }}
            />
          ))}
        </div>
      )}
    </div>
  );
}
