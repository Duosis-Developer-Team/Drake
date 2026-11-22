"use client";

/**
 * The Overview's picture of the whole platform, in the dashboards' own
 * visual language: the pulse (every bound service's traffic, speed and
 * errors on one time axis, exactly as an environment dashboard draws its
 * own), then every app as a card whose environments show how they are
 * doing and open their dashboards.
 *
 * Both draw from one overview read over every bound service. A section
 * that did not answer says so; nothing becomes zero.
 */

import { ArrowRight, ChevronRight } from "lucide-react";
import Link from "next/link";

import { FillGrid, HealthBar, Sparkline, kindColour } from "@/components/apps/AppLauncher";
import { OverviewWidget } from "@/components/features/dashboards/widgets";
import s from "@/components/features/dashboards/dashboards.module.css";
import { TipProvider } from "@/components/features/dashboards/tip";
import { STATUS_LABELS, toneForServiceStatus } from "@/components/service-health/primitives";
import { ProjectLogo } from "@/components/ui/ProjectLogo";
import { DataState } from "@/components/state/DataState";
import { toneSpec } from "@/lib/design/status";
import type { Overview, OverviewRange } from "@/lib/dashboards";
import type { DeploymentRow } from "@/lib/deployments";
import { useFormat, useT } from "@/lib/i18n";
import { pulseOf, type PlatformApp } from "@/lib/platform";

export function PlatformPulse({
  overview,
  failed,
  truncated,
  deploys,
  range,
  onRange,
  hasServices,
}: {
  overview: Overview | null;
  failed: boolean;
  truncated: boolean;
  deploys: DeploymentRow[];
  range: OverviewRange;
  onRange: (range: OverviewRange) => void;
  hasServices: boolean;
}) {
  const t = useT("commandCenter");
  const td = useT("dashboards");
  const ta = useT("apps");
  return (
    <div className={s.root} data-testid="platform-pulse">
      <TipProvider>
        <section className={s.card} aria-label={t("pulse.title")}>
          <header>
            <div>
              <h2>{t("pulse.title")}</h2>
              <p>
                {t("pulse.description", { range: td(`range.${range}`) })}
                {truncated ? ` · ${ta("trafficTruncated", { count: 32 })}` : ""}
              </p>
            </div>
            <div className={s.pills} role="group" aria-label={td("rangeLabel")}>
              {(["6h", "24h"] as const).map((r) => (
                <button key={r} type="button" aria-pressed={range === r} onClick={() => onRange(r)}>
                  {td(`range.${r}`)}
                </button>
              ))}
            </div>
          </header>
          {overview ? (
            <OverviewWidget overview={overview} deploys={deploys} t={td} />
          ) : (
            <div className={s.state}>
              {!hasServices ? (
                <DataState kind="empty" title={t("pulse.none")} description={t("pulse.noneHint")} />
              ) : failed ? (
                <DataState kind="error" title={t("pulse.failed")} />
              ) : (
                <DataState kind="loading" />
              )}
            </div>
          )}
        </section>
      </TipProvider>
    </div>
  );
}

