"use client";

/**
 * Agents: the inventory (Agent Visibility, phase 2).
 *
 * Which agents there are, how busy they are, what they call — every one
 * found on first sight in the traces Tracesis keeps. With none yet, the
 * screen is the way to connect one.
 */

import {
  Activity,
  Bot,
  Coins,
  LayoutGrid,
  Network,
  Plug,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { AgentCard, CONFIDENCE_ICON } from "@/components/agents/AgentCard";
import { ConnectPanel } from "@/components/agents/ConnectPanel";
import { dayTicks, useHourLabel } from "@/components/agents/useHourLabel";
import { ActivityBars, ProviderMix } from "@/components/agents/visuals";
import { AgentMap } from "@/components/agents/AgentMap";
import { GovernanceView } from "@/components/agents/Governance";
import { McpServers } from "@/components/agents/McpServers";
import { PriceNote } from "@/components/agents/PriceNote";
import { KpiTile, PillTabs } from "@/components/features/configure/kit";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";
import { DataState } from "@/components/state/DataState";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import {
  Button,
  SearchInput,
  SegmentedControl,
} from "@/components/ui/controls";
import { Drawer } from "@/components/ui/overlay";
import { ApiError } from "@/lib/api";
import {
  CONFIDENCES,
  compact,
  fetchAgents,
  fetchConnect,
  fetchMcpServers,
  fetchTopology,
  type AgentConnect,
  type AgentInventory,
  type Topology,
  usd,
  type Confidence,
  type Window,
} from "@/lib/agents";
import { useFormat, useLocale, useT } from "@/lib/i18n";

type State =
  | { kind: "loading" }
  | { kind: "error"; denied: boolean; message: string }
  | { kind: "ready"; data: AgentInventory };

export default function AgentsPage() {
  const t = useT("agents");
  const fmt = useFormat();
  const { locale } = useLocale();
  const [span, setSpan] = useState<Window>("24h");
  const [view, setView] = useState<"inventory" | "map" | "mcp" | "governance">(
    "inventory",
  );
  const [metric, setMetric] = useState<"calls" | "cost">("calls");
  const [state, setState] = useState<State>({ kind: "loading" });
  const [connect, setConnect] = useState<AgentConnect | null>(null);
  const [drawer, setDrawer] = useState(false);
  const [query, setQuery] = useState("");
  const [confidence, setConfidence] = useState<Confidence | "">("");
  const [outside, setOutside] = useState(false);
  const [firing, setFiring] = useState(false);
  const hourLabel = useHourLabel(span);

  useEffect(() => {
    let active = true;
    setState({ kind: "loading" });
    fetchAgents(span)
      .then((data) => active && setState({ kind: "ready", data }))
      .catch((error: unknown) => {
        if (!active) return;
        setState({
          kind: "error",
          denied: error instanceof ApiError && error.status === 403,
          message: error instanceof ApiError ? error.message : "",
        });
      });
    return () => {
      active = false;
    };
  }, [span]);

  useEffect(() => {
    fetchConnect()
      .then(setConnect)
      .catch(() => setConnect(null));
  }, []);

  const data = state.kind === "ready" ? state.data : null;
  const shown = useMemo(() => {
    if (!data) return [];
    const q = query.trim().toLowerCase();
    return data.items.filter(
      (a) =>
        (!confidence || a.confidence === confidence) &&
        (!outside || a.project === null) &&
        (!firing || (a.guardrails_firing ?? 0) > 0) &&
        (!q ||
          [
            a.display_name,
            a.service_name,
            a.workload,
            a.namespace,
            a.project?.key,
            ...a.models.map((m) => m.model),
          ]
            .filter(Boolean)
            .some((v) => String(v).toLowerCase().includes(q))),
    );
  }, [data, query, confidence, outside, firing]);

  const summary = data?.summary;
  const calls = summary
    ? summary.totals.invocations +
      summary.totals.inference_calls +
      summary.totals.tool_calls
    : 0;
  const failed = summary && calls ? summary.totals.errors / calls : null;

  return (
    <PageFrame>
      <PageHeader
        title={t("title")}
        description={t("description")}
        tabs={
          <PillTabs
            label={t("view.label")}
            value={view}
            onChange={setView}
            tabs={[
              {
                key: "inventory",
                label: t("view.inventory"),
                icon: LayoutGrid,
              },
              { key: "map", label: t("view.map"), icon: Network },
              { key: "mcp", label: t("view.mcp"), icon: Plug },
              {
                key: "governance",
                label: t("view.governance"),
                icon: ShieldCheck,
              },
            ]}
          />
        }
        actions={
          <>
            <SegmentedControl
              label={t("window.label")}
              value={span}
              onChange={setSpan}
              options={[
                { value: "24h", label: t("window.24h") },
                { value: "7d", label: t("window.7d") },
              ]}
            />
            <Button
              icon={Plug}
              onClick={() => setDrawer(true)}
              data-testid="agents-connect"
            >
              {t("connect.open")}
            </Button>
          </>
        }
      />
      <Drawer
        open={drawer}
        onClose={() => setDrawer(false)}
        title={t("connectPanel.title")}
        width="lg"
      >
        <ConnectPanel connect={connect} />
      </Drawer>

      {state.kind === "loading" ? (
        <Panel>
          <DataState kind="loading" />
        </Panel>
      ) : null}
      {state.kind === "error" ? (
        <Panel>
          <DataState
            kind={state.denied ? "permission-denied" : "error"}
            description={state.message || undefined}
          />
        </Panel>
      ) : null}

      {data && data.items.length === 0 && view === "mcp" ? (
        <McpServers span={span} />
      ) : null}
      {data && data.items.length === 0 && view === "governance" ? (
        <GovernanceView span={span} />
      ) : null}
      {data && data.items.length === 0 && view === "map" ? (
        <MapSection span={span} />
      ) : null}
      {data && data.items.length === 0 && view === "inventory" ? (
        <McpTeaser span={span} onOpen={() => setView("mcp")} />
      ) : null}
      {data && data.items.length === 0 && view === "inventory" ? (
        <Panel className="overflow-hidden" data-testid="agents-empty">
          <div className="grid gap-8 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
            <div className="flex flex-col justify-center gap-4">
              <EmptyIllustration />
              <h2 className="text-[1.25rem] font-semibold tracking-[-0.01em] text-ink">
                {t("empty.title")}
              </h2>
              <p className="max-w-md text-body text-ink-secondary">
                {t("empty.description")}
              </p>
            </div>
            <ConnectPanel connect={connect} />
          </div>
        </Panel>
      ) : null}

      {data && summary && data.items.length > 0 ? (
        <div className="space-y-6">
          <div className="page-grid" data-cols="4">
            <KpiTile
              icon={Bot}
              label={t("kpi.agents")}
              value={summary.agents}
              data-testid="agents-kpi-agents"
            >
              <p className="text-micro text-ink-muted">
                {t("kpi.agentsCaption", { new: summary.new })}
              </p>
            </KpiTile>
            <KpiTile
              icon={Activity}
              label={t("kpi.calls")}
              value={compact(calls, locale)}
            >
              <p className="text-micro text-ink-muted">
                {t("kpi.activeOf", {
                  active: summary.active,
                  agents: summary.agents,
                })}
              </p>
            </KpiTile>
            <KpiTile
              icon={Coins}
              label={t("kpi.cost")}
              value={usd(summary.cost_usd, locale)}
              data-testid="agents-kpi-cost"
            >
              <p className="text-micro text-ink-muted">
                {t("kpi.costCaption", {
                  tokens: compact(
                    summary.totals.input_tokens + summary.totals.output_tokens,
                    locale,
                  ),
                })}
              </p>
              {summary.unpriced_tokens ? (
                <p className="text-micro text-warning">
                  {t("kpi.unpriced", {
                    tokens: compact(summary.unpriced_tokens, locale),
                  })}
                </p>
              ) : null}
            </KpiTile>
            <KpiTile
              icon={ShieldAlert}
              tone={(failed ?? 0) > 0.05 ? "critical" : undefined}
              label={t("kpi.errors")}
              value={
                failed === null
                  ? "—"
                  : `${fmt.number(failed * 100, { maximumFractionDigits: 1 })}%`
              }
            >
              <p className="text-micro text-ink-muted">
                {t("kpi.errorsCaption")}
              </p>
            </KpiTile>
          </div>
          <PriceNote />

          {view === "governance" ? (
            <GovernanceView span={span} />
          ) : view === "mcp" ? (
            <McpServers span={span} />
          ) : view === "map" ? (
            <MapSection span={span} />
          ) : (
            <div className="space-y-6">
              <div className="page-split">
                <div className="page-main">
                  <Panel>
                    <PanelHeader
                      title={t("activity.title")}
                      description={
                        metric === "cost"
                          ? t("costDescription")
                          : t("activity.description")
                      }
                      actions={
                        <SegmentedControl
                          label={t("metric.label")}
                          value={metric}
                          onChange={setMetric}
                          options={[
                            {
                              value: "calls",
                              label: t("metric.calls"),
                            },
                            { value: "cost", label: t("metric.cost") },
                          ]}
                        />
                      }
                    />
                    <ActivityBars
                      label={t("activity.title")}
                      timeLabel={hourLabel}
                      tickAt={dayTicks(span)}
                      height={250}
                      errorLabel={t("activity.errors")}
                      valueFormat={
                        metric === "cost" ? (v) => usd(v, locale) : undefined
                      }
                      points={summary.series.map((p) => ({
                        t: p.t,
                        errors: p.errors,
                        parts: [
                          metric === "cost"
                            ? {
                                key: "cost",
                                label: t("metric.cost"),
                                value: p.cost_usd ?? 0,
                                color: "var(--gold)",
                              }
                            : {
                                key: "calls",
                                label: t("activity.calls"),
                                value: p.calls,
                                color: "var(--series-1)",
                              },
                        ],
                      }))}
                    />
                  </Panel>
                </div>
                <div className="page-aside">
                  <Panel>
                    <PanelHeader
                      title={t("providers.title")}
                      description={t("providers.description")}
                    />
                    <ProviderMix
                      providers={summary.providers}
                      empty={t("providers.none")}
                    />
                  </Panel>
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-3">
                <SearchInput
                  label={t("filter.search")}
                  value={query}
                  onChange={setQuery}
                />
                <div
                  role="group"
                  aria-label={t("confidence.label")}
                  className="flex flex-wrap gap-1.5"
                >
                  {(["", ...CONFIDENCES] as const).map((c) => (
                    <button
                      key={c || "all"}
                      type="button"
                      title={c ? t.dyn("confidence", `${c}Hint`, c) : undefined}
                      aria-pressed={confidence === c}
                      onClick={() => setConfidence(c)}
                      className={`inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-caption font-medium transition-colors ${
                        confidence === c
                          ? "border-brand bg-brand-soft text-brand"
                          : "border-border text-ink-secondary hover:bg-surface-hover"
                      }`}
                    >
                      {c ? (
                        <ConfidenceChip
                          label={t.dyn("confidence", c, c)}
                          icon={CONFIDENCE_ICON[c]}
                          count={summary.by_confidence[c] ?? 0}
                        />
                      ) : (
                        t("filter.all")
                      )}
                    </button>
                  ))}
                  <button
                    type="button"
                    aria-pressed={outside}
                    onClick={() => setOutside(!outside)}
                    className={`inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-caption font-medium transition-colors ${
                      outside
                        ? "border-brand bg-brand-soft text-brand"
                        : "border-border text-ink-secondary hover:bg-surface-hover"
                    }`}
                  >
                    {t("filter.outside")} · {summary.outside_catalog}
                  </button>
                  {summary.guardrails_firing ? (
                    <button
                      type="button"
                      aria-pressed={firing}
                      onClick={() => setFiring(!firing)}
                      data-testid="agents-filter-firing"
                      className={`inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-caption font-medium transition-colors ${
                        firing
                          ? "border-[var(--status-critical)] bg-critical-soft text-critical"
                          : "border-[var(--status-critical)]/40 text-critical hover:bg-critical-soft"
                      }`}
                    >
                      <ShieldAlert aria-hidden className="h-3.5 w-3.5" />
                      {t("guard.filter")} · {summary.guardrails_firing}
                    </button>
                  ) : null}
                </div>
              </div>

              {shown.length === 0 ? (
                <Panel>
                  <DataState kind="empty" title={t("empty.filtered")} />
                </Panel>
              ) : (
                <div
                  className="grid grid-cols-1 gap-4 md:grid-cols-2 2xl:grid-cols-3"
                  data-testid="agents-grid"
                >
                  {shown.map((agent) => (
                    <AgentCard key={agent.id} agent={agent} />
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      ) : null}
    </PageFrame>
  );
}

function ConfidenceChip({
  label,
  icon: Icon,
  count,
}: {
  label: string;
  icon: typeof Bot;
  count: number;
}) {
  return (
    <>
      <Icon aria-hidden className="h-3.5 w-3.5" />
      {label}
      <span className="tabular-nums opacity-70">{count}</span>
    </>
  );
}

function MapSection({ span }: { span: Window }) {
  const t = useT("agents");
  const [topology, setTopology] = useState<Topology | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    setTopology(null);
    setFailed(false);
    fetchTopology(span)
      .then((data) => active && setTopology(data))
      .catch(() => active && setFailed(true));
    return () => {
      active = false;
    };
  }, [span]);
  return (
    <Panel>
      <PanelHeader title={t("map.title")} description={t("map.hint")} />
      {failed ? <DataState kind="error" /> : null}
      {!failed && !topology ? <DataState kind="loading" /> : null}
      {topology && topology.edges.length === 0 ? (
        <p className="py-8 text-center text-caption text-ink-muted">
          {t("map.empty")}
        </p>
      ) : null}
      {topology && topology.edges.length > 0 ? (
        <AgentMap topology={topology} />
      ) : null}
    </Panel>
  );
}

/** With no agent yet, the MCP servers agents call may already report. */
function McpTeaser({ span, onOpen }: { span: Window; onOpen: () => void }) {
  const t = useT("agents");
  const [count, setCount] = useState(0);
  useEffect(() => {
    let active = true;
    fetchMcpServers(span)
      .then((body) => active && setCount(body.servers.length))
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [span]);
  if (!count) return null;
  return (
    <button
      type="button"
      onClick={onOpen}
      data-testid="agents-mcp-teaser"
      className="mb-6 flex w-full items-center gap-3 rounded-panel border border-border bg-surface px-4 py-3 text-left transition-colors hover:border-brand hover:bg-brand-soft/40"
    >
      <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-control bg-brand-soft text-brand">
        <Plug aria-hidden className="h-4.5 w-4.5" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-body font-semibold text-ink">
          {t("mcpTeaser.title", { count })}
        </span>
        <span className="block text-caption text-ink-secondary">
          {t("mcpTeaser.body")}
        </span>
      </span>
      <span className="shrink-0 text-caption font-semibold text-brand">
        {t("mcpTeaser.open")} →
      </span>
    </button>
  );
}

/** A quiet picture of what will happen: agents → Tracesis. */
function EmptyIllustration() {
  const nodes = [
    { x: 28, y: 30, c: "var(--series-6)" },
    { x: 28, y: 78, c: "var(--series-3)" },
    { x: 28, y: 126, c: "var(--series-1)" },
  ];
  return (
    <svg
      width="260"
      height="156"
      viewBox="0 0 260 156"
      aria-hidden
      className="max-w-full"
    >
      {nodes.map((n, i) => (
        <g key={i}>
          <path
            d={`M${n.x + 18} ${n.y} C 120 ${n.y}, 140 78, 196 78`}
            fill="none"
            stroke={n.c}
            strokeWidth="1.5"
            strokeDasharray="4 5"
            opacity="0.8"
          >
            <animate
              attributeName="stroke-dashoffset"
              from="18"
              to="0"
              dur="1.4s"
              repeatCount="indefinite"
            />
          </path>
          <circle
            cx={n.x}
            cy={n.y}
            r="17"
            fill={n.c}
            fillOpacity="0.16"
            stroke={n.c}
            strokeWidth="2"
          />
          <Sparkle x={n.x - 7} y={n.y - 7} color={n.c} />
        </g>
      ))}
      <rect
        x="196"
        y="50"
        width="56"
        height="56"
        rx="14"
        fill="var(--brand-soft)"
        stroke="var(--brand)"
        strokeWidth="2"
      />
      <path
        d="M210 70h28M210 78h20M210 86h12"
        stroke="var(--brand)"
        strokeWidth="3"
        strokeLinecap="round"
      />
    </svg>
  );
}

function Sparkle({ x, y, color }: { x: number; y: number; color: string }) {
  return (
    <g transform={`translate(${x} ${y})`}>
      <Sparkles width="14" height="14" color={color} />
    </g>
  );
}
