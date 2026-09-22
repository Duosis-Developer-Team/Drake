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
    "rounded-full border border-border bg-surface px-2.5 py-1 font-medium text-ink-secondary transition-colors hover:bg-surface-hover hover:text-ink";
  const headline =
    verdict.headlineKind === "critical"
      ? t("verdict.headline.critical", { count: verdict.criticalCount })
      : verdict.headlineKind === "warning"
        ? t("verdict.headline.warning", { count: verdict.warningCount })
        : t("verdict.headline.clear", { answered: verdict.sourcesAnswered, total: verdict.sourcesTotal });

  return (
    <Panel
      radius="canvas"
      flush
      tone={worstTone === "success" ? "default" : worstTone}
      data-testid="verdict-panel"
    >
      <div className="flex items-center justify-between gap-4 px-7 pt-6 pb-5">
        <div className="flex min-w-0 items-center gap-3.5">
          <span aria-hidden className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-full ${spec.chip}`}>
            <StatusIcon className="h-5 w-5" />
          </span>
          <div className="min-w-0">
            <p className="text-[1.0625rem] leading-6 font-semibold tracking-[-0.01em] text-ink">
              {t("verdict.title")}
            </p>
            <p className="text-caption text-ink-muted">{t("verdict.scope")}</p>
          </div>
        </div>
        <span
          data-testid="verdict-sources"
          className="inline-flex shrink-0 items-center gap-2 rounded-full border border-border bg-surface-2 px-3 py-1.5 text-caption font-medium text-ink"
        >
          <span
            aria-hidden
            className={`h-2 w-2 rounded-full ${allSourcesAnswered ? "bg-healthy" : "bg-warning"}`}
          />
          <span className="sr-only">
            {t("verdict.sourcesAnswered", { answered: verdict.sourcesAnswered, total: verdict.sourcesTotal })}
          </span>
          <span aria-hidden>
            {t("verdict.sourcesShort", { answered: verdict.sourcesAnswered, total: verdict.sourcesTotal })}
          </span>
        </span>
      </div>

      {/* The inset: same theme family, one step up — depth from the surface
          ladder, with a faint glow in the verdict's own status colour. */}
      <div
        className="relative mx-2.5 overflow-hidden rounded-[1.25rem] border border-border bg-surface-2"
        style={{
          backgroundImage: `radial-gradient(120% 140% at 100% 0%, color-mix(in srgb, var(${spec.token}) 26%, transparent) 0%, transparent 70%)`,
        }}
      >
        <div className="flex flex-wrap items-center justify-between gap-6 px-6 pt-6 pb-5">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-caption text-ink-muted">{t("verdict.flagged")}</p>
              {/* "All clear" only when every source answered — a quiet page
                  with silent sources is a partial view, not a healthy one. */}
              <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-micro font-medium ${labelSpec.chip}`}>
                <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${labelSpec.dot}`} />
                {flagged > 0
                  ? t(`tone.${worstTone}`)
                  : allSourcesAnswered
                    ? t("verdict.allClear")
                    : t("verdict.partialView")}
              </span>
            </div>
            <p className="mt-2 flex items-baseline gap-2.5">
              <span
                data-tabular
                className={`text-[4.25rem] leading-none font-semibold tracking-[-0.05em] ${numberTone}`}
              >
                {flagged}
              </span>
              <span className="text-body text-ink-muted">{t("verdict.signals", { count: flagged })}</span>
            </p>
            <p
              data-testid="verdict-headline"
              style={{ textWrap: "balance" }}
              className="mt-3 max-w-md text-body font-medium text-ink-secondary"
            >
              {headline}
            </p>
          </div>
          <SourceRing
            answered={verdict.sourcesAnswered}
            total={verdict.sourcesTotal}
            complete={allSourcesAnswered}
          />
        </div>

        {/* Always-present breakdown: zeros stay visible as zeros instead of
            the row collapsing to nothing when the estate is quiet. */}
        <dl className="grid grid-cols-1 border-t border-border sm:grid-cols-[minmax(0,0.7fr)_minmax(0,0.7fr)_minmax(0,1.6fr)]">
          <div className="flex items-center justify-between gap-3 px-6 py-4 sm:justify-start">
            <dt className="flex items-center gap-2 text-caption text-ink-muted">
              <span aria-hidden className={`h-2 w-2 rounded-full ${toneSpec("critical").dot}`} />
              {t("tone.critical")}
            </dt>
            <dd
              data-tabular
              data-testid={verdict.criticalCount > 0 ? "verdict-critical-count" : undefined}
              className={`text-[1.375rem] leading-none font-semibold ${verdict.criticalCount > 0 ? "text-critical" : "text-ink"}`}
            >
              {verdict.criticalCount}
            </dd>
          </div>
          <div className="flex items-center justify-between gap-3 border-t border-border px-6 py-4 sm:justify-start sm:border-t-0 sm:border-l">
            <dt className="flex items-center gap-2 text-caption text-ink-muted">
              <span aria-hidden className={`h-2 w-2 rounded-full ${toneSpec("warning").dot}`} />
              {t("tone.warning")}
            </dt>
            <dd
              data-tabular
              data-testid={verdict.warningCount > 0 ? "verdict-warning-count" : undefined}
              className={`text-[1.375rem] leading-none font-semibold ${verdict.warningCount > 0 ? "text-warning" : "text-ink"}`}
            >
              {verdict.warningCount}
            </dd>
          </div>
          <div className="flex min-w-0 flex-wrap items-center gap-2 border-t border-border px-6 py-4 text-micro sm:border-t-0 sm:border-l">
            <dt className="text-caption text-ink-muted">{t("verdict.affected")}</dt>
            <dd className="flex min-w-0 flex-wrap items-center gap-2">
              <span
                data-tabular
                className="mr-1 text-[1.375rem] leading-none font-semibold text-ink"
              >
                {projects + services + clusters}
              </span>
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
              {projects + services + clusters === 0 ? (
                <span className="text-caption text-ink-muted">{t("verdict.nothingInScope")}</span>
              ) : null}
              {verdict.oldestSuspectEvidence ? (
                <span data-testid="verdict-oldest-evidence" className="text-ink-muted">
                  {t("verdict.oldest", { label: verdict.oldestSuspectEvidence.label })}{" "}
                  <RelativeTime value={verdict.oldestSuspectEvidence.asOf} />
                </span>
              ) : null}
            </dd>
          </div>
        </dl>
      </div>

      <div className="flex gap-2.5 p-2.5 text-body font-medium">
        <button
          type="button"
          onClick={onRefresh}
          disabled={refreshing}
          data-testid={allSourcesAnswered ? undefined : "verdict-retry-sources"}
          className="flex flex-1 items-center justify-center gap-2 rounded-full border border-border bg-surface px-6 py-3 text-ink transition-colors hover:bg-surface-hover disabled:opacity-60"
        >
          <RefreshCw aria-hidden className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
          {refreshing
            ? t("verdict.retrying")
            : allSourcesAnswered
              ? t("verdict.recheck")
              : t("verdict.retryUnanswered")}
        </button>
        <Link
          href="/incidents"
          className="flex flex-[2] items-center justify-center gap-2 rounded-full bg-brand py-3 text-ink-inverse transition-colors hover:bg-brand-hover"
        >
          {t("verdict.openIncidents")}
          <ArrowUpRight aria-hidden className="h-4 w-4" />
        </Link>
      </div>
    </Panel>
  );
}

/**
 * Sources answered as a ring — the share of the estate this verdict can see.
 * Track, fill and ink all come from the theme, so the ring reads the same way
 * the rest of the page does in light and in dark.
 */
function SourceRing({
  answered,
  total,
  complete,
}: {
  answered: number;
  total: number;
  complete: boolean;
}) {
  const t = useT("commandCenter");
  const size = 128;
  const stroke = 14;
  const fill = 8;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const share = total > 0 ? answered / total : 0;
  return (
    <div aria-hidden className="relative shrink-0" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="var(--surface-3)" strokeWidth={stroke} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={complete ? "var(--brand-accent)" : "var(--status-warning)" /* i18n-ignore: CSS */}
          strokeWidth={fill}
          strokeLinecap="round"
          strokeDasharray={`${circumference * share} ${circumference}`}
        />
      </svg>
      <span className="absolute inset-0 flex flex-col items-center justify-center">
        <span data-tabular className="text-2xl leading-none font-semibold tracking-tight text-ink">
          {answered}/{total}
        </span>
        <span className="mt-1 text-micro text-ink-muted">{t("verdict.sources")}</span>
      </span>
    </div>
  );
}
