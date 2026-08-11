"use client";

/** Shared catalog UI primitives: loadable fetch, meta rows, honest
 * operational-state cards. `unknown`/`not_configured`/`stale` are rendered
 * as their own states — never as healthy, zero, or blank. */

import { useCallback, useEffect, useState } from "react";

import { DataState, type DataStateKind } from "@/components/state/DataState";
import { NotFoundState } from "@/components/ui/states";
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
      // The API answers 404 for a resource outside the caller's scope
      // precisely so that probing an id cannot reveal whether it exists, and
      // this renders that one way everywhere. It is deliberately NOT the
      // "not configured" state: nothing here is unconfigured, and saying so
      // would be a different — wrong — claim.
      return <NotFoundState />;
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

const OPERATIONAL_KIND: Record<OperationalState, DataStateKind> = {
  not_configured: "not-configured",
  unknown: "unknown",
  stale: "stale",
  degraded: "error",
  ok: "zero", // placeholder mapping; real healthy visuals arrive with live sources
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
            <DataState kind={OPERATIONAL_KIND[state]} />
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
