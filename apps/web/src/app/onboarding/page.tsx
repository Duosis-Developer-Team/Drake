"use client";

/**
 * Project onboarding.
 *
 *   repository → safe analysis → reviewable plan → approval → catalog
 *
 * Two things this screen refuses to do. It never shows a plan as
 * applicable when the server said it is not, and it never presents an
 * unconfigured integration as an empty one: "Drake cannot look" and "Drake
 * looked and found nothing" are different answers, and only one of them
 * means someone should go configure something.
 */

import {
  ChevronRight,
  FileWarning,
  FolderGit2,
  GitPullRequest,
  Inbox,
  PackageCheck,
  PlugZap,
  RefreshCw,
  ScanSearch,
  ShieldCheck,
} from "lucide-react";
import Link from "next/link";
import { Suspense } from "react";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";

import { useApi } from "@/components/catalog/primitives";
import { SessionBadge, sessionTone } from "@/components/onboarding/primitives";
import { StartOnboarding } from "@/components/onboarding/StartOnboarding";
import {
  FactChip,
  IconBubble,
  KpiTile,
  StateCard,
  Stepper,
  type StepStatus,
} from "@/components/protection/kit";
import { DataState } from "@/components/state/DataState";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { toneSpec, type StatusTone } from "@/lib/design/status";
import { useFormat, useT } from "@/lib/i18n";
import {
  MISSING_INPUT_LABELS,
  WIZARD_STEPS,
  shortSha,
  type GitHubStatus,
  type OnboardingSession,
  type SessionPage,
} from "@/lib/onboarding";
import { useSession } from "@/lib/session";

/** `WIZARD_STEPS` (the English source the tests name) → catalogue keys. */
const STEP_KEYS: Record<
  (typeof WIZARD_STEPS)[number],
  | "integrationStatus"
  | "repository"
  | "safeDiscovery"
  | "detectedStructure"
  | "review"
  | "approval"
  | "result"
> = {
  "Integration status": "integrationStatus",
  Repository: "repository",
  "Safe discovery": "safeDiscovery",
  "Detected structure": "detectedStructure",
  Review: "review",
  Approval: "approval",
  Result: "result",
};

function HowItWorks({ status }: { status: GitHubStatus | null }) {
  const t = useT("onboarding");
  const configured = status?.configuration_state === "configured";
  const notConfigured = status?.configuration_state === "not_configured";
  const steps = WIZARD_STEPS.map((step, index) => {
    let state: StepStatus = "upcoming";
    if (index === 0)
      state = configured ? "done" : notConfigured ? "blocked" : "current";
    if (index === 1 && configured) state = "current";
    const key = STEP_KEYS[step];
    return { label: t(`step.${key}`), status: state, hint: t(`stepHint.${key}`) };
  });
  return (
    <Panel>
      <PanelHeader title={t("how.title")} description={t("how.description")} />
      <div className="overflow-x-auto">
        <div className="min-w-[44rem]">
          <Stepper steps={steps} data-testid="wizard-steps" />
        </div>
      </div>
      <p className="flex items-center gap-3 rounded-full bg-surface-2 px-5 py-3 text-caption text-ink-secondary">
        <ShieldCheck aria-hidden className="h-4 w-4 shrink-0 text-healthy" />
        <span>{t("how.nothingExecuted")}</span>
      </p>
    </Panel>
  );
}

function NotConfigured({ status }: { status: GitHubStatus }) {
  const t = useT("onboarding");
  return (
    <Panel>
      <div data-testid="github-not-configured" className="flex flex-col gap-5">
        <StateCard
          kind="not-configured"
          icon={PlugZap}
          title={t("notConfigured.title")}
          description={t("notConfigured.description")}
        >
          <ul className="flex flex-wrap justify-center gap-2">
            {status.missing_operator_inputs.map((key) => (
              <li
                key={key}
                className={`rounded-full px-3.5 py-1.5 text-caption ${toneSpec("warning").chip}`}
              >
                {t.dyn("missingInput", key, MISSING_INPUT_LABELS[key] ?? key)}
              </li>
            ))}
          </ul>
        </StateCard>
        <p className="text-center text-micro text-ink-muted">{t("notConfigured.operatorNote")}</p>
      </div>
    </Panel>
  );
}

