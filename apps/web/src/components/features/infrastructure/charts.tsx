"use client";

/**
 * The screen's charts, as plain SVG over the API's own time grid. Every
 * chart names its lines on the chart itself and shows the value under the
 * cursor; none of them computes a number the API did not send.
 */

import { useId, useRef, useState } from "react";

import type { SeriesValue } from "@/lib/infrastructure";

import s from "./infrastructure.module.css";

export interface TipRow {
  label: string;
  value: string;
  color?: string;
  sum?: boolean;
}

function Tip({ x, y, title, rows }: { x: number; y: number; title: string; rows: TipRow[] }) {
  return (
    <div className={s.tip} style={{ left: Math.min(x, (typeof window !== "undefined" ? window.innerWidth : 1200) - 230), top: y }} role="presentation">
      <div className={s.tipT}>{title}</div>
      {rows.map((r) => (
        <div key={r.label} className={`${s.tipR} ${r.sum ? s.tipSum : ""}`}>
          {r.color ? <i style={{ background: r.color }} /> : null}
          <span>{r.label}</span>
          <b>{r.value}</b>
        </div>
      ))}
    </div>
  );
}

function useHover(n: number, viewW: number, left: number, right: number) {
  const ref = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<{ i: number; x: number; y: number } | null>(null);
  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const box = ref.current?.getBoundingClientRect();
    if (!box) return;
    const vx = ((e.clientX - box.left) / box.width) * viewW;
    const i = Math.round(((vx - left) / (viewW - left - right)) * (n - 1));
    if (i < 0 || i > n - 1) return setHover(null);
    setHover({ i, x: e.clientX, y: e.clientY });
  };
  return { ref, hover, onMove, onLeave: () => setHover(null) };
}

const pts = (data: SeriesValue[], x: (i: number) => number, y: (v: number) => number) =>
  data
    .map((v, i) => (v === null ? null : `${x(i).toFixed(1)},${y(v).toFixed(1)}`))
    .filter(Boolean)
    .join(" ");

export interface Line {
  value: number;
  label: string;
  color: string;
  dash: string;
}

