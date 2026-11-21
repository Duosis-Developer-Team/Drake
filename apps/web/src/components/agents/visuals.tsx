"use client";

/**
 * The pictures of Agent Visibility.
 *
 * Drawn as SVG at the size they are shown (measured, not stretched), so
 * type stays crisp and bars keep their gaps at any width. Colours come from
 * the series tokens: a provider keeps its colour on every page, and red is
 * only ever a failure.
 */

import { useEffect, useId, useMemo, useRef, useState } from "react";

import { providerColor, providerName } from "@/lib/agents";

export function useWidth<T extends HTMLElement>(
  fallback = 640,
): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () =>
      setWidth(Math.max(120, Math.floor(el.getBoundingClientRect().width)));
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

export interface BarPart {
  key: string;
  label: string;
  value: number;
  color: string;
}

export interface BarPoint {
  t: string;
  parts: BarPart[];
  errors: number;
}

/** Hourly stacked bars, failures as a red cap. Hover names the hour. */
export function ActivityBars({
  points,
  height = 200,
  label,
  timeLabel,
  errorLabel,
  tickAt,
  valueFormat,
}: {
  points: BarPoint[];
  height?: number;
  label: string;
  timeLabel: (iso: string, short: boolean) => string;
  errorLabel: string;
  /** Which points get an axis label (and a faint day line); spaced evenly without it. */
  tickAt?: (iso: string) => boolean;
  /** How a value reads on the axis and in the tooltip (counts by default). */
  valueFormat?: (value: number) => string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const left = 40;
  const bottom = 22;
  const plotW = width - left - 8;
  const plotH = height - bottom - 8;
  const totals = points.map((p) =>
    p.parts.reduce((a, part) => a + part.value, 0),
  );
  const peak = Math.max(0, ...totals);
  const max = peak > 0 ? peak : 1;
  const nice = niceCeil(max);
  const slot = plotW / Math.max(1, points.length);
  const barW = Math.max(2, Math.min(28, slot * 0.68));
  const y = (v: number) => 8 + plotH - (v / nice) * plotH;
  const ticks = [0, nice / 2, nice];
  const labelEvery = Math.max(
    1,
    Math.ceil(points.length / Math.max(2, Math.floor(plotW / 84))),
  );
  const legend =
    points[0]?.parts.map((p) => ({
      key: p.key,
      label: p.label,
      color: p.color,
    })) ?? [];
  const hovered = hover === null ? null : points[hover];

  return (
    <div ref={ref} className="relative w-full" data-testid="activity-bars">
      <svg
        width={width}
        height={height}
        role="img"
        aria-label={label}
        className="block"
      >
        {ticks.map((v) => (
          <g key={v}>
            <line
              x1={left}
              x2={width - 8}
              y1={y(v)}
              y2={y(v)}
              stroke="var(--chart-grid)"
            />
            <text
              x={left - 8}
              y={y(v) + 4}
              textAnchor="end"
              fontSize="11"
              fill="var(--chart-axis)"
              className="tabular-nums"
            >
              {valueFormat ? valueFormat(v) : formatCompact(v)}
            </text>
          </g>
        ))}
        {points.map((p, i) => {
          const x = left + i * slot + (slot - barW) / 2;
          let acc = 0;
          const active = hover === i;
          return (
            <g
              key={p.t}
              onMouseEnter={() => setHover(i)}
              onMouseLeave={() => setHover(null)}
            >
              <rect
                x={left + i * slot}
                y={8}
                width={slot}
                height={plotH}
                fill={active ? "var(--surface-hover)" : "transparent"}
              />
              {p.parts.map((part) => {
                if (!part.value) return null;
                const y0 = y(acc + part.value);
                const h = y(acc) - y0;
                acc += part.value;
                return (
                  <rect
                    key={part.key}
                    x={x}
                    y={y0}
                    width={barW}
                    height={Math.max(1, h)}
                    rx={Math.min(3, barW / 3)}
                    fill={part.color}
                    opacity={hover === null || active ? 1 : 0.55}
                  />
                );
              })}
              {p.errors > 0 ? (
                <rect
                  x={x}
                  y={y(totals[i]) - 3}
                  width={barW}
                  height={3}
                  rx={1.5}
                  fill="var(--status-critical)"
                />
              ) : null}
              {tickAt?.(p.t) ? (
                <line
                  x1={left + i * slot}
                  x2={left + i * slot}
                  y1={8}
                  y2={8 + plotH}
                  stroke="var(--chart-grid)"
                  strokeDasharray="2 3"
                />
              ) : null}
              {(tickAt ? tickAt(p.t) : i % labelEvery === 0) ? (
                <text
                  x={x + barW / 2}
                  y={height - 6}
                  textAnchor="middle"
                  fontSize="11"
                  fill="var(--chart-axis)"
                >
                  {timeLabel(p.t, true)}
                </text>
              ) : null}
            </g>
          );
        })}
      </svg>
      {hovered && hover !== null ? (
        <div
          className="pointer-events-none absolute top-1 z-10 min-w-40 rounded-control border border-border bg-[var(--chart-tooltip)] px-3 py-2 text-micro text-[var(--chart-tooltip-text)] shadow-lg"
          style={{
            left: Math.min(
              width - 170,
              Math.max(0, left + hover * slot + slot / 2 - 80),
            ),
          }}
        >
          <p className="mb-1 font-semibold">{timeLabel(hovered.t, false)}</p>
          {hovered.parts.map((part) => (
            <p
              key={part.key}
              className="flex items-center justify-between gap-4"
            >
              <span className="inline-flex items-center gap-1.5">
                <span
                  className="h-2 w-2 rounded-sm"
                  style={{ background: part.color }}
                />
                {part.label}
              </span>
              <span className="tabular-nums">
                {valueFormat
                  ? valueFormat(part.value)
                  : part.value.toLocaleString()}
              </span>
            </p>
          ))}
          {hovered.errors ? (
            <p className="mt-1 flex items-center justify-between gap-4 text-[#ff8a8a]">
              <span>{errorLabel}</span>
              <span className="tabular-nums">
                {hovered.errors.toLocaleString()}
              </span>
            </p>
          ) : null}
        </div>
      ) : null}
      {legend.length > 1 ? (
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 pl-10 text-micro text-ink-secondary">
          {legend.map((l) => (
            <span key={l.key} className="inline-flex items-center gap-1.5">
              <span
                className="h-2 w-2 rounded-sm"
                style={{ background: l.color }}
              />
              {l.label}
            </span>
          ))}
          <span className="inline-flex items-center gap-1.5">
            <span className="h-[3px] w-3 rounded-sm bg-critical" />
            {errorLabel}
          </span>
        </div>
      ) : null}
    </div>
  );
}

function niceCeil(value: number): number {
  const exp = 10 ** Math.floor(Math.log10(value));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * exp >= value) return m * exp;
  return 10 * exp;
}

