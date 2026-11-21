/**
 * Typed surface of the agent inventory (Agent Visibility).
 *
 * Every agent here was found on first sight in the traces Tracesis stores;
 * nobody registered it. Counts and names only: prompts, answers and tool
 * arguments never reach Tracesis.
 */
import { apiGet } from "@/lib/api";

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
    series: { t: string; calls: number; errors: number; tokens: number }[];
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
