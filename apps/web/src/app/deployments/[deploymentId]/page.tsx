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
import { PageFrame, PageHeader } from "@/components/shell/AppShell";

import { LoadGate, MetaRow, useApi } from "@/components/catalog/primitives";
import {
  EVIDENCE_TONE,
  EvidenceBadge,
  ROLLOUT_TONE,
  RolloutBadge,
  SignalDirectionBadge,
  VerdictBadge,
} from "@/components/deployments/primitives";
import { RingProgress } from "@/components/charts/visuals";
import { DataState } from "@/components/state/DataState";
import { Panel, PanelBody, PanelHeader } from "@/components/ui/Panel";
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

/** A small stat tile for a value that is already a formatted string (a
 *  duration, a verdict) rather than a raw number `Stat` could format
 *  itself — same visual weight as the ring/count tiles beside it. */
function FigureTile({
  label,
  value,
  caption,
  size = "figure",
}: {
  label: string;
  value: React.ReactNode;
  caption?: React.ReactNode;
  /** `"figure"` renders `value` as a big number-weight string. `"badge"`
   *  keeps a badge's own size and just centers it under the label. */
  size?: "figure" | "badge";
}) {
  return (
    <Panel>
      <p className="text-caption text-ink-muted">{label}</p>
      {size === "figure" ? (
        <p className="mt-1 text-[2rem] leading-none font-semibold tracking-[-0.03em] text-ink">
          {value}
        </p>
      ) : (
        <div className="mt-2">{value}</div>
      )}
      {caption ? (
        <div className="mt-1.5 text-micro text-ink-muted">{caption}</div>
      ) : null}
    </Panel>
  );
}

/** One prior revision, as a scannable row rather than a bullet of text. */
function RevisionRow({ entry }: { entry: RevisionEntry }) {
  return (
    <li className="flex flex-wrap items-center gap-3 px-7 py-4">
      <span className="font-mono text-caption text-ink" data-tabular>
        #{entry.revision}
      </span>
      <RolloutBadge state={entry.rollout_state} />
      <EvidenceBadge state={entry.evidence_state} />
      <span className="font-mono text-micro text-ink-muted">
        {entry.short_digest ?? "no digest"}
      </span>
      <span className="ml-auto flex flex-col items-end text-right">
        <time className="font-mono text-micro text-ink-muted">
          {entry.rollout_started_at}
        </time>
        <span className="text-micro text-ink-muted">
          {formatDuration(entry.rollout_started_at, entry.rollout_completed_at)}
        </span>
      </span>
    </li>
  );
}

/** One incident opened in the window after this rollout. */
function RelatedIncidentRow({ incident }: { incident: RelatedIncident }) {
  return (
    <li className="flex flex-wrap items-center gap-3 px-7 py-4">
      <span
        aria-hidden
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-critical-soft text-critical"
      >
        <span className="h-2 w-2 rounded-full bg-critical" />
      </span>
      <Link
        href={`/incidents/${incident.id}`}
        className="min-w-0 flex-1 truncate text-caption font-medium text-ink hover:underline"
      >
        {incident.title}
      </Link>
      <time className="ml-auto font-mono text-micro text-ink-muted">
        {incident.opened_at}
      </time>
    </li>
  );
}

