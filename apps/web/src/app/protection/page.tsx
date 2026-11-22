"use client";

/**
 * Protection Center.
 *
 * One band answers the estate's two questions side by side — is there a
 * usable copy, and has anyone proved it restores — then the reporters that
 * still owe evidence, then every policy as a card grouped by application.
 * Backup and restore are separate everywhere because they are separate
 * facts: collapsing them is how "the backup job is green" becomes "we are
 * safe". Policies the contract expects but that never reported are drawn
 * as dashed cards beside the real ones.
 */

import { RefreshCw, ShieldOff } from "lucide-react";
import { Suspense, useState } from "react";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";

import { useApi } from "@/components/catalog/primitives";
import { PillSelect, StateCard } from "@/components/protection/kit";
import { PolicyBoard, PostureBand } from "@/components/protection/Board";
import { DataState } from "@/components/state/DataState";
import { Panel } from "@/components/ui/Panel";
import { useT } from "@/lib/i18n";
import {
  BACKUP_LABELS,
  RECOVERABILITY_LABELS,
  protectionListPath,
  type BackupState,
  type ConnectorStatus,
  type ProtectionPage,
  type ProtectionSummary,
  type RecoverabilityState,
} from "@/lib/protection";
import { ReporterSetup } from "@/components/protection/ReporterSetup";

const BACKUP_STATES: BackupState[] = ["protected", "at_risk", "overdue", "failed", "unknown"];
const RECOVERABILITY_STATES: RecoverabilityState[] = ["verified", "unverified", "failed", "unknown"];

function ProtectionBoard() {
  const t = useT("protection");
  const common = useT("common");
  const [backupState, setBackupState] = useState<BackupState | "">("");
  const [recoverabilityState, setRecoverabilityState] = useState<RecoverabilityState | "">("");
  const [offsiteState, setOffsiteState] = useState<"present" | "missing" | "">("");

  const [summary] = useApi<ProtectionSummary>("/v1/protection/summary");
  const [connectors] = useApi<ConnectorStatus>("/v1/protection/connectors");
  const [page, retry] = useApi<ProtectionPage>(
    protectionListPath({
      backupState: backupState || undefined,
      recoverabilityState: recoverabilityState || undefined,
      offsiteState: offsiteState || undefined,
    }),
  );
  const filtered = Boolean(backupState || recoverabilityState || offsiteState);
  // Expected-but-absent policies belong on the unfiltered board only: a
  // filter asks about states, and an absent policy has none.
  const reporters = !filtered && connectors.state === "ready" ? connectors.data.reporters : [];
  const expected = reporters.reduce((n, r) => n + r.policies.filter((p) => !p.created).length, 0);

  return (
    <div className="flex flex-col gap-6">
      {summary.state === "ready" ? <PostureBand summary={summary.data} /> : null}
      <ReporterSetup emphasis={summary.state === "ready" && summary.data.total_policies === 0} />

      <div className="flex flex-wrap items-center gap-2" role="group" aria-label={t("filters.label")}>
        <PillSelect
          label={t("filters.backup")}
          value={backupState}
          placeholder={t("filters.any")}
          onChange={(value) => setBackupState(value)}
          options={BACKUP_STATES.map((value) => ({ value, label: t.dyn("backup", value, BACKUP_LABELS[value]) }))}
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
            {common("count.showing", { shown: page.data.items.length, total: page.data.total })}
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
            <StateCard kind="permission-denied" description={t("list.denied")} />
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
      {page.state === "ready" && page.data.items.length === 0 && expected === 0 ? (
        <Panel className="flex-1 justify-center">
          <StateCard kind="empty" icon={ShieldOff} title={t("list.emptyTitle")} description={t("list.emptyDescription")} />
        </Panel>
      ) : null}
      {page.state === "ready" && (page.data.items.length > 0 || expected > 0) ? (
        <PolicyBoard policies={page.data.items} reporters={reporters} />
      ) : null}
    </div>
  );
}

export default function ProtectionPage() {
  const t = useT("protection");
  return (
    <PageFrame>
      <PageHeader title={t("page.title")} description={t("page.description")} />
      <Suspense fallback={<DataState kind="loading" />}>
        <ProtectionBoard />
      </Suspense>
    </PageFrame>
  );
}
