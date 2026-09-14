"use client";

/**
 * Capacity risk — certificate expiry and PVC health, ranked worst first.
 *
 * Every item here is a fact the cluster's own inventory summary already
 * reported (brief §9.4's forecast panel); nothing is computed from a
 * client-side threshold. A cluster this board never got a summary for is
 * listed separately as "forecast unavailable" rather than being silently
 * absent, which would read as "no risk" when the truth is "not assessed".
 */

import Link from "next/link";

import { Countdown } from "@/components/charts/visuals";
import { toneSpec, compareTone } from "@/lib/design/status";
import type { CapacityRiskItem } from "@/lib/view-models/capacity-risk";

export function CapacityRiskBoard({
  items,
  unassessedClusters,
}: {
  items: CapacityRiskItem[];
  unassessedClusters: string[];
}) {
  const ranked = [...items].sort((a, b) => compareTone(a.tone, b.tone));

  return (
    <div data-testid="capacity-risk-board">
      {ranked.length === 0 ? (
        <p className="px-6 py-5 text-caption text-ink-secondary" data-testid="capacity-risk-empty">
          No certificate or PVC risk reported by the sources checked.
        </p>
      ) : (
        <ul className="divide-y divide-border">
          {ranked.map((item) => {
            const spec = toneSpec(item.tone);
            const Icon = spec.icon;
            return (
              <li key={item.key}>
                <Link
                  href={item.href}
                  className="flex items-start gap-3 px-6 py-3.5 transition-colors hover:bg-surface-hover"
                >
                  <Icon aria-hidden className={`mt-0.5 h-4 w-4 shrink-0 ${spec.text}`} />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-baseline gap-x-2">
                      <span className="text-body font-medium text-ink">{item.clusterName}</span>
                      <span className={`text-caption ${spec.text}`}>{item.label}</span>
                    </div>
                    <p className="mt-0.5 text-micro text-ink-muted">{item.detail}</p>
                    {item.kind === "certificate" ? (
                      <div className="mt-1.5 max-w-xs">
                        <Countdown deadline={item.deadline} label="Certificate expires" />
                      </div>
                    ) : null}
                  </div>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
      {unassessedClusters.length > 0 ? (
        <p className="border-t border-border px-3 py-2 text-micro text-ink-muted" data-testid="capacity-risk-unassessed">
          Forecast unavailable for {unassessedClusters.join(", ")} — no inventory summary reported.
        </p>
      ) : null}
    </div>
  );
}
