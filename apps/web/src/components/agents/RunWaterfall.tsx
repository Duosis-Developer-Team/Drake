"use client";

/**
 * One run on one timeline: each step a row under its parent, its bar where
 * and how long it ran. Model calls take their provider's colour, tools the
 * tool colour, failures are red. Rows are buttons: select one for its
 * details; a step with children folds.
 */

import {
  Bot,
  ChevronDown,
  ChevronRight,
  Cpu,
  Plug,
  Server,
  Sparkles,
  Wrench,
} from "lucide-react";
import { useMemo, useState } from "react";

import {
  compact,
  providerColor,
  providerName,
  runRows,
  STEP_COLOR,
  stepLabel,
  type RunSpan,
  type Step,
} from "@/lib/agents";
import { useFormat, useLocale, useT } from "@/lib/i18n";

export const STEP_ICON: Record<Step, typeof Bot> = {
  agent: Bot,
  model: Sparkles,
  tool: Wrench,
  mcp: Plug,
  genai: Cpu,
  other: Server,
};

/** Steps keep one colour per kind everywhere (runs, activity, time share);
 *  a model call's provider is the small dot beside its name. */
export function stepColor(span: RunSpan): string {
  return STEP_COLOR[span.step];
}

function ticks(total: number): number[] {
  return [0, 0.25, 0.5, 0.75, 1].map((f) => f * total);
}

