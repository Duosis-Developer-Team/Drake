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
import { formatWindowWith } from "@/components/protection/format";
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
import { useFormat, useT } from "@/lib/i18n";
import {
  type BackupRun,
  type ProtectionEvaluation,
  type ProtectionIncident,
  type ProtectionPolicy,
  type RestoreDrill,
} from "@/lib/protection";

/** English short forms of the drill validation keys; `t.dyn` prefers the catalogue. */
const VALIDATION_LABELS: Record<string, string> = {
  schema_present: "Schema present",
  row_counts_sane: "Row counts sane",
  migrations_applied: "Migrations applied",
  application_smoke: "Application smoke test",
};

type ChainLink = "backupRun" | "artifact" | "integrity" | "offsite" | "restoreDrill";
type ChainWord =
  | "notEvaluated"
  | "lastRunFailed"
  | "overdue"
  | "noGap"
  | "noSuccess"
  | "notObserved"
  | "notRequired"
  | "checkFailed"
  | "checkMissing"
  | "missing"
  | "proven"
  | "drillFailed"
  | "notYetProven"
  | "unknown";

type Link_ = {
  key: ChainLink;
  icon: LucideIcon;
  tone: StatusTone;
  word: ChainWord;
};

const CHAIN_ICON: Record<ChainLink, LucideIcon> = {
  backupRun: PlayCircle,
  artifact: Package,
  integrity: FileCheck2,
  offsite: CloudUpload,
  restoreDrill: ArchiveRestore,
};

function link(key: ChainLink, tone: StatusTone, word: ChainWord): Link_ {
  return { key, icon: CHAIN_ICON[key], tone, word };
}

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
      link("backupRun", "unknown", "notEvaluated"),
      link("artifact", "unknown", "notEvaluated"),
      link("integrity", "unknown", "notEvaluated"),
      link("offsite", "unknown", "notEvaluated"),
      link("restoreDrill", "unknown", "notEvaluated"),
    ];
  }
  const has = (code: string) => evaluation.reasons.includes(code);
  const run: Link_ = has("latest_run_failed")
    ? link("backupRun", "critical", "lastRunFailed")
    : has("backup_overdue")
      ? link("backupRun", "warning", "overdue")
      : evaluation.last_success_at
        ? link("backupRun", "success", "noGap")
        : link("backupRun", "unknown", "noSuccess");
  const artifact: Link_ = has("artifact_missing")
    ? link("artifact", "warning", "notObserved")
    : link("artifact", "success", "noGap");
  const integrity: Link_ = !policy.requires_integrity_check
    ? link("integrity", "not-applicable", "notRequired")
    : has("integrity_failed")
      ? link("integrity", "critical", "checkFailed")
      : has("integrity_missing")
        ? link("integrity", "warning", "checkMissing")
        : link("integrity", "success", "noGap");
  const offsite: Link_ = !policy.requires_offsite
    ? link("offsite", "not-applicable", "notRequired")
    : has("offsite_missing")
      ? link("offsite", "warning", "missing")
      : link("offsite", "success", "noGap");
  const restore: Link_ =
    evaluation.recoverability_state === "verified"
      ? link("restoreDrill", "success", "proven")
      : evaluation.recoverability_state === "failed"
        ? link("restoreDrill", "critical", "drillFailed")
        : evaluation.recoverability_state === "unverified"
          ? link("restoreDrill", "info", "notYetProven")
          : link("restoreDrill", "unknown", "unknown");
  return [run, artifact, integrity, offsite, restore];
}

