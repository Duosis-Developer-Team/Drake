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
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { Fragment, Suspense, useMemo, type ReactNode } from "react";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";

import { kindColour } from "@/components/apps/AppLauncher";
import { CountTile, ListFooter, useCursorList, useTotal, useUrlFilters } from "@/components/ops/ListKit";
import {
  AppTag,
  BandCard,
  Figure,
  HeatGrid,
  Legend,
  ListDivider,
  dayAxis,
  dayIndex,
  useDayName,
  useProjectIndex,
  useSample,
  type ProjectRef,
} from "@/components/ops/Visuals";
import { ProjectLogo } from "@/components/ui/ProjectLogo";
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
  VERDICT_LABELS,
  deploymentListPath,
  rolloutDurationSeconds,
  type ComparisonVerdict,
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
function DeploymentRowView({ row, projects }: { row: DeploymentRow; projects: Map<string, ProjectRef> }) {
  const t = useT("deployments");
  const fmt = useFormat();
  const readyShare =
    row.replicas.desired && row.replicas.desired > 0
      ? Math.min(1, (row.replicas.ready ?? 0) / row.replicas.desired)
      : null;
  return (
    <li
      data-testid={`deployment-row-${row.workload_name}`}
      className="relative grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-4 gap-y-3 py-4 pr-6 pl-7 transition-colors hover:bg-surface-hover md:grid-cols-[auto_minmax(0,1fr)_auto]"
    >
      <span aria-hidden className="absolute inset-y-3 left-0 w-[3px] rounded-r-full" style={{ background: `var(${toneSpec(ROLLOUT_TONE[row.rollout_state]).token})` }} />
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
        <div className="mt-2 flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
          <AppTag
            projectKey={row.project_key}
            environmentKey={row.environment_key}
            projects={projects}
            outside={t("list.row.unbound")}
          />
          {row.service_key ? <span className="font-mono text-micro text-ink-muted">{row.service_key}</span> : null}
          <span className="text-micro text-ink-muted">
            · {t("list.started", { when: fmt.relative(row.rollout_started_at) })}
          </span>
        </div>
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-micro text-ink-muted">
          <span className="font-mono">
            {row.cluster.cluster_ref}/{row.namespace} · {row.workload_kind}
          </span>
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
        <div className="flex w-40 justify-end">
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

const VERDICT_COLOUR: Record<ComparisonVerdict, string> = {
  improved: "var(--status-success)",
  stable: "var(--status-info)",
  regressed: "var(--status-critical)",
  insufficient_data: "var(--text-muted)",
};
const VERDICTS: ComparisonVerdict[] = ["improved", "stable", "regressed", "insufficient_data"];

/**
 * The last 30 days of rollouts in one card: a heat grid of rollouts per day
 * for each app's environment (a ring on a day that failed or regressed),
 * the window's figures, what health did afterwards, and the evidence lanes.
 * Drawn from a bounded sample; the tiles above are the server's totals.
 */
function DeployBand({
  projects,
  evidence,
}: {
  projects: Map<string, ProjectRef>;
  evidence: ReactNode;
}) {
  const t = useT("deployments");
  const fmt = useFormat();
  const sample = useSample<DeploymentRow>(deploymentListPath({ startedWithin: "30d" }), { max: 250 });
  const axis = useMemo(() => dayAxis(30), []);
  const items = useMemo(() => sample.items ?? [], [sample.items]);
  const rows = useMemo(() => {
    const places = new Map<string, { key: string; project: string | null; env: string | null; counts: number[]; bad: boolean[]; total: number }>();
    for (const row of items) {
      const i = dayIndex(axis, row.rollout_started_at);
      if (i < 0) continue;
      const key = row.project_key ? `${row.project_key}/${row.environment_key}` : "";
      const place = places.get(key) ?? {
        key,
        project: row.project_key,
        env: row.environment_key,
        counts: axis.map(() => 0),
        bad: axis.map(() => false),
        total: 0,
      };
      place.counts[i] += 1;
      place.total += 1;
      if (row.rollout_state === "failed" || row.health_comparison?.verdict === "regressed") place.bad[i] = true;
      places.set(key, place);
    }
    return [...places.values()].sort((a, b) => (a.key === "" ? 1 : b.key === "" ? -1 : b.total - a.total));
  }, [axis, items]);
  const days = new Set(items.map((r) => dayIndex(axis, r.rollout_started_at)).filter((i) => i >= 0)).size;
  const clean = items.filter((r) => r.rollout_state === "healthy").length;
  const verdicts = VERDICTS.map((v) => ({ v, n: items.filter((r) => r.health_comparison?.verdict === v).length }));
  const verdictTotal = verdicts.reduce((a, b) => a + b.n, 0);

  return (
    <BandCard
      data-testid="deployment-band"
      title={t("list.band.title")}
      note={sample.items && !sample.complete ? t("list.band.truncated", { count: items.length }) : undefined}
    >
      {sample.failed ? (
        <p className="text-caption text-ink-muted">{t("list.band.failed")}</p>
      ) : (
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.75fr)_minmax(0,1fr)]">
          <div className="flex min-w-0 flex-col gap-4">
            <div className="grid grid-cols-3 gap-4">
              <Figure label={t("list.band.total")} value={sample.items ? fmt.number(items.length) : "—"} note={t("list.band.totalNote")} />
              <Figure
                label={t("list.band.perDay")}
                value={days ? fmt.number(items.length / days, { maximumFractionDigits: 1 }) : "—"}
                note={t("list.band.perDayNote")}
              />
              <Figure
                label={t("list.band.success")}
                value={items.length ? `${fmt.number((clean / items.length) * 100, { maximumFractionDigits: 0 })}%` : "—"}
                note={t("list.band.successNote", { count: clean, total: items.length })}
                tone={items.length && clean === items.length ? "success" : undefined}
              />
            </div>
            {rows.length === 0 ? (
              <p className="rounded-control border border-dashed border-border px-4 py-6 text-center text-caption text-ink-muted">
                {sample.items ? t("list.band.none") : "…"}
              </p>
            ) : (
              <HeatGrid
                axis={axis}
                label={t("list.band.note")}
                rows={rows.slice(0, 8).map((place) => {
                  const project = place.project ? projects.get(place.project) : undefined;
                  return {
                    key: place.key || "outside",
                    counts: place.counts,
                    bad: place.bad,
                    label: place.project ? (
                      <span className="inline-flex min-w-0 items-center gap-1.5">
                        {project ? <ProjectLogo projectId={project.id} name={project.name} size="sm" variant="mark" /> : null}
                        <span className="truncate text-caption font-medium text-ink">{project?.name ?? place.project}</span>
                        <span className="inline-flex items-center gap-1 font-mono text-[10.5px] text-ink-muted">
                          <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: kindColour(place.env ?? "") }} />
                          {place.env}
                        </span>
                      </span>
                    ) : (
                      <span className="text-caption text-ink-muted">{t("list.band.outside")}</span>
                    ),
                  };
                })}
              />
            )}
          </div>
          <div className="flex min-w-0 flex-col gap-5 xl:border-l xl:border-border xl:pl-6">
            <div>
              <p className="mb-2 text-[11px] font-medium text-ink-muted">{t("list.band.after")}</p>
              <span className="flex h-2.5 overflow-hidden rounded-full bg-surface-3">
                {verdictTotal
                  ? verdicts.map(({ v, n }) =>
                      n ? <span key={v} style={{ flexGrow: n, background: VERDICT_COLOUR[v] }} title={`${t.dyn("verdict", v, VERDICT_LABELS[v])}: ${n}`} /> : null,
                    )
                  : null}
              </span>
              <div className="mt-2.5">
                <Legend
                  items={verdicts.map(({ v, n }) => ({ label: t.dyn("verdict", v, VERDICT_LABELS[v]), colour: VERDICT_COLOUR[v], value: n }))}
                />
              </div>
            </div>
            {evidence}
          </div>
        </div>
      )}
    </BandCard>
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
  const projects = useProjectIndex();
  const dayName = useDayName();

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

      <DeployBand
        projects={projects}
        evidence={
          <div data-testid="deployment-summary">
            <p className="mb-1 text-[11px] font-medium text-ink-muted">
              {t("list.evidencePanel.title")} · <span className="font-mono">{t("list.evidencePanel.description")}</span>
            </p>
            {/* `unverified` is its own lane and deliberately not red: an
                absence of evidence is not a failed rollout. */}
            <EvidenceLanes
              totals={evidence}
              active={evidenceState}
              onPick={(state) => update({ evidence_state: state })}
            />
          </div>
        }
      />

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
                  {list.items.map((row, i) => {
                    const name = dayName(row.rollout_started_at, c("time.today"), c("time.yesterday"));
                    const previous = i > 0 ? dayName(list.items[i - 1].rollout_started_at, c("time.today"), c("time.yesterday")) : null;
                    return (
                      <Fragment key={row.id}>
                        {name !== previous ? <ListDivider label={name} /> : null}
                        <DeploymentRowView row={row} projects={projects} />
                      </Fragment>
                    );
                  })}
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
