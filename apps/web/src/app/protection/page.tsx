"use client";

/**
 * Protection Center.
 *
 * The list answers one question per row: is there a usable copy, and has
 * anyone proved it can be restored. They are separate columns because they
 * are separate facts — collapsing them is how "the backup job is green"
 * becomes "we are safe".
 */

import Link from "next/link";
import { Suspense, useState } from "react";
import { PageFrame } from "@/components/shell/AppShell";

import { useApi } from "@/components/catalog/primitives";
import {
  BackupBadge,
  CountChip,
  RecoverabilityBadge,
} from "@/components/protection/primitives";
import { DataState } from "@/components/state/DataState";
import { Card } from "@/components/ui/Card";
import {
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

const SELECT_CLASS =
  "rounded-lg border border-border bg-surface px-2.5 py-1.5 text-xs text-ink";

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

function PolicyRow({ policy }: { policy: ProtectionPolicy }) {
  const evaluation = policy.evaluation;
  return (
    <tr
      className="border-t border-border align-top"
      data-testid={`protection-row-${policy.store_key}`}
    >
      <td className="py-2.5 pr-3">
        <div className="flex flex-col gap-1">
          <Link
            href={`/protection/${policy.id}`}
            className="text-sm font-medium text-ink hover:underline"
          >
            {policy.display_name}
          </Link>
          <span className="font-mono text-[11px] text-ink-muted">
            {policy.project_key}
            {policy.environment_key ? `/${policy.environment_key}` : ""} ·{" "}
            {policy.store_key}
          </span>
        </div>
      </td>
      <td className="py-2.5 pr-3">
        {evaluation ? (
          <BackupBadge state={evaluation.backup_state} />
        ) : (
          <span className="text-[11px] italic text-ink-muted">not evaluated</span>
        )}
      </td>
      <td className="py-2.5 pr-3">
        {evaluation ? (
          <RecoverabilityBadge state={evaluation.recoverability_state} />
        ) : (
          <span className="text-[11px] italic text-ink-muted">not evaluated</span>
        )}
      </td>
      <td className="py-2.5 pr-3 whitespace-nowrap text-xs text-ink-secondary">
        {formatAge(evaluation?.last_success_at ?? null)}
      </td>
      <td className="py-2.5 pr-3 whitespace-nowrap font-mono text-xs text-ink-secondary">
        {formatWindow(policy.rpo_seconds)}
      </td>
      <td className="py-2.5 pr-3 text-[11px] text-ink-secondary">
        {policy.requires_offsite ? "required" : "not required"}
      </td>
      <td className="py-2.5 pr-3 whitespace-nowrap text-xs text-ink-secondary">
        {formatAge(evaluation?.last_restore_at ?? null)}
      </td>
      <td className="py-2.5 pr-3 whitespace-nowrap text-xs text-ink-secondary">
        {formatAge(evaluation?.reporter_seen_at ?? null)}
      </td>
      <td className="py-2.5 text-[11px] text-ink-secondary">
        {evaluation && evaluation.reasons.length > 0
          ? (REASON_LABELS[evaluation.reasons[0]] ?? evaluation.reasons[0])
          : "—"}
      </td>
    </tr>
  );
}

function ProtectionTable() {
  const [backupState, setBackupState] = useState<BackupState | "">("");
  const [recoverabilityState, setRecoverabilityState] = useState<
    RecoverabilityState | ""
  >("");
  const [offsiteState, setOffsiteState] = useState<"present" | "missing" | "">("");

  const [summary] = useApi<ProtectionSummary>("/v1/protection/summary");
  const [page, retry] = useApi<ProtectionPage>(
    protectionListPath({
      backupState: backupState || undefined,
      recoverabilityState: recoverabilityState || undefined,
      offsiteState: offsiteState || undefined,
    }),
  );

  return (
    <div className="space-y-4">
      {summary.state === "ready" ? (
        <div className="flex flex-wrap gap-2" data-testid="protection-summary">
          <CountChip
            label="Protected"
            count={summary.data.backup.protected ?? 0}
            tone="healthy"
          />
          <CountChip label="Overdue" count={summary.data.backup.overdue ?? 0} tone="warning" />
          <CountChip label="Failed" count={summary.data.backup.failed ?? 0} tone="critical" />
          <CountChip label="Unknown" count={summary.data.backup.unknown ?? 0} tone="unknown" />
          <CountChip
            label="Restore verified"
            count={summary.data.recoverability.verified ?? 0}
            tone="healthy"
          />
          <CountChip
            label="Never verified"
            count={summary.data.recoverability.unverified ?? 0}
            tone="maintenance"
          />
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Filters">
        <label className="flex items-center gap-1.5 text-xs text-ink-secondary">
          Backup
          <select
            className={SELECT_CLASS}
            value={backupState}
            onChange={(event) => setBackupState(event.target.value as BackupState | "")}
          >
            <option value="">Any</option>
            {BACKUP_STATES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1.5 text-xs text-ink-secondary">
          Recoverability
          <select
            className={SELECT_CLASS}
            value={recoverabilityState}
            onChange={(event) =>
              setRecoverabilityState(event.target.value as RecoverabilityState | "")
            }
          >
            <option value="">Any</option>
            {RECOVERABILITY_STATES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1.5 text-xs text-ink-secondary">
          Offsite
          <select
            className={SELECT_CLASS}
            value={offsiteState}
            onChange={(event) =>
              setOffsiteState(event.target.value as "present" | "missing" | "")
            }
          >
            <option value="">Any</option>
            <option value="present">Present</option>
            <option value="missing">Missing</option>
          </select>
        </label>
      </div>

      {page.state === "loading" ? <DataState kind="loading" /> : null}
      {page.state === "error" ? (
        <Card>
          {page.notFound ? (
            <DataState
              kind="permission-denied"
              description="Viewing protection posture needs protection.view in this scope."
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
            title="No protection policies"
            description="Nothing matches these filters in your authorized scope. Drake shows a policy once a registered connector reports one."
          />
        </Card>
      ) : null}

      {page.state === "ready" && page.data.items.length > 0 ? (
        <Card>
          <div className="overflow-x-auto">
            <table className="w-full text-left" data-testid="protection-table">
              <thead>
                <tr className="text-caption text-ink-secondary">
                  <th className="pb-2 pr-3 font-medium">Store</th>
                  <th className="pb-2 pr-3 font-medium">Backup</th>
                  <th className="pb-2 pr-3 font-medium">Recoverability</th>
                  <th className="pb-2 pr-3 font-medium">Last success</th>
                  <th className="pb-2 pr-3 font-medium">RPO</th>
                  <th className="pb-2 pr-3 font-medium">Offsite</th>
                  <th className="pb-2 pr-3 font-medium">Last restore</th>
                  <th className="pb-2 pr-3 font-medium">Reporter</th>
                  <th className="pb-2 font-medium">Top reason</th>
                </tr>
              </thead>
              <tbody>
                {page.data.items.map((policy) => (
                  <PolicyRow key={policy.id} policy={policy} />
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-3 text-[11px] text-ink-muted">
            Showing {page.data.items.length} of {page.data.total} policies in your
            authorized scope.
          </p>
        </Card>
      ) : null}
    </div>
  );
}

export default function ProtectionPage() {
  return (
    <PageFrame>
      <div className="space-y-5">
      <div>
        <h1 className="text-title font-semibold text-ink">Protection</h1>
        <p className="mt-1 max-w-3xl text-caption text-ink-secondary">
          A successful backup job is not a backup, and a valid backup nobody has restored
          is not proven recoverable. Those are two separate columns here for exactly that
          reason.
        </p>
      </div>
      <Suspense fallback={<DataState kind="loading" />}>
        <ProtectionTable />
      </Suspense>
      </div>
    </PageFrame>
  );
}
