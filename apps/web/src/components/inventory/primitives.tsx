"use client";

/** Cluster inventory UI primitives. Honesty rules from ADR-0011/0017:
 * unknown, stale, reconciling, empty, and reconcile-required are DISTINCT
 * visual states; stale and unknown are never rendered in the healthy
 * color; counts always include the unknown bucket. */

import { StatusBadge, type HealthStatus } from "@/components/state/StatusBadge";
import type {
  AgentStatus,
  InventoryState,
  ResourceHealth,
} from "@/lib/inventory";

const AGENT_BADGE: Record<AgentStatus, { status: HealthStatus; label: string }> = {
  not_configured: { status: "unknown", label: "not configured" },
  enrolled: { status: "maintenance", label: "enrolled" },
  connected: { status: "healthy", label: "connected" },
  disconnected: { status: "warning", label: "disconnected" },
  revoked: { status: "critical", label: "revoked" },
};

export function AgentBadge({ status }: { status: AgentStatus | string | undefined }) {
  const spec = AGENT_BADGE[(status as AgentStatus) ?? "not_configured"] ?? {
    status: "unknown" as HealthStatus,
    label: String(status ?? "unknown"),
  };
  return <StatusBadge status={spec.status} label={spec.label} />;
}

const INVENTORY_BADGE: Record<InventoryState, { status: HealthStatus; label: string }> = {
  not_configured: { status: "unknown", label: "not configured" },
  empty: { status: "unknown", label: "empty" },
  reconciling: { status: "maintenance", label: "reconciling" },
  fresh: { status: "healthy", label: "fresh" },
  // Stale is stale — NEVER the healthy color, even though data exists.
  stale: { status: "stale", label: "stale" },
  reconcile_required: { status: "warning", label: "reconcile required" },
};

export function InventoryStateBadge({ state }: { state: InventoryState | string | undefined }) {
  const spec = INVENTORY_BADGE[(state as InventoryState) ?? "not_configured"] ?? {
    status: "unknown" as HealthStatus,
    label: String(state ?? "unknown"),
  };
  return <StatusBadge status={spec.status} label={spec.label} />;
}

const HEALTH_BADGE: Record<ResourceHealth, { status: HealthStatus; label: string }> = {
  healthy: { status: "healthy", label: "healthy" },
  degraded: { status: "warning", label: "degraded" },
  unhealthy: { status: "critical", label: "unhealthy" },
  unknown: { status: "unknown", label: "unknown" },
};

export function HealthBadge({ health }: { health: ResourceHealth | string }) {
  const spec = HEALTH_BADGE[(health as ResourceHealth) ?? "unknown"] ?? HEALTH_BADGE.unknown;
  return <StatusBadge status={spec.status} label={spec.label} />;
}

export function formatUtc(value: string | null | undefined): string {
  if (!value) return "—";
  try {
    return new Date(value).toISOString().replace(".000Z", "Z");
  } catch {
    return value;
  }
}
