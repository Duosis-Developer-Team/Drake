/**
 * Typed surface of the agent inventory (Agent Visibility).
 *
 * Every agent here was found on first sight in the traces Tracesis stores;
 * nobody registered it. Counts and names only: prompts, answers and tool
 * arguments never reach Tracesis.
 */
import { apiGet, apiMutate } from "@/lib/api";

export type Confidence = "stable" | "named" | "service" | "observed";
export type Window = "24h" | "7d";

export interface AgentTotals {
  invocations: number;
  inference_calls: number;
  tool_calls: number;
  errors: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  reasoning_tokens: number;
  duration_ms: number;
}

export interface AgentModel {
  model: string | null;
  provider: string | null;
  calls: number;
  cost_usd?: number | null;
  priced?: boolean;
  tokens?: number;
  input_tokens?: number;
  output_tokens?: number;
  cache_read_tokens?: number;
  reasoning_tokens?: number;
  errors?: number;
}

export interface AgentPoint {
  t: string;
  calls?: number;
  errors: number;
  invocations?: number;
  inference_calls?: number;
  tool_calls?: number;
  input_tokens?: number;
  output_tokens?: number;
  cost_usd?: number;
}

export interface Agent {
  id: string;
  display_name: string;
  confidence: Confidence;
  ingest: "cluster" | "external";
  lifecycle: "observed" | "approved" | "retired";
  service_name: string | null;
  namespace: string | null;
  workload: string | null;
  cluster_ref: string | null;
  agent_external_id: string | null;
  first_seen_at: string;
  last_seen_at: string;
  new: boolean;
  project: { id: string; key: string; name: string } | null;
  environment: { id: string; key: string } | null;
  binding_id: string | null;
  totals: AgentTotals;
  cost_usd: number;
  unpriced_tokens: number;
  guardrails_firing?: number;
  providers: { provider: string; calls: number }[];
  models: AgentModel[];
  series: AgentPoint[];
}

export interface AgentDetail extends Agent {
  tools: {
    tool: string;
    calls: number;
    errors: number;
    avg_ms: number | null;
  }[];
  window: Window;
}

export interface AgentInventory {
  window: Window;
  discovering: boolean;
  items: Agent[];
  summary: {
    agents: number;
    active: number;
    new: number;
    unowned: number;
    outside_catalog: number;
    by_confidence: Record<Confidence, number>;
    providers: { provider: string; calls: number }[];
    totals: Pick<
      AgentTotals,
      | "invocations"
      | "inference_calls"
      | "tool_calls"
      | "errors"
      | "input_tokens"
      | "output_tokens"
    >;
    series: {
      t: string;
      calls: number;
      errors: number;
      tokens: number;
      cost_usd: number;
    }[];
    cost_usd: number;
    unpriced_tokens: number;
    guardrails_firing?: number;
  };
}

export interface AgentConnect {
  cluster_endpoint: string;
  external_endpoint: string | null;
  protocol: string;
  content_capture: string;
}

export const fetchAgents = (window: Window) =>
  apiGet<AgentInventory>(`/v1/agents?window=${window}`);
export const fetchAgent = (id: string, window: Window = "7d") =>
  apiGet<AgentDetail>(`/v1/agents/${encodeURIComponent(id)}?window=${window}`);
export const fetchConnect = () => apiGet<AgentConnect>("/v1/agents/connect");

export const CONFIDENCES: Confidence[] = [
  "stable",
  "named",
  "service",
  "observed",
];

// A provider keeps its colour everywhere: the well-known ones a fixed slot,
// any other a slot chosen from its name, so it never changes between pages.
const KNOWN: Record<string, number> = {
  anthropic: 6,
  openai: 3,
  "gcp.gemini": 1,
  "gcp.vertex_ai": 1,
  gemini: 1,
  "aws.bedrock": 2,
  "azure.ai.openai": 5,
  mistral: 4,
  ollama: 5,
};

export function providerSlot(provider: string | null | undefined): number {
  const key = (provider ?? "").toLowerCase();
  if (key in KNOWN) return KNOWN[key];
  let hash = 0;
  for (const ch of key) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return (hash % 6) + 1;
}

export const providerColor = (provider: string | null | undefined) =>
  `var(--series-${providerSlot(provider)})`;

