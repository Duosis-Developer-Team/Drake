"use client";

/**
 * Presentational building blocks shared by the Protection and Onboarding
 * screens: KPI tiles, designed state cards, a definition grid, pill selects
 * and a step indicator. Nothing here fetches or decides — every value comes
 * in from the caller.
 */

import { ChevronDown, type LucideIcon } from "lucide-react";
import { useId } from "react";

import { Panel } from "@/components/ui/Panel";
import { toneSpec, type StatusTone } from "@/lib/design/status";
import { useT } from "@/lib/i18n";

/** A round tinted bubble around an icon. `neutral` reads as a quiet surface. */
export function IconBubble({
  icon: Icon,
  tone,
  size = "default",
}: {
  icon: LucideIcon;
  tone?: StatusTone;
  size?: "compact" | "default" | "large";
}) {
  const box =
    size === "compact" ? "h-9 w-9" : size === "large" ? "h-14 w-14" : "h-11 w-11";
  const glyph =
    size === "compact" ? "h-4 w-4" : size === "large" ? "h-6 w-6" : "h-[1.125rem] w-[1.125rem]";
  const colour = tone ? toneSpec(tone).chip : "border border-border bg-surface-2 text-ink-secondary";
  return (
    <span
      aria-hidden
      className={`flex shrink-0 items-center justify-center rounded-full ${box} ${colour}`}
    >
      <Icon className={glyph} />
    </span>
  );
}

/**
 * One headline number. The mini bar underneath shows `part of whole` when the
 * caller supplies a whole; with no whole (or a whole of zero) the bar stays an
 * empty track rather than implying a share.
 */
export function KpiTile({
  icon,
  label,
  value,
  tone,
  part,
  whole,
  caption,
  "data-testid": testId,
}: {
  icon: LucideIcon;
  label: string;
  value: React.ReactNode;
  tone?: StatusTone;
  part?: number;
  whole?: number;
  caption?: React.ReactNode;
  "data-testid"?: string;
}) {
  const share =
    part !== undefined && whole !== undefined && whole > 0
      ? Math.max(0, Math.min(1, part / whole))
      : null;
  const bar = tone ? toneSpec(tone).dot : "bg-ink";
  return (
    <Panel className="h-full !gap-5" data-testid={testId}>
      <div className="flex items-start justify-between gap-3">
        <p className="pt-1 text-caption font-medium text-ink-secondary">{label}</p>
        <IconBubble icon={icon} tone={tone} />
      </div>
      <p
        data-tabular
        className="text-[2.25rem] leading-none font-semibold tracking-[-0.03em] text-ink"
      >
        {value}
      </p>
      <div className="mt-auto space-y-2">
        {whole !== undefined ? (
          <span aria-hidden className="block h-1.5 overflow-hidden rounded-full bg-surface-3">
            <span
              className={`block h-full rounded-full ${bar}`}
              style={{ width: `${share === null ? 0 : Math.max(share * 100, share > 0 ? 4 : 0)}%` }}
            />
          </span>
        ) : null}
        {caption ? <p className="text-micro text-ink-muted">{caption}</p> : null}
      </div>
    </Panel>
  );
}

export type StateKind = "empty" | "unknown" | "permission-denied" | "error" | "not-configured" | "stale" | "partial";

/** Tone and test id per state; the copy lives in `protection.states.*`. */
const STATE_META: Record<
  StateKind,
  { tone: StatusTone; testId: string; copy: "empty" | "unknown" | "permissionDenied" | "error" | "notConfigured" | "stale" | "partial" }
> = {
  empty: { tone: "neutral", testId: "state-empty", copy: "empty" },
  unknown: { tone: "unknown", testId: "state-unknown", copy: "unknown" },
  "permission-denied": { tone: "denied", testId: "state-permission-denied", copy: "permissionDenied" },
  error: { tone: "critical", testId: "state-error", copy: "error" },
  "not-configured": { tone: "unknown", testId: "state-not-configured", copy: "notConfigured" },
  stale: { tone: "stale", testId: "state-stale", copy: "stale" },
  partial: { tone: "warning", testId: "state-partial", copy: "partial" },
};

/**
 * A designed state: icon medallion, a title, one line, an optional action.
 * Keeps the `state-*` test ids of the shared state primitives, so "empty",
 * "denied" and "failed" stay three distinguishable answers.
 */
export function StateCard({
  kind,
  icon,
  title,
  description,
  action,
  align = "center",
  children,
}: {
  kind: StateKind;
  icon?: LucideIcon;
  title?: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
  align?: "center" | "start";
  children?: React.ReactNode;
}) {
  const t = useT("protection");
  const meta = STATE_META[kind];
  const spec = toneSpec(meta.tone);
  const Icon = icon ?? spec.icon;
  const centered = align === "center";
  return (
    <div
      role="status"
      data-testid={meta.testId}
      className={`flex gap-4 ${
        centered ? "flex-col items-center px-6 py-10 text-center" : "items-start py-1"
      }`}
    >
      <span
        aria-hidden
        className={`flex shrink-0 items-center justify-center rounded-full ${
          centered ? "h-14 w-14 ring-8 ring-surface-2" : "h-11 w-11"
        } ${meta.tone === "neutral" || meta.tone === "denied" ? "bg-surface-3 text-ink-muted" : spec.chip}`}
      >
        <Icon className={centered ? "h-6 w-6" : "h-5 w-5"} />
      </span>
      <div className={`min-w-0 ${centered ? "max-w-md" : "flex-1"}`}>
        <p className="text-body font-semibold text-ink">{title ?? t(`states.${meta.copy}.title`)}</p>
        <p className="mt-1 text-caption text-ink-secondary">
          {description ?? t(`states.${meta.copy}.description`)}
        </p>
        {children ? <div className="mt-3">{children}</div> : null}
        {action ? (
          <div className={`mt-4 flex ${centered ? "justify-center" : ""}`}>{action}</div>
        ) : null}
      </div>
    </div>
  );
}

