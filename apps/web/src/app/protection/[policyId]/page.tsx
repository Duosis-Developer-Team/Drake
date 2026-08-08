"use client";

/**
 * Protection detail: the evidence chain for one store.
 *
 *   policy → runs → artifact → integrity → offsite → restore drill
 *
 * Each link is shown with what was actually observed, so a gap reads as a
 * gap rather than being papered over by the link before it.
 */

import Link from "next/link";
import { useParams } from "next/navigation";

import { LoadGate, MetaRow, useApi } from "@/components/catalog/primitives";
import {
  BackupBadge,
  OverallBadge,
  ReasonList,
  RecoverabilityBadge,
} from "@/components/protection/primitives";
import { DataState } from "@/components/state/DataState";
import { StatusBadge } from "@/components/state/StatusBadge";
import { Card } from "@/components/ui/Card";
import {
  formatAge,
  formatDuration,
  formatWindow,
  type BackupRun,
  type ProtectionIncident,
  type ProtectionPolicy,
  type RestoreDrill,
} from "@/lib/protection";

const VALIDATION_LABELS: Record<string, string> = {
  schema_present: "Schema present",
  row_counts_sane: "Row counts sane",
  migrations_applied: "Migrations applied",
  application_smoke: "Application smoke test",
};

export default function ProtectionDetailPage() {
  const { policyId } = useParams<{ policyId: string }>();
  const [policy, retry] = useApi<ProtectionPolicy>(`/v1/protection/policies/${policyId}`);
  const [runs] = useApi<{ runs: BackupRun[] }>(
    `/v1/protection/policies/${policyId}/runs`,
  );
  const [drills] = useApi<{ drills: RestoreDrill[] }>(
    `/v1/protection/policies/${policyId}/drills`,
  );
  const [incidents] = useApi<{ incidents: ProtectionIncident[] }>(
    `/v1/protection/policies/${policyId}/incidents`,
  );

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <LoadGate value={policy} retry={retry}>
        {(data) => (
          <>
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div>
                <p className="text-xs text-ink-muted">
                  <Link href="/protection" className="hover:text-ink">
                    Protection
                  </Link>{" "}
                  / <span className="font-mono">{data.project_key}</span>
                  {data.environment_key ? (
                    <>
                      {" "}
                      / <span className="font-mono">{data.environment_key}</span>
                    </>
                  ) : null}
                </p>
                <h1 className="mt-1 text-xl font-semibold tracking-tight text-ink">
                  {data.display_name}
                </h1>
              </div>
              {data.evaluation ? (
                <div className="flex items-center gap-2">
                  <BackupBadge state={data.evaluation.backup_state} />
                  <RecoverabilityBadge state={data.evaluation.recoverability_state} />
                  <OverallBadge state={data.evaluation.overall_state} />
                </div>
              ) : null}
            </div>

            {data.evaluation === null ? (
              <Card>
                <DataState
                  kind="unknown"
                  title="Not evaluated yet"
                  description="No assessment has been recorded for this policy. Drake shows nothing rather than assuming a state."
                />
              </Card>
            ) : (
              <Card title="Why">
                <ReasonList reasons={data.evaluation.reasons} />
                <p className="mt-3 border-t border-border pt-3 text-[11px] text-ink-muted">
                  Evaluated{" "}
                  <time className="font-mono">{data.evaluation.computed_at ?? "—"}</time> ·
                  reporter last seen {formatAge(data.evaluation.reporter_seen_at)} ·{" "}
                  {data.evaluation.consecutive_failures} failure
                  {data.evaluation.consecutive_failures === 1 ? "" : "s"} since the last
                  success
                </p>
              </Card>
            )}

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <Card title="Policy">
                <dl className="divide-y divide-border">
                  <MetaRow label="Store">
                    <span className="font-mono text-xs">
                      {data.store_key} ({data.store_kind})
                    </span>
                  </MetaRow>
                  <MetaRow label="Schedule">
                    <span className="text-xs">{data.schedule_description ?? "—"}</span>
                  </MetaRow>
                  <MetaRow label="RPO">
                    <span className="font-mono text-xs">
                      {formatWindow(data.rpo_seconds)}
                    </span>
                  </MetaRow>
                  <MetaRow label="RTO">
                    <span className="font-mono text-xs">
                      {formatWindow(data.rto_seconds)}
                    </span>
                  </MetaRow>
                  <MetaRow label="Offsite required">
                    <span className="text-xs">{data.requires_offsite ? "Yes" : "No"}</span>
                  </MetaRow>
                  <MetaRow label="Integrity required">
                    <span className="text-xs">
                      {data.requires_integrity_check ? "Yes" : "No"}
                    </span>
                  </MetaRow>
                  <MetaRow label="Restore verification valid for">
                    <span className="font-mono text-xs">
                      {formatWindow(data.restore_verification_ttl_seconds)}
                    </span>
                  </MetaRow>
                </dl>
              </Card>

              <Card title="Latest evidence">
                <dl className="divide-y divide-border">
                  <MetaRow label="Last successful backup">
                    <span className="text-xs">
                      {formatAge(data.evaluation?.last_success_at ?? null)}
                    </span>
                  </MetaRow>
                  <MetaRow label="Last attempt">
                    <span className="text-xs">
                      {formatAge(data.evaluation?.last_attempt_at ?? null)}
                    </span>
                  </MetaRow>
                  <MetaRow label="Last restore drill">
                    <span className="text-xs">
                      {formatAge(data.evaluation?.last_restore_at ?? null)}
                    </span>
                  </MetaRow>
                </dl>
              </Card>
            </div>

            <Card title="Backup attempts">
              <LoadGate value={runs} retry={() => undefined}>
                {(payload) =>
                  payload.runs.length === 0 ? (
                    <DataState
                      kind="empty"
                      title="No runs observed"
                      description="No backup run has been reported for this policy."
                    />
                  ) : (
                    <ul className="space-y-1.5" data-testid="run-timeline">
                      {payload.runs.map((run) => (
                        <li key={run.id} className="flex flex-wrap items-center gap-2">
                          <StatusBadge
                            status={
                              run.status === "succeeded"
                                ? "healthy"
                                : run.status === "failed"
                                  ? "critical"
                                  : "unknown"
                            }
                            label={run.status}
                          />
                          <span className="font-mono text-[11px] text-ink-muted">
                            {run.provider_run_id}
                          </span>
                          <span className="text-[11px] text-ink-secondary">
                            {formatDuration(run.duration_seconds)}
                          </span>
                          <span className="text-[11px] text-ink-secondary">
                            {run.artifact_count} artifact
                            {run.artifact_count === 1 ? "" : "s"}
                          </span>
                          {run.error_code ? (
                            <span className="text-[11px] text-critical">{run.error_code}</span>
                          ) : null}
                          <time className="ml-auto font-mono text-[11px] text-ink-muted">
                            {run.started_at}
                          </time>
                        </li>
                      ))}
                    </ul>
                  )
                }
              </LoadGate>
            </Card>

            <Card title="Restore drills">
              <LoadGate value={drills} retry={() => undefined}>
                {(payload) =>
                  payload.drills.length === 0 ? (
                    <DataState
                      kind="unknown"
                      title="Never restore-tested"
                      description="No restore drill has been recorded. A backup nobody has restored is not proven recoverable."
                    />
                  ) : (
                    <ul className="space-y-2" data-testid="drill-timeline">
                      {payload.drills.map((drill) => (
                        <li key={drill.id} className="flex flex-wrap items-center gap-2">
                          <StatusBadge
                            status={
                              drill.result === "passed"
                                ? "healthy"
                                : drill.result === "failed"
                                  ? "critical"
                                  : "warning"
                            }
                            label={drill.result}
                          />
                          <span className="font-mono text-[11px] text-ink-muted">
                            {drill.target_profile}
                          </span>
                          <span className="text-[11px] text-ink-secondary">
                            {formatDuration(drill.duration_seconds)}
                            {drill.rto_met === false ? " · slower than RTO" : ""}
                          </span>
                          <span className="text-[11px] text-ink-secondary">
                            {Object.entries(drill.validations)
                              .map(
                                ([key, passed]) =>
                                  `${VALIDATION_LABELS[key] ?? key}: ${passed ? "pass" : "fail"}`,
                              )
                              .join(" · ") || "no checks recorded"}
                          </span>
                          <time className="ml-auto font-mono text-[11px] text-ink-muted">
                            {drill.completed_at ?? drill.started_at}
                          </time>
                        </li>
                      ))}
                    </ul>
                  )
                }
              </LoadGate>
            </Card>

            <Card title="Related incidents">
              <LoadGate value={incidents} retry={() => undefined}>
                {(payload) =>
                  payload.incidents.length === 0 ? (
                    <DataState
                      kind="empty"
                      title="No protection incidents"
                      description="No incident has been opened for this project's protection posture."
                    />
                  ) : (
                    <ul className="space-y-1.5" data-testid="protection-incidents">
                      {payload.incidents.map((incident) => (
                        <li key={incident.id} className="flex flex-wrap items-center gap-2">
                          <Link
                            href={`/incidents/${incident.id}`}
                            className="text-xs font-medium text-ink hover:underline"
                          >
                            {incident.title}
                          </Link>
                          <span className="text-[11px] text-ink-muted">{incident.state}</span>
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
          </>
        )}
      </LoadGate>
    </div>
  );
}
