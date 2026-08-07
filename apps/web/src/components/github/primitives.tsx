"use client";

/** GitHub integration UI primitives.
 *
 * The honesty rules from ADR-0011/0020 hold here too: blocked, degraded,
 * disabled and unknown are DISTINCT visual states, and none of them
 * borrows the healthy colour. A repository we could not evaluate never
 * looks like one that passed.
 */

import { StatusBadge, type HealthStatus } from "@/components/state/StatusBadge";
import type { OnboardingState, PolicyVerdict } from "@/lib/github";

const ONBOARDING_BADGE: Record<OnboardingState, { status: HealthStatus; label: string }> = {
  discovered: { status: "unknown", label: "discovered" },
  validating: { status: "maintenance", label: "validating" },
  ready: { status: "healthy", label: "ready" },
  // Blocked is a decision, degraded is a wobble — they must not look alike.
  blocked: { status: "critical", label: "blocked" },
  degraded: { status: "warning", label: "degraded" },
  disabled: { status: "stale", label: "disabled" },
};

export function OnboardingBadge({ state }: { state: OnboardingState | string | undefined }) {
  const spec = ONBOARDING_BADGE[(state as OnboardingState) ?? "discovered"] ?? {
    status: "unknown" as HealthStatus,
    label: String(state ?? "unknown"),
  };
  return <StatusBadge status={spec.status} label={spec.label} />;
}

const VERDICT_BADGE: Record<PolicyVerdict, { status: HealthStatus; label: string }> = {
  pass: { status: "healthy", label: "pass" },
  warn: { status: "warning", label: "warn" },
  fail: { status: "critical", label: "fail" },
  unknown: { status: "unknown", label: "unknown" },
};

export function VerdictBadge({ verdict }: { verdict: PolicyVerdict | string | undefined }) {
  const spec = VERDICT_BADGE[(verdict as PolicyVerdict) ?? "unknown"] ?? VERDICT_BADGE.unknown;
  return <StatusBadge status={spec.status} label={spec.label} />;
}

const INSTALLATION_BADGE: Record<string, { status: HealthStatus; label: string }> = {
  active: { status: "healthy", label: "active" },
  suspended: { status: "warning", label: "suspended" },
  deleted: { status: "stale", label: "deleted" },
};

export function InstallationBadge({ state }: { state: string | undefined }) {
  const spec = INSTALLATION_BADGE[state ?? ""] ?? {
    status: "unknown" as HealthStatus,
    label: String(state ?? "unknown"),
  };
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
