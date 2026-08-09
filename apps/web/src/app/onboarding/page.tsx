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

import Link from "next/link";
import { Suspense } from "react";

import { useApi } from "@/components/catalog/primitives";
import { SessionBadge } from "@/components/onboarding/primitives";
import { DataState } from "@/components/state/DataState";
import { Card } from "@/components/ui/Card";
import {
  MISSING_INPUT_LABELS,
  WIZARD_STEPS,
  formatAge,
  shortSha,
  type GitHubStatus,
  type OnboardingSession,
  type SessionPage,
} from "@/lib/onboarding";

function NotConfigured({ status }: { status: GitHubStatus }) {
  return (
    <Card title="GitHub App">
      <div data-testid="github-not-configured">
        <DataState
          kind="not-configured"
          title="GitHub is not configured"
          description="Drake cannot read repositories. Nothing has been contacted, no token has been issued, and no repository list is being shown."
        />
        <ul className="mt-3 space-y-1">
          {status.missing_operator_inputs.map((key) => (
            <li key={key} className="text-xs text-ink-secondary">
              {MISSING_INPUT_LABELS[key] ?? key}
            </li>
          ))}
        </ul>
        <p className="mt-3 text-xs text-ink-muted">
          An operator configures the App identity and its credential references outside
          Drake. Drake never accepts a credential through this screen.
        </p>
      </div>
    </Card>
  );
}

function SessionRow({ session }: { session: OnboardingSession }) {
  return (
    <tr
      className="border-t border-border align-top"
      data-testid={`session-row-${session.repository.name}`}
    >
      <td className="py-2.5 pr-3">
        <div className="flex flex-col gap-1">
          <Link
            href={`/onboarding/${session.id}`}
            className="text-sm font-medium text-ink hover:underline"
          >
            {session.repository.full_name}
          </Link>
          <span className="font-mono text-[11px] text-ink-muted">
            {session.repository.default_branch} · {shortSha(session.analyzed_commit_sha)}
          </span>
        </div>
      </td>
      <td className="py-2.5 pr-3">
        <SessionBadge state={session.state} />
        {session.reason ? (
          <p className="mt-1 max-w-xs text-[11px] text-ink-secondary">{session.reason}</p>
        ) : null}
      </td>
      <td className="py-2.5 pr-3 text-xs text-ink-secondary">
        {session.plan ? (
          <>
            <div>
              v{session.plan.plan_version} · {session.plan.total_items} items
            </div>
            {session.plan.blocking_items > 0 ? (
              <div className="text-warning">
                {session.plan.blocking_items} need review
              </div>
            ) : null}
          </>
        ) : (
          <span className="italic text-ink-muted">not analysed</span>
        )}
      </td>
      <td className="py-2.5 pr-3 text-xs text-ink-secondary">
        {session.imported_project_key ? (
          <Link
            href={`/projects/${session.imported_project_id}`}
            className="hover:underline"
          >
            {session.imported_project_key}
          </Link>
        ) : (
          "—"
        )}
      </td>
      <td className="py-2.5 text-xs text-ink-secondary">{formatAge(session.created_at)}</td>
    </tr>
  );
}

function OnboardingInner() {
  const [status, retryStatus] = useApi<GitHubStatus>("/v1/onboarding/github/status");
  const [page, retryPage] = useApi<SessionPage>("/v1/onboarding/sessions");

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold text-ink">Onboard a project</h1>
        <p className="text-sm text-ink-secondary">
          Drake reads a repository statically, proposes what it would add to the catalog,
          and changes nothing until someone approves that exact proposal.
        </p>
      </header>

      <Card title="How this works">
        <ol className="flex flex-wrap gap-2" data-testid="wizard-steps">
          {WIZARD_STEPS.map((step, index) => (
            <li
              key={step}
              className="rounded-lg border border-border px-2.5 py-1 text-xs text-ink-secondary"
            >
              <span className="font-mono text-ink-muted">{index + 1}.</span> {step}
            </li>
          ))}
        </ol>
        <p className="mt-3 text-xs text-ink-muted">
          Nothing in a repository is executed: no build, no install, no script, no hook, no
          workflow. Drake reads an allowlist of metadata files at one immutable commit.
        </p>
      </Card>

      {status.state === "loading" ? (
        <DataState kind="loading" />
      ) : status.state === "error" ? (
        <DataState kind="error" description={status.message} onRetry={retryStatus} />
      ) : status.data.configuration_state === "not_configured" ? (
        <NotConfigured status={status.data} />
      ) : (
        <Card title="Integration health">
          <div
            className="grid grid-cols-2 gap-3 text-xs sm:grid-cols-4"
            data-testid="integration-health"
          >
            <div>
              <div className="text-lg font-semibold text-ink">{status.data.needs_review}</div>
              <div className="text-ink-muted">need review</div>
            </div>
            <div>
              <div className="text-lg font-semibold text-ink">{status.data.imported}</div>
              <div className="text-ink-muted">imported</div>
            </div>
            <div>
              <div className="text-lg font-semibold text-ink">
                {status.data.analyses_truncated}
              </div>
              <div className="text-ink-muted">partial analyses</div>
            </div>
            <div>
              <div className="text-lg font-semibold text-ink">{status.data.gitops_failed}</div>
              <div className="text-ink-muted">failed pull requests</div>
            </div>
          </div>
          {status.data.gitops_pr_enabled ? null : (
            <p className="mt-3 text-xs text-ink-muted" data-testid="gitops-disabled">
              GitOps pull requests are switched off. Drake will not write to any repository.
            </p>
          )}
        </Card>
      )}

      <Card title="Sessions">
        {page.state === "loading" ? (
          <DataState kind="loading" />
        ) : page.state === "error" ? (
          page.notFound ? (
            <DataState kind="permission-denied" />
          ) : (
            <DataState kind="error" description={page.message} onRetry={retryPage} />
          )
        ) : page.data.items.length === 0 ? (
          <DataState
            kind="empty"
            title="No onboarding sessions"
            description="Nothing in your scope is being onboarded. This is not a statement about which repositories exist."
          />
        ) : (
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-ink-muted">
              <tr>
                <th className="pb-2 pr-3 font-medium">Repository</th>
                <th className="pb-2 pr-3 font-medium">State</th>
                <th className="pb-2 pr-3 font-medium">Plan</th>
                <th className="pb-2 pr-3 font-medium">Project</th>
                <th className="pb-2 font-medium">Opened</th>
              </tr>
            </thead>
            <tbody>
              {page.data.items.map((session) => (
                <SessionRow key={session.id} session={session} />
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

export default function OnboardingPage() {
  return (
    <Suspense fallback={<DataState kind="loading" />}>
      <OnboardingInner />
    </Suspense>
  );
}
