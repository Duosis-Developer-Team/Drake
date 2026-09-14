"use client";

/**
 * Protection detail: the evidence chain for one store.
 *
 *   policy → runs → artifact → integrity → offsite → restore drill
 *
 * Each link is shown with what was actually observed, so a gap reads as a
 * gap rather than being papered over by the link before it.
 */

import {
  AlertOctagon,
  ArchiveRestore,
  CalendarClock,
  CheckCircle2,
  CircleSlash,
  Clock3,
  CloudUpload,
  FileCheck2,
  HardDriveDownload,
  HelpCircle,
  History,
  Package,
  PlayCircle,
  Siren,
  TriangleAlert,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";

import { LoadGate, useApi } from "@/components/catalog/primitives";
import { Countdown } from "@/components/charts/visuals";
import {
  DefGrid,
  IconBubble,
  KpiTile,
  StateCard,
} from "@/components/protection/kit";
import {
  BackupBadge,
  OverallBadge,
  ReasonList,
  RecoverabilityBadge,
} from "@/components/protection/primitives";
import { StatusBadge } from "@/components/state/StatusBadge";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { toneSpec, type StatusTone } from "@/lib/design/status";
import {
  formatAge,
  formatDuration,
  formatWindow,
  type BackupRun,
  type ProtectionEvaluation,
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

type Link_ = {
  label: string;
  icon: LucideIcon;
  tone: StatusTone;
  word: string;
};

/**
 * The chain, link by link, read straight off the evaluation's own reason
 * codes and the policy's stated requirements. A link with no reason against
 * it says "no gap reported" — never "healthy" — and a policy nobody has
 * evaluated shows every link as unknown.
 */
function evidenceChain(
  policy: ProtectionPolicy,
  evaluation: ProtectionEvaluation | null,
): Link_[] {
  if (!evaluation) {
    return [
      {
        label: "Backup run",
        icon: PlayCircle,
        tone: "unknown",
        word: "Not evaluated",
      },
      {
        label: "Artifact",
        icon: Package,
        tone: "unknown",
        word: "Not evaluated",
      },
      {
        label: "Integrity",
        icon: FileCheck2,
        tone: "unknown",
        word: "Not evaluated",
      },
      {
        label: "Offsite copy",
        icon: CloudUpload,
        tone: "unknown",
        word: "Not evaluated",
      },
      {
        label: "Restore drill",
        icon: ArchiveRestore,
        tone: "unknown",
        word: "Not evaluated",
      },
    ];
  }
  const has = (code: string) => evaluation.reasons.includes(code);
  const run: Link_ = has("latest_run_failed")
    ? {
        label: "Backup run",
        icon: PlayCircle,
        tone: "critical",
        word: "Last run failed",
      }
    : has("backup_overdue")
      ? {
          label: "Backup run",
          icon: PlayCircle,
          tone: "warning",
          word: "Overdue",
        }
      : evaluation.last_success_at
        ? {
            label: "Backup run",
            icon: PlayCircle,
            tone: "success",
            word: "No gap reported",
          }
        : {
            label: "Backup run",
            icon: PlayCircle,
            tone: "unknown",
            word: "No success seen",
          };
  const artifact: Link_ = has("artifact_missing")
    ? {
        label: "Artifact",
        icon: Package,
        tone: "warning",
        word: "Not observed",
      }
    : {
        label: "Artifact",
        icon: Package,
        tone: "success",
        word: "No gap reported",
      };
  const integrity: Link_ = !policy.requires_integrity_check
    ? {
        label: "Integrity",
        icon: FileCheck2,
        tone: "not-applicable",
        word: "Not required",
      }
    : has("integrity_failed")
      ? {
          label: "Integrity",
          icon: FileCheck2,
          tone: "critical",
          word: "Check failed",
        }
      : has("integrity_missing")
        ? {
            label: "Integrity",
            icon: FileCheck2,
            tone: "warning",
            word: "Check missing",
          }
        : {
            label: "Integrity",
            icon: FileCheck2,
            tone: "success",
            word: "No gap reported",
          };
  const offsite: Link_ = !policy.requires_offsite
    ? {
        label: "Offsite copy",
        icon: CloudUpload,
        tone: "not-applicable",
        word: "Not required",
      }
    : has("offsite_missing")
      ? {
          label: "Offsite copy",
          icon: CloudUpload,
          tone: "warning",
          word: "Missing",
        }
      : {
          label: "Offsite copy",
          icon: CloudUpload,
          tone: "success",
          word: "No gap reported",
        };
  const restore: Link_ =
    evaluation.recoverability_state === "verified"
      ? {
          label: "Restore drill",
          icon: ArchiveRestore,
          tone: "success",
          word: "Proven",
        }
      : evaluation.recoverability_state === "failed"
        ? {
            label: "Restore drill",
            icon: ArchiveRestore,
            tone: "critical",
            word: "Drill failed",
          }
        : evaluation.recoverability_state === "unverified"
          ? {
              label: "Restore drill",
              icon: ArchiveRestore,
              tone: "info",
              word: "Not yet proven",
            }
          : {
              label: "Restore drill",
              icon: ArchiveRestore,
              tone: "unknown",
              word: "Unknown",
            };
  return [run, artifact, integrity, offsite, restore];
}

function EvidenceChain({ links }: { links: Link_[] }) {
  return (
    <ol
      className="grid grid-cols-1 gap-3 sm:grid-cols-5"
      aria-label="Evidence chain"
    >
      {links.map((link, index) => {
        const spec = toneSpec(link.tone);
        const Icon = link.icon;
        return (
          <li
            key={link.label}
            className="relative flex min-w-0 flex-col items-center text-center"
          >
            {index < links.length - 1 ? (
              <span
                aria-hidden
                className="absolute top-7 left-[calc(50%+2rem)] hidden h-0.5 w-[calc(100%-4rem+0.75rem)] rounded-full bg-surface-3 sm:block"
              />
            ) : null}
            <span
              aria-hidden
              className={`relative flex h-14 w-14 items-center justify-center rounded-full ring-8 ring-surface ${spec.chip}`}
            >
              <Icon className="h-6 w-6" />
            </span>
            <span className="mt-3 text-caption font-semibold text-ink">
              {link.label}
            </span>
            <span className={`mt-0.5 text-micro ${spec.text}`}>
              {link.word}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function runTone(status: string): StatusTone {
  return status === "succeeded"
    ? "success"
    : status === "failed"
      ? "critical"
      : "unknown";
}

function drillTone(result: string): StatusTone {
  return result === "passed"
    ? "success"
    : result === "failed"
      ? "critical"
      : "warning";
}

const TONE_ICON: Partial<Record<StatusTone, LucideIcon>> = {
  success: CheckCircle2,
  critical: XCircle,
  warning: TriangleAlert,
  unknown: HelpCircle,
};

export default function ProtectionDetailPage() {
  const { policyId } = useParams<{ policyId: string }>();
  const [policy, retry] = useApi<ProtectionPolicy>(
    `/v1/protection/policies/${policyId}`,
  );
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
    <PageFrame>
      <LoadGate value={policy} retry={retry}>
        {(data) => {
          // The RPO deadline is derived, not invented: last observed success
          // plus the policy's own stated window. A missing success renders
          // as no deadline rather than a fabricated one.
          const rpoDeadline =
            data.evaluation?.last_success_at && data.rpo_seconds
              ? new Date(
                  new Date(data.evaluation.last_success_at).getTime() +
                    data.rpo_seconds * 1000,
                ).toISOString()
              : null;
          const evaluation = data.evaluation;
          const failures = evaluation?.consecutive_failures ?? null;

          return (
            <>
              <PageHeader
                title={data.display_name}
                description={
                  <>
                    <Link href="/protection" className="hover:text-ink">
                      Protection
                    </Link>{" "}
                    / <span className="font-mono">{data.project_key}</span>
                    {data.environment_key ? (
                      <>
                        {" "}
                        /{" "}
                        <span className="font-mono">
                          {data.environment_key}
                        </span>
                      </>
                    ) : null}{" "}
                    · <span className="font-mono">{data.store_kind}</span>
                  </>
                }
                actions={
                  evaluation ? (
                    <div className="flex flex-wrap items-center gap-2">
                      <BackupBadge state={evaluation.backup_state} />
                      <RecoverabilityBadge
                        state={evaluation.recoverability_state}
                      />
                      <OverallBadge state={evaluation.overall_state} />
                    </div>
                  ) : null
                }
              />

              <div className="flex flex-col gap-6">
                <div className="page-grid">
                  <KpiTile
                    icon={HardDriveDownload}
                    label="Last successful backup"
                    value={formatAge(evaluation?.last_success_at ?? null)}
                    tone={evaluation?.last_success_at ? "success" : "unknown"}
                    caption={`Last attempt ${formatAge(evaluation?.last_attempt_at ?? null)}`}
                  />
                  <KpiTile
                    icon={CalendarClock}
                    label="Recovery point objective"
                    value={formatWindow(data.rpo_seconds)}
                    tone="info"
                    caption={`RTO ${formatWindow(data.rto_seconds)}`}
                  />
                  <KpiTile
                    icon={History}
                    label="Last restore drill"
                    value={formatAge(evaluation?.last_restore_at ?? null)}
                    tone={evaluation?.last_restore_at ? "success" : "unknown"}
                    caption={`Proof valid for ${formatWindow(data.restore_verification_ttl_seconds)}`}
                  />
                  <KpiTile
                    icon={AlertOctagon}
                    label="Failures since last success"
                    value={failures === null ? "—" : failures}
                    tone={failures ? "critical" : "neutral"}
                    caption={`Reporter seen ${formatAge(evaluation?.reporter_seen_at ?? null)}`}
                  />
                </div>

                <div className="page-split">
                  <div className="page-main">
                    <Panel
                      tone={
                        evaluation && evaluation.reasons.length > 0
                          ? "warning"
                          : "default"
                      }
                    >
                      <PanelHeader
                        title="Why"
                        description={
                          evaluation
                            ? `Evaluated ${formatAge(evaluation.computed_at)} · each link of the chain, as observed`
                            : "Each link of the chain, as observed"
                        }
                      />
                      <EvidenceChain links={evidenceChain(data, evaluation)} />
                      {evaluation === null ? (
                        <StateCard
                          kind="unknown"
                          align="start"
                          title="Not evaluated yet"
                          description="No assessment has been recorded for this policy. Drake shows nothing rather than assuming a state."
                        />
                      ) : (
                        <ReasonList reasons={evaluation.reasons} />
                      )}
                    </Panel>
                  </div>

                  <div className="page-aside">
                    <Panel>
                      <PanelHeader
                        title="RPO buffer"
                        description="Last success plus the stated window."
                      />
                      {data.rpo_seconds ? (
                        <Countdown
                          label="RPO buffer remaining"
                          deadline={rpoDeadline}
                          windowDays={Math.max(1, data.rpo_seconds / 86_400)}
                          warnDays={Math.max(
                            0.5,
                            data.rpo_seconds / 86_400 / 3,
                          )}
                          criticalDays={0}
                        />
                      ) : (
                        <p className="text-caption text-ink-muted">
                          This policy states no RPO window.
                        </p>
                      )}
                      <div className="mt-auto flex flex-wrap gap-2">
                        <RequirementPill
                          label="Offsite"
                          required={data.requires_offsite}
                        />
                        <RequirementPill
                          label="Integrity check"
                          required={data.requires_integrity_check}
                        />
                      </div>
                    </Panel>
                  </div>
                </div>

                <div className="grid grid-cols-1 items-start gap-6 xl:grid-cols-2">
                  <Panel flush>
                    <PanelHeader
                      flush
                      title="Backup attempts"
                      description="Every run the connector reported, newest first."
                    />
                    <LoadGate value={runs} retry={() => undefined}>
                      {(payload) =>
                        payload.runs.length === 0 ? (
                          <StateCard
                            kind="empty"
                            icon={PlayCircle}
                            title="No runs observed"
                            description="No backup run has been reported for this policy."
                          />
                        ) : (
                          <ul
                            className="divide-y divide-border"
                            data-testid="run-timeline"
                          >
                            {payload.runs.map((run) => {
                              const tone = runTone(run.status);
                              return (
                                <li
                                  key={run.id}
                                  className="flex flex-wrap items-center gap-4 px-7 py-4 transition-colors hover:bg-surface-hover"
                                >
                                  <IconBubble
                                    icon={TONE_ICON[tone] ?? HelpCircle}
                                    tone={tone}
                                  />
                                  <div className="min-w-0 flex-1">
                                    <p className="truncate font-mono text-caption font-semibold text-ink">
                                      {run.provider_run_id}
                                    </p>
                                    <p className="mt-0.5 flex flex-wrap gap-x-3 text-micro text-ink-muted">
                                      <span className="inline-flex items-center gap-1">
                                        <Clock3
                                          aria-hidden
                                          className="h-3 w-3"
                                        />
                                        {formatDuration(run.duration_seconds)}
                                      </span>
                                      <span>
                                        {run.artifact_count} artifact
                                        {run.artifact_count === 1 ? "" : "s"}
                                      </span>
                                      {run.error_code ? (
                                        <span className="font-mono text-critical">
                                          {run.error_code}
                                        </span>
                                      ) : null}
                                    </p>
                                  </div>
                                  <div className="flex shrink-0 flex-col items-end gap-1">
                                    <StatusBadge
                                      status={tone}
                                      label={run.status}
                                    />
                                    <time className="font-mono text-micro text-ink-muted">
                                      {run.started_at}
                                    </time>
                                  </div>
                                </li>
                              );
                            })}
                          </ul>
                        )
                      }
                    </LoadGate>
                  </Panel>

                  <Panel flush>
                    <PanelHeader
                      flush
                      title="Restore drills"
                      description="Proof that a copy actually comes back."
                    />
                    <LoadGate value={drills} retry={() => undefined}>
                      {(payload) =>
                        payload.drills.length === 0 ? (
                          <StateCard
                            kind="unknown"
                            icon={ArchiveRestore}
                            title="Never restore-tested"
                            description="No restore drill has been recorded. A backup nobody has restored is not proven recoverable."
                          />
                        ) : (
                          <ul
                            className="divide-y divide-border"
                            data-testid="drill-timeline"
                          >
                            {payload.drills.map((drill) => {
                              const tone = drillTone(drill.result);
                              const checks = Object.entries(drill.validations);
                              return (
                                <li
                                  key={drill.id}
                                  className="flex flex-wrap items-start gap-4 px-7 py-4 transition-colors hover:bg-surface-hover"
                                >
                                  <IconBubble
                                    icon={TONE_ICON[tone] ?? HelpCircle}
                                    tone={tone}
                                  />
                                  <div className="min-w-0 flex-1">
                                    <p className="font-mono text-caption font-semibold text-ink">
                                      {drill.target_profile}
                                    </p>
                                    <p className="mt-0.5 text-micro text-ink-muted">
                                      {formatDuration(drill.duration_seconds)}
                                      {drill.rto_met === false
                                        ? " · slower than RTO"
                                        : ""}
                                    </p>
                                    <ul className="mt-2 flex flex-wrap gap-1.5">
                                      {checks.length === 0 ? (
                                        <li className="rounded-full bg-surface-2 px-3 py-1 text-micro text-ink-muted">
                                          no checks recorded
                                        </li>
                                      ) : (
                                        checks.map(([key, passed]) => (
                                          <li
                                            key={key}
                                            className={`rounded-full px-3 py-1 text-micro ${
                                              toneSpec(
                                                passed ? "success" : "critical",
                                              ).chip
                                            }`}
                                          >
                                            {`${VALIDATION_LABELS[key] ?? key}: ${passed ? "pass" : "fail"}`}
                                          </li>
                                        ))
                                      )}
                                    </ul>
                                  </div>
                                  <div className="flex shrink-0 flex-col items-end gap-1">
                                    <StatusBadge
                                      status={tone}
                                      label={drill.result}
                                    />
                                    <time className="font-mono text-micro text-ink-muted">
                                      {drill.completed_at ?? drill.started_at}
                                    </time>
                                  </div>
                                </li>
                              );
                            })}
                          </ul>
                        )
                      }
                    </LoadGate>
                  </Panel>
                </div>

                <Panel flush>
                  <PanelHeader flush title="Related incidents" />
                  <LoadGate value={incidents} retry={() => undefined}>
                    {(payload) =>
                      payload.incidents.length === 0 ? (
                        <StateCard
                          kind="empty"
                          icon={Siren}
                          title="No protection incidents"
                          description="No incident has been opened for this project's protection posture."
                        />
                      ) : (
                        <ul
                          className="divide-y divide-border"
                          data-testid="protection-incidents"
                        >
                          {payload.incidents.map((incident) => (
                            <li
                              key={incident.id}
                              className="relative flex flex-wrap items-center gap-4 px-7 py-4 transition-colors hover:bg-surface-hover"
                            >
                              <IconBubble
                                icon={Siren}
                                tone={
                                  incident.state === "resolved"
                                    ? "success"
                                    : "critical"
                                }
                              />
                              <div className="min-w-0 flex-1">
                                <Link
                                  href={`/incidents/${incident.id}`}
                                  className="text-body font-semibold text-ink after:absolute after:inset-0 after:content-['']"
                                >
                                  {incident.title}
                                </Link>
                                <p className="mt-0.5 text-micro text-ink-muted">
                                  opened{" "}
                                  <time className="font-mono">
                                    {incident.opened_at}
                                  </time>
                                </p>
                              </div>
                              <span className="rounded-full bg-surface-2 px-3 py-1 text-micro font-medium text-ink-secondary">
                                {incident.state}
                              </span>
                            </li>
                          ))}
                        </ul>
                      )
                    }
                  </LoadGate>
                </Panel>

                <Panel>
                  <PanelHeader
                    title="Policy"
                    description="What this store promises."
                  />
                  <DefGrid
                    items={[
                      {
                        label: "Store",
                        value: `${data.store_key} (${data.store_kind})`,
                        mono: true,
                      },
                      {
                        label: "Schedule",
                        value: data.schedule_description ?? "—",
                      },
                      {
                        label: "RPO",
                        value: formatWindow(data.rpo_seconds),
                        mono: true,
                      },
                      {
                        label: "RTO",
                        value: formatWindow(data.rto_seconds),
                        mono: true,
                      },
                      {
                        label: "Offsite required",
                        value: data.requires_offsite ? "Yes" : "No",
                      },
                      {
                        label: "Integrity required",
                        value: data.requires_integrity_check ? "Yes" : "No",
                      },
                      {
                        label: "Restore verification valid for",
                        value: formatWindow(
                          data.restore_verification_ttl_seconds,
                        ),
                        mono: true,
                      },
                      {
                        label: "Evaluated at",
                        value: evaluation?.computed_at ?? "—",
                        mono: true,
                      },
                    ]}
                  />
                </Panel>
              </div>
            </>
          );
        }}
      </LoadGate>
    </PageFrame>
  );
}

function RequirementPill({
  label,
  required,
}: {
  label: string;
  required: boolean;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-micro font-medium ${
        required ? "bg-surface-2 text-ink" : "bg-surface-2 text-ink-muted"
      }`}
    >
      {required ? (
        <CheckCircle2 aria-hidden className="h-3.5 w-3.5" />
      ) : (
        <CircleSlash aria-hidden className="h-3.5 w-3.5" />
      )}
      {label} {required ? "required" : "not required"}
    </span>
  );
}
