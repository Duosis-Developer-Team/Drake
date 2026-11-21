"use client";

/**
 * The service map: who calls whom, and how much.
 *
 * Each service is a ring. The ring is split by response class (2xx, 4xx,
 * 5xx); inside it, the request curve of the window and the average rate per
 * minute. Edges are calls: width is volume, the moving dashes run faster the
 * more traffic an edge carries, and a call whose p95 is a second or more is
 * drawn in gold — the brand's one colour for "the slow path".
 *
 * Built for use, not only for looking at:
 * - every service is a focusable control (Tab, Enter) with a full spoken
 *   label; hover and focus both light up its neighbourhood;
 * - every edge is also listed in a table under the drawing;
 * - nothing moves under `prefers-reduced-motion`.
 */

import { useCallback, useId, useLayoutEffect, useMemo, useRef, useState } from "react";

import { SLOW_MS, type GraphEdge, type GraphNode } from "@/lib/dashboards";
import { useFormat, useT } from "@/lib/i18n";
import { flowSeconds, layoutServiceMap, perMinute, type MapGraph, type PlacedNode } from "@/lib/serviceMap";

const OK = "var(--status-success)";
const CLIENT = "var(--series-5)";
const SERVER = "var(--status-critical)";
const SLOW = "var(--gold)";
const QUERY = "var(--series-3)";
const EDGE = "var(--text-muted)";

function useMeasuredWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setWidth(el.clientWidth);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      const w = Math.round(entry.contentRect.width);
      if (w > 0) setWidth(w);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

function smooth(points: [number, number][]): string {
  if (points.length === 0) return "";
  let d = `M${points[0][0].toFixed(1)},${points[0][1].toFixed(1)}`;
  for (let i = 1; i < points.length; i++) {
    const [x0, y0] = points[i - 1];
    const [x1, y1] = points[i];
    const cx = (x0 + x1) / 2;
    d += `C${cx.toFixed(1)},${y0.toFixed(1)} ${cx.toFixed(1)},${y1.toFixed(1)} ${x1.toFixed(1)},${y1.toFixed(1)}`;
  }
  return d;
}

function ringSegments(node: GraphNode): [number, string][] {
  if (node.kind === "users") return [[1, EDGE]];
  if (node.kind === "database") return [[1, QUERY]];
  if (!node.codes) return [[1, "var(--brand)"]];
  const total = Object.values(node.codes).reduce((a, b) => a + b, 0) || 1;
  let c4 = 0;
  let c5 = 0;
  for (const [code, v] of Object.entries(node.codes)) {
    if (code.startsWith("4")) c4 += v;
    else if (code.startsWith("5")) c5 += v;
  }
  return (
    [
      [(total - c4 - c5) / total, OK],
      [c4 / total, CLIENT],
      [c5 / total, SERVER],
    ] as [number, string][]
  ).filter(([f]) => f > 0);
}

interface Geometry {
  edge: GraphEdge;
  d: string;
  lx: number;
  ly: number;
  width: number;
  slow: boolean;
  label: string;
  seconds: number;
}

