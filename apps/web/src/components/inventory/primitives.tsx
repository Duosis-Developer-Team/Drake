"use client";

/** Cluster inventory UI primitives. Honesty rules from ADR-0011/0017:
 * unknown, stale, reconciling, empty, and reconcile-required are DISTINCT
 * visual states; stale and unknown are never rendered in the healthy
 * color; counts always include the unknown bucket.
 *
 * The words come from `clusters.enum.*`; only the tone is decided here. The
 * badges print them lowercased (by locale), as they always have: inside a
 * card they read as a state word, not as a heading. */

import { StatusBadge, type HealthStatus } from "@/components/state/StatusBadge";
import { useFormat, useT } from "@/lib/i18n";
import type {
  AgentStatus,
  InventoryState,
  ResourceHealth,
} from "@/lib/inventory";

const AGENT_TONE: Record<AgentStatus, HealthStatus> = {
  not_configured: "unknown",
  enrolled: "maintenance",
  connected: "healthy",
  disconnected: "warning",
  revoked: "critical",
};

export function AgentBadge({ status }: { status: AgentStatus | string | undefined }) {
  const t = useT("clusters");
  const fmt = useFormat();
  const token = status ?? "not_configured";
  const tone = AGENT_TONE[token as AgentStatus] ?? ("unknown" as HealthStatus);
  return (
    <StatusBadge status={tone} label={t.dyn("enum.agent", token).toLocaleLowerCase(fmt.tag)} />
  );
}

const INVENTORY_TONE: Record<InventoryState, HealthStatus> = {
  not_configured: "unknown",
  empty: "unknown",
  reconciling: "maintenance",
  fresh: "healthy",
  // Stale is stale — NEVER the healthy color, even though data exists.
  stale: "stale",
  reconcile_required: "warning",
};

export function InventoryStateBadge({ state }: { state: InventoryState | string | undefined }) {
  const t = useT("clusters");
  const fmt = useFormat();
  const token = state ?? "not_configured";
  const tone = INVENTORY_TONE[token as InventoryState] ?? ("unknown" as HealthStatus);
  return (
    <StatusBadge status={tone} label={t.dyn("enum.inventory", token).toLocaleLowerCase(fmt.tag)} />
  );
}

const HEALTH_TONE: Record<ResourceHealth, HealthStatus> = {
  healthy: "healthy",
  degraded: "warning",
  unhealthy: "critical",
  unknown: "unknown",
};

export function HealthBadge({ health }: { health: ResourceHealth | string }) {
  const t = useT("clusters");
  const fmt = useFormat();
  const token = HEALTH_TONE[health as ResourceHealth] ? health : "unknown";
  return (
    <StatusBadge
      status={HEALTH_TONE[token as ResourceHealth]}
      label={t.dyn("enum.health", token).toLocaleLowerCase(fmt.tag)}
    />
  );
}
