"use client";

/**
 * Objectives as a board, not a list.
 *
 * Each objective is a card that answers its three questions at a glance:
 * how close it is to its promise (compliance against the target, with its
 * recent trend under a dashed target line), how much room is left (the
 * error budget as a ring that empties), and whether it is burning now.
 * Cards are grouped by application, with the environment's colour on the
 * edge, so "which app is in trouble" is visible before any name is read.
 *
 * The rules of the old list still hold: an objective nobody has measured
 * is never drawn as healthy, and a missing number is a dash, never 0%.
 */

import { Activity, ArrowRight, Flame, Timer } from "lucide-react";
import Link from "next/link";
import { useEffect, useId, useMemo, useState } from "react";

import { kindColour } from "@/components/apps/AppLauncher";
import { SloBadge } from "@/components/alerting/primitives";
import { StatusBadge } from "@/components/state/StatusBadge";
import { ProjectLogo } from "@/components/ui/ProjectLogo";
import { apiGet } from "@/lib/api";
import { toneSpec, type StatusTone } from "@/lib/design/status";
import { useFormat, useT } from "@/lib/i18n";
import {
  formatBudget,
  formatRatio,
  formatTarget,
  type Slo,
  type SloEvaluation,
  type SloStatus,
} from "@/lib/alerting";
import { useResource } from "@/lib/useResource";

/** Verdict → tone. The unmeasured states are never the healthy tone. */
export const SLO_TONE: Record<SloStatus, StatusTone> = {
  healthy: "success",
  warning: "warning",
  critical: "critical",
  exhausted: "critical",
  insufficient_data: "unknown",
  not_configured: "unknown",
  stale: "stale",
  query_failed: "critical",
};

export const UNMEASURED: SloStatus[] = ["insufficient_data", "stale", "query_failed", "not_configured"];

/* ---------------- project names and ids, by key ---------------- */

/** The objectives read names projects by key; logos and names need the id. */
export function useProjectsByKey(): Map<string, { id: string; name: string }> {
  const projects = useResource<{ projects: { id: string; project_key: string; display_name: string }[] }>(
    "/v1/projects?lifecycle=active&limit=100",
  );
  return useMemo(
    () =>
      new Map(
        (projects.data?.projects ?? []).map((p) => [p.project_key, { id: p.id, name: p.display_name }]),
      ),
    [projects.data],
  );
}

/* ---------------- the budget ring ---------------- */

function budgetTone(remaining: number | null, tone: StatusTone): StatusTone {
  if (remaining === null) return tone === "success" ? "unknown" : tone;
  if (remaining < 0.25) return "critical";
  if (remaining < 0.5) return "warning";
  return "success";
}

/** Error budget left, as a ring that empties. Overspent draws a full red
 * ring and says by how much; an unmeasured budget is a dashed empty ring. */
export function BudgetRing({
  remaining,
  tone,
  size = 88,
  label,
}: {
  remaining: number | null;
  tone: StatusTone;
  size?: number;
  label: string;
}) {
  const stroke = 8;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const share = remaining === null ? 0 : Math.max(0, Math.min(1, remaining));
  const colour = `var(${toneSpec(budgetTone(remaining, tone)).token})`;
  const overspent = remaining !== null && remaining < 0;
  return (
    <div
      className="relative grid shrink-0 place-items-center"
      style={{ width: size, height: size }}
      role="img"
      aria-label={`${label}: ${formatBudget(remaining)}`}
    >
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90" aria-hidden>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="var(--surface-3)"
          strokeWidth={stroke}
          strokeDasharray={remaining === null ? "3 5" : undefined}
        />
        {remaining !== null ? (
          <circle
            cx={size / 2}
            cy={size / 2}
            r={r}
            fill="none"
            stroke={overspent ? "var(--status-critical)" : colour}
            strokeWidth={stroke}
            strokeLinecap="round"
            strokeDasharray={`${(overspent ? 1 : share) * c} ${c}`}
            className="transition-[stroke-dasharray] duration-700"
          />
        ) : null}
      </svg>
      <span className="absolute inset-0 grid place-items-center text-center">
        <span>
          <span
            data-tabular
            className={`block font-mono text-[0.9375rem] leading-none font-semibold ${overspent ? "text-critical" : "text-ink"}`}
          >
            {formatBudget(remaining)}
          </span>
          <span className="mt-1 block text-[10px] leading-none text-ink-muted">{label}</span>
        </span>
      </span>
    </div>
  );
}

/* ---------------- the compliance trend ---------------- */

