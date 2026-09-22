"use client";

/**
 * Deployment list.
 *
 * One row per observed workload revision: what is running, how much Drake
 * can prove about where it came from, how the rollout went, and how health
 * looked afterwards.
 */

import {
  CheckCircle2,
  Fingerprint,
  GitCommitHorizontal,
  Hourglass,
  PackageSearch,
  Rocket,
  ShieldCheck,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { Suspense, useState } from "react";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";

import { useApi } from "@/components/catalog/primitives";
import {
  EVIDENCE_TONE,
  EvidenceBadge,
  ROLLOUT_TONE,
  RolloutBadge,
  ShortRef,
  VerdictBadge,
} from "@/components/deployments/primitives";
import {
  BreakdownRing,
  CardTitle,
  EmptyHero,
  FactPill,
  KpiTile,
  PillSelect,
  RowBubble,
  ShareBar,
  StatePad,
  Toolbar,
} from "@/components/incidents/OpsKit";
import { DataState } from "@/components/state/DataState";
import { Panel } from "@/components/ui/Panel";
import { toneSpec } from "@/lib/design/status";
import { useFormat, useT } from "@/lib/i18n";
import {
  EVIDENCE_LABELS,
  deploymentListPath,
  rolloutDurationSeconds,
  type DeploymentPage,
  type DeploymentRow,
  type EvidenceState,
  type RolloutState,
} from "@/lib/deployments";

const ROLLOUT_STATES: RolloutState[] = [
  "pending",
  "progressing",
  "healthy",
  "degraded",
  "failed",
  "stalled",
  "unknown",
];
const EVIDENCE_STATES: EvidenceState[] = [
  "verified",
  "partial",
  "unverified",
  "conflict",
];
/** The `started_within` values the API accepts, and how to name each. */
const WINDOWS: { value: string; key: "time.lastHours" | "time.lastDays"; count: number }[] = [
  { value: "24h", key: "time.lastHours", count: 24 },
  { value: "7d", key: "time.lastDays", count: 7 },
  { value: "30d", key: "time.lastDays", count: 30 },
];

/** A short ref as a small mono pill with an icon — or its honest absence. */
function RefPill({
  icon: Icon,
  value,
  label,
}: {
  icon: LucideIcon;
  value: string | null;
  label: string;
}) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-surface-2 px-2.5 py-0.5">
      <Icon aria-hidden className="h-3.5 w-3.5 text-ink-muted" />
      <ShortRef value={value} label={label} />
    </span>
  );
}

/**
 * One deployment. Workload identity leads with rollout/evidence chips; the
 * provenance refs follow; replicas (with a ready bar), duration and the
 * post-rollout verdict sit on the right. The whole row opens it.
 */
function DeploymentRowView({ row }: { row: DeploymentRow }) {
  const t = useT("deployments");
  const fmt = useFormat();
  const readyShare =
    row.replicas.desired && row.replicas.desired > 0
      ? Math.min(1, (row.replicas.ready ?? 0) / row.replicas.desired)
      : null;
  return (
    <li
      data-testid={`deployment-row-${row.workload_name}`}
      className="relative grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-4 gap-y-3 px-7 py-4 transition-colors hover:bg-surface-hover md:grid-cols-[auto_minmax(0,1fr)_auto]"
    >
      <RowBubble icon={Rocket} tone={ROLLOUT_TONE[row.rollout_state]} />
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
          <Link
            href={`/deployments/${row.id}`}
            className="truncate text-body font-semibold text-ink after:absolute after:inset-0 after:content-['']"
          >
            {row.workload_name}
          </Link>
          <span className="font-mono text-caption text-ink-muted">
            #{row.revision}
          </span>
          <RolloutBadge state={row.rollout_state} />
          <EvidenceBadge state={row.evidence_state} />
        </div>
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-micro text-ink-muted">
          <span className="font-mono">
            {row.cluster.cluster_ref}/{row.namespace} · {row.workload_kind}
          </span>
          {row.project_key ? (
            <span className="font-mono">
              {row.project_key}/{row.environment_key}/{row.service_key}
            </span>
          ) : (
            <span>{t("list.row.unbound")}</span>
          )}
          <RefPill
            icon={Fingerprint}
            value={row.short_digest}
            label={t("ref.imageDigest")}
          />
          <RefPill
            icon={GitCommitHorizontal}
            value={row.short_commit}
            label={t("ref.commit")}
          />
        </div>
        {row.rollout_reason ? (
          <p className="mt-1.5 truncate text-caption text-ink-secondary">
            {row.rollout_reason}
          </p>
        ) : null}
      </div>
      <div className="col-span-2 flex flex-wrap items-center gap-5 md:col-span-1 md:justify-end">
        <div className="w-24">
          <div className="flex items-baseline justify-between text-micro text-ink-muted">
            <span>{t("list.row.ready")}</span>
            <span
              className="font-mono text-caption font-semibold text-ink"
              data-tabular
            >
              {row.replicas.ready ?? "—"} / {row.replicas.desired ?? "—"}
            </span>
          </div>
          <ShareBar
            share={readyShare}
            tone={ROLLOUT_TONE[row.rollout_state]}
            className="mt-1.5"
          />
        </div>
        <span
          className="w-12 text-right text-caption text-ink-secondary"
          data-tabular
        >
          {fmt.duration(
            rolloutDurationSeconds(row.rollout_started_at, row.rollout_completed_at),
            { compact: true },
          )}
        </span>
        <div className="flex w-32 justify-end">
          {row.health_comparison ? (
            <VerdictBadge verdict={row.health_comparison.verdict} />
          ) : (
            <span className="text-micro text-ink-muted">{t("list.row.notCompared")}</span>
          )}
        </div>
      </div>
    </li>
  );
}