/** One series with named reference lines, its peak, and "now". */
export function LineChart({
  data,
  lines,
  color,
  format,
  timeLabel,
  axis,
  peakLabel,
  label,
  forecastPerStep,
  forecastSteps = 0,
  width = 820,
  height = 230,
  tipTitle,
  tipRows,
}: {
  data: SeriesValue[];
  lines: Line[];
  color: string;
  format: (v: number) => string;
  timeLabel: (hoursAgo: number) => string;
  axis?: number[];
  peakLabel?: (value: string, index: number) => string;
  label: string;
  forecastPerStep?: number | null;
  forecastSteps?: number;
  width?: number;
  height?: number;
  tipTitle: (index: number) => string;
  tipRows: (index: number) => TipRow[];
}) {
  const id = useId().replace(/:/g, "");
  const n = data.length;
  const total = n + forecastSteps;
  const L = 48;
  const R = lines.some((l) => l.label) ? 96 : 12;
  const T = 24;
  const B = 26;
  const real = data.filter((v): v is number => v !== null);
  const max = Math.max(1e-9, ...real, ...lines.map((l) => l.value), ...(axis ?? [])) * 1.08;
  const x = (i: number) => L + (i / Math.max(1, total - 1)) * (width - L - R);
  const y = (v: number) => T + (1 - v / max) * (height - T - B);
  const h = useHover(n, width, L, R + (width - L - R) * (forecastSteps / Math.max(1, total - 1)));
  let peak = -1;
  data.forEach((v, i) => {
    if (v !== null && (peak < 0 || v > (data[peak] as number))) peak = i;
  });
  let lastIdx = -1;
  for (let i = n - 1; i >= 0; i--) if (data[i] !== null) { lastIdx = i; break; }
  const ticks = axis ?? [0, max / 2, max / 1.08];
  const line = pts(data, x, y);
  const first = data.findIndex((v) => v !== null);
  return (
    <>
      <svg
        ref={h.ref}
        className={s.chart}
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={label}
        onMouseMove={h.onMove}
        onMouseLeave={h.onLeave}
      >
        <defs>
          <linearGradient id={`g${id}`} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0" stopColor={color} stopOpacity=".26" />
            <stop offset="1" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>
        {ticks.map((v) => (
          <g key={v}>
            <line x1={L} x2={width - R} y1={y(v)} y2={y(v)} stroke="var(--chart-grid)" />
            <text x={L - 8} y={y(v) + 4} textAnchor="end" fontSize="11" fill="var(--ink-3)" fontFamily="var(--font-mono)">
              {format(v)}
            </text>
          </g>
        ))}
        {[24, 12, 0].map((ago, k) => (
          <text
            key={ago}
            x={x(Math.round(((24 - ago) / 24) * (n - 1)))}
            y={height - 6}
            textAnchor={k === 0 ? "start" : k === 2 && !forecastSteps ? "end" : "middle"}
            fontSize="11"
            fill="var(--ink-3)"
            fontFamily="var(--font-mono)"
          >
            {timeLabel(ago)}
          </text>
        ))}
        {first >= 0 && lastIdx >= 0 ? (
          <polygon points={`${x(first)},${y(0)} ${line} ${x(lastIdx)},${y(0)}`} fill={`url(#g${id})`} />
        ) : null}
        <polyline points={line} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" />
        {lines.map((l) => (
          <g key={l.label}>
            <line x1={L} x2={width - R} y1={y(l.value)} y2={y(l.value)} stroke={l.color} strokeDasharray={l.dash} />
            <text x={width - R + 8} y={y(l.value) + 4} fontSize="11.5" fill={l.color} fontFamily="var(--font-sans)">
              {l.label}
            </text>
          </g>
        ))}
        {forecastSteps && forecastPerStep && lastIdx >= 0 ? (
          <line
            x1={x(lastIdx)}
            y1={y(data[lastIdx] as number)}
            x2={x(total - 1)}
            y2={y((data[lastIdx] as number) + forecastPerStep * forecastSteps)}
            stroke={color}
            strokeWidth="1.6"
            strokeDasharray="5 4"
          />
        ) : null}
        {peak >= 0 && peakLabel ? (
          <g>
            <circle cx={x(peak)} cy={y(data[peak] as number)} r="4" fill="var(--surface-1)" stroke={color} strokeWidth="2" />
            <text
              x={Math.min(Math.max(x(peak), L + 70), width - R - 70)}
              y={y(data[peak] as number) - 10}
              textAnchor="middle"
              fontSize="11.5"
              fill="var(--ink)"
              fontFamily="var(--font-sans)"
            >
              {peakLabel(format(data[peak] as number), peak)}
            </text>
          </g>
        ) : null}
        {lastIdx >= 0 ? <circle cx={x(lastIdx)} cy={y(data[lastIdx] as number)} r="4.5" fill={color} stroke="var(--surface-1)" strokeWidth="2" /> : null}
        {h.hover ? (
          <g pointerEvents="none">
            <line x1={x(h.hover.i)} x2={x(h.hover.i)} y1={T} y2={height - B} stroke="var(--ink)" />
            {data[h.hover.i] !== null ? <circle cx={x(h.hover.i)} cy={y(data[h.hover.i] as number)} r="3.5" fill={color} /> : null}
          </g>
        ) : null}
      </svg>
      {h.hover ? <Tip x={h.hover.x} y={h.hover.y} title={tipTitle(h.hover.i)} rows={tipRows(h.hover.i)} /> : null}
    </>
  );
}

export interface Band {
  key: string;
  label: string;
  color: string;
  data: SeriesValue[];
}