/** A clean two-column definition grid for metadata. */
export function DefGrid({
  items,
  columns = 2,
}: {
  items: { label: string; value: React.ReactNode; mono?: boolean }[];
  columns?: 1 | 2;
}) {
  return (
    <dl className={`grid gap-3 ${columns === 2 ? "sm:grid-cols-2" : ""}`}>
      {items.map((item) => (
        <div key={item.label} className="min-w-0 rounded-2xl bg-surface-2 px-4 py-3">
          <dt className="text-micro tracking-[0.08em] text-ink-muted uppercase">{item.label}</dt>
          <dd
            className={`mt-1 min-w-0 text-body font-medium break-words text-ink ${
              item.mono ? "font-mono text-caption" : ""
            }`}
          >
            {item.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** A labelled native select drawn as one pill. */
export function PillSelect<T extends string>({
  label,
  value,
  options,
  placeholder,
  onChange,
}: {
  label: string;
  value: T | "";
  options: { value: T; label: string }[];
  placeholder: string;
  onChange: (value: T | "") => void;
}) {
  const id = useId();
  const active = value !== "";
  return (
    <div
      className={`relative inline-flex h-10 items-center gap-2 rounded-full border pr-9 pl-4 focus-within:ring-2 focus-within:ring-focus transition-colors ${
        active ? "border-ink/25 bg-surface-3" : "border-border bg-surface hover:bg-surface-hover"
      }`}
    >
      <label htmlFor={id} className="shrink-0 text-caption text-ink-muted">
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value as T | "")}
        className="min-w-0 cursor-pointer appearance-none bg-transparent text-caption font-semibold text-ink outline-none"
      >
        <option value="">{placeholder}</option>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      <ChevronDown
        aria-hidden
        className="pointer-events-none absolute top-1/2 right-3.5 h-4 w-4 -translate-y-1/2 text-ink-muted"
      />
    </div>
  );
}

export type StepStatus = "done" | "current" | "blocked" | "upcoming";

/**
 * A horizontal step indicator: numbered nodes joined by a track. A finished
 * step fills the track after it.
 */
export function Stepper({
  steps,
  "data-testid": testId,
  compact = false,
}: {
  steps: { label: string; status: StepStatus; hint?: string }[];
  "data-testid"?: string;
  compact?: boolean;
}) {
  return (
    <ol
      data-testid={testId}
      className="grid gap-y-5"
      style={{ gridTemplateColumns: `repeat(${steps.length}, minmax(0, 1fr))` }}
    >
      {steps.map((step, index) => {
        const last = index === steps.length - 1;
        const node =
          step.status === "done"
            ? "bg-brand text-ink-inverse"
            : step.status === "current"
              ? "bg-surface text-ink ring-2 ring-ink"
              : step.status === "blocked"
                ? `${toneSpec("warning").chip}`
                : "bg-surface-3 text-ink-muted";
        return (
          <li
            key={step.label}
            aria-current={step.status === "current" ? "step" : undefined}
            className="relative flex min-w-0 flex-col items-center text-center"
          >
            {last ? null : (
              <span
                aria-hidden
                className={`absolute top-[1.125rem] left-[calc(50%+1.5rem)] h-0.5 w-[calc(100%-3rem)] rounded-full ${
                  step.status === "done" ? "bg-ink" : "bg-surface-3"
                }`}
              />
            )}
            <span
              className={`relative flex h-9 w-9 items-center justify-center rounded-full text-caption font-semibold ${node}`}
            >
              {step.status === "done" ? "✓" : index + 1}
            </span>
            <span
              className={`mt-3 px-1 font-medium ${compact ? "text-micro" : "text-caption"} ${
                step.status === "upcoming" ? "text-ink-muted" : "text-ink"
              }`}
            >
              {step.label}
            </span>
            {step.hint ? (
              <span className="mt-0.5 hidden px-1 text-micro text-ink-muted lg:block">{step.hint}</span>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}

/** A small chip carrying a label and a value, for row facts. */
export function FactChip({
  label,
  children,
  tone,
}: {
  label: string;
  children: React.ReactNode;
  tone?: StatusTone;
}) {
  return (
    <span
      className={`inline-flex max-w-full items-center gap-1.5 rounded-full px-3 py-1 text-micro ${
        tone ? toneSpec(tone).chip : "bg-surface-2 text-ink-secondary"
      }`}
    >
      <span className={tone ? "opacity-80" : "text-ink-muted"}>{label}</span>
      <span data-tabular className="truncate font-semibold">
        {children}
      </span>
    </span>
  );
}
