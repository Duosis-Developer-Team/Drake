"use client";

/**
 * Protection Center.
 *
 * The list answers one question per row: is there a usable copy, and has
 * anyone proved it can be restored. They are separate chips because they
 * are separate facts — collapsing them is how "the backup job is green"
 * becomes "we are safe".
 */

import {
  AlertTriangle,
  ChevronRight,
  Database,
  History,
  RefreshCw,
  ShieldCheck,
  ShieldOff,
  Shield,
} from "lucide-react";
import Link from "next/link";
import { Suspense, useState } from "react";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";

import { useApi } from "@/components/catalog/primitives";
import { Donut } from "@/components/charts/visuals";
import {
  FactChip,
  IconBubble,
  KpiTile,
  PillSelect,
  StateCard,
} from "@/components/protection/kit";
import {
  BackupBadge,
  RecoverabilityBadge,
  overallTone,
} from "@/components/protection/primitives";
import { DataState } from "@/components/state/DataState";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import {
  BACKUP_LABELS,
  RECOVERABILITY_LABELS,
  REASON_LABELS,
  formatAge,
  formatWindow,
  protectionListPath,
  type BackupState,
  type ProtectionPage,
  type ProtectionPolicy,
  type ProtectionSummary,
  type RecoverabilityState,
} from "@/lib/protection";

const BACKUP_STATES: BackupState[] = [
  "protected",
  "at_risk",
  "overdue",
  "failed",
  "unknown",
];
const RECOVERABILITY_STATES: RecoverabilityState[] = [
  "verified",
  "unverified",
  "failed",
  "unknown",
];

function SummaryStats({ summary }: { summary: ProtectionSummary }) {
  const total = summary.total_policies;
  const protectedCount = summary.backup.protected ?? 0;
  const needsAttention =
    (summary.backup.at_risk ?? 0) +
    (summary.backup.overdue ?? 0) +
    (summary.backup.failed ?? 0);
  const unverified = summary.recoverability.unverified ?? 0;
  const verified = summary.recoverability.verified ?? 0;
  return (
    <div className="page-grid" data-testid="protection-stats">
      <KpiTile
        icon={Shield}
        label="Policies"
        value={total}
        caption={`${verified} with a verified restore`}
        part={verified}
        whole={total}
        tone="info"
      />
      <KpiTile
        icon={ShieldCheck}
        label="Protected"
        value={protectedCount}
        tone="success"
        part={protectedCount}
        whole={total}
        caption={`of ${total} ${total === 1 ? "policy" : "policies"}`}
      />
      <KpiTile
        icon={AlertTriangle}
        label="Needs attention"
        value={needsAttention}
        tone={(summary.backup.failed ?? 0) > 0 ? "critical" : "warning"}
        part={needsAttention}
        whole={total}
        caption="At risk, overdue or failed"
      />
      <KpiTile
        icon={History}
        label="Never restore-tested"
        value={unverified}
        tone="unknown"
        part={unverified}
        whole={total}
        caption="A backup nobody restored is unproven"
      />
    </div>
  );
}

/** The ring placeholder a breakdown shows before anything reports. */
function EmptyBreakdown({ message }: { message: string }) {
  return (
    <div
      className="flex flex-wrap items-center gap-5"
      data-testid="donut-empty"
    >
      <span
        aria-hidden
        className="relative flex h-24 w-24 shrink-0 items-center justify-center rounded-full border-[12px] border-surface-3"
      >
        <span data-tabular className="text-title font-semibold text-ink-muted">
          0
        </span>
      </span>
      <p className="min-w-[10rem] flex-1 text-caption text-ink-muted">
        {message}
      </p>
    </div>
  );
}

