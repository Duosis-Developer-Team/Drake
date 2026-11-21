/**
 * The service map: data and layout.
 *
 * The graph itself (`trafficGraph`) is built from the environment overview
 * read (ADR-0033): one node per service that served traffic, one per
 * database, a "users" node for traffic that entered from outside, and an
 * edge per measured call. This module adds what the drawing needs:
 *
 * - `layoutServiceMap`: columns by call depth, rows ordered to keep edges
 *   from crossing, spacing that grows with the most crowded column so nodes
 *   never overlap.
 * - `neighbourhood`: one service, what it calls and what calls it.
 * - `useEnvironmentTraffic`: the read itself, polled, with honest states.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  MAX_OVERVIEW_BINDINGS,
  fetchOverview,
  overviewPath,
  serviceLines,
  trafficGraph,
  type GraphEdge,
  type GraphNode,
  type Overview,
  type OverviewRange,
  type ServiceLine,
} from "@/lib/dashboards";
import { fetchServiceHealthList, type ServiceHealthRow } from "@/lib/serviceHealth";

export interface MapGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface PlacedNode {
  node: GraphNode;
  x: number;
  y: number;
  r: number;
  column: number;
}

export interface MapLayout {
  width: number;
  height: number;
  placed: Map<string, PlacedNode>;
}

/** Requests per minute, averaged over the window the series covers. */
export function perMinute(node: GraphNode, bucketSeconds: number): number {
  const series = node.series;
  if (series.length === 0) return 0;
  const total = series.reduce((a, b) => a + b, 0);
  return total / Math.max(1, bucketSeconds / 60) / series.length;
}

/** One service and its direct callers and callees (plus the users entry). */
export function neighbourhood(graph: MapGraph, id: string): MapGraph {
  const keep = new Set<string>([id]);
  for (const e of graph.edges) {
    if (e.from === id) keep.add(e.to);
    if (e.to === id) keep.add(e.from);
  }
  return {
    nodes: graph.nodes.filter((n) => keep.has(n.id)),
    edges: graph.edges.filter((e) => keep.has(e.from) && keep.has(e.to) && (e.from === id || e.to === id)),
  };
}

/**
 * Columns by depth: users first, services by how far down the call chain
 * they sit, databases last. Inside a column, nodes are ordered by the mean
 * row of what feeds them (two barycentre passes), which removes most
 * crossings on graphs of this size without a layout library.
 */
export function layoutServiceMap(
  graph: MapGraph,
  width: number,
  { compact = false }: { compact?: boolean } = {},
): MapLayout {
  const { nodes, edges } = graph;
  const depth = new Map<string, number>();
  for (const n of nodes) depth.set(n.id, n.kind === "users" ? 0 : 1);
  // Longest path from users; bounded by node count so a cycle cannot spin.
  for (let pass = 0; pass < nodes.length; pass++) {
    let changed = false;
    for (const e of edges) {
      if (e.kind !== "call") continue;
      const next = (depth.get(e.from) ?? 1) + 1;
      if (next > (depth.get(e.to) ?? 1) && next <= nodes.length) {
        depth.set(e.to, next);
        changed = true;
      }
    }
    if (!changed) break;
  }
  const serviceMax = Math.max(
    1,
    ...nodes.filter((n) => n.kind === "service").map((n) => depth.get(n.id) ?? 1),
  );
  for (const n of nodes) if (n.kind === "database") depth.set(n.id, serviceMax + 1);
  const hasUsers = nodes.some((n) => n.kind === "users");
  const firstColumn = hasUsers ? 0 : Math.min(...nodes.map((n) => depth.get(n.id) ?? 1));
  const lastColumn = Math.max(...nodes.map((n) => depth.get(n.id) ?? 1));

  const columns = new Map<number, GraphNode[]>();
  for (const n of nodes) {
    const c = depth.get(n.id) ?? 1;
    columns.set(c, [...(columns.get(c) ?? []), n]);
  }

  const maxRequests = Math.max(1, ...nodes.map((n) => n.requests));
  const [rMin, rMax] = compact ? [20, 30] : [28, 44];
  const radius = (n: GraphNode) =>
    rMin + (rMax - rMin) * (Math.log10(n.requests + 1) / Math.log10(maxRequests + 1));

  // Row order: start in input order, then sort each column by the mean index
  // of its upstream neighbours, twice.
  const order = new Map<string, number>();
  for (const list of columns.values()) list.forEach((n, i) => order.set(n.id, i / Math.max(1, list.length)));
  const sortedColumns = [...columns.keys()].sort((a, b) => a - b);
  for (let pass = 0; pass < 2; pass++) {
    for (const c of sortedColumns) {
      const list = columns.get(c) ?? [];
      const score = (n: GraphNode) => {
        const ups = edges.filter((e) => e.to === n.id).map((e) => order.get(e.from) ?? 0.5);
        return ups.length ? ups.reduce((a, b) => a + b, 0) / ups.length : (order.get(n.id) ?? 0.5);
      };
      list.sort((a, b) => score(a) - score(b));
      list.forEach((n, i) => order.set(n.id, (i + 0.5) / list.length));
    }
  }

  const crowded = Math.max(1, ...[...columns.values()].map((l) => l.length));
  // A call that skips a column is routed between that column's nodes, so
  // leave it a lane wide enough for the line and its count.
  const skips = edges.some((e) => Math.abs((depth.get(e.from) ?? 1) - (depth.get(e.to) ?? 1)) > 1);
  const rowGap = (compact ? 2 * rMax + 56 : 2 * rMax + 74) + (skips ? 34 : 0);
  const top = compact ? 34 : 44;
  const bottom = compact ? 50 : 62;
  const height = Math.max(compact ? 260 : 420, top + bottom + crowded * rowGap);
  const sidePad = rMax + (compact ? 30 : 44);
  const span = Math.max(1, lastColumn - firstColumn);

  const placed = new Map<string, PlacedNode>();
  for (const [c, list] of columns) {
    const x =
      lastColumn === firstColumn
        ? width / 2
        : sidePad + ((c - firstColumn) / span) * (width - 2 * sidePad);
    // Centre a short column instead of stretching it over the full height.
    const used = list.length * rowGap;
    const offset = top + (height - top - bottom - used) / 2;
    list.forEach((n, i) => {
      placed.set(n.id, { node: n, x, y: offset + (i + 0.5) * rowGap, r: radius(n), column: c });
    });
  }
  return { width, height, placed };
}

