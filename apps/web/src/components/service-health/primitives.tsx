"use client";

/**
 * Service-health display primitives.
 *
 * Everything here renders a decision the API already made. There is no
 * threshold, no comparison against a limit, and no place where a status is
 * inferred from a number — if this file could compute "healthy", the
 * backend and the browser could disagree, and the screen would be the one
 * people believe.
 */

import {
  REASON_LABELS,
  SIGNAL_LABELS,
  formatSignal,
  type ServiceHealthStatus,
  type SignalValue,
} from "@/lib/serviceHealth";
import { StatusBadge, type HealthStatus } from "@/components/state/StatusBadge";
import { toneSpec, type StatusTone } from "@/lib/design/status";

/**
 * A round icon bubble, tinted by tone. The leading mark on rows, KPI cards
 * and state cards — the same 40px circle the Command Center uses.
 */
export function IconBubble({
  icon: Icon,
  tone,
  size = "md",
}: {
  icon: React.ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
  tone?: StatusTone;
  size?: "md" | "lg";
}) {
  const box = size === "lg" ? "h-14 w-14" : "h-10 w-10";
  const glyph = size === "lg" ? "h-6 w-6" : "h-[1.125rem] w-[1.125rem]";
  return (
    <span
      aria-hidden
      className={`flex ${box} shrink-0 items-center justify-center rounded-full ${
        tone ? toneSpec(tone).chip : "border border-border bg-surface-2 text-ink-secondary"
      }`}
    >
      <Icon className={glyph} aria-hidden />
    </span>
  );
}

/**
 * A designed empty / denied / error block: bubble, title, one line, and an
 * optional action. Never a bare sentence floating in a card.
 */
export function StateCard({
  icon,
  tone,
  title,
  description,
  action,
  children,
}: {
  icon: React.ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
  tone?: StatusTone;
  title: React.ReactNode;
  description?: React.ReactNode;
  action?: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center px-6 py-12 text-center">
      <span className="rounded-full bg-surface-2 p-2">
        <IconBubble icon={icon} tone={tone} size="lg" />
      </span>
      <p className="mt-5 text-[1.0625rem] leading-6 font-semibold tracking-[-0.01em] text-ink">
        {title}
      </p>
      {description ? (
        <p className="mt-1 max-w-md text-caption text-ink-muted">{description}</p>
      ) : null}
      {children ? <div className="mt-4 w-full max-w-md text-left">{children}</div> : null}
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  );
}

/** A thin fill bar for a bounded fraction. `null` draws an empty track. */
export function MiniMeter({
  fraction,
  tone = "neutral",
  className = "",
}: {
  fraction: number | null;
  tone?: StatusTone;
  className?: string;
}) {
  const known = fraction !== null && !Number.isNaN(fraction);
  const width = known ? Math.max(0, Math.min(1, fraction)) * 100 : 0;
  return (
    <span aria-hidden className={`block h-1.5 overflow-hidden rounded-full bg-surface-3 ${className}`}>
      {known && width > 0 ? (
        <span
          className={`block h-full rounded-full ${toneSpec(tone).dot}`}
          style={{ width: `${Math.max(width, 4)}%` }}
        />
      ) : null}
    </span>
  );
}

/** Pill link / button classes, shared so every action on these screens matches. */
export const PILL_SECONDARY =
  "inline-flex items-center gap-1.5 rounded-full border border-border bg-surface px-3.5 py-1.5 text-caption font-medium text-ink transition-colors hover:bg-surface-hover disabled:cursor-not-allowed disabled:opacity-50";
export const PILL_PRIMARY =
  "inline-flex items-center gap-1.5 rounded-full bg-brand px-4 py-2 text-caption font-medium text-ink-inverse transition-colors hover:bg-brand-hover disabled:cursor-not-allowed disabled:opacity-50";

/** API status → badge vocabulary. A pure renaming: no status is merged
 * into another, and `not_configured` never borrows the healthy colour. It
 * uses the same quiet "not applicable" tone the Command Center tiles use, so
 * one service reads the same colour on both screens. */
const BADGE_STATUS: Record<ServiceHealthStatus, HealthStatus | StatusTone> = {
  healthy: "healthy",
  degraded: "warning",
  critical: "critical",
  unknown: "unknown",
  stale: "stale",
  not_configured: "not-applicable",
};

/** The shared tone for one API status — the same mapping the badge uses. */
export const STATUS_TONE: Record<ServiceHealthStatus, StatusTone> = {
  healthy: "success",
  degraded: "warning",
  critical: "critical",
  unknown: "unknown",
  stale: "stale",
  not_configured: "not-applicable",
};

export function toneForServiceStatus(status: string): StatusTone {
  return STATUS_TONE[status as ServiceHealthStatus] ?? "unknown";
}

/** Worst first — the order a triage list and a legend both follow. */
export const STATUS_ORDER: ServiceHealthStatus[] = [
  "critical",
  "degraded",
  "stale",
  "unknown",
  "not_configured",
  "healthy",
];

export const STATUS_LABELS: Record<ServiceHealthStatus, string> = {
  healthy: "Healthy",
  degraded: "Degraded",
  critical: "Critical",
  unknown: "Unknown",
  stale: "Stale",
  not_configured: "Not configured",
};

export function HealthBadge({ status }: { status: ServiceHealthStatus }) {
  return <StatusBadge status={BADGE_STATUS[status]} label={STATUS_LABELS[status]} />;
}