function Breakdowns({ summary }: { summary: ProtectionSummary }) {
  const backup = [
    {
      name: "Protected",
      value: summary.backup.protected ?? 0,
      tone: "success" as const,
    },
    {
      name: "At risk",
      value: summary.backup.at_risk ?? 0,
      tone: "warning" as const,
    },
    {
      name: "Overdue",
      value: summary.backup.overdue ?? 0,
      tone: "warning" as const,
    },
    {
      name: "Failed",
      value: summary.backup.failed ?? 0,
      tone: "critical" as const,
    },
    {
      name: "Unknown",
      value: summary.backup.unknown ?? 0,
      tone: "unknown" as const,
    },
  ];
  const restore = [
    {
      name: "Verified",
      value: summary.recoverability.verified ?? 0,
      tone: "success" as const,
    },
    {
      name: "Never verified",
      value: summary.recoverability.unverified ?? 0,
      tone: "unknown" as const,
    },
    {
      name: "Failed",
      value: summary.recoverability.failed ?? 0,
      tone: "critical" as const,
    },
    {
      name: "Unknown",
      value: summary.recoverability.unknown ?? 0,
      tone: "unknown" as const,
    },
  ];
  const sum = (slices: { value: number }[]) =>
    slices.reduce((total, slice) => total + slice.value, 0);
  return (
    <div className="page-aside" data-testid="protection-summary">
      {/* Two separate questions, so two separate donuts. "Backed up" and
          "provably restorable" are not the same claim, and a policy can
          be the first without ever having been the second. */}
      <Panel>
        <PanelHeader
          title="Backup state"
          description="By freshest backup outcome."
        />
        {sum(backup) === 0 ? (
          <EmptyBreakdown message="No policy reports a backup state yet." />
        ) : (
          <Donut
            size={120}
            thickness={14}
            label="Policies by backup state"
            slices={backup}
          />
        )}
      </Panel>
      <Panel>
        <PanelHeader
          title="Restore evidence"
          description="Whether a restore was ever proven."
        />
        {sum(restore) === 0 ? (
          <EmptyBreakdown message="No restore drill has been recorded." />
        ) : (
          <Donut
            size={120}
            thickness={14}
            label="Policies by restore evidence"
            slices={restore}
          />
        )}
      </Panel>
    </div>
  );
}

function PolicyRow({ policy }: { policy: ProtectionPolicy }) {
  const evaluation = policy.evaluation;
  const reason =
    evaluation && evaluation.reasons.length > 0
      ? (REASON_LABELS[evaluation.reasons[0]] ?? evaluation.reasons[0])
      : null;
  return (
    <li
      className="group relative flex flex-wrap items-center gap-x-5 gap-y-3 px-7 py-5 transition-colors hover:bg-surface-hover"
      data-testid={`protection-row-${policy.store_key}`}
    >
      <IconBubble
        icon={Database}
        tone={evaluation ? overallTone(evaluation.overall_state) : "unknown"}
      />
      <div className="min-w-0 flex-1 basis-64">
        <Link
          href={`/protection/${policy.id}`}
          className="text-body font-semibold text-ink after:absolute after:inset-0 after:content-['']"
        >
          {policy.display_name}
        </Link>
        <span className="mt-0.5 block truncate font-mono text-micro text-ink-muted">
          {policy.project_key}
          {policy.environment_key ? `/${policy.environment_key}` : ""} ·{" "}
          {policy.store_key}
        </span>
        <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
          <FactChip label="Last success">
            {formatAge(evaluation?.last_success_at ?? null)}
          </FactChip>
          <FactChip label="RPO">
            <span>{formatWindow(policy.rpo_seconds)}</span>
          </FactChip>
          <FactChip label="Offsite">
            {policy.requires_offsite ? "required" : "not required"}
          </FactChip>
          <FactChip label="Last restore">
            {formatAge(evaluation?.last_restore_at ?? null)}
          </FactChip>
          <FactChip label="Reporter">
            {formatAge(evaluation?.reporter_seen_at ?? null)}
          </FactChip>
        </div>
      </div>
      <div className="flex shrink-0 flex-col items-end gap-2">
        <div className="flex flex-wrap items-center justify-end gap-2">
          <span className="flex items-center gap-1.5">
            <span className="text-micro text-ink-muted">Backup</span>
            {evaluation ? (
              <BackupBadge state={evaluation.backup_state} />
            ) : (
              <span className="rounded-full bg-surface-3 px-2.5 py-0.5 text-caption text-ink-muted italic">
                not evaluated
              </span>
            )}
          </span>
          <span className="flex items-center gap-1.5">
            <span className="text-micro text-ink-muted">Restore</span>
            {evaluation ? (
              <RecoverabilityBadge state={evaluation.recoverability_state} />
            ) : (
              <span className="rounded-full bg-surface-3 px-2.5 py-0.5 text-caption text-ink-muted italic">
                not evaluated
              </span>
            )}
          </span>
        </div>
        <span className="flex items-center gap-1 text-caption text-ink-secondary">
          {reason ? (
            <>
              <AlertTriangle aria-hidden className="h-3.5 w-3.5 text-warning" />
              <span>{reason}</span>
            </>
          ) : (
            <span className="text-ink-muted">No open reason</span>
          )}
        </span>
      </div>
      <ChevronRight
        aria-hidden
        className="hidden h-4 w-4 shrink-0 text-ink-muted transition-transform group-hover:translate-x-0.5 md:block"
      />
    </li>
  );
}

