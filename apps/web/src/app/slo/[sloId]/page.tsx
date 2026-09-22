"use client";

/**
 * SLO detail.
 *
 * Four things this screen insists on saying out loud:
 *
 * **How the number was measured.** A compliance figure whose method is
 * invisible invites a stronger reading than it deserves.
 *
 * **Which objective it was judged against.** Historical evaluations keep
 * the target that was in force when they ran, so tightening a target today
 * does not retroactively rewrite last month.
 *
 * **Why a burn level did or did not fire.** Both windows are shown, because
 * a level is active only when both exceed the threshold.
 *
 * **That correlation is not causation.** Nearby deployments and incidents
 * are shown because an operator wants them; Drake does not claim any of
 * them caused anything.
 */

import Link from "next/link";
import { useParams } from "next/navigation";
import { PageFrame } from "@/components/shell/AppShell";

import { BurnTable, SloBadge } from "@/components/alerting/primitives";
import { LoadGate, MetaRow, useApi } from "@/components/catalog/primitives";
import { Gauge, Sparkline } from "@/components/charts/visuals";
import { DataState } from "@/components/state/DataState";
import { Card } from "@/components/ui/Card";
import { useFormat, useT } from "@/lib/i18n";
import {
  SLO_EXPLANATIONS,
  formatBudget,
  formatRatio,
  type SloDetail,
  type SloEvaluation,
} from "@/lib/alerting";

