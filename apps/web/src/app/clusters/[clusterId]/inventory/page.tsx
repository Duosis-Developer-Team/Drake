"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { LoadGate, useApi } from "@/components/catalog/primitives";
import {
  HealthBadge,
  InventoryStateBadge,
  formatUtc,
} from "@/components/inventory/primitives";
import { DataState } from "@/components/state/DataState";
import { StatusBadge } from "@/components/state/StatusBadge";
import { Card } from "@/components/ui/Card";
import { ApiError, apiGet } from "@/lib/api";
import {
  INVENTORY_KINDS,
  type InventoryResourceList,
  type InventoryResourceRow,
} from "@/lib/inventory";

const HEALTH_OPTIONS = ["healthy", "degraded", "unhealthy", "unknown"] as const;
const LIFECYCLE_OPTIONS = ["active", "missing", "all"] as const;

function buildQuery(filters: {
  kind: string;
  health: string;
  lifecycle: string;
  search: string;
  cursor?: string;
}): string {
  const params = new URLSearchParams();
  if (filters.kind) params.set("kind", filters.kind);
  if (filters.health) params.set("health", filters.health);
  if (filters.lifecycle && filters.lifecycle !== "active") {
    params.set("lifecycle", filters.lifecycle);
  }
  if (filters.search.trim().length >= 2) params.set("search", filters.search.trim());
  if (filters.cursor) params.set("cursor", filters.cursor);
  const encoded = params.toString();
  return encoded ? `?${encoded}` : "";
}

