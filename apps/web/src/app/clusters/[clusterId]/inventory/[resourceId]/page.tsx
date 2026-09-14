"use client";

/**
 * One inventory resource.
 *
 * Health leads — with the reason codes that produced it — then how the
 * resource was observed, the bounded spec/status summaries, its conditions,
 * and the allowlisted labels, annotations and owners last.
 */

import {
  Activity,
  Boxes,
  CalendarClock,
  ChevronRight,
  Eye,
  GitBranch,
  ListChecks,
  RefreshCw,
  Tag,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { useApi } from "@/components/catalog/primitives";
import {
  DefinitionGrid,
  IconBubble,
  PillLink,
  ScreenGate,
  StatTile,
  StateCard,
} from "@/components/clusters/primitives";
import {
  HealthBadge,
  InventoryStateBadge,
  formatUtc,
} from "@/components/inventory/primitives";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { RelativeTime } from "@/components/ui/identifiers";
import { humanize, toneForHealth } from "@/lib/design/status";
import type { InventoryResourceDetail } from "@/lib/inventory";

/** Allowlisted key/value pairs, as pills. */
function BoundedMap({
  entries,
  empty,
}: {
  entries: Record<string, string>;
  empty: string;
}) {
  const pairs = Object.entries(entries);
  if (pairs.length === 0) {
    return (
      <p className="rounded-[1rem] bg-surface-2 px-5 py-4 text-caption text-ink-muted">
        {empty}
      </p>
    );
  }
  return (
    <ul className="flex flex-wrap gap-2">
      {pairs.map(([key, value]) => (
        <li
          key={key}
          className="max-w-full rounded-full border border-border bg-surface-2 px-3 py-1.5 font-mono text-micro break-all text-ink"
        >
          <span className="text-ink-muted">{key}=</span>
          {value}
        </li>
      ))}
    </ul>
  );
}

/** True/False/Unknown, as the same status tones as everything else — a
 *  condition's `status` is a Kubernetes tri-state, not a status word, so it
 *  gets mapped once here rather than reusing `toneForHealth`. */
function conditionTone(status: string) {
  if (status === "True") return "success" as const;
  if (status === "False") return "critical" as const;
  return "unknown" as const;
}

/** A bounded summary map as a definition grid, or a designed absence. */
function SummaryPanel({
  title,
  icon,
  entries,
  empty,
}: {
  title: string;
  icon: LucideIcon;
  entries: Record<string, string | number | boolean | null>;
  empty: string;
}) {
  const pairs = Object.entries(entries);
  return (
    <Panel className="h-full">
      <div className="flex items-center gap-3">
        <IconBubble icon={icon} />
        <PanelHeader
          title={title}
          description="Bounded — allowlisted fields only"
        />
      </div>
      {pairs.length === 0 ? (
        <p className="rounded-[1rem] bg-surface-2 px-5 py-4 text-caption text-ink-muted">
          {empty}
        </p>
      ) : (
        <DefinitionGrid
          items={pairs.map(([key, value]) => ({
            label: key,
            value: (
              <span className="font-mono text-caption">{String(value)}</span>
            ),
          }))}
        />
      )}
    </Panel>
  );
}

export default function InventoryResourcePage() {
  const { clusterId, resourceId } = useParams<{
    clusterId: string;
    resourceId: string;
  }>();
  const [resource, retry] = useApi<InventoryResourceDetail>(
    `/v1/clusters/${clusterId}/inventory/resources/${resourceId}`,
  );

  return (
    <PageFrame>
      <ScreenGate
        value={resource}
        retry={retry}
        notFound={
          <StateCard
            testId="state-not-found"
            icon={Boxes}
            tone="not-applicable"
            title="Not found"
            description="This resource does not exist in your authorized scope."
            action={
              <PillLink
                LinkComponent={Link}
                href={`/clusters/${clusterId}/inventory`}
              >
                Back to inventory
              </PillLink>
            }
          />
        }
      >
        {(data) => (
          <>
            <nav
              aria-label="Breadcrumb"
              className="mb-3 flex min-w-0 flex-wrap items-center gap-1.5 text-micro text-ink-muted"
            >
              <Link href="/clusters" className="rounded hover:text-ink">
                Clusters
              </Link>
              <ChevronRight aria-hidden className="h-3 w-3" />
              <Link
                href={`/clusters/${clusterId}`}
                className="max-w-[12rem] truncate rounded font-mono hover:text-ink"
              >
                {clusterId}
              </Link>
              <ChevronRight aria-hidden className="h-3 w-3" />
              <Link
                href={`/clusters/${clusterId}/inventory`}
                className="rounded hover:text-ink"
              >
                Inventory
              </Link>
              <ChevronRight aria-hidden className="h-3 w-3" />
              <span className="font-mono text-ink-secondary">{data.kind}</span>
            </nav>

            <PageHeader
              title={<span className="break-all">{data.name}</span>}
              description={`${data.namespace ? `${data.namespace} · ` : ""}${data.kind}${
                data.api_group ? ` · ${data.api_group}/${data.api_version}` : ""
              }`}
              status={
                <>
                  <HealthBadge health={data.health} />
                  {data.lifecycle === "missing" ? (
                    <StatusBadge status="stale" label="missing" />
                  ) : null}
                </>
              }
            />

            <div className="page-grid mb-6">
              <StatTile
                icon={Activity}
                tone={toneForHealth(data.health)}
                label="Health"
                value={
                  <span className="block truncate text-[1.625rem] leading-none font-semibold tracking-[-0.02em] text-ink">
                    {humanize(data.health)}
                  </span>
                }
              >
                <p className="text-micro text-ink-muted">
                  {data.health_reasons.length === 0
                    ? "No adverse signals"
                    : `${data.health_reasons.length} reason${data.health_reasons.length === 1 ? "" : "s"} recorded`}
                </p>
              </StatTile>
              <StatTile
                icon={RefreshCw}
                tone={toneForHealth(data.inventory.state)}
                label="Inventory sweep"
                value={
                  <span className="block truncate text-[1.625rem] leading-none font-semibold tracking-[-0.02em] text-ink">
                    {humanize(data.inventory.state)}
                  </span>
                }
              >
                <p className="text-micro text-ink-muted">
                  {data.lifecycle === "missing"
                    ? "Gone from the cluster, still listed"
                    : "Present in the last sweep"}
                </p>
              </StatTile>
              <StatTile
                icon={CalendarClock}
                label="First seen"
                value={
                  <span className="block truncate text-[1.625rem] leading-none font-semibold tracking-[-0.02em] text-ink">
                    <RelativeTime value={data.first_seen_at} />
                  </span>
                }
              >
                <p className="font-mono text-micro text-ink-muted">
                  {formatUtc(data.first_seen_at)}
                </p>
              </StatTile>
              <StatTile
                icon={Eye}
                label="Last seen"
                value={
                  <span className="block truncate text-[1.625rem] leading-none font-semibold tracking-[-0.02em] text-ink">
                    <RelativeTime value={data.last_seen_at} />
                  </span>
                }
              >
                <p className="font-mono text-micro text-ink-muted">
                  {formatUtc(data.last_seen_at)}
                </p>
              </StatTile>
            </div>

            <div className="grid grid-cols-1 items-stretch gap-6 lg:grid-cols-2">
              <Panel data-testid="health-card" className="h-full">
                <PanelHeader
                  title="Health"
                  description="Reason codes behind the derived health"
                  actions={<HealthBadge health={data.health} />}
                />
                {data.health_reasons.length > 0 ? (
                  <ul className="space-y-2" data-testid="health-reasons">
                    {data.health_reasons.map((reason) => (
                      <li
                        key={reason}
                        className="flex items-center gap-3 rounded-[1rem] bg-surface-2 px-4 py-3"
                      >
                        <IconBubble
                          icon={Activity}
                          tone={toneForHealth(data.health)}
                          size="small"
                        />
                        <span className="font-mono text-caption text-ink">
                          {reason}
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <div className="flex items-center gap-3 rounded-[1rem] bg-surface-2 px-4 py-3">
                    <IconBubble icon={Activity} tone="success" size="small" />
                    <p className="text-caption text-ink-secondary">
                      No adverse signals in the observed status.
                    </p>
                  </div>
                )}
              </Panel>

              <Panel data-testid="observation-card" className="h-full">
                <PanelHeader
                  title="Observation"
                  description="Where and when this was seen"
                />
                <DefinitionGrid
                  items={[
                    {
                      label: "Observed at (source)",
                      value: (
                        <span className="font-mono text-caption">
                          {formatUtc(data.observed_at)}
                        </span>
                      ),
                    },
                    {
                      label: "Source",
                      value: (
                        <span className="font-mono text-caption">
                          {data.provenance.source}
                        </span>
                      ),
                    },
                    {
                      label: "Inventory sweep",
                      value: (
                        <InventoryStateBadge state={data.inventory.state} />
                      ),
                    },
                    {
                      label: "UID",
                      value: (
                        <span className="font-mono text-micro break-all">
                          {data.uid}
                        </span>
                      ),
                    },
                  ]}
                />
              </Panel>
            </div>

            <div className="mt-6 grid grid-cols-1 items-stretch gap-6 lg:grid-cols-2">
              <SummaryPanel
                title="Spec summary"
                icon={ListChecks}
                entries={data.spec_summary}
                empty="No summarized spec fields."
              />
              <SummaryPanel
                title="Status summary"
                icon={Activity}
                entries={data.status_summary}
                empty="No summarized status fields."
              />
            </div>

            <Panel flush className="mt-6">
              <PanelHeader
                flush
                title="Conditions"
                meta={
                  <span data-tabular>{data.conditions.length} reported</span>
                }
              />
              {data.conditions.length === 0 ? (
                <StateCard
                  compact
                  testId="conditions-empty"
                  icon={ListChecks}
                  title="No conditions"
                  description="The source object carries no status conditions."
                />
              ) : (
                <ul className="divide-y divide-border">
                  {data.conditions.map((condition) => (
                    <li
                      key={condition.type}
                      className="flex flex-wrap items-center gap-x-4 gap-y-2 px-7 py-4 transition-colors hover:bg-surface-hover"
                    >
                      <IconBubble
                        icon={ListChecks}
                        tone={conditionTone(condition.status)}
                      />
                      <div className="min-w-0 flex-1">
                        <p className="font-mono text-caption font-semibold text-ink">
                          {condition.type}
                        </p>
                        {condition.reason || condition.message ? (
                          <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-caption text-ink-muted">
                            {condition.reason ? (
                              <span className="font-mono text-micro text-ink-secondary">
                                {condition.reason}
                              </span>
                            ) : null}
                            {condition.message ? (
                              <span>{condition.message}</span>
                            ) : null}
                          </p>
                        ) : null}
                      </div>
                      <StatusBadge
                        status={conditionTone(condition.status)}
                        label={condition.status}
                        size="compact"
                      />
                    </li>
                  ))}
                </ul>
              )}
            </Panel>

            <div className="mt-6 grid grid-cols-1 items-stretch gap-6 lg:grid-cols-2">
              <Panel className="h-full">
                <div className="flex items-center gap-3">
                  <IconBubble icon={Tag} />
                  <PanelHeader title="Labels" description="Allowlisted" />
                </div>
                <BoundedMap entries={data.labels} empty="None recorded." />
              </Panel>
              <Panel className="h-full">
                <div className="flex items-center gap-3">
                  <IconBubble icon={Tag} />
                  <PanelHeader title="Annotations" description="Allowlisted" />
                </div>
                <BoundedMap entries={data.annotations} empty="None recorded." />
              </Panel>
            </div>

            {data.owners.length > 0 ? (
              <Panel flush className="mt-6">
                <PanelHeader flush title="Owners" />
                <ul className="divide-y divide-border">
                  {data.owners.map((owner) => (
                    <li
                      key={owner.uid}
                      className="flex flex-wrap items-center gap-x-4 gap-y-1 px-7 py-4"
                    >
                      <IconBubble icon={GitBranch} />
                      <span className="min-w-0 flex-1">
                        <span className="block font-mono text-caption font-semibold text-ink">
                          {owner.kind}/{owner.name}
                        </span>
                        <span className="mt-0.5 block font-mono text-micro break-all text-ink-muted">
                          {owner.uid}
                        </span>
                      </span>
                    </li>
                  ))}
                </ul>
              </Panel>
            ) : null}
          </>
        )}
      </ScreenGate>
    </PageFrame>
  );
}