function useHistory(sloId: string, enabled: boolean): SloEvaluation[] | null {
  const [items, setItems] = useState<SloEvaluation[] | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let active = true;
    apiGet<{ evaluations: SloEvaluation[] }>(`/v1/slo/${encodeURIComponent(sloId)}/evaluations?limit=48`)
      .then((body) => active && setItems(body.evaluations))
      .catch(() => active && setItems([]));
    return () => {
      active = false;
    };
  }, [sloId, enabled]);
  return items;
}

/** Compliance over the recent evaluations, oldest left, with the target as
 * a dashed line: above it the promise held, below it it did not. */
function ComplianceTrend({ history, target, colour }: { history: SloEvaluation[]; target: number; colour: string }) {
  const id = useId();
  const points = [...history]
    .reverse()
    .map((e) => e.compliance_ratio)
    .filter((v): v is number => v !== null);
  if (points.length < 2) return null;
  const lo = Math.min(...points, target);
  const hi = Math.max(...points, target, lo + 1e-4);
  const pad = (hi - lo) * 0.25 || 0.0005;
  const min = lo - pad;
  const max = Math.min(1, hi + pad);
  const h = 40;
  const y = (v: number) => h - 2 - ((v - min) / (max - min || 1)) * (h - 4);
  const step = 100 / (points.length - 1);
  const line = points.map((v, i) => `${i ? "L" : "M"}${(i * step).toFixed(2)},${y(v).toFixed(2)}`).join("");
  return (
    <svg viewBox={`0 0 100 ${h}`} preserveAspectRatio="none" className="block h-10 w-full" aria-hidden>
      <defs>
        <linearGradient id={id} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={colour} stopOpacity="0.25" />
          <stop offset="100%" stopColor={colour} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={`${line}L100,${h}L0,${h}Z`} fill={`url(#${id})`} />
      <line
        x1="0"
        x2="100"
        y1={y(target)}
        y2={y(target)}
        stroke="var(--text-muted)"
        strokeDasharray="3 3"
        strokeWidth="1"
        vectorEffect="non-scaling-stroke"
      />
      <path d={line} fill="none" stroke={colour} strokeWidth="1.75" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
    </svg>
  );
}

/* ---------------- one objective ---------------- */

