"use client";

/**
 * One run of one agent, step by step: how long it took, where the time
 * went, and every model and tool call on one timeline.
 */

import {
  ArrowLeft,
  Check,
  Clock,
  Copy,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Wrench,
  Coins,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { RunWaterfall, StepDetail } from "@/components/agents/RunWaterfall";
import { KpiTile } from "@/components/features/configure/kit";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";
import { DataState } from "@/components/state/DataState";
import { PILL_SECONDARY } from "@/components/service-health/primitives";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { ApiError } from "@/lib/api";
import {
  compact,
  fetchAgent,
  fetchRun,
  STEP_COLOR,
  timeShare,
  type AgentRunDetail,
  type RunSpan,
} from "@/lib/agents";
import { useCrumbLabel } from "@/lib/crumbs";
import { useFormat, useLocale, useT } from "@/lib/i18n";

type State =
  | { kind: "loading" }
  | { kind: "missing" }
  | { kind: "error"; denied: boolean; message: string }
  | { kind: "ready"; run: AgentRunDetail };

export default function RunPage() {
  const t = useT("agents");
  const fmt = useFormat();
  const { locale } = useLocale();
  const router = useRouter();
  const { agentId, traceId } = useParams<{
    agentId: string;
    traceId: string;
  }>();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [agentName, setAgentName] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  useCrumbLabel(agentId, agentName);
  useCrumbLabel(traceId, traceId ? `${traceId.slice(0, 8)}…` : null);

  useEffect(() => {
    let active = true;
    fetchAgent(agentId, "24h")
      .then((a) => active && setAgentName(a.display_name))
      .catch(() => undefined);
    fetchRun(agentId, traceId)
      .then((run) => {
        if (!active) return;
        setState({ kind: "ready", run });
        const first =
          run.spans?.find((s) => s.agent?.self && s.step === "agent") ??
          run.spans?.[0];
        if (first) setSelected(first.span_id);
      })
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
  }, [agentId, traceId]);

  const run = state.kind === "ready" ? state.run : null;
  const spans: RunSpan[] = useMemo(() => run?.spans ?? [], [run]);
  const total = run?.duration_ms ?? 0;
  const share = useMemo(() => timeShare(spans, total), [spans, total]);
  const chosen = spans.find((s) => s.span_id === selected) ?? null;

  const back = (
    <Link href={`/agents/${agentId}`} className={PILL_SECONDARY}>
      <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
      {t("run.back", { name: agentName ?? t("title") })}
    </Link>
  );

  if (state.kind !== "ready" || !run || run.state !== "ok") {
    return (
      <PageFrame>
        <div className="mb-4">{back}</div>
        <Panel>
          {state.kind === "loading" ? <DataState kind="loading" /> : null}
          {state.kind === "missing" ? (
            <DataState kind="empty" title={t("run.notFound")} />
          ) : null}
          {state.kind === "error" ? (
            <DataState
              kind={state.denied ? "permission-denied" : "error"}
              description={state.message || undefined}
            />
          ) : null}
          {run && run.state !== "ok" ? (
            <p className="py-8 text-center text-caption text-ink-muted">
              {run.state === "not_configured"
                ? t("runs.notConfigured")
                : t("runs.unavailable")}
            </p>
          ) : null}
        </Panel>
      </PageFrame>
    );
  }

  const mine = spans.filter((s) => s.agent?.self);
  const modelCalls = spans.filter((s) => s.step === "model").length;
  const toolCalls = spans.filter((s) => s.step === "tool").length;
  const failed = spans.filter((s) => s.status === "error").length;
  const input = spans
    .filter((s) => s.step === "model")
    .reduce((a, s) => a + s.input_tokens, 0);
  const output = spans
    .filter((s) => s.step === "model")
    .reduce((a, s) => a + s.output_tokens, 0);
  const parts = [
    {
      key: "model",
      label: t("run.time.model"),
      value: share.model,
      color: STEP_COLOR.model,
    },
    {
      key: "tool",
      label: t("run.time.tool"),
      value: share.tool,
      color: STEP_COLOR.tool,
    },
    {
      key: "other",
      label: t("run.time.other"),
      value: share.other,
      color: STEP_COLOR.agent,
    },
  ];

  return (
    <PageFrame>
      <div className="mb-4">{back}</div>
      <PageHeader
        title={`${t("run.title")} · ${fmt.relative(run.started_at)}`}
        status={
          <span
            className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-micro font-semibold ${
              failed
                ? "bg-critical-soft text-critical"
                : "bg-healthy-soft text-healthy"
            }`}
          >
            {failed ? (
              <ShieldAlert aria-hidden className="h-3 w-3" />
            ) : (
              <ShieldCheck aria-hidden className="h-3 w-3" />
            )}
            {failed
              ? t("runs.failedSteps", { count: failed })
              : t("run.detail.ok")}
          </span>
        }
        meta={
          <>
            <time dateTime={run.started_at}>{fmt.utc(run.started_at)}</time>
            <button
              type="button"
              title={t("run.copyTrace")}
              onClick={() => {
                void navigator.clipboard?.writeText(run.trace_id).then(() => {
                  setCopied(true);
                  window.setTimeout(() => setCopied(false), 1500);
                });
              }}
              className="inline-flex items-center gap-1 font-mono hover:text-ink"
            >
              {t("run.trace")} {run.trace_id}
              {copied ? (
                <Check aria-hidden className="h-3 w-3 text-healthy" />
              ) : (
                <Copy aria-hidden className="h-3 w-3" />
              )}
            </button>
            {mine.length && agentName ? (
              <span>
                {t("run.mine", { count: mine.length, name: agentName })}
              </span>
            ) : null}
          </>
        }
      />

      <div className="space-y-6">
        <div className="page-grid" data-cols="4">
          <KpiTile
            icon={Clock}
            label={t("run.kpi.duration")}
            value={fmt.latency(total)}
            data-testid="run-kpi-duration"
          />
          <KpiTile
            icon={Sparkles}
            label={t("run.kpi.modelCalls")}
            value={modelCalls}
          />
          <KpiTile
            icon={Wrench}
            label={t("run.kpi.toolCalls")}
            value={toolCalls}
          >
            {failed ? (
              <p className="text-micro font-medium text-critical">
                {t("runs.failedSteps", { count: failed })}
              </p>
            ) : null}
          </KpiTile>
          <KpiTile
            icon={Coins}
            label={t("run.kpi.tokens")}
            value={compact(input + output, locale)}
          >
            <p className="text-micro text-ink-muted">
              {t("kpi.tokensCaption", {
                input: compact(input, locale),
                output: compact(output, locale),
              })}
            </p>
          </KpiTile>
        </div>

        <Panel>
          <PanelHeader title={t("run.time.title")} />
          <div
            className="flex h-3 w-full overflow-hidden rounded-full bg-surface-3"
            data-testid="run-time-share"
          >
            {parts.map((p) =>
              p.value > 0 ? (
                <span
                  key={p.key}
                  className="h-full"
                  style={{
                    width: `${(p.value / Math.max(1, total)) * 100}%`,
                    background: p.color,
                  }}
                />
              ) : null,
            )}
          </div>
          <ul className="mt-3 flex flex-wrap gap-x-6 gap-y-2">
            {parts.map((p) => (
              <li
                key={p.key}
                className="flex items-baseline gap-2 text-caption"
              >
                <span
                  className="h-2.5 w-2.5 shrink-0 self-center rounded-sm"
                  style={{ background: p.color }}
                />
                <span className="font-semibold tabular-nums text-ink">
                  {fmt.latency(p.value)}
                </span>
                <span className="text-ink-muted">
                  {fmt.number(total ? (p.value / total) * 100 : 0, {
                    maximumFractionDigits: 0,
                  })}
                  % {p.label}
                </span>
              </li>
            ))}
          </ul>
        </Panel>

        <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_21rem]">
          <Panel>
            <PanelHeader
              title={t("run.steps")}
              description={t("run.stepsHint")}
            />
            <RunWaterfall
              spans={spans}
              total={total}
              selected={selected}
              onSelect={setSelected}
            />
            {run.hidden_spans || run.truncated ? (
              <div className="mt-3 space-y-1 text-micro text-ink-muted">
                {run.hidden_spans ? (
                  <p>{t("run.hidden", { count: run.hidden_spans })}</p>
                ) : null}
                {run.truncated ? <p>{t("run.truncated")}</p> : null}
              </div>
            ) : null}
          </Panel>
          <Panel className="xl:sticky xl:top-24">
            <PanelHeader title={t("run.detail.title")} />
            <StepDetail
              span={chosen}
              onOpenAgent={(id) => router.push(`/agents/${id}`)}
            />
            <p className="mt-5 border-t border-border pt-3 text-micro text-ink-muted">
              {t("run.noContent")}
            </p>
          </Panel>
        </div>
      </div>
    </PageFrame>
  );
}
