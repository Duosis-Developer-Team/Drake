/**
 * Operational verdict — the Command Center's lead panel.
 *
 * A card like every other card on the page: it takes the theme's own surface,
 * border and ink, so it belongs to the page instead of sitting on it as a
 * differently-coloured block. What sets it apart is its content — a status
 * badge, the flagged count at display size, and a sources ring — laid out on
 * a theme-following inset. The number is what is flagged right now; the
 * sentence beside it says how much of the estate actually answered, so a zero
 * is never read as "healthy" when sources were silent.
 */
import { ArrowUpRight, RefreshCw } from "lucide-react";
import Link from "next/link";

import { RelativeTime } from "@/components/ui/identifiers";
import { Panel } from "@/components/ui/Panel";
import { toneSpec } from "@/lib/design/status";
import { useT } from "@/lib/i18n";
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
  const t = useT("commandCenter");
  const worstTone = verdict.criticalCount > 0 ? "critical" : verdict.warningCount > 0 ? "warning" : "success";
  const { projects, services, clusters } = verdict.affectedScope;
  const allSourcesAnswered = verdict.sourcesAnswered >= verdict.sourcesTotal;
  const flagged = verdict.criticalCount + verdict.warningCount;
  const spec = toneSpec(worstTone);
  const StatusIcon = spec.icon;
  const labelSpec = flagged === 0 && !allSourcesAnswered ? toneSpec("warning") : spec;
  const numberTone = flagged > 0 ? spec.text : "text-ink";
  const scopeChip =
    "rounded-full border border-border bg-surface px-2 py-0.5 text-micro font-medium text-ink-secondary transition-colors hover:bg-surface-hover hover:text-ink";
  const headline =
    verdict.headlineKind === "critical"
      ? t("verdict.headline.critical", { count: verdict.criticalCount })
      : verdict.headlineKind === "warning"
        ? t("verdict.headline.warning", { count: verdict.warningCount })
        : t("verdict.headline.clear", { answered: verdict.sourcesAnswered, total: verdict.sourcesTotal });

  return (
    <Panel
      flush
      tone={worstTone === "success" ? "default" : worstTone}
      data-testid="verdict-panel"
      className="!flex-row flex-wrap items-center gap-x-5 gap-y-3 px-5 py-4"
    >
      <span aria-hidden className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full ${spec.chip}`}>
        <StatusIcon className="h-5 w-5" />
      </span>

      <div className="min-w-0 flex-1 basis-80">
        <div className="flex flex-wrap items-center gap-2">
          <p
            data-testid="verdict-headline"
            className={`text-[1.0625rem] leading-6 font-bold tracking-[-0.01em] ${flagged > 0 ? numberTone : "text-ink"}`}
          >
            {headline}
          </p>
          {/* "All clear" only when every source answered — a quiet page with
              silent sources is a partial view, not a healthy one. */}
          {flagged === 0 ? (
            <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-micro font-medium ${labelSpec.chip}`}>
              <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${labelSpec.dot}`} />
              {allSourcesAnswered ? t("verdict.allClear") : t("verdict.partialView")}
            </span>
          ) : null}
        </div>
        <dl className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-caption text-ink-muted">
          <div className="flex items-center gap-1.5">
            <dt className="flex items-center gap-1.5">
              <span aria-hidden className={`h-2 w-2 rounded-full ${toneSpec("critical").dot}`} />
              {t("tone.critical")}
            </dt>
            <dd
              data-tabular
              data-testid={verdict.criticalCount > 0 ? "verdict-critical-count" : undefined}
              className={`font-semibold ${verdict.criticalCount > 0 ? "text-critical" : "text-ink"}`}
            >
              {verdict.criticalCount}
            </dd>
          </div>
          <div className="flex items-center gap-1.5">
            <dt className="flex items-center gap-1.5">
              <span aria-hidden className={`h-2 w-2 rounded-full ${toneSpec("warning").dot}`} />
              {t("tone.warning")}
            </dt>
            <dd
              data-tabular
              data-testid={verdict.warningCount > 0 ? "verdict-warning-count" : undefined}
              className={`font-semibold ${verdict.warningCount > 0 ? "text-warning" : "text-ink"}`}
            >
              {verdict.warningCount}
            </dd>
          </div>
          <div className="flex min-w-0 flex-wrap items-center gap-1.5">
            <dt>{t("verdict.affected")}</dt>
            <dd className="flex min-w-0 flex-wrap items-center gap-1.5">
              {projects > 0 ? (
                <Link href="/projects" data-testid="verdict-scope-projects" className={scopeChip}>
                  {t("verdict.projects", { count: projects })}
                </Link>
              ) : null}
              {services > 0 ? (
                <Link href="/service-health" data-testid="verdict-scope-services" className={scopeChip}>
                  {t("verdict.services", { count: services })}
                </Link>
              ) : null}
              {clusters > 0 ? (
                <Link href="/clusters" data-testid="verdict-scope-clusters" className={scopeChip}>
                  {t("verdict.clusters", { count: clusters })}
                </Link>
              ) : null}
              {projects + services + clusters === 0 ? <span>{t("verdict.nothingInScope")}</span> : null}
              {verdict.oldestSuspectEvidence ? (
                <span data-testid="verdict-oldest-evidence">
                  {t("verdict.oldest", { label: verdict.oldestSuspectEvidence.label })}{" "}
                  <RelativeTime value={verdict.oldestSuspectEvidence.asOf} />
                </span>
              ) : null}
            </dd>
          </div>
        </dl>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <span
          data-testid="verdict-sources"
          className={`inline-flex h-8 items-center gap-2 rounded-control border px-2.5 text-caption font-medium ${
            allSourcesAnswered ? "border-border text-ink-secondary" : "border-warning text-warning"
          }`}
        >
          <span aria-hidden className={`h-2 w-2 rounded-full ${allSourcesAnswered ? "bg-healthy" : "bg-warning"}`} />
          {t("verdict.sourcesAnswered", { answered: verdict.sourcesAnswered, total: verdict.sourcesTotal })}
        </span>
        <button
          type="button"
          onClick={onRefresh}
          disabled={refreshing}
          data-testid={allSourcesAnswered ? undefined : "verdict-retry-sources"}
          className="inline-flex h-8 items-center gap-1.5 rounded-control border border-border bg-surface px-3 text-caption font-medium text-ink transition-colors hover:bg-surface-hover disabled:opacity-60"
        >
          <RefreshCw aria-hidden className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
          {refreshing
            ? t("verdict.retrying")
            : allSourcesAnswered
              ? t("verdict.recheck")
              : t("verdict.retryUnanswered")}
        </button>
        <Link
          href="/incidents"
          className="inline-flex h-8 items-center gap-1.5 rounded-control bg-brand px-3 text-caption font-semibold text-ink-inverse transition-colors hover:bg-brand-hover"
        >
          {t("verdict.openIncidents")}
          <ArrowUpRight aria-hidden className="h-3.5 w-3.5" />
        </Link>
      </div>
    </Panel>
  );
}