export function ServiceMap({
  graph,
  bucketSeconds,
  focusId,
  onOpen,
  compact = false,
  ariaLabel,
}: {
  graph: MapGraph;
  bucketSeconds: number;
  /** A service drawn as the subject of the map (the service page). */
  focusId?: string;
  /** Opening a service; absent means services are not interactive. */
  onOpen?: (node: GraphNode) => void;
  compact?: boolean;
  ariaLabel?: string;
}) {
  const t = useT("serviceMap");
  const fmt = useFormat();
  const uid = useId().replace(/:/g, "");
  const [wrapRef, measured] = useMeasuredWidth<HTMLDivElement>();
  const width = Math.max(compact ? 520 : 680, measured || 1000);
  const layout = useMemo(() => layoutServiceMap(graph, width, { compact }), [graph, width, compact]);
  const [active, setActive] = useState<string | null>(null);
  const [tip, setTip] = useState<{ x: number; y: number; lines: string[] } | null>(null);

  const n = useCallback((v: number, digits = 0) => fmt.number(v, { maximumFractionDigits: digits }), [fmt]);
  const nameOf = useCallback(
    (id: string) => {
      const node = graph.nodes.find((x) => x.id === id);
      if (!node) return id;
      return node.kind === "users" ? t("users") : node.label;
    },
    [graph.nodes, t],
  );

  const maxCount = Math.max(1, ...graph.edges.map((e) => e.count));
  const geometry: Geometry[] = graph.edges.flatMap((edge) => {
    const a = layout.placed.get(edge.from);
    const b = layout.placed.get(edge.to);
    if (!a || !b) return [];
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const len = Math.hypot(dx, dy) || 1;
    const ux = dx / len;
    const uy = dy / len;
    const ring = compact ? 6 : 8;
    let sx = a.x + ux * (a.r + ring + 4);
    let sy = a.y + uy * (a.r + ring + 4);
    let ex = b.x - ux * (b.r + ring + 8);
    let ey = b.y - uy * (b.r + ring + 8);
    const skip = Math.max(0, Math.abs(b.column - a.column) - 1);
    let mx = (sx + ex) / 2;
    let my = (sy + ey) / 2;
    if (skip > 0) {
      // A call that skips columns must not run through the nodes in between:
      // route it through the nearest free band (between two nodes' labels,
      // or above / below the column), then solve the control point so the
      // curve's midpoint sits in that band.
      const lo = Math.min(a.column, b.column);
      const hi = Math.max(a.column, b.column);
      const between = [...layout.placed.values()]
        .filter((p) => p.column > lo && p.column < hi)
        .sort((p, q) => p.y - q.y);
      const bands: number[] = [];
      if (between.length) {
        const first = between[0];
        const last = between[between.length - 1];
        bands.push(first.y - first.r - 22, last.y + last.r + (compact ? 52 : 62));
        for (let k = 1; k < between.length; k++) {
          const top = between[k - 1].y + between[k - 1].r + (compact ? 46 : 56);
          const bottom = between[k].y - between[k].r - 18;
          if (bottom > top) bands.push((top + bottom) / 2);
        }
      }
      const target = bands.length
        ? bands.reduce((best, y) => (Math.abs(y - my) < Math.abs(best - my) ? y : best))
        : my - 64 * skip;
      my = 2 * target - (sy + ey) / 2;
    } else if (dy !== 0) {
      mx -= uy * 18;
      my += ux * 18;
    }
    // Leave and enter each ring along the curve's own tangent, not the
    // straight line, so a bent edge does not cut through its own node.
    if (skip > 0) {
      const la = Math.hypot(mx - a.x, my - a.y) || 1;
      sx = a.x + ((mx - a.x) / la) * (a.r + ring + 4);
      sy = a.y + ((my - a.y) / la) * (a.r + ring + 4);
      const lb = Math.hypot(b.x - mx, b.y - my) || 1;
      ex = b.x - ((b.x - mx) / lb) * (b.r + ring + 8);
      ey = b.y - ((b.y - my) / lb) * (b.r + ring + 8);
    }
    const slow = edge.kind === "call" && (edge.p95 ?? 0) >= SLOW_MS;
    const at = 0.5;
    const lx = (1 - at) ** 2 * sx + 2 * (1 - at) * at * mx + at * at * ex;
    const ly = (1 - at) ** 2 * sy + 2 * (1 - at) * at * my + at * at * ey;
    const label =
      edge.kind === "query"
        ? t("queries", { count: n(edge.count) })
        : slow
          ? `${n(edge.count)} · ${fmt.latency(edge.p95)}`
          : n(edge.count);
    return [
      {
        edge,
        d: `M${sx.toFixed(1)},${sy.toFixed(1)}Q${mx.toFixed(1)},${my.toFixed(1)} ${ex.toFixed(1)},${ey.toFixed(1)}`,
        lx,
        ly,
        width: 1.25 + 5.5 * (Math.log10(edge.count + 1) / Math.log10(maxCount + 1)),
        slow,
        label,
        seconds: flowSeconds(edge.count, maxCount),
      },
    ];
  });

  const touches = (id: string) =>
    active === null ||
    active === id ||
    graph.edges.some((e) => (e.from === active && e.to === id) || (e.to === active && e.from === id));

  const nodeSummary = (p: PlacedNode) => {
    const node = p.node;
    if (node.kind === "service" && !node.longLived) {
      return t("nodeLabel", {
        name: node.label,
        rate: n(perMinute(node, bucketSeconds), 1),
        p95: fmt.latency(node.p95),
      });
    }
    return t("nodeLabelNoLatency", {
      name: node.kind === "users" ? t("users") : node.label,
      total: n(node.requests),
      unit: node.kind === "database" ? t("queriesUnit") : t("requestsUnit"),
    });
  };

  const nodeTip = (p: PlacedNode): string[] => {
    const node = p.node;
    const lines = [node.kind === "users" ? t("users") : node.label];
    lines.push(`${n(node.requests)} ${node.kind === "database" ? t("queriesUnit") : t("requestsUnit")}`);
    if (node.longLived) lines.push(t("streaming"));
    else if (node.p95 !== null) lines.push(t("p95", { value: fmt.latency(node.p95) }));
    if (node.codes) {
      for (const [code, v] of Object.entries(node.codes).sort((a, b) => b[1] - a[1]).slice(0, 4)) {
        lines.push(`${code} · ${n(v)}`);
      }
    }
    return lines;
  };

  const showNodeTip = (p: PlacedNode) =>
    setTip({
      x: p.x + p.r + 200 > layout.width ? p.x - p.r - 200 : p.x + p.r + 18,
      y: Math.max(8, p.y - p.r),
      lines: nodeTip(p),
    });

  return (
    <div ref={wrapRef} className="relative w-full" data-testid="service-map">
      <svg
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        width="100%"
        height={layout.height}
        role="group"
        aria-label={ariaLabel ?? t("aria", { nodes: graph.nodes.length, edges: graph.edges.length })}
        className="block overflow-visible"
      >
        <defs>
          {[
            ["n", EDGE],
            ["s", SLOW],
            ["q", QUERY],
          ].map(([key, colour]) => (
            <marker
              key={key}
              id={`${uid}-arrow-${key}`}
              viewBox="0 0 10 10"
              refX="7"
              refY="5"
              markerWidth="9"
              markerHeight="9"
              markerUnits="userSpaceOnUse"
              orient="auto-start-reverse"
            >
              <path d="M0,0L10,5L0,10z" style={{ fill: colour }} />
            </marker>
          ))}
        </defs>

        {geometry.map((g, i) => {
          const colour = g.slow ? SLOW : g.edge.kind === "query" ? QUERY : EDGE;
          const key = g.slow ? "s" : g.edge.kind === "query" ? "q" : "n";
          const lit = active === null || g.edge.from === active || g.edge.to === active;
          return (
            <g
              key={`${g.edge.from}-${g.edge.to}-${i}`}
              style={{ opacity: lit ? 1 : 0.18, transition: "opacity 160ms" }}
              onMouseEnter={() =>
                setTip({
                  x: g.lx + 12,
                  y: g.ly + 10,
                  lines: [
                    `${nameOf(g.edge.from)} → ${nameOf(g.edge.to)}`,
                    `${n(g.edge.count)} ${g.edge.kind === "query" ? t("queriesUnit") : t("requestsUnit")}`,
                    ...(g.edge.p95 !== null ? [t("p95", { value: fmt.latency(g.edge.p95) })] : []),
                  ],
                })
              }
              onMouseLeave={() => setTip(null)}
            >
              <path
                d={g.d}
                fill="none"
                strokeWidth={g.width}
                strokeLinecap="round"
                markerEnd={`url(#${uid}-arrow-${key})`}
                style={{ stroke: colour, strokeOpacity: g.slow ? 0.85 : 0.38 }}
              />
              <path
                d={g.d}
                fill="none"
                strokeWidth={Math.max(1.4, g.width * 0.55)}
                strokeLinecap="round"
                strokeDasharray="2 14"
                className="motion-safe:animate-[trace-flow_var(--flow)_linear_infinite]"
                style={{ stroke: colour, ["--flow" as string]: `${g.seconds}s` }}
              />
              <path d={g.d} fill="none" strokeWidth={14} stroke="transparent" />
            </g>
          );
        })}


        {/* Labels last, so no edge ever runs across another edge's count. */}
        {geometry.map((g, i) => {
          const lit = active === null || g.edge.from === active || g.edge.to === active;
          const labelWidth = g.label.length * 7.1 + 18;
          return (
            <g key={`label-${i}`} style={{ opacity: lit ? 1 : 0.18, transition: "opacity 160ms" }} aria-hidden>
                <rect
                  x={g.lx - labelWidth / 2}
                  y={g.ly - 10}
                  width={labelWidth}
                  height={20}
                  rx={10}
                  style={{ fill: "var(--surface-1)", stroke: g.slow ? SLOW : "var(--border-subtle)" }}
                />
                <text
                  x={g.lx}
                  y={g.ly + 3.8}
                  textAnchor="middle"
                  className="font-mono"
                  style={{ fontSize: 11, fill: g.slow ? SLOW : "var(--text-secondary)" }}
                >
                  {g.label}
                </text>
            </g>
          );
        })}

        {[...layout.placed.values()].map((p, i) => {
          const node = p.node;
          const ring = compact ? 5 : 7;
          const rr = p.r + ring;
          const circumference = 2 * Math.PI * rr;
          const segments = ringSegments(node);
          let offset = 0;
          const series = node.series.length > 1 ? node.series : [0, 0];
          const top = Math.max(...series, 1e-9);
          const ww = p.r * 1.5;
          const hh = p.r * 0.3;
          const x0 = -ww / 2;
          const y0 = p.r * 0.88;
          const wave = smooth(
            series.map((v, j): [number, number] => [x0 + (j / Math.max(1, series.length - 1)) * ww, y0 - (v / top) * hh]),
          );
          const isService = node.kind === "service";
          const interactive = isService && !!onOpen;
          const focus = focusId === node.id;
          const big =
            isService
              ? n(perMinute(node, bucketSeconds), 1)
              : fmt.number(node.requests, { notation: node.requests >= 10_000 ? "compact" : "standard", maximumFractionDigits: 1 });
          const unit = isService ? t("perMinute") : node.kind === "database" ? t("queriesUnit") : t("requestsUnit");
          const latency =
            node.kind === "users" ? "" : node.longLived ? t("streaming") : node.p95 === null ? "" : t("p95", { value: fmt.latency(node.p95) });
          const name = node.kind === "users" ? t("users") : node.label;
          return (
            <g
              key={node.id}
              transform={`translate(${p.x.toFixed(1)},${p.y.toFixed(1)})`}
              role={interactive ? "button" : "img"}
              tabIndex={interactive ? 0 : undefined}
              aria-label={interactive ? `${nodeSummary(p)}. ${t("open", { name })}` : nodeSummary(p)}
              data-testid={`service-map-node-${node.kind}`}
              className="outline-none [&:focus-visible>.focus-ring]:opacity-100"
              style={{
                cursor: interactive ? "pointer" : "default",
                opacity: touches(node.id) ? 1 : 0.22,
                transition: "opacity 160ms",
                animationDelay: `${i * 60}ms`,
              }}
              onMouseEnter={() => {
                setActive(node.id);
                showNodeTip(p);
              }}
              onMouseLeave={() => {
                setActive(null);
                setTip(null);
              }}
              onFocus={() => {
                setActive(node.id);
                showNodeTip(p);
              }}
              onBlur={() => {
                setActive(null);
                setTip(null);
              }}
              onClick={() => interactive && onOpen?.(node)}
              onKeyDown={(event) => {
                if (!interactive) return;
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  onOpen?.(node);
                }
              }}
            >
              <circle
                r={rr + 9}
                className="focus-ring opacity-0 transition-opacity"
                style={{ fill: "none", stroke: "var(--focus-ring)", strokeWidth: 2 }}
              />
              {focus ? <circle r={rr + 12} style={{ fill: "var(--brand-soft)" }} /> : null}
              <circle r={rr} style={{ fill: "none", stroke: "var(--surface-3)", strokeWidth: ring }} />
              {segments.map(([fraction, colour], j) => {
                const length = Math.max(0, circumference * fraction - (segments.length > 1 ? 3 : 0));
                const el = (
                  <circle
                    key={j}
                    r={rr}
                    transform="rotate(-90)"
                    strokeDasharray={`${length} ${circumference}`}
                    strokeDashoffset={-offset}
                    style={{ fill: "none", stroke: colour, strokeWidth: ring }}
                  />
                );
                offset += circumference * fraction;
                return el;
              })}
              <circle r={p.r} style={{ fill: "var(--surface-1)", stroke: "var(--border-subtle)" }} />
              <clipPath id={`${uid}-clip-${i}`}>
                <circle r={p.r - 1} />
              </clipPath>
              <g clipPath={`url(#${uid}-clip-${i})`}>
                <path
                  d={`${wave}L${(x0 + ww).toFixed(1)},${p.r}L${x0.toFixed(1)},${p.r}Z`}
                  style={{ fill: node.slow ? SLOW : "var(--brand)", fillOpacity: 0.16 }}
                />
                <path d={wave} fill="none" strokeWidth={1.3} style={{ stroke: node.slow ? SLOW : "var(--brand)", strokeOpacity: 0.7 }} />
              </g>
              <text
                y={compact ? -2 : -5}
                textAnchor="middle"
                className="font-[family-name:var(--font-display)]"
                style={{ fontSize: compact ? 15 : 19, fontWeight: 700, fill: "var(--text-primary)", letterSpacing: "-0.02em" }}
              >
                {big}
              </text>
              <text y={compact ? 10 : 11} textAnchor="middle" style={{ fontSize: compact ? 9.5 : 10.5, fill: "var(--text-muted)" }}>
                {unit}
              </text>
              <text
                y={rr + (compact ? 18 : 22)}
                textAnchor="middle"
                style={{ fontSize: compact ? 12 : 13, fontWeight: 600, fill: "var(--text-primary)" }}
              >
                {name}
              </text>
              {latency ? (
                <text
                  y={rr + (compact ? 32 : 38)}
                  textAnchor="middle"
                  className="font-mono"
                  style={{ fontSize: compact ? 10.5 : 11.5, fill: node.slow ? SLOW : "var(--text-muted)", fontWeight: node.slow ? 600 : 400 }}
                >
                  {latency}
                </text>
              ) : null}
              {node.slow ? (
                <g transform={`translate(${(-rr * 0.72).toFixed(1)},${(-rr * 0.72).toFixed(1)})`} aria-hidden>
                  <circle r={9} style={{ fill: SLOW }} />
                  <path d="M0,-4.2V1.2M0,3.8V4" strokeWidth={2} strokeLinecap="round" style={{ stroke: "var(--canvas)" }} />
                </g>
              ) : null}
            </g>
          );
        })}
      </svg>

      {tip ? (
        <div
          role="presentation"
          className="pointer-events-none absolute z-10 min-w-36 rounded-control border border-border bg-surface-elevated px-3 py-2 text-caption shadow-overlay"
          style={{ left: Math.min(tip.x, layout.width - 180), top: tip.y }}
        >
          {tip.lines.map((line, i) => (
            <p key={i} className={i === 0 ? "font-semibold text-ink" : "font-mono text-[0.75rem] text-ink-secondary"}>
              {line}
            </p>
          ))}
        </div>
      ) : null}

      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-micro text-ink-muted">
        <LegendSwatch colour={OK} label={t("legend.ok")} />
        <LegendSwatch colour={CLIENT} label={t("legend.clientErrors")} />
        <LegendSwatch colour={SERVER} label={t("legend.serverErrors")} />
        <LegendLine colour={EDGE} thick label={t("legend.width")} />
        <LegendLine colour={SLOW} label={t("legend.slow")} />
        <LegendLine colour={QUERY} label={t("legend.query")} />
        <span>{t("legend.motion")}</span>
      </div>

      <details className="mt-3 text-caption">
        <summary className="cursor-pointer text-brand">{t("table.show", { count: graph.edges.length })}</summary>
        <div className="mt-2 overflow-x-auto">
          <table className="w-full min-w-[28rem] text-left">
            <thead className="text-micro text-ink-muted">
              <tr>
                <th className="py-1.5 pr-3 font-medium">{t("table.from")}</th>
                <th className="py-1.5 pr-3 font-medium">{t("table.to")}</th>
                <th className="py-1.5 pr-3 text-right font-medium">{t("table.count")}</th>
                <th className="py-1.5 text-right font-medium">{t("table.p95")}</th>
              </tr>
            </thead>
            <tbody>
              {[...graph.edges]
                .sort((a, b) => b.count - a.count)
                .map((e, i) => (
                  <tr key={i} className="border-t border-border">
                    <td className="py-1.5 pr-3">{nameOf(e.from)}</td>
                    <td className="py-1.5 pr-3">{nameOf(e.to)}</td>
                    <td className="py-1.5 pr-3 text-right font-mono">{n(e.count)}</td>
                    <td
                      className={`py-1.5 text-right font-mono ${(e.p95 ?? 0) >= SLOW_MS && e.kind === "call" ? "font-semibold text-gold" : ""}`}
                    >
                      {fmt.latency(e.p95)}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}

function LegendSwatch({ colour, label }: { colour: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span aria-hidden className="h-2 w-2 rounded-full" style={{ background: colour }} />
      {label}
    </span>
  );
}

function LegendLine({ colour, label, thick = false }: { colour: string; label: string; thick?: boolean }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span aria-hidden className="w-4 rounded-full" style={{ background: colour, height: thick ? 4 : 2 }} />
      {label}
    </span>
  );
}