export default function DeploymentDetailPage() {
  const { deploymentId } = useParams<{ deploymentId: string }>();
  const [deployment, retry] = useApi<DeploymentRow>(
    `/v1/deployments/${deploymentId}`,
  );
  const [revisions] = useApi<{ revisions: RevisionEntry[] }>(
    `/v1/deployments/${deploymentId}/revisions`,
  );
  const [incidents] = useApi<{ incidents: RelatedIncident[] }>(
    `/v1/deployments/${deploymentId}/incidents`,
  );

  return (
    <PageFrame>
      <LoadGate value={deployment} retry={retry}>
        {(data) => {
          const chainKeys = Object.keys(CHAIN_LABELS);
          const observedCount = chainKeys.filter(
            (key) => data.evidence_detail[key],
          ).length;
          const readyRatio =
            data.replicas.desired && data.replicas.desired > 0
              ? Math.min(1, (data.replicas.ready ?? 0) / data.replicas.desired)
              : null;
          return (
            <div className="space-y-6">
              <PageHeader
                title={
                  <>
                    {data.workload_name}{" "}
                    <span className="font-mono text-body text-ink-muted">
                      #{data.revision}
                    </span>
                  </>
                }
                status={
                  <>
                    <EvidenceBadge state={data.evidence_state} />
                    <RolloutBadge state={data.rollout_state} />
                  </>
                }
                meta={
                  <>
                    <Link href="/deployments" className="hover:text-ink">
                      Deployments
                    </Link>
                    <span className="font-mono">
                      {data.cluster.cluster_ref}/{data.namespace}
                    </span>
                    {data.project_key ? (
                      <span className="font-mono">
                        {data.project_key}/{data.environment_key}/
                        {data.service_key}
                      </span>
                    ) : (
                      <span className="italic">not bound to a service</span>
                    )}
                  </>
                }
              />

              <div
                className="page-grid motion-safe:animate-[fade-in_360ms_var(--ease-entrance)_backwards]"
                data-testid="deployment-detail-summary"
              >
                <Panel>
                  <div className="flex items-center justify-between gap-4">
                    <div className="min-w-0">
                      <p className="text-caption text-ink-muted">
                        Replicas ready
                      </p>
                      <p
                        data-tabular
                        className="mt-1 text-[2rem] leading-none font-semibold tracking-[-0.03em] text-ink"
                      >
                        {data.replicas.ready ?? "—"} /{" "}
                        {data.replicas.desired ?? "—"}
                      </p>
                    </div>
                    <RingProgress
                      value={readyRatio}
                      unit="ratio"
                      label="Replicas ready"
                      tone={ROLLOUT_TONE[data.rollout_state]}
                      size={48}
                    />
                  </div>
                </Panel>
                <FigureTile
                  label="Rollout duration"
                  value={formatDuration(
                    data.rollout_started_at,
                    data.rollout_completed_at,
                  )}
                  caption={
                    data.rollout_completed_at
                      ? "completed"
                      : "still rolling out"
                  }
                />
                <Panel>
                  <div className="flex items-center justify-between gap-4">
                    <div className="min-w-0">
                      <p className="text-caption text-ink-muted">
                        Evidence chain
                      </p>
                      <p
                        data-tabular
                        className="mt-1 text-[2rem] leading-none font-semibold tracking-[-0.03em] text-ink"
                      >
                        {observedCount}/{chainKeys.length}
                      </p>
                    </div>
                    <RingProgress
                      value={
                        chainKeys.length > 0
                          ? observedCount / chainKeys.length
                          : null
                      }
                      unit="ratio"
                      label="Evidence chain observed"
                      tone={EVIDENCE_TONE[data.evidence_state]}
                      size={48}
                    />
                  </div>
                </Panel>
                <FigureTile
                  label="Health verdict"
                  size="badge"
                  value={
                    data.health_comparison ? (
                      <VerdictBadge verdict={data.health_comparison.verdict} />
                    ) : (
                      <span className="text-body italic text-ink-muted">
                        not compared
                      </span>
                    )
                  }
                  caption={
                    data.health_comparison
                      ? `${data.health_comparison.incident_count} incident${
                          data.health_comparison.incident_count === 1 ? "" : "s"
                        } after rollout`
                      : "waiting for the window after this rollout to close"
                  }
                />
              </div>

              <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
                <Panel
                  flush
                  className="motion-safe:animate-[fade-in_400ms_var(--ease-entrance)_backwards] [animation-delay:40ms]"
                >
                  <PanelHeader flush title="Provenance" />
                  <PanelBody>
                    <p className="mb-3 text-caption text-ink-secondary">
                      {EVIDENCE_DESCRIPTIONS[data.evidence_state]}
                    </p>
                    <ul
                      className="grid grid-cols-1 gap-1.5 sm:grid-cols-2"
                      data-testid="evidence-chain"
                    >
                      {Object.entries(CHAIN_LABELS).map(([key, label]) => {
                        const observed = Boolean(data.evidence_detail[key]);
                        return (
                          <li
                            key={key}
                            className={`flex items-center gap-2 rounded-lg px-2.5 py-2 text-caption ${
                              observed ? "bg-healthy-soft" : "bg-surface-2"
                            }`}
                          >
                            <span
                              aria-hidden
                              className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                                observed ? "bg-healthy" : "bg-unknown"
                              }`}
                            />
                            <span className="min-w-0 flex-1 truncate text-ink-secondary">
                              {label}
                            </span>
                            <span className="shrink-0 text-micro text-ink-muted">
                              {observed ? "observed" : "not observed"}
                            </span>
                          </li>
                        );
                      })}
                    </ul>
                    <dl className="mt-3 divide-y divide-border border-t border-border pt-2">
                      <MetaRow label="Image">
                        <span className="font-mono text-[11px]">
                          {data.primary_image ?? "—"}
                        </span>
                      </MetaRow>
                      <MetaRow label="Digest">
                        <span className="font-mono text-[11px]">
                          {data.short_digest ?? "—"}
                        </span>
                      </MetaRow>
                      <MetaRow label="Commit">
                        <span className="font-mono text-[11px]">
                          {data.short_commit ?? "—"}
                        </span>
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
                          <span className="text-xs italic text-ink-muted">
                            not observed
                          </span>
                        )}
                      </MetaRow>
                    </dl>
                  </PanelBody>
                </Panel>

                <Panel
                  flush
                  className="motion-safe:animate-[fade-in_400ms_var(--ease-entrance)_backwards] [animation-delay:40ms]"
                >
                  <PanelHeader flush title="Rollout" />
                  <PanelBody>
                    <dl className="divide-y divide-border">
                      <MetaRow label="Ready / desired">
                        <span className="font-mono text-xs">
                          {data.replicas.ready ?? "—"} /{" "}
                          {data.replicas.desired ?? "—"}
                        </span>
                      </MetaRow>
                      <MetaRow label="Updated">
                        <span className="font-mono text-xs">
                          {data.replicas.updated ?? "—"}
                        </span>
                      </MetaRow>
                      <MetaRow label="Available">
                        <span className="font-mono text-xs">
                          {data.replicas.available ?? "—"}
                        </span>
                      </MetaRow>
                      <MetaRow label="Generation">
                        <span className="font-mono text-xs">
                          {data.revision} observed{" "}
                          {data.observed_generation ?? "—"}
                        </span>
                      </MetaRow>
                      <MetaRow label="Started">
                        <time className="font-mono text-xs">
                          {data.rollout_started_at}
                        </time>
                      </MetaRow>
                      <MetaRow label="Duration">
                        <span className="font-mono text-xs">
                          {formatDuration(
                            data.rollout_started_at,
                            data.rollout_completed_at,
                          )}
                        </span>
                      </MetaRow>
                      {data.rollout_reason ? (
                        <MetaRow label="Reason">
                          <span className="text-xs">{data.rollout_reason}</span>
                        </MetaRow>
                      ) : null}
                    </dl>
                  </PanelBody>
                </Panel>
              </div>

              <Panel className="motion-safe:animate-[fade-in_420ms_var(--ease-entrance)_backwards] [animation-delay:80ms]">
                <PanelHeader
                  title="Health before and after"
                  description="A comparison of two time windows, not a causal claim. Drake does not assert that this deployment caused any change it shows here."
                />
                {data.health_comparison ? (
                  <div className="space-y-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <VerdictBadge verdict={data.health_comparison.verdict} />
                      <span className="text-micro text-ink-muted">
                        {data.health_comparison.incident_count} incident
                        {data.health_comparison.incident_count === 1
                          ? ""
                          : "s"}{" "}
                        opened in the window after this rollout
                      </span>
                    </div>
                    {data.health_comparison.signals ? (
                      <div className="w-full min-w-0 max-w-full overflow-x-auto [contain:paint]">
                        <table
                          className="w-full text-left"
                          data-testid="health-comparison"
                        >
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
                                <tr
                                  key={name}
                                  className="border-t border-border"
                                >
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
                                    <SignalDirectionBadge
                                      direction={signal.direction}
                                    />
                                  </td>
                                </tr>
                              ),
                            )}
                          </tbody>
                        </table>
                      </div>
                    ) : null}
                  </div>
                ) : (
                  <DataState
                    kind="unknown"
                    title="Not compared yet"
                    description="A comparison is computed once the rollout has finished and the window after it has closed."
                  />
                )}
              </Panel>

              <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
                <Panel
                  flush
                  className="motion-safe:animate-[fade-in_440ms_var(--ease-entrance)_backwards] [animation-delay:100ms]"
                >
                  <PanelHeader flush title="Revision history" />
                  <LoadGate value={revisions} retry={() => undefined}>
                    {(payload) =>
                      payload.revisions.length === 0 ? (
                        <div className="px-7 py-8">
                          <DataState
                            kind="empty"
                            title="No prior revisions recorded"
                          />
                        </div>
                      ) : (
                        <ul
                          className="divide-y divide-border"
                          data-testid="revision-timeline"
                        >
                          {payload.revisions.map((entry) => (
                            <RevisionRow key={entry.id} entry={entry} />
                          ))}
                        </ul>
                      )
                    }
                  </LoadGate>
                </Panel>

                <Panel
                  flush
                  className="motion-safe:animate-[fade-in_440ms_var(--ease-entrance)_backwards] [animation-delay:100ms]"
                >
                  <PanelHeader flush title="Incidents in the window" />
                  <LoadGate value={incidents} retry={() => undefined}>
                    {(payload) =>
                      payload.incidents.length === 0 ? (
                        <div className="px-7 py-8">
                          <DataState
                            kind="empty"
                            title="No incidents"
                            description="No incident opened for this service in the two hours after this rollout."
                          />
                        </div>
                      ) : (
                        <ul
                          className="divide-y divide-border"
                          data-testid="related-incidents"
                        >
                          {payload.incidents.map((incident) => (
                            <RelatedIncidentRow
                              key={incident.id}
                              incident={incident}
                            />
                          ))}
                        </ul>
                      )
                    }
                  </LoadGate>
                </Panel>
              </div>
            </div>
          );
        }}
      </LoadGate>
    </PageFrame>
  );
}
