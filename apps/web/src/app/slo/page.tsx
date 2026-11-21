"use client";

/**
 * SLO overview.
 *
 * One row per objective, and four numbers that are deliberately not one:
 * the objective, what compliance actually was, how much error budget is
 * left, and whether it is currently burning fast enough to matter.
 *
 * `insufficient_data`, `stale`, `query_failed` and `not_configured` each
 * render as themselves. None of them renders as healthy, and none renders
 * as 0% — "we could not measure" and "we measured zero" are different
 * answers and this screen keeps them apart.
 */

import { Target } from "lucide-react";
import Link from "next/link";
import { Suspense, useEffect, useState } from "react";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";

import { SloBadge } from "@/components/alerting/primitives";
import {
  NewSloButton,
  SloComposer,
  SloSuggestions,
} from "@/components/alerting/SloComposer";
import {
  CountTile,
  ListFooter,
  useCursorList,
  useTotal,
  useUrlFilters,
} from "@/components/ops/ListKit";
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
import { StatusBadge } from "@/components/state/StatusBadge";
import { Panel } from "@/components/ui/Panel";
import type { StatusTone } from "@/lib/design/status";
import { useFormat, useT } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import {
  SLO_EXPLANATIONS,
  SLO_LABELS,
  formatBudget,
  fetchSloOptions,
  formatRatio,
  formatTarget,
  sloListPath,
  type SloOptions,
  type Slo,
  type SloStatus,
} from "@/lib/alerting";

const STATES: SloStatus[] = [
  "healthy",
  "warning",
  "critical",
  "exhausted",
  "insufficient_data",
  "stale",
  "query_failed",
  "not_configured",
];

/** Verdict → tone. The unmeasured states are never the healthy tone. */
const SLO_TONE: Record<SloStatus, StatusTone> = {
  healthy: "success",
  warning: "warning",
  critical: "critical",
  exhausted: "critical",
  insufficient_data: "unknown",
  not_configured: "unknown",
  stale: "stale",
  query_failed: "critical",
};

/**
 * One objective. Name, verdict and any active burn lead; then compliance
 * and remaining budget as the two numbers this screen exists to keep apart,
 * each with its own bar. The whole row opens the objective.
 */
function SloRow({ slo }: { slo: Slo }) {
  const t = useT("alerting");
  const fmt = useFormat();
  const evaluation = slo.evaluation;
  const activeBurn = evaluation?.burn_rates.find((rate) => rate.active) ?? null;
  const tone: StatusTone = evaluation ? SLO_TONE[evaluation.status] : "unknown";
  const remaining = evaluation?.error_budget_remaining ?? null;
  return (
    <li
      data-testid={`slo-row-${slo.slo_key}`}
      className="relative grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-4 gap-y-3 px-7 py-4 transition-colors hover:bg-surface-hover lg:grid-cols-[auto_minmax(0,1fr)_auto]"
    >
      <RowBubble icon={Target} tone={tone} />
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
          <Link
            href={`/slo/${slo.id}`}
            className="truncate text-body font-semibold text-ink after:absolute after:inset-0 after:content-['']"
          >
            {slo.display_name}
          </Link>
          {evaluation ? (
            <SloBadge status={evaluation.status} />
          ) : (
            /* Never "healthy" for something nobody has measured. */
            <StatusBadge
              status="unknown"
              label={t("sloList.row.notEvaluated")}
              size="compact"
            />
          )}
          {activeBurn ? (
            <StatusBadge
              status={
                activeBurn.severity === "critical" ? "critical" : "warning"
              }
              label={`${activeBurn.factor}× ${activeBurn.name}`}
              size="compact"
            />
          ) : null}
        </div>
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-micro text-ink-muted">
          <span className="font-mono">
            {[slo.project_key, slo.environment_key, slo.service_key]
              .filter(Boolean)
              .join("/")}{" "}
            · {t.dyn("indicator", slo.indicator, slo.indicator)}
          </span>
          <span aria-hidden className="h-1 w-1 rounded-full bg-border-strong" />
          <span className="text-caption text-ink-secondary">
            {t("sloList.row.target", {
              ratio: formatTarget(slo.objective_ratio),
              window: fmt.duration(slo.window_seconds, { compact: true }),
            })}
          </span>
          {!activeBurn ? <span>{t("sloList.row.notBurning")}</span> : null}
          <span>
            {evaluation ? fmt.relative(evaluation.evaluated_for) : "—"}
          </span>
        </div>
      </div>
      <div className="col-span-2 grid grid-cols-2 gap-6 lg:col-span-1 lg:w-80">
        <div>
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-micro text-ink-muted">
              {t("sloList.row.compliance")}
            </span>
            <span data-tabular className="text-body font-semibold text-ink">
              {formatRatio(evaluation?.compliance_ratio ?? null)}
            </span>
          </div>
          <p className="mt-2 truncate text-micro text-ink-muted">
            {t("sloList.row.ofTarget", {
              ratio: formatRatio(slo.objective_ratio),
            })}
          </p>
        </div>
        <div>
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-micro text-ink-muted">
              {t("sloList.row.budgetLeft")}
            </span>
            <span
              data-tabular
              className={`text-body font-semibold ${
                (remaining ?? 0) < 0 ? "text-critical" : "text-ink"
              }`}
            >
              {formatBudget(remaining)}
            </span>
          </div>
          <ShareBar
            share={remaining}
            tone={(remaining ?? 0) < 0.25 ? "critical" : tone}
            className="mt-2"
          />
        </div>
      </div>
    </li>
  );
}

