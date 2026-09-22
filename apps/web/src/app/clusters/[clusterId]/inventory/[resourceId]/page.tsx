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
} from "@/components/inventory/primitives";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { RelativeTime } from "@/components/ui/identifiers";
import { humanize, toneForHealth } from "@/lib/design/status";
import { useFormat, useT } from "@/lib/i18n";
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
  const t = useT("clusters");
  const pairs = Object.entries(entries);
  return (
    <Panel className="h-full">
      <div className="flex items-center gap-3">
        <IconBubble icon={icon} />
        <PanelHeader title={title} description={t("resource.summary.bounded")} />
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
  const t = useT("clusters");
  const tc = useT("common");
  const fmt = useFormat();
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
            title={t("shared.notFoundTitle")}
            description={t("shared.notFoundDescription")}
            action={
              <PillLink
                LinkComponent={Link}
                href={`/clusters/${clusterId}/inventory`}
              >
                {t("resource.backToInventory")}
              </PillLink>
            }
          />
        }
      >
        {(data) => (
          <>
            <nav
              aria-label={t("breadcrumb.label")}
              className="mb-3 flex min-w-0 flex-wrap items-center gap-1.5 text-micro text-ink-muted"
            >
              <Link href="/clusters" className="rounded hover:text-ink">
                {t("breadcrumb.clusters")}
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
                {t("breadcrumb.inventory")}
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
                    <StatusBadge status="stale" label={t("resource.missing")} />
                  ) : null}
                </>
              }
            />

            <div className="page-grid mb-6">
              <StatTile
                icon={Activity}
                tone={toneForHealth(data.health)}
                label={tc("field.health")}
                value={
                  <span className="block truncate text-[1.625rem] leading-none font-semibold tracking-[-0.02em] text-ink">
                    {t.dyn("enum.health", data.health, humanize(data.health))}
                  </span>
                }
              >
                <p className="text-micro text-ink-muted">
                  {data.health_reasons.length === 0
                    ? t("resource.tile.noAdverseSignals")
                    : t("resource.tile.reasonsRecorded", { count: data.health_reasons.length })}
                </p>
              </StatTile>
              <StatTile
                icon={RefreshCw}
                tone={toneForHealth(data.inventory.state)}
                label={t("shared.inventorySweep")}
                value={
                  <span className="block truncate text-[1.625rem] leading-none font-semibold tracking-[-0.02em] text-ink">
                    {t.dyn("enum.inventory", data.inventory.state, humanize(data.inventory.state))}
                  </span>
                }
              >
                <p className="text-micro text-ink-muted">
                  {data.lifecycle === "missing"
                    ? t("resource.tile.gone")
                    : t("resource.tile.present")}
                </p>
              </StatTile>
              <StatTile
                icon={CalendarClock}
                label={t("resource.tile.firstSeen")}
                value={
                  <span className="block truncate text-[1.625rem] leading-none font-semibold tracking-[-0.02em] text-ink">
                    <RelativeTime value={data.first_seen_at} />
                  </span>
                }
              >
                <p className="font-mono text-micro text-ink-muted">
                  {fmt.utc(data.first_seen_at)}
                </p>
              </StatTile>
              <StatTile
                icon={Eye}
                label={t("resource.tile.lastSeen")}
                value={
                  <span className="block truncate text-[1.625rem] leading-none font-semibold tracking-[-0.02em] text-ink">
                    <RelativeTime value={data.last_seen_at} />
                  </span>
                }
              >
                <p className="font-mono text-micro text-ink-muted">
                  {fmt.utc(data.last_seen_at)}
                </p>
              </StatTile>
            </div>

            <div className="grid grid-cols-1 items-stretch gap-6 lg:grid-cols-2">
              <Panel data-testid="health-card" className="h-full">
                <PanelHeader
                  title={tc("field.health")}
                  description={t("resource.health.description")}
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
                      {t("resource.health.noSignals")}
                    </p>
                  </div>
                )}
              </Panel>

              <Panel data-testid="observation-card" className="h-full">
                <PanelHeader
                  title={t("resource.observation.title")}
                  description={t("resource.observation.description")}
                />
                <DefinitionGrid
                  items={[
                    {
                      label: t("resource.observation.observedAt"),
                      value: (
                        <span className="font-mono text-caption">
                          {fmt.utc(data.observed_at)}
                        </span>
                      ),
                    },
                    {
                      label: tc("field.source"),
                      value: (
                        <span className="font-mono text-caption">
                          {data.provenance.source}
                        </span>
                      ),
                    },
                    {
                      label: t("shared.inventorySweep"),
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
                title={t("resource.summary.spec")}
                icon={ListChecks}
                entries={data.spec_summary}
                empty={t("resource.summary.noSpec")}
              />
              <SummaryPanel
                title={t("resource.summary.status")}
                icon={Activity}
                entries={data.status_summary}
                empty={t("resource.summary.noStatus")}
              />
            </div>

            <Panel flush className="mt-6">
              <PanelHeader
                flush
                title={t("resource.conditions.title")}
                meta={
                  <span data-tabular>
                    {t("resource.conditions.reported", { count: data.conditions.length })}
                  </span>
                }
              />
              {data.conditions.length === 0 ? (
                <StateCard
                  compact
                  testId="conditions-empty"
                  icon={ListChecks}
                  title={t("resource.conditions.emptyTitle")}
                  description={t("resource.conditions.emptyDescription")}
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
                  <PanelHeader title={tc("field.labels")} description={t("resource.allowlisted")} />
                </div>
                <BoundedMap entries={data.labels} empty={t("resource.noneRecorded")} />
              </Panel>
              <Panel className="h-full">
                <div className="flex items-center gap-3">
                  <IconBubble icon={Tag} />
                  <PanelHeader title={tc("field.annotations")} description={t("resource.allowlisted")} />
                </div>
                <BoundedMap entries={data.annotations} empty={t("resource.noneRecorded")} />
              </Panel>
            </div>

            {data.owners.length > 0 ? (
              <Panel flush className="mt-6">
                <PanelHeader flush title={t("resource.owners")} />
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
