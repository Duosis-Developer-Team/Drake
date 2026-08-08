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
 */

import { StatusBadge, type HealthStatus } from "@/components/state/StatusBadge";
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

export function SessionBadge({ state }: { state: SessionState }) {
  return <StatusBadge status={SESSION_BADGE[state]} label={SESSION_LABELS[state]} />;
}

export function ActionBadge({ action }: { action: PlanAction }) {
  return <StatusBadge status={ACTION_BADGE[action]} label={ACTION_LABELS[action]} />;
}

export function GitOpsBadge({ state }: { state: GitOpsState }) {
  return <StatusBadge status={GITOPS_BADGE[state]} label={GITOPS_LABELS[state]} />;
}