function ProtectionTable() {
  const [backupState, setBackupState] = useState<BackupState | "">("");
  const [recoverabilityState, setRecoverabilityState] = useState<
    RecoverabilityState | ""
  >("");
  const [offsiteState, setOffsiteState] = useState<"present" | "missing" | "">(
    "",
  );

  const [summary] = useApi<ProtectionSummary>("/v1/protection/summary");
  const [page, retry] = useApi<ProtectionPage>(
    protectionListPath({
      backupState: backupState || undefined,
      recoverabilityState: recoverabilityState || undefined,
      offsiteState: offsiteState || undefined,
    }),
  );
  const filtered = Boolean(backupState || recoverabilityState || offsiteState);

  const list = (
    <div className="page-main">
      <div
        className="flex flex-wrap items-center gap-2"
        role="group"
        aria-label="Filters"
      >
        <PillSelect
          label="Backup"
          value={backupState}
          placeholder="Any"
          onChange={(value) => setBackupState(value)}
          options={BACKUP_STATES.map((value) => ({
            value,
            label: BACKUP_LABELS[value],
          }))}
        />
        <PillSelect
          label="Recoverability"
          value={recoverabilityState}
          placeholder="Any"
          onChange={(value) => setRecoverabilityState(value)}
          options={RECOVERABILITY_STATES.map((value) => ({
            value,
            label: RECOVERABILITY_LABELS[value],
          }))}
        />
        <PillSelect
          label="Offsite"
          value={offsiteState}
          placeholder="Any"
          onChange={(value) => setOffsiteState(value)}
          options={[
            { value: "present", label: "Present" },
            { value: "missing", label: "Missing" },
          ]}
        />
        {filtered ? (
          <button
            type="button"
            onClick={() => {
              setBackupState("");
              setRecoverabilityState("");
              setOffsiteState("");
            }}
            className="h-10 rounded-full px-4 text-caption font-medium text-ink-secondary transition-colors hover:bg-surface-hover hover:text-ink"
          >
            Clear filters
          </button>
        ) : null}
        {page.state === "ready" ? (
          <span data-tabular className="ml-auto text-caption text-ink-muted">
            Showing {page.data.items.length} of {page.data.total}
          </span>
        ) : null}
      </div>

      {page.state === "loading" ? (
        <Panel>
          <DataState kind="loading" />
        </Panel>
      ) : null}
      {page.state === "error" ? (
        <Panel>
          {page.notFound ? (
            <StateCard
              kind="permission-denied"
              description="Viewing protection posture needs protection.view in this scope."
            />
          ) : (
            <StateCard
              kind="error"
              description={page.message}
              action={
                <button
                  type="button"
                  onClick={retry}
                  className="inline-flex items-center gap-1.5 rounded-full border border-border px-4 py-2 text-caption font-medium text-ink transition-colors hover:bg-surface-hover"
                >
                  <RefreshCw aria-hidden className="h-3.5 w-3.5" />
                  Retry
                </button>
              }
            />
          )}
        </Panel>
      ) : null}
      {page.state === "ready" && page.data.items.length === 0 ? (
        <Panel className="flex-1 justify-center">
          <StateCard
            kind="empty"
            icon={ShieldOff}
            title="No protection policies"
            description="Nothing matches in your authorized scope. A policy appears once a registered connector reports one."
          />
        </Panel>
      ) : null}

      {page.state === "ready" && page.data.items.length > 0 ? (
        <Panel flush>
          <PanelHeader
            flush
            title="Policies"
            description="Backup and restore evidence per store, in your authorized scope."
          />
          <ul className="divide-y divide-border" data-testid="protection-table">
            {page.data.items.map((policy) => (
              <PolicyRow key={policy.id} policy={policy} />
            ))}
          </ul>
        </Panel>
      ) : null}
    </div>
  );

  return (
    <div className="flex flex-col gap-6">
      {summary.state === "ready" ? (
        <SummaryStats summary={summary.data} />
      ) : null}
      {summary.state === "ready" ? (
        <div className="page-split">
          {list}
          <Breakdowns summary={summary.data} />
        </div>
      ) : (
        list
      )}
    </div>
  );
}

export default function ProtectionPage() {
  return (
    <PageFrame>
      <PageHeader
        title="Protection"
        description="Backed up and proven restorable are two different answers — Drake keeps them apart."
      />
      <Suspense fallback={<DataState kind="loading" />}>
        <ProtectionTable />
      </Suspense>
    </PageFrame>
  );
}