const PROVIDER_NAMES: Record<string, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  "gcp.gemini": "Gemini",
  "gcp.vertex_ai": "Vertex AI",
  gemini: "Gemini",
  "aws.bedrock": "Bedrock",
  "azure.ai.openai": "Azure OpenAI",
  mistral: "Mistral",
  ollama: "Ollama",
  x_ai: "xAI",
};

export const providerName = (provider: string | null | undefined) =>
  provider ? (PROVIDER_NAMES[provider.toLowerCase()] ?? provider) : "—";

/** 1 234 567 → 1.2M; short enough for a card, exact in its title. */
export function compact(value: number, locale: string): string {
  return new Intl.NumberFormat(locale, {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}

export function callsOf(agent: Pick<Agent, "totals">): number {
  return (
    agent.totals.invocations +
    agent.totals.inference_calls +
    agent.totals.tool_calls
  );
}

export function errorRatio(agent: Pick<Agent, "totals">): number | null {
  const calls = callsOf(agent);
  return calls ? agent.totals.errors / calls : null;
}

// --- runs (phase 3) ---------------------------------------------------------

export type StoreState = "ok" | "not_configured" | "unavailable";

export interface AgentRun {
  trace_id: string;
  started_at: string;
  duration_ms: number;
  invocations: number;
  model_calls: number;
  tool_calls: number;
  errors: number;
  input_tokens: number;
  output_tokens: number;
  models: string[];
  tools: string[];
}

export interface AgentRuns {
  agent_id: string;
  window: Window;
  status: "any" | "error";
  state: StoreState;
  runs: AgentRun[];
}

export type Step = "agent" | "model" | "tool" | "mcp" | "genai" | "other";

export interface RunSpan {
  span_id: string;
  parent_id: string | null;
  name: string;
  step: Step;
  service: string | null;
  agent: { id: string; name: string; self: boolean } | null;
  offset_ms: number;
  duration_ms: number;
  status: "ok" | "error";
  provider: string | null;
  model: string | null;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  reasoning_tokens: number;
  tool: string | null;
  tool_type: string | null;
  mcp_method: string | null;
  server: string | null;
  error_type: string | null;
  http_status: string | null;
  http_route: string | null;
  cost_usd?: number | null;
}

export interface AgentRunDetail {
  agent_id: string;
  trace_id: string;
  state: StoreState;
  started_at?: string;
  duration_ms?: number;
  hidden_spans?: number;
  truncated?: boolean;
  spans?: RunSpan[];
  cost_usd?: number | null;
}

export const fetchRuns = (
  id: string,
  window: Window,
  status: "any" | "error" = "any",
) =>
  apiGet<AgentRuns>(
    `/v1/agents/${encodeURIComponent(id)}/runs?window=${window}&status=${status}`,
  );
export const fetchRun = (id: string, traceId: string) =>
  apiGet<AgentRunDetail>(
    `/v1/agents/${encodeURIComponent(id)}/runs/${encodeURIComponent(traceId)}`,
  );

export const STEP_COLOR: Record<Step, string> = {
  agent: "var(--series-1)",
  model: "var(--series-3)",
  tool: "var(--series-6)",
  mcp: "var(--series-4)",
  genai: "var(--series-2)",
  other: "var(--text-muted)",
};

/** What a step is called in a run: the model, the tool, the agent — the span name last. */
export function stepLabel(span: RunSpan): string {
  if (span.step === "model" && span.model) return span.model;
  if (span.step === "tool" && span.tool) return span.tool;
  if (span.step === "agent" && span.agent) return span.agent.name;
  if (span.mcp_method) return span.mcp_method;
  if (span.http_route) return span.http_route;
  return span.name;
}

export interface RunRow {
  span: RunSpan;
  depth: number;
  children: number;
  /** Ids of the ancestors, root first: collapsing one hides this row. */
  path: string[];
}

/**
 * The run as rows in reading order: each step under its parent, siblings by
 * start time. A span whose parent is not in the run (left out, or lost) is a
 * root, so nothing disappears.
 */
export function runRows(spans: RunSpan[]): RunRow[] {
  const ids = new Set(spans.map((s) => s.span_id));
  const kids = new Map<string | null, RunSpan[]>();
  for (const span of spans) {
    const parent =
      span.parent_id && ids.has(span.parent_id) ? span.parent_id : null;
    const list = kids.get(parent) ?? [];
    list.push(span);
    kids.set(parent, list);
  }
  for (const list of kids.values())
    list.sort(
      (a, b) => a.offset_ms - b.offset_ms || a.span_id.localeCompare(b.span_id),
    );
  const rows: RunRow[] = [];
  const seen = new Set<string>();
  const walk = (parent: string | null, depth: number, path: string[]) => {
    for (const span of kids.get(parent) ?? []) {
      if (seen.has(span.span_id)) continue;
      seen.add(span.span_id);
      rows.push({
        span,
        depth,
        children: kids.get(span.span_id)?.length ?? 0,
        path,
      });
      walk(span.span_id, depth + 1, [...path, span.span_id]);
    }
  };
  walk(null, 0, []);
  return rows;
}

/**
 * Where a run's time went: time inside model calls, inside tools, and the
 * rest (the agent's own work and waiting). Overlapping steps of one kind are
 * merged, so parallel tool calls are not counted twice.
 */
export function timeShare(spans: RunSpan[], total: number) {
  const union = (kind: Step) => {
    const spansOf = spans
      .filter((s) => s.step === kind)
      .map((s) => [s.offset_ms, s.offset_ms + s.duration_ms] as const)
      .sort((a, b) => a[0] - b[0]);
    let sum = 0;
    let end = -Infinity;
    let start = 0;
    for (const [a, b] of spansOf) {
      if (a > end) {
        if (end > -Infinity) sum += end - start;
        start = a;
        end = b;
      } else end = Math.max(end, b);
    }
    if (end > -Infinity) sum += end - start;
    return sum;
  };
  const model = Math.min(total, union("model"));
  const tool = Math.min(Math.max(0, total - model), union("tool"));
  return { model, tool, other: Math.max(0, total - model - tool) };
}

// --- topology (phase 3b) -----------------------------------------------------

export interface TopologyNode {
  id: string;
  kind: "agent" | "model" | "tool";
  label: string;
  calls: number;
  errors: number;
  agent_id?: string;
  confidence?: Confidence;
  ingest?: "cluster" | "external";
  project?: string | null;
  provider?: string | null;
  tokens?: number;
  server?: string | null;
  avg_ms?: number | null;
}

export interface TopologyEdge {
  source: string;
  target: string;
  kind: "agent" | "model" | "tool";
  calls: number;
  errors: number;
  tokens?: number;
  avg_ms?: number | null;
}

export interface Topology {
  window: Window;
  focus?: string | null;
  nodes: TopologyNode[];
  edges: TopologyEdge[];
}

export const fetchTopology = (window: Window, focus?: string) =>
  apiGet<Topology>(
    `/v1/agents/topology?window=${window}${focus ? `&focus=${encodeURIComponent(focus)}` : ""}`,
  );

export interface Placed {
  node: TopologyNode;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface PlacedHeader {
  key: string;
  kind: "models" | "tools" | "server";
  label: string;
  x: number;
  y: number;
  w: number;
}

export interface MapLayout {
  width: number;
  height: number;
  nodes: Placed[];
  headers: PlacedHeader[];
  byId: Map<string, Placed>;
}

const AGENT_W = 216;
const AGENT_H = 58;
const RES_W = 248;
const RES_H = 42;
const GAP = 10;
const HEADER_H = 26;
const PAD = 8;

/**
 * Columns: agents by depth (a sub-agent one column right of its caller),
 * then one column of what agents reach — models first, then tools grouped
 * by the server they run on. Within a column, nodes sit at the average
 * height of what they connect to, which keeps lines from crossing.
 */
export function layoutTopology(topology: Topology, width: number): MapLayout {
  const agents = topology.nodes.filter((n) => n.kind === "agent");
  const models = topology.nodes.filter((n) => n.kind === "model");
  const tools = topology.nodes.filter((n) => n.kind === "tool");
  const delegations = topology.edges.filter((e) => e.kind === "agent");

  // Depth: longest chain of callers, bounded (cycles stop at the bound).
  const depth = new Map<string, number>(agents.map((a) => [a.id, 0]));
  for (let pass = 0; pass < 3; pass++) {
    for (const e of delegations) {
      const d = (depth.get(e.source) ?? 0) + 1;
      if (depth.has(e.target) && d > (depth.get(e.target) ?? 0))
        depth.set(e.target, Math.min(d, 2));
    }
  }
  const agentCols = Math.max(1, ...[...depth.values()].map((d) => d + 1));
  const cols = agentCols + 1;
  const usable = Math.max(width, AGENT_W * agentCols + RES_W + 80 * agentCols);
  const step = (usable - PAD * 2 - RES_W) / Math.max(1, cols - 1);
  const colX = (c: number) => PAD + c * step;

  const placed = new Map<string, Placed>();
  const headers: PlacedHeader[] = [];
  const neighbours = (id: string) =>
    topology.edges.flatMap((e) =>
      e.target === id ? [e.source] : e.source === id ? [e.target] : [],
    );
  const centre = (id: string, fallback: number) => {
    const ys = neighbours(id)
      .map((n) => placed.get(n))
      .filter((p): p is Placed => !!p)
      .map((p) => p.y + p.h / 2);
    return ys.length ? ys.reduce((a, b) => a + b, 0) / ys.length : fallback;
  };

  const columnHeights: number[] = [];
  for (let c = 0; c < agentCols; c++) {
    const inCol = agents.filter((a) => (depth.get(a.id) ?? 0) === c);
    const ordered =
      c === 0
        ? [...inCol].sort(
            (a, b) => b.calls - a.calls || a.label.localeCompare(b.label),
          )
        : [...inCol].sort((a, b) => centre(a.id, 0) - centre(b.id, 0));
    let y = HEADER_H;
    for (const node of ordered) {
      placed.set(node.id, { node, x: colX(c), y, w: AGENT_W, h: AGENT_H });
      y += AGENT_H + GAP;
    }
    columnHeights.push(y);
  }

  // The resource column's height does not depend on its order: know it
  // first, centre the agent columns against it, then order resources by
  // where their agents now sit.
  const servers = new Set(tools.map((x) => x.server ?? "")).size;
  const resourceHeight =
    (models.length ? HEADER_H + models.length * (RES_H + GAP) + GAP : 0) +
    (tools.length
      ? HEADER_H +
        servers * (HEADER_H - 4 + GAP / 2) +
        tools.length * (RES_H + GAP)
      : 0);
  const height =
    Math.max(...columnHeights, resourceHeight, AGENT_H + HEADER_H) + PAD;
  for (let c = 0; c < agentCols; c++) {
    const shift = Math.max(0, (height - columnHeights[c]) / 2 - HEADER_H / 2);
    for (const p of placed.values())
      if (p.x === colX(c) && p.node.kind === "agent") p.y += shift;
  }

  const rx = colX(cols - 1);
  let y = 0;
  if (models.length) {
    headers.push({
      key: "models",
      kind: "models",
      label: "",
      x: rx,
      y,
      w: RES_W,
    });
    y += HEADER_H;
    for (const node of [...models].sort(
      (a, b) => centre(a.id, 0) - centre(b.id, 0) || b.calls - a.calls,
    )) {
      placed.set(node.id, { node, x: rx, y, w: RES_W, h: RES_H });
      y += RES_H + GAP;
    }
    y += GAP;
  }
  if (tools.length) {
    headers.push({
      key: "tools",
      kind: "tools",
      label: "",
      x: rx,
      y,
      w: RES_W,
    });
    y += HEADER_H;
    const groups = new Map<string, TopologyNode[]>();
    for (const t of tools) {
      const key = t.server ?? "";
      groups.set(key, [...(groups.get(key) ?? []), t]);
    }
    const groupCentre = (list: TopologyNode[]) =>
      list.reduce((a, n) => a + centre(n.id, 0), 0) / Math.max(1, list.length);
    const orderedGroups = [...groups.entries()].sort(
      (a, b) => groupCentre(a[1]) - groupCentre(b[1]),
    );
    for (const [server, list] of orderedGroups) {
      headers.push({
        key: `server:${server}`,
        kind: "server",
        label: server,
        x: rx,
        y,
        w: RES_W,
      });
      y += HEADER_H - 4;
      for (const node of [...list].sort(
        (a, b) => centre(a.id, 0) - centre(b.id, 0) || b.calls - a.calls,
      )) {
        placed.set(node.id, { node, x: rx, y, w: RES_W, h: RES_H });
        y += RES_H + GAP;
      }
      y += GAP / 2;
    }
  }
  return {
    width: usable,
    height,
    nodes: [...placed.values()],
    headers,
    byId: placed,
  };
}

/** Line width for a link carrying `calls`, against the busiest link shown. */
export function edgeWidth(calls: number, max: number): number {
  if (max <= 0) return 1.25;
  return 1.25 + (3.75 * Math.log1p(calls)) / Math.log1p(max);
}

// --- MCP servers (phase 3c) --------------------------------------------------

export interface McpUse {
  name: string | null;
  calls: number;
  errors: number;
  avg_ms: number | null;
}

export interface McpServer {
  id: string;
  service_name: string | null;
  cluster_ref: string | null;
  namespace: string | null;
  workload: string | null;
  first_seen_at: string;
  last_seen_at: string;
  project: { id: string; key: string; name: string } | null;
  environment: string | null;
  calls: number;
  errors: number;
  avg_ms: number | null;
  tools: McpUse[];
  clients: McpUse[];
  methods: McpUse[];
  series: { t: string; calls: number; errors: number }[];
}

export const fetchMcpServers = (window: Window) =>
  apiGet<{ window: Window; servers: McpServer[] }>(
    `/v1/agents/mcp-servers?window=${window}`,
  );

// --- cost (phase 4) ----------------------------------------------------------

export interface PriceFetch {
  fetched_at: string;
  status: "live" | "fallback" | "failed";
  source: string;
  models: number;
  changed: number;
}

export interface PriceStatus {
  enabled: boolean;
  currency: "USD";
  basis: string;
  last_fetch: PriceFetch | null;
  last_good_fetch: PriceFetch | null;
  priced_models: number;
  unpriced: { provider: string | null; model: string }[];
}

export const fetchPrices = () => apiGet<PriceStatus>("/v1/agents/prices");

/** US dollars, readable at any size: $0, $0.0042, $3.20, $12.4K. */
export function usd(value: number | null | undefined, locale: string): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (value === 0) return "$0";
  const abs = Math.abs(value);
  if (abs < 0.01) {
    return new Intl.NumberFormat(locale, {
      style: "currency",
      currency: "USD",
      maximumSignificantDigits: 2,
    }).format(value);
  }
  if (abs < 10_000) {
    return new Intl.NumberFormat(locale, {
      style: "currency",
      currency: "USD",
    }).format(value);
  }
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency: "USD",
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}

// --- guardrails (phase 5) ----------------------------------------------------

export type GuardKind =
  "failure_ratio" | "avg_run_seconds" | "cost_day" | "silence_hours";
export const GUARD_KINDS: GuardKind[] = [
  "failure_ratio",
  "avg_run_seconds",
  "cost_day",
  "silence_hours",
];
export type GuardState = "ok" | "firing" | "no_data" | "pending";

export interface Guardrail {
  kind: GuardKind;
  set: boolean;
  threshold: number | null;
  severity: "warning" | "critical" | null;
  enabled: boolean;
  version: number | null;
  state: GuardState | null;
  value: number | null;
  since: string | null;
  evaluated_at: string | null;
  incident_id: string | null;
}

export interface Guardrails {
  agent_id: string;
  can_manage: boolean;
  pages: boolean;
  min_calls: number;
  evaluating: boolean;
  items: Guardrail[];
}

export const fetchGuardrails = (id: string) =>
  apiGet<Guardrails>(`/v1/agents/${encodeURIComponent(id)}/guardrails`);

export const setGuardrail = (
  csrfToken: string,
  id: string,
  kind: GuardKind,
  body: {
    threshold: number;
    severity: "warning" | "critical";
    enabled: boolean;
  },
) =>
  apiMutate<Guardrails>(
    `/v1/agents/${encodeURIComponent(id)}/guardrails/${kind}`,
    {
      csrfToken,
      body: { ...body, threshold: String(body.threshold) },
    },
  );

export const removeGuardrail = (
  csrfToken: string,
  id: string,
  kind: GuardKind,
) =>
  apiMutate<Guardrails>(
    `/v1/agents/${encodeURIComponent(id)}/guardrails/${kind}`,
    {
      csrfToken,
      method: "DELETE",
    },
  );

/** A guardrail's threshold as people type it: a percentage for the failure rate. */
export const toInput = (kind: GuardKind, threshold: number) =>
  kind === "failure_ratio" ? Math.round(threshold * 10000) / 100 : threshold;
export const fromInput = (kind: GuardKind, value: number) =>
  kind === "failure_ratio" ? value / 100 : value;
