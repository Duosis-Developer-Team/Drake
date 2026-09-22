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
import { useT } from "@/lib/i18n";
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
  /** The source's name as the caller wants it read — already translated. */
  label: string;
  /** Also the catalogue key: `commandCenter.evidence.state.<state>`. */
  state: SourceCoverageState;
  /** Backend error text, quoted as-is. */
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
  const t = useT("commandCenter");
  const rows = sources.map(classifySource);
  const answered = rows.filter((row) => row.state === "configured-fresh").length;
  const share = rows.length > 0 ? (answered / rows.length) * 100 : 0;
  return (
    <div data-testid="evidence-coverage" className="flex flex-col gap-5">
      <div className="flex items-center gap-4">
        <span data-tabular className="text-[2rem] leading-none font-semibold tracking-[-0.03em] text-ink">
          {answered}
          <span className="text-ink-muted">/{rows.length}</span>
        </span>
        <span className="text-caption text-ink-muted">{t("evidence.answered")}</span>
        <span aria-hidden className="ml-auto hidden h-2 w-48 overflow-hidden rounded-full bg-surface-3 sm:block">
          <span className="block h-full rounded-full bg-healthy" style={{ width: `${share}%` }} />
        </span>
      </div>
      <ul className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5">
        {rows.map((row) => {
          const spec = toneSpec(STATE_TONE[row.state]);
          const Icon = spec.icon;
          return (
            <li
              key={row.key}
              title={row.detail}
              className="flex flex-col gap-4 rounded-[1.125rem] bg-surface-2 p-4"
            >
              <span className="flex items-center justify-between">
                <span aria-hidden className={`flex h-10 w-10 items-center justify-center rounded-full ${spec.chip}`}>
                  <Icon className="h-4 w-4" />
                </span>
                <span aria-hidden className={`h-2 w-2 rounded-full ${spec.dot}`} />
              </span>
              <span className="min-w-0">
                <span className="block text-body font-semibold text-ink">{row.label}</span>
                <span className={`mt-0.5 block text-caption ${spec.text}`}>{t(`evidence.state.${row.state}`)}</span>
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