export function ObjectiveCard({ slo, index = 0 }: { slo: Slo; index?: number }) {
  const t = useT("alerting");
  const fmt = useFormat();
  const evaluation = slo.evaluation;
  const tone: StatusTone = evaluation ? SLO_TONE[evaluation.status] : "unknown";
  const spec = toneSpec(tone);
  const activeBurn = evaluation?.burn_rates.find((rate) => rate.active) ?? null;
  const remaining = evaluation?.error_budget_remaining ?? null;
  const history = useHistory(slo.id, evaluation !== null);
  const Icon = slo.indicator === "latency" ? Timer : Activity;
  const accent = slo.environment_key ? kindColour(slo.environment_key) : "var(--text-muted)";
  const compliance = evaluation?.compliance_ratio ?? null;
  const below = compliance !== null && compliance < slo.objective_ratio;

  return (
    <li
      data-testid={`slo-row-${slo.slo_key}`}
      style={{ animationDelay: `${index * 40}ms` }}
      className={`group relative flex animate-[rise-in_.4s_cubic-bezier(.2,.8,.2,1)_both] flex-col overflow-hidden rounded-2xl border bg-surface p-5 pl-6 transition-[transform,border-color,box-shadow] duration-200 hover:-translate-y-0.5 hover:border-border-strong hover:shadow-[0_18px_40px_-24px_rgb(0_0_0/0.45)] ${
        tone === "critical" ? "border-critical/50" : tone === "warning" ? "border-warning/50" : "border-border"
      }`}
    >
      <span aria-hidden className="absolute inset-y-0 left-0 w-[3px]" style={{ background: accent }} />
      {/* A wash of the verdict's colour behind the head of the card. */}
      <span
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-24 opacity-60"
        style={{
          background: `radial-gradient(70% 100% at 0% 0%, color-mix(in srgb, var(${spec.token}) 10%, transparent), transparent)`,
        }}
      />

      <div className="relative flex items-start gap-3">
        <span className={`grid h-9 w-9 shrink-0 place-items-center rounded-xl ${spec.chip}`}>
          <Icon aria-hidden className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <Link
            href={`/slo/${slo.id}`}
            className="block truncate text-[0.9375rem] font-semibold tracking-[-0.01em] text-ink outline-none after:absolute after:inset-0 after:rounded-2xl focus-visible:after:ring-2 focus-visible:after:ring-focus"
          >
            {slo.display_name}
          </Link>
          <p className="mt-0.5 truncate font-mono text-[11px] text-ink-muted">
            {[slo.environment_key, slo.service_key].filter(Boolean).join(" · ")} ·{" "}
            {t.dyn("indicator", slo.indicator, slo.indicator)}
          </p>
        </div>
        <span className="relative shrink-0">
          {evaluation ? (
            <SloBadge status={evaluation.status} />
          ) : (
            <StatusBadge status="unknown" label={t("sloList.row.notEvaluated")} size="compact" />
          )}
        </span>
      </div>

      <div className="relative mt-4 flex items-center gap-4">
        <BudgetRing remaining={remaining} tone={tone} label={t("sloList.row.budgetLeft")} />
        <div className="min-w-0 flex-1">
          <p className="text-[11px] text-ink-muted">{t("sloList.row.compliance")}</p>
          <p className="flex flex-wrap items-baseline gap-x-2">
            <span
              data-tabular
              className={`font-[family-name:var(--font-display)] text-[1.625rem] leading-tight font-bold tracking-[-0.03em] ${
                below ? "text-critical" : compliance === null ? "text-ink-muted" : "text-ink"
              }`}
            >
              {formatRatio(compliance)}
            </span>
            <span className="text-micro text-ink-muted">
              {t("sloList.row.ofTarget", { ratio: formatRatio(slo.objective_ratio) })}
            </span>
          </p>
          <div className="mt-1.5 -mx-0.5">
            {history && history.length > 1 ? (
              <ComplianceTrend history={history} target={slo.objective_ratio} colour={`var(${spec.token})`} />
            ) : (
              <div className="h-10 rounded-md border border-dashed border-border" aria-hidden />
            )}
          </div>
        </div>
      </div>

      <div className="relative mt-4 flex flex-wrap items-center gap-x-3 gap-y-1.5 border-t border-border pt-3 text-micro text-ink-muted">
        <span className="rounded-full bg-surface-2 px-2 py-0.5 font-mono text-ink-secondary">
          {t("sloBoard.target", {
            ratio: formatTarget(slo.objective_ratio),
            window: fmt.duration(slo.window_seconds, { compact: true }),
          })}
        </span>
        {activeBurn ? (
          <span
            className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-semibold ${
              activeBurn.severity === "critical" ? "bg-critical-soft text-critical" : "bg-warning-soft text-warning"
            }`}
          >
            <Flame aria-hidden className="h-3 w-3" />
            {`${activeBurn.factor}× ${activeBurn.name}`}
          </span>
        ) : (
          <span>{t("sloList.row.notBurning")}</span>
        )}
        <span className="ml-auto inline-flex items-center gap-1">
          {evaluation ? fmt.relative(evaluation.evaluated_for) : "—"}
          <ArrowRight aria-hidden className="h-3 w-3 transition-transform group-hover:translate-x-0.5" />
        </span>
      </div>
    </li>
  );
}

/* ---------------- grouped by application ---------------- */

export function ObjectiveGroups({ items }: { items: Slo[] }) {
  const t = useT("alerting");
  const projects = useProjectsByKey();
  const groups = useMemo(() => {
    const byKey = new Map<string, Slo[]>();
    for (const slo of items) byKey.set(slo.project_key, [...(byKey.get(slo.project_key) ?? []), slo]);
    const rank = (s: Slo) => {
      const tone = s.evaluation ? SLO_TONE[s.evaluation.status] : "unknown";
      return tone === "critical" ? 0 : tone === "warning" ? 1 : tone === "success" ? 3 : 2;
    };
    return [...byKey.entries()]
      .map(([key, slos]) => ({ key, slos: [...slos].sort((a, b) => rank(a) - rank(b) || a.display_name.localeCompare(b.display_name)) }))
      .sort((a, b) => Math.min(...a.slos.map(rank)) - Math.min(...b.slos.map(rank)) || a.key.localeCompare(b.key));
  }, [items]);
  return (
    <div className="flex flex-col gap-8" data-testid="slo-board">
      {groups.map((group) => {
        const project = projects.get(group.key);
        const meeting = group.slos.filter((s) => s.evaluation?.status === "healthy").length;
        return (
          <section key={group.key} aria-label={project?.name ?? group.key}>
            <header className="mb-3 flex items-center gap-3">
              <span
                className="grid h-11 w-11 place-items-center rounded-[0.85rem] border border-border bg-gradient-to-b from-surface to-surface-2"
                style={{ boxShadow: "inset 0 1px 0 rgb(255 255 255 / 0.07), 0 8px 18px -12px rgb(0 0 0 / 0.45)" }}
              >
                <ProjectLogo projectId={project?.id ?? group.key} name={project?.name ?? group.key} size="md" variant="mark" />
              </span>
              <div className="min-w-0">
                <h2 className="truncate text-[1.0625rem] font-semibold tracking-[-0.015em] text-ink">
                  {project?.name ?? group.key}
                </h2>
                <p className="text-micro text-ink-muted">
                  {t("sloBoard.groupMeta", { count: group.slos.length, meeting })}
                </p>
              </div>
            </header>
            <ul className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
              {group.slos.map((slo, i) => (
                <ObjectiveCard key={slo.id} slo={slo} index={i} />
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

/* ---------------- the posture band ---------------- */

interface Slice {
  key: string;
  label: string;
  caption: string;
  value: number | null;
  tone: StatusTone;
  status?: string;
  testId: string;
}

/** The whole estate's objectives in one band: a ring by verdict, and the
 * four verdicts as tiles that filter the board. */
export function PostureBand({
  total,
  slices,
  active,
  onPick,
}: {
  total: number | null;
  slices: Slice[];
  active: string;
  onPick: (status: string) => void;
}) {
  const t = useT("alerting");
  const size = 132;
  const stroke = 14;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const known = slices.reduce((a, s) => a + (s.value ?? 0), 0);
  let offset = 0;
  return (
    <section
      className="grid gap-5 rounded-2xl border border-border bg-surface p-5 sm:p-6 lg:grid-cols-[auto_minmax(0,1fr)] lg:items-center"
      data-testid="slo-posture"
    >
      <div className="flex items-center gap-5">
        <div className="relative" style={{ width: size, height: size }}>
          <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90" aria-hidden>
            <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--surface-3)" strokeWidth={stroke} />
            {known > 0
              ? slices.map((s) => {
                  const share = (s.value ?? 0) / known;
                  const dash = share * c;
                  const el = (
                    <circle
                      key={s.key}
                      cx={size / 2}
                      cy={size / 2}
                      r={r}
                      fill="none"
                      stroke={`var(${toneSpec(s.tone).token})`}
                      strokeWidth={stroke}
                      strokeDasharray={`${Math.max(0, dash - (share > 0 && share < 1 ? 2 : 0))} ${c}`}
                      strokeDashoffset={-offset}
                    />
                  );
                  offset += dash;
                  return el;
                })
              : null}
          </svg>
          <span className="absolute inset-0 grid place-items-center text-center">
            <span>
              <span data-tabular className="block font-[family-name:var(--font-display)] text-[2rem] leading-none font-bold text-ink">
                {total ?? "—"}
              </span>
              <span className="mt-1 block text-micro text-ink-muted">{t("sloList.byVerdict.center")}</span>
            </span>
          </span>
        </div>
        <div className="min-w-0 lg:hidden">
          <p className="text-body font-semibold text-ink">{t("sloBoard.postureTitle")}</p>
          <p className="text-caption text-ink-muted">{t("sloBoard.postureHint")}</p>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        {slices.map((s) => {
          const pressed = Boolean(s.status) && active === s.status;
          const spec = toneSpec(s.tone);
          const body = (
            <>
              <span className="flex items-center gap-2 text-caption text-ink-secondary">
                <span aria-hidden className={`h-2 w-2 rounded-full ${spec.dot}`} />
                {s.label}
              </span>
              <span data-tabular className="mt-2 block font-[family-name:var(--font-display)] text-[1.875rem] leading-none font-bold text-ink">
                {s.value ?? "—"}
              </span>
              <span className="mt-1.5 block text-micro text-ink-muted">{s.caption}</span>
              <span aria-hidden className="mt-3 block h-1 overflow-hidden rounded-full bg-surface-3">
                <span
                  className={`block h-full rounded-full ${spec.dot}`}
                  style={{ width: `${known && s.value ? (s.value / known) * 100 : 0}%` }}
                />
              </span>
            </>
          );
          const cls = `rounded-xl border p-3.5 text-left transition-colors ${
            pressed ? "border-brand-accent/60 bg-surface-selected" : "border-border bg-surface-2/50"
          }`;
          return s.status ? (
            <button
              key={s.key}
              type="button"
              aria-pressed={pressed}
              data-testid={s.testId}
              onClick={() => onPick(s.status!)}
              className={`${cls} hover:border-border-strong`}
            >
              {body}
            </button>
          ) : (
            <div key={s.key} data-testid={s.testId} className={cls}>
              {body}
            </div>
          );
        })}
      </div>
    </section>
  );
}
