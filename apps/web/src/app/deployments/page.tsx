"use client";

/**
 * Deployment list.
 *
 * One row per observed workload revision: what is running, how much Drake
 * can prove about where it came from, how the rollout went, and how health
 * looked afterwards.
 */

import {
  Fingerprint,
  GitCommitHorizontal,
  PackageSearch,
  Rocket,
  ShieldCheck,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { Suspense } from "react";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";

import { CountTile, ListFooter, useCursorList, useTotal, useUrlFilters } from "@/components/ops/ListKit";
import {
  EVIDENCE_TONE,
  EvidenceBadge,
  ROLLOUT_TONE,
  RolloutBadge,
  ShortRef,
  VerdictBadge,
} from "@/components/deployments/primitives";
import {
  CardTitle,
  EmptyHero,
  FactPill,
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

/** Evidence grades under the current filters, as four lanes — never merged
 *  into one. Each count is the server's own total, and each lane filters. */
function EvidenceLanes({
  totals,
  active,
  onPick,
}: {
  totals: Record<EvidenceState, number | null>;
  active: EvidenceState | "";
  onPick: (state: EvidenceState | "") => void;
}) {
  const t = useT("deployments");
  const all = EVIDENCE_STATES.reduce((sum, state) => sum + (totals[state] ?? 0), 0);
  return (
    <ul className="space-y-1">
      {EVIDENCE_STATES.map((state) => {
        const count = totals[state];
        return (
          <li key={state}>
            <button
              type="button"
              aria-pressed={active === state}
              onClick={() => onPick(active === state ? "" : state)}
              className={`flex w-full flex-col gap-2 rounded-control px-2 py-2 text-left transition-colors ${
                active === state ? "bg-surface-selected" : "hover:bg-surface-hover"
              }`}
            >
              <span className="flex w-full items-center justify-between gap-3">
                <span className="flex min-w-0 items-center gap-2 text-caption text-ink-secondary">
                  <span aria-hidden className={`h-2 w-2 shrink-0 rounded-full ${toneSpec(EVIDENCE_TONE[state]).dot}`} />
                  <span className="truncate">{t.dyn("evidence", state, EVIDENCE_LABELS[state])}</span>
                </span>
                <span data-tabular className="text-body font-semibold text-ink">
                  {count ?? "—"}
                </span>
              </span>
              <ShareBar share={count !== null && all > 0 ? count / all : null} tone={EVIDENCE_TONE[state]} className="h-1.5" />
            </button>
          </li>
        );
      })}
    </ul>
  );
}

const FILTER_KEYS = ["rollout_state", "evidence_state", "started_within"] as const;

function withLimit(path: string): string {
  return `${path}${path.includes("?") ? "&" : "?"}limit=1`;
}

function DeploymentTable() {
  const t = useT("deployments");
  const c = useT("common");
  const [filters, update] = useUrlFilters(FILTER_KEYS);
  const rolloutState = (ROLLOUT_STATES as readonly string[]).includes(filters.rollout_state)
    ? (filters.rollout_state as RolloutState)
    : "";
  const evidenceState = (EVIDENCE_STATES as readonly string[]).includes(filters.evidence_state)
    ? (filters.evidence_state as EvidenceState)
    : "";
  const startedWithin = WINDOWS.some((w) => w.value === filters.started_within) ? filters.started_within : "";

  const [list, retry] = useCursorList<DeploymentRow>(
    deploymentListPath({
      rolloutState: rolloutState || undefined,
      evidenceState: evidenceState || undefined,
      startedWithin: startedWithin || undefined,
    }),
  );
  // Server totals under the other filters; each tile then applies its own.
  const rolloutTotal = (state: RolloutState) =>
    withLimit(deploymentListPath({ rolloutState: state, evidenceState: evidenceState || undefined, startedWithin: startedWithin || undefined }));
  const evidenceTotal = (state: EvidenceState) =>
    withLimit(deploymentListPath({ rolloutState: rolloutState || undefined, evidenceState: state, startedWithin: startedWithin || undefined }));
  const healthy = useTotal(rolloutTotal("healthy"));
  const failed = useTotal(rolloutTotal("failed"));
  const degraded = useTotal(rolloutTotal("degraded"));
  const stalled = useTotal(rolloutTotal("stalled"));
  const evidence: Record<EvidenceState, number | null> = {
    verified: useTotal(evidenceTotal("verified")),
    partial: useTotal(evidenceTotal("partial")),
    unverified: useTotal(evidenceTotal("unverified")),
    conflict: useTotal(evidenceTotal("conflict")),
  };
  const toggleRollout = (state: RolloutState) => update({ rollout_state: rolloutState === state ? "" : state });

  const windowLabel = (value: string): string => {
    const window = WINDOWS.find((entry) => entry.value === value);
    return window ? c(window.key, { count: window.count }) : value;
  };

  return (
    <div className="flex flex-col gap-5">
      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <CountTile
          data-testid="deployment-kpi-healthy"
          label={t("list.kpi.healthy")}
          value={healthy}
          tone="success"
          active={rolloutState === "healthy"}
          onClick={() => toggleRollout("healthy")}
          caption={t("list.kpi.healthyCaption")}
        />
        <CountTile
          data-testid="deployment-kpi-failed"
          label={t("list.kpi.failed")}
          value={failed}
          tone="critical"
          active={rolloutState === "failed"}
          onClick={() => toggleRollout("failed")}
          caption={t("list.kpi.failedCaption")}
        />
        <CountTile
          data-testid="deployment-kpi-degraded"
          label={t("list.kpi.degraded")}
          value={degraded}
          tone="warning"
          active={rolloutState === "degraded"}
          onClick={() => toggleRollout("degraded")}
          caption={t("list.kpi.degradedCaption")}
        />
        <CountTile
          data-testid="deployment-kpi-stalled"
          label={t("list.kpi.stalled")}
          value={stalled}
          tone="warning"
          active={rolloutState === "stalled"}
          onClick={() => toggleRollout("stalled")}
          caption={t("list.kpi.stalledCaption")}
        />
      </div>

      <Toolbar
        data-testid="deployment-filters"
        summary={
          list.state === "ready" && list.total !== null
            ? t("list.filters.summary", { shown: list.items.length, total: list.total })
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
          onChange={(value) => update({ rollout_state: value })}
        />
        <PillSelect
          label={t("list.filters.evidence")}
          value={evidenceState}
          placeholder={t("list.filters.any")}
          options={EVIDENCE_STATES.map((value) => ({
            value,
            label: t.dyn("evidence", value, EVIDENCE_LABELS[value]),
          }))}
          onChange={(value) => update({ evidence_state: value })}
        />
        <PillSelect
          label={t("list.filters.startedWithin")}
          value={startedWithin}
          placeholder={t("list.filters.anyTime")}
          options={WINDOWS.map(({ value }) => ({ value, label: windowLabel(value) }))}
          onChange={(value) => update({ started_within: value })}
        />
      </Toolbar>

      <div className="page-split">
        <div className="page-main">
          <Panel flush>
            <CardTitle
              flush
              icon={Rocket}
              title={t("list.table.title")}
              description={t("list.table.description")}
            />
            {list.state === "loading" ? (
              <StatePad>
                <DataState kind="loading" />
              </StatePad>
            ) : null}
            {list.state === "error" ? (
              <StatePad>
                {list.denied ? (
                  <DataState kind="permission-denied" description={t("list.table.forbidden")} />
                ) : (
                  <DataState kind="error" description={list.message} onRetry={retry} />
                )}
              </StatePad>
            ) : null}
            {list.state === "ready" && list.items.length === 0 ? (
              <EmptyHero
                icon={PackageSearch}
                title={t("list.empty.title")}
                description={t("list.empty.description")}
              >
                <FactPill>
                  {t("list.empty.rollout", {
                    value: rolloutState ? t.dyn("rollout", rolloutState, rolloutState) : t("list.filters.any"),
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
            {list.state === "ready" && list.items.length > 0 ? (
              <>
                <ul className="divide-y divide-border" data-testid="deployment-table">
                  {list.items.map((row) => (
                    <DeploymentRowView key={row.id} row={row} />
                  ))}
                </ul>
                <ListFooter
                  shown={list.items.length}
                  summary={t("list.table.footer", { shown: list.items.length, total: list.total ?? list.items.length })}
                  hasMore={list.hasMore}
                  loadingMore={list.loadingMore}
                  moreError={list.moreError}
                  onMore={list.loadMore}
                  moreLabel={t("list.more")}
                  loadingLabel={t("list.loadingMore")}
                  errorLabel={t("list.moreError")}
                />
              </>
            ) : null}
          </Panel>
        </div>

        <div className="page-aside">
          <Panel data-testid="deployment-summary">
            <CardTitle
              icon={ShieldCheck}
              title={t("list.evidencePanel.title")}
              description={t("list.evidencePanel.description")}
            />
            {/* `unverified` is its own lane and deliberately not red: an
                absence of evidence is not a failed rollout. */}
            <EvidenceLanes
              totals={evidence}
              active={evidenceState}
              onPick={(state) => update({ evidence_state: state })}
            />
          </Panel>
        </div>
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
