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
import { useFormat, useT } from "@/lib/i18n";
import {
  EVIDENCE_DESCRIPTIONS,
  rolloutDurationSeconds,
  type DeploymentRow,
  type RelatedIncident,
  type RevisionEntry,
} from "@/lib/deployments";

/** The links of the evidence chain, in the order they are shown. Each is a
 *  key of `evidence_detail` and of the `chain.*` catalogue entries. */
const CHAIN_KEYS = ["commit", "workflow", "declared_digest", "running_digest"] as const;

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
  const t = useT("deployments");
  const fmt = useFormat();
  return (
    <li className="flex flex-wrap items-center gap-3 px-7 py-4">
      <span className="font-mono text-caption text-ink" data-tabular>
        #{entry.revision}
      </span>
      <RolloutBadge state={entry.rollout_state} />
      <EvidenceBadge state={entry.evidence_state} />
      <span className="font-mono text-micro text-ink-muted">
        {entry.short_digest ?? t("ref.missing", { label: t("ref.digest") })}
      </span>
      <span className="ml-auto flex flex-col items-end text-right">
        <time dateTime={entry.rollout_started_at} className="font-mono text-micro text-ink-muted">
          {fmt.utc(entry.rollout_started_at)}
        </time>
        <span className="text-micro text-ink-muted">
          {fmt.duration(
            rolloutDurationSeconds(entry.rollout_started_at, entry.rollout_completed_at),
          )}
        </span>
      </span>
    </li>
  );
}

/** One incident opened in the window after this rollout. */
function RelatedIncidentRow({ incident }: { incident: RelatedIncident }) {
  const fmt = useFormat();
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
      <time dateTime={incident.opened_at} className="ml-auto font-mono text-micro text-ink-muted">
        {fmt.utc(incident.opened_at)}
      </time>
    </li>
  );
}

