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

import { useApi } from "@/components/catalog/primitives";
import {
  EvidenceBadge,
  RolloutBadge,
  ShortRef,
  VerdictBadge,
} from "@/components/deployments/primitives";
import { DataState } from "@/components/state/DataState";
import { Card } from "@/components/ui/Card";
import {
  deploymentListPath,
  formatDuration,
  type DeploymentPage,
  type DeploymentRow,
  type EvidenceState,
  type RolloutState,
} from "@/lib/deployments";

const SELECT_CLASS =
  "rounded-lg border border-border bg-surface px-2.5 py-1.5 text-xs text-ink";

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

function DeploymentRowView({ row }: { row: DeploymentRow }) {
  return (
    <tr
      className="border-t border-border align-top"
      data-testid={`deployment-row-${row.workload_name}`}
    >
      <td className="py-2.5 pr-3">
        <div className="flex flex-col gap-1">
          <Link
            href={`/deployments/${row.id}`}
            className="text-sm font-medium text-ink hover:underline"
          >
            {row.workload_name}
          </Link>
          <span className="font-mono text-[11px] text-ink-muted">
            {row.cluster.cluster_ref}/{row.namespace} · {row.workload_kind}
          </span>
          {row.project_key ? (
            <span className="font-mono text-[11px] text-ink-muted">
              {row.project_key}/{row.environment_key}/{row.service_key}
            </span>
          ) : (
            <span className="text-[11px] italic text-ink-muted">not bound to a service</span>
          )}
        </div>
      </td>
      <td className="py-2.5 pr-3 whitespace-nowrap font-mono text-xs text-ink">
        #{row.revision}
      </td>
      <td className="py-2.5 pr-3">
        <RolloutBadge state={row.rollout_state} />
        {row.rollout_reason ? (
          <span className="mt-1 block text-[11px] text-ink-muted">{row.rollout_reason}</span>
        ) : null}
      </td>
      <td className="py-2.5 pr-3">
        <EvidenceBadge state={row.evidence_state} />
      </td>
      <td className="py-2.5 pr-3">
        <div className="flex flex-col gap-0.5">
          <ShortRef value={row.short_digest} label="image digest" />
          <ShortRef value={row.short_commit} label="commit" />
        </div>
      </td>
      <td className="py-2.5 pr-3 whitespace-nowrap font-mono text-xs text-ink">
        {row.replicas.ready ?? "—"} / {row.replicas.desired ?? "—"}
      </td>
      <td className="py-2.5 pr-3 whitespace-nowrap text-xs text-ink-secondary">
        {formatDuration(row.rollout_started_at, row.rollout_completed_at)}
      </td>
      <td className="py-2.5">
        {row.health_comparison ? (
          <VerdictBadge verdict={row.health_comparison.verdict} />
        ) : (
          <span className="text-[11px] italic text-ink-muted">not compared yet</span>
        )}
      </td>
    </tr>
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
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Filters">
        <label className="flex items-center gap-1.5 text-xs text-ink-secondary">
          Rollout
          <select
            className={SELECT_CLASS}
            value={rolloutState}
            onChange={(event) => setRolloutState(event.target.value as RolloutState | "")}
          >
            <option value="">Any</option>
            {ROLLOUT_STATES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1.5 text-xs text-ink-secondary">
          Evidence
          <select
            className={SELECT_CLASS}
            value={evidenceState}
            onChange={(event) => setEvidenceState(event.target.value as EvidenceState | "")}
          >
            <option value="">Any</option>
            {EVIDENCE_STATES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1.5 text-xs text-ink-secondary">
          Started within
          <select
            className={SELECT_CLASS}
            value={startedWithin}
            onChange={(event) => setStartedWithin(event.target.value)}
          >
            <option value="">Any time</option>
            {WINDOWS.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
      </div>

      {page.state === "loading" ? <DataState kind="loading" /> : null}
      {page.state === "error" ? (
        <Card>
          {page.notFound ? (
            <DataState
              kind="permission-denied"
              description="Your current scope does not include deployments."
            />
          ) : (
            <DataState kind="error" description={page.message} onRetry={retry} />
          )}
        </Card>
      ) : null}
      {page.state === "ready" && page.data.items.length === 0 ? (
        <Card>
          <DataState
            kind="empty"
            title="No deployments"
            description="Nothing matches these filters in your authorized scope. Drake records a revision when a cluster agent reports a workload generation."
          />
        </Card>
      ) : null}
      {page.state === "ready" && page.data.items.length > 0 ? (
        <Card>
          <div className="overflow-x-auto">
            <table className="w-full text-left" data-testid="deployment-table">
              <thead>
                <tr className="text-[11px] uppercase tracking-wide text-ink-muted">
                  <th className="pb-2 pr-3 font-medium">Workload</th>
                  <th className="pb-2 pr-3 font-medium">Revision</th>
                  <th className="pb-2 pr-3 font-medium">Rollout</th>
                  <th className="pb-2 pr-3 font-medium">Evidence</th>
                  <th className="pb-2 pr-3 font-medium">Digest / commit</th>
                  <th className="pb-2 pr-3 font-medium">Ready</th>
                  <th className="pb-2 pr-3 font-medium">Duration</th>
                  <th className="pb-2 font-medium">Health after</th>
                </tr>
              </thead>
              <tbody>
                {page.data.items.map((row) => (
                  <DeploymentRowView key={row.id} row={row} />
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-3 text-[11px] text-ink-muted">
            Showing {page.data.items.length} of {page.data.total} deployments in your
            authorized scope.
          </p>
        </Card>
      ) : null}
    </div>
  );
}

export default function DeploymentsPage() {
  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">Deployments</h1>
        <p className="mt-1 text-sm text-ink-secondary">
          One row per observed workload revision. Evidence says how much of the commit →
          workflow → digest → workload chain Drake actually saw; anything less than the
          whole chain is never shown as verified.
        </p>
      </div>
      <Suspense fallback={<DataState kind="loading" />}>
        <DeploymentTable />
      </Suspense>
    </div>
  );
}
