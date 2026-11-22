"use client";

/**
 * The app launcher: projects as app icons, an app's environments as the
 * places to open, and — where a page needs it — the environment's services
 * as cards.
 *
 * Choosing is one tap per level, never a dropdown: the icons say which app
 * this is before its name does, an environment card shows how that place
 * is doing (its services' health, its traffic over the day) before it is
 * opened, and the chosen path stays visible above what it opened.
 */

import { ArrowRight, X } from "lucide-react";
import Link from "next/link";
import { useId, type ReactNode } from "react";

import { STATUS_LABELS, toneForServiceStatus } from "@/components/service-health/primitives";
import { ProjectLogo } from "@/components/ui/ProjectLogo";
import { toneSpec } from "@/lib/design/status";
import { useFormat, useT } from "@/lib/i18n";
import { envKind, type EnvKind, type PlatformApp, type PlatformEnv, type Pulse } from "@/lib/platform";
import type { ServiceHealthStatus } from "@/lib/serviceHealth";

/* ---------------- small marks ---------------- */

const KIND_COLOUR: Record<EnvKind, string> = {
  prod: "var(--gold)",
  staging: "var(--series-4)",
  test: "var(--series-2)",
  dev: "var(--brand-accent)",
  other: "var(--text-muted)",
};

export function kindColour(key: string): string {
  return KIND_COLOUR[envKind(key)];
}

/** Traffic as a soft area under a line; nothing drawn for no data. */
export function Sparkline({
  values,
  height = 48,
  colour = "var(--brand-accent)",
  className = "",
}: {
  values: number[];
  height?: number;
  colour?: string;
  className?: string;
}) {
  const id = useId();
  if (values.length < 2) return <div style={{ height }} className={className} aria-hidden />;
  const peak = Math.max(...values, 1e-9);
  const step = 100 / (values.length - 1);
  const points = values.map((v, i) => [i * step, height - 2 - (v / peak) * (height - 6)] as const);
  const line = points.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(2)},${y.toFixed(2)}`).join("");
  return (
    <svg
      aria-hidden
      viewBox={`0 0 100 ${height}`}
      preserveAspectRatio="none"
      className={`block w-full ${className}`}
      style={{ height }}
    >
      <defs>
        <linearGradient id={id} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={colour} stopOpacity="0.28" />
          <stop offset="100%" stopColor={colour} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={`${line}L100,${height}L0,${height}Z`} fill={`url(#${id})`} />
      <path d={line} fill="none" stroke={colour} strokeWidth="1.5" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
    </svg>
  );
}

const TALLY_ORDER: ServiceHealthStatus[] = ["critical", "degraded", "stale", "unknown", "not_configured", "healthy"];

/** The environment's services by health, as one segmented bar. */
export function HealthBar({ tally, className = "" }: { tally: Map<ServiceHealthStatus, number>; className?: string }) {
  const t = useT("serviceHealth");
  const total = [...tally.values()].reduce((a, b) => a + b, 0);
  if (!total) return null;
  const parts = TALLY_ORDER.filter((s) => tally.get(s));
  const label = parts
    .map((s) => `${tally.get(s)} ${t.dyn("status", s, STATUS_LABELS[s]).toLocaleLowerCase()}`)
    .join(", ");
  return (
    <span role="img" aria-label={label} title={label} className={`flex h-1.5 w-full gap-[2px] overflow-hidden rounded-full ${className}`}>
      {parts.map((s) => (
        <span
          key={s}
          className={`h-full ${toneSpec(toneForServiceStatus(s)).dot}`}
          style={{ flexGrow: tally.get(s) }}
        />
      ))}
    </span>
  );
}

function StatusDot({ status, className = "" }: { status: ServiceHealthStatus | null; className?: string }) {
  if (!status) return null;
  return <span aria-hidden className={`rounded-full ${toneSpec(toneForServiceStatus(status)).dot} ${className}`} />;
}

/* ---------------- equal columns ---------------- */

