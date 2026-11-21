"use client";

/**
 * The agent map: which agents there are, which hand work to which, and what
 * they reach — models, and tools grouped by the server they run on.
 *
 * Lines are calls over the window: width is volume (logarithmic, so one
 * busy link does not flatten the rest), colour is the kind of call — the
 * same colours as a run's steps — and red when more than one call in
 * twenty failed. Hover or focus a node to light up its neighbourhood;
 * select an agent to open it. Every link is also in the table under the
 * drawing.
 */

import {
  ChevronDown,
  ChevronRight,
  Server,
  Sparkles,
  Wrench,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { AgentGlyph, useWidth } from "@/components/agents/visuals";
import { DataState } from "@/components/state/DataState";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import {
  compact,
  edgeWidth,
  fetchTopology,
  layoutTopology,
  providerColor,
  providerName,
  STEP_COLOR,
  type Placed,
  type Topology,
  type TopologyEdge,
} from "@/lib/agents";
import { useFormat, useLocale, useT } from "@/lib/i18n";

const FAILING = 0.05;

function edgeColor(e: TopologyEdge): string {
  if (e.calls > 0 && e.errors / e.calls > FAILING)
    return "var(--status-critical)";
  return STEP_COLOR[e.kind];
}

function path(from: Placed, to: Placed): string {
  const x1 = from.x + from.w;
  const y1 = from.y + from.h / 2;
  const x2 = to.x;
  const y2 = to.y + to.h / 2;
  const dx = Math.max(40, (x2 - x1) / 2);
  return `M${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
}

export function AgentMap({
  topology,
  compactMode = false,
}: {
  topology: Topology;
  compactMode?: boolean;
}) {
  const t = useT("agents");
  const fmt = useFormat();
  const { locale } = useLocale();
  const router = useRouter();
  const [ref, width] = useWidth<HTMLDivElement>(960);
  const [hover, setHover] = useState<string | null>(null);
  const [table, setTable] = useState(false);
  const layout = useMemo(
    () => layoutTopology(topology, width),
    [topology, width],
  );
  const maxCalls = Math.max(0, ...topology.edges.map((e) => e.calls));
  const labels = new Map(topology.nodes.map((n) => [n.id, n.label]));

  const lit = useMemo(() => {
    if (!hover) return null;
    const set = new Set([hover]);
    for (const e of topology.edges) {
      if (e.source === hover) set.add(e.target);
      if (e.target === hover) set.add(e.source);
    }
    return set;
  }, [hover, topology.edges]);

  // An agent's ring shows where its model calls went.
  const providersOf = useMemo(() => {
    const map = new Map<string, { provider: string; calls: number }[]>();
    for (const e of topology.edges) {
      if (e.kind !== "model") continue;
      const provider =
        topology.nodes.find((n) => n.id === e.target)?.provider ?? "";
      map.set(e.source, [
        ...(map.get(e.source) ?? []),
        { provider, calls: e.calls },
      ]);
    }
    return map;
  }, [topology]);

  const describe = (e: TopologyEdge) =>
    [
      `${labels.get(e.source)} → ${labels.get(e.target)}`,
      t("map.calls", { count: fmt.number(e.calls) }),
      e.errors ? t("map.failed", { count: fmt.number(e.errors) }) : null,
      e.avg_ms ? t("detail.avg", { value: fmt.latency(e.avg_ms) }) : null,
      e.tokens ? `${compact(e.tokens, locale)} tok` : null,
    ]
      .filter(Boolean)
      .join(" · ");

  return (
    <div data-testid="agent-map">
      <div ref={ref} className="overflow-x-auto">
        <div
          className="relative"
          style={{ width: layout.width, height: layout.height }}
        >
          <svg
            width={layout.width}
            height={layout.height}
            className="absolute inset-0"
            role="img"
            aria-label={t("map.title")}
          >
            {topology.edges.map((e) => {
              const from = layout.byId.get(e.source);
              const to = layout.byId.get(e.target);
              if (!from || !to) return null;
              const on =
                !lit ||
                (lit.has(e.source) &&
                  lit.has(e.target) &&
                  (e.source === hover || e.target === hover));
              return (
                <g key={`${e.source}>${e.target}`}>
                  <path
                    d={path(from, to)}
                    fill="none"
                    stroke={edgeColor(e)}
                    strokeWidth={edgeWidth(e.calls, maxCalls)}
                    strokeLinecap="round"
                    opacity={on ? (lit ? 0.95 : 0.5) : 0.08}
                    className="transition-opacity duration-150"
                  >
                    <title>{describe(e)}</title>
                  </path>
                  {lit && on ? (
                    <path
                      d={path(from, to)}
                      fill="none"
                      stroke="var(--surface-1)"
                      strokeWidth={1.25}
                      strokeDasharray="2 14"
                      className="motion-safe:animate-[trace-flow_1.2s_linear_infinite]"
                      opacity={0.9}
                    />
                  ) : null}
                </g>
              );
            })}
          </svg>

          {layout.headers.map((h) => (
            <div
              key={h.key}
              className="absolute flex items-center gap-1.5 truncate text-micro text-ink-muted"
              style={{ left: h.x, top: h.y + 4, width: h.w, height: 18 }}
            >
              {h.kind === "models" ? (
                <>
                  <Sparkles
                    aria-hidden
                    className="h-3.5 w-3.5"
                    style={{ color: STEP_COLOR.model }}
                  />
                  <span className="font-semibold tracking-wide uppercase">
                    {t("map.models")}
                  </span>
                </>
              ) : h.kind === "tools" ? (
                <>
                  <Wrench
                    aria-hidden
                    className="h-3.5 w-3.5"
                    style={{ color: STEP_COLOR.tool }}
                  />
                  <span className="font-semibold tracking-wide uppercase">
                    {t("map.tools")}
                  </span>
                </>
              ) : (
                <>
                  <Server aria-hidden className="h-3 w-3 shrink-0" />
                  <span className="truncate font-mono">
                    {h.label || t("map.inProcess")}
                  </span>
                </>
              )}
            </div>
          ))}

          {layout.nodes.map((p) => {
            const n = p.node;
            const dim = lit && !lit.has(n.id);
            const failing = n.calls > 0 && n.errors / n.calls > FAILING;
            const common = {
              onMouseEnter: () => setHover(n.id),
              onMouseLeave: () => setHover(null),
              onFocus: () => setHover(n.id),
              onBlur: () => setHover(null),
              style: { left: p.x, top: p.y, width: p.w, height: p.h },
            };
            if (n.kind === "agent") {
              return (
                <button
                  key={n.id}
                  type="button"
                  {...common}
                  onClick={() =>
                    n.agent_id && router.push(`/agents/${n.agent_id}`)
                  }
                  data-testid={`map-node-${n.label}`}
                  aria-label={`${n.label} · ${t("map.calls", { count: fmt.number(n.calls) })}`}
                  className={`absolute flex items-center gap-2.5 rounded-panel border bg-surface px-2.5 text-left shadow-sm transition-all duration-150 hover:shadow-md focus-visible:outline-2 focus-visible:outline-brand ${
                    n.confidence === "observed" ? "border-dashed" : ""
                  } ${hover === n.id ? "border-brand" : "border-border"} ${dim ? "opacity-30" : ""}`}
                >
                  <AgentGlyph
                    name={n.label}
                    providers={providersOf.get(n.id) ?? []}
                    size={36}
                    observed={n.confidence === "observed"}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-caption font-semibold text-ink">
                      {n.label}
                    </span>
                    <span className="block truncate text-micro text-ink-muted">
                      {n.project ?? t("card.outside")} ·{" "}
                      {compact(n.calls, locale)}
                      {failing ? (
                        <span className="text-critical">
                          {" "}
                          · {t("map.failed", { count: fmt.number(n.errors) })}
                        </span>
                      ) : null}
                    </span>
                  </span>
                </button>
              );
            }
            const Icon = n.kind === "model" ? Sparkles : Wrench;
            const color =
              n.kind === "model" ? STEP_COLOR.model : STEP_COLOR.tool;
            return (
              <div
                key={n.id}
                tabIndex={0}
                {...common}
                data-testid={`map-node-${n.label}`}
                className={`absolute flex items-center gap-2 rounded-control border bg-surface px-2 transition-opacity duration-150 ${
                  hover === n.id ? "border-brand" : "border-border"
                } ${dim ? "opacity-30" : ""}`}
              >
                <span
                  className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md"
                  style={{
                    background: `color-mix(in srgb, ${color} 16%, transparent)`,
                    color,
                  }}
                >
                  <Icon aria-hidden className="h-3.5 w-3.5" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-mono text-micro font-medium text-ink">
                    {n.label}
                  </span>
                  <span className="flex items-center gap-1 truncate text-[0.6875rem] text-ink-muted">
                    {n.kind === "model" && n.provider ? (
                      <>
                        <span
                          className="h-1.5 w-1.5 shrink-0 rounded-full"
                          style={{ background: providerColor(n.provider) }}
                        />
                        {providerName(n.provider)} ·{" "}
                      </>
                    ) : null}
                    {compact(n.calls, locale)}
                    {n.kind === "model" && n.tokens
                      ? ` · ${compact(n.tokens, locale)} tok`
                      : ""}
                    {n.kind === "tool" && n.avg_ms
                      ? ` · ${fmt.latency(n.avg_ms)}`
                      : ""}
                    {failing ? (
                      <span className="text-critical">
                        &nbsp;·{" "}
                        {t("map.failed", { count: fmt.number(n.errors) })}
                      </span>
                    ) : null}
                  </span>
                </span>
              </div>
            );
          })}
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-3">
        <ul className="flex flex-wrap gap-x-4 gap-y-1 text-micro text-ink-muted">
          {(
            [
              ["agent", t("map.legendAgent")],
              ["model", t("map.legendModel")],
              ["tool", t("map.legendTool")],
            ] as const
          ).map(([k, label]) => (
            <li key={k} className="inline-flex items-center gap-1.5">
              <span
                className="h-[3px] w-5 rounded-full"
                style={{ background: STEP_COLOR[k] }}
              />
              {label}
            </li>
          ))}
          <li className="inline-flex items-center gap-1.5">
            <span className="h-[3px] w-5 rounded-full bg-[var(--status-critical)]" />
            {t("map.legendFailing")}
          </li>
          <li className="inline-flex items-center gap-1.5">
            <svg width="22" height="10" aria-hidden>
              <path
                d="M1 8 L21 8"
                stroke="var(--text-muted)"
                strokeWidth="1"
                strokeLinecap="round"
              />
              <path
                d="M1 3 L21 3"
                stroke="var(--text-muted)"
                strokeWidth="3.5"
                strokeLinecap="round"
              />
            </svg>
            {t("map.legendWidth")}
          </li>
        </ul>
        {!compactMode ? (
          <button
            type="button"
            onClick={() => setTable(!table)}
            aria-expanded={table}
            className="inline-flex items-center gap-1 text-caption font-medium text-ink-secondary hover:text-ink"
          >
            {table ? (
              <ChevronDown className="h-3.5 w-3.5" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" />
            )}
            {t("map.table")}
          </button>
        ) : null}
      </div>
      {table ? (
        <table
          className="mt-3 w-full text-caption"
          data-testid="agent-map-table"
        >
          <thead>
            <tr className="border-b border-border text-left text-micro text-ink-muted">
              <th className="py-1.5 font-medium">{t("map.from")}</th>
              <th className="py-1.5 font-medium">{t("map.to")}</th>
              <th className="py-1.5 text-right font-medium">
                {t("card.calls")}
              </th>
              <th className="py-1.5 text-right font-medium">
                {t("card.failed")}
              </th>
            </tr>
          </thead>
          <tbody>
            {[...topology.edges]
              .sort((a, b) => b.calls - a.calls)
              .map((e) => (
                <tr
                  key={`${e.source}>${e.target}`}
                  className="border-b border-border/60"
                >
                  <td className="py-1.5">{labels.get(e.source)}</td>
                  <td className="py-1.5 font-mono text-micro">
                    {labels.get(e.target)}
                  </td>
                  <td className="py-1.5 text-right tabular-nums">
                    {fmt.number(e.calls)}
                  </td>
                  <td
                    className={`py-1.5 text-right tabular-nums ${e.errors ? "text-critical" : ""}`}
                  >
                    {fmt.number(e.errors)}
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      ) : null}
    </div>
  );
}

/** One agent's neighbourhood: its callers, its callees and what it reaches. */
export function AgentConnections({
  agentId,
  span,
}: {
  agentId: string;
  span: "24h" | "7d";
}) {
  const t = useT("agents");
  const [topology, setTopology] = useState<Topology | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    setTopology(null);
    setFailed(false);
    fetchTopology(span, agentId)
      .then((data) => active && setTopology(data))
      .catch(() => active && setFailed(true));
    return () => {
      active = false;
    };
  }, [agentId, span]);
  if (failed || (topology && topology.edges.length === 0)) return null;
  return (
    <Panel data-testid="agent-connections">
      <PanelHeader
        title={t("map.connections")}
        description={t("map.connectionsHint")}
      />
      {topology ? (
        <AgentMap topology={topology} compactMode />
      ) : (
        <DataState kind="loading" />
      )}
    </Panel>
  );
}
