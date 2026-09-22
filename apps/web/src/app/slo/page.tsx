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

import { CircleCheck, Flame, HelpCircle, Target, XCircle } from "lucide-react";
import Link from "next/link";
import { Suspense, useState } from "react";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";

import { SloBadge } from "@/components/alerting/primitives";
import { useApi } from "@/components/catalog/primitives";
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
import { StatusBadge } from "@/components/state/StatusBadge";
import { Panel } from "@/components/ui/Panel";
import type { StatusTone } from "@/lib/design/status";
import { useFormat, useT } from "@/lib/i18n";
import {
  SLO_EXPLANATIONS,
  SLO_LABELS,
  formatBudget,
  formatRatio,
  sloListPath,
  type Page,
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
              ratio: formatRatio(slo.objective_ratio),
              window: fmt.duration(slo.window_seconds, { compact: true }),
            })}
          </span>
          {!activeBurn ? <span>{t("sloList.row.notBurning")}</span> : null}
          <span>{evaluation ? fmt.relative(evaluation.evaluated_for) : "—"}</span>
        </div>
      </div>
      <div className="col-span-2 grid grid-cols-2 gap-6 lg:col-span-1 lg:w-80">
        <div>
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-micro text-ink-muted">{t("sloList.row.compliance")}</span>
            <span data-tabular className="text-body font-semibold text-ink">
              {formatRatio(evaluation?.compliance_ratio ?? null)}
            </span>
          </div>
          <p className="mt-2 truncate text-micro text-ink-muted">
            {t("sloList.row.ofTarget", { ratio: formatRatio(slo.objective_ratio) })}
          </p>
        </div>
        <div>
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-micro text-ink-muted">{t("sloList.row.budgetLeft")}</span>
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

function SloInner() {
  const t = useT("alerting");
  const [status, setStatus] = useState<string>("");
  const [page, retry] = useApi<Page<Slo>>(sloListPath({ status }));

  const items = page.state === "ready" ? page.data.items : [];
  const total = items.length;
  const meeting = items.filter(
    (slo) => slo.evaluation?.status === "healthy",
  ).length;
  const burning = items.filter(
    (slo) => slo.evaluation?.status === "warning",
  ).length;
  const breached = items.filter((slo) =>
    ["critical", "exhausted", "query_failed"].includes(
      slo.evaluation?.status ?? "",
    ),
  ).length;
  const stale = items.filter(
    (slo) => slo.evaluation?.status === "stale",
  ).length;
  const neverMeasured = items.filter(
    (slo) =>
      !slo.evaluation ||
      ["insufficient_data", "not_configured"].includes(slo.evaluation.status),
  ).length;
  const share = (count: number) => (total > 0 ? count / total : null);

  return (
    <PageFrame>
      <PageHeader
        title={t("sloList.title")}
        description={t("sloList.description")}
      />
      <div className="flex flex-col gap-6">
        {page.state === "ready" ? (
          <div className="page-grid motion-safe:animate-[fade-in_360ms_var(--ease-entrance)_backwards]">
            <KpiTile
              data-testid="alerting-kpi-meeting"
              label={t("sloList.kpi.meeting")}
              value={meeting}
              icon={CircleCheck}
              tone="success"
              share={share(meeting)}
              caption={t("sloList.kpi.meetingCaption")}
            />
            <KpiTile
              data-testid="alerting-kpi-burning-fast"
              label={t("sloList.kpi.burning")}
              value={burning}
              icon={Flame}
              tone="warning"
              share={share(burning)}
              caption={t("sloList.kpi.burningCaption")}
            />
            <KpiTile
              data-testid="alerting-kpi-breached"
              label={t("sloList.kpi.breached")}
              value={breached}
              icon={XCircle}
              tone="critical"
              share={share(breached)}
              caption={t("sloList.kpi.breachedCaption")}
            />
            <KpiTile
              data-testid="alerting-kpi-never-measured"
              label={t("sloList.kpi.neverMeasured")}
              value={neverMeasured}
              icon={HelpCircle}
              tone="unknown"
              share={share(neverMeasured)}
              caption={t("sloList.kpi.neverMeasuredCaption")}
            />
          </div>
        ) : null}

        <Toolbar
          data-testid="slo-filters"
          summary={
            page.state === "ready"
              ? t("sloList.shown", { shown: total, total: page.data.total })
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
            onChange={setStatus}
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
                icon={Target}
                title={t("sloList.heading.title")}
                description={t("sloList.heading.description")}
              />
              {page.state === "loading" ? (
                <StatePad>
                  <DataState kind="loading" />
                </StatePad>
              ) : page.state === "error" ? (
                <StatePad>
                  {page.notFound ? (
                    <DataState kind="permission-denied" />
                  ) : (
                    <DataState
                      kind="error"
                      description={page.message}
                      onRetry={retry}
                    />
                  )}
                </StatePad>
              ) : total === 0 ? (
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

          {page.state === "ready" ? (
            /* insufficient_data and not_configured stay OUT of the healthy
               bucket: nothing was measured, and a green slice for an
               unmeasured objective is the most misleading thing here. */
            <div className="page-aside">
              <Panel
                data-testid="slo-overview"
                className="motion-safe:animate-[fade-in_440ms_var(--ease-entrance)_backwards] [animation-delay:80ms]"
              >
                <CardTitle
                  icon={Target}
                  title={t("sloList.byVerdict.title")}
                  description={t("sloList.byVerdict.description")}
                />
                <BreakdownRing
                  label={t("sloList.byVerdict.ringLabel")}
                  centerCaption={t("sloList.byVerdict.center")}
                  slices={[
                    { name: t("sloList.byVerdict.meeting"), value: meeting, tone: "success" },
                    { name: t("sloList.byVerdict.burning"), value: burning, tone: "warning" },
                    { name: t("sloList.byVerdict.breached"), value: breached, tone: "critical" },
                    { name: t("sloList.byVerdict.stale"), value: stale, tone: "stale" },
                    {
                      name: t("sloList.byVerdict.neverMeasured"),
                      value: neverMeasured,
                      tone: "unknown",
                    },
                  ]}
                />
              </Panel>
            </div>
          ) : null}
        </div>
      </div>
    </PageFrame>
  );
}

export default function SloPage() {
  return (
    <Suspense fallback={<DataState kind="loading" />}>
      <SloInner />
    </Suspense>
  );
}
