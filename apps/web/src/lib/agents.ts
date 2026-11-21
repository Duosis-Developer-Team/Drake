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
  tools: { tool: string; calls: number; errors: number; avg_ms: number | null }[];
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
      "invocations" | "inference_calls" | "tool_calls" | "errors" | "input_tokens" | "output_tokens"
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

export const fetchAgents = (window: Window) => apiGet<AgentInventory>(`/v1/agents?window=${window}`);
export const fetchAgent = (id: string, window: Window = "7d") =>
  apiGet<AgentDetail>(`/v1/agents/${encodeURIComponent(id)}?window=${window}`);
export const fetchConnect = () => apiGet<AgentConnect>("/v1/agents/connect");

export const CONFIDENCES: Confidence[] = ["stable", "named", "service", "observed"];

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
  return new Intl.NumberFormat(locale, { notation: "compact", maximumFractionDigits: 1 }).format(
    value,
  );
}

export function callsOf(agent: Pick<Agent, "totals">): number {
  return agent.totals.invocations + agent.totals.inference_calls + agent.totals.tool_calls;
}

export function errorRatio(agent: Pick<Agent, "totals">): number | null {
  const calls = callsOf(agent);
  return calls ? agent.totals.errors / calls : null;
}