const FILTER_KEYS = ["status"] as const;

function withLimit(path: string): string {
  return `${path}${path.includes("?") ? "&" : "?"}limit=1`;
}

function SloInner({ onChanged }: { onChanged: () => void }) {
  const t = useT("alerting");
  const { state: session } = useSession();
  const csrfToken =
    session.status === "authenticated" ? session.me.csrf_token : null;
  const [options, setOptions] = useState<SloOptions | null>(null);
  const [composing, setComposing] = useState(false);
  useEffect(() => {
    let active = true;
    fetchSloOptions()
      .then((value) => active && setOptions(value))
      .catch(() => active && setOptions(null));
    return () => {
      active = false;
    };
  }, []);
  const canCompose = Boolean(
    options?.can_manage && csrfToken && options.services.length > 0,
  );
  const [filters, update] = useUrlFilters(FILTER_KEYS);
  const status = (STATES as string[]).includes(filters.status)
    ? filters.status
    : "";
  const [list, retry] = useCursorList<Slo>(sloListPath({ status }), {
    paging: "offset",
  });
  const items = list.state === "ready" ? list.items : [];

  // Server totals, not a tally of the loaded page. Breached spans three
  // states, so it is a sum; "never measured" also covers objectives with no
  // evaluation at all, so it is what remains of the whole.
  const all = useTotal(withLimit(sloListPath({})));
  const meeting = useTotal(withLimit(sloListPath({ status: "healthy" })));
  const burning = useTotal(withLimit(sloListPath({ status: "warning" })));
  const critical = useTotal(withLimit(sloListPath({ status: "critical" })));
  const exhausted = useTotal(withLimit(sloListPath({ status: "exhausted" })));
  const failed = useTotal(withLimit(sloListPath({ status: "query_failed" })));
  const stale = useTotal(withLimit(sloListPath({ status: "stale" })));
  const sum = (values: (number | null)[]) =>
    values.some((v) => v === null)
      ? null
      : values.reduce<number>((a, v) => a + (v ?? 0), 0);
  const breached = sum([critical, exhausted, failed]);
  const known = sum([meeting, burning, breached, stale]);
  const neverMeasured =
    all === null || known === null ? null : Math.max(0, all - known);
  const toggle = (value: string) =>
    update({ status: status === value ? "" : value });

  return (
    <PageFrame>
      <PageHeader
        title={t("sloList.title")}
        description={t("sloList.description")}
        actions={
          canCompose ? (
            <NewSloButton onClick={() => setComposing(true)} />
          ) : undefined
        }
      />
      {options && canCompose && csrfToken ? (
        <SloComposer
          open={composing}
          onClose={() => setComposing(false)}
          options={options}
          csrfToken={csrfToken}
          onCreated={onChanged}
        />
      ) : null}
      <div className="flex flex-col gap-5">
        <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
          <CountTile
            data-testid="alerting-kpi-meeting"
            label={t("sloList.kpi.meeting")}
            value={meeting}
            tone="success"
            active={status === "healthy"}
            onClick={() => toggle("healthy")}
            caption={t("sloList.kpi.meetingCaption")}
          />
          <CountTile
            data-testid="alerting-kpi-burning-fast"
            label={t("sloList.kpi.burning")}
            value={burning}
            tone="warning"
            active={status === "warning"}
            onClick={() => toggle("warning")}
            caption={t("sloList.kpi.burningCaption")}
          />
          <CountTile
            data-testid="alerting-kpi-breached"
            label={t("sloList.kpi.breached")}
            value={breached}
            tone="critical"
            active={false}
            caption={t("sloList.kpi.breachedCaption")}
          />
          <CountTile
            data-testid="alerting-kpi-never-measured"
            label={t("sloList.kpi.neverMeasured")}
            value={neverMeasured}
            tone="unknown"
            active={false}
            caption={t("sloList.kpi.neverMeasuredCaption")}
          />
        </div>

        {options && csrfToken ? (
          <SloSuggestions
            options={options}
            csrfToken={csrfToken}
            onCreated={onChanged}
          />
        ) : null}

        <Toolbar
          data-testid="slo-filters"
          summary={
            list.state === "ready" && list.total !== null
              ? t("sloList.shown", {
                  shown: list.items.length,
                  total: list.total,
                })
              : undefined
          }
        >
          <PillSelect
            label={t("sloList.filter.state")}
            value={status}
            placeholder={t("sloList.filter.anyState")}
            options={STATES.map((value) => ({
              value,
              label: t.dyn("slo", value, SLO_LABELS[value]),
            }))}
            onChange={(value) => update({ status: value })}
          />
        </Toolbar>

        <div>
          <div>
            <Panel
              flush
              className="motion-safe:animate-[fade-in_420ms_var(--ease-entrance)_backwards] [animation-delay:60ms]"
            >
              <CardTitle
                flush
                icon={Target}
                title={t("sloList.heading.title")}
                description={t("sloList.heading.description")}
              />
              {list.state === "loading" ? (
                <StatePad>
                  <DataState kind="loading" />
                </StatePad>
              ) : list.state === "error" ? (
                <StatePad>
                  {list.denied ? (
                    <DataState kind="permission-denied" />
                  ) : (
                    <DataState
                      kind="error"
                      description={list.message}
                      onRetry={retry}
                    />
                  )}
                </StatePad>
              ) : items.length === 0 ? (
                <EmptyHero
                  icon={Target}
                  title={t("sloList.empty.title")}
                  description={t("sloList.empty.description")}
                >
                  <FactPill>
                    {t("sloList.empty.state", {
                      value: status
                        ? t.dyn("slo", status, SLO_LABELS[status as SloStatus])
                        : t("sloList.empty.any"),
                    })}
                  </FactPill>
                </EmptyHero>
              ) : (
                <>
                  <ul className="divide-y divide-border">
                    {items.map((slo) => (
                      <SloRow key={slo.id} slo={slo} />
                    ))}
                  </ul>
                  <ListFooter
                    shown={items.length}
                    summary={t("sloList.shown", {
                      shown: items.length,
                      total:
                        list.state === "ready"
                          ? (list.total ?? items.length)
                          : items.length,
                    })}
                    hasMore={list.state === "ready" && list.hasMore}
                    loadingMore={list.state === "ready" && list.loadingMore}
                    moreError={list.state === "ready" && list.moreError}
                    onMore={() => list.state === "ready" && list.loadMore()}
                    moreLabel={t("list.more")}
                    loadingLabel={t("list.loadingMore")}
                    errorLabel={t("list.moreError")}
                  />

                  {/* Anything that is not a measurement is explained in words, so
                    nobody reads a dash as a zero. */}
                  {items.some(
                    (slo) =>
                      slo.evaluation === null ||
                      [
                        "insufficient_data",
                        "stale",
                        "query_failed",
                        "not_configured",
                      ].includes(slo.evaluation.status),
                  ) ? (
                    <div
                      className="space-y-2 border-t border-border bg-surface-2/60 px-7 py-5"
                      data-testid="slo-caveats"
                    >
                      {items
                        .filter(
                          (slo) =>
                            slo.evaluation !== null &&
                            [
                              "insufficient_data",
                              "stale",
                              "query_failed",
                              "not_configured",
                            ].includes(slo.evaluation.status),
                        )
                        .map((slo) => (
                          <p
                            key={slo.id}
                            className="text-caption text-ink-secondary"
                          >
                            <span className="font-semibold text-ink">
                              {slo.display_name}
                            </span>
                            {" — "}
                            {t.dyn(
                              "sloExplanation",
                              slo.evaluation!.status,
                              SLO_EXPLANATIONS[slo.evaluation!.status],
                            )}
                          </p>
                        ))}
                      {items.some((slo) => slo.evaluation === null) ? (
                        <p className="text-caption text-ink-secondary">
                          {t("sloList.caveat.notEvaluated")}
                        </p>
                      ) : null}
                    </div>
                  ) : null}
                </>
              )}
            </Panel>
          </div>
        </div>
      </div>
    </PageFrame>
  );
}

export default function SloPage() {
  // A new objective changes the list, every count and the suggestions:
  // remounting reads them all again rather than patching each by hand.
  const [generation, setGeneration] = useState(0);
  return (
    <Suspense fallback={<DataState kind="loading" />}>
      <SloInner
        key={generation}
        onChanged={() => setGeneration((n) => n + 1)}
      />
    </Suspense>
  );
}
