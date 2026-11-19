"use client";

/**
 * The dashboard's widgets (ADR-0033), drawn as in the approved v18 design.
 * Each takes what the overview read answered and draws it; none adds a
 * number the API did not send.
 */

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import { apiGet } from "@/lib/api";
import {
  SLOW_MS,
  answered,
  apdex,
  errorsPerBucket,
  logMakeup,
  requestsPerMinute,
  trafficGraph,
  worstP95,
  type Finding,
  type GraphNode,
  type Overview,
  type ServiceLine,
} from "@/lib/dashboards";
import { useFormat, useT } from "@/lib/i18n";
import type { NamespaceSnapshot } from "@/lib/infrastructure";
import { traceDetailPath, tracesPath, type TraceDetail, type TracePage } from "@/lib/traces";

import s from "./dashboards.module.css";

type T = ReturnType<typeof useT<"dashboards">>;
const PALETTE = ["var(--s1)", "var(--s4)", "var(--s3)", "var(--s2)", "var(--s6)", "var(--s5)"];
export const colourOf = (index: number) => PALETTE[index % PALETTE.length];

function useN() {
  const fmt = useFormat();
  return {
    n: (v: number | null | undefined, d = 0) =>
      v === null || v === undefined ? "—" : fmt.number(v, { maximumFractionDigits: d, minimumFractionDigits: d }),
    ms: (v: number | null | undefined) => {
      if (v === null || v === undefined) return "—";
      if (v >= 1000)
        return `${fmt.number(v / 1000, { maximumFractionDigits: 2, minimumFractionDigits: 2 })} ${fmt.tag.startsWith("tr") ? "sn" : "s"}`;
      return `${fmt.number(v, { maximumFractionDigits: v < 10 ? 1 : 0 })} ms`;
    },
    pct: (v: number | null | undefined, d = 0) => {
      if (v === null || v === undefined) return "—";
      const x = fmt.number(v * 100, { maximumFractionDigits: d });
      return fmt.tag.startsWith("tr") ? `%${x}` : `${x}%`;
    },
    hour: (t: number) =>
      new Date(t * 1000).toLocaleTimeString(fmt.tag, { hour: "2-digit", minute: "2-digit" }),
  };
}

function curve(points: [number, number][]): string {
  if (points.length < 2) return points.length ? `M${points[0][0]},${points[0][1]}` : "";
  let d = `M${points[0][0]},${points[0][1]}`;
  for (let i = 1; i < points.length; i++) {
    const [a, b] = points[i - 1];
    const [c, e] = points[i];
    const m = (a + c) / 2;
    d += `C${m},${b} ${m},${e} ${c},${e}`;
  }
  return d;
}

