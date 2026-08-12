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

import Link from "next/link";
import { Suspense, useState } from "react";
import { PageFrame } from "@/components/shell/AppShell";

import { SloBadge } from "@/components/alerting/primitives";
import { useApi } from "@/components/catalog/primitives";
import { Donut } from "@/components/charts/visuals";
import { DataState } from "@/components/state/DataState";
import { StatusBadge } from "@/components/state/StatusBadge";
import { Card } from "@/components/ui/Card";
import {
  SLO_EXPLANATIONS,
  formatAge,
  formatBudget,
  formatRatio,
  formatWindow,
  sloListPath,
  type Page,
  type Slo,
  type SloStatus,
} from "@/lib/alerting";

const SELECT_CLASS =
  "rounded-lg border border-border bg-surface px-2.5 py-1.5 text-xs text-ink";

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

function SloRow({ slo }: { slo: Slo }) {
  const evaluation = slo.evaluation;
  const activeBurn = evaluation?.burn_rates.find((rate) => rate.active) ?? null;
  return (
    <tr className="border-t border-border align-top" data-testid={`slo-row-${slo.slo_key}`}>
      <td className="py-2.5 pr-3">
        <div className="flex flex-col gap-1">
          <Link
            href={`/slo/${slo.id}`}
            className="text-sm font-medium text-ink hover:underline"
          >
            {slo.display_name}
          </Link>
          <span className="font-mono text-[11px] text-ink-muted">
            {[slo.project_key, slo.environment_key, slo.service_key]
              .filter(Boolean)
              .join("/")}{" "}
            · {slo.indicator}
          </span>
        </div>
      </td>
      <td className="py-2.5 pr-3 text-sm text-ink">
        {formatRatio(slo.objective_ratio)}
        <div className="text-[11px] text-ink-muted">
          over {formatWindow(slo.window_seconds)}
        </div>
      </td>
      <td className="py-2.5 pr-3">
        {evaluation ? (
          <SloBadge status={evaluation.status} />
        ) : (
          /* Never "healthy" for something nobody has measured. */
          <span className="text-[11px] italic text-ink-muted">not evaluated</span>
        )}
      </td>
      <td className="py-2.5 pr-3 text-sm text-ink">
        {formatRatio(evaluation?.compliance_ratio ?? null)}
      </td>
      <td className="py-2.5 pr-3 text-sm">
        <span
          className={
            (evaluation?.error_budget_remaining ?? 0) < 0 ? "text-critical" : "text-ink"
          }
        >
          {formatBudget(evaluation?.error_budget_remaining ?? null)}
        </span>
      </td>
      <td className="py-2.5 pr-3">
        {activeBurn ? (
          <StatusBadge
            status={activeBurn.severity === "critical" ? "critical" : "warning"}
            label={`${activeBurn.factor}× ${activeBurn.name}`}
          />
        ) : (
          <span className="text-[11px] text-ink-muted">not burning</span>
        )}
      </td>
      <td className="py-2.5 text-xs text-ink-secondary">
        {evaluation ? formatAge(evaluation.evaluated_for) : "—"}
      </td>
    </tr>
  );
}

function SloInner() {
  const [status, setStatus] = useState<string>("");
  const [page, retry] = useApi<Page<Slo>>(sloListPath({ status }));

  return (
    <PageFrame>
      <div className="space-y-5">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold text-ink">Service objectives</h1>
        <p className="text-sm text-ink-secondary">
          What was promised, what was measured, and how much room is left before the
          promise is broken.
        </p>
      </header>

      <Card title="Objectives">
        <div className="mb-3 flex flex-wrap gap-2">
          <select
            aria-label="SLO state"
            className={SELECT_CLASS}
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          >
            <option value="">Any state</option>
            {STATES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </div>

        {page.state === "loading" ? (
          <DataState kind="loading" />
        ) : page.state === "error" ? (
          page.notFound ? (
            <DataState kind="permission-denied" />
          ) : (
            <DataState kind="error" description={page.message} onRetry={retry} />
          )
        ) : page.data.items.length === 0 ? (
          <DataState
            kind="empty"
            title="No objectives in scope"
            description="No service level objective is configured for anything you can see."
          />
        ) : (
          <>
            {/* insufficient_data and not_configured stay OUT of the healthy
                wedge: nothing was measured, and a green slice for an
                unmeasured objective is the most misleading thing here. */}
            <div className="mb-4 border-b border-border pb-4">
              <Donut
                size={116}
                thickness={13}
                label="Objectives on this page by verdict"
                centerLabel={`${page.data.items.length}`}
                slices={[
                  {
                    name: "Meeting",
                    value: page.data.items.filter((slo) => slo.evaluation?.status === "healthy")
                      .length,
                    tone: "success",
                  },
                  {
                    name: "Burning fast",
                    value: page.data.items.filter((slo) => slo.evaluation?.status === "warning")
                      .length,
                    tone: "warning",
                  },
                  {
                    name: "Breached",
                    value: page.data.items.filter((slo) =>
                      ["critical", "exhausted", "query_failed"].includes(
                        slo.evaluation?.status ?? "",
                      ),
                    ).length,
                    tone: "critical",
                  },
                  {
                    name: "Stale",
                    value: page.data.items.filter((slo) => slo.evaluation?.status === "stale")
                      .length,
                    tone: "stale",
                  },
                  {
                    name: "Never measured",
                    value: page.data.items.filter(
                      (slo) =>
                        !slo.evaluation ||
                        ["insufficient_data", "not_configured"].includes(slo.evaluation.status),
                    ).length,
                    tone: "unknown",
                  },
                ]}
              />
            </div>
            <div className="w-full min-w-0 max-w-full overflow-x-auto [contain:paint]">
            <table className="w-full text-left text-sm">
              <thead className="text-xs text-ink-muted">
                <tr>
                  <th className="pb-2 pr-3 font-medium">Objective</th>
                  <th className="pb-2 pr-3 font-medium">Target</th>
                  <th className="pb-2 pr-3 font-medium">State</th>
                  <th className="pb-2 pr-3 font-medium">Compliance</th>
                  <th className="pb-2 pr-3 font-medium">Budget left</th>
                  <th className="pb-2 pr-3 font-medium">Burn</th>
                  <th className="pb-2 font-medium">Evaluated</th>
                </tr>
              </thead>
              <tbody>
                {page.data.items.map((slo) => (
                  <SloRow key={slo.id} slo={slo} />
                ))}
              </tbody>
            </table>
            </div>

            {/* Anything that is not a measurement is explained in words, so
                nobody reads a dash as a zero. */}
            {page.data.items.some(
              (slo) =>
                slo.evaluation === null ||
                ["insufficient_data", "stale", "query_failed", "not_configured"].includes(
                  slo.evaluation.status,
                ),
            ) ? (
              <div className="mt-4 space-y-1.5" data-testid="slo-caveats">
                {page.data.items
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
                    <p key={slo.id} className="text-xs text-ink-secondary">
                      <span className="font-medium text-ink">{slo.display_name}</span>{" "}
                      — {SLO_EXPLANATIONS[slo.evaluation!.status]}
                    </p>
                  ))}
                {page.data.items.some((slo) => slo.evaluation === null) ? (
                  <p className="text-xs text-ink-secondary">
                    Objectives marked <span className="italic">not evaluated</span> have
                    never been measured. That is not the same as meeting the target.
                  </p>
                ) : null}
              </div>
            ) : null}
          </>
        )}
      </Card>
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
