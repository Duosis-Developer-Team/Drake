"use client";

/**
 * The platform as applications: each project is an app, each of its
 * environments a place to open, each bound service a card inside it.
 *
 * One service list and one overview read (the same read an environment
 * dashboard makes, over every bound service at once) are enough to draw
 * every app, environment and service with its own traffic. Nothing here
 * adds a number the API did not send: an environment whose services sent
 * no traces has `requests: null`, not zero.
 */

import { useEffect, useMemo, useState } from "react";

import {
  MAX_OVERVIEW_BINDINGS,
  answered,
  fetchOverview,
  overviewPath,
  type Overview,
  type OverviewRange,
} from "@/lib/dashboards";
import type { ServiceHealthRow, ServiceHealthStatus } from "@/lib/serviceHealth";
import { boundIds } from "@/lib/signals";
import { useResource } from "@/lib/useResource";

/**
 * Worst first, for the one state an app or environment wears. A service
 * nobody configured is not a problem the place has: it shows in the health
 * bar, but it does not turn a running environment grey.
 */
const SEVERITY: ServiceHealthStatus[] = [
  "critical",
  "degraded",
  "stale",
  "unknown",
  "healthy",
  "not_configured",
];

export interface PlatformEnv {
  environmentId: string;
  key: string;
  projectId: string;
  projectKey: string;
  rows: ServiceHealthRow[];
  bindingIds: string[];
  tally: Map<ServiceHealthStatus, number>;
  worst: ServiceHealthStatus | null;
}

export interface PlatformApp {
  projectId: string;
  projectKey: string;
  name: string;
  envs: PlatformEnv[];
  services: number;
  /** Every service of the app by health, across its environments. */
  tally: Map<ServiceHealthStatus, number>;
  worst: ServiceHealthStatus | null;
}

/** prod first, then staging, test, dev; anything else after, by name. */
const ENV_RANK = [
  /^prod/,
  /^(live|main)$/,
  /^(stag|stage|uat|preprod)/,
  /^(test|qa)/,
  /^(dev|develop)/,
];
export function envRank(key: string): number {
  const i = ENV_RANK.findIndex((re) => re.test(key.toLowerCase()));
  return i < 0 ? ENV_RANK.length : i;
}

/** What kind of place an environment is, for its colour and label. */
export type EnvKind = "prod" | "staging" | "test" | "dev" | "other";
export function envKind(key: string): EnvKind {
  const k = key.toLowerCase();
  if (/^(prod|live|main)/.test(k)) return "prod";
  if (/^(stag|stage|uat|preprod)/.test(k)) return "staging";
  if (/^(test|qa)/.test(k)) return "test";
  if (/^(dev|develop)/.test(k)) return "dev";
  return "other";
}

function worstOf(statuses: Iterable<ServiceHealthStatus>): ServiceHealthStatus | null {
  let best: ServiceHealthStatus | null = null;
  for (const s of statuses) {
    const rank = SEVERITY.indexOf(s);
    if (best === null || (rank >= 0 && rank < SEVERITY.indexOf(best))) best = s;
  }
  return best;
}

export function groupApps(
  rows: ServiceHealthRow[],
  names: Map<string, string> = new Map(),
): PlatformApp[] {
  const apps = new Map<string, PlatformApp>();
  const envs = new Map<string, PlatformEnv>();
  for (const row of rows) {
    let app = apps.get(row.project_id);
    if (!app) {
      app = {
        projectId: row.project_id,
        projectKey: row.project_key,
        name: names.get(row.project_id) ?? row.project_key,
        envs: [],
        services: 0,
        tally: new Map(),
        worst: null,
      };
      apps.set(row.project_id, app);
    }
    let env = envs.get(row.environment_id);
    if (!env) {
      env = {
        environmentId: row.environment_id,
        key: row.environment_key,
        projectId: row.project_id,
        projectKey: row.project_key,
        rows: [],
        bindingIds: [],
        tally: new Map(),
        worst: null,
      };
      envs.set(row.environment_id, env);
      app.envs.push(env);
    }
    env.rows.push(row);
    if (row.binding) env.bindingIds.push(row.binding.id);
    env.tally.set(row.health.status, (env.tally.get(row.health.status) ?? 0) + 1);
    app.tally.set(row.health.status, (app.tally.get(row.health.status) ?? 0) + 1);
    app.services += 1;
  }
  for (const app of apps.values()) {
    for (const env of app.envs) env.worst = worstOf(env.tally.keys());
    app.envs.sort((a, b) => envRank(a.key) - envRank(b.key) || a.key.localeCompare(b.key));
    app.worst = worstOf(app.envs.flatMap((e) => (e.worst ? [e.worst] : [])));
  }
  return [...apps.values()].sort((a, b) => a.name.localeCompare(b.name));
}

/** A set of services' traffic, summed per bucket, from one overview read. */
export interface Pulse {
  requests: number | null;
  errors: number | null;
  worstP95: number | null;
  perBucket: number[];
}

export function pulseOf(overview: Overview | null, bindingIds: string[]): Pulse | null {
  const traffic = answered(overview?.traffic);
  if (!overview || !traffic) return null;
  const ids = new Set(bindingIds);
  const own = traffic.filter((x) => ids.has(x.binding_id));
  if (own.length === 0) return { requests: null, errors: null, worstP95: null, perBucket: [] };
  const perBucket = overview.buckets.map((_, i) => own.reduce((a, s) => a + (s.buckets[i]?.requests ?? 0), 0));
  const p95s = own.flatMap((s) => s.buckets.map((b) => b.p95_ms)).filter((v): v is number => v !== null);
  return {
    requests: perBucket.reduce((a, b) => a + b, 0),
    errors: own.reduce((a, s) => a + s.buckets.reduce((b, y) => b + y.errors, 0), 0),
    worstP95: p95s.length ? Math.max(...p95s) : null,
    perBucket,
  };
}

/** Project display names, keyed by id; keys stand in until they arrive. */
export function useProjectNames(): Map<string, string> {
  const projects = useResource<{ projects: { id: string; display_name: string }[] }>(
    "/v1/projects?lifecycle=active&limit=100",
  );
  return useMemo(
    () => new Map((projects.data?.projects ?? []).map((p) => [p.id, p.display_name])),
    [projects.data],
  );
}

/**
 * One overview read over every bound service. More than the read takes
 * (32) leaves the rest without a pulse and says so through `truncated`.
 */
export function usePlatformOverview(
  rows: ServiceHealthRow[] | null,
  range: OverviewRange = "24h",
): { overview: Overview | null; failed: boolean; truncated: boolean } {
  const ids = useMemo(() => boundIds(rows ?? []), [rows]);
  const path = useMemo(() => overviewPath(ids, range), [ids, range]);
  const [state, setState] = useState<{ path: string | null; overview: Overview | null; failed: boolean }>({
    path: null,
    overview: null,
    failed: false,
  });
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    fetchOverview(path, controller.signal)
      .then((overview) => setState({ path, overview, failed: false }))
      .catch(() => {
        if (!controller.signal.aborted) setState({ path, overview: null, failed: true });
      });
    return () => controller.abort();
  }, [path]);
  const current = state.path === path;
  return {
    overview: current ? state.overview : null,
    failed: current && state.failed,
    truncated: ids.length > MAX_OVERVIEW_BINDINGS,
  };
}