/**
 * N items share the row equally — two halves, three thirds, up to five —
 * so a launcher with few apps or environments fills its width instead of
 * leaving a ragged gap. Narrower screens cap the columns.
 */
export function FillGrid({
  count,
  max = 5,
  gap = "gap-4",
  children,
  className = "",
  ...rest
}: {
  count: number;
  max?: number;
  gap?: string;
  children: ReactNode;
  className?: string;
} & React.HTMLAttributes<HTMLElement>) {
  const n = Math.max(1, count);
  const style = {
    "--cols-sm": Math.min(n, 2),
    "--cols-md": Math.min(n, 3),
    "--cols-lg": Math.min(n, max),
  } as React.CSSProperties;
  return (
    <ul
      {...rest}
      style={style}
      className={`grid grid-cols-1 sm:[grid-template-columns:repeat(var(--cols-sm),minmax(0,1fr))] md:[grid-template-columns:repeat(var(--cols-md),minmax(0,1fr))] xl:[grid-template-columns:repeat(var(--cols-lg),minmax(0,1fr))] ${gap} ${className}`}
    >
      {children}
    </ul>
  );
}

/* ---------------- level 1: apps ---------------- */

export function AppGrid({
  apps,
  selectedId,
  onSelect,
}: {
  apps: PlatformApp[];
  selectedId: string | null;
  onSelect: (projectId: string | null) => void;
}) {
  const t = useT("apps");
  const ts = useT("serviceHealth");
  return (
    <FillGrid count={apps.length} aria-label={t("title")} data-testid="app-grid">
      {apps.map((app, i) => {
        const active = app.projectId === selectedId;
        const dim = selectedId !== null && !active;
        return (
          <li key={app.projectId} style={{ animationDelay: `${i * 45}ms` }} className="min-w-0 animate-[rise-in_.4s_cubic-bezier(.2,.8,.2,1)_both]">
            <button
              type="button"
              aria-pressed={active}
              onClick={() => onSelect(active ? null : app.projectId)}
              data-testid={`app-${app.projectKey}`}
              className={`group relative flex h-full w-full flex-col items-center overflow-hidden rounded-2xl border bg-surface px-6 pt-8 pb-6 text-center transition-[transform,border-color,box-shadow,opacity] duration-200 outline-none hover:-translate-y-0.5 hover:shadow-[0_18px_40px_-24px_rgb(0_0_0/0.45)] focus-visible:ring-2 focus-visible:ring-focus ${
                active
                  ? "border-brand-accent/70 ring-1 ring-brand-accent/40"
                  : "border-border hover:border-border-strong"
              } ${dim ? "opacity-70 hover:opacity-100" : ""}`}
            >
              {/* A soft wash of the brand behind the chosen app's icon. */}
              <span
                aria-hidden
                className={`pointer-events-none absolute inset-x-0 top-0 h-40 transition-opacity duration-300 ${active ? "opacity-100" : "opacity-0 group-hover:opacity-60"}`}
                style={{ background: "radial-gradient(60% 100% at 50% 0%, color-mix(in srgb, var(--brand-accent) 14%, transparent), transparent)" }}
              />
              <span
                className="relative grid h-32 w-32 place-items-center rounded-[2rem] border border-border bg-gradient-to-b from-surface to-surface-2 transition-transform duration-200 group-hover:scale-[1.03]"
                style={{
                  boxShadow:
                    "inset 0 1px 0 rgb(255 255 255 / 0.07), 0 18px 32px -18px rgb(0 0 0 / 0.5), 0 2px 4px -2px rgb(0 0 0 / 0.12)",
                }}
              >
                <ProjectLogo projectId={app.projectId} name={app.name} size="hero" variant="mark" />
                {app.worst ? (
                  <span
                    title={ts.dyn("status", app.worst, STATUS_LABELS[app.worst])}
                    className="absolute -top-1.5 -right-1.5 grid h-6 w-6 place-items-center rounded-full bg-surface"
                  >
                    <StatusDot status={app.worst} className="h-3.5 w-3.5" />
                  </span>
                ) : null}
              </span>
              <span className="relative mt-5 w-full truncate font-[family-name:var(--font-display)] text-[1.375rem] font-bold tracking-[-0.025em] text-ink">
                {app.name}
              </span>
              <span className="relative mt-1 text-caption text-ink-muted">
                {t("meta", { envs: app.envs.length, services: app.services })}
              </span>
              <span className="relative mt-4 flex flex-wrap justify-center gap-1.5">
                {app.envs.map((env) => (
                  <span
                    key={env.environmentId}
                    className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface-2 px-2.5 py-0.5 font-mono text-[11px] text-ink-secondary"
                  >
                    <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: kindColour(env.key) }} />
                    {env.key}
                  </span>
                ))}
              </span>
              <HealthBar tally={app.tally} className="relative mt-5 max-w-[14rem]" />
            </button>
          </li>
        );
      })}
    </FillGrid>
  );
}

