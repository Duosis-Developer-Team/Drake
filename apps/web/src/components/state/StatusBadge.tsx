/**
 * Semantic status badge. Color is never the only signal: every badge carries
 * its label text, and the critical red is reserved for health states only.
 */
export type HealthStatus =
  | "healthy"
  | "warning"
  | "critical"
  | "unknown"
  | "stale"
  | "maintenance";

const STATUS_CLASSES: Record<HealthStatus, { dot: string; badge: string; label: string }> = {
  healthy: { dot: "bg-healthy", badge: "bg-healthy-soft text-healthy", label: "Healthy" },
  warning: { dot: "bg-warning", badge: "bg-warning-soft text-warning", label: "Warning" },
  critical: { dot: "bg-critical", badge: "bg-critical-soft text-critical", label: "Critical" },
  unknown: { dot: "bg-unknown", badge: "bg-unknown-soft text-unknown", label: "Unknown" },
  stale: { dot: "bg-stale", badge: "bg-stale-soft text-stale", label: "Stale" },
  maintenance: {
    dot: "bg-maintenance",
    badge: "bg-maintenance-soft text-maintenance",
    label: "Maintenance",
  },
};

export function StatusBadge({ status, label }: { status: HealthStatus; label?: string }) {
  const spec = STATUS_CLASSES[status];
  return (
    <span
      data-testid={`status-${status}`}
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${spec.badge}`}
    >
      <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${spec.dot}`} />
      {label ?? spec.label}
    </span>
  );
}
