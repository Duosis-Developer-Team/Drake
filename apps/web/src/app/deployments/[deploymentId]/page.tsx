"use client";

/**
 * Deployment detail: the evidence chain, the rollout, and health either
 * side of it.
 *
 * The health comparison is labelled as correlation everywhere it appears.
 * Two time windows cannot support a causal claim, and a screen that
 * implies one will be believed.
 */

import Link from "next/link";
import { useParams } from "next/navigation";
import { PageFrame } from "@/components/shell/AppShell";

import { LoadGate, MetaRow, useApi } from "@/components/catalog/primitives";
import {
  EvidenceBadge,
  RolloutBadge,
  VerdictBadge,
} from "@/components/deployments/primitives";
import { DataState } from "@/components/state/DataState";
import { Card } from "@/components/ui/Card";
import {
  EVIDENCE_DESCRIPTIONS,
  formatDuration,
  formatSignal,
  type DeploymentRow,
  type RelatedIncident,
  type RevisionEntry,
} from "@/lib/deployments";

const SIGNAL_LABELS: Record<string, string> = {
  request_rate: "Request rate",
  error_ratio: "Error ratio",
  latency_p95: "Latency (p95)",
  restarts: "Restarts",
  availability: "Scrape availability",
};

const CHAIN_LABELS: Record<string, string> = {
  commit: "Commit SHA",
  workflow: "Workflow run",
  declared_digest: "Digest in the workload spec",
  running_digest: "Digest the node pulled",
};

