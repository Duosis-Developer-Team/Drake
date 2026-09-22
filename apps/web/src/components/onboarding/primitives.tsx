"use client";

/**
 * Onboarding display primitives.
 *
 * Colour is assigned once, here, and two choices carry the meaning:
 *
 * Only `imported` is green. A plan that is ready, approved, or merely
 * analysed has changed nothing yet, and a green badge on any of them would
 * suggest work that has not happened.
 *
 * `stale` is a warning, not an error. Nothing went wrong — the repository
 * simply moved, and the review needs redoing against the new commit.
 *
 * The words come from `onboarding.session.*` / `action.*` / `gitops.*`; the
 * `*_LABELS` records in lib/onboarding stay the English source and the
 * fallback for a token the catalogue does not know.
 */

import { StatusBadge, type HealthStatus } from "@/components/state/StatusBadge";
import type { StatusTone } from "@/lib/design/status";
import { useT } from "@/lib/i18n";
import {
  ACTION_LABELS,
  GITOPS_LABELS,
  SESSION_LABELS,
  type GitOpsState,
  type PlanAction,
  type SessionState,
} from "@/lib/onboarding";

const SESSION_BADGE: Record<SessionState, HealthStatus> = {
  draft: "unknown",
  discovery_pending: "maintenance",
  analyzing: "maintenance",
  needs_review: "warning",
  ready: "maintenance",
  approved: "maintenance",
  applying: "maintenance",
  // The only green state: something actually reached the catalog.
  imported: "healthy",
  failed: "critical",
  cancelled: "unknown",
  not_configured: "unknown",
  stale: "warning",
  provider_unavailable: "warning",
};

const ACTION_BADGE: Record<PlanAction, HealthStatus> = {
  create: "maintenance",
  link: "maintenance",
  update_metadata: "maintenance",
  no_change: "unknown",
  conflict: "critical",
  unmapped: "warning",
  unsupported: "warning",
};

const GITOPS_BADGE: Record<GitOpsState, HealthStatus> = {
  pending: "unknown",
  active: "maintenance",
  failed: "critical",
  stale: "warning",
  cancelled: "unknown",
};

const HEALTH_TONE: Record<HealthStatus, StatusTone> = {
  healthy: "success",
  warning: "warning",
  critical: "critical",
  unknown: "unknown",
  stale: "stale",
  maintenance: "info",
};

/** The session state's tone, for an avatar. Same mapping as the badge. */
export function sessionTone(state: SessionState): StatusTone {
  return HEALTH_TONE[SESSION_BADGE[state]];
}

export function SessionBadge({ state }: { state: SessionState }) {
  const t = useT("onboarding");
  return (
    <StatusBadge status={SESSION_BADGE[state]} label={t.dyn("session", state, SESSION_LABELS[state])} />
  );
}

export function ActionBadge({ action }: { action: PlanAction }) {
  const t = useT("onboarding");
  return (
    <StatusBadge status={ACTION_BADGE[action]} label={t.dyn("action", action, ACTION_LABELS[action])} />
  );
}

export function GitOpsBadge({ state }: { state: GitOpsState }) {
  const t = useT("onboarding");
  return (
    <StatusBadge status={GITOPS_BADGE[state]} label={t.dyn("gitops", state, GITOPS_LABELS[state])} />
  );
}
