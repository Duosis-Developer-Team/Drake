"use client";

/**
 * Deployment list.
 *
 * One row per observed workload revision: what is running, how much Drake
 * can prove about where it came from, how the rollout went, and how health
 * looked afterwards.
 */

import Link from "next/link";
import { Suspense, useState } from "react";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";

import { useApi } from "@/components/catalog/primitives";
import {
  EvidenceBadge,
  RolloutBadge,
  ShortRef,
  VerdictBadge,
} from "@/components/deployments/primitives";
import { StackedBar } from "@/components/charts/visuals";
import { DataState } from "@/components/state/DataState";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { FilterBar, Select } from "@/components/ui/controls";
import {
  deploymentListPath,
  formatDuration,
  type DeploymentPage,
  type DeploymentRow,
  type EvidenceState,
  type RolloutState,
} from "@/lib/deployments";

const ROLLOUT_STATES: RolloutState[] = [
  "pending",
  "progressing",
  "healthy",
  "degraded",
  "failed",
  "stalled",
  "unknown",
];
const EVIDENCE_STATES: EvidenceState[] = ["verified", "partial", "unverified", "conflict"];
const WINDOWS = ["24h", "7d", "30d"];

/**
 * One deployment, as a card: workload identity leads, rollout/evidence
 * state read as chips beside it, and the provenance chain (digest, commit,
 * ready count, duration, post-rollout health) trails as a scannable strip.
 */
function DeploymentRowView({ row }: { row: DeploymentRow }) {
  return (
    <li
      data-testid={`deployment-row-${row.workload_name}`}
      className="flex flex-wrap items-start gap-4 px-4 py-4 transition-colors hover:bg-surface-hover"
    >
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <Link
            href={`/deployments/${row.id}`}
            className="text-body font-semibold text-ink hover:underline"
          >
            {row.workload_name}
          </Link>
          <span className="font-mono text-caption text-ink-muted">#{row.revision}</span>
          <RolloutBadge state={row.rollout_state} />
          <EvidenceBadge state={row.evidence_state} />
        </div>
        <span className="mt-1 block font-mono text-micro text-ink-muted">
          {row.cluster.cluster_ref}/{row.namespace} · {row.workload_kind}
        </span>
        {row.project_key ? (
          <span className="block font-mono text-micro text-ink-muted">
            {row.project_key}/{row.environment_key}/{row.service_key}
          </span>
        ) : (
          <span className="block text-micro italic text-ink-muted">not bound to a service</span>
        )}
        {row.rollout_reason ? (
          <span className="mt-1 block text-caption text-ink-secondary">{row.rollout_reason}</span>
        ) : null}
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <ShortRef value={row.short_digest} label="image digest" />
          <ShortRef value={row.short_commit} label="commit" />
        </div>
      </div>
      <div className="flex shrink-0 flex-col items-end gap-1.5 text-right">
        <span className="font-mono text-body text-ink" data-tabular>
          {row.replicas.ready ?? "—"} / {row.replicas.desired ?? "—"}
        </span>
        <span className="text-micro text-ink-muted">
          {formatDuration(row.rollout_started_at, row.rollout_completed_at)}
        </span>
        {row.health_comparison ? (
          <VerdictBadge verdict={row.health_comparison.verdict} />
        ) : (
          <span className="text-micro italic text-ink-muted">not compared yet</span>
        )}
      </div>
    </li>
  );
}