export default function ClusterInventoryPage() {
  const { clusterId } = useParams<{ clusterId: string }>();
  const initialKind = useSearchParams().get("kind") ?? "";

  const [kind, setKind] = useState(initialKind);
  const [health, setHealth] = useState("");
  const [lifecycle, setLifecycle] = useState<string>("active");
  const [search, setSearch] = useState("");

  const query = buildQuery({ kind, health, lifecycle, search });
  const [firstPage, retry] = useApi<InventoryResourceList>(
    `/v1/clusters/${clusterId}/inventory/resources${query}`,
  );

  // Additional cursor pages are appended; filters reset the accumulation.
  const [extraRows, setExtraRows] = useState<InventoryResourceRow[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState<string | null>(null);
  useEffect(() => {
    setExtraRows([]);
    setNextCursor(null);
    setMoreError(null);
  }, [query]);
  useEffect(() => {
    if (firstPage.state === "ready") setNextCursor(firstPage.data.next_cursor);
  }, [firstPage]);

  const loadMore = useCallback(() => {
    if (!nextCursor) return;
    setLoadingMore(true);
    setMoreError(null);
    apiGet<InventoryResourceList>(
      `/v1/clusters/${clusterId}/inventory/resources${buildQuery({
        kind,
        health,
        lifecycle,
        search,
        cursor: nextCursor,
      })}`,
    )
      .then((body) => {
        setExtraRows((rows) => [...rows, ...body.resources]);
        setNextCursor(body.next_cursor);
      })
      .catch((error: unknown) => {
        setMoreError(error instanceof ApiError ? error.message : "request failed");
      })
      .finally(() => setLoadingMore(false));
  }, [clusterId, kind, health, lifecycle, search, nextCursor]);

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <div>
        <p className="text-xs text-ink-muted">
          <Link href="/clusters" className="hover:text-ink">
            Clusters
          </Link>{" "}
          /{" "}
          <Link href={`/clusters/${clusterId}`} className="hover:text-ink">
            Detail
          </Link>{" "}
          / Inventory
        </p>
        <h1 className="mt-1 text-xl font-semibold tracking-tight text-ink">
          Inventory resources
        </h1>
        <p className="mt-1 text-sm text-ink-secondary">
          Observed Kubernetes resources with derived health. Missing resources
          stay listed under the missing lifecycle — they are never silently
          dropped.
        </p>
      </div>

      <Card>
        <form
          className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4"
          onSubmit={(event) => event.preventDefault()}
          aria-label="Inventory filters"
        >
          <label className="block text-xs font-medium text-ink-muted">
            Kind
            <select
              value={kind}
              onChange={(event) => setKind(event.target.value)}
              className="mt-1 w-full rounded-lg border border-border bg-surface px-2.5 py-1.5 text-sm text-ink"
              data-testid="filter-kind"
            >
              <option value="">All kinds</option>
              {INVENTORY_KINDS.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-xs font-medium text-ink-muted">
            Health
            <select
              value={health}
              onChange={(event) => setHealth(event.target.value)}
              className="mt-1 w-full rounded-lg border border-border bg-surface px-2.5 py-1.5 text-sm text-ink"
              data-testid="filter-health"
            >
              <option value="">All health states</option>
              {HEALTH_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-xs font-medium text-ink-muted">
            Lifecycle
            <select
              value={lifecycle}
              onChange={(event) => setLifecycle(event.target.value)}
              className="mt-1 w-full rounded-lg border border-border bg-surface px-2.5 py-1.5 text-sm text-ink"
              data-testid="filter-lifecycle"
            >
              {LIFECYCLE_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-xs font-medium text-ink-muted">
            Name contains
            <input
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              minLength={2}
              maxLength={64}
              placeholder="min 2 characters"
              className="mt-1 w-full rounded-lg border border-border bg-surface px-2.5 py-1.5 text-sm text-ink"
              data-testid="filter-search"
            />
          </label>
        </form>

        <LoadGate value={firstPage} retry={retry}>
          {(body) => {
            const rows = [...body.resources, ...extraRows];
            return (
              <div className="space-y-3">
                <div className="flex flex-wrap items-center gap-2 text-xs text-ink-muted">
                  <span>Inventory state:</span>
                  <InventoryStateBadge state={body.inventory.state} />
                  <span>
                    as of <time className="font-mono">{formatUtc(body.as_of)}</time>
                  </span>
                </div>
                {rows.length === 0 ? (
                  <DataState
                    kind="empty"
                    title="No resources match"
                    description="Nothing in the authorized inventory matches these filters."
                  />
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-2xl text-left text-sm">
                      <thead>
                        <tr className="border-b border-border text-xs text-ink-muted">
                          <th scope="col" className="py-2 pr-3 font-medium">
                            Kind
                          </th>
                          <th scope="col" className="py-2 pr-3 font-medium">
                            Namespace
                          </th>
                          <th scope="col" className="py-2 pr-3 font-medium">
                            Name
                          </th>
                          <th scope="col" className="py-2 pr-3 font-medium">
                            Health
                          </th>
                          <th scope="col" className="py-2 pr-3 font-medium">
                            Lifecycle
                          </th>
                          <th scope="col" className="py-2 font-medium">
                            Observed (UTC)
                          </th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-border" data-testid="resource-rows">
                        {rows.map((row) => (
                          <tr key={row.id}>
                            <td className="py-2 pr-3 font-mono text-xs">{row.kind}</td>
                            <td className="py-2 pr-3 font-mono text-xs">
                              {row.namespace ?? "—"}
                            </td>
                            <td className="py-2 pr-3">
                              <Link
                                href={`/clusters/${clusterId}/inventory/${row.id}`}
                                className="font-mono text-xs text-ink underline-offset-2 hover:underline"
                              >
                                {row.name}
                              </Link>
                            </td>
                            <td className="py-2 pr-3">
                              <HealthBadge health={row.health} />
                            </td>
                            <td className="py-2 pr-3">
                              {row.lifecycle === "missing" ? (
                                <StatusBadge status="stale" label="missing" />
                              ) : (
                                <span className="text-xs text-ink-secondary">active</span>
                              )}
                            </td>
                            <td className="py-2 font-mono text-xs">
                              {formatUtc(row.observed_at)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {moreError ? (
                  <DataState kind="error" description={moreError} onRetry={loadMore} />
                ) : null}
                {nextCursor ? (
                  <button
                    type="button"
                    onClick={loadMore}
                    disabled={loadingMore}
                    className="rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-ink-secondary hover:bg-surface-sunken disabled:opacity-60"
                    data-testid="load-more"
                  >
                    {loadingMore ? "Loading…" : "Load more"}
                  </button>
                ) : null}
              </div>
            );
          }}
        </LoadGate>
      </Card>
    </div>
  );
}