export default function DeploymentDetailPage() {
  const { deploymentId } = useParams<{ deploymentId: string }>();
  const t = useT("deployments");
  const c = useT("common");
  const fmt = useFormat();
  const [deployment, retry] = useApi<DeploymentRow>(
    `/v1/deployments/${deploymentId}`,
  );
  const [revisions] = useApi<{ revisions: RevisionEntry[] }>(
    `/v1/deployments/${deploymentId}/revisions`,
  );
  const [incidents] = useApi<{ incidents: RelatedIncident[] }>(
    `/v1/deployments/${deploymentId}/incidents`,
  );

  /** A signal value in this locale's digits. `null` renders as a dash,
   *  never as zero; precision follows the magnitude, as `formatSignal`. */
  const signalText = (value: number | null): string => {
    if (value === null || Number.isNaN(value)) return "—";
    const digits = Math.abs(value) >= 100 ? 0 : Math.abs(value) >= 1 ? 2 : 4;
    return fmt.number(value, { minimumFractionDigits: digits, maximumFractionDigits: digits });
  };

  return (
    <PageFrame>
      <LoadGate value={deployment} retry={retry}>
        {(data) => {
          const observedCount = CHAIN_KEYS.filter(
            (key) => data.evidence_detail[key],
          ).length;
          const readyRatio =
            data.replicas.desired && data.replicas.desired > 0
              ? Math.min(1, (data.replicas.ready ?? 0) / data.replicas.desired)
              : null;
          const durationText = fmt.duration(
            rolloutDurationSeconds(data.rollout_started_at, data.rollout_completed_at),
          );
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
                      {t("detail.back")}
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
                      <span className="italic">{t("detail.unbound")}</span>
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
                        {t("detail.replicasReady")}
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
                      label={t("detail.replicasReady")}
                      tone={ROLLOUT_TONE[data.rollout_state]}
                      size={48}
                    />
                  </div>
                </Panel>
                <FigureTile
                  label={t("detail.rolloutDuration")}
                  value={durationText}
                  caption={
                    data.rollout_completed_at
                      ? t("detail.completed")
                      : t("detail.stillRollingOut")
                  }
                />
                <Panel>
                  <div className="flex items-center justify-between gap-4">
                    <div className="min-w-0">
                      <p className="text-caption text-ink-muted">
                        {t("detail.evidenceChain")}
                      </p>
                      <p
                        data-tabular
                        className="mt-1 text-[2rem] leading-none font-semibold tracking-[-0.03em] text-ink"
                      >
                        {observedCount}/{CHAIN_KEYS.length}
                      </p>
                    </div>
                    <RingProgress
                      value={
                        CHAIN_KEYS.length > 0
                          ? observedCount / CHAIN_KEYS.length
                          : null
                      }
                      unit="ratio"
                      label={t("detail.evidenceChainObserved")}
                      tone={EVIDENCE_TONE[data.evidence_state]}
                      size={48}
                    />
                  </div>
                </Panel>
                <FigureTile
                  label={t("detail.healthVerdict")}
                  size="badge"
                  value={
                    data.health_comparison ? (
                      <VerdictBadge verdict={data.health_comparison.verdict} />
                    ) : (
                      <span className="text-body italic text-ink-muted">
                        {t("detail.notCompared")}
                      </span>
                    )
                  }
                  caption={
                    data.health_comparison
                      ? t("detail.incidentsAfter", {
                          count: data.health_comparison.incident_count,
                        })
                      : t("detail.waitingForWindow")
                  }
                />
              </div>

              <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
                <Panel
                  flush
                  className="motion-safe:animate-[fade-in_400ms_var(--ease-entrance)_backwards] [animation-delay:40ms]"
                >
                  <PanelHeader flush title={t("detail.provenance.title")} />
                  <PanelBody>
                    <p className="mb-3 text-caption text-ink-secondary">
                      {t.dyn(
                        "evidenceDescription",
                        data.evidence_state,
                        EVIDENCE_DESCRIPTIONS[data.evidence_state],
                      )}
                    </p>
                    <ul
                      className="grid grid-cols-1 gap-1.5 sm:grid-cols-2"
                      data-testid="evidence-chain"
                    >
                      {CHAIN_KEYS.map((key) => {
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
                              {t(`chain.${key}`)}
                            </span>
                            <span className="shrink-0 text-micro text-ink-muted">
                              {observed
                                ? t("detail.provenance.observed")
                                : t("detail.provenance.notObserved")}
                            </span>
                          </li>
                        );
                      })}
                    </ul>
                    <dl className="mt-3 divide-y divide-border border-t border-border pt-2">
                      <MetaRow label={c("field.image")}>
                        <span className="font-mono text-[11px]">
                          {data.primary_image ?? "—"}
                        </span>
                      </MetaRow>
                      <MetaRow label={t("detail.provenance.digest")}>
                        <span className="font-mono text-[11px]">
                          {data.short_digest ?? "—"}
                        </span>
                      </MetaRow>
                      <MetaRow label={t("detail.provenance.commit")}>
                        <span className="font-mono text-[11px]">
                          {data.short_commit ?? "—"}
                        </span>
                      </MetaRow>
                      <MetaRow label={t("detail.provenance.workflowRun")}>
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
                            {t("detail.provenance.notObserved")}
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
                  <PanelHeader flush title={t("detail.rollout.title")} />
                  <PanelBody>
                    <dl className="divide-y divide-border">
                      <MetaRow label={t("detail.rollout.readyDesired")}>
                        <span className="font-mono text-xs">
                          {data.replicas.ready ?? "—"} /{" "}
                          {data.replicas.desired ?? "—"}
                        </span>
                      </MetaRow>
                      <MetaRow label={t("detail.rollout.updated")}>
                        <span className="font-mono text-xs">
                          {data.replicas.updated ?? "—"}
                        </span>
                      </MetaRow>
                      <MetaRow label={t("detail.rollout.available")}>
                        <span className="font-mono text-xs">
                          {data.replicas.available ?? "—"}
                        </span>
                      </MetaRow>
                      <MetaRow label={t("detail.rollout.generation")}>
                        <span className="font-mono text-xs">
                          {t("detail.rollout.generationValue", {
                            revision: data.revision,
                            observed: data.observed_generation ?? "—",
                          })}
                        </span>
                      </MetaRow>
                      <MetaRow label={t("detail.rollout.started")}>
                        <time dateTime={data.rollout_started_at} className="font-mono text-xs">
                          {fmt.utc(data.rollout_started_at)}
                        </time>
                      </MetaRow>
                      <MetaRow label={t("detail.rollout.duration")}>
                        <span className="font-mono text-xs">{durationText}</span>
                      </MetaRow>
                      {data.rollout_reason ? (
                        <MetaRow label={c("field.reason")}>
                          <span className="text-xs">{data.rollout_reason}</span>
                        </MetaRow>
                      ) : null}
                    </dl>
                  </PanelBody>
                </Panel>
              </div>

              <Panel className="motion-safe:animate-[fade-in_420ms_var(--ease-entrance)_backwards] [animation-delay:80ms]">
                <PanelHeader
                  title={t("detail.comparison.title")}
                  description={t("detail.comparison.description")}
                />
                {data.health_comparison ? (
                  <div className="space-y-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <VerdictBadge verdict={data.health_comparison.verdict} />
                      <span className="text-micro text-ink-muted">
                        {t("detail.comparison.incidentsInWindow", {
                          count: data.health_comparison.incident_count,
                        })}
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
                              <th className="pb-1 pr-3 font-medium">
                                {t("detail.comparison.signal")}
                              </th>
                              <th className="pb-1 pr-3 font-medium">
                                {t("detail.comparison.before")}
                              </th>
                              <th className="pb-1 pr-3 font-medium">
                                {t("detail.comparison.after")}
                              </th>
                              <th className="pb-1 font-medium">
                                {t("detail.comparison.direction")}
                              </th>
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
                                    {t.dyn("signal", name, name)}
                                  </td>
                                  <td className="py-1.5 pr-3 font-mono text-xs">
                                    {signalText(signal.before)}
                                  </td>
                                  <td className="py-1.5 pr-3 font-mono text-xs">
                                    {signalText(signal.after)}
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
                    title={t("detail.comparison.notYetTitle")}
                    description={t("detail.comparison.notYetDescription")}
                  />
                )}
              </Panel>

              <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
                <Panel
                  flush
                  className="motion-safe:animate-[fade-in_440ms_var(--ease-entrance)_backwards] [animation-delay:100ms]"
                >
                  <PanelHeader flush title={t("detail.revisions.title")} />
                  <LoadGate value={revisions} retry={() => undefined}>
                    {(payload) =>
                      payload.revisions.length === 0 ? (
                        <div className="px-7 py-8">
                          <DataState
                            kind="empty"
                            title={t("detail.revisions.empty")}
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
                  <PanelHeader flush title={t("detail.incidents.title")} />
                  <LoadGate value={incidents} retry={() => undefined}>
                    {(payload) =>
                      payload.incidents.length === 0 ? (
                        <div className="px-7 py-8">
                          <DataState
                            kind="empty"
                            title={t("detail.incidents.emptyTitle")}
                            description={t("detail.incidents.emptyDescription")}
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
