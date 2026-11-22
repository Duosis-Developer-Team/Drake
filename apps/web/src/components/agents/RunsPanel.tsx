"use client";

/**
 * An agent's latest runs: one row each, newest first. The bar is the run's
 * duration against the slowest one shown, so a slow run stands out without
 * reading a number.
 */

import { ChevronRight, Sparkles, Wrench } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Avatar, displayName } from "@/components/agents/People";
import { DataState } from "@/components/state/DataState";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { SegmentedControl } from "@/components/ui/controls";
import { ApiError } from "@/lib/api";
import {
  compact,
  fetchRuns,
  STEP_COLOR,
  type AgentRuns,
  type Window,
} from "@/lib/agents";
import { useFormat, useLocale, useT } from "@/lib/i18n";

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; data: AgentRuns };

export function RunsPanel({
  agentId,
  span,
}: {
  agentId: string;
  span: Window;
}) {
  const t = useT("agents");
  const fmt = useFormat();
  const { locale } = useLocale();
  const [status, setStatus] = useState<"any" | "error">("any");
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    let active = true;
    setState({ kind: "loading" });
    fetchRuns(agentId, span, status)
      .then((data) => active && setState({ kind: "ready", data }))
      .catch((error: unknown) => {
        if (active)
          setState({
            kind: "error",
            message: error instanceof ApiError ? error.message : "",
          });
      });
    return () => {
      active = false;
    };
  }, [agentId, span, status]);

  const data = state.kind === "ready" ? state.data : null;
  const runs = data?.state === "ok" ? data.runs : [];
  const slowest = Math.max(1, ...runs.map((r) => r.duration_ms));

  return (
    <Panel data-testid="agent-runs">
      <PanelHeader
        title={t("runs.title")}
        description={t("runs.hint")}
        actions={
          <SegmentedControl
            label={t("runs.title")}
            value={status}
            onChange={setStatus}
            options={[
              { value: "any", label: t("runs.all") },
              { value: "error", label: t("runs.failed") },
            ]}
          />
        }
      />
      {state.kind === "loading" ? <DataState kind="loading" /> : null}
      {state.kind === "error" ? (
        <DataState kind="error" description={state.message || undefined} />
      ) : null}
      {data && data.state !== "ok" ? (
        <p className="py-6 text-center text-caption text-ink-muted">
          {data.state === "not_configured"
            ? t("runs.notConfigured")
            : t("runs.unavailable")}
        </p>
      ) : null}
      {data?.state === "ok" && runs.length === 0 ? (
        <p className="py-6 text-center text-caption text-ink-muted">
          {status === "error" ? t("runs.emptyFailed") : t("runs.empty")}
        </p>
      ) : null}
      {runs.length > 0 ? (
        <ol className="-mx-2 divide-y divide-border">
          {runs.map((run) => {
            const failed = run.errors > 0;
            return (
              <li key={run.trace_id}>
                <Link
                  href={`/agents/${agentId}/runs/${run.trace_id}`}
                  aria-label={`${t("runs.open")} · ${fmt.utc(run.started_at)}`}
                  data-testid={`run-${run.trace_id}`}
                  className="group grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1.5 rounded-control px-2 py-2.5 hover:bg-surface-hover md:grid-cols-[9rem_minmax(8rem,1.2fr)_minmax(0,1.4fr)_5rem_1rem]"
                >
                  <span className="flex min-w-0 items-center gap-2">
                    <span
                      aria-hidden
                      className={`h-2 w-2 shrink-0 rounded-full ${failed ? "bg-critical" : "bg-healthy"}`}
                    />
                    <time
                      dateTime={run.started_at}
                      title={fmt.utc(run.started_at)}
                      className="truncate text-caption text-ink"
                    >
                      {fmt.relative(run.started_at)}
                    </time>
                  </span>
                  <span className="flex items-center gap-2 md:order-none">
                    <span
                      className="hidden h-1.5 flex-1 overflow-hidden rounded-full bg-surface-3 md:block"
                      aria-hidden
                    >
                      <span
                        className="block h-full rounded-full"
                        style={{
                          width: `${Math.max(3, (run.duration_ms / slowest) * 100)}%`,
                          background: failed
                            ? "var(--status-critical)"
                            : STEP_COLOR.agent,
                        }}
                      />
                    </span>
                    <span className="w-16 text-right text-caption font-medium tabular-nums text-ink">
                      {fmt.latency(run.duration_ms)}
                    </span>
                  </span>
                  <span className="col-span-2 flex min-w-0 items-center gap-x-3 text-micro text-ink-secondary md:col-span-1">
                    <span
                      className="inline-flex items-center gap-1"
                      title={t("runs.models", { count: run.model_calls })}
                    >
                      <Sparkles
                        aria-hidden
                        className="h-3.5 w-3.5"
                        style={{ color: STEP_COLOR.model }}
                      />
                      <span className="tabular-nums">{run.model_calls}</span>
                    </span>
                    <span
                      className="inline-flex items-center gap-1"
                      title={t("runs.tools", { count: run.tool_calls })}
                    >
                      <Wrench
                        aria-hidden
                        className="h-3.5 w-3.5"
                        style={{ color: STEP_COLOR.tool }}
                      />
                      <span className="tabular-nums">{run.tool_calls}</span>
                    </span>
                    {failed ? (
                      <span className="font-medium text-critical">
                        {t("runs.failedSteps", { count: run.errors })}
                      </span>
                    ) : null}
                    {run.user ? (
                      <span
                        className="inline-flex shrink-0 items-center"
                        title={`${t("runs.user")}: ${displayName(run.user, t("mcp.unnamedPerson"))}`}
                        data-testid={`run-user-${run.trace_id}`}
                      >
                        <Avatar person={run.user} size="xs" />
                      </span>
                    ) : null}
                    <span className="min-w-0 flex-1 truncate font-mono text-ink-muted">
                      {[...run.models, ...run.tools].slice(0, 3).join(" · ")}
                    </span>
                  </span>
                  <span className="hidden text-right text-caption tabular-nums text-ink-secondary md:block">
                    {compact(run.input_tokens + run.output_tokens, locale)}
                  </span>
                  <ChevronRight
                    aria-hidden
                    className="hidden h-4 w-4 text-ink-muted transition-transform group-hover:translate-x-0.5 md:block"
                  />
                </Link>
              </li>
            );
          })}
        </ol>
      ) : null}
    </Panel>
  );
}