function formatCompact(v: number): string {
  return new Intl.NumberFormat("en", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(v);
}

/** An agent's mark: initials, ringed by its providers' share of calls. */
export function AgentGlyph({
  name,
  providers,
  size = 44,
  observed = false,
}: {
  name: string;
  providers: { provider: string; calls: number }[];
  size?: number;
  observed?: boolean;
}) {
  const id = useId().replace(/:/g, "");
  const total = providers.reduce((a, p) => a + p.calls, 0);
  const r = size / 2 - 2.5;
  const c = 2 * Math.PI * r;
  const initials = useMemo(() => {
    const words = name
      .replace(/([a-z])([A-Z])/g, "$1 $2")
      .split(/[\s_\-./:]+/)
      .filter(Boolean);
    if (words.length === 0) return "?";
    if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
    return (words[0][0] + words[1][0]).toUpperCase();
  }, [name]);
  const main = providers[0]?.provider;
  let offset = 0;
  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      aria-hidden
      className="shrink-0"
    >
      <defs>
        <clipPath id={`g${id}`}>
          <circle cx={size / 2} cy={size / 2} r={r - 3} />
        </clipPath>
      </defs>
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r - 3}
        fill={main ? providerColor(main) : "var(--surface-3)"}
        fillOpacity={main ? 0.16 : 1}
      />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke="var(--border-subtle)"
        strokeWidth="3"
        strokeDasharray={observed ? "3 3" : undefined}
      />
      {total > 0
        ? providers.map((p) => {
            const len = (p.calls / total) * c;
            const seg = (
              <circle
                key={p.provider}
                cx={size / 2}
                cy={size / 2}
                r={r}
                fill="none"
                stroke={providerColor(p.provider)}
                strokeWidth="3"
                strokeDasharray={`${Math.max(0, len - 1.5)} ${c}`}
                strokeDashoffset={-offset}
                transform={`rotate(-90 ${size / 2} ${size / 2})`}
                strokeLinecap="round"
              />
            );
            offset += len;
            return seg;
          })
        : null}
      <text
        x="50%"
        y="50%"
        dy="0.36em"
        textAnchor="middle"
        fontSize={size * 0.3}
        fontWeight="650"
        fill="var(--text-primary)"
        clipPath={`url(#g${id})`}
      >
        {initials}
      </text>
    </svg>
  );
}

