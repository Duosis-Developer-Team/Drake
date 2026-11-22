"use client";

/**
 * The Protection Center as a board.
 *
 * Two questions stay two questions everywhere on it: is there a usable
 * copy (backup state), and has anyone proved it restores (restore
 * evidence). Policies are cards grouped by application, each with an RPO
 * meter — how old the freshest good backup is against the promise, with the
 * promise as a tick — so "late" is visible before any word is read.
 *
 * Policies the reviewed contract expects but that do not exist here yet
 * (their reporter never reported) are drawn as dashed cards in the same
 * place: honest absence, never an empty board pretending all is well.
 */

import { ArrowRight, Clock3, Database, HardDriveDownload, RotateCcw, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";

import { kindColour } from "@/components/apps/AppLauncher";
import { useProjectsByKey } from "@/components/alerting/SloBoard";
import { Donut } from "@/components/charts/visuals";
import { formatWindowWith } from "@/components/protection/format";
import { BackupBadge, RecoverabilityBadge, overallTone } from "@/components/protection/primitives";
import { ProjectLogo } from "@/components/ui/ProjectLogo";
import { toneSpec, type StatusTone } from "@/lib/design/status";
import { useFormat, useT } from "@/lib/i18n";
import {
  REASON_LABELS,
  type ProtectionPolicy,
  type ProtectionSummary,
  type ReporterPolicy,
  type ReporterStatus,
} from "@/lib/protection";

/* ---------------- posture band ---------------- */

function EmptyRing({ message }: { message: string }) {
  return (
    <div className="flex flex-wrap items-center gap-5" data-testid="donut-empty">
      <span
        aria-hidden
        className="relative flex h-[120px] w-[120px] shrink-0 items-center justify-center rounded-full border-[14px] border-dashed border-surface-3"
      >
        <span data-tabular className="text-title font-semibold text-ink-muted">
          0
        </span>
      </span>
      <p className="min-w-[10rem] flex-1 text-caption text-ink-muted">{message}</p>
    </div>
  );
}

export function PostureBand({ summary }: { summary: ProtectionSummary }) {
  const t = useT("protection");
  const total = summary.total_policies;
  const usable = summary.backup.protected ?? 0;
  const provable = summary.recoverability.verified ?? 0;
  const attention = (summary.backup.at_risk ?? 0) + (summary.backup.overdue ?? 0) + (summary.backup.failed ?? 0);
  const neverTested = summary.recoverability.unverified ?? 0;
  const backup = [
    { name: t("backup.protected"), value: summary.backup.protected ?? 0, tone: "success" as const },
    { name: t("backup.at_risk"), value: summary.backup.at_risk ?? 0, tone: "warning" as const },
    { name: t("backup.overdue"), value: summary.backup.overdue ?? 0, tone: "warning" as const },
    { name: t("backup.failed"), value: summary.backup.failed ?? 0, tone: "critical" as const },
    { name: t("backup.unknown"), value: summary.backup.unknown ?? 0, tone: "unknown" as const },
  ];
  const restore = [
    { name: t("recoverability.verified"), value: summary.recoverability.verified ?? 0, tone: "success" as const },
    { name: t("recoverability.unverified"), value: summary.recoverability.unverified ?? 0, tone: "unknown" as const },
    { name: t("recoverability.failed"), value: summary.recoverability.failed ?? 0, tone: "critical" as const },
    { name: t("recoverability.unknown"), value: summary.recoverability.unknown ?? 0, tone: "unknown" as const },
  ];
  const sum = (slices: { value: number }[]) => slices.reduce((a, s) => a + s.value, 0);
  const stats: { label: string; value: number; tone: StatusTone }[] = [
    { label: t("stats.protected"), value: usable, tone: "success" },
    { label: t("stats.verifiedLabel"), value: provable, tone: "success" },
    { label: t("stats.needsAttention"), value: attention, tone: (summary.backup.failed ?? 0) > 0 ? "critical" : "warning" },
    { label: t("stats.neverTested"), value: neverTested, tone: "unknown" },
  ];
  return (
    <section
      data-testid="protection-summary"
      className="grid gap-6 rounded-2xl border border-border bg-surface p-5 sm:p-6 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)_minmax(0,1fr)]"
    >
      <div className="flex flex-col justify-between gap-5">
        <div className="flex items-start gap-3">
          <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-brand-soft text-brand-accent">
            <ShieldCheck aria-hidden className="h-5 w-5" />
          </span>
          <div className="min-w-0">
            <p className="text-[1.0625rem] font-semibold tracking-[-0.015em] text-ink">
              {total > 0 ? t("board.headline", { usable, provable, total }) : t("board.headlineNone")}
            </p>
            <p className="mt-1 text-caption text-ink-muted">{t("board.headlineHint")}</p>
          </div>
        </div>
        <dl className={`grid grid-cols-2 gap-2.5 ${total === 0 ? "hidden" : ""}`}>
          {stats.map((s) => (
            <div key={s.label} className="rounded-xl bg-surface-2/70 px-3 py-2.5">
              <dt className="flex items-center gap-1.5 text-[11px] text-ink-muted">
                <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${toneSpec(s.tone).dot}`} />
                {s.label}
              </dt>
              <dd data-tabular className="mt-1 font-[family-name:var(--font-display)] text-[1.5rem] leading-none font-bold text-ink">
                {s.value}
                <span className="ml-1 text-caption font-normal text-ink-muted">/ {total}</span>
              </dd>
            </div>
          ))}
        </dl>
      </div>
      {/* Two separate questions, so two separate rings. */}
      <div className="rounded-xl border border-border p-4">
        <p className="text-body font-semibold text-ink">{t("breakdown.backupTitle")}</p>
        <p className="mb-3 text-micro text-ink-muted">{t("breakdown.backupDescription")}</p>
        {sum(backup) === 0 ? (
          <EmptyRing message={t("breakdown.backupEmpty")} />
        ) : (
          <Donut size={120} thickness={14} label={t("breakdown.backupChart")} slices={backup} />
        )}
      </div>
      <div className="rounded-xl border border-border p-4">
        <p className="text-body font-semibold text-ink">{t("breakdown.restoreTitle")}</p>
        <p className="mb-3 text-micro text-ink-muted">{t("breakdown.restoreDescription")}</p>
        {sum(restore) === 0 ? (
          <EmptyRing message={t("breakdown.restoreEmpty")} />
        ) : (
          <Donut size={120} thickness={14} label={t("breakdown.restoreChart")} slices={restore} />
        )}
      </div>
    </section>
  );
}

/* ---------------- RPO meter ---------------- */

/** The freshest good backup's age against the RPO. The tick is the promise;
 * the bar runs past it when the backup is late. */
function RpoMeter({ lastSuccess, rpo }: { lastSuccess: string | null; rpo: number }) {
  const t = useT("protection");
  const fmt = useFormat();
  const age = lastSuccess ? Math.max(0, (Date.now() - new Date(lastSuccess).getTime()) / 1000) : null;
  const ratio = age === null || rpo <= 0 ? null : age / rpo;
  const fill = ratio === null ? 0 : Math.min(1, ratio / 1.5);
  const tone: StatusTone = ratio === null ? "unknown" : ratio >= 1 ? "critical" : ratio >= 0.8 ? "warning" : "success";
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2 text-[11px] text-ink-muted">
        <span>{t("row.lastSuccess")}</span>
        <span data-tabular className={`font-mono font-semibold ${ratio !== null && ratio >= 1 ? "text-critical" : "text-ink"}`}>
          {lastSuccess ? fmt.relative(lastSuccess) : t("board.noBackupYet")}
        </span>
      </div>
      <span
        className={`relative mt-1.5 block h-2 rounded-full ${ratio === null ? "border border-dashed border-border-strong" : "bg-surface-3"}`}
        role="img"
        aria-label={t("board.rpoMeter")}
      >
        {ratio !== null ? (
          <span
            className={`absolute inset-y-0 left-0 rounded-full ${toneSpec(tone).dot}`}
            style={{ width: `${fill * 100}%` }}
          />
        ) : null}
        <span aria-hidden className="absolute -top-1 h-4 w-0.5 rounded bg-ink" style={{ left: "66.6%" }} />
      </span>
      <div className="mt-1 flex justify-between text-[11px] text-ink-muted">
        <span>{t("board.now")}</span>
        <span className="flex items-center gap-1">
          {t("row.rpo")} <span data-tabular className="font-mono text-ink-secondary">{formatWindowWith(fmt, rpo)}</span>
        </span>
      </div>
    </div>
  );
}

/* ---------------- one policy ---------------- */

export function PolicyCard({ policy, index = 0 }: { policy: ProtectionPolicy; index?: number }) {
  const t = useT("protection");
  const fmt = useFormat();
  const evaluation = policy.evaluation;
  const tone = evaluation ? overallTone(evaluation.overall_state) : "unknown";
  const reason =
    evaluation && evaluation.reasons.length > 0
      ? t.dyn("reason", evaluation.reasons[0], REASON_LABELS[evaluation.reasons[0]] ?? evaluation.reasons[0])
      : null;
  return (
    <li
      data-testid={`protection-row-${policy.store_key}`}
      style={{ animationDelay: `${index * 40}ms` }}
      className={`group relative flex animate-[rise-in_.4s_cubic-bezier(.2,.8,.2,1)_both] flex-col overflow-hidden rounded-2xl border bg-surface p-5 pl-6 transition-[transform,border-color,box-shadow] duration-200 hover:-translate-y-0.5 hover:border-border-strong hover:shadow-[0_18px_40px_-24px_rgb(0_0_0/0.45)] ${
        tone === "critical" ? "border-critical/50" : tone === "warning" ? "border-warning/50" : "border-border"
      }`}
    >
      <span
        aria-hidden
        className="absolute inset-y-0 left-0 w-[3px]"
        style={{ background: policy.environment_key ? kindColour(policy.environment_key) : "var(--text-muted)" }}
      />
      <div className="flex items-start gap-3">
        <span className={`grid h-9 w-9 shrink-0 place-items-center rounded-xl ${toneSpec(tone).chip}`}>
          <Database aria-hidden className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <Link
            href={`/protection/${policy.id}`}
            className="block truncate text-[0.9375rem] font-semibold tracking-[-0.01em] text-ink outline-none after:absolute after:inset-0 after:rounded-2xl focus-visible:after:ring-2 focus-visible:after:ring-focus"
          >
            {policy.display_name}
          </Link>
          <p className="mt-0.5 truncate font-mono text-[11px] text-ink-muted">
            {policy.environment_key ?? "—"} · {policy.store_key}
          </p>
        </div>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-2.5">
        {[
          { label: t("row.backup"), icon: HardDriveDownload, badge: evaluation ? <BackupBadge state={evaluation.backup_state} /> : null },
          { label: t("row.restore"), icon: RotateCcw, badge: evaluation ? <RecoverabilityBadge state={evaluation.recoverability_state} /> : null },
        ].map(({ label, icon: Icon, badge }) => (
          <div key={label} className="rounded-xl bg-surface-2/70 px-3 py-2.5">
            <p className="flex items-center gap-1.5 text-[11px] text-ink-muted">
              <Icon aria-hidden className="h-3 w-3" />
              {label}
            </p>
            <div className="mt-1.5">
              {badge ?? (
                <span className="rounded-full bg-surface-3 px-2.5 py-0.5 text-caption text-ink-muted italic">
                  {t("row.notEvaluated")}
                </span>
              )}
            </div>
          </div>
        ))}
      </div>

      <div className="mt-4">
        <RpoMeter lastSuccess={evaluation?.last_success_at ?? null} rpo={policy.rpo_seconds} />
      </div>

      <dl className="mt-4 grid grid-cols-3 gap-2 border-t border-border pt-3 text-[11px]">
        <div className="min-w-0">
          <dt className="text-ink-muted">{t("row.lastRestore")}</dt>
          <dd data-tabular className="truncate font-semibold text-ink">{fmt.relative(evaluation?.last_restore_at)}</dd>
        </div>
        <div className="min-w-0">
          <dt className="text-ink-muted">{t("row.offsite")}</dt>
          <dd className="truncate font-semibold text-ink">
            {policy.requires_offsite ? t("row.required") : t("row.notRequired")}
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-ink-muted">{t("row.reporter")}</dt>
          <dd data-tabular className="truncate font-semibold text-ink">{fmt.relative(evaluation?.reporter_seen_at)}</dd>
        </div>
      </dl>

      <p className="mt-3 flex items-center gap-1.5 text-caption">
        {reason ? (
          <span className="inline-flex items-center gap-1.5 rounded-full bg-warning-soft px-2.5 py-0.5 font-medium text-warning">
            {reason}
          </span>
        ) : (
          <span className="text-ink-muted">{t("row.noOpenReason")}</span>
        )}
        <ArrowRight aria-hidden className="ml-auto h-3.5 w-3.5 text-ink-muted transition-transform group-hover:translate-x-0.5" />
      </p>
    </li>
  );
}

/** A policy the contract expects that does not exist here yet. */
function ExpectedCard({ policy, reporter }: { policy: ReporterPolicy; reporter: ReporterStatus }) {
  const t = useT("protection");
  const fmt = useFormat();
  return (
    <li
      data-testid={`protection-expected-${policy.policy_key}`}
      className="relative flex flex-col self-start overflow-hidden rounded-2xl border border-dashed border-border-strong bg-surface-2/30 p-5 pl-6"
    >
      <span
        aria-hidden
        className="absolute inset-y-0 left-0 w-[3px] opacity-50"
        style={{ background: policy.environment_key ? kindColour(policy.environment_key) : "var(--text-muted)" }}
      />
      <div className="flex items-start gap-3">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-surface-3 text-ink-muted">
          <Database aria-hidden className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-[0.9375rem] font-semibold text-ink">{policy.display_name}</p>
          <p className="mt-0.5 truncate font-mono text-[11px] text-ink-muted">
            {policy.environment_key ?? "—"} · {policy.policy_key}
          </p>
        </div>
        <span className="shrink-0 rounded-full bg-surface-3 px-2.5 py-0.5 text-micro font-semibold text-ink-secondary">
          {t("board.expected")}
        </span>
      </div>
      <p className="mt-4 flex items-center gap-2 text-caption text-ink-secondary">
        <Clock3 aria-hidden className="h-3.5 w-3.5 text-ink-muted" />
        {t.dyn("board.awaiting", reporter.state, reporter.state)}
      </p>
      <div className="mt-3 flex flex-wrap gap-1.5 text-[11px]">
        {[
          policy.schedule,
          policy.rpo_seconds ? t("setup.rpo", { value: fmt.duration(policy.rpo_seconds, { compact: true }) }) : null,
          policy.requires_offsite ? t("setup.offsite") : null,
          policy.store_kind,
        ]
          .filter(Boolean)
          .map((fact) => (
            <span key={fact} className="rounded-full bg-surface px-2.5 py-0.5 text-ink-secondary">
              {fact}
            </span>
          ))}
      </div>
    </li>
  );
}

/* ---------------- grouped by application ---------------- */

export function PolicyBoard({
  policies,
  reporters,
}: {
  policies: ProtectionPolicy[];
  reporters: ReporterStatus[];
}) {
  const t = useT("protection");
  const projects = useProjectsByKey();
  const groups = useMemo(() => {
    const byKey = new Map<
      string,
      { key: string; id: string | null; policies: ProtectionPolicy[]; expected: { policy: ReporterPolicy; reporter: ReporterStatus }[] }
    >();
    const group = (key: string, id: string | null) => {
      let g = byKey.get(key);
      if (!g) {
        g = { key, id, policies: [], expected: [] };
        byKey.set(key, g);
      }
      if (!g.id && id) g.id = id;
      return g;
    };
    for (const p of policies) group(p.project_key, p.project_id).policies.push(p);
    for (const r of reporters)
      for (const p of r.policies) if (!p.created) group(r.project_key, r.project_id).expected.push({ policy: p, reporter: r });
    const rank = (p: ProtectionPolicy) => {
      const tone = p.evaluation ? overallTone(p.evaluation.overall_state) : "unknown";
      return tone === "critical" ? 0 : tone === "warning" ? 1 : tone === "success" ? 3 : 2;
    };
    return [...byKey.values()]
      .map((g) => ({ ...g, policies: [...g.policies].sort((a, b) => rank(a) - rank(b) || a.display_name.localeCompare(b.display_name)) }))
      .sort((a, b) => a.key.localeCompare(b.key));
  }, [policies, reporters]);

  return (
    <div className="flex flex-col gap-8" data-testid="protection-table">
      {groups.map((g) => {
        const project = projects.get(g.key);
        const name = project?.name ?? g.key;
        const restorable = g.policies.filter((p) => p.evaluation?.recoverability_state === "verified").length;
        return (
          <section key={g.key} aria-label={name}>
            <header className="mb-3 flex items-center gap-3">
              <span
                className="grid h-11 w-11 place-items-center rounded-[0.85rem] border border-border bg-gradient-to-b from-surface to-surface-2"
                style={{ boxShadow: "inset 0 1px 0 rgb(255 255 255 / 0.07), 0 8px 18px -12px rgb(0 0 0 / 0.45)" }}
              >
                <ProjectLogo projectId={g.id ?? project?.id ?? g.key} name={name} size="md" variant="mark" />
              </span>
              <div className="min-w-0">
                <h2 className="truncate text-[1.0625rem] font-semibold tracking-[-0.015em] text-ink">{name}</h2>
                <p className="text-micro text-ink-muted">
                  {t("board.groupMeta", { count: g.policies.length, restorable, expected: g.expected.length })}
                </p>
              </div>
            </header>
            <ul className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
              {g.policies.map((p, i) => (
                <PolicyCard key={p.id} policy={p} index={i} />
              ))}
              {g.expected.map(({ policy, reporter }) => (
                <ExpectedCard key={`${reporter.connector_key}:${policy.policy_key}`} policy={policy} reporter={reporter} />
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
