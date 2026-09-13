"use client";

/**
 * Evidence coverage — always visible, not only shown when the attention
 * queue is empty (brief §9.2, Task 2.6).
 *
 * The empty attention queue used to be the only place this page said what it
 * checked. That buried the same fact an operator needs just as much when
 * something IS flagged: which of the five sources actually answered, and
 * which are stale, denied, or unreachable. A source is never rolled up into
 * a single "healthy" claim — this panel has no such sentence anywhere in it.
 */

import { toneSpec, type StatusTone } from "@/lib/design/status";
import type { Resource } from "@/lib/useResource";

export type SourceCoverageState =
  | "loading"
  | "configured-fresh"
  | "configured-stale"
  | "not-configured"
  | "permission-denied"
  | "unavailable";

export interface SourceCoverage {
  key: string;
  label: string;
  state: SourceCoverageState;
  detail?: string;
}

const STATE_TONE: Record<SourceCoverageState, StatusTone> = {
  loading: "pending",
  "configured-fresh": "success",
  "configured-stale": "stale",
  "not-configured": "not-applicable",
  "permission-denied": "denied",
  unavailable: "critical",
};

const STATE_LABEL: Record<SourceCoverageState, string> = {
  loading: "Checking…",
  "configured-fresh": "Answered",
  "configured-stale": "Last known good",
  "not-configured": "Not configured",
  "permission-denied": "Permission required",
  unavailable: "Unavailable",
};

export function classifySource(source: { key: string; label: string; resource: Resource<unknown> }): SourceCoverage {
  const { key, label, resource } = source;
  if (resource.denied) return { key, label, state: "permission-denied" };
  if (resource.notFound) return { key, label, state: "not-configured" };
  if (resource.data === null) {
    if (resource.loading) return { key, label, state: "loading" };
    return { key, label, state: "unavailable", detail: resource.error ?? undefined };
  }
  // Data is on screen, but the most recent background refresh failed — this
  // is last-known-good, not a fresh answer, and is named as such rather than
  // silently kept indistinguishable from a source that just answered.
  if (resource.error) return { key, label, state: "configured-stale", detail: resource.error };
  return { key, label, state: "configured-fresh" };
}

export function EvidenceCoverage({
  sources,
}: {
  sources: readonly { key: string; label: string; resource: Resource<unknown> }[];
}) {
  const rows = sources.map(classifySource);
  return (
    <div data-testid="evidence-coverage">
      <ul className="divide-y divide-border">
        {rows.map((row) => {
          const spec = toneSpec(STATE_TONE[row.state]);
          const Icon = spec.icon;
          return (
            <li key={row.key} className="flex items-center gap-3 px-3 py-2">
              <Icon aria-hidden className={`h-4 w-4 shrink-0 ${spec.text}`} />
              <span className="min-w-0 flex-1 text-caption text-ink">{row.label}</span>
              <span className={`shrink-0 text-caption ${spec.text}`}>{STATE_LABEL[row.state]}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
