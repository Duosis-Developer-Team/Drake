"use client";

/**
 * The original one-component state renderer, now a thin adapter over the
 * separate primitives in `components/ui/states`.
 *
 * It stays because roughly thirty screens call it with a `kind`, and the
 * distinctions it encodes — that "no data", "zero" and "query failed" are
 * three different truths — are exactly the ones the new primitives keep. The
 * `data-testid` each kind renders is unchanged, so the existing state tests
 * keep testing the same thing.
 */

import {
  DeniedState,
  EmptyState,
  ErrorState,
  LoadingSkeleton,
  NoDataState,
  NotConfiguredState,
  PartialBanner,
  StaleBanner,
  UnknownState,
} from "@/components/ui/states";

export type DataStateKind =
  | "loading"
  | "empty"
  | "error"
  | "no-data"
  | "zero"
  | "stale"
  | "partial"
  | "estimated"
  | "unknown"
  | "not-configured"
  | "permission-denied";

/** Every kind this product distinguishes. Asserted by the state tests. */
export const DISTINCT_STATE_KINDS: DataStateKind[] = [
  "loading",
  "empty",
  "error",
  "no-data",
  "zero",
  "stale",
  "partial",
  "estimated",
  "unknown",
  "not-configured",
  "permission-denied",
];

export function DataState({
  kind,
  title,
  description,
  lastSuccessAt,
  onRetry,
}: {
  kind: DataStateKind;
  title?: string;
  description?: string;
  /** For stale states: when the last good value was produced. */
  lastSuccessAt?: string;
  onRetry?: () => void;
}) {
  switch (kind) {
    case "loading":
      return <LoadingSkeleton rows={3} />;
    case "empty":
      return <EmptyState compact title={title} description={description} />;
    case "error":
      return <ErrorState compact description={description} onRetry={onRetry} />;
    case "no-data":
      return <NoDataState compact title={title} description={description} />;
    case "zero":
      // A measured zero is a value, not an absence: it renders as the number.
      return (
        <p data-testid="state-zero" className="text-body text-ink">
          <span data-tabular className="font-semibold">
            0
          </span>{" "}
          <span className="text-ink-secondary">
            {description ?? "The source reported an actual value of 0."}
          </span>
        </p>
      );
    case "stale":
      return <StaleBanner asOf={lastSuccessAt} description={description} />;
    case "partial":
      return <PartialBanner description={description} />;
    case "estimated":
      return (
        <p data-testid="state-estimated" className="text-caption text-warning">
          <span className="font-medium">Estimated.</span>{" "}
          {description ??
            "Derived from a documented estimation method, not an exact measurement."}
        </p>
      );
    case "unknown":
      return <UnknownState compact title={title} description={description} />;
    case "not-configured":
      return <NotConfiguredState compact title={title} description={description} />;
    case "permission-denied":
      return <DeniedState compact title={title} description={description} />;
  }
}