/* ---------------- level 2: the app's environments ---------------- */

export function AppShelf({
  app,
  onClose,
  children,
  title,
  action,
}: {
  app: PlatformApp;
  onClose: () => void;
  children: ReactNode;
  title: string;
  action?: ReactNode;
}) {
  const t = useT("apps");
  return (
    <section
      aria-label={app.name}
      data-testid="app-shelf"
      className="animate-[rise-in_.35s_cubic-bezier(.2,.8,.2,1)_both] rounded-[1.25rem] border border-border bg-surface-2/60 p-4 sm:p-5"
    >
      <header className="mb-4 flex items-center gap-3">
        <ProjectLogo projectId={app.projectId} name={app.name} size="md" variant="mark" />
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-[1.0625rem] font-semibold tracking-[-0.01em] text-ink">{app.name}</h2>
          <p className="text-micro text-ink-muted">{title}</p>
        </div>
        {action}
        <button
          type="button"
          onClick={onClose}
          aria-label={t("close", { app: app.name })}
          className="grid h-8 w-8 place-items-center rounded-full text-ink-muted transition-colors hover:bg-surface-hover hover:text-ink"
        >
          <X className="h-4 w-4" aria-hidden />
        </button>
      </header>
      {children}
    </section>
  );
}

export function EnvCard({
  env,
  pulse,
  pending,
  href,
  onSelect,
  active = false,
  cta,
  stats,
  series,
}: {
  env: PlatformEnv;
  pulse: Pulse | null;
  /** The traffic read has not answered yet. */
  pending: boolean;
  href?: string;
  onSelect?: () => void;
  active?: boolean;
  cta?: string;
  /** Figures to show instead of the traffic ones (Logs shows log lines). */
  stats?: { label: string; value: string; tone?: string }[];
  /** A series to draw instead of requests per bucket. */
  series?: { values: number[]; colour?: string } | null;
}) {
  const t = useT("apps");
  const ts = useT("serviceHealth");
  const fmt = useFormat();
  const kind = envKind(env.key);
  const colour = KIND_COLOUR[kind];
  const errorRatio = pulse?.requests ? (pulse.errors ?? 0) / pulse.requests : null;
  const drawn =
    series !== undefined
      ? series && series.values.length > 1 && series.values.some((v) => v > 0)
        ? series
        : null
      : pulse && pulse.perBucket.length > 1 && pulse.requests
        ? { values: pulse.perBucket, colour: kind === "prod" ? "var(--gold)" : "var(--brand-accent)" }
        : null;
  const body = (
    <>
      <span aria-hidden className="absolute inset-y-0 left-0 w-[3px]" style={{ background: colour }} />
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-mono text-[10.5px] font-medium tracking-[0.14em] text-ink-muted uppercase">{t(`kind.${kind}`)}</p>
          <h3 className="mt-1 truncate font-[family-name:var(--font-display)] text-[1.625rem] leading-none font-bold tracking-[-0.03em] text-ink">
            {env.key}
          </h3>
        </div>
        {env.worst ? (
          <span className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-micro font-semibold ${toneSpec(toneForServiceStatus(env.worst)).chip}`}>
            <StatusDot status={env.worst} className="h-1.5 w-1.5" />
            {ts.dyn("status", env.worst, STATUS_LABELS[env.worst])}
          </span>
        ) : null}
      </div>

      <div className="relative mt-4 -mx-1">
        {drawn ? (
          <Sparkline values={drawn.values} height={52} colour={drawn.colour ?? "var(--brand-accent)"} />
        ) : (
          <div className="grid h-[52px] place-items-center rounded-lg border border-dashed border-border text-micro text-ink-muted">
            {pending ? t("trafficPending") : t("noTraffic")}
          </div>
        )}
      </div>

      {stats ? (
        <dl className="mt-4 grid grid-cols-3 gap-3">
          {stats.map((stat) => (
            <div key={stat.label} className="min-w-0">
              <dt className="truncate text-[11px] text-ink-muted">{stat.label}</dt>
              <dd data-tabular className={`mt-0.5 truncate font-mono text-[0.9375rem] font-semibold ${stat.tone ?? (stat.value === "—" ? "text-ink-muted" : "text-ink")}`}>
                {stat.value}
              </dd>
            </div>
          ))}
        </dl>
      ) : (
      <dl className="mt-4 grid grid-cols-3 gap-3">
        <div>
          <dt className="text-[11px] text-ink-muted">{t("stat.requests")}</dt>
          <dd data-tabular className="mt-0.5 font-mono text-[0.9375rem] font-semibold text-ink">
            {pulse?.requests === null || pulse === null ? "—" : fmt.number(pulse.requests)}
          </dd>
        </div>
        <div>
          <dt className="text-[11px] text-ink-muted">{t("stat.errors")}</dt>
          <dd data-tabular className={`mt-0.5 font-mono text-[0.9375rem] font-semibold ${errorRatio ? "text-critical" : "text-ink"}`}>
            {errorRatio === null ? "—" : `${fmt.number(errorRatio * 100, { maximumFractionDigits: errorRatio < 0.1 ? 1 : 0 })}%`}
          </dd>
        </div>
        <div>
          <dt className="text-[11px] text-ink-muted">{t("stat.p95")}</dt>
          <dd
            data-tabular
            className={`mt-0.5 font-mono text-[0.9375rem] font-semibold ${
              pulse?.worstP95 != null && pulse.worstP95 >= 1000 ? "text-warning" : "text-ink"
            }`}
          >
            {pulse?.worstP95 == null ? "—" : fmt.latency(pulse.worstP95)}
          </dd>
        </div>
      </dl>
      )}

      <div className="mt-4 flex items-center gap-3 border-t border-border pt-3.5">
        <HealthBar tally={env.tally} className="max-w-[7rem]" />
        <span className="text-micro whitespace-nowrap text-ink-secondary">{t("services", { count: env.rows.length })}</span>
        <span className="ml-auto inline-flex items-center gap-1 text-micro font-semibold text-brand-accent">
          {cta ?? t("open")}
          <ArrowRight aria-hidden className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" />
        </span>
      </div>
    </>
  );
  const className = `group relative flex h-full w-full flex-col overflow-hidden rounded-2xl border bg-surface p-5 pl-6 text-left transition-[transform,border-color,box-shadow] duration-200 hover:-translate-y-0.5 hover:border-border-strong hover:shadow-[0_18px_40px_-24px_rgb(0_0_0/0.45)] focus-visible:ring-2 focus-visible:ring-focus outline-none ${
    active ? "border-brand-accent/70 ring-1 ring-brand-accent/40" : "border-border"
  }`;
  return (
    <li className="min-w-0">
      {href ? (
        <Link href={href} className={className} data-testid={`env-${env.projectKey}-${env.key}`}>
          {body}
        </Link>
      ) : (
        <button type="button" aria-pressed={active} onClick={onSelect} className={className} data-testid={`env-${env.projectKey}-${env.key}`}>
          {body}
        </button>
      )}
    </li>
  );
}

export function EnvGrid({ count, children }: { count: number; children: ReactNode }) {
  return <FillGrid count={count}>{children}</FillGrid>;
}