/** Stacked usage by project, with capacity and request lines named on the chart. */
export function StackedChart({
  bands,
  capacity,
  requested,
  format,
  capLabel,
  reqLabel,
  timeLabel,
  label,
  focus,
  onFocus,
  tipTitle,
  totalLabel,
}: {
  bands: Band[];
  capacity: number;
  requested: number;
  format: (v: number) => string;
  capLabel: string;
  reqLabel: string;
  timeLabel: (hoursAgo: number) => string;
  label: string;
  focus: string | null;
  onFocus: (key: string | null) => void;
  tipTitle: (index: number) => string;
  totalLabel: string;
}) {
  const W = 1180;
  const H = 340;
  const L = 48;
  const R = 160;
  const T = 16;
  const B = 28;
  const n = bands[0]?.data.length ?? 0;
  const stacks: { band: Band; lo: number[]; hi: number[] }[] = [];
  let base = new Array<number>(n).fill(0);
  for (const band of bands) {
    const hi = base.map((v, i) => v + (band.data[i] ?? 0));
    stacks.push({ band, lo: base, hi });
    base = hi;
  }
  const top0 = Math.max(capacity, requested, ...base, 1e-9) * 1.04;
  const mag = Math.pow(10, Math.floor(Math.log10(top0 / 4)));
  const step = [1, 2, 2.5, 5, 10].map((k) => k * mag).find((st) => top0 / st <= 5) ?? top0 / 4;
  const max = Math.ceil(top0 / step) * step;
  const x = (i: number) => L + (i / Math.max(1, n - 1)) * (W - L - R);
  const y = (v: number) => T + (1 - v / max) * (H - T - B);
  const h = useHover(n, W, L, R);
  const ticks: number[] = [];
  for (let v = 0; v <= max + 1e-9; v += step) ticks.push(v);
  let last = Number.POSITIVE_INFINITY;
  const endTotal = base[n - 1] || 1;
  // Bands too thin to read get no direct label; the hover readout still names them.
  const labels = stacks
    .filter((st) => (st.hi[n - 1] - st.lo[n - 1]) / endTotal >= 0.03)
    .map((st) => ({ st, mid: y((st.lo[n - 1] + st.hi[n - 1]) / 2) }))
    .sort((a, b) => b.mid - a.mid)
    .map((l) => {
      const yy = Math.min(l.mid, last - 15);
      last = yy;
      return { ...l, yy };
    });
  const capY = y(capacity);
  const reqY = y(requested);
  const close = Math.abs(capY - reqY) < 18;
  return (
    <>
      <svg ref={h.ref} className={s.chart} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label} onMouseMove={h.onMove} onMouseLeave={() => { h.onLeave(); onFocus(null); }}>
        {ticks.map((v) => (
          <g key={v}>
            <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} stroke="var(--chart-grid)" />
            <text x={L - 8} y={y(v) + 4} textAnchor="end" fontSize="11" fill="var(--ink-3)" fontFamily="var(--font-mono)">
              {format(v)}
            </text>
          </g>
        ))}
        {[24, 18, 12, 6, 0].map((ago, k) => (
          <text key={ago} x={x(Math.round(((24 - ago) / 24) * (n - 1)))} y={H - 8} textAnchor={k === 0 ? "start" : k === 4 ? "end" : "middle"} fontSize="11" fill="var(--ink-3)" fontFamily="var(--font-mono)">
            {timeLabel(ago)}
          </text>
        ))}
        {stacks.map(({ band, lo, hi }) => {
          const up = hi.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
          const down = lo.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).reverse().join(" ");
          return (
            <g key={band.key} className={`${s.band} ${focus && focus !== band.key ? s.fade : ""}`} onMouseEnter={() => onFocus(band.key)}>
              <polygon points={`${up} ${down}`} fill={`color-mix(in srgb, ${band.color} 62%, var(--surface-1))`} />
              <polyline points={up} fill="none" stroke={band.color} strokeWidth="1" />
            </g>
          );
        })}
        {labels.map(({ st, mid, yy }) => (
          <g key={st.band.key} className={`${s.blab} ${focus && focus !== st.band.key ? s.fade : ""}`}>
            <line x1={x(n - 1) + 4} x2={x(n - 1) + 14} y1={mid} y2={yy} stroke="var(--ink-3)" />
            <text x={x(n - 1) + 18} y={yy + 4} fontSize="12" fill="var(--ink-2)" fontFamily="var(--font-sans)">
              {st.band.label}{" "}
              <tspan fill="var(--ink)" fontFamily="var(--font-mono)">
                {format(st.band.data[n - 1] ?? 0)}
              </tspan>
            </text>
          </g>
        ))}
        <line x1={L} x2={W - R} y1={capY} y2={capY} stroke="var(--ink-2)" strokeWidth="1.2" />
        <text x={L + 6} y={capY - 6} fontSize="11" fill="var(--ink-2)" fontFamily="var(--font-mono)">
          {capLabel}
        </text>
        <line x1={L} x2={W - R} y1={reqY} y2={reqY} stroke="var(--ink-2)" strokeDasharray="3 4" />
        <text x={close ? W - R - 6 : L + 6} y={close ? reqY + (requested > capacity ? -6 : 15) : reqY + 15} textAnchor={close ? "end" : "start"} fontSize="11" fill="var(--ink-3)" fontFamily="var(--font-mono)">
          {reqLabel}
        </text>
        {h.hover ? (
          <g pointerEvents="none">
            <line x1={x(h.hover.i)} x2={x(h.hover.i)} y1={T} y2={H - B} stroke="var(--ink)" />
            {stacks.map(({ band, hi }) => (
              <circle key={band.key} cx={x(h.hover!.i)} cy={y(hi[h.hover!.i])} r="3.5" fill={band.color} stroke="var(--surface-1)" strokeWidth="1.5" />
            ))}
          </g>
        ) : null}
      </svg>
      {h.hover ? (
        <Tip
          x={h.hover.x}
          y={h.hover.y}
          title={tipTitle(h.hover.i)}
          rows={[
            ...[...stacks].reverse().map(({ band }) => ({ label: band.label, value: format(band.data[h.hover!.i] ?? 0), color: band.color })),
            { label: totalLabel, value: format(base[h.hover.i]), sum: true },
          ]}
        />
      ) : null}
    </>
  );
}