export default function SloDetailPage() {
  const t = useT("alerting");
  const fmt = useFormat();
  const { sloId } = useParams<{ sloId: string }>();
  const [slo, retry] = useApi<SloDetail>(`/v1/slo/${sloId}`);
  const [history] = useApi<{ evaluations: SloEvaluation[] }>(
    `/v1/slo/${sloId}/evaluations`,
  );

  return (
    <PageFrame>
      <div className="space-y-5">
      <LoadGate value={slo} retry={retry}>
        {(data) => (
          <>
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div>
                <h1 className="text-xl font-semibold text-ink">{data.display_name}</h1>
                <p className="mt-1 font-mono text-xs text-ink-muted">
                  {[data.project_key, data.environment_key, data.service_key]
                    .filter(Boolean)
                    .join("/")}{" "}
                  · {t.dyn("indicator", data.indicator, data.indicator)}
                </p>
              </div>
              {data.evaluation ? (
                <SloBadge status={data.evaluation.status} />
              ) : (
                <span className="text-xs italic text-ink-muted">{t("sloDetail.notEvaluated")}</span>
              )}
            </div>

            <div className="grid gap-5 md:grid-cols-2">
              <Card title={t("sloDetail.promise.title")}>
                <MetaRow label={t("sloDetail.promise.objective")}>
                  {formatRatio(data.objective_ratio)}
                </MetaRow>
                <MetaRow label={t("sloDetail.promise.window")}>
                  {fmt.duration(data.window_seconds, { compact: true })}
                </MetaRow>
                <MetaRow label={t("sloDetail.promise.indicator")}>
                  {t.dyn("indicator", data.indicator, data.indicator)}
                </MetaRow>
                {data.threshold_profile_key ? (
                  <MetaRow label={t("sloDetail.promise.latencyProfile")}>
                    {data.threshold_profile_key}
                  </MetaRow>
                ) : null}
                <MetaRow label={t("sloDetail.promise.burnProfile")}>{data.burn_profile_key}</MetaRow>
                {/* Server-controlled, from a reviewed contract. There is no
                    field on this screen that changes any of it. */}
                <p className="mt-2 text-xs text-ink-secondary" data-testid="measurement">
                  {data.measurement}
                </p>
              </Card>

              <Card title={t("sloDetail.measured.title")}>
                {data.evaluation === null ? (
                  <DataState
                    kind="not-configured"
                    title={t("sloDetail.measured.neverTitle")}
                    description={t("sloDetail.measured.neverDescription")}
                  />
                ) : (
                  <div className="space-y-1" data-testid="slo-evaluation">
                    {/* An error budget is the textbook gauge: bounded by
                        construction, and the whole question is how much of it
                        is left. The bands are the objective's own, not
                        invented here. */}
                    <div className="flex flex-wrap items-center justify-around gap-3 pb-2">
                      <Gauge
                        label={t("sloDetail.measured.compliance")}
                        unit="ratio"
                        value={data.evaluation.compliance_ratio}
                        thresholds={{
                          warn: data.evaluation.objective_ratio,
                          critical: data.evaluation.objective_ratio * 0.99,
                          direction: "below",
                        }}
                        caption={t("sloDetail.measured.objectiveCaption", {
                          ratio: formatRatio(data.evaluation.objective_ratio),
                        })}
                        missingReason={t("sloDetail.measured.notMeasured")}
                      />
                      <Gauge
                        label={t("sloDetail.measured.budgetRemaining")}
                        unit="ratio"
                        value={
                          data.evaluation.error_budget_total
                            ? Math.max(
                                0,
                                (data.evaluation.error_budget_remaining ?? 0) /
                                  data.evaluation.error_budget_total,
                              )
                            : null
                        }
                        thresholds={{ warn: 0.25, critical: 0.05, direction: "below" }}
                        caption={
                          (data.evaluation.error_budget_remaining ?? 0) < 0
                            ? t("sloDetail.measured.overspent")
                            : t("sloDetail.measured.consumedCaption", {
                                value: formatBudget(data.evaluation.error_budget_consumed),
                              })
                        }
                        missingReason={t("sloDetail.measured.noBudget")}
                      />
                    </div>
                    <MetaRow label={t("sloDetail.measured.budgetConsumed")}>
                      {formatBudget(data.evaluation.error_budget_consumed)}
                    </MetaRow>
                    <MetaRow label={t("sloDetail.measured.budgetRemaining")}>
                      <span
                        className={
                          (data.evaluation.error_budget_remaining ?? 0) < 0
                            ? "text-critical"
                            : undefined
                        }
                      >
                        {formatBudget(data.evaluation.error_budget_remaining)}
                      </span>
                    </MetaRow>
                    <MetaRow label={t("sloDetail.measured.window")}>
                      {fmt.relative(data.evaluation.window_start)} →{" "}
                      {fmt.relative(data.evaluation.window_end)}
                    </MetaRow>
                    <MetaRow label={t("sloDetail.measured.dataQuality")}>
                      {data.evaluation.data_quality}
                    </MetaRow>
                    <MetaRow label={t("sloDetail.measured.samples")}>
                      {fmt.number(data.evaluation.sample_count)}
                    </MetaRow>
                    {/* The objective this measurement was judged against —
                        not necessarily the one configured today. */}
                    <MetaRow label={t("sloDetail.measured.judgedAgainst")}>
                      {formatRatio(data.evaluation.objective_ratio)} (v
                      {data.evaluation.definition_version})
                    </MetaRow>
                    <p className="mt-2 text-xs text-ink-secondary">
                      {t.dyn(
                        "sloExplanation",
                        data.evaluation.status,
                        SLO_EXPLANATIONS[data.evaluation.status],
                      )}
                    </p>
                  </div>
                )}
              </Card>
            </div>

            <Card title={t("sloDetail.burn.title")}>
              <BurnTable rates={data.evaluation?.burn_rates ?? []} />
              <p className="mt-3 text-xs text-ink-muted">{t("sloDetail.burn.bothWindows")}</p>
              <p className="mt-1 text-xs text-ink-muted">{t("sloDetail.burn.notPaging")}</p>
            </Card>

            <Card title={t("sloDetail.context.title")}>
              {data.context === null ? (
                <DataState kind="empty" title={t("sloDetail.context.empty")} />
              ) : (
                <div className="grid gap-4 md:grid-cols-3" data-testid="slo-context">
                  <div>
                    <p className="mb-1.5 text-xs font-medium text-ink">
                      {t("sloDetail.context.deployments")}
                    </p>
                    {data.context.deployments.length === 0 ? (
                      <p className="text-xs text-ink-muted">{t("sloDetail.context.noneRecorded")}</p>
                    ) : (
                      <ul className="space-y-1">
                        {data.context.deployments.map((deployment) => (
                          <li key={deployment.id} className="text-xs text-ink-secondary">
                            {t("sloDetail.context.deploymentRow", {
                              generation: deployment.generation,
                              state: deployment.rollout_state,
                              when: fmt.relative(deployment.observed_at),
                            })}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                  <div>
                    <p className="mb-1.5 text-xs font-medium text-ink">
                      {t("sloDetail.context.incidents")}
                    </p>
                    {data.context.incidents.length === 0 ? (
                      <p className="text-xs text-ink-muted">{t("sloDetail.context.noneOpen")}</p>
                    ) : (
                      <ul className="space-y-1">
                        {data.context.incidents.map((incident) => (
                          <li key={incident.id} className="text-xs">
                            <Link
                              href={`/incidents/${incident.id}`}
                              className="text-ink-secondary hover:underline"
                            >
                              {incident.title}
                            </Link>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                  <div>
                    <p className="mb-1.5 text-xs font-medium text-ink">
                      {t("sloDetail.context.alerts")}
                    </p>
                    {data.context.alerts.length === 0 ? (
                      <p className="text-xs text-ink-muted">{t("sloDetail.context.noneFiring")}</p>
                    ) : (
                      <ul className="space-y-1">
                        {data.context.alerts.map((alert) => (
                          <li key={alert.id} className="text-xs">
                            <Link
                              href={`/alerts/${alert.id}`}
                              className="text-ink-secondary hover:underline"
                            >
                              {alert.alert_name}
                            </Link>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                </div>
              )}
              {data.context ? (
                <p className="mt-3 text-xs text-ink-muted" data-testid="correlation-note">
                  {data.context.correlation_note}
                </p>
              ) : null}
            </Card>

            <Card title={t("sloDetail.history.title")}>
              {history.state === "loading" ? (
                <DataState kind="loading" />
              ) : history.state === "error" ? (
                <DataState kind="error" description={history.message} />
              ) : history.data.evaluations.length === 0 ? (
                <DataState kind="empty" title={t("sloDetail.history.empty")} />
              ) : (
                <div className="space-y-4">
                <div className="flex items-center gap-3">
                  <Sparkline
                    label={t("sloDetail.history.sparkline")}
                    tone="info"
                    width={160}
                    height={32}
                    points={[...history.data.evaluations]
                      .sort((a, b) => a.evaluated_for.localeCompare(b.evaluated_for))
                      .map((evaluation) => evaluation.compliance_ratio)}
                  />
                  <span className="text-caption text-ink-muted">
                    {t("sloDetail.history.caption")}
                  </span>
                </div>
                <div className="w-full min-w-0 max-w-full overflow-x-auto [contain:paint]">
                <table className="w-full text-left text-xs" data-testid="slo-history">
                  <thead className="text-ink-muted">
                    <tr>
                      <th className="pb-1.5 pr-3 font-medium">{t("sloDetail.history.evaluated")}</th>
                      <th className="pb-1.5 pr-3 font-medium">{t("sloDetail.history.state")}</th>
                      <th className="pb-1.5 pr-3 font-medium">{t("sloDetail.history.compliance")}</th>
                      <th className="pb-1.5 pr-3 font-medium">{t("sloDetail.history.budgetLeft")}</th>
                      <th className="pb-1.5 font-medium">{t("sloDetail.history.objective")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {history.data.evaluations.map((evaluation) => (
                      <tr key={evaluation.evaluated_for} className="border-t border-border">
                        <td className="py-1.5 pr-3 text-ink-secondary">
                          {fmt.relative(evaluation.evaluated_for)}
                        </td>
                        <td className="py-1.5 pr-3">
                          <SloBadge status={evaluation.status} />
                        </td>
                        <td className="py-1.5 pr-3 text-ink">
                          {formatRatio(evaluation.compliance_ratio)}
                        </td>
                        <td className="py-1.5 pr-3 text-ink">
                          {formatBudget(evaluation.error_budget_remaining)}
                        </td>
                        <td className="py-1.5 text-ink-muted">
                          {formatRatio(evaluation.objective_ratio)} (v
                          {evaluation.definition_version})
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                </div>
                </div>
              )}
            </Card>
          </>
        )}
      </LoadGate>
      </div>
    </PageFrame>
  );
}