function DeploymentTable() {
  const [rolloutState, setRolloutState] = useState<RolloutState | "">("");
  const [evidenceState, setEvidenceState] = useState<EvidenceState | "">("");
  const [startedWithin, setStartedWithin] = useState("");

  const [page, retry] = useApi<DeploymentPage>(
    deploymentListPath({
      rolloutState: rolloutState || undefined,
      evidenceState: evidenceState || undefined,
      startedWithin: startedWithin || undefined,
    }),
  );

  return (
    <div className="space-y-4">
      <Panel
        data-testid="deployment-filters"
        className="motion-safe:animate-[fade-in_360ms_var(--ease-entrance)_backwards]"
      >
        <div role="group" aria-label="Filters">
          <FilterBar>
            <Select
              label="Rollout"
              value={rolloutState}
              placeholder="Any"
              options={ROLLOUT_STATES.map((value) => ({ value, label: value }))}
              onChange={(value) => setRolloutState(value as RolloutState | "")}
            />
            <Select
              label="Evidence"
              value={evidenceState}
              placeholder="Any"
              options={EVIDENCE_STATES.map((value) => ({ value, label: value }))}
              onChange={(value) => setEvidenceState(value as EvidenceState | "")}
            />
            <Select
              label="Started within"
              value={startedWithin}
              placeholder="Any time"
              options={WINDOWS.map((value) => ({ value, label: value }))}
              onChange={setStartedWithin}
            />
          </FilterBar>
        </div>
      </Panel>

      {page.state === "loading" ? <DataState kind="loading" /> : null}
      {page.state === "error" ? (
        <Panel>
          {page.notFound ? (
            <DataState
              kind="permission-denied"
              description="Your current scope does not include deployments."
            />
          ) : (
            <DataState kind="error" description={page.message} onRetry={retry} />
          )}
        </Panel>
      ) : null}
      {page.state === "ready" && page.data.items.length === 0 ? (
        <Panel>
          <DataState
            kind="empty"
            title="No deployments"
            description="Nothing matches these filters in your authorized scope. Drake records a revision when a cluster agent reports a workload generation."
          />
        </Panel>
      ) : null}
      {page.state === "ready" && page.data.items.length > 0 ? (
        <Panel
          data-testid="deployment-summary"
          className="motion-safe:animate-[fade-in_400ms_var(--ease-entrance)_backwards] [animation-delay:60ms]"
        >
          <PanelHeader title="In this view" />
          {/* From the rows on screen, labelled as such. `unverified` is its
              own segment and deliberately not red: an absence of evidence is
              not a failed rollout. */}
          <StackedBar
            label="Rollouts on this page by state"
            segments={[
              {
                name: "Failed",
                value: page.data.items.filter((row) => row.rollout_state === "failed").length,
                tone: "critical",
              },
              {
                name: "Degraded",
                value: page.data.items.filter((row) => row.rollout_state === "degraded").length,
                tone: "warning",
              },
              {
                name: "Stalled",
                value: page.data.items.filter((row) => row.rollout_state === "stalled").length,
                tone: "warning",
              },
              {
                name: "Progressing",
                value: page.data.items.filter((row) => row.rollout_state === "progressing").length,
                tone: "info",
              },
              {
                name: "Healthy",
                value: page.data.items.filter((row) => row.rollout_state === "healthy").length,
                tone: "success",
              },
              {
                name: "Unknown",
                value: page.data.items.filter((row) =>
                  ["unknown", "pending"].includes(row.rollout_state),
                ).length,
                tone: "unknown",
              },
            ]}
          />
        </Panel>
      ) : null}

      {page.state === "ready" && page.data.items.length > 0 ? (
        <Panel
          flush
          className="motion-safe:animate-[fade-in_440ms_var(--ease-entrance)_backwards] [animation-delay:100ms]"
        >
          <ul className="divide-y divide-border" data-testid="deployment-table">
            {page.data.items.map((row) => (
              <DeploymentRowView key={row.id} row={row} />
            ))}
          </ul>
          <p className="border-t border-border px-4 py-3 text-micro text-ink-muted">
            Showing {page.data.items.length} of {page.data.total} deployments in your
            authorized scope.
          </p>
        </Panel>
      ) : null}
    </div>
  );
}

export default function DeploymentsPage() {
  return (
    <PageFrame>
      <PageHeader
        title="Deployments"
        description="One row per observed workload revision. Evidence says how much of the commit → workflow → digest → workload chain Drake actually saw; anything less than the whole chain is never shown as verified."
      />
      <div className="space-y-5">
        <Suspense fallback={<DataState kind="loading" />}>
          <DeploymentTable />
        </Suspense>
      </div>
    </PageFrame>
  );
}
