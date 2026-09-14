"use client";

/**
 * The Projects portfolio's risk map (design spec §6.1): criticality × observed
 * health, kept as a real grid so one critical project among many healthy
 * ones stays its own visible cell instead of averaging into a portfolio
 * score. Desktop renders a semantic table; below that width a `<details>`
 * per criticality band replaces the horizontally-scrolling grid, mirroring
 * `HealthMatrix`'s dual-render pattern.
 *
 * A column header is also the tone filter control the Projects page reads
 * back through `activeTone`/`onToneChange` — clicking it again clears the
 * filter, so at most one tone is ever active at a time.
 */

import Link from "next/link";

import { TONE_SEVERITY, toneSpec, type StatusTone } from "@/lib/design/status";
import type { PortfolioRiskModel, ProjectRiskItem } from "@/lib/view-models/portfolio-risk";

const CRITICALITY_ORDER = ["critical", "high", "medium", "low"] as const;
const CRITICALITY_LABELS: Record<(typeof CRITICALITY_ORDER)[number], string> = {
  critical: "Critical",
  high: "High",
  medium: "Medium",
  low: "Low",
};

export interface ProjectRiskMapProps {
  model: PortfolioRiskModel;
  compact?: boolean;
  activeTone: string | null;
  onToneChange: (tone: string | null) => void;
}

function presentCriticalities(items: ProjectRiskItem[]) {
  return CRITICALITY_ORDER.filter((criticality) =>
    items.some((item) => item.criticality === criticality),
  );
}

function presentTones(items: ProjectRiskItem[]): StatusTone[] {
  return [...new Set(items.map((item) => item.tone))].sort(
    (a, b) => TONE_SEVERITY[a] - TONE_SEVERITY[b],
  );
}

function ProjectLink({ item }: { item: ProjectRiskItem }) {
  const spec = toneSpec(item.tone);
  return (
    <Link
      href={item.href}
      className="inline-flex items-center gap-1.5 rounded-control px-1 py-0.5 hover:bg-surface-hover"
    >
      <span aria-hidden className={`h-2.5 w-2.5 shrink-0 rounded-sm ${spec.dot}`} />
      <span className="text-caption text-ink">{item.displayName}</span>
      <span className="text-micro text-ink-muted">— {item.healthLabel}</span>
    </Link>
  );
}

function ToneHeaderButton({
  tone,
  active,
  onToggle,
}: {
  tone: StatusTone;
  active: boolean;
  onToggle: () => void;
}) {
  const spec = toneSpec(tone);
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={active}
      className={`rounded-control px-1.5 py-1 text-xs font-medium transition-colors ${
        active ? "bg-accent text-ink-inverse" : "text-ink-secondary hover:bg-surface-sunken"
      }`}
    >
      {spec.label}
    </button>
  );
}

function Grid({
  items,
  activeTone,
  onToneChange,
}: {
  items: ProjectRiskItem[];
  activeTone: string | null;
  onToneChange: (tone: string | null) => void;
}) {
  const criticalities = presentCriticalities(items);
  const tones = presentTones(items);

  return (
    <div className="hidden overflow-x-auto lg:block" data-testid="project-risk-map-grid">
      <table className="w-full border-collapse text-caption">
        <caption className="sr-only">Projects by criticality and observed health</caption>
        <thead>
          <tr>
            <th scope="col" className="w-28" />
            {tones.map((tone) => (
              <th
                key={tone}
                scope="col"
                className="border-b border-border px-3 py-1.5 text-left font-medium text-ink-secondary"
              >
                <ToneHeaderButton
                  tone={tone}
                  active={activeTone === tone}
                  onToggle={() => onToneChange(activeTone === tone ? null : tone)}
                />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {criticalities.map((criticality) => (
            <tr key={criticality}>
              <th scope="row" className="py-1.5 pr-3 text-left font-medium text-ink-secondary">
                {CRITICALITY_LABELS[criticality]}
              </th>
              {tones.map((tone) => {
                const cellItems = items.filter(
                  (item) => item.criticality === criticality && item.tone === tone,
                );
                const bg = toneSpec(tone).chip.split(" ")[0];
                return (
                  <td key={tone} className="px-1.5 py-1.5 align-top">
                    {cellItems.length > 0 ? (
                      <div className={`flex flex-col gap-1 rounded-lg p-2 ${bg}`}>
                        {cellItems.map((item) => (
                          <ProjectLink key={item.projectId} item={item} />
                        ))}
                      </div>
                    ) : (
                      <div className="flex h-full items-center justify-center px-2 py-2 text-micro text-ink-muted">
                        —
                      </div>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Disclosure({ items, compact }: { items: ProjectRiskItem[]; compact: boolean }) {
  const criticalities = presentCriticalities(items);
  return (
    <div className={compact ? "" : "lg:hidden"} data-testid="project-risk-map-disclosure">
      <ul className="divide-y divide-border">
        {criticalities.map((criticality) => (
          <li key={criticality}>
            <details open>
              <summary className="cursor-pointer px-1 py-2 text-caption font-medium text-ink">
                {CRITICALITY_LABELS[criticality]}
              </summary>
              <ul className="space-y-1.5 py-1.5 pl-3">
                {items
                  .filter((item) => item.criticality === criticality)
                  .map((item) => (
                    <li key={item.projectId}>
                      <ProjectLink item={item} />
                    </li>
                  ))}
              </ul>
            </details>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function ProjectRiskMap({
  model,
  compact = false,
  activeTone,
  onToneChange,
}: ProjectRiskMapProps) {
  return (
    <div data-testid="project-risk-map">
      <p className="mb-2 text-[11px] text-ink-muted">
        {model.complete
          ? "All projects assessed"
          : `Showing ${model.servicesLoaded}${
              model.servicesTotal !== null ? ` of ${model.servicesTotal}` : ""
            } services loaded so far.`}
      </p>
      {!compact ? <Grid items={model.items} activeTone={activeTone} onToneChange={onToneChange} /> : null}
      <Disclosure items={model.items} compact={compact} />
    </div>
  );
}
