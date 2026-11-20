"use client";

/**
 * The screen's instruments, drawn as plain SVG over real timestamps:
 * the 24-hour alarm clock, a rule's lane, a sparkline and the full chart.
 *
 * Nothing here computes whether a rule fired. Episodes come from the API
 * (the runner's own events, or the server's replay for a preview); these
 * components only place them on a clock face or a time axis.
 */

import { useId } from "react";

import type { AlertRule, Episode, Operator, SeriesPoint, Severity } from "@/lib/alertRules";

import s from "./alertRules.module.css";

export const DAY_MS = 24 * 3600 * 1000;

export function tone(rule: Pick<AlertRule, "status" | "severity">): string {
  if (rule.status === "muted" || rule.status === "disabled") return "var(--mute)";
  if (rule.status === "firing" || rule.status === "pending") return severityTone(rule.severity);
  return "var(--ok)";
}

export function severityTone(severity: Severity): string {
  return severity === "critical" ? "var(--crit)" : "var(--warn)";
}

export function hhmm(date: Date, tag: string): string {
  return date.toLocaleTimeString(tag, { hour: "2-digit", minute: "2-digit", hour12: false });
}

function polar(cx: number, cy: number, r: number, deg: number): [number, number] {
  const a = ((deg - 90) * Math.PI) / 180;
  return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
}

