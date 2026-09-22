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
import { useT } from "@/lib/i18n";
import type {
  PortfolioRiskModel,
  ProjectRiskItem,
} from "@/lib/view-models/portfolio-risk";

/** Criticality bands, worst first. Their words are `common.severity.*`. */
const CRITICALITY_ORDER = ["critical", "high", "medium", "low"] as const;

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
  const t = useT("commandCenter");
  const spec = toneSpec(item.tone);
  // The health word follows the evidence, not the English `healthLabel` the
  // view-model also carries for non-React readers.
  const health =
    item.evidence === "unassessed"
      ? t("riskMap.health.unassessed")
      : item.evidence === "incomplete"
        ? t("riskMap.health.incomplete")
        : t(`tone.${item.tone}`);
  return (
    <Link
      href={item.href}
      className="flex min-w-0 items-center gap-2 rounded-full border border-border bg-surface py-1 pr-3 pl-1.5 transition-colors hover:border-border-strong hover:bg-surface-hover"
    >
      <span
        aria-hidden
        className={`h-2.5 w-2.5 shrink-0 rounded-full ${spec.dot}`}
      />
      <span className="truncate text-caption font-medium text-ink">
        {item.displayName}
      </span>
      <span className="sr-only">— {health}</span>
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
  const t = useT("commandCenter");
  const spec = toneSpec(tone);
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={active}
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-micro font-medium whitespace-nowrap transition-colors ${
        active
          ? "border-transparent bg-accent text-ink-inverse"
          : "border-border bg-surface text-ink-secondary hover:bg-surface-hover hover:text-ink"
      }`}
    >
      <span
        aria-hidden
        className={`h-2 w-2 rounded-full ${active ? "bg-ink-inverse" : spec.dot}`}
      />
      {t(`tone.${tone}`)}
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
  const t = useT("commandCenter");
  const common = useT("common");
  const criticalities = presentCriticalities(items);
  const tones = presentTones(items);

  return (
    <div
      className="hidden overflow-x-auto lg:block"
      data-testid="project-risk-map-grid"
    >
      {/* Below ~28rem (e.g. a page aside) the matrix stacks: tone controls
          wrap as a chip row, each criticality becomes a label over its
          non-empty cells, and each cell names its tone itself. */}
      <table className="block w-full border-separate border-spacing-2 text-caption @md/risk:table">
        <caption className="sr-only">{t("riskMap.caption")}</caption>
        <thead className="block @md/risk:table-header-group">
          <tr className="flex flex-wrap gap-1.5 @md/risk:table-row">
            <th scope="col" className="hidden w-20 @md/risk:table-cell" />
            {tones.map((tone) => (
              <th
                key={tone}
                scope="col"
                className="block px-0 pb-1 text-left font-medium @md/risk:table-cell"
              >
                <ToneHeaderButton
                  tone={tone}
                  active={activeTone === tone}
                  onToggle={() =>
                    onToneChange(activeTone === tone ? null : tone)
                  }
                />
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="block @md/risk:table-row-group">
          {criticalities.map((criticality) => (
            <tr
              key={criticality}
              className="mt-4 flex flex-col gap-2 @md/risk:mt-0 @md/risk:table-row"
            >
              <th
                scope="row"
                className="block pr-1 text-left align-middle text-micro font-medium tracking-[0.08em] text-ink-muted uppercase @md/risk:table-cell"
              >
                {common(`severity.${criticality}`)}
              </th>
              {tones.map((tone) => {
                const cellItems = items.filter(
                  (item) =>
                    item.criticality === criticality && item.tone === tone,
                );
                const bg = toneSpec(tone).chip.split(" ")[0];
                return (
                  <td
                    key={tone}
                    className={`align-top @md/risk:table-cell ${cellItems.length > 0 ? "block" : "hidden"}`}
                  >
                    {cellItems.length > 0 ? (
                      <div
                        className={`flex min-h-[4.5rem] flex-col gap-2 rounded-[1rem] p-2.5 ${bg}`}
                      >
                        <span className="flex items-baseline gap-2 px-1">
                          <span
                            data-tabular
                            className="text-[1.25rem] leading-none font-semibold text-ink"
                          >
                            {cellItems.length}
                          </span>
                          <span
                            aria-hidden
                            className="truncate text-micro text-ink-secondary @md/risk:hidden"
                          >
                            {t(`tone.${tone}`)}
                          </span>
                        </span>
                        <div className="flex flex-col gap-1.5">
                          {cellItems.map((item) => (
                            <ProjectLink key={item.projectId} item={item} />
                          ))}
                        </div>
                      </div>
                    ) : (
                      <div className="flex min-h-[4.5rem] items-center justify-center rounded-[1rem] border border-dashed border-border text-micro text-ink-muted">
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

function Disclosure({
  items,
  compact,
}: {
  items: ProjectRiskItem[];
  compact: boolean;
}) {
  const common = useT("common");
  const criticalities = presentCriticalities(items);
  return (
    <div
      className={compact ? "" : "lg:hidden" /* i18n-ignore: CSS */}
      data-testid="project-risk-map-disclosure"
    >
      <ul className="flex flex-col gap-2">
        {criticalities.map((criticality) => (
          <li key={criticality}>
            <details
              open
              className="rounded-[1rem] border border-border px-3 py-2"
            >
              <summary className="cursor-pointer text-micro font-medium tracking-[0.08em] text-ink-muted uppercase">
                {common(`severity.${criticality}`)}
              </summary>
              <ul className="flex flex-wrap gap-1.5 pt-2">
                {items
                  .filter((item) => item.criticality === criticality)
                  .map((item) => (
                    <li key={item.projectId} className="min-w-0">
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
  const t = useT("commandCenter");
  return (
    <div
      data-testid="project-risk-map"
      className="@container/risk flex min-w-0 flex-col gap-3"
    >
      <p
        className={`inline-flex items-center gap-1.5 self-start rounded-full px-2.5 py-1 text-micro ${
          model.complete
            ? "bg-surface-2 text-ink-secondary"
            : "bg-unknown-soft text-unknown"
        }`}
      >
        {model.complete
          ? t("riskMap.allAssessed")
          : model.servicesTotal !== null
            ? t("riskMap.partial", { shown: model.servicesLoaded, total: model.servicesTotal })
            : t("riskMap.partialUnknownTotal", { shown: model.servicesLoaded })}
      </p>
      {!compact ? (
        <Grid
          items={model.items}
          activeTone={activeTone}
          onToneChange={onToneChange}
        />
      ) : null}
      <Disclosure items={model.items} compact={compact} />
    </div>
  );
}
