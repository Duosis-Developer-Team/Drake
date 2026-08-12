/**
 * Status indicators.
 *
 * Three shapes over the one vocabulary in `lib/design/status`:
 *
 *   `StatusBadge`     a chip with an icon and a word — for a row, a header
 *   `StatusDot`       a dot with a word — for dense tables, where a chip per
 *                     cell turns the column into confetti
 *   `HealthIndicator` icon + word + optional detail — for a panel's summary
 *
 * None of them can be colour-only: the icon and the label are not optional,
 * because a colour-blind reader, a greyscale print and a screen reader all
 * need the state to survive without hue.
 */

import type { StatusTone } from "@/lib/design/status";
import { humanize, toneForHealth, toneSpec } from "@/lib/design/status";

export type { StatusTone };

/** Drake's long-standing badge vocabulary, mapped onto the shared tones. */
export type HealthStatus =
  | "healthy"
  | "warning"
  | "critical"
  | "unknown"
  | "stale"
  | "maintenance";

const LEGACY_TONE: Record<HealthStatus, StatusTone> = {
  healthy: "success",
  warning: "warning",
  critical: "critical",
  unknown: "unknown",
  stale: "stale",
  maintenance: "info",
};

function resolve(status: HealthStatus | StatusTone): StatusTone {
  return (LEGACY_TONE as Record<string, StatusTone>)[status] ?? (status as StatusTone);
}

export function StatusBadge({
  status,
  label,
  size = "default",
}: {
  status: HealthStatus | StatusTone;
  label?: string;
  size?: "default" | "compact";
}) {
  const tone = resolve(status);
  const spec = toneSpec(tone);
  const Icon = spec.icon;
  return (
    <span
      data-testid={`status-${status}`}
      data-tone={tone}
      className={`inline-flex max-w-full items-center gap-1.5 rounded-full font-medium ${
        size === "compact" ? "px-1.5 py-0.5 text-micro" : "px-2 py-0.5 text-caption"
      } ${spec.chip}`}
    >
      <Icon aria-hidden className="h-3.5 w-3.5 shrink-0" />
      <span className="truncate">{label ?? spec.label}</span>
    </span>
  );
}

/** The dense-table variant: same meaning, a fraction of the visual weight. */
export function StatusDot({
  status,
  label,
}: {
  status: HealthStatus | StatusTone;
  label?: string;
}) {
  const tone = resolve(status);
  const spec = toneSpec(tone);
  return (
    <span
      data-testid={`status-dot-${tone}`}
      className="inline-flex items-center gap-1.5 text-caption text-ink"
    >
      <span aria-hidden className={`h-2 w-2 shrink-0 rounded-full ${spec.dot}`} />
      {label ?? spec.label}
    </span>
  );
}

export function HealthIndicator({
  status,
  label,
  detail,
}: {
  status: HealthStatus | StatusTone;
  label?: string;
  detail?: React.ReactNode;
}) {
  const tone = resolve(status);
  const spec = toneSpec(tone);
  const Icon = spec.icon;
  return (
    <span className="inline-flex items-start gap-2">
      <Icon aria-hidden className={`mt-0.5 h-4 w-4 shrink-0 ${spec.text}`} />
      <span className="min-w-0">
        <span className={`text-body font-medium ${spec.text}`}>{label ?? spec.label}</span>
        {detail ? <span className="block text-caption text-ink-secondary">{detail}</span> : null}
      </span>
    </span>
  );
}

/** A raw backend health word, badged without the caller mapping it first. */
export function HealthWord({ value, label }: { value: string | null | undefined; label?: string }) {
  return <StatusBadge status={toneForHealth(value)} label={label ?? humanize(value)} />;
}