function EvidenceChain({ links }: { links: Link_[] }) {
  const t = useT("protection");
  return (
    <ol
      className="grid grid-cols-1 gap-3 sm:grid-cols-5"
      aria-label={t("detail.chain.label")}
    >
      {links.map((item, index) => {
        const spec = toneSpec(item.tone);
        const Icon = item.icon;
        return (
          <li
            key={item.key}
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
              {t(`detail.chain.${item.key}`)}
            </span>
            <span className={`mt-0.5 text-micro ${spec.text}`}>
              {t(`detail.chain.word.${item.word}`)}
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
  const t = useT("protection");
  const common = useT("common");
  const incidentsT = useT("incidents");
  const fmt = useFormat();
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
  const windowOf = (seconds: number | null) => formatWindowWith(fmt, seconds);

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
                      {t("detail.crumb")}
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
                    label={t("detail.kpi.lastSuccess")}
                    value={fmt.relative(evaluation?.last_success_at)}
                    tone={evaluation?.last_success_at ? "success" : "unknown"}
                    caption={t("detail.kpi.lastAttempt", {
                      when: fmt.relative(evaluation?.last_attempt_at),
                    })}
                  />
                  <KpiTile
                    icon={CalendarClock}
                    label={t("detail.kpi.rpo")}
                    value={windowOf(data.rpo_seconds)}
                    tone="info"
                    caption={t("detail.kpi.rto", { value: windowOf(data.rto_seconds) })}
                  />
                  <KpiTile
                    icon={History}
                    label={t("detail.kpi.lastDrill")}
                    value={fmt.relative(evaluation?.last_restore_at)}
                    tone={evaluation?.last_restore_at ? "success" : "unknown"}
                    caption={t("detail.kpi.proofValid", {
                      value: windowOf(data.restore_verification_ttl_seconds),
                    })}
                  />
                  <KpiTile
                    icon={AlertOctagon}
                    label={t("detail.kpi.failures")}
                    value={failures === null ? "—" : fmt.number(failures)}
                    tone={failures ? "critical" : "neutral"}
                    caption={t("detail.kpi.reporterSeen", {
                      when: fmt.relative(evaluation?.reporter_seen_at),
                    })}
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
                        title={t("detail.why.title")}
                        description={
                          evaluation
                            ? t("detail.why.evaluated", {
                                when: fmt.relative(evaluation.computed_at),
                              })
                            : t("detail.why.notEvaluated")
                        }
                      />
                      <EvidenceChain links={evidenceChain(data, evaluation)} />
                      {evaluation === null ? (
                        <StateCard
                          kind="unknown"
                          align="start"
                          title={t("detail.why.notEvaluatedTitle")}
                          description={t("detail.why.notEvaluatedDescription")}
                        />
                      ) : (
                        <ReasonList reasons={evaluation.reasons} />
                      )}
                    </Panel>
                  </div>

                  <div className="page-aside">
                    <Panel>
                      <PanelHeader
                        title={t("detail.rpo.title")}
                        description={t("detail.rpo.description")}
                      />
                      {data.rpo_seconds ? (
                        <Countdown
                          label={t("detail.rpo.countdown")}
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
                          {t("detail.rpo.none")}
                        </p>
                      )}
                      <div className="mt-auto flex flex-wrap gap-2">
                        <RequirementPill
                          label={t("detail.rpo.offsite")}
                          required={data.requires_offsite}
                        />
                        <RequirementPill
                          label={t("detail.rpo.integrity")}
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
                      title={t("detail.runs.title")}
                      description={t("detail.runs.description")}
                    />
                    <LoadGate value={runs} retry={() => undefined}>
                      {(payload) =>
                        payload.runs.length === 0 ? (
                          <StateCard
                            kind="empty"
                            icon={PlayCircle}
                            title={t("detail.runs.emptyTitle")}
                            description={t("detail.runs.emptyDescription")}
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
                                        {fmt.duration(run.duration_seconds, { compact: true })}
                                      </span>
                                      <span>
                                        {t("detail.runs.artifacts", { count: run.artifact_count })}
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
                                      label={t.dyn("detail.runStatus", run.status, run.status)}
                                    />
                                    <time className="font-mono text-micro text-ink-muted">
                                      {fmt.utc(run.started_at)}
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
                      title={t("detail.drills.title")}
                      description={t("detail.drills.description")}
                    />
                    <LoadGate value={drills} retry={() => undefined}>
                      {(payload) =>
                        payload.drills.length === 0 ? (
                          <StateCard
                            kind="unknown"
                            icon={ArchiveRestore}
                            title={t("detail.drills.emptyTitle")}
                            description={t("detail.drills.emptyDescription")}
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
                                      {fmt.duration(drill.duration_seconds, { compact: true })}
                                      {drill.rto_met === false
                                        ? ` · ${t("detail.drills.slowerThanRto")}`
                                        : ""}
                                    </p>
                                    <ul className="mt-2 flex flex-wrap gap-1.5">
                                      {checks.length === 0 ? (
                                        <li className="rounded-full bg-surface-2 px-3 py-1 text-micro text-ink-muted">
                                          {t("detail.drills.noChecks")}
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
                                            {t("detail.drills.check", {
                                              label: t.dyn(
                                                "detail.validation",
                                                key,
                                                VALIDATION_LABELS[key] ?? key,
                                              ),
                                              result: passed ? "pass" : "fail",
                                            })}
                                          </li>
                                        ))
                                      )}
                                    </ul>
                                  </div>
                                  <div className="flex shrink-0 flex-col items-end gap-1">
                                    <StatusBadge
                                      status={tone}
                                      label={t.dyn("detail.drillResult", drill.result, drill.result)}
                                    />
                                    <time className="font-mono text-micro text-ink-muted">
                                      {fmt.utc(drill.completed_at ?? drill.started_at)}
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
                  <PanelHeader flush title={t("detail.incidents.title")} />
                  <LoadGate value={incidents} retry={() => undefined}>
                    {(payload) =>
                      payload.incidents.length === 0 ? (
                        <StateCard
                          kind="empty"
                          icon={Siren}
                          title={t("detail.incidents.emptyTitle")}
                          description={t("detail.incidents.emptyDescription")}
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
                                  <time className="font-mono">
                                    {t("detail.incidents.opened", {
                                      when: fmt.utc(incident.opened_at),
                                    })}
                                  </time>
                                </p>
                              </div>
                              <span className="rounded-full bg-surface-2 px-3 py-1 text-micro font-medium text-ink-secondary">
                                {/* The incident area owns its state labels. */}
                                {incidentsT.dyn("state", incident.state, incident.state)}
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
                    title={t("detail.policy.title")}
                    description={t("detail.policy.description")}
                  />
                  <DefGrid
                    items={[
                      {
                        label: t("detail.policy.store"),
                        value: `${data.store_key} (${data.store_kind})`,
                        mono: true,
                      },
                      {
                        label: t("detail.policy.schedule"),
                        value: data.schedule_description ?? "—",
                      },
                      {
                        label: t("detail.policy.rpo"),
                        value: windowOf(data.rpo_seconds),
                        mono: true,
                      },
                      {
                        label: t("detail.policy.rto"),
                        value: windowOf(data.rto_seconds),
                        mono: true,
                      },
                      {
                        label: t("detail.policy.offsiteRequired"),
                        value: data.requires_offsite
                          ? common("state.yes")
                          : common("state.no"),
                      },
                      {
                        label: t("detail.policy.integrityRequired"),
                        value: data.requires_integrity_check
                          ? common("state.yes")
                          : common("state.no"),
                      },
                      {
                        label: t("detail.policy.verificationValid"),
                        value: windowOf(data.restore_verification_ttl_seconds),
                        mono: true,
                      },
                      {
                        label: t("detail.policy.evaluatedAt"),
                        value: fmt.utc(evaluation?.computed_at),
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
  const t = useT("protection");
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
      {required
        ? t("detail.rpo.required", { label })
        : t("detail.rpo.notRequired", { label })}
    </span>
  );
}
