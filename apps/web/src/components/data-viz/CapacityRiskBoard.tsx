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

import { ShieldCheck } from "lucide-react";
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
        <div className="flex flex-col items-center justify-center gap-4 px-7 py-10 text-center">
          <span aria-hidden className="relative flex h-20 w-20 items-center justify-center">
            <span className="absolute inset-0 rounded-full bg-healthy-soft" />
            <span className="absolute inset-3 rounded-full border border-healthy/30 bg-surface" />
            <ShieldCheck className="relative h-7 w-7 text-healthy" />
          </span>
          <div>
            <p className="text-body font-semibold text-ink">No capacity risk in sight</p>
            <p className="mt-1 max-w-sm text-caption text-ink-muted" data-testid="capacity-risk-empty">
              No certificate or PVC risk reported by the sources checked.
            </p>
          </div>
          <div className="flex gap-2 text-micro">
            <span className="rounded-full bg-surface-2 px-3 py-1 text-ink-secondary">Certificates</span>
            <span className="rounded-full bg-surface-2 px-3 py-1 text-ink-secondary">Persistent volumes</span>
          </div>
        </div>
      ) : (
        <ul className="divide-y divide-border">
          {ranked.map((item) => {
            const spec = toneSpec(item.tone);
            const Icon = spec.icon;
            return (
              <li key={item.key}>
                <Link
                  href={item.href}
                  className="flex items-start gap-4 px-7 py-4 transition-colors hover:bg-surface-hover"
                >
                  <span aria-hidden className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full ${spec.chip}`}>
                    <Icon className="h-4 w-4" />
                  </span>
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
        <p className="border-t border-border px-7 py-3 text-micro text-ink-muted" data-testid="capacity-risk-unassessed">
          Forecast unavailable for {unassessedClusters.join(", ")} — no inventory summary reported.
        </p>
      ) : null}
    </div>
  );
}