function useWidth<E extends HTMLElement>(): [React.RefObject<E | null>, number] {
  const ref = useRef<E | null>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

function HourTicks({ buckets, every = 3 }: { buckets: number[]; every?: number }) {
  const { hour } = useN();
  const n = buckets.length;
  return (
    <div className={s.ticks}>
      {buckets.map((t, i) => {
        const d = new Date(t * 1000);
        if (d.getMinutes() !== 0 || d.getHours() % every !== 0) return null;
        return (
          <span key={t} style={{ left: `${((i + 0.5) / n) * 100}%` }}>
            {hour(t)}
          </span>
        );
      })}
    </div>
  );
}

/* ================= overview ================= */

export function OverviewWidget({ overview, t }: { overview: Overview; t: T }) {
  const { n, ms } = useN();
  const traffic = answered(overview.traffic);
  const latency = answered(overview.latency);
  const total = traffic ? traffic.reduce((a, x) => a + x.buckets.reduce((b, y) => b + y.requests, 0), 0) : null;
  const errors = traffic ? traffic.reduce((a, x) => a + x.buckets.reduce((b, y) => b + y.errors, 0), 0) : null;
  const ap = apdex(latency);
  const rpm = requestsPerMinute(overview);
  const worst = worstP95(overview);
  const errs = errorsPerBucket(overview);
  const peak = Math.max(...rpm.map((v) => v ?? 0), 1e-9);
  const cls = (v: number | null) => (v === null ? s["c-none"] : v >= SLOW_MS ? s["c-slow"] : v >= 100 ? s["c-mid"] : s["c-fast"]);
  return (
    <>
      <div className={s.kpis}>
        <Kpi label={t("overview.requests")} value={n(total)} note={t("overview.inWindow")} />
        <Kpi label={t("overview.errors")} value={n(errors)} note={t("overview.errorsNote")} tone={errors ? "bad" : "ok"} />
        <Kpi label={t("overview.slow")} value={n(latency ? ap.tolerable + ap.slow : null)} note={t("overview.slowNote")} tone="warn" />
        <Kpi label={t("overview.apdex")} value={ap.score === null ? "—" : n(Math.floor(ap.score * 100) / 100, 2)} note={t("overview.apdexNote")} tone="ok" />
      </div>
      <div className={s.lanes}>
        <div className={s.lane}>
          <span className={s.laneLabel}>
            {t("overview.laneRequests")}
            <small>{t("overview.peak", { value: n(peak, 1) })}</small>
          </span>
          <div className={s.bars}>
            {rpm.map((v, i) => (
              <i key={i} title={`${v === null ? "—" : n(v, 1)}`} style={{ height: `${Math.max(4, ((v ?? 0) / peak) * 100)}%`, animationDelay: `${i * 12}ms` }} />
            ))}
          </div>
        </div>
        <div className={s.lane}>
          <span className={s.laneLabel}>
            {t("overview.laneSpeed")}
            <small>{t("overview.laneSpeedNote")}</small>
          </span>
          <div className={s.cells}>
            {worst.map((v, i) => (
              <i key={i} className={cls(v)} title={v === null ? t("overview.noRequests") : `p95 ${ms(v)}`} style={{ animationDelay: `${300 + i * 12}ms` }} />
            ))}
          </div>
        </div>
        <div className={s.lane}>
          <span className={s.laneLabel}>
            {t("overview.laneErrors")}
            <small>5xx</small>
          </span>
          <div className={s.cells}>
            {errs.map((v, i) => (
              <i key={i} className={v === null ? s["c-none"] : v ? s["c-err"] : s["c-ok"]} title={v === null ? "—" : n(v)} style={{ animationDelay: `${300 + i * 12}ms` }} />
            ))}
          </div>
        </div>
        <div className={s.lane}>
          <span />
          <HourTicks buckets={overview.buckets} />
        </div>
        <div className={`${s.key} ${s.laneKey}`}>
          <span><i className={s["c-fast"]} />{t("overview.keyFast")}</span>
          <span><i className={s["c-mid"]} />{t("overview.keyMid")}</span>
          <span><i className={s["c-slow"]} />{t("overview.keySlow")}</span>
          <span><i className={s["c-ok"]} />{t("overview.keyClean")}</span>
          <span><i className={s["c-none"]} />{t("overview.noRequests")}</span>
        </div>
      </div>
    </>
  );
}

function Kpi({ label, value, note, tone = "" }: { label: string; value: string; note: string; tone?: string }) {
  return (
    <div className={`${s.kpi} ${tone ? s[tone] : ""}`}>
      <span>{label}</span>
      <b>{value}</b>
      <em>{note}</em>
    </div>
  );
}

/* ================= services table ================= */

function spark(values: number[], width = 196, height = 28) {
  const v = values.length ? values : [0, 0];
  const mx = Math.max(...v, 1e-9);
  const pts = v.map((x, i): [number, number] => [(i / Math.max(1, v.length - 1)) * width, height - 2 - (x / mx) * (height - 5)]);
  return { d: curve(pts), area: `${curve(pts)}L${width},${height}L0,${height}Z` };
}

export function ServicesWidget({ lines, t }: { lines: ServiceLine[]; t: T }) {
  const { n, ms, pct } = useN();
  const mx = Math.max(...lines.map((l) => l.requests ?? 0), 1);
  const lp = (v: number) => Math.max(0, Math.min(100, (Math.log10(Math.max(1, v)) / 4) * 100));
  return (
    <div>
      <div className={s.th}>
        <span>{t("services.service")}</span>
        <span>{t("services.status")}</span>
        <span>{t("services.requests")}</span>
        <span className={s.num}>{t("services.errors")}</span>
        <span>{t("services.worstP95")}</span>
        <span>{t("services.window")}</span>
      </div>
      {lines.map((l, i) => {
        const c = colourOf(i);
        const slow = !l.longLived && (l.worstP95 ?? 0) >= SLOW_MS;
        const status = l.row.health.status;
        const tone = status === "healthy" ? "" : status === "degraded" ? "warn" : status === "critical" ? "bad" : "mute";
        const { d, area } = spark(l.perBucket);
        return (
          <div key={l.bindingId} className={s.tr} style={{ animationDelay: `${200 + i * 60}ms` }}>
            <span className={s.svc}>
              <i style={{ background: c }} />
              <Link href={`/service-health/${l.bindingId}`}>{l.name}</Link>
            </span>
            <span className={`${s.pill} ${tone ? s[tone] : ""}`}>{t(`status.${status}` as never)}</span>
            <span className={s.meter}>
              <em style={{ width: `${Math.max(2, (Math.log10((l.requests ?? 0) + 1) / Math.log10(mx + 1)) * 100)}%`, background: c, opacity: 0.75 }} />
              <b>{n(l.requests)}</b>
            </span>
            <span className={s.num}>{l.requests ? pct((l.errors ?? 0) / l.requests, 1) : "—"}</span>
            <span className={`${s.lat} ${slow ? s.slow : ""}`}>
              {l.longLived ? (
                <span className={s.num}>{t("services.streaming")}</span>
              ) : l.worstP95 === null ? (
                <span className={s.num}>—</span>
              ) : (
                <>
                  <span className={s.track}><em style={{ width: `${lp(l.worstP95)}%` }} /><u /></span>
                  <b>{ms(l.worstP95)}</b>
                </>
              )}
            </span>
            <svg viewBox="0 0 196 28" width="196" height="28" aria-hidden>
              <path d={area} style={{ fill: c }} fillOpacity={0.14} />
              <path d={d} fill="none" style={{ stroke: c }} strokeWidth={1.6} />
            </svg>
          </div>
        );
      })}
    </div>
  );
}

/* ================= traffic graph ================= */

export function TrafficGraphWidget({ lines, overview, t }: { lines: ServiceLine[]; overview: Overview; t: T }) {
  const { n, ms } = useN();
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<string | null>(null);
  const { nodes, edges } = useMemo(() => trafficGraph(lines, overview), [lines, overview]);
  const W = Math.max(700, width || 1000);
  const colourBy = new Map(lines.map((l, i) => [l.bindingId, colourOf(i)]));

  // Columns by depth: users, then services by how far down the call chain
  // they sit, then databases.
  const depth = new Map<string, number>([["users", 0]]);
  for (const node of nodes) if (node.kind === "service") depth.set(node.id, 1);
  for (let pass = 0; pass < nodes.length; pass++)
    for (const e of edges)
      if (e.kind === "call") depth.set(e.to, Math.max(depth.get(e.to) ?? 1, (depth.get(e.from) ?? 1) + 1));
  const serviceMax = Math.max(1, ...nodes.filter((x) => x.kind === "service").map((x) => depth.get(x.id) ?? 1));
  for (const node of nodes) if (node.kind === "database") depth.set(node.id, serviceMax + 1);
  const cols = serviceMax + 2;
  const byCol = new Map<number, GraphNode[]>();
  for (const node of nodes) {
    const c = depth.get(node.id) ?? 1;
    byCol.set(c, [...(byCol.get(c) ?? []), node]);
  }
  const crowded = Math.max(1, ...[...byCol.values()].map((list) => list.length));
  const H = Math.max(470, crowded * 160 + 90);
  const mxr = Math.max(...nodes.map((x) => x.requests), 1);
  const R = (x: GraphNode) => 30 + 20 * (Math.log10(x.requests + 1) / Math.log10(mxr + 1));
  const pos = new Map<string, { x: number; y: number; r: number; c: number }>();
  for (const [c, list] of byCol) {
    list.forEach((node, i) => {
      const x = 70 + (c / Math.max(1, cols - 1)) * (W - 140);
      const y = 50 + ((i + 0.5) / list.length) * (H - 110);
      pos.set(node.id, { x, y, r: R(node), c });
    });
  }
  const mxe = Math.max(...edges.map((e) => e.count), 1);
  const colour = (node: GraphNode) =>
    node.kind === "users" ? "var(--ink-3)" : node.kind === "database" ? "var(--teal)" : colourBy.get(node.id) ?? "var(--blue)";
  const linked = (id: string) => (hover ? hover === id || edges.some((e) => (e.from === hover && e.to === id) || (e.to === hover && e.from === id)) : false);

  return (
    <div ref={ref} className={`${s.graph} ${hover ? s.hov : ""}`}>
      <svg viewBox={`0 0 ${W} ${H}`} height={H} role="img" aria-label={t("graph.title")}>
        <defs>
          {["n", "q", "w"].map((k) => (
            <marker key={k} id={`dash-arrow-${k}`} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
              <path d="M0,0L10,5L0,10z" style={{ fill: k === "q" ? "var(--teal)" : k === "w" ? "var(--amber)" : "color-mix(in srgb, var(--ink-3) 60%, transparent)" }} />
            </marker>
          ))}
        </defs>
        {edges.map((e, i) => {
          const a = pos.get(e.from);
          const b = pos.get(e.to);
          if (!a || !b) return null;
          const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1, ux = dx / len, uy = dy / len;
          const sx = a.x + ux * (a.r + 12), sy = a.y + uy * (a.r + 12), ex = b.x - ux * (b.r + 16), ey = b.y - uy * (b.r + 16);
          const skip = Math.max(0, Math.abs(b.c - a.c) - 1);
          const bend = 26 + skip * 70, mx = (sx + ex) / 2 - uy * bend, my = (sy + ey) / 2 + ux * bend;
          const slow = e.kind === "call" && (e.p95 ?? 0) >= SLOW_MS;
          const tt = slow ? 0.3 : e.kind === "query" ? 0.72 : skip ? 0.7 : 0.5;
          const lx = (1 - tt) ** 2 * sx + 2 * (1 - tt) * tt * mx + tt * tt * ex;
          const ly = (1 - tt) ** 2 * sy + 2 * (1 - tt) * tt * my + tt * tt * ey;
          const w = 1.5 + 6 * (Math.log10(e.count + 1) / Math.log10(mxe + 1));
          const d = `M${sx},${sy}Q${mx},${my} ${ex},${ey}`;
          const text = e.kind === "query" ? t("graph.queries", { count: n(e.count) }) : slow ? `${n(e.count)} · ${ms(e.p95)}` : n(e.count);
          const k = slow ? "w" : e.kind === "query" ? "q" : "n";
          const on = hover !== null && (e.from === hover || e.to === hover);
          return (
            <g key={i} className={`${s.edge} ${e.kind === "query" ? s.query : ""} ${slow ? s.slowEdge : ""} ${on ? s.on : ""}`} style={{ animationDelay: `${i * 90}ms` }}>
              <path d={d} className={s.bs} strokeWidth={w} markerEnd={`url(#dash-arrow-${k})`} />
              <path d={d} className={s.fl} strokeWidth={Math.max(1.5, w * 0.5)} />
              <g className={s.lb}>
                <rect x={lx - text.length * 3.3 - 9} y={ly - 11} width={text.length * 6.6 + 18} height={22} rx={11} />
                <text x={lx} y={ly + 4} textAnchor="middle">{text}</text>
              </g>
            </g>
          );
        })}
        {nodes.map((node, i) => {
          const p = pos.get(node.id);
          if (!p) return null;
          const c = colour(node);
          const rr = p.r + 7;
          const C = 2 * Math.PI * rr;
          let segments: [number, string][] = [[1, c]];
          if (node.codes) {
            const tot = Object.values(node.codes).reduce((a, b) => a + b, 0) || 1;
            const c4 = Object.entries(node.codes).filter(([k]) => k.startsWith("4")).reduce((a, [, v]) => a + v, 0);
            const c5 = Object.entries(node.codes).filter(([k]) => k.startsWith("5")).reduce((a, [, v]) => a + v, 0);
            segments = ([[(tot - c4 - c5) / tot, "var(--ok)"], [c4 / tot, "var(--violet)"], [c5 / tot, "var(--red)"]] as [number, string][]).filter(([f]) => f > 0);
          }
          let off = 0;
          const series = node.series.length ? node.series : [0, 0];
          const mxs = Math.max(...series, 1e-9);
          const ww = p.r * 1.5, hh = p.r * 0.42, x0 = -ww / 2, y0 = p.r * 0.82;
          const wave = curve(series.map((v, j): [number, number] => [x0 + (j / Math.max(1, series.length - 1)) * ww, y0 - (v / mxs) * hh]));
          const big = node.kind === "service" ? n(series.reduce((a, b) => a + b, 0) / Math.max(1, (overview.bucket_seconds ?? 3600) / 60) / Math.max(1, series.length), 1) : n(node.requests);
          const label = node.kind === "users" ? t("graph.users") : node.label;
          const unit = node.kind === "users" ? t("graph.requestsUnit") : node.kind === "database" ? t("graph.queriesUnit") : t("graph.perMinute");
          const lat = node.kind === "users" ? "" : node.longLived ? t("graph.streaming") : node.p95 === null ? "" : `p95 ${ms(node.p95)}`;
          return (
            <g key={node.id} className={`${s.node} ${node.slow ? s.slowNode : ""} ${linked(node.id) ? s.on : ""}`} transform={`translate(${p.x},${p.y})`} style={{ ["--c" as string]: c, animationDelay: `${i * 80}ms` }} onMouseEnter={() => setHover(node.id)} onMouseLeave={() => setHover(null)}>
              <circle r={rr + 14} className={s.halo} />
              <circle r={rr} className={s.tk} />
              {segments.map(([f, sc], j) => {
                const l = Math.max(0, C * f - (segments.length > 1 ? 3 : 0));
                const el = <circle key={j} r={rr} className={s.rg} style={{ stroke: sc }} strokeDasharray={`${l} ${C}`} strokeDashoffset={-off} transform="rotate(-90)" />;
                off += C * f;
                return el;
              })}
              <circle r={p.r} className={s.core} />
              <clipPath id={`dash-clip-${i}`}><circle r={p.r - 1.5} /></clipPath>
              <g clipPath={`url(#dash-clip-${i})`}>
                <path d={`${wave}L${x0 + ww},${p.r}L${x0},${p.r}Z`} className={s.wv} />
                <path d={wave} className={s.wl} />
              </g>
              <text y={-1} textAnchor="middle" className={s.big}>{big}</text>
              <text y={15} textAnchor="middle" className={s.sm}>{unit}</text>
              <text y={rr + 24} textAnchor="middle" className={s.nm}>{label}</text>
              <text y={rr + 40} textAnchor="middle" className={s.lt}>{lat}</text>
              {node.kind !== "users" && (
                <g className={`${s.bd} ${node.slow ? s.w : s.o}`} transform={`translate(${-rr * 0.72},${-rr * 0.72})`}>
                  <circle r={11} />
                  <path d={node.slow ? "M0,-5V1.5M0,4.6V4.8" : "M-4.2,0.3L-1.2,3.2L4.4,-3"} />
                </g>
              )}
            </g>
          );
        })}
      </svg>
      <div className={s.key}>
        <span><i style={{ background: "var(--ok)" }} />{t("graph.ok")}</span>
        <span><i style={{ background: "var(--violet)" }} />4xx</span>
        <span><i style={{ background: "var(--red)" }} />5xx</span>
        <span><i style={{ width: 18, height: 4, background: "var(--ink-3)" }} />{t("graph.width")}</span>
        <span><i style={{ width: 18, height: 3, background: "var(--amber)" }} />{t("graph.slow")}</span>
      </div>
    </div>
  );
}

/* ================= findings ================= */

const FINDING_COLOUR: Record<string, string> = {
  periodic_error_logs: "var(--red)",
  connection_per_query: "var(--teal)",
  slow_call: "var(--amber)",
  client_errors: "var(--blue)",
};

export function FindingsWidget({ overview, lines, t }: { overview: Overview; lines: ServiceLine[]; t: T }) {
  const { n, ms, pct } = useN();
  const [open, setOpen] = useState<number | null>(null);
  const name = (id: string) => lines.find((l) => l.bindingId === id)?.name ?? t("findings.aService");
  const items = overview.findings.items;
  if (!items.length) {
    return (
      <>
        <p className={s.calm}>{t("findings.none")}</p>
        {overview.findings.skipped.length > 0 && <p className={s.skipped}>{t("findings.skipped")}</p>}
      </>
    );
  }
  return (
    <div>
      {items.map((f, i) => {
        const c = FINDING_COLOUR[f.kind] ?? "var(--blue)";
        const m = f.metrics as Record<string, never>;
        const who = f.binding_ids.map(name);
        const head = {
          periodic_error_logs: [t("findings.periodic.title"), t("findings.periodic.body", { pattern: String(m.pattern ?? "").slice(0, 80) }), n(m.lines), t("findings.periodic.unit")],
          connection_per_query: [t("findings.pool.title", { service: who[0] ?? "" }), t("findings.pool.body", { ratio: pct(Number(m.ratio)), database: String(m.database ?? m.system ?? "") }), n(m.connects), t("findings.pool.unit")],
          slow_call: [t("findings.slow.title", { from: who[0] ?? "", to: who[1] ?? "" }), m.likely_cause ? t("findings.slow.bodyCause", { to: who[1] ?? "" }) : t("findings.slow.body"), ms(Number(m.p95_ms)), t("findings.slow.unit")],
          client_errors: [t("findings.client.title", { service: who[0] ?? "" }), m.auth_only ? t("findings.client.bodyAuth") : t("findings.client.body"), n(m.client_errors), t("findings.client.unit")],
        }[f.kind];
        return (
          <div key={i}>
            <div className={`${s.finding} ${open === i ? s.on : ""}`} style={{ ["--c" as string]: c, animationDelay: `${200 + i * 80}ms` }}>
              <div className={s.fText}>
                <span className={s.fNo}>{String(i + 1).padStart(2, "0")}</span>
                <div>
                  <b>{head[0]}</b>
                  <p>{head[1]}</p>
                  <span className={s.chips}>
                    {who.map((w) => <em key={w}>{w}</em>)}
                    <small>{f.severity === "info" ? t("findings.info") : t("findings.warning")}</small>
                  </span>
                </div>
              </div>
              <div className={s.fChart}><FindingChart finding={f} overview={overview} t={t} /></div>
              <div className={s.fValue}>
                <b>{head[2]}</b>
                <small>{head[3]}</small>
                {f.kind === "slow_call" && (
                  <button type="button" className="open" onClick={() => setOpen(open === i ? null : i)} style={{ all: "unset", cursor: "pointer", fontSize: 12, fontWeight: 600, color: "var(--ink-2)", marginTop: 8 }}>
                    {open === i ? t("findings.closeEvidence") : t("findings.openEvidence")}
                  </button>
                )}
              </div>
            </div>
            {open === i && f.kind === "slow_call" && (
              <div className={s.evidence}><SlowCallEvidence from={f.binding_ids[0]} to={f.binding_ids[1]} t={t} /></div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function FindingChart({ finding, overview, t }: { finding: Finding; overview: Overview; t: T }) {
  const { n, ms } = useN();
  const [ref, width] = useWidth<HTMLDivElement>();
  const W = Math.max(320, width || 400);
  const H = 84;
  const m = finding.metrics as Record<string, never>;
  let body: React.ReactNode = null;
  if (finding.kind === "periodic_error_logs") {
    const lines = answered(overview.log_lines) ?? [];
    const mx = Math.max(...lines.map((b) => b.errors), 1);
    const bw = W / Math.max(1, lines.length);
    body = (
      <>
        {lines.map((b, i) => {
          const h = b.errors ? Math.max(3, (b.errors / mx) * (H - 22)) : 2;
          return <rect key={i} x={i * bw + 1} y={H - 18 - h} width={Math.max(2, bw - 2)} height={h} rx={2} style={{ fill: b.errors ? "var(--red)" : "var(--line)" }}><title>{n(b.errors)}</title></rect>;
        })}
        <text x={0} y={H - 2} className="ax" style={{ fontSize: 10.5, fill: "var(--ink-3)" }}>{t("findings.periodic.axis")}</text>
      </>
    );
  } else if (finding.kind === "connection_per_query") {
    const q = Number(m.queries), c = Number(m.connects), mx = Math.max(q, c, 1), L = 110;
    const bw = (v: number) => Math.max(3, (v / mx) * (W - L - 120));
    body = (
      <>
        <text x={0} y={22} style={{ fontSize: 11.5, fontWeight: 600, fill: "var(--ink-2)" }}>{t("findings.pool.queries")}</text>
        <rect x={L} y={10} width={bw(q)} height={16} rx={5} style={{ fill: "var(--teal)" }} />
        <text x={L + bw(q) + 8} y={22} style={{ fontSize: 11.5, fontWeight: 600, fill: "var(--ink-2)" }}>{n(q)}</text>
        <text x={0} y={54} style={{ fontSize: 11.5, fontWeight: 600, fill: "var(--ink-2)" }}>{t("findings.pool.connects")}</text>
        <rect x={L} y={42} width={bw(c)} height={16} rx={5} style={{ fill: "var(--amber)" }} />
        <text x={L + bw(c) + 8} y={54} style={{ fontSize: 11.5, fontWeight: 600, fill: "var(--amber)" }}>{n(c)}</text>
      </>
    );
  } else if (finding.kind === "slow_call") {
    const traffic = answered(overview.traffic)?.find((x) => x.binding_id === finding.binding_ids[0])?.buckets ?? [];
    const L = 44, B = 16, x = (i: number) => L + (i / Math.max(1, traffic.length - 1)) * (W - L - 6);
    const y = (v: number) => 4 + (1 - Math.log10(Math.max(1, v)) / Math.log10(5000)) * (H - B - 4);
    body = (
      <>
        {[[10, "10 ms"], [100, "100 ms"], [1000, "1 sn"]].map(([v, l]) => (
          <g key={String(v)}>
            <line x1={L} x2={W - 6} y1={y(v as number)} y2={y(v as number)} style={{ stroke: v === 1000 ? "var(--amber)" : "var(--line)" }} strokeDasharray={v === 1000 ? "4 4" : "2 5"} />
            <text x={L - 6} y={y(v as number) + 4} textAnchor="end" style={{ fontSize: 10.5, fill: v === 1000 ? "var(--amber)" : "var(--ink-3)" }}>{l}</text>
          </g>
        ))}
        {traffic.map((b, i) =>
          b.p95_ms === null ? null : (
            <g key={i}>
              <line x1={x(i)} x2={x(i)} y1={y(1)} y2={y(b.p95_ms)} style={{ stroke: b.p95_ms >= SLOW_MS ? "var(--amber)" : "color-mix(in srgb, var(--violet) 45%, transparent)" }} strokeWidth={2} />
              <circle cx={x(i)} cy={y(b.p95_ms)} r={b.p95_ms >= SLOW_MS ? 4.5 : 3} style={{ fill: b.p95_ms >= SLOW_MS ? "var(--amber)" : "var(--violet)" }}><title>p95 {ms(b.p95_ms)}</title></circle>
            </g>
          ),
        )}
      </>
    );
  } else if (finding.kind === "client_errors") {
    const codes = Object.entries((m.codes ?? {}) as Record<string, number>).slice(0, 5);
    const mx = Math.max(...codes.map(([, v]) => v), 1), bw = (W - 6) / Math.max(1, codes.length);
    body = (
      <>
        {codes.map(([code, v], i) => {
          const h = (v / mx) * (H - 34);
          return (
            <g key={code}>
              <rect x={i * bw + bw * 0.25} y={H - 18 - h} width={bw * 0.5} height={h} rx={5} style={{ fill: "var(--blue)" }} />
              <text x={i * bw + bw / 2} y={H - 22 - h} textAnchor="middle" style={{ fontSize: 11.5, fontWeight: 600, fill: "var(--ink-2)" }}>{n(v)}</text>
              <text x={i * bw + bw / 2} y={H - 3} textAnchor="middle" style={{ fontSize: 10.5, fill: "var(--ink-3)" }}>{code}</text>
            </g>
          );
        })}
      </>
    );
  }
  return (
    <div ref={ref}>
      <svg viewBox={`0 0 ${W} ${H}`} height={H} aria-hidden>{body}</svg>
    </div>
  );
}

/** "Where did this request's time go?": the slowest call, as a donut. */
function SlowCallEvidence({ from, to, t }: { from: string; to: string; t: T }) {
  const { ms, pct } = useN();
  const [state, setState] = useState<{ total: number; parts: [string, string, number][]; route: string } | "loading" | "none">("loading");
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const page = await apiGet<TracePage>(tracesPath(from, { range: "24h", errorsOnly: false, sort: "slowest" }));
        const slowest = page.requests.find((r) => (r.duration_ms ?? 0) < 300_000);
        if (!slowest) throw new Error("none");
        const [own, called] = await Promise.all([
          apiGet<TraceDetail>(traceDetailPath(from, slowest.trace_id)),
          apiGet<TraceDetail>(traceDetailPath(to, slowest.trace_id)).catch(() => ({ spans: [] }) as unknown as TraceDetail),
        ]);
        const db = (name: string) => name === "connect" || /^(SELECT|INSERT|UPDATE|DELETE)\b/.test(name);
        const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);
        const total = slowest.duration_ms ?? 0;
        const connect = sum(called.spans.filter((x) => x.name === "connect").map((x) => x.duration_ms ?? 0));
        const queries = sum(called.spans.filter((x) => db(x.name) && x.name !== "connect").map((x) => x.duration_ms ?? 0));
        const mine = sum(own.spans.filter((x) => db(x.name)).map((x) => x.duration_ms ?? 0));
        const rest = Math.max(0, total - connect - queries - mine);
        if (!cancelled)
          setState({
            total,
            route: slowest.route,
            parts: [
              [t("evidence.connect"), "var(--amber)", connect],
              [t("evidence.queries"), "var(--teal)", queries],
              [t("evidence.own"), "var(--violet)", mine],
              [t("evidence.rest"), "var(--line-2)", rest],
            ],
          });
      } catch {
        if (!cancelled) setState("none");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [from, to, t]);
  if (state === "loading") return <p className={s.calm}>{t("evidence.loading")}</p>;
  if (state === "none") return <p className={s.calm}>{t("evidence.none")}</p>;
  const R = 78, C = 2 * Math.PI * R;
  let off = 0;
  const top = state.parts.reduce((a, b) => (b[2] > a[2] ? b : a));
  return (
    <div className={s.donut}>
      <svg viewBox="-110 -110 220 220" width="220" height="220" aria-hidden>
        <circle r={R} className={s.trk} />
        {state.parts.map(([l, c, v], i) => {
          const len = state.total ? (v / state.total) * C : 0;
          const el = <circle key={l} r={R} className={s.seg} style={{ stroke: c, animationDelay: `${i * 140}ms` }} strokeDasharray={`${Math.max(0, len - 3)} ${C}`} strokeDashoffset={-off} transform="rotate(-90)"><title>{l}</title></circle>;
          off += len;
          return el;
        })}
        <text y={-4} textAnchor="middle" className={s.tv}>{ms(state.total)}</text>
        <text y={16} textAnchor="middle" className={s.tl}>{t("evidence.oneRequest")}</text>
      </svg>
      <div className={s.donutSide}>
        <b>{t("evidence.title", { total: ms(state.total) })}</b>
        <span><code>{state.route}</code></span>
        {state.parts.map(([l, c, v]) => (
          <div key={l} className={`${s.dRow} ${l === top[0] ? s.hot : ""}`}>
            <i style={{ background: c }} />
            <span>{l}</span>
            <b>{ms(v)}</b>
            <em>{pct(state.total ? v / state.total : 0)}</em>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ================= latency heatmap ================= */

export function HeatmapWidget({ overview, t }: { overview: Overview; t: T }) {
  const { n, hour } = useN();
  const [ref, width] = useWidth<HTMLDivElement>();
  const latency = answered(overview.latency);
  const traffic = answered(overview.traffic);
  if (!latency) return null;
  const W = Math.max(600, width || 800), M = 150, L = 56, B = 26, Tp = 6, rows = 13, ch = 22, H = Tp + rows * ch + B;
  const gw = W - L - M - 18, cw = gw / Math.max(1, latency.cells.length);
  const mx = Math.max(1, ...latency.cells.flat());
  const rowY = (b: number) => Tp + (12 - b) * ch;
  const busiest = traffic?.slice().sort((a, b) => b.buckets.reduce((x, y) => x + y.requests, 0) - a.buckets.reduce((x, y) => x + y.requests, 0))[0];
  const pts: [number, number][] = [];
  busiest?.buckets.forEach((b, i) => {
    if (b.p95_ms) pts.push([L + (i + 0.5) * cw, Tp + (12 - Math.log10(Math.max(1, b.p95_ms)) * 3) * ch + ch / 2]);
  });
  const tmx = Math.max(1, ...latency.totals);
  const mxL = L + gw + 18;
  return (
    <div ref={ref} className={s.heat}>
      <svg viewBox={`0 0 ${W} ${H}`} height={H} role="img" aria-label={t("heat.title")}>
        <rect x={L} y={rowY(12)} width={gw} height={4 * ch} style={{ fill: "color-mix(in srgb, var(--amber) 6%, transparent)" }} />
        {latency.cells.map((col, i) =>
          col.map((v, b) => {
            if (!v) return null;
            const o = 0.22 + 0.78 * (Math.log(v + 1) / Math.log(mx + 1));
            const c = b >= latency.slow_from_class ? "var(--amber)" : "var(--blue)";
            return (
              <rect key={`${i}-${b}`} className={s.hc} x={L + i * cw + 1} y={rowY(b) + 1} width={Math.max(1, cw - 2)} height={ch - 2} rx={3} style={{ fill: `color-mix(in srgb, ${c} ${Math.round(o * 100)}%, var(--card))`, animationDelay: `${i * 14 + (12 - b) * 8}ms` }}>
                <title>{`${hour(overview.buckets[i])} · ${n(v)}`}</title>
              </rect>
            );
          }),
        )}
        {[[0, "1 ms"], [3, "10 ms"], [6, "100 ms"], [9, "1 sn"], [12, "10 sn"]].map(([b, l]) => (
          <text key={String(b)} x={L - 10} y={rowY(b as number) + ch / 2 + 4} textAnchor="end" className={b === 9 ? s.axw : s.ax}>{l}</text>
        ))}
        <line x1={L} x2={L + gw} y1={rowY(9) + ch} y2={rowY(9) + ch} style={{ stroke: "color-mix(in srgb, var(--amber) 70%, transparent)" }} strokeDasharray="4 4" />
        {pts.length > 1 && <path d={curve(pts)} className={s.p95} />}
        {latency.totals.map((v, b) => {
          const w = v ? Math.max(3, (Math.log10(v + 1) / Math.log10(tmx + 1)) * (M - 50)) : 0;
          return (
            <g key={b}>
              <rect className={s.mg} x={mxL} y={rowY(b) + 3} width={w} height={ch - 6} rx={3} style={{ fill: b >= latency.slow_from_class ? "var(--amber)" : "color-mix(in srgb, var(--blue) 70%, transparent)", animationDelay: `${400 + (12 - b) * 40}ms` }} />
              {v > 0 && <text x={mxL + w + 6} y={rowY(b) + ch / 2 + 4} className={s.mv}>{v >= 1000 ? `${n(v / 1000, 1)} B` : n(v)}</text>}
            </g>
          );
        })}
        {overview.buckets.map((tt, i) => {
          const d = new Date(tt * 1000);
          if (d.getMinutes() !== 0 || d.getHours() % (cw * 3 < 60 ? 6 : 3) !== 0) return null;
          return <text key={tt} x={L + (i + 0.5) * cw} y={H - 6} textAnchor="middle" className={s.ax}>{hour(tt)}</text>;
        })}
        <text x={mxL} y={H - 6} className={s.ax}>{t("heat.total")}</text>
      </svg>
      <div className={s.key}>
        <span><i style={{ background: "color-mix(in srgb, var(--blue) 30%, var(--card))" }} /><i style={{ background: "var(--blue)" }} />{t("heat.keyCount")}</span>
        <span><i style={{ background: "var(--amber)" }} />{t("heat.keySlow")}</span>
        {busiest && <span><i style={{ width: 18, height: 2, background: "var(--ink)" }} />{t("heat.keyLine")}</span>}
      </div>
    </div>
  );
}

/* ================= endpoints ================= */

export function EndpointsWidget({ overview, lines, mode, t }: { overview: Overview; lines: ServiceLine[]; mode: "busy" | "slow"; t: T }) {
  const { n, ms } = useN();
  const routes = answered(overview.routes) ?? [];
  const colourBy = new Map(lines.map((l, i) => [l.bindingId, colourOf(i)]));
  const nameBy = new Map(lines.map((l) => [l.bindingId, l.name]));
  const rows = (mode === "busy" ? routes.slice().sort((a, b) => b.requests - a.requests) : routes.filter((r) => r.p95_ms !== null).sort((a, b) => (b.p95_ms ?? 0) - (a.p95_ms ?? 0))).slice(0, 7);
  const mxn = Math.max(1, ...rows.map((r) => r.requests));
  const mxp = Math.max(1, ...rows.map((r) => r.p95_ms ?? 0));
  if (!rows.length) return <p className={s.calm}>{t("endpoints.none")}</p>;
  return (
    <div className={s.board}>
      {rows.map((r, i) => {
        const [method, ...path] = r.route.split(" ");
        const c = colourBy.get(r.binding_id) ?? "var(--blue)";
        const slow = (r.p95_ms ?? 0) >= SLOW_MS;
        const f = mode === "busy" ? Math.log10(r.requests + 1) / Math.log10(mxn + 1) : (r.p95_ms ?? 0) / mxp;
        const bar = mode === "busy" ? `linear-gradient(90deg, color-mix(in srgb, ${c} 55%, transparent), ${c})` : slow ? "linear-gradient(90deg, color-mix(in srgb, var(--amber) 45%, transparent), var(--amber))" : "linear-gradient(90deg, color-mix(in srgb, var(--blue) 45%, transparent), var(--blue))";
        return (
          <div key={`${r.binding_id}${r.route}`} className={s.bRow} style={{ animationDelay: `${200 + i * 50}ms` }}>
            <span className={s.rank}>{i + 1}</span>
            <div className={s.bMain}>
              <div className={s.bTop}>
                <em>{path.length ? method : ""}</em>
                <code title={r.route}>{path.length ? path.join(" ") : r.route}</code>
                <small><i style={{ width: 6, height: 6, borderRadius: "50%", background: c, display: "inline-block" }} />{nameBy.get(r.binding_id)}</small>
              </div>
              <div className={s.bBar}><b style={{ width: `${Math.max(2, f * 100)}%`, background: bar }} /></div>
            </div>
            <div className={s.bNum}>
              <strong>{mode === "busy" ? n(r.requests) : ms(r.p95_ms)}</strong>
              <span className={`${s.pill} ${slow ? s.warn : r.p95_ms === null ? s.mute : ""}`}>
                {mode === "busy" ? (r.p95_ms === null ? t("services.streaming") : ms(r.p95_ms)) : t("endpoints.calls", { count: n(r.requests) })}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ================= capacity ================= */

export function CapacityWidget({ namespaces, t }: { namespaces: NamespaceSnapshot[]; t: T }) {
  const { n, pct } = useN();
  const sum = (f: (x: NamespaceSnapshot) => number | null) => {
    const vals = namespaces.map(f).filter((v): v is number => v !== null);
    return vals.length ? vals.reduce((a, b) => a + b, 0) : null;
  };
  const GiB = 1024 ** 3;
  const cpu = sum((x) => x.cpu.now), cpuReq = sum((x) => x.cpu_requested), cpuLim = sum((x) => x.cpu_limit);
  const mem = sum((x) => x.memory.now), memReq = sum((x) => x.memory_requested), memLim = sum((x) => x.memory_limit);
  const pods = sum((x) => x.pods), restarts = sum((x) => x.restarts_24h);
  const one = (label: string, used: number | null, req: number | null, lim: number | null, f: (v: number) => string, c: string) => {
    if (used === null) return null;
    const top = Math.max(lim ?? 0, req ?? 0, used) || 1;
    const waste = req !== null ? Math.max(0, req - used) : 0;
    return (
      <div className={s.cap}>
        <div className={s.capHead}>
          <span>{label}</span>
          <b>{f(used)}</b>
          {lim !== null && <em>{t("capacity.ofLimit", { value: f(lim) })}</em>}
        </div>
        <div className={s.capBar}>
          <i style={{ width: `${(used / top) * 100}%`, background: c }} />
          <i className={s.hatch} style={{ width: `${(waste / top) * 100}%` }} />
          {req !== null && (
            <u style={{ left: `${(req / top) * 100}%` }}>
              <span>{t("capacity.requested", { value: f(req) })}</span>
            </u>
          )}
        </div>
        <div className={s.capFoot}>
          <span><i style={{ background: c }} />{t("capacity.used", { value: pct(used / top) })}</span>
          {req !== null && <span><i className={s.hatch} />{t("capacity.idle", { value: pct(waste / top) })}</span>}
        </div>
      </div>
    );
  };
  return (
    <div>
      {one(t("capacity.cpu"), cpu, cpuReq, cpuLim, (v) => n(v, 2), "var(--teal)")}
      {one(t("capacity.memory"), mem === null ? null : mem / GiB, memReq === null ? null : memReq / GiB, memLim === null ? null : memLim / GiB, (v) => `${n(v, 1)} GiB`, "var(--blue)")}
      {pods !== null && (
        <div className={s.pods}>
          <div>
            {Array.from({ length: Math.min(60, pods) }, (_, i) => (
              <svg key={i} viewBox="0 0 24 24" width="22" height="22" style={{ animationDelay: `${400 + i * 40}ms` }} aria-hidden><path d="M12 2l8.5 5v10L12 22l-8.5-5V7z" /></svg>
            ))}
          </div>
          <span><b>{n(pods)}</b> {t("capacity.pods", { restarts: n(restarts ?? 0) })}</span>
        </div>
      )}
    </div>
  );
}

/* ================= databases ================= */

export function DatabasesWidget({ overview, lines, t }: { overview: Overview; lines: ServiceLine[]; t: T }) {
  const { n, ms } = useN();
  const rows = (answered(overview.databases) ?? []).slice().sort((a, b) => b.calls - a.calls).slice(0, 6);
  const colourBy = new Map(lines.map((l, i) => [l.bindingId, colourOf(i)]));
  if (!rows.length) return <p className={s.calm}>{t("databases.none")}</p>;
  const total = rows.reduce((a, b) => a + b.calls, 0);
  const mx = Math.max(1, ...rows.map((r) => r.calls));
  return (
    <div>
      <div className={s.dbTop}><b>{n(total)}</b><span>{t("databases.operations")}</span></div>
      {rows.map((r, i) => (
        <div key={`${r.binding_id}${r.operation}${r.database}`} className={`${s.dbRow} ${r.operation === "connect" ? s.hot : ""}`} style={{ animationDelay: `${200 + i * 50}ms` }}>
          <span className={s.dbOp}><i style={{ background: colourBy.get(r.binding_id) }} /><code title={r.database ?? ""}>{r.operation}</code></span>
          <span className={s.dbBar}><b style={{ width: `${Math.max(1.5, (Math.log10(r.calls + 1) / Math.log10(mx + 1)) * 100)}%` }} /></span>
          <strong>{n(r.calls)}</strong>
          <em>{ms(r.p95_ms)}</em>
        </div>
      ))}
    </div>
  );
}

/* ================= logs ================= */

export function LogsWidget({ overview, t }: { overview: Overview; t: T }) {
  const { n, pct } = useN();
  const make = logMakeup(overview);
  if (make.total === null) return null;
  const all = make.total || 1;
  const segs: [string, number, string][] = [
    [t("logs.health"), make.health, "var(--line-2)"],
    [t("logs.errors"), make.errors, "var(--red)"],
    [t("logs.app"), make.app, "var(--blue)"],
  ];
  const R = 62, C = 2 * Math.PI * R;
  let off = 0;
  return (
    <div className={s.logs}>
      <svg viewBox="-86 -86 172 172" width="140" height="140" aria-hidden>
        <circle r={R} className={s.trk} />
        {segs.map(([l, v, c], i) => {
          const len = Math.max(C * 0.012, (v / all) * C);
          const el = <circle key={l} r={R} className={s.seg} style={{ stroke: c, animationDelay: `${i * 140}ms` }} strokeDasharray={`${Math.max(0, len - 3)} ${C}`} strokeDashoffset={-off} transform="rotate(-90)" />;
          off += len;
          return el;
        })}
        <text y={-2} textAnchor="middle" className={s.tv}>{n(make.total)}</text>
        <text y={16} textAnchor="middle" className={s.tl}>{t("logs.lines")}</text>
      </svg>
      <div className={s.logLegend}>
        {segs.map(([l, v, c]) => (
          <div key={l}>
            <i style={{ background: c }} />
            <b>{pct(v / all)}</b>
            <span>{l}</span>
            <em>{n(v)}</em>
          </div>
        ))}
      </div>
    </div>
  );
}