/** Evidence grades on this page, as four lanes — never merged into one. */
function EvidenceLanes({ items }: { items: DeploymentRow[] }) {
  const t = useT("deployments");
  const total = items.length;
  return (
    <ul className="space-y-4">
      {EVIDENCE_STATES.map((state) => {
        const count = items.filter(
          (row) => row.evidence_state === state,
        ).length;
        return (
          <li key={state} className="flex flex-col gap-2">
            <div className="flex items-center justify-between gap-3">
              <span className="flex min-w-0 items-center gap-2 text-caption text-ink-secondary">
                <span
                  aria-hidden
                  className={`h-2 w-2 shrink-0 rounded-full ${toneSpec(EVIDENCE_TONE[state]).dot}`}
                />
                <span className="truncate">
                  {t.dyn("evidence", state, EVIDENCE_LABELS[state])}
                </span>
              </span>
              <span data-tabular className="text-body font-semibold text-ink">
                {count}
              </span>
            </div>
            <ShareBar
              share={total > 0 ? count / total : null}
              tone={EVIDENCE_TONE[state]}
              className="h-2"
            />
          </li>
        );
      })}
    </ul>
  );
}

function DeploymentTable() {
  const t = useT("deployments");
  const c = useT("common");
  const [rolloutState, setRolloutState] = useState<RolloutState | "">("");
  const [evidenceState, setEvidenceState] = useState<EvidenceState | "">("");
  const [startedWithin, setStartedWithin] = useState("");

  const [page, retry] = useApi<DeploymentPage>(
    deploymentListPath({
      rolloutState: rolloutState || undefined,
      evidenceState: evidenceState || undefined,
      startedWithin: startedWithin || undefined,
    }),
  );

  const items = page.state === "ready" ? page.data.items : [];
  const total = items.length;
  const failed = items.filter((row) => row.rollout_state === "failed").length;
  const degraded = items.filter(
    (row) => row.rollout_state === "degraded",
  ).length;
  const stalled = items.filter((row) => row.rollout_state === "stalled").length;
  const progressing = items.filter(
    (row) => row.rollout_state === "progressing",
  ).length;
  const healthy = items.filter((row) => row.rollout_state === "healthy").length;
  const unknownState = items.filter((row) =>
    ["unknown", "pending"].includes(row.rollout_state),
  ).length;
  const verified = items.filter(
    (row) => row.evidence_state === "verified",
  ).length;
  const share = (count: number) => (total > 0 ? count / total : null);

  const windowLabel = (value: string): string => {
    const window = WINDOWS.find((entry) => entry.value === value);
    return window ? c(window.key, { count: window.count }) : value;
  };

  return (
    <div className="flex flex-col gap-6">
      {page.state === "ready" ? (
        <div className="page-grid motion-safe:animate-[fade-in_360ms_var(--ease-entrance)_backwards]">
          <KpiTile
            data-testid="deployment-kpi-healthy"
            label={t("list.kpi.healthy")}
            value={healthy}
            icon={CheckCircle2}
            tone="success"
            share={share(healthy)}
            caption={t("list.kpi.healthyCaption", { count: total })}
          />
          <KpiTile
            data-testid="deployment-kpi-failed-or-degraded"
            label={t("list.kpi.failedOrDegraded")}
            value={failed + degraded}
            icon={XCircle}
            tone="critical"
            share={share(failed + degraded)}
            caption={t("list.kpi.failedOrDegradedCaption", { failed, degraded })}
          />
          <KpiTile
            data-testid="deployment-kpi-stalled"
            label={t("list.kpi.stalled")}
            value={stalled}
            icon={Hourglass}
            tone="warning"
            share={share(stalled)}
            caption={t("list.kpi.stalledCaption", { count: progressing })}
          />
          <KpiTile
            data-testid="deployment-kpi-verified-evidence"
            label={t("list.kpi.verified")}
            value={verified}
            icon={ShieldCheck}
            tone="info"
            share={share(verified)}
            caption={t("list.kpi.verifiedCaption")}
          />
        </div>
      ) : null}

      <Toolbar
        data-testid="deployment-filters"
        summary={
          page.state === "ready"
            ? t("list.filters.summary", { shown: total, total: page.data.total })
            : undefined
        }
      >
        <PillSelect
          label={t("list.filters.rollout")}
          value={rolloutState}
          placeholder={t("list.filters.any")}
          options={ROLLOUT_STATES.map((value) => ({
            value,
            label: t.dyn("rollout", value, value),
          }))}
          onChange={(value) => setRolloutState(value as RolloutState | "")}
        />
        <PillSelect
          label={t("list.filters.evidence")}
          value={evidenceState}
          placeholder={t("list.filters.any")}
          options={EVIDENCE_STATES.map((value) => ({
            value,
            label: t.dyn("evidence", value, EVIDENCE_LABELS[value]),
          }))}
          onChange={(value) => setEvidenceState(value as EvidenceState | "")}
        />
        <PillSelect
          label={t("list.filters.startedWithin")}
          value={startedWithin}
          placeholder={t("list.filters.anyTime")}
          options={WINDOWS.map(({ value }) => ({ value, label: windowLabel(value) }))}
          onChange={setStartedWithin}
        />
      </Toolbar>

      <div className="page-split">
        <div className="page-main">
          <Panel
            flush
            className="motion-safe:animate-[fade-in_420ms_var(--ease-entrance)_backwards] [animation-delay:60ms]"
          >
            <CardTitle
              flush
              icon={Rocket}
              title={t("list.table.title")}
              description={t("list.table.description")}
            />
            {page.state === "loading" ? (
              <StatePad>
                <DataState kind="loading" />
              </StatePad>
            ) : null}
            {page.state === "error" ? (
              <StatePad>
                {page.notFound ? (
                  <DataState
                    kind="permission-denied"
                    description={t("list.table.forbidden")}
                  />
                ) : (
                  <DataState
                    kind="error"
                    description={page.message}
                    onRetry={retry}
                  />
                )}
              </StatePad>
            ) : null}
            {page.state === "ready" && total === 0 ? (
              <EmptyHero
                icon={PackageSearch}
                title={t("list.empty.title")}
                description={t("list.empty.description")}
              >
                <FactPill>
                  {t("list.empty.rollout", {
                    value: rolloutState
                      ? t.dyn("rollout", rolloutState, rolloutState)
                      : t("list.filters.any"),
                  })}
                </FactPill>
                <FactPill>
                  {t("list.empty.evidence", {
                    value: evidenceState
                      ? t.dyn("evidence", evidenceState, EVIDENCE_LABELS[evidenceState])
                      : t("list.filters.any"),
                  })}
                </FactPill>
                <FactPill>
                  {t("list.empty.started", {
                    value: startedWithin ? windowLabel(startedWithin) : t("list.filters.anyTime"),
                  })}
                </FactPill>
              </EmptyHero>
            ) : null}
            {page.state === "ready" && total > 0 ? (
              <>
                <ul
                  className="divide-y divide-border"
                  data-testid="deployment-table"
                >
                  {items.map((row) => (
                    <DeploymentRowView key={row.id} row={row} />
                  ))}
                </ul>
                <p className="border-t border-border px-7 py-4 text-micro text-ink-muted">
                  {t("list.table.footer", { shown: items.length, total: page.data.total })}
                </p>
              </>
            ) : null}
          </Panel>
        </div>

        {page.state === "ready" ? (
          <div className="page-aside">
            <Panel
              data-testid="deployment-summary"
              className="motion-safe:animate-[fade-in_440ms_var(--ease-entrance)_backwards] [animation-delay:80ms]"
            >
              <CardTitle
                icon={Rocket}
                title={t("list.outcomes.title")}
                description={t("list.outcomes.description")}
              />
              <BreakdownRing
                label={t("list.outcomes.chart")}
                centerCaption={t("list.outcomes.center")}
                slices={[
                  { name: t("rollout.failed"), value: failed, tone: "critical" },
                  { name: t("rollout.degraded"), value: degraded, tone: "warning" },
                  { name: t("rollout.stalled"), value: stalled, tone: "warning" },
                  { name: t("rollout.progressing"), value: progressing, tone: "info" },
                  { name: t("rollout.healthy"), value: healthy, tone: "success" },
                  { name: t("rollout.unknown"), value: unknownState, tone: "unknown" },
                ]}
              />
            </Panel>
            <Panel className="motion-safe:animate-[fade-in_460ms_var(--ease-entrance)_backwards] [animation-delay:100ms]">
              <CardTitle
                icon={ShieldCheck}
                title={t("list.evidencePanel.title")}
                description={t("list.evidencePanel.description")}
              />
              {/* `unverified` is its own lane and deliberately not red: an
                  absence of evidence is not a failed rollout. */}
              <EvidenceLanes items={items} />
            </Panel>
          </div>
        ) : null}
      </div>
    </div>
  );
}

export default function DeploymentsPage() {
  const t = useT("deployments");
  return (
    <PageFrame>
      <PageHeader title={t("list.title")} description={t("list.description")} />
      <Suspense fallback={<DataState kind="loading" />}>
        <DeploymentTable />
      </Suspense>
    </PageFrame>
  );
}
