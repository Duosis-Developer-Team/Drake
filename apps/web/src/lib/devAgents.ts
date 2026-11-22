/**
 * Development agents (Agent Visibility): who changes the watched
 * repositories, with which coding agent, and — when Claude Code's own
 * telemetry is connected — what it cost and what it did.
 *
 * A repository is a node whether or not it is catalogued: a catalogued one
 * carries its project (name, logo, services); any other repository of the
 * watched GitHub organisations is shown by its own name.
 *
 * Commits come from the repositories (a `Co-Authored-By:` trailer, a
 * "Generated with …" marker, an agent author); usage from Claude Code.
 * A person without telemetry has `telemetry: null`: not connected, never 0.
 */

import { apiGet, apiMutate } from "@/lib/api";

export const DEV_WINDOWS = ["7d", "30d", "90d"] as const;
export type DevWindow = (typeof DEV_WINDOWS)[number];

export const CODING_AGENTS = [
  "claude_code",
  "copilot",
  "codex",
  "cursor",
  "devin",
  "other_ai",
  "none",
] as const;
export type CodingAgent = (typeof CODING_AGENTS)[number];

export interface UsageItem {
  name: string;
  calls: number;
  failures: number;
  cost_usd: number;
  tokens: number;
}

export interface DevTelemetry {
  cost_usd: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_creation_tokens: number;
  tokens: number;
  api_requests: number;
  api_errors: number;
  prompts: number;
  sessions: number;
  active_seconds: number | null;
  lines_added: number | null;
  lines_removed: number | null;
  commits: number | null;
  pull_requests: number | null;
  edits_accepted: number;
  edits_rejected: number;
  tools?: UsageItem[];
  mcp_tools?: UsageItem[];
  models?: UsageItem[];
  languages?: UsageItem[];
}

export interface DevPerson {
  key: string;
  name: string;
  logins: string[];
  linked: boolean;
  bot: boolean;
  commits: number;
  additions: number;
  deletions: number;
  agents: Partial<Record<CodingAgent, number>>;
  tools: CodingAgent[];
  /** Repository node ids (`gh:<id>`, or `repo:<owner/name>`). */
  repositories: string[];
  active_days: number;
  last_at: string | null;
  series: number[];
  telemetry: DevTelemetry | null;
}

export interface DevRepository {
  /** `gh:<GitHub id>` (a rename keeps it), or `repo:<owner/name>`. */
  id: string;
  /** owner/name */
  full_name: string;
  name: string;
  /** The repository on GitHub, built by the API. */
  url: string;
  /** In the catalog: it has a project (logo, name) and services. */
  catalogued: boolean;
  project_id: string | null;
  project_key: string | null;
  project_name: string | null;
  commits: number;
  agent_commits: number;
  people: number;
  services: number;
  /** Claude Code's cost here; null when no session named it. */
  cost_usd: number | null;
}

export interface DevService {
  repository: string;
  /** null: files outside any catalog service (repository level). */
  key: string | null;
  commits: number;
}

export interface DevEdge {
  person: string;
  repository: string;
  agent: CodingAgent;
  commits: number;
  additions: number;
  deletions: number;
  last_at: string | null;
  telemetry: DevTelemetry | null;
}

export interface DevMap {
  window: DevWindow;
  as_of: string;
  days: string[];
  configured: {
    commits: boolean;
    telemetry: boolean;
    repositories: number;
    /** The GitHub organisations read whole (shown to org-wide viewers only). */
    organisations: string[];
  };
  summary: {
    commits: number;
    agent_commits: number;
    people: number;
    repositories: number;
    catalogued: number;
    top_agent: CodingAgent | null;
    agents: Partial<Record<CodingAgent, number>>;
    cost_usd: number | null;
    tokens: number | null;
    sessions: number | null;
  };
  people: DevPerson[];
  repositories: DevRepository[];
  services: DevService[];
  edges: DevEdge[];
}

export interface DevCommit {
  sha: string;
  short: string;
  title: string;
  authored_at: string;
  author_login: string | null;
  author_name: string;
  person: string;
  agent: CodingAgent;
  evidence: "trailer" | "pr_marker" | "bot_author" | null;
  additions: number;
  deletions: number;
  services: string[];
  pr_number: number | null;
  repository: string;
  repository_id: string;
  project_id: string | null;
  project_key: string | null;
  project_name: string | null;
  url: string;
  pr_url: string | null;
}

export interface DevConnect {
  endpoint: string | null;
  collecting: boolean;
  projects: string[];
  protocol: string;
  content_capture: string;
}

export const fetchDevMap = (window: DevWindow) =>
  apiGet<DevMap>(`/v1/agents/dev/map?window=${window}`);

export function devCommitsPath(filter: {
  window: DevWindow;
  person?: string;
  repository?: string;
  service?: string;
  agent?: CodingAgent;
}): string {
  const q = new URLSearchParams({ window: filter.window, limit: "200" });
  if (filter.person) q.set("person", filter.person);
  if (filter.repository) q.set("repository", filter.repository);
  if (filter.service) q.set("service", filter.service);
  if (filter.agent) q.set("agent", filter.agent);
  return `/v1/agents/dev/commits?${q.toString()}`;
}

export const fetchDevCommits = (path: string) =>
  apiGet<{ items: DevCommit[]; total: number }>(path);

export const fetchDevConnect = () => apiGet<DevConnect>("/v1/agents/dev/connect");

export function setDevAlias(
  csrfToken: string,
  body: { kind: "github" | "claude"; alias: string; person_key: string; display_name?: string },
) {
  return apiMutate(`/v1/agents/dev/aliases`, { csrfToken, method: "PUT", body });
}

/** The agents' own names: proper nouns, the same in every language. */
export const AGENT_NAME: Record<CodingAgent, string> = {
  claude_code: "Claude Code",
  copilot: "Copilot",
  codex: "Codex",
  cursor: "Cursor",
  devin: "Devin",
  other_ai: "Other agent",
  none: "By hand",
};

/** Colour per coding agent: the same in both themes, readable on either. */
export const AGENT_COLOUR: Record<CodingAgent, string> = {
  claude_code: "#d97757",
  copilot: "#8b5cf6",
  codex: "#10a37f",
  cursor: "#3b82f6",
  devin: "#14b8a6",
  other_ai: "#e0a33a",
  none: "var(--text-muted)",
};

/** A stable colour per person, from their key. */
const PERSON_PALETTE = [
  "#6366f1",
  "#0ea5e9",
  "#10b981",
  "#f59e0b",
  "#ef4444",
  "#a855f7",
  "#14b8a6",
  "#f97316",
  "#65a30d",
  "#ec4899",
];

export function personColour(key: string): string {
  let hash = 0;
  for (let i = 0; i < key.length; i += 1) hash = (hash * 31 + key.charCodeAt(i)) >>> 0;
  return PERSON_PALETTE[hash % PERSON_PALETTE.length];
}

export function initials(name: string): string {
  const words = name
    .replace(/[·[\]()]/g, " ")
    .split(/\s+/)
    .filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toLocaleUpperCase();
  return (words[0][0] + words[words.length - 1][0]).toLocaleUpperCase();
}

export function usd(value: number, locale: string): string {
  return new Intl.NumberFormat(locale === "tr" ? "tr-TR" : "en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: value < 10 ? 2 : 0,
  }).format(value);
}

export function compact(value: number, locale: string): string {
  return new Intl.NumberFormat(locale === "tr" ? "tr-TR" : "en-US", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}
