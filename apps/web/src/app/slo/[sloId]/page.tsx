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
import { DataState } from "@/components/state/DataState";
import { Card } from "@/components/ui/Card";
import {
  SLO_EXPLANATIONS,
  formatAge,
  formatBudget,
  formatRatio,
  formatWindow,
  type SloDetail,
  type SloEvaluation,
} from "@/lib/alerting";

export default function SloDetailPage() {
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
                  · {data.indicator}
                </p>
              </div>
              {data.evaluation ? (
                <SloBadge status={data.evaluation.status} />
              ) : (
                <span className="text-xs italic text-ink-muted">not evaluated</span>
              )}
            </div>

            <div className="grid gap-5 md:grid-cols-2">
              <Card title="The promise">
                <MetaRow label="Objective">{formatRatio(data.objective_ratio)}</MetaRow>
                <MetaRow label="Rolling window">
                  {formatWindow(data.window_seconds)}
                </MetaRow>
                <MetaRow label="Indicator">{data.indicator}</MetaRow>
                {data.threshold_profile_key ? (
                  <MetaRow label="Latency profile">{data.threshold_profile_key}</MetaRow>
                ) : null}
                <MetaRow label="Burn profile">{data.burn_profile_key}</MetaRow>
                {/* Server-controlled, from a reviewed contract. There is no
                    field on this screen that changes any of it. */}
                <p className="mt-2 text-xs text-ink-secondary" data-testid="measurement">
                  {data.measurement}
                </p>
              </Card>

              <Card title="What was measured">
                {data.evaluation === null ? (
                  <DataState
                    kind="not-configured"
                    title="Never evaluated"
                    description="No measurement has been recorded for this objective. That is not the same as meeting it."
                  />
                ) : (
                  <div className="space-y-1" data-testid="slo-evaluation">
                    <MetaRow label="Compliance">
                      {formatRatio(data.evaluation.compliance_ratio)}
                    </MetaRow>
                    <MetaRow label="Budget consumed">
                      {formatBudget(data.evaluation.error_budget_consumed)}
                    </MetaRow>
                    <MetaRow label="Budget remaining">
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
                    <MetaRow label="Window">
                      {formatAge(data.evaluation.window_start)} →{" "}
                      {formatAge(data.evaluation.window_end)}
                    </MetaRow>
                    <MetaRow label="Data quality">{data.evaluation.data_quality}</MetaRow>
                    <MetaRow label="Samples">
                      {String(data.evaluation.sample_count)}
                    </MetaRow>
                    {/* The objective this measurement was judged against —
                        not necessarily the one configured today. */}
                    <MetaRow label="Judged against">
                      {formatRatio(data.evaluation.objective_ratio)} (v
                      {data.evaluation.definition_version})
                    </MetaRow>
                    <p className="mt-2 text-xs text-ink-secondary">
                      {SLO_EXPLANATIONS[data.evaluation.status]}
                    </p>
                  </div>
                )}
              </Card>
            </div>

            <Card title="Multi-window burn rate">
              <BurnTable rates={data.evaluation?.burn_rates ?? []} />
              <p className="mt-3 text-xs text-ink-muted">
                A level is active only when its long and its short window both exceed the
                threshold. One window alone is a spike or a memory.
              </p>
              <p className="mt-1 text-xs text-ink-muted">
                Drake computes these for the dashboard. The authoritative paging signal is
                PrometheusRule → Alertmanager; Drake does not page.
              </p>
            </Card>

            <Card title="Around this objective">
              {data.context === null ? (
                <DataState kind="empty" title="No nearby activity" />
              ) : (
                <div className="grid gap-4 md:grid-cols-3" data-testid="slo-context">
                  <div>
                    <p className="mb-1.5 text-xs font-medium text-ink">Deployments</p>
                    {data.context.deployments.length === 0 ? (
                      <p className="text-xs text-ink-muted">none recorded</p>
                    ) : (
                      <ul className="space-y-1">
                        {data.context.deployments.map((deployment) => (
                          <li key={deployment.id} className="text-xs text-ink-secondary">
                            gen {deployment.generation} · {deployment.rollout_state} ·{" "}
                            {formatAge(deployment.observed_at)}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                  <div>
                    <p className="mb-1.5 text-xs font-medium text-ink">Incidents</p>
                    {data.context.incidents.length === 0 ? (
                      <p className="text-xs text-ink-muted">none open</p>
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
                    <p className="mb-1.5 text-xs font-medium text-ink">Firing alerts</p>
                    {data.context.alerts.length === 0 ? (
                      <p className="text-xs text-ink-muted">none firing</p>
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

            <Card title="Evaluation history">
              {history.state === "loading" ? (
                <DataState kind="loading" />
              ) : history.state === "error" ? (
                <DataState kind="error" description={history.message} />
              ) : history.data.evaluations.length === 0 ? (
                <DataState kind="empty" title="No evaluations yet" />
              ) : (
                <div className="w-full min-w-0 max-w-full overflow-x-auto [contain:paint]">
                <table className="w-full text-left text-xs" data-testid="slo-history">
                  <thead className="text-ink-muted">
                    <tr>
                      <th className="pb-1.5 pr-3 font-medium">Evaluated</th>
                      <th className="pb-1.5 pr-3 font-medium">State</th>
                      <th className="pb-1.5 pr-3 font-medium">Compliance</th>
                      <th className="pb-1.5 pr-3 font-medium">Budget left</th>
                      <th className="pb-1.5 font-medium">Objective</th>
                    </tr>
                  </thead>
                  <tbody>
                    {history.data.evaluations.map((evaluation) => (
                      <tr key={evaluation.evaluated_for} className="border-t border-border">
                        <td className="py-1.5 pr-3 text-ink-secondary">
                          {formatAge(evaluation.evaluated_for)}
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
              )}
            </Card>
          </>
        )}
      </LoadGate>
      </div>
    </PageFrame>
  );
}
