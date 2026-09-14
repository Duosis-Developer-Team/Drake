/**
 * Operational verdict — the Command Center's lead panel.
 *
 * Laid out after the reference's balance card: a lit obsidian slab with a
 * title and a source chip, one bright card-in-card carrying the headline
 * number, and a split action row. The number is what is flagged right now;
 * the sentence beside it says how much of the estate actually answered, so a
 * zero is never read as "healthy" when sources were silent.
 */
import { ArrowUpRight, RefreshCw } from "lucide-react";
import Link from "next/link";

import { RelativeTime } from "@/components/ui/identifiers";
import { Panel } from "@/components/ui/Panel";
import type { OperationalVerdict } from "@/lib/view-models/verdict";

export function VerdictPanel({
  verdict,
  onRefresh,
  refreshing,
}: {
  verdict: OperationalVerdict;
  onRefresh: () => void;
  refreshing: boolean;
}) {
  const worstTone = verdict.criticalCount > 0 ? "critical" : verdict.warningCount > 0 ? "warning" : "success";
  const { projects, services, clusters } = verdict.affectedScope;
  const allSourcesAnswered = verdict.sourcesAnswered >= verdict.sourcesTotal;
  const flagged = verdict.criticalCount + verdict.warningCount;
  const numberTone = { critical: "text-[#d92d20]", warning: "text-[#b54708]", success: "text-[#161616]" }[worstTone];

  return (
    <Panel
      radius="canvas"
      surface="hero"
      tone={worstTone === "success" ? "default" : worstTone}
      data-testid="verdict-panel"
      className="!gap-0 !p-2.5"
    >
      <div className="flex items-start justify-between gap-3 px-4 pt-3.5 pb-5">
        <div className="min-w-0">
          <p className="text-[1.0625rem] font-medium text-hero-ink">Operational verdict</p>
          <p className="text-micro text-hero-ink-muted">Across your authorized scope</p>
        </div>
        <span
          data-testid="verdict-sources"
          className="inline-flex shrink-0 items-center gap-2 rounded-full border border-white/10 bg-white/[0.06] px-3 py-1.5 text-caption font-medium text-hero-ink"
        >
          <span
            aria-hidden
            className={`h-2 w-2 rounded-full ${allSourcesAnswered ? "bg-[#f1f1f1]" : "bg-[#fdb022]"}`}
          />
          <span className="sr-only">{verdict.sourcesAnswered} of {verdict.sourcesTotal} sources answered</span>
          <span aria-hidden>
            {verdict.sourcesAnswered}/{verdict.sourcesTotal} sources
          </span>
        </span>
      </div>

      {/* Card-in-card: the one bright surface on the screen. */}
      <div className="flex flex-1 flex-col rounded-[1.25rem] bg-[#f4f4f4] text-[#161616] shadow-[0_10px_30px_-12px_rgba(0,0,0,0.6)]">
        <div className="flex-1 px-6 pt-5 pb-6">
          <p className="text-caption text-[#6b6b6b]">Flagged right now</p>
          <p className="mt-1 flex items-baseline gap-2">
            <span data-tabular className={`text-[4rem] leading-none font-semibold tracking-[-0.04em] ${numberTone}`}>
              {flagged}
            </span>
          </p>
          <p
            data-testid="verdict-headline"
            style={{ textWrap: "balance" }}
            className="mt-3 text-body font-medium text-[#3a3a3a]"
          >
            {verdict.headline}
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-2 text-micro">
            {verdict.criticalCount > 0 ? (
              <span data-testid="verdict-critical-count" className="rounded-full bg-[#fee4e2] px-2.5 py-1 font-medium text-[#b42318]">
                {verdict.criticalCount} critical
              </span>
            ) : null}
            {verdict.warningCount > 0 ? (
              <span data-testid="verdict-warning-count" className="rounded-full bg-[#fef0c7] px-2.5 py-1 font-medium text-[#93370d]">
                {verdict.warningCount} warning{verdict.warningCount === 1 ? "" : "s"}
              </span>
            ) : null}
            {projects > 0 ? (
              <Link href="/projects" data-testid="verdict-scope-projects" className="rounded-full bg-[#e8e8e8] px-2.5 py-1 font-medium text-[#3a3a3a] hover:bg-[#dedede]">
                {projects} project{projects === 1 ? "" : "s"}
              </Link>
            ) : null}
            {services > 0 ? (
              <Link href="/service-health" data-testid="verdict-scope-services" className="rounded-full bg-[#e8e8e8] px-2.5 py-1 font-medium text-[#3a3a3a] hover:bg-[#dedede]">
                {services} service{services === 1 ? "" : "s"}
              </Link>
            ) : null}
            {clusters > 0 ? (
              <Link href="/clusters" data-testid="verdict-scope-clusters" className="rounded-full bg-[#e8e8e8] px-2.5 py-1 font-medium text-[#3a3a3a] hover:bg-[#dedede]">
                {clusters} cluster{clusters === 1 ? "" : "s"}
              </Link>
            ) : null}
            {verdict.oldestSuspectEvidence ? (
              <span data-testid="verdict-oldest-evidence" className="text-[#6b6b6b]">
                oldest: {verdict.oldestSuspectEvidence.label}{" "}
                <RelativeTime value={verdict.oldestSuspectEvidence.asOf} />
              </span>
            ) : null}
          </div>
        </div>
        <div className="grid grid-cols-2 border-t border-[#e2e2e2] text-body font-medium">
          <button
            type="button"
            onClick={onRefresh}
            disabled={refreshing}
            data-testid={allSourcesAnswered ? undefined : "verdict-retry-sources"}
            className="flex items-center justify-center gap-2 rounded-bl-[1.25rem] py-3.5 transition-colors hover:bg-[#ebebeb] disabled:opacity-60"
          >
            <RefreshCw aria-hidden className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
            {refreshing ? "Retrying…" : allSourcesAnswered ? "Re-check" : "Retry unanswered sources"}
          </button>
          <Link
            href="/incidents"
            className="flex items-center justify-center gap-2 rounded-br-[1.25rem] border-l border-[#e2e2e2] py-3.5 transition-colors hover:bg-[#ebebeb]"
          >
            <ArrowUpRight aria-hidden className="h-4 w-4" />
            Incidents
          </Link>
        </div>
      </div>
    </Panel>
  );
}
