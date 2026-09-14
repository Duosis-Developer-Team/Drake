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

import {
  Boxes,
  Container,
  Database,
  EyeOff,
  FolderTree,
  RefreshCw,
  SearchX,
  Server,
  Shapes,
  Workflow,
  X,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";

import { SortedBarChart } from "@/components/charts/CategoryCharts";
import { ToneCounters } from "@/components/charts/visuals";
import {
  IconBubble,
  PillLink,
  SegmentBar,
  StatTile,
  StateCard,
} from "@/components/clusters/primitives";
import { PillSearch, PillSelect } from "@/components/inventory/controls";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";
import { Panel, PanelHeader, SectionHeader } from "@/components/ui/Panel";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { Button } from "@/components/ui/controls";
import {
  CopyableIdentifier,
  FreshnessIndicator,
  Timestamp,
} from "@/components/ui/identifiers";
import {
  DeniedState,
  ErrorState,
  LoadingSkeleton,
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

// Events are overwhelmingly Normal — 5717 of 5983 in the production
// cluster — so listing them unfiltered buries the ones worth reading.
const EVENT_TYPE_OPTIONS = [
  { value: "", label: "All events" },
  { value: "Warning", label: "Warnings only" },
  { value: "Normal", label: "Normal only" },
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
  eventType: string;
  cursor?: string;
}): string {
  const params = new URLSearchParams();
  if (filters.kind) params.set("kind", filters.kind);
  // Only meaningful for events, and only sent when it is: attaching it to
  // any other kind would filter on a field those rows do not have, which
  // returns nothing and reads like "no inventory".
  if (filters.kind === "Event" && filters.eventType) {
    params.set("event_type", filters.eventType);
  }
  if (filters.health) params.set("health", filters.health);
  if (filters.lifecycle && filters.lifecycle !== "active") {
    params.set("lifecycle", filters.lifecycle);
  }
  if (filters.search.trim().length >= 2)
    params.set("search", filters.search.trim());
  if (filters.cursor) params.set("cursor", filters.cursor);
  const encoded = params.toString();
  return encoded ? `?${encoded}` : "";
}

function rollupSegments(rollup: HealthRollup) {
  return [
    {
      key: "healthy",
      label: "Healthy",
      value: rollup.healthy,
      tone: "success" as const,
    },
    {
      key: "degraded",
      label: "Degraded",
      value: rollup.degraded,
      tone: "warning" as const,
    },
    {
      key: "unhealthy",
      label: "Unhealthy",
      value: rollup.unhealthy,
      tone: "critical" as const,
    },
    {
      key: "unknown",
      label: "Unknown",
      value: rollup.unknown,
      tone: "unknown" as const,
    },
  ];
}

const HEADER_CELL =
  "h-11 px-4 text-left text-micro font-medium tracking-[0.08em] whitespace-nowrap text-ink-muted uppercase first:pl-7 last:pr-7";
const BODY_CELL = "h-14 px-4 align-middle first:pl-7 last:pr-7";

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
  const eventType = params.get("event_type") ?? "";
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

  const query = buildQuery({ kind, health, lifecycle, search, eventType });
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
        eventType,
        cursor: nextCursor,
      })}`,
    )
      .then((body) => {
        setExtraRows((rows) => [...rows, ...body.resources]);
        setNextCursor(body.next_cursor);
      })
      .catch((error: unknown) => {
        setMoreError(
          error instanceof ApiError ? error.message : "request failed",
        );
      })
      .finally(() => setLoadingMore(false));
  }, [clusterId, kind, health, lifecycle, search, eventType, nextCursor]);

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

  const inventoryState = summary.data?.inventory.state;
  const missingCount = summary.data?.inventory.missing_resources ?? 0;
  const topKinds = [...kindCategories]
    .sort((a, b) => b.value - a.value)
    .slice(0, 3);

  return (
    <PageFrame width="wide">
      <PageHeader
        title="Inventory"
        description="Observed Kubernetes resources with derived health — a resource that disappears stays listed as missing."
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
          <PillLink
            LinkComponent={Link}
            href={`/clusters/${clusterId}`}
            icon={Server}
          >
            Cluster detail
          </PillLink>
        }
      />

      {summary.data ? (
        <div className="page-grid mb-6">
          <StatTile
            icon={Boxes}
            label="Active resources"
            value={summary.data.inventory.active_resources ?? 0}
            suffix="observed"
          >
            <SegmentBar
              label="Resources by lifecycle"
              segments={[
                {
                  key: "active",
                  label: "Active",
                  value: summary.data.inventory.active_resources ?? 0,
                  tone: "info",
                },
                {
                  key: "missing",
                  label: "Missing",
                  value: missingCount,
                  tone: "stale",
                },
              ]}
            />
          </StatTile>
          <StatTile
            icon={EyeOff}
            tone={missingCount > 0 ? "stale" : null}
            label="Missing"
            value={missingCount}
            suffix="gone from the cluster"
          >
            <p className="text-micro text-ink-muted">
              {missingCount > 0 && lifecycle === "active"
                ? "Hidden by the active-only filter below"
                : "Kept in the list, never silently dropped"}
            </p>
          </StatTile>
          <StatTile
            icon={Shapes}
            label="Kinds observed"
            value={kindCategories.length}
            suffix="kinds"
          >
            {topKinds.length > 0 ? (
              <ul className="flex flex-wrap gap-1.5">
                {topKinds.map((entry) => (
                  <li
                    key={entry.name}
                    className="inline-flex items-center gap-1.5 rounded-full bg-surface-2 px-2.5 py-1 text-micro text-ink-secondary"
                  >
                    <span className="font-mono">{entry.name}</span>
                    <span data-tabular className="font-semibold text-ink">
                      {entry.value}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-micro text-ink-muted">
                No allowlisted kind recorded yet
              </p>
            )}
          </StatTile>
          <StatTile
            icon={RefreshCw}
            tone={inventoryState ? toneForHealth(inventoryState) : null}
            label="Inventory sweep"
            value={
              <span className="block truncate text-[1.625rem] leading-none font-semibold tracking-[-0.02em] text-ink">
                {humanize(inventoryState)}
              </span>
            }
          >
            <FreshnessIndicator
              asOf={summary.data.as_of}
              state={inventoryState === "stale" ? "stale" : "fresh"}
            />
          </StatTile>
        </div>
      ) : null}

      {summary.data && summary.data.inventory.state !== "not_configured" ? (
        <div className="mb-6 grid grid-cols-1 items-stretch gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <Panel data-testid="inventory-rollups" className="h-full">
            <PanelHeader
              title="What the last sweep found"
              description="Health composition per resource class, as the agent reported it."
              level={2}
            />
            <ul className="space-y-5">
              {(
                [
                  ["Nodes", Server, summary.data.nodes],
                  ["Namespaces", FolderTree, summary.data.namespaces],
                  ["Workloads", Workflow, summary.data.workloads],
                  ["Pods", Container, summary.data.pods],
                  [
                    "Volume claims",
                    Database,
                    summary.data.persistent_volume_claims,
                  ],
                ] as const
              )
                .filter(([, , rollup]) => rollup.total > 0)
                .map(([label, icon, rollup]) => (
                  <li key={label} className="flex items-center gap-4">
                    <IconBubble icon={icon} size="small" />
                    <div className="min-w-0 flex-1">
                      <p className="mb-2 flex items-baseline justify-between gap-2 text-caption">
                        <span className="font-medium text-ink">{label}</span>
                        <span data-tabular className="font-semibold text-ink">
                          {rollup.total}
                        </span>
                      </p>
                      <SegmentBar
                        label={`${label} by health`}
                        height="h-2"
                        segments={rollupSegments(rollup)}
                      />
                    </div>
                  </li>
                ))}
            </ul>
            {summary.data.pods.crashloop > 0 ||
            summary.data.pods.oom_killed > 0 ||
            summary.data.pods.restarts > 0 ? (
              <div className="mt-auto rounded-[1rem] bg-surface-2 px-5 py-4">
                <p className="mb-3 text-micro tracking-[0.08em] text-ink-muted uppercase">
                  Pod instability
                </p>
                {/* Counts of specific failure modes, not a composition — they
                    do not add up to the pod total, so counters, not wedges. */}
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
          </Panel>

          <div className="h-full min-w-0 [&>*]:h-full">
            <SortedBarChart
              title="Resources by kind"
              question="Which kinds make up this cluster's inventory?"
              unit="count"
              status={kindCategories.length === 0 ? "empty" : "ready"}
              asOf={summary.data.as_of}
              freshness={
                summary.data.inventory.state === "stale" ? "stale" : "fresh"
              }
              categories={kindCategories}
              emptyDescription="The last sweep recorded no resources of any allowlisted kind."
            />
          </div>
        </div>
      ) : null}

      <div className="mt-10">
        <SectionHeader
          title="Resources"
          description="Secrets and ConfigMaps are outside the collected set and never appear here."
        />
      </div>

      {/* One card-less toolbar of pills; the count and the reset live on it. */}
      <div
        className="mt-4 mb-4 flex flex-wrap items-center gap-2.5"
        data-testid="filter-bar"
      >
        <PillSelect
          data-testid="filter-kind"
          label="Kind"
          value={kind}
          placeholder="All kinds"
          active={Boolean(kind)}
          options={INVENTORY_KINDS.map((option) => ({
            value: option,
            label: option,
          }))}
          // Leaving the URL is what people paste, so a filter that no
          // longer applies must not ride along in it: switching away
          // from Event drops the event type rather than hiding it.
          onChange={(value) =>
            setParam(
              value === "Event"
                ? { kind: value }
                : { kind: value, event_type: "" },
            )
          }
        />
        <PillSelect
          data-testid="filter-health"
          label="Health"
          value={health}
          placeholder="Any health"
          active={Boolean(health)}
          options={HEALTH_OPTIONS}
          onChange={(value) => setParam({ health: value })}
        />
        {kind === "Event" ? (
          <PillSelect
            data-testid="filter-event-type"
            label="Event type"
            value={eventType}
            active={Boolean(eventType)}
            options={EVENT_TYPE_OPTIONS}
            onChange={(value) => setParam({ event_type: value })}
          />
        ) : null}
        <PillSelect
          data-testid="filter-lifecycle"
          label="Lifecycle"
          value={lifecycle}
          active={lifecycle !== "active"}
          options={LIFECYCLE_OPTIONS}
          onChange={(value) => setParam({ lifecycle: value })}
        />
        <PillSearch
          data-testid="filter-search"
          label="Name"
          value={draft}
          onChange={setDraft}
          placeholder="Name (min 2 characters)"
        />
        {filtered ? (
          <button
            type="button"
            onClick={() => {
              setDraft("");
              router.replace(`/clusters/${clusterId}/inventory`, {
                scroll: false,
              });
            }}
            className="inline-flex h-10 items-center gap-1.5 rounded-full px-3.5 text-caption font-medium text-ink-secondary transition-colors hover:bg-surface-hover hover:text-ink"
          >
            <X aria-hidden className="h-4 w-4" />
            Clear filters
          </button>
        ) : null}
        <span className="ml-auto flex flex-wrap items-center gap-2">
          {lifecycle === "active" && missingCount > 0 ? (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-stale-soft px-3 py-1.5 text-micro text-stale">
              <EyeOff aria-hidden className="h-3.5 w-3.5" />
              {missingCount} missing resource(s) are hidden by the active-only
              filter.
            </span>
          ) : null}
          {page.data ? (
            <span
              data-tabular
              className="rounded-full bg-surface px-3 py-1.5 text-micro font-medium text-ink-secondary"
            >
              {`${rows.length} shown${nextCursor ? " (more available)" : ""}`}
            </span>
          ) : null}
        </span>
      </div>

      <Panel flush>
        {page.loading && !page.data ? (
          <div className="px-7 py-5">
            <LoadingSkeleton
              variant="table"
              rows={6}
              label="Loading inventory"
            />
          </div>
        ) : page.notFound ? (
          <StateCard
            testId="state-not-found"
            icon={Server}
            tone="not-applicable"
            title="Not found"
            description="This cluster's inventory does not exist in your authorized scope."
          />
        ) : page.denied ? (
          <div className="px-7 py-4">
            <DeniedState />
          </div>
        ) : !page.data ? (
          <div className="px-7 py-4">
            <ErrorState
              description={page.error ?? undefined}
              correlationId={page.correlationId}
              onRetry={page.reload}
            />
          </div>
        ) : (
          <>
            <div data-testid="resource-rows">
              {rows.length === 0 ? (
                <StateCard
                  testId="state-empty"
                  icon={SearchX}
                  title="No resources match"
                  description="Nothing in the authorized inventory matches these filters."
                  action={
                    filtered ? (
                      <Button
                        size="compact"
                        onClick={() => {
                          setDraft("");
                          router.replace(`/clusters/${clusterId}/inventory`, {
                            scroll: false,
                          });
                        }}
                      >
                        Clear filters
                      </Button>
                    ) : undefined
                  }
                />
              ) : (
                <div className="w-full min-w-0 max-w-full overflow-x-auto [contain:paint]">
                  <table
                    className="w-full border-collapse text-body"
                    data-tabular
                  >
                    <caption className="sr-only">
                      Inventory resources matching the current filters
                    </caption>
                    <thead className="sticky top-0 z-10 bg-surface-2">
                      <tr className="border-b border-border">
                        <th scope="col" className={HEADER_CELL}>
                          Name
                        </th>
                        <th scope="col" className={HEADER_CELL}>
                          Kind
                        </th>
                        <th scope="col" className={HEADER_CELL}>
                          Namespace
                        </th>
                        <th scope="col" className={HEADER_CELL}>
                          Health
                        </th>
                        <th scope="col" className={HEADER_CELL}>
                          Lifecycle
                        </th>
                        <th
                          scope="col"
                          className={`${HEADER_CELL} hidden text-right lg:table-cell`}
                        >
                          Observed
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((row) => (
                        <tr
                          key={row.id}
                          className="border-b border-border transition-colors last:border-b-0 hover:bg-surface-hover"
                        >
                          <td className={BODY_CELL}>
                            <Link
                              href={`/clusters/${clusterId}/inventory/${row.id}`}
                              className="rounded font-mono text-caption font-semibold break-all text-ink hover:text-brand"
                            >
                              {row.name}
                            </Link>
                          </td>
                          <td className={BODY_CELL}>
                            <span className="inline-flex rounded-full bg-surface-2 px-2.5 py-1 font-mono text-micro text-ink-secondary">
                              {row.kind}
                            </span>
                          </td>
                          <td
                            className={`${BODY_CELL} font-mono text-micro text-ink-secondary`}
                          >
                            {row.namespace ?? (
                              <span className="text-ink-muted">—</span>
                            )}
                          </td>
                          <td className={BODY_CELL}>
                            <StatusBadge
                              status={toneForHealth(row.health)}
                              label={humanize(row.health)}
                              size="compact"
                            />
                          </td>
                          <td className={BODY_CELL}>
                            {row.lifecycle === "missing" ? (
                              <StatusBadge
                                status="stale"
                                label="Missing"
                                size="compact"
                              />
                            ) : (
                              <span className="text-caption text-ink-secondary">
                                Active
                              </span>
                            )}
                          </td>
                          <td
                            className={`${BODY_CELL} hidden text-right lg:table-cell`}
                          >
                            <Timestamp
                              value={row.observed_at}
                              className="text-ink-muted"
                            />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
            {moreError ? (
              <div className="px-7 py-2">
                <ErrorState
                  compact
                  description={moreError}
                  onRetry={loadMore}
                />
              </div>
            ) : null}
            {nextCursor ? (
              <div className="flex justify-center border-t border-border px-7 py-4">
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
        <p className="mt-4 text-micro text-ink-muted">
          Cluster{" "}
          <CopyableIdentifier
            value={clusterId}
            label="cluster id"
            truncate={16}
          />{" "}
          · inventory as of <Timestamp value={page.data.as_of} />
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