function IntegrationHealth({ status }: { status: GitHubStatus }) {
  const t = useT("onboarding");
  const fmt = useFormat();
  const pullRequests =
    status.gitops_pending + status.gitops_active + status.gitops_failed;
  return (
    <div className="flex flex-col gap-4">
      <div className="page-grid" data-testid="integration-health">
        <KpiTile
          icon={FileWarning}
          label={t("health.needReview")}
          value={status.needs_review}
          tone="warning"
          part={status.needs_review}
          whole={status.sessions}
          caption={t("health.ofSessions", { count: status.sessions })}
        />
        <KpiTile
          icon={PackageCheck}
          label={t("health.imported")}
          value={status.imported}
          tone="success"
          part={status.imported}
          whole={status.sessions}
          caption={t("health.ofSessions", { count: status.sessions })}
        />
        <KpiTile
          icon={ScanSearch}
          label={t("health.partialAnalyses")}
          value={status.analyses_truncated}
          tone="info"
          part={status.analyses_truncated}
          whole={status.analyses}
          caption={t("health.ofAnalyses", {
            count: status.analyses,
            when: fmt.relative(status.last_analyzed_at),
          })}
        />
        <KpiTile
          icon={GitPullRequest}
          label={t("health.failedPullRequests")}
          value={status.gitops_failed}
          tone={status.gitops_failed > 0 ? "critical" : "neutral"}
          part={status.gitops_failed}
          whole={pullRequests}
          caption={t("health.ofPullRequests", { count: pullRequests })}
        />
      </div>
      {status.gitops_pr_enabled ? null : (
        <p
          className="flex items-center gap-3 self-start rounded-full border border-border bg-surface px-5 py-2.5 text-caption text-ink-secondary"
          data-testid="gitops-disabled"
        >
          <GitPullRequest
            aria-hidden
            className="h-4 w-4 shrink-0 text-ink-muted"
          />
          {t("health.gitopsOff")}
        </p>
      )}
    </div>
  );
}

/** Where the sessions are, as bars against the session total. */
function Pipeline({ status }: { status: GitHubStatus }) {
  const t = useT("onboarding");
  // Each row is a session state, so it wears the state's own label.
  const rows: { label: string; count: number; tone: StatusTone }[] = [
    { label: t("session.needs_review"), count: status.needs_review, tone: "warning" },
    { label: t("session.ready"), count: status.ready, tone: "info" },
    { label: t("session.imported"), count: status.imported, tone: "success" },
    { label: t("session.stale"), count: status.stale, tone: "stale" },
    {
      label: t("session.provider_unavailable"),
      count: status.provider_unavailable,
      tone: "unknown",
    },
  ];
  return (
    <Panel className="h-full">
      <PanelHeader title={t("pipeline.title")} description={t("pipeline.description")} />
      <p className="flex items-baseline gap-2">
        <span
          data-tabular
          className="text-[2.25rem] leading-none font-semibold tracking-[-0.03em] text-ink"
        >
          {status.sessions}
        </span>
        <span className="text-caption text-ink-muted">{t("pipeline.sessions")}</span>
      </p>
      <ul className="flex flex-col gap-4">
        {rows.map((row) => {
          const share =
            status.sessions > 0 ? (row.count / status.sessions) * 100 : 0;
          return (
            <li key={row.label}>
              <span className="flex min-w-0 items-center gap-2 text-caption text-ink-secondary">
                <span
                  aria-hidden
                  className={`h-2.5 w-2.5 shrink-0 rounded-full ${toneSpec(row.tone).dot}`}
                />
                <span className="min-w-0 truncate">{row.label}</span>
                <span
                  data-tabular
                  className="ml-auto shrink-0 font-semibold text-ink"
                >
                  {row.count}
                </span>
              </span>
              <span
                aria-hidden
                className="mt-2 block h-2 overflow-hidden rounded-full bg-surface-3"
              >
                <span
                  className={`block h-full rounded-full ${toneSpec(row.tone).dot}`}
                  style={{ width: `${share}%` }}
                />
              </span>
            </li>
          );
        })}
      </ul>
      <div className="mt-auto grid grid-cols-2 gap-3">
        <div className="rounded-2xl bg-surface-2 px-4 py-3">
          <p className="text-micro text-ink-muted">{t("pipeline.analyses")}</p>
          <p data-tabular className="mt-1 text-title font-semibold text-ink">
            {status.analyses}
          </p>
        </div>
        <div className="rounded-2xl bg-surface-2 px-4 py-3">
          <p className="text-micro text-ink-muted">{t("pipeline.analysesFailed")}</p>
          <p data-tabular className="mt-1 text-title font-semibold text-ink">
            {status.analyses_failed}
          </p>
        </div>
      </div>
    </Panel>
  );
}