function AppCard({ app, overview, index }: { app: PlatformApp; overview: Overview | null; index: number }) {
  const t = useT("apps");
  const ts = useT("serviceHealth");
  const fmt = useFormat();
  return (
    <article
      data-testid={`overview-app-${app.projectKey}`}
      style={{ animationDelay: `${index * 60}ms` }}
      className="flex h-full animate-[rise-in_.45s_cubic-bezier(.2,.8,.2,1)_both] flex-col overflow-hidden rounded-panel border border-border bg-surface"
    >
      <Link
        href={`/dashboards?project=${app.projectId}`}
        className="group flex items-center gap-3.5 px-5 pt-5 pb-4 outline-none focus-visible:ring-2 focus-visible:ring-focus"
      >
        <span
          className="grid h-12 w-12 shrink-0 place-items-center rounded-[0.9rem] border border-border bg-gradient-to-b from-surface to-surface-2"
          style={{ boxShadow: "inset 0 1px 0 rgb(255 255 255 / 0.07), 0 8px 18px -12px rgb(0 0 0 / 0.45)" }}
        >
          <ProjectLogo projectId={app.projectId} name={app.name} size="md" variant="mark" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[1.0625rem] font-semibold tracking-[-0.015em] text-ink group-hover:text-brand-accent">
            {app.name}
          </span>
          <span className="block text-micro text-ink-muted">{t("meta", { envs: app.envs.length, services: app.services })}</span>
        </span>
        {app.worst ? (
          <span className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-micro font-semibold ${toneSpec(toneForServiceStatus(app.worst)).chip}`}>
            <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${toneSpec(toneForServiceStatus(app.worst)).dot}`} />
            {ts.dyn("status", app.worst, STATUS_LABELS[app.worst])}
          </span>
        ) : null}
      </Link>
      <ul className="border-t border-border">
        {app.envs.map((env) => {
          const pulse = pulseOf(overview, env.bindingIds);
          const ratio = pulse?.requests ? (pulse.errors ?? 0) / pulse.requests : null;
          return (
            <li key={env.environmentId} className="border-b border-border last:border-b-0">
              <Link
                href={`/dashboards/${env.environmentId}`}
                className="group grid grid-cols-[4.5rem_minmax(0,1fr)_auto] items-center gap-4 px-5 py-3 transition-colors outline-none hover:bg-surface-hover focus-visible:bg-surface-hover sm:grid-cols-[5.5rem_minmax(0,1fr)_14rem_1rem]"
              >
                <span className="flex min-w-0 flex-col gap-1.5">
                  <span className="flex items-center gap-2">
                    <span aria-hidden className="h-3 w-[3px] rounded-full" style={{ background: kindColour(env.key) }} />
                    <span className="truncate text-[0.9375rem] font-semibold text-ink">{env.key}</span>
                  </span>
                  <HealthBar tally={env.tally} />
                </span>
                <span className="min-w-0">
                  {pulse && pulse.perBucket.length > 1 && pulse.requests ? (
                    <Sparkline values={pulse.perBucket} height={34} colour={env.key.startsWith("prod") ? "var(--gold)" : "var(--brand-accent)"} />
                  ) : (
                    <span className="block text-micro text-ink-muted">{overview ? t("noTraffic") : t("trafficPending")}</span>
                  )}
                </span>
                <span className="hidden grid-cols-3 gap-3 text-right sm:grid">
                  <span>
                    <span className="block text-[10.5px] text-ink-muted">{t("stat.requests")}</span>
                    <span data-tabular className="font-mono text-[0.8125rem] font-semibold text-ink">
                      {pulse?.requests == null ? "—" : fmt.number(pulse.requests)}
                    </span>
                  </span>
                  <span>
                    <span className="block text-[10.5px] text-ink-muted">{t("stat.errors")}</span>
                    <span data-tabular className={`font-mono text-[0.8125rem] font-semibold ${ratio ? "text-critical" : "text-ink"}`}>
                      {ratio === null ? "—" : `${fmt.number(ratio * 100, { maximumFractionDigits: ratio < 0.1 ? 1 : 0 })}%`}
                    </span>
                  </span>
                  <span>
                    <span className="block text-[10.5px] text-ink-muted">p95</span>
                    <span
                      data-tabular
                      className={`font-mono text-[0.8125rem] font-semibold ${pulse?.worstP95 != null && pulse.worstP95 >= 1000 ? "text-warning" : "text-ink"}`}
                    >
                      {pulse?.worstP95 == null ? "—" : fmt.latency(pulse.worstP95)}
                    </span>
                  </span>
                </span>
                <ChevronRight aria-hidden className="h-4 w-4 text-ink-muted transition-transform group-hover:translate-x-0.5" />
              </Link>
            </li>
          );
        })}
      </ul>
    </article>
  );
}

export function AppsBoard({ apps, overview }: { apps: PlatformApp[]; overview: Overview | null }) {
  const t = useT("commandCenter");
  const ta = useT("apps");
  if (apps.length === 0) return null;
  return (
    <section aria-labelledby="overview-apps" data-testid="overview-apps" className="flex flex-col gap-3">
      <div className="flex items-end justify-between gap-3 px-0.5">
        <div>
          <h2 id="overview-apps" className="text-[1.125rem] font-semibold tracking-[-0.02em] text-ink">
            {t("apps.title")}
          </h2>
          <p className="text-caption text-ink-muted">{t("apps.description")}</p>
        </div>
        <Link href="/dashboards" className="inline-flex items-center gap-1 text-caption font-medium text-brand-accent hover:underline">
          {ta("title")}
          <ArrowRight aria-hidden className="h-3.5 w-3.5" />
        </Link>
      </div>
      {/* Apps share the row equally: two halves, three thirds. */}
      <FillGrid count={apps.length} max={3}>
        {apps.map((app, i) => (
          <li key={app.projectId} className="min-w-0">
            <AppCard app={app} overview={overview} index={i} />
          </li>
        ))}
      </FillGrid>
    </section>
  );
}