/** Where the model calls went: one bar, one segment per provider. */
export function ProviderMix({
  providers,
  empty,
}: {
  providers: { provider: string; calls: number }[];
  empty: string;
}) {
  const total = providers.reduce((a, p) => a + p.calls, 0);
  if (!total) return <p className="text-caption text-ink-muted">{empty}</p>;
  return (
    <div className="space-y-3" data-testid="provider-mix">
      <div className="flex h-3 w-full overflow-hidden rounded-full bg-surface-3">
        {providers.map((p) => (
          <span
            key={p.provider}
            title={`${providerName(p.provider)} · ${p.calls}`}
            style={{
              width: `${(p.calls / total) * 100}%`,
              background: providerColor(p.provider),
            }}
            className="h-full border-r-2 border-[var(--surface-1)] last:border-r-0"
          />
        ))}
      </div>
      <ul className="space-y-1.5">
        {providers.slice(0, 6).map((p) => (
          <li
            key={p.provider}
            className="flex items-center justify-between gap-3 text-caption"
          >
            <span className="inline-flex items-center gap-2 text-ink">
              <span
                className="h-2.5 w-2.5 rounded-full"
                style={{ background: providerColor(p.provider) }}
              />
              {providerName(p.provider)}
            </span>
            <span className="tabular-nums text-ink-secondary">
              {p.calls.toLocaleString()}{" "}
              <span className="text-ink-muted">
                · {Math.round((p.calls / total) * 100)}%
              </span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Tokens by kind: input (cached part shaded) and output (reasoning part shaded). */
export function TokenBar({
  input,
  cached,
  output,
  reasoning,
  max,
  color,
}: {
  input: number;
  cached: number;
  output: number;
  reasoning: number;
  max: number;
  color: string;
}) {
  const w = (v: number) => `${(v / Math.max(1, max)) * 100}%`;
  return (
    <div
      className="flex h-2.5 w-full overflow-hidden rounded-full bg-surface-3"
      aria-hidden
    >
      <span
        className="h-full"
        style={{ width: w(Math.max(0, input - cached)), background: color }}
      />
      <span
        className="h-full"
        style={{
          width: w(Math.min(cached, input)),
          background: color,
          opacity: 0.45,
        }}
      />
      <span
        className="h-full"
        style={{
          width: w(Math.max(0, output - reasoning)),
          background: "var(--series-5)",
        }}
      />
      <span
        className="h-full"
        style={{
          width: w(Math.min(reasoning, output)),
          background: "var(--series-5)",
          opacity: 0.45,
        }}
      />
    </div>
  );
}

/** The card's small area: calls per hour, failures as red dots on the line. */
export function MiniArea({
  points,
  color,
  label,
  height = 44,
}: {
  points: { calls: number; errors: number }[];
  color: string;
  label: string;
  height?: number;
}) {
  const id = useId().replace(/:/g, "");
  const [ref, width] = useWidth<HTMLDivElement>(240);
  const max = Math.max(1, ...points.map((p) => p.calls));
  const step = width / Math.max(1, points.length - 1);
  const y = (v: number) => height - 3 - (v / max) * (height - 8);
  const line = points
    .map((p, i) => `${(i * step).toFixed(1)},${y(p.calls).toFixed(1)}`)
    .join(" ");
  return (
    <div ref={ref} className="w-full">
      <svg
        width={width}
        height={height}
        role="img"
        aria-label={label}
        className="block overflow-visible"
      >
        <defs>
          <linearGradient id={`m${id}`} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0" stopColor={color} stopOpacity="0.32" />
            <stop offset="1" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>
        <line
          x1="0"
          x2={width}
          y1={height - 1}
          y2={height - 1}
          stroke="var(--chart-grid)"
        />
        {points.length > 1 ? (
          <>
            <polygon
              points={`0,${height - 1} ${line} ${width},${height - 1}`}
              fill={`url(#m${id})`}
            />
            <polyline
              points={line}
              fill="none"
              stroke={color}
              strokeWidth="1.75"
              strokeLinejoin="round"
              strokeLinecap="round"
            />
            {points.map((p, i) =>
              p.errors ? (
                <circle
                  key={i}
                  cx={i * step}
                  cy={y(p.calls)}
                  r="2.6"
                  fill="var(--status-critical)"
                  stroke="var(--surface-1)"
                  strokeWidth="1.2"
                />
              ) : null,
            )}
          </>
        ) : null}
      </svg>
    </div>
  );
}
