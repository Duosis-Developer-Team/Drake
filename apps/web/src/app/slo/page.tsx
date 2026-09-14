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
import { PageFrame, PageHeader } from "@/components/shell/AppShell";

import { SloBadge } from "@/components/alerting/primitives";
import { useApi } from "@/components/catalog/primitives";
import { Donut } from "@/components/charts/visuals";
import { DataState } from "@/components/state/DataState";
import { StatusBadge } from "@/components/state/StatusBadge";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { FilterBar, Select } from "@/components/ui/controls";
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

/**
 * One objective, as a card: name and target lead, then compliance and
 * budget as the two numbers this whole screen exists to keep apart — each
 * its own stat rather than two more grid columns competing with the badge.
 */
function SloRow({ slo }: { slo: Slo }) {
  const evaluation = slo.evaluation;
  const activeBurn = evaluation?.burn_rates.find((rate) => rate.active) ?? null;
  return (
    <li
      data-testid={`slo-row-${slo.slo_key}`}
      className="flex flex-wrap items-start gap-4 px-4 py-4 transition-colors hover:bg-surface-hover"
    >
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <Link href={`/slo/${slo.id}`} className="text-body font-semibold text-ink hover:underline">
            {slo.display_name}
          </Link>
          {evaluation ? (
            <SloBadge status={evaluation.status} />
          ) : (
            /* Never "healthy" for something nobody has measured. */
            <span className="text-caption italic text-ink-muted">not evaluated</span>
          )}
          {activeBurn ? (
            <StatusBadge
              status={activeBurn.severity === "critical" ? "critical" : "warning"}
              label={`${activeBurn.factor}× ${activeBurn.name}`}
              size="compact"
            />
          ) : null}
        </div>
        <span className="mt-1 block font-mono text-micro text-ink-muted">
          {[slo.project_key, slo.environment_key, slo.service_key].filter(Boolean).join("/")} ·{" "}
          {slo.indicator}
        </span>
        <span className="mt-1 block text-caption text-ink-secondary">
          Target {formatRatio(slo.objective_ratio)} over {formatWindow(slo.window_seconds)}
          {!activeBurn ? <span className="text-ink-muted"> · not burning</span> : null}
        </span>
      </div>
      <div className="flex shrink-0 items-start gap-6 text-right">
        <div>
          <span data-tabular className="block text-title font-semibold text-ink">
            {formatRatio(evaluation?.compliance_ratio ?? null)}
          </span>
          <span className="text-micro text-ink-muted">compliance</span>
        </div>
        <div>
          <span
            data-tabular
            className={`block text-title font-semibold ${
              (evaluation?.error_budget_remaining ?? 0) < 0 ? "text-critical" : "text-ink"
            }`}
          >
            {formatBudget(evaluation?.error_budget_remaining ?? null)}
          </span>
          <span className="text-micro text-ink-muted">budget left</span>
        </div>
        <span className="text-micro text-ink-muted">
          {evaluation ? formatAge(evaluation.evaluated_for) : "—"}
        </span>
      </div>
    </li>
  );
}

function SloInner() {
  const [status, setStatus] = useState<string>("");
  const [page, retry] = useApi<Page<Slo>>(sloListPath({ status }));

  return (
    <PageFrame>
      <PageHeader
        title="Service objectives"
        description="What was promised, what was measured, and how much room is left before the promise is broken."
      />
      <div className="space-y-5">
        <Panel
          data-testid="slo-filters"
          className="motion-safe:animate-[fade-in_360ms_var(--ease-entrance)_backwards]"
        >
          <FilterBar>
            <Select
              label="State"
              hideLabel={false}
              value={status}
              placeholder="Any state"
              options={STATES.map((value) => ({ value, label: value }))}
              onChange={setStatus}
            />
          </FilterBar>
        </Panel>

        {page.state === "loading" ? (
          <Panel>
            <DataState kind="loading" />
          </Panel>
        ) : page.state === "error" ? (
          <Panel>
            {page.notFound ? (
              <DataState kind="permission-denied" />
            ) : (
              <DataState kind="error" description={page.message} onRetry={retry} />
            )}
          </Panel>
        ) : page.data.items.length === 0 ? (
          <Panel>
            <DataState
              kind="empty"
              title="No objectives in scope"
              description="No service level objective is configured for anything you can see."
            />
          </Panel>
        ) : (
          <>
            {/* insufficient_data and not_configured stay OUT of the healthy
                wedge: nothing was measured, and a green slice for an
                unmeasured objective is the most misleading thing here. */}
            <Panel
              data-testid="slo-overview"
              className="motion-safe:animate-[fade-in_400ms_var(--ease-entrance)_backwards] [animation-delay:60ms]"
            >
              <PanelHeader
                title="Objectives on this page"
                description="By verdict — a page, not the whole authorized set."
              />
              <Donut
                size={132}
                thickness={14}
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
            </Panel>

            <Panel
              flush
              className="motion-safe:animate-[fade-in_440ms_var(--ease-entrance)_backwards] [animation-delay:100ms]"
            >
              <ul className="divide-y divide-border">
                {page.data.items.map((slo) => (
                  <SloRow key={slo.id} slo={slo} />
                ))}
              </ul>

              {/* Anything that is not a measurement is explained in words, so
                  nobody reads a dash as a zero. */}
              {page.data.items.some(
                (slo) =>
                  slo.evaluation === null ||
                  ["insufficient_data", "stale", "query_failed", "not_configured"].includes(
                    slo.evaluation.status,
                  ),
              ) ? (
                <div
                  className="space-y-1.5 border-t border-border px-4 py-3"
                  data-testid="slo-caveats"
                >
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
                      <p key={slo.id} className="text-caption text-ink-secondary">
                        <span className="font-medium text-ink">{slo.display_name}</span>{" "}
                        — {SLO_EXPLANATIONS[slo.evaluation!.status]}
                      </p>
                    ))}
                  {page.data.items.some((slo) => slo.evaluation === null) ? (
                    <p className="text-caption text-ink-secondary">
                      Objectives marked <span className="italic">not evaluated</span> have
                      never been measured. That is not the same as meeting the target.
                    </p>
                  ) : null}
                </div>
              ) : null}
            </Panel>
          </>
        )}
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
