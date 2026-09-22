"use client";

/** GitHub integration UI primitives.
 *
 * The honesty rules from ADR-0011/0020 hold here too: blocked, degraded,
 * disabled and unknown are DISTINCT visual states, and none of them
 * borrows the healthy colour. A repository we could not evaluate never
 * looks like one that passed.
 *
 * Colour is decided here; the words come from `integrations.badge.*` so
 * the two locales share one mapping.
 */

import { Fragment } from "react";

import { StatusBadge, type HealthStatus } from "@/components/state/StatusBadge";
import { useT } from "@/lib/i18n";
import type { OnboardingState, PolicyVerdict } from "@/lib/github";

const ONBOARDING_BADGE: Record<OnboardingState, HealthStatus> = {
  discovered: "unknown",
  validating: "maintenance",
  ready: "healthy",
  // Blocked is a decision, degraded is a wobble — they must not look alike.
  blocked: "critical",
  degraded: "warning",
  disabled: "stale",
};

export function OnboardingBadge({ state }: { state: OnboardingState | string | undefined }) {
  const t = useT("integrations");
  const token = state ?? "discovered";
  const status = ONBOARDING_BADGE[token as OnboardingState] ?? ("unknown" as HealthStatus);
  return <StatusBadge status={status} label={t.dyn("badge.onboarding", token, token)} />;
}

const VERDICT_BADGE: Record<PolicyVerdict, HealthStatus> = {
  pass: "healthy",
  warn: "warning",
  fail: "critical",
  unknown: "unknown",
};

export function VerdictBadge({ verdict }: { verdict: PolicyVerdict | string | undefined }) {
  const t = useT("integrations");
  const token = VERDICT_BADGE[(verdict as PolicyVerdict) ?? "unknown"] ? (verdict as PolicyVerdict) : "unknown";
  return <StatusBadge status={VERDICT_BADGE[token]} label={t.dyn("badge.verdict", token, token)} />;
}

const INSTALLATION_BADGE: Record<string, HealthStatus> = {
  active: "healthy",
  suspended: "warning",
  deleted: "stale",
};

export function InstallationBadge({ state }: { state: string | undefined }) {
  const t = useT("integrations");
  const token = state || "unknown";
  const status = INSTALLATION_BADGE[token] ?? ("unknown" as HealthStatus);
  return <StatusBadge status={status} label={t.dyn("badge.installation", token, token)} />;
}

export function formatUtc(value: string | null | undefined): string {
  if (!value) return "—";
  try {
    return new Date(value).toISOString().replace(".000Z", "Z");
  } catch {
    return value;
  }
}

/**
 * A catalogue template whose `{slot}` values are React nodes rather than
 * strings: a styled number, a `<time>`, a `<strong>`. The template comes
 * from `t("key")` unformatted, so the translated word order decides where
 * each node lands; a slot with no part renders its placeholder so a missing
 * name is visible rather than silently blank.
 */
export function RichMessage({
  template,
  parts,
}: {
  template: string;
  parts: Record<string, React.ReactNode>;
}) {
  const out: React.ReactNode[] = [];
  const pattern = /\{(\w+)\}/g;
  let last = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(template)) !== null) {
    if (match.index > last) out.push(template.slice(last, match.index));
    out.push(<Fragment key={`${match[1]}-${match.index}`}>{parts[match[1]] ?? match[0]}</Fragment>);
    last = match.index + match[0].length;
  }
  if (last < template.length) out.push(template.slice(last));
  return <>{out}</>;
}