/** What happened to one signal, in words, when it is not a plain value. */
const SIGNAL_STATE_LABELS: Record<string, string> = {
  empty: "no data",
  failed: "query failed",
  not_configured: "no datasource",
  not_collected: "not collected",
  stale: "stale",
};

/**
 * One measurement. An absent value renders as a dash plus the reason it is
 * absent — never as `0`, and never as a blank cell that reads like zero.
 */
export function SignalCell({ signal, unit }: { signal: SignalValue; unit: string }) {
  const note = SIGNAL_STATE_LABELS[signal.state];
  return (
    <span className="inline-flex items-baseline gap-1.5" data-testid={`signal-${signal.state}`}>
      <span className="font-mono text-xs text-ink">{formatSignal(signal.value, unit)}</span>
      {note ? <span className="text-[11px] italic text-ink-muted">{note}</span> : null}
      {signal.state === "ok" && signal.from_cache ? (
        <span className="text-[11px] italic text-ink-muted">cached</span>
      ) : null}
    </span>
  );
}

/** A bare number that may be null. Same rule: a dash, never a zero. */
export function Measure({ value, unit }: { value: number | null | undefined; unit: string }) {
  return (
    <span className="font-mono text-xs text-ink">
      {formatSignal(value ?? null, unit)}
    </span>
  );
}

export function ReasonList({
  reasons,
  messages,
  tone = "warning",
  size = "compact",
}: {
  reasons: string[];
  messages?: string[];
  tone?: StatusTone;
  size?: "compact" | "roomy";
}) {
  if (reasons.length === 0) return null;
  const spec = toneSpec(tone);
  if (size === "compact") {
    return (
      <ul className="flex flex-wrap gap-1.5" data-testid="reason-list">
        {reasons.map((reason, index) => (
          <li
            key={reason}
            title={messages?.[index]}
            className="inline-flex items-center gap-1.5 rounded-full bg-surface-2 px-2.5 py-1 text-micro text-ink-secondary"
          >
            <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${spec.dot}`} />
            <span className="font-medium">{REASON_LABELS[reason] ?? reason}</span>
          </li>
        ))}
      </ul>
    );
  }
  const Icon = spec.icon;
  return (
    <ul className="space-y-2.5" data-testid="reason-list">
      {reasons.map((reason, index) => (
        <li key={reason} className="flex items-start gap-3 rounded-[1rem] bg-surface-2 px-4 py-3">
          <span
            aria-hidden
            className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${spec.chip}`}
          >
            <Icon className="h-3.5 w-3.5" />
          </span>
          <span className="min-w-0">
            <span className="block text-body font-semibold text-ink">
              {REASON_LABELS[reason] ?? reason}
            </span>
            {messages?.[index] ? (
              <span className="mt-0.5 block text-caption text-ink-muted">{messages[index]}</span>
            ) : null}
          </span>
        </li>
      ))}
    </ul>
  );
}

export function MissingSignals({ missing }: { missing: string[] }) {
  if (missing.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5" data-testid="missing-signals">
      <span className="mr-1 text-micro font-medium text-ink-secondary">Not measured:</span>
      {missing.map((name) => (
        <span
          key={name}
          className="rounded-full border border-dashed border-border px-2.5 py-0.5 text-micro text-ink-muted"
        >
          {SIGNAL_LABELS[name] ?? name}
        </span>
      ))}
    </div>
  );
}

/**
 * The banner above a partial or last-good answer.
 *
 * Shown before the numbers, not after them: a reader who takes the values
 * at face value and stops has still been told what they are looking at.
 */
export function FreshnessNotice({
  partial,
  servedFromLastGood,
  computedAt,
  servedAt,
  newestSampleAt,
}: {
  partial: boolean;
  servedFromLastGood: boolean;
  computedAt: string;
  servedAt?: string;
  newestSampleAt?: string | null;
}) {
  if (!partial && !servedFromLastGood) return null;
  const tone: StatusTone = servedFromLastGood ? "stale" : "warning";
  const Icon = toneSpec(tone).icon;
  return (
    <div
      role="status"
      data-testid="freshness-notice"
      className={`flex items-start gap-4 rounded-[1.25rem] px-5 py-4 text-caption ${toneSpec(tone).chip}`}
    >
      <span aria-hidden className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface/60">
        <Icon className="h-4 w-4" />
      </span>
      <div className="min-w-0">
        {servedFromLastGood ? (
          <p>
            <span className="font-semibold">Last known values.</span> The datasource could not
            be reached, so this is the most recent successful reading — computed{" "}
            <time className="font-mono">{computedAt}</time>
            {servedAt ? (
              <>
                {" "}
                and served <time className="font-mono">{servedAt}</time>
              </>
            ) : null}
            .
          </p>
        ) : (
          <p>
            <span className="font-semibold">Partial result.</span> Some signals were
            unavailable, so this answer does not cover everything.
          </p>
        )}
        {newestSampleAt ? (
          <p className="mt-1 opacity-80">
            Newest sample: <time className="font-mono">{newestSampleAt}</time>
          </p>
        ) : null}
      </div>
    </div>
  );
}

/** How a binding itself stands, separately from what it measured. */
export function BindingStateBadge({
  lifecycle,
  resolved,
}: {
  lifecycle: string;
  resolved: boolean;
}) {
  if (lifecycle !== "active") {
    return <StatusBadge status="maintenance" label="Disabled" />;
  }
  return resolved ? (
    <StatusBadge status="healthy" label="Resolved" />
  ) : (
    <StatusBadge status="unknown" label="Unresolved" />
  );
}