function SessionRow({ session }: { session: OnboardingSession }) {
  const t = useT("onboarding");
  const fmt = useFormat();
  return (
    <li
      className="group relative flex flex-wrap items-center gap-x-5 gap-y-3 px-7 py-5 transition-colors hover:bg-surface-hover"
      data-testid={`session-row-${session.repository.name}`}
    >
      <IconBubble icon={FolderGit2} tone={sessionTone(session.state)} />
      <div className="min-w-0 flex-1 basis-64">
        <Link
          href={`/onboarding/${session.id}`}
          className="text-body font-semibold break-all text-ink after:absolute after:inset-0 after:content-['']"
        >
          {session.repository.full_name}
        </Link>
        <span className="mt-0.5 block font-mono text-micro text-ink-muted">
          {session.repository.default_branch} ·{" "}
          {shortSha(session.analyzed_commit_sha)}
        </span>
        {session.reason ? (
          <p className="mt-1 max-w-prose text-micro text-ink-secondary">
            {session.reason}
          </p>
        ) : null}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {session.plan ? (
          <>
            <FactChip label={t("row.plan")}>
              {t("row.planSummary", {
                version: session.plan.plan_version,
                count: session.plan.total_items,
              })}
            </FactChip>
            {session.plan.blocking_items > 0 ? (
              <FactChip label={t("row.decisions")} tone="warning">
                {t("row.needReview", { count: session.plan.blocking_items })}
              </FactChip>
            ) : null}
          </>
        ) : (
          <span className="rounded-full bg-surface-2 px-3 py-1 text-micro text-ink-muted italic">
            {t("row.notAnalysed")}
          </span>
        )}
        {session.imported_project_key ? (
          <Link
            href={`/projects/${session.imported_project_id}`}
            className="relative z-10 rounded-full bg-surface-2 px-3 py-1 text-micro font-semibold text-ink hover:bg-surface-3"
          >
            {session.imported_project_key}
          </Link>
        ) : null}
      </div>
      <div className="flex shrink-0 flex-col items-end gap-1.5">
        <SessionBadge state={session.state} />
        <span className="text-micro text-ink-muted">
          {t("row.opened", { when: fmt.relative(session.created_at) })}
        </span>
      </div>
      <ChevronRight
        aria-hidden
        className="hidden h-4 w-4 shrink-0 text-ink-muted transition-transform group-hover:translate-x-0.5 md:block"
      />
    </li>
  );
}

function OnboardingInner() {
  const t = useT("onboarding");
  const { state: auth } = useSession();
  const csrfToken = auth.status === "authenticated" ? auth.me.csrf_token : "";
  const [status, retryStatus] = useApi<GitHubStatus>(
    "/v1/onboarding/github/status",
  );
  const [page, retryPage] = useApi<SessionPage>("/v1/onboarding/sessions");
  const configured =
    status.state === "ready" &&
    status.data.configuration_state === "configured";

  const sessions = (
    <Panel flush>
      <PanelHeader
        flush
        title={t("sessions.title")}
        description={t("sessions.description")}
        meta={
          page.state === "ready" && page.data.items.length > 0 ? (
            <span>{t("sessions.total", { count: page.data.total })}</span>
          ) : null
        }
      />
      {page.state === "loading" ? (
        <div className="px-7 py-5">
          <DataState kind="loading" />
        </div>
      ) : page.state === "error" ? (
        page.notFound ? (
          <StateCard kind="permission-denied" />
        ) : (
          <StateCard
            kind="error"
            description={page.message}
            action={<RetryButton onClick={retryPage} />}
          />
        )
      ) : page.data.items.length === 0 ? (
        <StateCard
          kind="empty"
          icon={Inbox}
          title={t("sessions.empty.title")}
          description={t("sessions.empty.description")}
        />
      ) : (
        <ul className="divide-y divide-border">
          {page.data.items.map((session) => (
            <SessionRow key={session.id} session={session} />
          ))}
        </ul>
      )}
    </Panel>
  );

  return (
    <PageFrame>
      <PageHeader title={t("page.title")} description={t("page.description")} />
      <div className="flex flex-col gap-6">
        <HowItWorks status={status.state === "ready" ? status.data : null} />

        {status.state === "loading" ? (
          <Panel>
            <DataState kind="loading" />
          </Panel>
        ) : status.state === "error" ? (
          <Panel>
            <StateCard
              kind="error"
              description={status.message}
              action={<RetryButton onClick={retryStatus} />}
            />
          </Panel>
        ) : status.data.configuration_state === "not_configured" ? (
          <NotConfigured status={status.data} />
        ) : (
          <IntegrationHealth status={status.data} />
        )}

        {configured && status.state === "ready" ? (
          <div className="page-split">
            <div className="page-main">
              <StartOnboarding
                csrfToken={csrfToken}
                canManage={status.data.can_manage}
              />
            </div>
            <div className="page-aside">
              <Pipeline status={status.data} />
            </div>
          </div>
        ) : null}

        {sessions}
      </div>
    </PageFrame>
  );
}

function RetryButton({ onClick }: { onClick: () => void }) {
  const t = useT("common");
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex items-center gap-1.5 rounded-full border border-border px-4 py-2 text-caption font-medium text-ink transition-colors hover:bg-surface-hover"
    >
      <RefreshCw aria-hidden className="h-3.5 w-3.5" />
      {t("action.retry")}
    </button>
  );
}

export default function OnboardingPage() {
  return (
    <Suspense fallback={<DataState kind="loading" />}>
      <OnboardingInner />
    </Suspense>
  );
}
