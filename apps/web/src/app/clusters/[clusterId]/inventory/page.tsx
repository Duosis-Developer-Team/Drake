"use client";

/**
 * Cluster inventory.
 *
 * The table is the product here — an operator comes to find one resource —
 * so the summary above it is deliberately thin: a distribution by kind, the
 * health composition of each resource class, and the freshness of the sweep
 * that produced all of it.
 *
 * Kind distribution is a sorted bar and not a pie. A real cluster has twenty
 * kinds with a long tail, and nobody can rank twenty wedges; the bar ranks
 * them for you and folds the tail into one honest "other" row.
 *
 * Two rules the whole screen is built around:
 *
 *   Missing resources stay listed. A resource that vanished from the cluster
 *   is the most interesting row on the page, and the default filter shows
 *   active only — so the count of what that hides is always printed.
 *
 *   Secrets and ConfigMaps never appear — not in the table, not in the kind
 *   filter, not as a bar in the distribution chart. The kind list comes from
 *   `INVENTORY_KINDS`, which is the allowlist.
 */

import { useParams, useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";

import { SortedBarChart } from "@/components/charts/CategoryCharts";
import { Donut, ToneCounters } from "@/components/charts/visuals";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { Panel, PanelHeader, SectionHeader } from "@/components/ui/Panel";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { Button, FilterBar, Select } from "@/components/ui/controls";
import { CopyableIdentifier, FreshnessIndicator, Timestamp } from "@/components/ui/identifiers";
import {
  DeniedState,
  EmptyState,
  ErrorState,
  LoadingSkeleton,
  NotFoundState,
} from "@/components/ui/states";
import { humanize, toneForHealth } from "@/lib/design/status";
import { ApiError, apiGet } from "@/lib/api";
import {
  INVENTORY_KINDS,
  type HealthRollup,
  type InventoryResourceList,
  type InventoryResourceRow,
  type InventorySummary,
} from "@/lib/inventory";
import { useResource } from "@/lib/useResource";

const HEALTH_OPTIONS = [
  { value: "healthy", label: "Healthy" },
  { value: "degraded", label: "Degraded" },
  { value: "unhealthy", label: "Unhealthy" },
  { value: "unknown", label: "Unknown" },
];

const LIFECYCLE_OPTIONS = [
  { value: "active", label: "Active" },
  { value: "missing", label: "Missing" },
  { value: "all", label: "All" },
];

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

function rollupSegments(rollup: HealthRollup) {
  return [
    { name: "Healthy", value: rollup.healthy, tone: "success" as const },
    { name: "Degraded", value: rollup.degraded, tone: "warning" as const },
    { name: "Unhealthy", value: rollup.unhealthy, tone: "critical" as const },
    { name: "Unknown", value: rollup.unknown, tone: "unknown" as const },
  ];
}

function InventoryInner() {
  const { clusterId } = useParams<{ clusterId: string }>();
  const router = useRouter();
  const params = useSearchParams();

  // Filter state lives in the URL: a filtered inventory view is the thing
  // people paste to each other, and the back button has to undo a filter.
  const kind = params.get("kind") ?? "";
  const health = params.get("health") ?? "";
  const lifecycle = params.get("lifecycle") ?? "active";
  const search = params.get("search") ?? "";
  const [draft, setDraft] = useState(search);

  const setParam = useCallback(
    (updates: Record<string, string>) => {
      const next = new URLSearchParams(params.toString());
      for (const [key, value] of Object.entries(updates)) {
        if (value) next.set(key, value);
        else next.delete(key);
      }
      const encoded = next.toString();
      router.replace(
        `/clusters/${clusterId}/inventory${encoded ? `?${encoded}` : ""}`,
        { scroll: false },
      );
    },
    [params, router, clusterId],
  );

  useEffect(() => {
    if (draft === search) return;
    const timer = setTimeout(
      () => setParam({ search: draft.trim().length >= 2 ? draft.trim() : "" }),
      300,
    );
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft]);

  const query = buildQuery({ kind, health, lifecycle, search });
  const summary = useResource<InventorySummary>(
    `/v1/clusters/${clusterId}/inventory/summary`,
  );
  const page = useResource<InventoryResourceList>(
    `/v1/clusters/${clusterId}/inventory/resources${query}`,
  );

  // Cursor pages accumulate; changing a filter starts over.
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
    if (page.data) setNextCursor(page.data.next_cursor);
  }, [page.data]);

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

  const rows = useMemo(
    () => [...(page.data?.resources ?? []), ...extraRows],
    [page.data, extraRows],
  );
  const filtered = Boolean(kind || health || search) || lifecycle !== "active";

  const kindCategories = useMemo(
    () =>
      Object.entries(summary.data?.by_kind ?? {}).map(([name, rollup]) => ({
        name,
        value: rollup.total,
      })),
    [summary.data],
  );

  const columns: Column<InventoryResourceRow>[] = [
    {
      key: "kind",
      header: "Kind",
      cell: (row) => <span className="font-mono text-micro text-ink-secondary">{row.kind}</span>,
    },
    {
      key: "namespace",
      header: "Namespace",
      cell: (row) => (
        <span className="font-mono text-micro text-ink-secondary">{row.namespace ?? "—"}</span>
      ),
    },
    {
      key: "name",
      header: "Name",
      cell: (row) => (
        <Link
          href={`/clusters/${clusterId}/inventory/${row.id}`}
          className="rounded font-mono text-micro text-ink hover:text-brand"
        >
          {row.name}
        </Link>
      ),
    },
    {
      key: "health",
      header: "Health",
      cell: (row) => (
        <StatusBadge status={toneForHealth(row.health)} label={humanize(row.health)} size="compact" />
      ),
    },
    {
      key: "lifecycle",
      header: "Lifecycle",
      cell: (row) =>
        row.lifecycle === "missing" ? (
          <StatusBadge status="stale" label="Missing" size="compact" />
        ) : (
          <span className="text-caption text-ink-secondary">Active</span>
        ),
    },
    {
      key: "observed",
      header: "Observed",
      align: "right",
      priority: "low",
      cell: (row) => <Timestamp value={row.observed_at} className="text-ink-muted" />,
    },
  ];

  return (
    <PageFrame width="wide">
      <PageHeader
        title="Inventory"
        description="Observed Kubernetes resources with derived health. A resource that disappeared stays listed as missing — it is never silently dropped."
        status={
          page.data ? (
            <StatusBadge
              status={toneForHealth(page.data.inventory.state)}
              label={humanize(page.data.inventory.state)}
            />
          ) : undefined
        }
        meta={
          page.data ? (
            <FreshnessIndicator
              asOf={page.data.as_of}
              state={page.data.inventory.state === "stale" ? "stale" : "fresh"}
            />
          ) : undefined
        }
        actions={
          <Link
            href={`/clusters/${clusterId}`}
            className="rounded text-caption font-medium text-brand hover:underline"
          >
            Cluster detail
          </Link>
        }
      />

      {summary.data && summary.data.inventory.state !== "not_configured" ? (
        <div className="mb-5 grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <Panel data-testid="inventory-rollups">
            <PanelHeader
              title="What the last sweep found"
              description="Health composition per resource class, from the states the agent reported."
              level={2}
              meta={
                <>
                  <span data-tabular>
                    {summary.data.inventory.active_resources ?? 0} active
                  </span>
                  <span data-tabular className={summary.data.inventory.missing_resources ? "text-stale" : ""}>
                    {summary.data.inventory.missing_resources ?? 0} missing
                  </span>
                </>
              }
            />
            <div className="grid grid-cols-1 gap-x-4 gap-y-5 sm:grid-cols-2">
              {(
                [
                  ["Nodes", summary.data.nodes],
                  ["Namespaces", summary.data.namespaces],
                  ["Workloads", summary.data.workloads],
                  ["Pods", summary.data.pods],
                  ["Volume claims", summary.data.persistent_volume_claims],
                ] as const
              )
                .filter(([, rollup]) => rollup.total > 0)
                .map(([label, rollup]) => (
                  <div key={label} className="min-w-0">
                    <p className="mb-1.5 text-caption font-medium text-ink">{label}</p>
                    <Donut
                      size={104}
                      thickness={12}
                      label={`${label} by health`}
                      centerLabel={`${rollup.total}`}
                      slices={rollupSegments(rollup)}
                    />
                  </div>
                ))}
              {summary.data.pods.crashloop > 0 ||
              summary.data.pods.oom_killed > 0 ||
              summary.data.pods.restarts > 0 ? (
                <div className="sm:col-span-2">
                  <p className="mb-1.5 text-caption font-medium text-ink">Pod instability</p>
                  {/* These are counts of specific failure modes, not a
                      composition — they do not add up to the pod total, so
                      they get counters rather than a wedge each. */}
                  <ToneCounters
                    items={[
                      {
                        label: "crash-looping",
                        count: summary.data.pods.crashloop,
                        tone: "critical",
                      },
                      {
                        label: "OOM-killed",
                        count: summary.data.pods.oom_killed,
                        tone: "critical",
                      },
                      {
                        label: "restarts in window",
                        count: summary.data.pods.restarts,
                        tone: "warning",
                      },
                    ]}
                  />
                </div>
              ) : null}
            </div>
          </Panel>

          <SortedBarChart
            title="Resources by kind"
            question="Which kinds make up this cluster's inventory?"
            unit="count"
            status={kindCategories.length === 0 ? "empty" : "ready"}
            asOf={summary.data.as_of}
            freshness={summary.data.inventory.state === "stale" ? "stale" : "fresh"}
            categories={kindCategories}
            emptyDescription="The last sweep recorded no resources of any allowlisted kind."
          />
        </div>
      ) : null}

      <SectionHeader
        title="Resources"
        description="Secrets and ConfigMaps are outside the collected set and never appear here."
      />

      <Panel flush className="mt-3">
        <div className="border-b border-border px-4 py-3">
          <FilterBar
            summary={
              page.data
                ? `${rows.length} shown${nextCursor ? " (more available)" : ""}`
                : undefined
            }
            onReset={
              filtered
                ? () => {
                    setDraft("");
                    router.replace(`/clusters/${clusterId}/inventory`, { scroll: false });
                  }
                : undefined
            }
          >
            <Select
                data-testid="filter-kind"
                label="Kind"
                value={kind}
                placeholder="All kinds"
                options={INVENTORY_KINDS.map((option) => ({ value: option, label: option }))}
                onChange={(value) => setParam({ kind: value })}
              />
            <Select
                data-testid="filter-health"
                label="Health"
                value={health}
                placeholder="Any health"
                options={HEALTH_OPTIONS}
                onChange={(value) => setParam({ health: value })}
              />
            <Select
                data-testid="filter-lifecycle"
                label="Lifecycle"
                value={lifecycle}
                options={LIFECYCLE_OPTIONS}
                onChange={(value) => setParam({ lifecycle: value })}
              />
            <label className="flex items-center gap-2 text-caption text-ink-secondary">
              Name
              <input
                type="search"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                minLength={2}
                maxLength={64}
                placeholder="min 2 characters"
                data-testid="filter-search"
                className="h-9 w-40 rounded-control border border-border bg-surface px-2.5 text-body text-ink placeholder:text-ink-muted"
              />
            </label>
          </FilterBar>
          {lifecycle === "active" && (summary.data?.inventory.missing_resources ?? 0) > 0 ? (
            <p className="mt-2 text-micro text-stale">
              {summary.data?.inventory.missing_resources} missing resource(s) are hidden by the
              active-only filter.
            </p>
          ) : null}
        </div>

        {page.loading && !page.data ? (
          <div className="px-4 py-4">
            <LoadingSkeleton variant="table" rows={6} label="Loading inventory" />
          </div>
        ) : page.notFound ? (
          <div className="px-4 py-2">
            <NotFoundState description="This cluster's inventory does not exist in your authorized scope." />
          </div>
        ) : page.denied ? (
          <div className="px-4 py-2">
            <DeniedState />
          </div>
        ) : !page.data ? (
          <div className="px-4 py-2">
            <ErrorState
              description={page.error ?? undefined}
              correlationId={page.correlationId}
              onRetry={page.reload}
            />
          </div>
        ) : (
          <>
            <div data-testid="resource-rows">
              <DataTable
                caption="Inventory resources matching the current filters"
                rows={rows}
                columns={columns}
                density="compact"
                stickyHeader
                rowKey={(row) => row.id}
                emptyState={
                  <EmptyState
                    title="No resources match"
                    description="Nothing in the authorized inventory matches these filters."
                  />
                }
              />
            </div>
            {moreError ? (
              <div className="px-4 py-2">
                <ErrorState compact description={moreError} onRetry={loadMore} />
              </div>
            ) : null}
            {nextCursor ? (
              <div className="border-t border-border px-4 py-2">
                <Button
                  onClick={loadMore}
                  disabled={loadingMore}
                  size="compact"
                  data-testid="load-more"
                >
                  {loadingMore ? "Loading…" : "Load more"}
                </Button>
              </div>
            ) : null}
          </>
        )}
      </Panel>

      {page.data ? (
        <p className="mt-3 text-micro text-ink-muted">
          Cluster{" "}
          <CopyableIdentifier value={clusterId} label="cluster id" truncate={16} /> · inventory as
          of <Timestamp value={page.data.as_of} />
        </p>
      ) : null}
    </PageFrame>
  );
}

export default function ClusterInventoryPage() {
  return (
    <Suspense
      fallback={
        <PageFrame width="wide">
          <LoadingSkeleton variant="table" rows={6} label="Loading inventory" />
        </PageFrame>
      }
    >
      <InventoryInner />
    </Suspense>
  );
}