function arc(cx: number, cy: number, r: number, d0: number, d1: number): string {
  const [x0, y0] = polar(cx, cy, r, d0);
  const [x1, y1] = polar(cx, cy, r, Math.min(d1, d0 + 359.9));
  const large = d1 - d0 > 180 ? 1 : 0;
  return `M${x0.toFixed(2)} ${y0.toFixed(2)}A${r} ${r} 0 ${large} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
}

function dayDegrees(date: Date): number {
  return ((date.getHours() * 60 + date.getMinutes() + date.getSeconds() / 60) / 4) % 360;
}

export interface Span {
  start: number;
  end: number;
  episode: Episode;
}

/** Episodes clipped to the window, as epoch-ms spans. */
export function spans(episodes: Episode[], now: number): Span[] {
  const from = now - DAY_MS;
  return episodes
    .map((episode) => ({
      start: Math.max(from, Date.parse(episode.fired_at)),
      end: Math.min(now, episode.resolved_at ? Date.parse(episode.resolved_at) : now),
      episode,
    }))
    .filter((span) => span.end >= span.start);
}

export function AlarmClock({
  rules,
  now,
  firing,
  labels,
  onPick,
  onHover,
}: {
  rules: AlertRule[];
  now: number;
  firing: number;
  labels: { center: string; firing: string };
  onPick: (id: string) => void;
  onHover: (rule: AlertRule | null, event?: React.MouseEvent) => void;
}) {
  const cx = 240;
  const cy = 240;
  const r0 = 92;
  const gap = rules.length > 7 ? Math.max(11, 128 / rules.length) : 19;
  const width = Math.min(11, gap - 6);
  const ticks = [];
  for (let h = 0; h < 24; h++) {
    const deg = h * 15;
    const major = h % 6 === 0;
    const [x0, y0] = polar(cx, cy, 226, deg);
    const [x1, y1] = polar(cx, cy, major ? 212 : 219, deg);
    ticks.push(
      <line
        key={`t${h}`}
        x1={x0}
        y1={y0}
        x2={x1}
        y2={y1}
        stroke={major ? "var(--ink-3)" : "var(--hair)"}
        strokeWidth={major ? 1.4 : 1}
      />,
    );
    if (h % 3 === 0) {
      const [tx, ty] = polar(cx, cy, 236, deg);
      ticks.push(
        <text
          key={`l${h}`}
          x={tx}
          y={ty + 4}
          textAnchor="middle"
          fontSize="11"
          fill="var(--ink-3)"
          fontFamily="var(--font-mono)"
        >
          {String(h).padStart(2, "0")}
        </text>,
      );
    }
  }
  const [hx, hy] = polar(cx, cy, 226, dayDegrees(new Date(now)));
  return (
    <svg viewBox="-16 -8 512 496" role="img" aria-label={labels.center}>
      {ticks}
      {rules.map((rule, k) => {
        const radius = r0 + k * gap;
        const color = tone({ ...rule, status: rule.status === "muted" ? "muted" : "firing" });
        return (
          <g
            key={rule.id}
            className={s.ring}
            data-ring={rule.id}
            onMouseMove={(event) => onHover(rule, event)}
            onMouseLeave={() => onHover(null)}
            onClick={() => onPick(rule.id)}
          >
            <circle cx={cx} cy={cy} r={radius} fill="none" stroke="var(--track)" strokeWidth={width} />
            {spans(rule.episodes, now).map((span) => {
              const d0 = dayDegrees(new Date(span.start));
              const minutes = (span.end - span.start) / 60000;
              return (
                <path
                  key={span.episode.fired_at + span.episode.binding_id}
                  d={arc(cx, cy, radius, d0, d0 + Math.max(4, minutes / 4))}
                  fill="none"
                  stroke={color}
                  strokeWidth={width}
                />
              );
            })}
            <circle cx={cx} cy={cy} r={radius} fill="none" stroke="transparent" strokeWidth={gap} />
          </g>
        );
      })}
      <line x1={cx} y1={cy} x2={hx} y2={hy} stroke="var(--ink)" strokeWidth="1.5" />
      <circle cx={hx} cy={hy} r="4" fill="var(--ink)" />
      <circle cx={cx} cy={cy} r="72" fill="var(--plate)" stroke="var(--hair)" />
      <text
        x={cx}
        y={cy - 4}
        textAnchor="middle"
        fontSize="46"
        fontWeight="500"
        fill={firing ? "var(--crit)" : "var(--ok)"}
        fontFamily="var(--font-mono)"
        letterSpacing="-2"
      >
        {firing}
      </text>
      <text x={cx} y={cy + 18} textAnchor="middle" fontSize="12" fill="var(--ink-2)">
        {labels.center}
      </text>
      <text
        x={cx}
        y={cy + 34}
        textAnchor="middle"
        fontSize="10"
        fill="var(--ink-3)"
        fontFamily="var(--font-mono)"
        letterSpacing="1.4"
      >
        {labels.firing}
      </text>
    </svg>
  );
}

export function Lane({
  rule,
  now,
  onSegment,
}: {
  rule: AlertRule;
  now: number;
  onSegment: (span: Span | null, event?: React.MouseEvent) => void;
}) {
  const w = 600;
  const from = now - DAY_MS;
  const color = tone({ ...rule, status: rule.status === "muted" ? "muted" : "firing" });
  return (
    <svg viewBox={`0 0 ${w} 28`} preserveAspectRatio="none" aria-hidden="true">
      <rect x="0" y="9" width={w} height="10" rx="2" fill="var(--track)" />
      {[1, 2, 3].map((k) => (
        <line key={k} x1={(k * w) / 4} x2={(k * w) / 4} y1="5" y2="23" stroke="var(--hair)" />
      ))}
      {spans(rule.episodes, now).map((span) => (
        <rect
          key={span.episode.fired_at + span.episode.binding_id}
          x={(((span.start - from) / DAY_MS) * w).toFixed(1)}
          y="6"
          width={Math.max(3, ((span.end - span.start) / DAY_MS) * w).toFixed(1)}
          height="16"
          rx="2"
          fill={color}
          onMouseMove={(event) => onSegment(span, event)}
          onMouseLeave={() => onSegment(null)}
        />
      ))}
      <rect x={w - 2} y="2" width="2" height="24" fill="var(--ink)" />
    </svg>
  );
}

function numeric(points: SeriesPoint[]): { t: number; v: number }[] {
  return points
    .filter((p): p is { at: string; value: number } => p.value !== null)
    .map((p) => ({ t: Date.parse(p.at), v: p.value }));
}

export function Spark({
  points,
  threshold,
  color,
  tall = false,
}: {
  points: SeriesPoint[];
  threshold: number;
  color: string;
  /** One or two firing rules get a taller trace, so the panel reads as a chart. */
  tall?: boolean;
}) {
  const id = useId().replace(/:/g, "");
  const data = numeric(points).slice(-120);
  const w = 420;
  const h = tall ? 150 : 46;
  const style = { height: h };
  if (data.length < 2)
    return <svg className={s.spark} style={style} viewBox={`0 0 ${w} ${h}`} aria-hidden="true" />;
  const t0 = data[0].t;
  const t1 = data[data.length - 1].t;
  const max = Math.max(threshold * 1.25, ...data.map((d) => d.v)) || 1;
  const x = (t: number) => ((t - t0) / Math.max(1, t1 - t0)) * w;
  const y = (v: number) => h - 2 - (v / max) * (h - 6);
  const pts = data.map((d) => `${x(d.t).toFixed(1)},${y(d.v).toFixed(1)}`).join(" ");
  return (
    <svg
      className={s.spark}
      style={style}
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      <defs>
        <linearGradient id={`sp${id}`} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stopColor={color} stopOpacity=".35" />
          <stop offset="1" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <line
        x1="0"
        x2={w}
        y1={y(threshold)}
        y2={y(threshold)}
        stroke={color}
        strokeDasharray="4 4"
        vectorEffect="non-scaling-stroke"
      />
      <polygon points={`0,${h} ${pts} ${w},${h}`} fill={`url(#sp${id})`} />
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.6" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

export function SignalChart({
  points,
  episodes,
  threshold,
  operator,
  severity,
  now,
  format,
  timeLabel,
  thresholdLabel,
  nowLabel,
  height = 260,
  width = 720,
  label,
}: {
  points: SeriesPoint[];
  episodes: Episode[];
  threshold: number;
  operator: Operator;
  severity: Severity;
  now: number;
  format: (v: number) => string;
  timeLabel: (t: number) => string;
  thresholdLabel: string;
  nowLabel: string;
  height?: number;
  width?: number;
  label: string;
}) {
  const id = useId().replace(/:/g, "");
  const W = width;
  const H = height;
  const L = 56;
  const R = 14;
  const T = 14;
  const B = 30;
  const from = now - DAY_MS;
  const data = numeric(points);
  // An anomaly rule's threshold moves with its normal: drawn as a line
  // through each point's own edge rather than one flat level.
  const moving = points.some((p) => p.threshold !== undefined);
  const edges = moving
    ? points.flatMap((p) =>
        p.threshold === null || p.threshold === undefined ? [] : [{ t: Date.parse(p.at), v: p.threshold }],
      )
    : [];
  const lineAt = moving ? (edges[edges.length - 1]?.v ?? 0) : threshold;
  const max = Math.max(lineAt * 1.4, ...data.map((d) => d.v), ...edges.map((d) => d.v), 1e-9) * 1.06;
  const x = (t: number) => L + ((t - from) / DAY_MS) * (W - L - R);
  const y = (v: number) => T + (1 - v / max) * (H - T - B);
  const color = severityTone(severity);
  const pts = data.map((d) => `${x(d.t).toFixed(1)},${y(d.v).toFixed(1)}`).join(" ");
  const last = data[data.length - 1];
  const below = operator === "<" || operator === "below_normal";
  const lastBreached = last !== undefined && (below ? last.v < lineAt : last.v > lineAt);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label}>
      <defs>
        <linearGradient id={`a${id}`} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stopColor="var(--accent)" stopOpacity=".3" />
          <stop offset="1" stopColor="var(--accent)" stopOpacity="0" />
        </linearGradient>
        <linearGradient id={`b${id}`} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stopColor={color} stopOpacity=".22" />
          <stop offset="1" stopColor={color} stopOpacity=".04" />
        </linearGradient>
      </defs>
      {spans(episodes, now).map((span) => {
        const bx = x(span.start);
        const bw = Math.max(3, x(span.end) - bx);
        return (
          <g key={span.episode.fired_at + span.episode.binding_id}>
            <rect x={bx} y={T} width={bw} height={H - T - B} fill={`url(#b${id})`} />
            <rect x={bx} y={T} width={bw} height="2" fill={color} />
          </g>
        );
      })}
      {[0, 1, 2, 3, 4].map((k) => {
        const v = (max * k) / 4;
        return (
          <g key={k}>
            <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} stroke="var(--chart-grid)" />
            <text
              x={L - 8}
              y={y(v) + 4}
              textAnchor="end"
              fontSize="11"
              fill="var(--ink-3)"
              fontFamily="var(--font-mono)"
            >
              {format(v)}
            </text>
          </g>
        );
      })}
      {data.length > 1 ? (
        <>
          <polygon
            points={`${x(data[0].t)},${y(0)} ${pts} ${x(last.t)},${y(0)}`}
            fill={`url(#a${id})`}
          />
          <polyline
            points={pts}
            fill="none"
            stroke="var(--accent)"
            strokeWidth="2"
            strokeLinejoin="round"
          />
        </>
      ) : null}
      {moving ? (
        <polyline
          points={edges.map((d) => `${x(d.t).toFixed(1)},${y(d.v).toFixed(1)}`).join(" ")}
          fill="none"
          stroke={color}
          strokeWidth="1.5"
          strokeDasharray="7 5"
        />
      ) : (
        <line
          x1={L}
          x2={W - R}
          y1={y(threshold)}
          y2={y(threshold)}
          stroke={color}
          strokeWidth="1.5"
          strokeDasharray="7 5"
        />
      )}
      <rect x={W - R - 96} y={y(lineAt) - 21} width="96" height="17" rx="4" fill={color} />
      <text
        x={W - R - 48}
        y={y(lineAt) - 9}
        textAnchor="middle"
        fontSize="11"
        fontWeight="500"
        fill="var(--plate)"
        fontFamily="var(--font-mono)"
      >
        {thresholdLabel}
      </text>
      {last ? (
        <circle
          cx={x(last.t)}
          cy={y(last.v)}
          r="5"
          fill={lastBreached ? color : "var(--accent)"}
          stroke="var(--sunken)"
          strokeWidth="2.5"
        />
      ) : null}
      {[0, 6, 12, 18, 24].map((hour) => {
        const t = from + hour * 3600 * 1000;
        return (
          <text
            key={hour}
            x={x(t)}
            y={H - 10}
            textAnchor={hour === 0 ? "start" : hour === 24 ? "end" : "middle"}
            fontSize="11"
            fill="var(--ink-3)"
            fontFamily="var(--font-mono)"
          >
            {hour === 24 ? nowLabel : timeLabel(t)}
          </text>
        );
      })}
    </svg>
  );
}