export function RunWaterfall({
  spans,
  total,
  selected,
  onSelect,
}: {
  spans: RunSpan[];
  total: number;
  selected: string | null;
  onSelect: (spanId: string) => void;
}) {
  const t = useT("agents");
  const fmt = useFormat();
  const { locale } = useLocale();
  const rows = useMemo(() => runRows(spans), [spans]);
  const [folded, setFolded] = useState<Set<string>>(new Set());
  const shown = rows.filter((r) => !r.path.some((id) => folded.has(id)));
  const span = Math.max(1, total);
  const pct = (ms: number) => `${(ms / span) * 100}%`;

  const toggle = (id: string) =>
    setFolded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <div className="overflow-x-auto" data-testid="run-waterfall">
      <div className="min-w-[44rem]">
        <div className="grid grid-cols-[minmax(14rem,2fr)_minmax(0,3fr)] border-b border-border pb-1.5 text-micro text-ink-muted">
          <span>{t("run.steps")}</span>
          <div className="relative h-4">
            {ticks(span).map((v, i) => (
              <span
                key={i}
                className="absolute top-0 whitespace-nowrap tabular-nums"
                style={{
                  left: pct(v),
                  transform:
                    i === 0
                      ? "none"
                      : i === 4
                        ? "translateX(-100%)"
                        : "translateX(-50%)",
                }}
              >
                {fmt.latency(v)}
              </span>
            ))}
          </div>
        </div>
        <ul role="list">
          {shown.map(({ span: s, depth, children }) => {
            const Icon = STEP_ICON[s.step];
            const color = stepColor(s);
            const active = selected === s.span_id;
            const isFolded = folded.has(s.span_id);
            const failed = s.status === "error";
            const tokens = s.input_tokens + s.output_tokens;
            const sub =
              s.step === "model"
                ? providerName(s.provider)
                : s.step === "agent" && s.agent && !s.agent.self
                  ? t("run.kind.agent")
                  : s.step === "other"
                    ? s.service
                    : s.server;
            const left = (s.offset_ms / span) * 100;
            const width = Math.max(0.4, (s.duration_ms / span) * 100);
            const labelInside = left + width > 78;
            return (
              <li
                key={s.span_id}
                className={`group grid grid-cols-[minmax(14rem,2fr)_minmax(0,3fr)] items-center border-b border-border/60 ${
                  active ? "bg-brand-soft/60" : "hover:bg-surface-hover"
                }`}
              >
                <div
                  className="flex min-w-0 items-center gap-1.5 py-1.5 pr-3"
                  style={{ paddingLeft: depth * 16 }}
                >
                  {children > 0 ? (
                    <button
                      type="button"
                      onClick={() => toggle(s.span_id)}
                      aria-label={
                        isFolded ? t("run.expand") : t("run.collapse")
                      }
                      aria-expanded={!isFolded}
                      className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded text-ink-muted hover:bg-surface-3 hover:text-ink"
                    >
                      {isFolded ? (
                        <ChevronRight className="h-3.5 w-3.5" />
                      ) : (
                        <ChevronDown className="h-3.5 w-3.5" />
                      )}
                    </button>
                  ) : (
                    <span className="w-5 shrink-0" />
                  )}
                  <button
                    type="button"
                    onClick={() => onSelect(s.span_id)}
                    aria-pressed={active}
                    data-testid={`step-${s.span_id}`}
                    className="flex min-w-0 flex-1 items-center gap-2 text-left"
                  >
                    <span
                      className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md"
                      style={{
                        background: `color-mix(in srgb, ${color} 16%, transparent)`,
                        color,
                      }}
                    >
                      <Icon aria-hidden className="h-3.5 w-3.5" />
                    </span>
                    <span className="min-w-0">
                      <span
                        className={`block truncate text-caption font-medium ${failed ? "text-critical" : "text-ink"} ${
                          s.step === "model" || s.step === "tool"
                            ? "font-mono"
                            : ""
                        }`}
                      >
                        {stepLabel(s)}
                      </span>
                      {sub ? (
                        <span className="flex min-w-0 items-center gap-1 text-micro text-ink-muted">
                          {s.step === "model" && s.provider ? (
                            <span
                              aria-hidden
                              className="h-1.5 w-1.5 shrink-0 rounded-full"
                              style={{ background: providerColor(s.provider) }}
                            />
                          ) : null}
                          <span className="truncate">{sub}</span>
                        </span>
                      ) : null}
                    </span>
                  </button>
                </div>
                <button
                  type="button"
                  onClick={() => onSelect(s.span_id)}
                  tabIndex={-1}
                  aria-hidden
                  className="relative h-9 w-full"
                >
                  {[0.25, 0.5, 0.75].map((f) => (
                    <span
                      key={f}
                      className="absolute inset-y-0 w-px bg-[var(--chart-grid)]"
                      style={{ left: `${f * 100}%` }}
                    />
                  ))}
                  <span
                    className={`absolute top-1/2 h-3.5 -translate-y-1/2 rounded-[3px] ${failed ? "ring-2 ring-[var(--status-critical)]" : ""}`}
                    style={{
                      left: `${left}%`,
                      width: `${width}%`,
                      minWidth: 3,
                      background: failed ? "var(--status-critical)" : color,
                      opacity: active || selected === null ? 1 : 0.75,
                    }}
                  />
                  <span
                    className="absolute top-1/2 -translate-y-1/2 whitespace-nowrap px-1.5 text-micro tabular-nums text-ink-secondary"
                    style={
                      labelInside
                        ? { right: `${100 - left}%` }
                        : { left: `calc(${left + width}% + 2px)` }
                    }
                  >
                    {fmt.latency(s.duration_ms)}
                    {tokens ? ` · ${compact(tokens, locale)} tok` : ""}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

export function StepDetail({
  span,
  onOpenAgent,
}: {
  span: RunSpan | null;
  onOpenAgent?: (id: string) => void;
}) {
  const t = useT("agents");
  const fmt = useFormat();
  if (!span)
    return (
      <p className="py-6 text-center text-caption text-ink-muted">
        {t("run.detail.none")}
      </p>
    );
  const Icon = STEP_ICON[span.step];
  const color = stepColor(span);
  const rows: [string, React.ReactNode, boolean?][] = [
    [t("run.detail.kind"), t.dyn("run.kind", span.step, span.step)],
    [t("run.detail.agent"), span.agent?.name ?? null],
    [t("run.detail.service"), span.service, true],
    [
      t("run.detail.provider"),
      span.provider ? providerName(span.provider) : null,
    ],
    [t("run.detail.model"), span.model, true],
    [
      t("run.detail.input"),
      span.input_tokens
        ? `${fmt.number(span.input_tokens)}${span.cache_read_tokens ? ` (${fmt.number(span.cache_read_tokens)} ${t("run.detail.cached")})` : ""}`
        : null,
    ],
    [
      t("run.detail.output"),
      span.output_tokens
        ? `${fmt.number(span.output_tokens)}${span.reasoning_tokens ? ` (${fmt.number(span.reasoning_tokens)} ${t("run.detail.reasoning")})` : ""}`
        : null,
    ],
    [t("run.detail.tool"), span.tool, true],
    [t("run.detail.toolType"), span.tool_type],
    [t("run.detail.mcp"), span.mcp_method, true],
    [t("run.detail.server"), span.server, true],
    [
      t("run.detail.http"),
      [span.http_status, span.http_route].filter(Boolean).join(" ") || null,
      true,
    ],
    [t("run.detail.start"), `+${fmt.latency(span.offset_ms)}`],
    [t("run.detail.duration"), fmt.latency(span.duration_ms)],
    [t("run.detail.span"), span.name, true],
  ];
  return (
    <div className="space-y-4" data-testid="step-detail">
      <div className="flex items-start gap-3">
        <span
          className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-control"
          style={{
            background: `color-mix(in srgb, ${color} 16%, transparent)`,
            color,
          }}
        >
          <Icon aria-hidden className="h-4.5 w-4.5" />
        </span>
        <div className="min-w-0">
          <p className="truncate font-mono text-body font-semibold text-ink">
            {stepLabel(span)}
          </p>
          <p
            className={`text-micro font-medium ${span.status === "error" ? "text-critical" : "text-healthy"}`}
          >
            {span.status === "error"
              ? `${t("run.detail.failed")}${span.error_type ? ` · ${span.error_type}` : ""}`
              : t("run.detail.ok")}
          </p>
        </div>
      </div>
      <dl className="space-y-2.5">
        {rows
          .filter(([, v]) => v !== null && v !== undefined && v !== "")
          .map(([k, v, mono]) => (
            <div
              key={k}
              className="grid grid-cols-[7rem_minmax(0,1fr)] gap-2 text-caption"
            >
              <dt className="text-ink-muted">{k}</dt>
              <dd
                className={`break-all font-medium text-ink ${mono ? "font-mono text-micro leading-5" : ""}`}
              >
                {v}
              </dd>
            </div>
          ))}
      </dl>
      {span.agent && !span.agent.self && onOpenAgent ? (
        <button
          type="button"
          onClick={() => onOpenAgent(span.agent!.id)}
          className="text-caption font-medium text-brand hover:underline"
        >
          {t("run.detail.openAgent")} →
        </button>
      ) : null}
    </div>
  );
}
