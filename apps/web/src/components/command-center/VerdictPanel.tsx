/**
 * Operational verdict — the Command Center's lead panel.
 *
 * Replaces the five-equal-KPI-card triage strip (brief §9.3 forbids it): one
 * status sentence, the critical/warning totals it's built from, which
 * projects/services/clusters they touch, and how much of the picture is
 * actually visible right now. No tile here is ever a bare zero standing in
 * for "not checked" — a verdict with fewer sources answered says so in
 * words, in the same sentence as the counts.
 */
import Link from "next/link";

import { ProgressGauge } from "@/components/charts/GaugeChart";
import { RelativeTime } from "@/components/ui/identifiers";
import { Panel } from "@/components/ui/Panel";
import { toneSpec } from "@/lib/design/status";
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
  const spec = toneSpec(worstTone);
  const Icon = spec.icon;
  const { projects, services, clusters } = verdict.affectedScope;
  const allSourcesAnswered = verdict.sourcesAnswered >= verdict.sourcesTotal;

  // The hero surface is theme-invariant obsidian (Panel's `surface="hero"`),
  // so every status colour used against it must be the "on dark" reading
  // regardless of the page's own light/dark theme — the dark-theme tone
  // tokens are chosen for contrast on this exact obsidian background.
  const heroTone: Record<typeof worstTone, string> = {
    critical: "text-[#f97066]",
    warning: "text-[#fdb022]",
    success: "text-sidebar-ink",
  };

  return (
    <Panel
      radius="canvas"
      surface="hero"
      tone={worstTone === "success" ? "default" : worstTone}
      data-testid="verdict-panel"
      className="min-h-60 justify-center !p-8"
    >
      <div className="flex flex-wrap items-center justify-between gap-8">
        <div className="flex min-w-0 max-w-2xl items-start gap-4">
          <Icon aria-hidden className={`mt-1 h-8 w-8 shrink-0 ${heroTone[worstTone]}`} />
          <div className="min-w-0">
            <p
              data-testid="verdict-headline"
              style={{ textWrap: "balance" }}
              className={`text-5xl font-semibold tracking-tight ${heroTone[worstTone]}`}
            >
              {verdict.headline}
            </p>
            <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-2">
              {verdict.criticalCount > 0 ? (
                <span data-testid="verdict-critical-count" className="flex items-baseline gap-1.5">
                  <span className="text-metric font-semibold text-[#f97066]">
                    {verdict.criticalCount}
                  </span>
                  <span className="text-caption text-sidebar-ink-muted">critical</span>
                </span>
              ) : null}
              {verdict.warningCount > 0 ? (
                <span data-testid="verdict-warning-count" className="flex items-baseline gap-1.5">
                  <span className="text-metric font-semibold text-[#fdb022]">
                    {verdict.warningCount}
                  </span>
                  <span className="text-caption text-sidebar-ink-muted">
                    warning{verdict.warningCount === 1 ? "" : "s"}
                  </span>
                </span>
              ) : null}
              {projects > 0 ? (
                <Link
                  href="/projects"
                  data-testid="verdict-scope-projects"
                  className="text-caption text-sidebar-ink-muted hover:text-sidebar-ink hover:underline"
                >
                  {projects} project{projects === 1 ? "" : "s"}
                </Link>
              ) : null}
              {services > 0 ? (
                <Link
                  href="/service-health"
                  data-testid="verdict-scope-services"
                  className="text-caption text-sidebar-ink-muted hover:text-sidebar-ink hover:underline"
                >
                  {services} service{services === 1 ? "" : "s"}
                </Link>
              ) : null}
              {clusters > 0 ? (
                <Link
                  href="/clusters"
                  data-testid="verdict-scope-clusters"
                  className="text-caption text-sidebar-ink-muted hover:text-sidebar-ink hover:underline"
                >
                  {clusters} cluster{clusters === 1 ? "" : "s"}
                </Link>
              ) : null}
            </div>
          </div>
        </div>
        {/* Card-in-card: a bright surface nested in the obsidian hero, the
            way the PayFlow reference sets its one headline metric apart
            from the dark panel that holds it — depth from a colour jump,
            not a border. */}
        <div className="flex shrink-0 items-center gap-4 rounded-[1.25rem] bg-[#f1f1f1] py-4 pl-5 pr-6 shadow-overlay">
          <ProgressGauge
            size={88}
            value={verdict.sourcesTotal > 0 ? (verdict.sourcesAnswered / verdict.sourcesTotal) * 100 : 0}
            label={`${verdict.sourcesAnswered}/${verdict.sourcesTotal}`}
            color="#2a78d6"
            trackColor="#e3e3e3"
            textColor="#161616"
            ariaLabel={`${verdict.sourcesAnswered} of ${verdict.sourcesTotal} sources answered`}
          />
          <div className="flex flex-col items-start gap-1.5 text-micro text-[#646464]">
            <span data-testid="verdict-sources" className="text-caption font-semibold text-[#161616]">
              {verdict.sourcesAnswered} of {verdict.sourcesTotal} sources answered
            </span>
            {verdict.oldestSuspectEvidence ? (
              <span data-testid="verdict-oldest-evidence">
                oldest: {verdict.oldestSuspectEvidence.label}{" "}
                <RelativeTime value={verdict.oldestSuspectEvidence.asOf} />
              </span>
            ) : null}
            {!allSourcesAnswered ? (
              <button
                type="button"
                onClick={onRefresh}
                disabled={refreshing}
                data-testid="verdict-retry-sources"
                className="font-semibold text-[#2a78d6] hover:underline disabled:opacity-60"
              >
                {refreshing ? "Retrying…" : "Retry unanswered sources"}
              </button>
            ) : null}
          </div>
        </div>
      </div>
    </Panel>
  );
}
