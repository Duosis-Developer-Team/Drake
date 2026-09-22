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
import { useFormat, useT } from "@/lib/i18n";
import {
  BACKUP_LABELS,
  RECOVERABILITY_LABELS,
  REASON_LABELS,
  protectionListPath,
  type BackupState,
  type ProtectionPage,
  type ProtectionPolicy,
  type ProtectionSummary,
  type RecoverabilityState,
} from "@/lib/protection";
import { formatWindowWith } from "@/components/protection/format";

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
  const t = useT("protection");
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
        label={t("stats.policies")}
        value={total}
        caption={t("stats.verifiedRestore", { count: verified })}
        part={verified}
        whole={total}
        tone="info"
      />
      <KpiTile
        icon={ShieldCheck}
        label={t("stats.protected")}
        value={protectedCount}
        tone="success"
        part={protectedCount}
        whole={total}
        caption={t("stats.ofPolicies", { count: total })}
      />
      <KpiTile
        icon={AlertTriangle}
        label={t("stats.needsAttention")}
        value={needsAttention}
        tone={(summary.backup.failed ?? 0) > 0 ? "critical" : "warning"}
        part={needsAttention}
        whole={total}
        caption={t("stats.needsAttentionCaption")}
      />
      <KpiTile
        icon={History}
        label={t("stats.neverTested")}
        value={unverified}
        tone="unknown"
        part={unverified}
        whole={total}
        caption={t("stats.neverTestedCaption")}
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
  const t = useT("protection");
  const backup = [
    {
      name: t("backup.protected"),
      value: summary.backup.protected ?? 0,
      tone: "success" as const,
    },
    {
      name: t("backup.at_risk"),
      value: summary.backup.at_risk ?? 0,
      tone: "warning" as const,
    },
    {
      name: t("backup.overdue"),
      value: summary.backup.overdue ?? 0,
      tone: "warning" as const,
    },
    {
      name: t("backup.failed"),
      value: summary.backup.failed ?? 0,
      tone: "critical" as const,
    },
    {
      name: t("backup.unknown"),
      value: summary.backup.unknown ?? 0,
      tone: "unknown" as const,
    },
  ];
  const restore = [
    {
      name: t("recoverability.verified"),
      value: summary.recoverability.verified ?? 0,
      tone: "success" as const,
    },
    {
      name: t("recoverability.unverified"),
      value: summary.recoverability.unverified ?? 0,
      tone: "unknown" as const,
    },
    {
      name: t("recoverability.failed"),
      value: summary.recoverability.failed ?? 0,
      tone: "critical" as const,
    },
    {
      name: t("recoverability.unknown"),
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
          title={t("breakdown.backupTitle")}
          description={t("breakdown.backupDescription")}
        />
        {sum(backup) === 0 ? (
          <EmptyBreakdown message={t("breakdown.backupEmpty")} />
        ) : (
          <Donut
            size={120}
            thickness={14}
            label={t("breakdown.backupChart")}
            slices={backup}
          />
        )}
      </Panel>
      <Panel>
        <PanelHeader
          title={t("breakdown.restoreTitle")}
          description={t("breakdown.restoreDescription")}
        />
        {sum(restore) === 0 ? (
          <EmptyBreakdown message={t("breakdown.restoreEmpty")} />
        ) : (
          <Donut
            size={120}
            thickness={14}
            label={t("breakdown.restoreChart")}
            slices={restore}
          />
        )}
      </Panel>
    </div>
  );
}

function PolicyRow({ policy }: { policy: ProtectionPolicy }) {
  const t = useT("protection");
  const fmt = useFormat();
  const evaluation = policy.evaluation;
  const reason =
    evaluation && evaluation.reasons.length > 0
      ? t.dyn(
          "reason",
          evaluation.reasons[0],
          REASON_LABELS[evaluation.reasons[0]] ?? evaluation.reasons[0],
        )
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
          <FactChip label={t("row.lastSuccess")}>
            {fmt.relative(evaluation?.last_success_at)}
          </FactChip>
          <FactChip label={t("row.rpo")}>
            <span>{formatWindowWith(fmt, policy.rpo_seconds)}</span>
          </FactChip>
          <FactChip label={t("row.offsite")}>
            {policy.requires_offsite ? t("row.required") : t("row.notRequired")}
          </FactChip>
          <FactChip label={t("row.lastRestore")}>
            {fmt.relative(evaluation?.last_restore_at)}
          </FactChip>
          <FactChip label={t("row.reporter")}>
            {fmt.relative(evaluation?.reporter_seen_at)}
          </FactChip>
        </div>
      </div>
      <div className="flex shrink-0 flex-col items-end gap-2">
        <div className="flex flex-wrap items-center justify-end gap-2">
          <span className="flex items-center gap-1.5">
            <span className="text-micro text-ink-muted">{t("row.backup")}</span>
            {evaluation ? (
              <BackupBadge state={evaluation.backup_state} />
            ) : (
              <span className="rounded-full bg-surface-3 px-2.5 py-0.5 text-caption text-ink-muted italic">
                {t("row.notEvaluated")}
              </span>
            )}
          </span>
          <span className="flex items-center gap-1.5">
            <span className="text-micro text-ink-muted">{t("row.restore")}</span>
            {evaluation ? (
              <RecoverabilityBadge state={evaluation.recoverability_state} />
            ) : (
              <span className="rounded-full bg-surface-3 px-2.5 py-0.5 text-caption text-ink-muted italic">
                {t("row.notEvaluated")}
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
            <span className="text-ink-muted">{t("row.noOpenReason")}</span>
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
  const t = useT("protection");
  const common = useT("common");
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
        aria-label={t("filters.label")}
      >
        <PillSelect
          label={t("filters.backup")}
          value={backupState}
          placeholder={t("filters.any")}
          onChange={(value) => setBackupState(value)}
          options={BACKUP_STATES.map((value) => ({
            value,
            label: t.dyn("backup", value, BACKUP_LABELS[value]),
          }))}
        />
        <PillSelect
          label={t("filters.recoverability")}
          value={recoverabilityState}
          placeholder={t("filters.any")}
          onChange={(value) => setRecoverabilityState(value)}
          options={RECOVERABILITY_STATES.map((value) => ({
            value,
            label: t.dyn("recoverability", value, RECOVERABILITY_LABELS[value]),
          }))}
        />
        <PillSelect
          label={t("filters.offsite")}
          value={offsiteState}
          placeholder={t("filters.any")}
          onChange={(value) => setOffsiteState(value)}
          options={[
            { value: "present", label: t("filters.present") },
            { value: "missing", label: t("filters.missing") },
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
            {t("filters.clear")}
          </button>
        ) : null}
        {page.state === "ready" ? (
          <span data-tabular className="ml-auto text-caption text-ink-muted">
            {common("count.showing", {
              shown: page.data.items.length,
              total: page.data.total,
            })}
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
              description={t("list.denied")}
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
                  {common("action.retry")}
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
            title={t("list.emptyTitle")}
            description={t("list.emptyDescription")}
          />
        </Panel>
      ) : null}

      {page.state === "ready" && page.data.items.length > 0 ? (
        <Panel flush>
          <PanelHeader
            flush
            title={t("list.title")}
            description={t("list.description")}
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
  const t = useT("protection");
  return (
    <PageFrame>
      <PageHeader
        title={t("page.title")}
        description={t("page.description")}
      />
      <Suspense fallback={<DataState kind="loading" />}>
        <ProtectionTable />
      </Suspense>
    </PageFrame>
  );
}