export default function DeploymentDetailPage() {
  const { deploymentId } = useParams<{ deploymentId: string }>();
  const [deployment, retry] = useApi<DeploymentRow>(`/v1/deployments/${deploymentId}`);
  const [revisions] = useApi<{ revisions: RevisionEntry[] }>(
    `/v1/deployments/${deploymentId}/revisions`,
  );
  const [incidents] = useApi<{ incidents: RelatedIncident[] }>(
    `/v1/deployments/${deploymentId}/incidents`,
  );

  return (
    <PageFrame>
      <div className="space-y-5">
      <LoadGate value={deployment} retry={retry}>
        {(data) => (
          <>
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div>
                <p className="text-xs text-ink-muted">
                  <Link href="/deployments" className="hover:text-ink">
                    Deployments
                  </Link>{" "}
                  / <span className="font-mono">{data.cluster.cluster_ref}</span> /{" "}
                  <span className="font-mono">{data.namespace}</span>
                </p>
                <h1 className="mt-1 text-title font-semibold text-ink">
                  {data.workload_name}{" "}
                  <span className="font-mono text-sm text-ink-muted">#{data.revision}</span>
                </h1>
              </div>
              <div className="flex items-center gap-2">
                <EvidenceBadge state={data.evidence_state} />
                <RolloutBadge state={data.rollout_state} />
              </div>
            </div>

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <Card title="Provenance">
                <p className="mb-3 text-xs text-ink-secondary">
                  {EVIDENCE_DESCRIPTIONS[data.evidence_state]}
                </p>
                <ul className="space-y-1" data-testid="evidence-chain">
                  {Object.entries(CHAIN_LABELS).map(([key, label]) => (
                    <li key={key} className="flex items-center gap-2 text-xs">
                      <span
                        aria-hidden
                        className={`h-1.5 w-1.5 rounded-full ${
                          data.evidence_detail[key] ? "bg-healthy" : "bg-unknown"
                        }`}
                      />
                      <span className="text-ink-secondary">{label}</span>
                      <span className="ml-auto text-[11px] text-ink-muted">
                        {data.evidence_detail[key] ? "observed" : "not observed"}
                      </span>
                    </li>
                  ))}
                </ul>
                <dl className="mt-3 divide-y divide-border border-t border-border pt-2">
                  <MetaRow label="Image">
                    <span className="font-mono text-[11px]">{data.primary_image ?? "—"}</span>
                  </MetaRow>
                  <MetaRow label="Digest">
                    <span className="font-mono text-[11px]">{data.short_digest ?? "—"}</span>
                  </MetaRow>
                  <MetaRow label="Commit">
                    <span className="font-mono text-[11px]">{data.short_commit ?? "—"}</span>
                  </MetaRow>
                  <MetaRow label="Workflow run">
                    {data.workflow.run_url ? (
                      <a
                        href={data.workflow.run_url}
                        target="_blank"
                        rel="noreferrer noopener"
                        className="font-mono text-[11px] underline"
                      >
                        {data.workflow.repository} #{data.workflow.run_id}
                      </a>
                    ) : (
                      <span className="text-xs italic text-ink-muted">not observed</span>
                    )}
                  </MetaRow>
                </dl>
              </Card>

              <Card title="Rollout">
                <dl className="divide-y divide-border">
                  <MetaRow label="Ready / desired">
                    <span className="font-mono text-xs">
                      {data.replicas.ready ?? "—"} / {data.replicas.desired ?? "—"}
                    </span>
                  </MetaRow>
                  <MetaRow label="Updated">
                    <span className="font-mono text-xs">{data.replicas.updated ?? "—"}</span>
                  </MetaRow>
                  <MetaRow label="Available">
                    <span className="font-mono text-xs">{data.replicas.available ?? "—"}</span>
                  </MetaRow>
                  <MetaRow label="Generation">
                    <span className="font-mono text-xs">
                      {data.revision} observed {data.observed_generation ?? "—"}
                    </span>
                  </MetaRow>
                  <MetaRow label="Started">
                    <time className="font-mono text-xs">{data.rollout_started_at}</time>
                  </MetaRow>
                  <MetaRow label="Duration">
                    <span className="font-mono text-xs">
                      {formatDuration(data.rollout_started_at, data.rollout_completed_at)}
                    </span>
                  </MetaRow>
                </dl>
              </Card>
            </div>

            <Card title="Health before and after">
              {data.health_comparison ? (
                <div className="space-y-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <VerdictBadge verdict={data.health_comparison.verdict} />
                    <span className="text-[11px] text-ink-muted">
                      {data.health_comparison.incident_count} incident
                      {data.health_comparison.incident_count === 1 ? "" : "s"} opened in the
                      window after this rollout
                    </span>
                  </div>
                  {data.health_comparison.signals ? (
                    <table className="w-full text-left" data-testid="health-comparison">
                      <thead>
                        <tr className="text-caption text-ink-secondary">
                          <th className="pb-1 pr-3 font-medium">Signal</th>
                          <th className="pb-1 pr-3 font-medium">Before</th>
                          <th className="pb-1 pr-3 font-medium">After</th>
                          <th className="pb-1 font-medium">Direction</th>
                        </tr>
                      </thead>
                      <tbody>
                        {Object.entries(data.health_comparison.signals).map(
                          ([name, signal]) => (
                            <tr key={name} className="border-t border-border">
                              <td className="py-1.5 pr-3 text-xs text-ink">
                                {SIGNAL_LABELS[name] ?? name}
                              </td>
                              <td className="py-1.5 pr-3 font-mono text-xs">
                                {formatSignal(signal.before)}
                              </td>
                              <td className="py-1.5 pr-3 font-mono text-xs">
                                {formatSignal(signal.after)}
                              </td>
                              <td className="py-1.5 text-xs text-ink-secondary">
                                {signal.direction === "unknown"
                                  ? "not measured"
                                  : signal.direction}
                              </td>
                            </tr>
                          ),
                        )}
                      </tbody>
                    </table>
                  ) : null}
                  <p className="text-[11px] text-ink-muted">
                    This is a comparison of two time windows, not a causal claim. Drake does
                    not assert that this deployment caused any change it shows here.
                  </p>
                </div>
              ) : (
                <DataState
                  kind="unknown"
                  title="Not compared yet"
                  description="A comparison is computed once the rollout has finished and the window after it has closed."
                />
              )}
            </Card>

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <Card title="Revision history">
                <LoadGate value={revisions} retry={() => undefined}>
                  {(payload) => (
                    <ul className="space-y-1.5" data-testid="revision-timeline">
                      {payload.revisions.map((entry) => (
                        <li key={entry.id} className="flex flex-wrap items-center gap-2">
                          <span className="font-mono text-xs text-ink">#{entry.revision}</span>
                          <RolloutBadge state={entry.rollout_state} />
                          <EvidenceBadge state={entry.evidence_state} />
                          <span className="font-mono text-[11px] text-ink-muted">
                            {entry.short_digest ?? "no digest"}
                          </span>
                          <time className="ml-auto font-mono text-[11px] text-ink-muted">
                            {entry.rollout_started_at}
                          </time>
                        </li>
                      ))}
                    </ul>
                  )}
                </LoadGate>
              </Card>

              <Card title="Incidents in the window">
                <LoadGate value={incidents} retry={() => undefined}>
                  {(payload) =>
                    payload.incidents.length === 0 ? (
                      <DataState
                        kind="empty"
                        title="No incidents"
                        description="No incident opened for this service in the two hours after this rollout."
                      />
                    ) : (
                      <ul className="space-y-1.5" data-testid="related-incidents">
                        {payload.incidents.map((incident) => (
                          <li key={incident.id} className="flex flex-wrap items-center gap-2">
                            <Link
                              href={`/incidents/${incident.id}`}
                              className="text-xs font-medium text-ink hover:underline"
                            >
                              {incident.title}
                            </Link>
                            <time className="ml-auto font-mono text-[11px] text-ink-muted">
                              {incident.opened_at}
                            </time>
                          </li>
                        ))}
                      </ul>
                    )
                  }
                </LoadGate>
              </Card>
            </div>
          </>
        )}
      </LoadGate>
      </div>
    </PageFrame>
  );
}