/** Flow speed in seconds: more traffic, faster flow (0.8 s – 4 s). */
export function flowSeconds(count: number, maxCount: number): number {
  const share = Math.log10(count + 1) / Math.log10(Math.max(2, maxCount) + 1);
  return Math.round((4 - 3.2 * share) * 10) / 10;
}

export type TrafficState =
  | { kind: "loading" }
  | { kind: "error" }
  | { kind: "no_bindings" }
  | { kind: "ready"; overview: Overview; lines: ServiceLine[]; graph: MapGraph; truncated: boolean };

const POLL_MS = 60_000;

/**
 * The environment's traffic graph, read the same way the dashboard reads it:
 * the bound services of the environment, then one overview read across
 * their bindings (at most `MAX_OVERVIEW_BINDINGS`). Polled every minute.
 */
export function useEnvironmentTraffic(environmentId: string | null, range: OverviewRange): {
  state: TrafficState;
  rows: ServiceHealthRow[] | null;
  reload: () => void;
} {
  const [rows, setRows] = useState<ServiceHealthRow[] | null>(null);
  const [rowsFailed, setRowsFailed] = useState(false);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [overviewFailed, setOverviewFailed] = useState(false);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (!environmentId) return;
    let cancelled = false;
    setRows(null);
    setRowsFailed(false);
    fetchServiceHealthList({ environmentId })
      .then((list) => !cancelled && setRows(list.items))
      .catch(() => !cancelled && setRowsFailed(true));
    return () => {
      cancelled = true;
    };
  }, [environmentId, nonce]);

  const bindingIds = useMemo(
    () => (rows ?? []).flatMap((r) => (r.binding ? [r.binding.id] : [])),
    [rows],
  );
  const path = useMemo(() => overviewPath(bindingIds, range), [bindingIds, range]);

  useEffect(() => {
    if (!path) return;
    let cancelled = false;
    const controller = new AbortController();
    const load = () =>
      fetchOverview(path, controller.signal)
        .then((o) => {
          if (cancelled) return;
          setOverview(o);
          setOverviewFailed(false);
        })
        .catch(() => !cancelled && setOverviewFailed(true));
    setOverview(null);
    setOverviewFailed(false);
    load();
    const id = setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      controller.abort();
      clearInterval(id);
    };
  }, [path, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  const state = useMemo<TrafficState>(() => {
    if (!environmentId) return { kind: "loading" };
    if (rowsFailed) return { kind: "error" };
    if (rows === null) return { kind: "loading" };
    if (bindingIds.length === 0) return { kind: "no_bindings" };
    if (overviewFailed && !overview) return { kind: "error" };
    if (!overview) return { kind: "loading" };
    const lines = serviceLines(rows, overview);
    return {
      kind: "ready",
      overview,
      lines,
      graph: trafficGraph(lines, overview),
      truncated: bindingIds.length > MAX_OVERVIEW_BINDINGS,
    };
  }, [environmentId, rows, rowsFailed, bindingIds, overview, overviewFailed]);

  return { state, rows, reload };
}

/**
 * Which environment a binding belongs to. The binding read does not carry
 * the environment id, so it is looked up from the services list the reader
 * can already see (one request, cached for the page's lifetime).
 */
export function useBindingEnvironment(bindingId: string): {
  environmentId: string | null;
  label: string | null;
  resolved: boolean;
} {
  const [found, setFound] = useState<{ environmentId: string | null; label: string | null; resolved: boolean }>({
    environmentId: null,
    label: null,
    resolved: false,
  });
  useEffect(() => {
    let cancelled = false;
    fetchServiceHealthList({})
      .then((page) => {
        if (cancelled) return;
        const row = page.items.find((r) => r.binding?.id === bindingId);
        setFound({
          environmentId: row?.environment_id ?? null,
          label: row ? row.display_name || row.service_key : null,
          resolved: true,
        });
      })
      .catch(() => !cancelled && setFound({ environmentId: null, label: null, resolved: true }));
    return () => {
      cancelled = true;
    };
  }, [bindingId]);
  return found;
}