/** A small 24-hour trace; hovering shows the value at that time. */
export function Spark({
  data,
  color,
  title,
  format,
  tipTitle,
}: {
  data: SeriesValue[];
  color: string;
  title: string;
  format: (v: number) => string;
  tipTitle: (index: number) => string;
}) {
  const id = useId().replace(/:/g, "");
  const W = 130;
  const H = 30;
  const real = data.filter((v): v is number => v !== null);
  const max = Math.max(1e-9, ...real) * 1.12;
  const x = (i: number) => (i / Math.max(1, data.length - 1)) * W;
  const y = (v: number) => H - 1.5 - (v / max) * (H - 4);
  const h = useHover(data.length, W, 0, 0);
  const line = pts(data, x, y);
  return (
    <>
      <svg ref={h.ref} className={s.spark} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true" onMouseMove={h.onMove} onMouseLeave={h.onLeave}>
        <defs>
          <linearGradient id={`p${id}`} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0" stopColor={color} stopOpacity=".3" />
            <stop offset="1" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>
        {line ? <polygon points={`0,${H} ${line} ${W},${H}`} fill={`url(#p${id})`} /> : null}
        <polyline points={line} fill="none" stroke={color} strokeWidth="1.4" vectorEffect="non-scaling-stroke" />
        {h.hover ? <line x1={x(h.hover.i)} x2={x(h.hover.i)} y1="0" y2={H} stroke="var(--ink)" vectorEffect="non-scaling-stroke" /> : null}
      </svg>
      {h.hover ? (
        <Tip x={h.hover.x} y={h.hover.y} title={tipTitle(h.hover.i)} rows={[{ label: title, value: data[h.hover.i] === null ? "—" : format(data[h.hover.i] as number) }]} />
      ) : null}
    </>
  );
}
