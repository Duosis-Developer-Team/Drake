"use client";

/** Shared catalog UI primitives: loadable fetch, meta rows, honest
 * operational-state cards. `unknown`/`not_configured`/`stale` are rendered
 * as their own states — never as healthy, zero, or blank. */

import { useCallback, useEffect, useState } from "react";

import { DataState, type DataStateKind } from "@/components/state/DataState";
import { StatusBadge, type HealthStatus } from "@/components/state/StatusBadge";
import { Card } from "@/components/ui/Card";
import { ApiError, apiGet } from "@/lib/api";
import type { OperationalState, Provenance } from "@/lib/catalog";

export type Loadable<T> =
  | { state: "loading" }
  | { state: "error"; message: string; correlationId?: string; notFound?: boolean }
  | { state: "ready"; data: T };

export function useApi<T>(path: string | null): [Loadable<T>, () => void] {
  const [value, setValue] = useState<Loadable<T>>({ state: "loading" });
  const load = useCallback(() => {
    if (!path) return;
    setValue({ state: "loading" });
    apiGet<T>(path)
      .then((data) => setValue({ state: "ready", data }))
      .catch((error: unknown) => {
        if (error instanceof ApiError) {
          setValue({
            state: "error",
            message: error.message,
            correlationId: error.correlationId,
            notFound: error.status === 404,
          });
        } else {
          setValue({ state: "error", message: "request failed" });
        }
      });
  }, [path]);
  useEffect(() => {
    load();
  }, [load]);
  return [value, load];
}

export function LoadGate<T>({
  value,
  retry,
  children,
}: {
  value: Loadable<T>;
  retry: () => void;
  children: (data: T) => React.ReactNode;
}) {
  if (value.state === "loading") return <DataState kind="loading" />;
  if (value.state === "error") {
    if (value.notFound) {
      return (
        <DataState
          kind="not-configured"
          title="Not found"
          description="This resource does not exist in your authorized scope."
        />
      );
    }
    return (
      <div>
        <DataState kind="error" description={value.message} onRetry={retry} />
        {value.correlationId ? (
          <p className="mt-1 text-xs text-ink-muted">
            Correlation ID: <span className="font-mono">{value.correlationId}</span>
          </p>
        ) : null}
      </div>
    );
  }
  return <>{children(value.data)}</>;
}

/**
 * Absence and degradation are DataStates; a capability that IS reporting is
 * a status, not an absence.
 *
 * `ok` used to map to `zero`, whose own description reads "The source
 * reported an actual value of 0." That was a placeholder waiting for live
 * sources, and while it stood, a service with working metrics rendered a
 * sentence that was simply false. The same conflation sat on `degraded`,
 * which mapped to `error` — "The request did not complete" — when degraded
 * means the opposite: it IS answering, just not well.
 */
const OPERATIONAL_KIND: Record<
  Exclude<OperationalState, "ok" | "degraded">,
  DataStateKind
> = {
  not_configured: "not-configured",
  unknown: "unknown",
  stale: "stale",
};

const OPERATIONAL_BADGE: Record<"ok" | "degraded", { status: HealthStatus; label: string }> = {
  // "Reporting" rather than "Healthy": this says the capability is wired and
  // answering. Whether what it reports is healthy is the signal's verdict,
  // and belongs to the charts, not to this card.
  ok: { status: "healthy", label: "Reporting" },
  degraded: { status: "warning", label: "Degraded" },
};

export function OperationalGrid({
  states,
  labels,
}: {
  states: Record<string, OperationalState>;
  labels: Record<string, string>;
}) {
  return (
    <div
      className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4"
      data-testid="operational-grid"
    >
      {Object.entries(labels).map(([key, label]) => {
        const state = states[key] ?? "unknown";
        return (
          <Card key={key} title={label}>
            {state === "ok" || state === "degraded" ? (
              <StatusBadge
                status={OPERATIONAL_BADGE[state].status}
                label={OPERATIONAL_BADGE[state].label}
              />
            ) : (
              <DataState kind={OPERATIONAL_KIND[state]} />
            )}
          </Card>
        );
      })}
    </div>
  );
}

const CRITICALITY_STATUS: Record<string, HealthStatus> = {
  low: "unknown",
  medium: "maintenance",
  high: "warning",
  critical: "critical",
};

export function CriticalityBadge({ criticality }: { criticality: string }) {
  return (
    <StatusBadge
      status={CRITICALITY_STATUS[criticality] ?? "unknown"}
      label={criticality}
    />
  );
}

export function LifecycleBadge({ lifecycle }: { lifecycle: string }) {
  return (
    <StatusBadge
      status={lifecycle === "active" ? "healthy" : "unknown"}
      label={lifecycle}
    />
  );
}

export function MetaRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-2 py-1.5">
      <dt className="text-xs font-medium text-ink-muted">{label}</dt>
      <dd className="text-sm text-ink">{children}</dd>
    </div>
  );
}

export function provenanceProps(source: Provenance, asOf: string) {
  return {
    source: `${source.kind}:${source.ref || "-"}`,
    asOf,
    freshness: "catalog",
    measurementMethod: "catalog_record",
    confidence: "exact" as const,
  };
}
