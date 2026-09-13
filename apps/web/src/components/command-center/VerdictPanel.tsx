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

  return (
    <Panel
      radius="canvas"
      tone={worstTone === "success" ? "default" : worstTone}
      data-testid="verdict-panel"
    >
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
          <Icon aria-hidden className={`mt-1 h-6 w-6 shrink-0 ${spec.text}`} />
          <div className="min-w-0">
            <p
              data-testid="verdict-headline"
              style={{ textWrap: "balance" }}
              className={`text-display font-semibold ${worstTone === "success" ? "text-ink" : spec.text}`}
            >
              {verdict.headline}
            </p>
            <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-caption">
              {verdict.criticalCount > 0 ? (
                <span data-testid="verdict-critical-count" className="font-medium text-critical">
                  {verdict.criticalCount} critical
                </span>
              ) : null}
              {verdict.warningCount > 0 ? (
                <span data-testid="verdict-warning-count" className="font-medium text-warning">
                  {verdict.warningCount} warning{verdict.warningCount === 1 ? "" : "s"}
                </span>
              ) : null}
              {projects > 0 ? (
                <Link
                  href="/projects"
                  data-testid="verdict-scope-projects"
                  className="text-ink-secondary hover:text-ink hover:underline"
                >
                  {projects} project{projects === 1 ? "" : "s"}
                </Link>
              ) : null}
              {services > 0 ? (
                <Link
                  href="/service-health"
                  data-testid="verdict-scope-services"
                  className="text-ink-secondary hover:text-ink hover:underline"
                >
                  {services} service{services === 1 ? "" : "s"}
                </Link>
              ) : null}
              {clusters > 0 ? (
                <Link
                  href="/clusters"
                  data-testid="verdict-scope-clusters"
                  className="text-ink-secondary hover:text-ink hover:underline"
                >
                  {clusters} cluster{clusters === 1 ? "" : "s"}
                </Link>
              ) : null}
            </div>
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1 text-micro text-ink-muted">
          <span data-testid="verdict-sources">
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
              className="font-medium text-brand hover:underline disabled:opacity-60"
            >
              {refreshing ? "Retrying…" : "Retry unanswered sources"}
            </button>
          ) : null}
        </div>
      </div>
    </Panel>
  );
}
