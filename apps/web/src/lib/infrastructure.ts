/**
 * Typed infrastructure surface (ADR-0032).
 *
 * The API reads everything through the telemetry broker and serves one
 * snapshot per cluster; this module only reshapes it for the screen. It
 * never decides a number: "now" values, requests and limits are the API's,
 * and a missing value stays missing (null), never zero.
 */

import { apiGet } from "@/lib/api";

export type SeriesValue = number | null;

export interface InfraCluster {
  id: string;
  ref: string;
  name: string;
  metrics_configured: boolean;
  nodes: number;
}

export interface NodeSnapshot {
  name: string;
  kubelet_version: string | null;
  health: string;
  cores: number | null;
  cpu: { now: number | null; series: SeriesValue[]; allocatable: number | null; requested: number | null };
  memory: { now: number | null; series: SeriesValue[]; allocatable: number | null; requested: number | null };
  disk: { now: number | null; series: SeriesValue[]; size_bytes: number | null; growth_per_day: number | null };
  load5: { now: number | null; series: SeriesValue[] };
  pods: { running: number | null; allocatable: number | null };
}

export interface NamespaceGroup {
  kind: "project" | "kubernetes" | "monitoring" | "drake" | "unclaimed";
  key: string;
  name: string;
}

export interface NamespaceSnapshot {
  namespace: string;
  node: string;
  group: NamespaceGroup;
  project: {
    project_id: string;
    project_key: string;
    project_name: string;
    environment_id: string;
    environment_key: string;
  } | null;
  cpu: { now: number | null; series: SeriesValue[] };
  memory: { now: number | null; series: SeriesValue[] };
  cpu_requested: number | null;
  memory_requested: number | null;
  cpu_limit: number | null;
  memory_limit: number | null;
  pods: number | null;
  restarts_24h: number | null;
}

export interface InfraSnapshot {
  cluster: { id: string; ref: string; name: string };
  state: "ok" | "partial" | "denied" | "not_configured" | "unavailable";
  built_at: string;
  node_times?: number[];
  node_step_seconds?: number;
  times: number[];
  step_seconds: number;
  nodes: NodeSnapshot[];
  namespaces: NamespaceSnapshot[];
  missing: string[];
  age_seconds?: number;
  refreshing?: boolean;
}

export interface PodSnapshot {
  pod: string;
  node: string | null;
  cpu: { now: number | null; series: SeriesValue[] };
  memory: number | null;
  cpu_requested: number | null;
  cpu_limit: number | null;
  memory_limit: number | null;
  restarts_24h: number | null;
}

export interface NamespacePods {
  namespace: string;
  times: number[];
  step_seconds: number;
  pods: PodSnapshot[];
  state: "ok" | "partial";
  missing: string[];
}

export async function fetchClusters(): Promise<InfraCluster[]> {
  return (await apiGet<{ clusters: InfraCluster[] }>("/v1/infrastructure/clusters")).clusters;
}

export function fetchSnapshot(clusterId: string, signal?: AbortSignal): Promise<InfraSnapshot> {
  return apiGet<InfraSnapshot>(`/v1/infrastructure/clusters/${encodeURIComponent(clusterId)}`, signal);
}

const POD_CACHE_MS = 60_000;
const podCache = new Map<string, { at: number; value: Promise<NamespacePods> }>();

/**
 * A namespace's pods, remembered for a minute. Opening a project asks for
 * its environments' pods in the background (`prefetchNamespacePods`), so
 * the click on an environment usually finds them already here.
 */
export function fetchNamespacePods(clusterId: string, namespace: string): Promise<NamespacePods> {
  const key = `${clusterId}/${namespace}`;
  const hit = podCache.get(key);
  if (hit && Date.now() - hit.at < POD_CACHE_MS) return hit.value;
  const value = apiGet<NamespacePods>(
    `/v1/infrastructure/clusters/${encodeURIComponent(clusterId)}/namespaces/${encodeURIComponent(namespace)}`,
  );
  podCache.set(key, { at: Date.now(), value });
  value.catch(() => podCache.delete(key));
  return value;
}

export function prefetchNamespacePods(clusterId: string, namespaces: string[]): void {
  for (const ns of namespaces.slice(0, 6)) void fetchNamespacePods(clusterId, ns).catch(() => undefined);
}

/** Tests only. */
export function clearPodCache(): void {
  podCache.clear();
}

export const GiB = 1024 ** 3;

/** Sum two aligned series; a gap stays a gap only where both are gaps. */
export function addSeries(a: SeriesValue[], b: SeriesValue[]): SeriesValue[] {
  return a.map((x, i) => {
    const y = b[i] ?? null;
    if (x === null) return y;
    if (y === null) return x;
    return x + y;
  });
}

export function lastReal(series: SeriesValue[]): number | null {
  for (let i = series.length - 1; i >= 0; i--) if (series[i] !== null) return series[i];
  return null;
}

export function peakOf(series: SeriesValue[]): { index: number; value: number } | null {
  let best: { index: number; value: number } | null = null;
  series.forEach((v, i) => {
    if (v !== null && (best === null || v > best.value)) best = { index: i, value: v };
  });
  return best;
}

/** How a use compares with what was asked for. */
export type UseVerdict = "over" | "fits" | "idle" | "unknown";
export function requestVerdict(used: number | null, requested: number | null): UseVerdict {
  if (used === null || requested === null || requested <= 0) return "unknown";
  const r = used / requested;
  if (r > 1.05) return "over";
  if (r < 0.4) return "idle";
  return "fits";
}

export type LimitVerdict = "crit" | "near" | "ok" | "unknown";
export function limitVerdict(used: number | null, limit: number | null): LimitVerdict {
  if (used === null || limit === null || limit <= 0) return "unknown";
  const r = used / limit;
  if (r >= 0.9) return "crit";
  if (r >= 0.8) return "near";
  return "ok";
}
