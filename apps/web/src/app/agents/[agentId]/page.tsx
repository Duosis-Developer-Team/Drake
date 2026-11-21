"use client";

/**
 * One agent: what it is, what it did hour by hour, which models it spent
 * tokens on, which tools it called and how they went.
 */

import {
  ArrowLeft,
  Bot,
  Coins,
  Globe,
  ShieldAlert,
  Sparkles,
  Wrench,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { ConfidenceBadge } from "@/components/agents/AgentCard";
import { AgentConnections } from "@/components/agents/AgentMap";
import { GuardrailsPanel } from "@/components/agents/GuardrailsPanel";
import { RunsPanel } from "@/components/agents/RunsPanel";
import { dayTicks, useHourLabel } from "@/components/agents/useHourLabel";
import {
  ActivityBars,
  AgentGlyph,
  TokenBar,
} from "@/components/agents/visuals";
import { KpiTile } from "@/components/features/configure/kit";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";
import { DataState } from "@/components/state/DataState";
import { PILL_SECONDARY } from "@/components/service-health/primitives";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { ProjectLogo } from "@/components/ui/ProjectLogo";
import { SegmentedControl } from "@/components/ui/controls";
import { ApiError } from "@/lib/api";
import { useCrumbLabel } from "@/lib/crumbs";
import {
  compact,
  errorRatio,
  fetchAgent,
  providerColor,
  providerName,
  usd,
  type AgentDetail,
  type Window,
} from "@/lib/agents";
import { useFormat, useLocale, useT } from "@/lib/i18n";

type State =
  | { kind: "loading" }
  | { kind: "missing" }
  | { kind: "error"; denied: boolean; message: string }
  | { kind: "ready"; agent: AgentDetail };

const RUN = "var(--series-1)";
const MODEL = "var(--series-3)";
const TOOL = "var(--series-6)";

export default function AgentPage() {
  const t = useT("agents");
  const fmt = useFormat();
  const { locale } = useLocale();
  const { agentId } = useParams<{ agentId: string }>();
  const [span, setSpan] = useState<Window>("7d");
  const [state, setState] = useState<State>({ kind: "loading" });
  const hourLabel = useHourLabel(span);
  useCrumbLabel(
    agentId,
    state.kind === "ready" ? state.agent.display_name : null,
  );

  useEffect(() => {
    let active = true;
    setState({ kind: "loading" });
    fetchAgent(agentId, span)
      .then((agent) => active && setState({ kind: "ready", agent }))
      .catch((error: unknown) => {
        if (!active) return;
        if (error instanceof ApiError && error.status === 404)
          return setState({ kind: "missing" });
        setState({
          kind: "error",
          denied: error instanceof ApiError && error.status === 403,
          message: error instanceof ApiError ? error.message : "",
        });
      });
    return () => {
      active = false;
    };
  }, [agentId, span]);

  const back = (
    <Link href="/agents" className={PILL_SECONDARY}>
      <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
      {t("detail.back")}
    </Link>
  );

  if (state.kind !== "ready") {
    return (
      <PageFrame>
        <div className="mb-4">{back}</div>
        <Panel>
          {state.kind === "loading" ? <DataState kind="loading" /> : null}
          {state.kind === "missing" ? (
            <DataState kind="empty" title={t("detail.notFound")} />
          ) : null}
          {state.kind === "error" ? (
            <DataState
              kind={state.denied ? "permission-denied" : "error"}
              description={state.message || undefined}
            />
          ) : null}
        </Panel>
      </PageFrame>
    );
  }

  const a = state.agent;
  const ratio = errorRatio(a);
  const avgRun = a.totals.invocations
    ? a.totals.duration_ms / a.totals.invocations / 1000
    : null;
  const tokens = a.totals.input_tokens + a.totals.output_tokens;
  const maxTokens = Math.max(
    1,
    ...a.models.map((m) => (m.input_tokens ?? 0) + (m.output_tokens ?? 0)),
  );
  const maxTool = Math.max(1, ...a.tools.map((x) => x.calls));
  const where = a.project
    ? `${a.project.key}${a.environment ? ` / ${a.environment.key}` : ""}`
    : t("detail.outside");

  return (
    <PageFrame>
      <div className="mb-4">{back}</div>
      <PageHeader
        leading={
          <AgentGlyph
            name={a.display_name}
            providers={a.providers}
            size={56}
            observed={a.confidence === "observed"}
          />
        }
        title={a.display_name}
        status={
          <span className="flex flex-wrap items-center gap-1.5">
            <ConfidenceBadge confidence={a.confidence} />
            {a.ingest === "external" ? (
              <span className="inline-flex items-center gap-1 rounded-full bg-surface-3 px-2 py-0.5 text-micro font-medium text-ink-secondary">
                <Globe aria-hidden className="h-3 w-3" />
                {t("card.external")}
              </span>
            ) : null}
            {a.new ? (
              <span className="rounded-full bg-healthy-soft px-2 py-0.5 text-micro font-semibold text-healthy">
                {t("card.new")}
              </span>
            ) : null}
          </span>
        }
        meta={
          <>
            <span className="inline-flex items-center gap-1.5">
              {a.project ? (
                <ProjectLogo
                  projectId={a.project.id}
                  name={a.project.key}
                  size="sm"
                  variant="mark"
                />
              ) : null}
              <span className="font-mono">{where}</span>
            </span>
            {a.service_name ? (
              <span className="font-mono">{a.service_name}</span>
            ) : null}
            <time dateTime={a.last_seen_at} title={fmt.utc(a.last_seen_at)}>
              {t("card.lastSeen", { when: fmt.relative(a.last_seen_at) })}
            </time>
          </>
        }
        actions={
          <SegmentedControl
            label={t("window.label")}
            value={span}
            onChange={setSpan}
            options={[
              { value: "24h", label: t("window.24h") },
              { value: "7d", label: t("window.7d") },
            ]}
          />
        }
      />

      <div className="space-y-6">
        <div className="page-grid" data-cols="4">
          <KpiTile
            icon={Bot}
            label={t("detail.kpi.runs")}
            value={compact(a.totals.invocations, locale)}
            data-testid="agent-kpi-runs"
          >
            <p className="text-micro text-ink-muted">
              {t("detail.kpi.avgRun")}:{" "}
              {avgRun === null ? "—" : fmt.duration(avgRun, { compact: true })}
            </p>
          </KpiTile>
          <KpiTile
            icon={Sparkles}
            label={t("detail.kpi.modelCalls")}
            value={compact(a.totals.inference_calls, locale)}
          >
            <p className="text-micro text-ink-muted">
              {t("detail.kpi.modelsCaption", {
                count: a.models.length,
                providers:
                  a.providers.map((p) => providerName(p.provider)).join(", ") ||
                  "—",
              })}
            </p>
          </KpiTile>
          <KpiTile
            icon={Wrench}
            label={t("detail.kpi.toolCalls")}
            value={compact(a.totals.tool_calls, locale)}
          >
            <p className="text-micro text-ink-muted">
              {t("detail.kpi.toolsCaption", { count: a.tools.length })}
            </p>
          </KpiTile>
          <KpiTile
            icon={ratio !== null && ratio > 0.05 ? ShieldAlert : Coins}
            tone={ratio !== null && ratio > 0.05 ? "critical" : undefined}
            label={t("detail.kpi.cost")}
            value={usd(a.cost_usd, locale)}
            data-testid="agent-kpi-cost"
          >
            <p className="text-micro text-ink-muted">
              {t("kpi.costCaption", { tokens: compact(tokens, locale) })}
            </p>
            <p
              className={`text-micro ${ratio !== null && ratio > 0.05 ? "font-medium text-critical" : "text-ink-muted"}`}
            >
              {t("detail.kpi.failed")}:{" "}
              {ratio === null
                ? "—"
                : `${fmt.number(ratio * 100, { maximumFractionDigits: 1 })}%`}
            </p>
          </KpiTile>
        </div>

        <Panel>
          <PanelHeader
            title={t("detail.activity")}
            description={t("detail.activityHint")}
          />
          <ActivityBars
            label={t("detail.activity")}
            timeLabel={hourLabel}
            tickAt={dayTicks(span)}
            errorLabel={t("activity.errors")}
            height={220}
            points={a.series.map((p) => ({
              t: p.t,
              errors: p.errors,
              parts: [
                {
                  key: "runs",
                  label: t("detail.kpi.runs"),
                  value: p.invocations ?? 0,
                  color: RUN,
                },
                {
                  key: "model",
                  label: t("detail.kpi.modelCalls"),
                  value: p.inference_calls ?? 0,
                  color: MODEL,
                },
                {
                  key: "tool",
                  label: t("detail.kpi.toolCalls"),
                  value: p.tool_calls ?? 0,
                  color: TOOL,
                },
              ],
            }))}
          />
        </Panel>

        <GuardrailsPanel agentId={a.id} />

        <RunsPanel agentId={a.id} span={span} />

        <AgentConnections agentId={a.id} span={span} />

        <Panel>
          <PanelHeader
            title={t("detail.models")}
            description={t("detail.modelsHint")}
          />
          {a.models.length === 0 ? (
            <p className="py-6 text-center text-caption text-ink-muted">
              {t("detail.noModels")}
            </p>
          ) : (
            <>
              <ul className="divide-y divide-border" data-testid="agent-models">
                {a.models.map((m) => {
                  const input = m.input_tokens ?? 0;
                  const output = m.output_tokens ?? 0;
                  const color = providerColor(m.provider);
                  return (
                    <li
                      key={`${m.provider}-${m.model}`}
                      className="grid gap-2 py-3.5 first:pt-0 last:pb-0"
                    >
                      <div className="flex items-baseline justify-between gap-3">
                        <div className="flex min-w-0 items-center gap-2">
                          <span
                            className="h-2.5 w-2.5 shrink-0 rounded-full"
                            style={{ background: color }}
                          />
                          <span className="truncate font-mono text-caption font-medium text-ink">
                            {m.model ?? "—"}
                          </span>
                          <span className="shrink-0 text-micro text-ink-muted">
                            {providerName(m.provider)}
                          </span>
                        </div>
                        <span className="shrink-0 text-caption tabular-nums text-ink-secondary">
                          <span className="font-semibold text-ink">
                            {m.cost_usd !== null && m.cost_usd !== undefined
                              ? usd(m.cost_usd, locale)
                              : t("detail.noPrice")}
                          </span>{" "}
                          · {compact(input + output, locale)} tok ·{" "}
                          {compact(m.calls, locale)} {t("card.calls")}
                          {m.errors ? (
                            <span className="text-critical">
                              {" "}
                              · {m.errors} {t("card.failed")}
                            </span>
                          ) : null}
                        </span>
                      </div>
                      <TokenBar
                        input={input}
                        cached={m.cache_read_tokens ?? 0}
                        output={output}
                        reasoning={m.reasoning_tokens ?? 0}
                        max={maxTokens}
                        color={color}
                      />
                      <p className="flex flex-wrap gap-x-3 text-micro tabular-nums text-ink-muted">
                        <span>
                          {compact(input, locale)} {t("detail.tokenInput")}
                          {m.cache_read_tokens
                            ? ` (${compact(m.cache_read_tokens, locale)} ${t("detail.tokenCached")})`
                            : ""}
                        </span>
                        <span>
                          {compact(output, locale)} {t("detail.tokenOutput")}
                          {m.reasoning_tokens
                            ? ` (${compact(m.reasoning_tokens, locale)} ${t("detail.tokenReasoning")})`
                            : ""}
                        </span>
                      </p>
                    </li>
                  );
                })}
              </ul>
              <TokenLegend />
            </>
          )}
        </Panel>

        <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
          <Panel>
            <PanelHeader
              title={t("detail.tools")}
              description={t("detail.toolsHint")}
            />
            {a.tools.length === 0 ? (
              <p className="py-6 text-center text-caption text-ink-muted">
                {t("detail.noTools")}
              </p>
            ) : (
              <ul className="space-y-3" data-testid="agent-tools">
                {a.tools.map((x) => {
                  const failed = x.calls ? x.errors / x.calls : 0;
                  return (
                    <li
                      key={x.tool}
                      className="grid grid-cols-[minmax(0,12rem)_minmax(0,1fr)_auto] items-center gap-3"
                    >
                      <span
                        className="truncate font-mono text-caption text-ink"
                        title={x.tool}
                      >
                        {x.tool}
                      </span>
                      <span
                        className="relative h-2.5 overflow-hidden rounded-full bg-surface-3"
                        aria-hidden
                      >
                        <span
                          className="absolute inset-y-0 left-0 rounded-full"
                          style={{
                            width: `${(x.calls / maxTool) * 100}%`,
                            background: TOOL,
                          }}
                        />
                        {x.errors ? (
                          <span
                            className="absolute inset-y-0 rounded-r-full bg-critical"
                            style={{
                              left: `${((x.calls - x.errors) / maxTool) * 100}%`,
                              width: `${(x.errors / maxTool) * 100}%`,
                            }}
                          />
                        ) : null}
                      </span>
                      <span className="text-right text-micro tabular-nums text-ink-secondary">
                        <span className="font-semibold text-ink">
                          {compact(x.calls, locale)}
                        </span>
                        {x.errors ? (
                          <span className="text-critical">
                            {" "}
                            ·{" "}
                            {fmt.number(failed * 100, {
                              maximumFractionDigits: 1,
                            })}
                            %
                          </span>
                        ) : null}
                        {x.avg_ms !== null
                          ? ` · ${t("detail.avg", { value: fmt.latency(x.avg_ms) })}`
                          : ""}
                      </span>
                    </li>
                  );
                })}
              </ul>
            )}
          </Panel>
          <Panel>
            <PanelHeader title={t("detail.identity")} />
            <div className="mb-4 flex items-start gap-3 rounded-control bg-surface-2 px-3.5 py-3">
              <span className="shrink-0 whitespace-nowrap">
                <ConfidenceBadge confidence={a.confidence} />
              </span>
              <p className="text-micro text-ink-secondary">
                {t.dyn("confidence", `${a.confidence}Hint`, a.confidence)}
              </p>
            </div>
            <dl className="space-y-3" data-testid="agent-identity">
              {(
                [
                  [t("detail.externalId"), a.agent_external_id, true],
                  [t("detail.service"), a.service_name, true],
                  [
                    t("detail.workload"),
                    a.workload
                      ? `${a.namespace ?? ""}/${a.workload}`
                      : a.namespace,
                    true,
                  ],
                  [t("detail.cluster"), a.cluster_ref, true],
                  [
                    t("detail.project"),
                    a.project
                      ? `${a.project.name}${a.environment ? ` · ${a.environment.key}` : ""}`
                      : t("detail.outside"),
                    false,
                  ],
                  [
                    t("detail.ingest"),
                    a.ingest === "external"
                      ? t("detail.ingestExternal")
                      : t("detail.ingestCluster"),
                    false,
                  ],
                  [t("detail.firstSeen"), fmt.utc(a.first_seen_at), false],
                  [t("detail.lastSeen"), fmt.utc(a.last_seen_at), false],
                ] as const
              )
                .filter(([, v]) => v)
                .map(([k, v, mono]) => (
                  <div key={k}>
                    <dt className="text-micro text-ink-muted">{k}</dt>
                    <dd
                      className={`break-all text-caption font-medium text-ink ${mono ? "font-mono" : ""}`}
                    >
                      {v}
                    </dd>
                  </div>
                ))}
            </dl>
          </Panel>
        </div>
      </div>
    </PageFrame>
  );
}

function TokenLegend() {
  const t = useT("agents");
  const items = [
    { label: t("detail.tokenInput"), color: "var(--text-muted)", opacity: 1 },
    {
      label: t("detail.tokenCached"),
      color: "var(--text-muted)",
      opacity: 0.45,
    },
    { label: t("detail.tokenOutput"), color: "var(--series-5)", opacity: 1 },
    {
      label: t("detail.tokenReasoning"),
      color: "var(--series-5)",
      opacity: 0.45,
    },
  ];
  return (
    <p className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-border pt-3 text-micro text-ink-muted">
      {items.map((i) => (
        <span key={i.label} className="inline-flex items-center gap-1.5">
          <span
            className="h-2 w-3.5 rounded-sm"
            style={{ background: i.color, opacity: i.opacity }}
          />
          {i.label}
        </span>
      ))}
    </p>
  );
}
