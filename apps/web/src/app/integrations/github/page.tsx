"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  ArrowRight,
  Building2,
  FolderGit2,
  GitBranch,
  Github,
  KeyRound,
  Lock,
  ShieldAlert,
  ShieldCheck,
  Webhook,
} from "lucide-react";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";

import { LoadGate, useApi } from "@/components/catalog/primitives";
import {
  InstallationBadge,
  OnboardingBadge,
  VerdictBadge,
  formatUtc,
} from "@/components/github/primitives";
import {
  IconBubble,
  InitialsBubble,
  MetaGrid,
  PILL_BUTTON,
  StateCard,
} from "@/components/features/configure/kit";
import { DataState } from "@/components/state/DataState";
import { StatusBadge } from "@/components/state/StatusBadge";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { ApiError, apiGet, apiMutate } from "@/lib/api";
import type { StatusTone } from "@/lib/design/status";
import {
  CANDIDATE_BLOCKERS,
  fetchRepositoryCandidate,
  type RepositoryCandidate,
} from "@/lib/onboarding";
import { useSession } from "@/lib/session";
import {
  isStale,
  type GitHubInstallation,
  type GitHubRepository,
  type GitHubStatus,
  type PolicySnapshot,
} from "@/lib/github";

const MISSING_INPUT_LABELS: Record<string, string> = {
  feature_disabled: "The GitHub App integration is switched off",
  app_identity: "App client id (or app id)",
  private_key_reference: "Private key secret reference",
  webhook_secret_reference: "Webhook secret reference",
};

const REPO_TONE: Record<string, StatusTone> = {
  ready: "success",
  blocked: "critical",
  degraded: "warning",
  validating: "info",
  disabled: "stale",
  discovered: "unknown",
};

export default function GitHubIntegrationPage() {
  const { hasPermission } = useSession();
  const canManage = hasPermission("integration.manage");
  // Separate on purpose: managing the GitHub integration is not permission
  // to onboard a project into the catalog.
  const canOnboard = hasPermission("onboarding.manage");

  const [status, retryStatus] = useApi<GitHubStatus>(
    "/v1/integrations/github/status",
  );
  const [installations, retryInstallations] = useApi<{
    installations: GitHubInstallation[];
  }>("/v1/integrations/github/installations");
  const [repositories, retryRepositories] = useApi<{
    repositories: GitHubRepository[];
    next_cursor: string | null;
  }>("/v1/integrations/github/repositories");

  return (
    <PageFrame>
      <PageHeader
        title="GitHub App integration"
        description={
          <>
            <Link href="/integrations" className="hover:text-ink">
              Integrations
            </Link>{" "}
            / GitHub — read-only repository governance; Drake never changes a
            repository setting.
          </>
        }
      />

      <div className="space-y-6">
        {status.state === "ready" ? (
          <ConfigurationCard status={status.data} />
        ) : (
          <Panel>
            <LoadGate value={status} retry={retryStatus}>
              {() => null}
            </LoadGate>
          </Panel>
        )}

        <div className="page-split">
          <section aria-labelledby="repositories-heading" className="page-main">
            <div className="-mb-2 flex items-baseline justify-between gap-3">
              <h2
                id="repositories-heading"
                className="text-[1.0625rem] font-semibold text-ink"
              >
                Repositories
              </h2>
              {repositories.state === "ready" ? (
                <span className="text-micro text-ink-muted">
                  {repositories.data.repositories.length} visible to you
                </span>
              ) : null}
            </div>
            {repositories.state === "ready" ? null : (
              <Panel>
                <LoadGate value={repositories} retry={retryRepositories}>
                  {() => null}
                </LoadGate>
              </Panel>
            )}
            {repositories.state === "ready" ? (
              repositories.data.repositories.length === 0 ? (
                <Panel>
                  <StateCard
                    kind="empty"
                    icon={FolderGit2}
                    title="No repositories in your scope"
                    description="Repositories the installation can see, and you are authorized to view, appear here."
                  />
                </Panel>
              ) : (
                <div className="space-y-6" data-testid="repository-list">
                  {repositories.data.repositories.map((repository) => (
                    <RepositoryCard
                      key={repository.id}
                      repository={repository}
                      canManage={canManage}
                      canOnboard={canOnboard}
                    />
                  ))}
                </div>
              )
            ) : null}
          </section>

          <div className="page-aside">
            <Panel flush aria-labelledby="installations-heading">
              <PanelHeader
                flush
                title="Installations"
                id="installations-heading"
                description="Organizations the App is installed on"
              />
              <LoadGate value={installations} retry={retryInstallations}>
                {(data) =>
                  data.installations.length === 0 ? (
                    <div className="px-7 py-2">
                      <StateCard
                        kind="not-configured"
                        icon={Building2}
                        title="No installation yet"
                        description="Once the GitHub App is installed for the organization, its installation appears here."
                      />
                    </div>
                  ) : (
                    <ul
                      className="divide-y divide-border"
                      data-testid="installation-list"
                    >
                      {data.installations.map((installation) => (
                        <li
                          key={installation.id}
                          className="px-7 py-4 transition-colors hover:bg-surface-hover"
                        >
                          <div className="flex items-center gap-3">
                            <InitialsBubble
                              name={installation.account_login || "?"}
                            />
                            <div className="min-w-0 flex-1">
                              <p className="truncate text-body font-semibold text-ink">
                                {installation.account_login ||
                                  "unknown account"}
                              </p>
                              <p className="truncate text-micro text-ink-muted">
                                {installation.repository_selection} repositories
                                · {installation.subscribed_events.length} events
                              </p>
                            </div>
                            <InstallationBadge state={installation.state} />
                          </div>
                          {installation.subscribed_events.length > 0 ||
                          installation.last_error_code ? (
                            <div className="mt-3 flex flex-wrap gap-1.5 pl-[3.25rem]">
                              {installation.last_error_code ? (
                                <StatusBadge
                                  status="warning"
                                  label={installation.last_error_code}
                                  size="compact"
                                />
                              ) : null}
                              {installation.subscribed_events.map((event) => (
                                <span
                                  key={event}
                                  className="rounded-full bg-surface-2 px-2 py-0.5 font-mono text-micro text-ink-secondary"
                                >
                                  {event}
                                </span>
                              ))}
                            </div>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  )
                }
              </LoadGate>
            </Panel>
          </div>
        </div>
      </div>
    </PageFrame>
  );
}

function StatTile({
  icon,
  tone,
  label,
  value,
  detail,
}: {
  icon: typeof Github;
  tone?: StatusTone;
  label: string;
  value: number;
  detail?: React.ReactNode;
}) {
  return (
    <div className="flex h-full flex-col rounded-[1.25rem] bg-surface-2 p-5">
      <IconBubble icon={icon} tone={tone ?? "neutral"} />
      <span
        data-tabular
        className={`mt-5 text-[2.25rem] leading-none font-semibold tracking-[-0.03em] ${
          tone === "critical" ? "text-critical" : "text-ink"
        }`}
      >
        {value}
      </span>
      <span className="mt-2 text-caption font-medium text-ink-secondary">
        {label}
      </span>
      {detail ? <div className="mt-auto pt-3">{detail}</div> : null}
    </div>
  );
}

function ConfigurationCard({ status }: { status: GitHubStatus }) {
  const configured = status.configuration_state === "configured";
  return (
    <Panel data-testid="github-status-card">
      <div className="grid grid-cols-1 items-stretch gap-6 lg:grid-cols-[minmax(0,20rem)_minmax(0,1fr)]">
        <div className="flex flex-col gap-4">
          <div className="flex items-center gap-4">
            <span
              aria-hidden
              className="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl bg-accent text-ink-inverse"
            >
              <Github className="h-7 w-7" />
            </span>
            <div className="min-w-0">
              <h2 className="text-[1.25rem] leading-7 font-semibold tracking-[-0.01em] text-ink">
                Connection readiness
              </h2>
              <p className="text-caption text-ink-muted">
                GitHub App · read-only
              </p>
            </div>
          </div>
          {configured ? (
            <>
              <div className="flex items-center gap-2">
                <span className="text-caption text-ink-muted">
                  Configuration
                </span>
                <StatusBadge status="healthy" label="configured" />
              </div>
              <div className="mt-auto">
                <p className="mb-2 flex items-center gap-1.5 text-micro text-ink-muted">
                  <Webhook aria-hidden className="h-3.5 w-3.5" />
                  Subscribed events
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {status.supported_events.map((event) => (
                    <span
                      key={event}
                      className="rounded-full border border-border bg-surface-2 px-2.5 py-1 font-mono text-micro text-ink-secondary"
                    >
                      {event}
                    </span>
                  ))}
                </div>
              </div>
            </>
          ) : null}
        </div>

        {configured ? (
          <div className="grid grid-cols-1 items-stretch gap-4 sm:grid-cols-3">
            <StatTile
              icon={Building2}
              label="Installations"
              value={status.installations}
            />
            <StatTile
              icon={FolderGit2}
              tone="info"
              label="Repositories"
              value={status.repositories}
            />
            <StatTile
              icon={ShieldAlert}
              tone={status.blocked_repositories > 0 ? "critical" : undefined}
              label="Blocked by a security gate"
              value={status.blocked_repositories}
              detail={
                status.blocked_repositories > 0 ? (
                  <StatusBadge
                    status="critical"
                    label="operator action required"
                  />
                ) : (
                  <span className="text-micro text-ink-muted">
                    No gate is open
                  </span>
                )
              }
            />
          </div>
        ) : (
          <div
            className="rounded-[1.25rem] bg-surface-2 p-6"
            data-testid="github-not-configured"
          >
            <StateCard
              inline
              kind="not-configured"
              title="GitHub App is not connected yet"
              description="Drake shows nothing here until an operator supplies the app identity and its secret references."
            >
              <ul className="mt-4 space-y-2">
                {status.missing_operator_inputs.map((item) => (
                  <li
                    key={item}
                    className="flex items-center gap-3 rounded-full bg-surface px-3 py-2 text-caption text-ink"
                  >
                    <KeyRound
                      aria-hidden
                      className="h-4 w-4 shrink-0 text-warning"
                    />
                    {MISSING_INPUT_LABELS[item] ?? item}
                  </li>
                ))}
              </ul>
              <p className="mt-3 flex items-center gap-1.5 text-micro text-ink-muted">
                <Lock aria-hidden className="h-3.5 w-3.5" />
                Secrets are supplied out of band and referenced by name; never
                shown here.
              </p>
            </StateCard>
          </div>
        )}
      </div>
    </Panel>
  );
}

function RepositoryCard({
  repository,
  canManage,
  canOnboard,
}: {
  repository: GitHubRepository;
  canManage: boolean;
  canOnboard: boolean;
}) {
  const { state: session } = useSession();
  const csrf = session.status === "authenticated" ? session.me.csrf_token : "";
  const [snapshot, setSnapshot] = useState<PolicySnapshot | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const blocked = Boolean(repository.security_gate);
  const stale = isStale(repository.last_reconciled_at);

  const loadPolicy = useCallback(async () => {
    setActionError(null);
    try {
      setSnapshot(
        await apiGet<PolicySnapshot>(
          `/v1/integrations/github/repositories/${repository.id}/policy`,
        ),
      );
    } catch (error) {
      setActionError(
        error instanceof ApiError ? error.message : "request failed",
      );
    }
  }, [repository.id]);

  const reconcile = useCallback(async () => {
    setBusy(true);
    setActionError(null);
    try {
      await apiMutate(
        `/v1/integrations/github/repositories/${repository.id}/reconcile`,
        {
          csrfToken: csrf,
          method: "POST",
        },
      );
      await loadPolicy();
    } catch (error) {
      setActionError(
        error instanceof ApiError ? error.message : "request failed",
      );
    } finally {
      setBusy(false);
    }
  }, [csrf, loadPolicy, repository.id]);

  const tone = blocked
    ? "critical"
    : (REPO_TONE[repository.onboarding_state] ?? "unknown");

  return (
    // A <section>: the e2e suite finds the first card as a section inside
    // the repository list.
    <Panel
      aria-label={repository.full_name}
      data-testid="repository-card"
      tone={blocked ? "critical" : "default"}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-4">
          <IconBubble icon={FolderGit2} tone={tone} size="lg" />
          <div className="min-w-0">
            <p className="truncate text-[1.0625rem] font-semibold text-ink">
              {repository.full_name}
            </p>
            <p className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-micro text-ink-muted">
              <span className="inline-flex items-center gap-1">
                <GitBranch aria-hidden className="h-3.5 w-3.5" />
                <span className="font-mono">
                  default branch: {repository.default_branch || "unknown"}
                </span>
              </span>
              <span className="inline-flex items-center gap-1">
                <Lock aria-hidden className="h-3.5 w-3.5" />
                {repository.private ? "private" : "public"}
              </span>
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {stale && !blocked ? (
            <StatusBadge status="stale" label="stale" />
          ) : null}
          <OnboardingBadge state={repository.onboarding_state} />
        </div>
      </div>

      {repository.pending_reconciliation && !blocked ? (
        <div
          data-testid="reconciliation-required"
          className="rounded-[1.25rem] bg-surface-2 p-5"
        >
          <StateCard
            inline
            kind="no-data"
            tone="warning"
            title="Reconciliation required"
            description="A recent change could not be recorded in full, so this installation is being re-read. What is shown may be incomplete until that finishes."
          />
        </div>
      ) : null}

      {blocked ? (
        <div
          data-testid="security-gate-warning"
          className="rounded-[1.25rem] bg-critical-soft p-5"
        >
          <StateCard
            inline
            kind="permission-denied"
            icon={ShieldAlert}
            tone="critical"
            title="Blocked by a manual security gate"
            description={
              repository.security_gate_reason ||
              "An operator must review and close this security gate before Drake may onboard this repository."
            }
          />
        </div>
      ) : null}

      <MetaGrid
        columns={repository.last_error_code ? 3 : 2}
        items={[
          {
            label: "Last reconciliation",
            value: (
              <span className="font-mono">
                {formatUtc(repository.last_reconciled_at)}
              </span>
            ),
          },
          {
            label: "Last policy evaluation",
            value: (
              <span className="font-mono">
                {formatUtc(repository.last_policy_evaluated_at)}
              </span>
            ),
          },
          ...(repository.last_error_code
            ? [
                {
                  label: "Last error",
                  value: (
                    <span className="font-mono text-critical">
                      {repository.last_error_code}
                    </span>
                  ),
                },
              ]
            : []),
        ]}
      />

      {actionError ? (
        <div className="rounded-[1.25rem] bg-critical-soft p-4">
          <DataState
            kind="error"
            description={actionError}
            onRetry={loadPolicy}
          />
        </div>
      ) : null}

      {snapshot ? <PolicyResult snapshot={snapshot} /> : null}

      {/*
        Onboarding lives on its own screen now. The panel that used to open
        here drove the Sprint 5B import — scan, validate, import — which
        wrote catalog rows with no plan, no approval and no receipt. Those
        endpoints answer 410, and this is a link to the reviewed flow rather
        than a second way in.

        Gated on `onboarding.manage`, not `integration.manage`: managing an
        integration is not permission to onboard a project, and a link that
        leads somewhere the operator is refused is worse than no link.
      */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-5">
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={loadPolicy} className={PILL_BUTTON}>
            <ShieldCheck aria-hidden className="h-3.5 w-3.5" />
            Show last policy result
          </button>
          {canManage ? (
            <button
              type="button"
              onClick={reconcile}
              disabled={busy || blocked}
              title={
                blocked
                  ? "Reconciliation stays disabled while the gate is open."
                  : undefined
              }
              className={PILL_BUTTON}
              data-testid="reconcile-button"
            >
              {busy ? "Evaluating…" : "Reconcile (dry run)"}
            </button>
          ) : null}
        </div>
        <OnboardingLink repositoryId={repository.id} canOnboard={canOnboard} />
      </div>
    </Panel>
  );
}

/**
 * The onboarding action for ONE repository, decided by the server.
 *
 * `hasPermission("onboarding.manage")` only says the operator holds it
 * SOMEWHERE. Offering a live action on that basis is the same mistake the
 * session endpoints made before this sprint: the permission came from one
 * place and the target from another, so the link led to a screen that
 * refuses. The server answers about this repository, in this scope.
 */
function OnboardingLink({
  repositoryId,
  canOnboard,
}: {
  repositoryId: string;
  canOnboard: boolean;
}) {
  const [candidate, setCandidate] = useState<RepositoryCandidate | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "denied">(
    canOnboard ? "loading" : "denied",
  );

  useEffect(() => {
    if (!canOnboard) return;
    let cancelled = false;
    fetchRepositoryCandidate(repositoryId)
      .then((value) => {
        if (cancelled) return;
        setCandidate(value);
        setState("ready");
      })
      .catch(() => {
        if (!cancelled) setState("denied");
      });
    return () => {
      cancelled = true;
    };
  }, [canOnboard, repositoryId]);

  const note = "max-w-sm text-right text-micro text-ink-muted";

  if (state === "loading") {
    return (
      <p className={note} data-testid="onboarding-link-loading">
        Checking whether this repository can be onboarded…
      </p>
    );
  }

  if (state === "denied") {
    // Says nothing about whether the repository exists elsewhere: the same
    // answer covers "no such repository" and "not in a scope you may act
    // in", which is the distinction the scoping is there to keep.
    return (
      <p className={note} data-testid="onboarding-link-denied">
        Onboarding needs the onboarding manage permission on this
        repository&apos;s scope.
      </p>
    );
  }

  if (candidate?.active_session_id) {
    return (
      <Link
        href={`/onboarding/${candidate.active_session_id}`}
        data-testid="onboarding-link-existing"
        className={PILL_BUTTON}
      >
        Open the open onboarding session
        <ArrowRight aria-hidden className="h-3.5 w-3.5" />
      </Link>
    );
  }

  if (!candidate?.startable) {
    return (
      <p
        className="max-w-sm text-right text-micro text-warning"
        data-testid="onboarding-link-blocked"
      >
        {CANDIDATE_BLOCKERS[candidate?.reason_code ?? ""] ??
          "This repository cannot be onboarded right now."}
      </p>
    );
  }

  return (
    <Link
      href={`/onboarding?repository_id=${repositoryId}`}
      data-testid="onboarding-link"
      className="inline-flex items-center gap-1.5 rounded-full bg-accent px-4 py-1.5 text-caption font-medium text-ink-inverse hover:opacity-90"
    >
      Onboard this repository
      <ArrowRight aria-hidden className="h-3.5 w-3.5" />
    </Link>
  );
}

function PolicyResult({ snapshot }: { snapshot: PolicySnapshot }) {
  if (snapshot.state === "never_evaluated") {
    return (
      <div
        data-testid="policy-never-evaluated"
        className="rounded-[1.25rem] bg-surface-2 p-5"
      >
        <StateCard
          inline
          kind="no-data"
          icon={ShieldCheck}
          title="No policy evaluation yet"
          description="Run a dry-run reconciliation to produce the first snapshot."
        />
      </div>
    );
  }
  const blocking = snapshot.results.filter(
    (result) => result.blocking && result.verdict === "fail",
  );
  const other = snapshot.results.filter(
    (result) =>
      !(result.blocking && result.verdict === "fail") &&
      result.verdict !== "pass",
  );
  const passed = snapshot.results.filter(
    (result) => result.verdict === "pass",
  ).length;
  return (
    <div
      className="space-y-4 rounded-[1.25rem] border border-border p-5"
      data-testid="policy-result"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-body font-semibold text-ink">
            Policy result
          </span>
          <VerdictBadge verdict={snapshot.overall} />
          {snapshot.dry_run ? (
            <StatusBadge status="maintenance" label="dry run" />
          ) : null}
        </div>
        <span className="text-micro text-ink-muted">
          evaluated{" "}
          <time className="font-mono">{formatUtc(snapshot.evaluated_at)}</time>
        </span>
      </div>

      <div className="grid grid-cols-3 gap-3">
        {[
          {
            label: "Blocking",
            value: snapshot.blocking_count ?? 0,
            tone: "critical" as StatusTone,
          },
          {
            label: "Not determinable",
            value: snapshot.unknown_count ?? 0,
            tone: "unknown" as StatusTone,
          },
          {
            label: "Rules passed",
            value: passed,
            tone: "success" as StatusTone,
          },
        ].map((tile) => (
          <div key={tile.label} className="rounded-2xl bg-surface-2 px-4 py-3">
            <p className="text-micro text-ink-muted">{tile.label}</p>
            <p
              data-tabular
              className={`mt-1 text-[1.5rem] leading-none font-semibold ${
                tile.value > 0 && tile.tone === "critical"
                  ? "text-critical"
                  : tile.value > 0 && tile.tone === "unknown"
                    ? "text-unknown"
                    : "text-ink"
              }`}
            >
              {tile.value}
            </p>
          </div>
        ))}
      </div>

      {blocking.length > 0 ? (
        <div data-testid="blocking-violations" className="space-y-2">
          <p className="text-micro font-medium tracking-[0.08em] text-critical uppercase">
            Blocking violations
          </p>
          <ul className="space-y-2">
            {blocking.map((result) => (
              <li
                key={result.rule_id}
                className="flex gap-3 rounded-2xl bg-critical-soft p-4"
              >
                <ShieldAlert
                  aria-hidden
                  className="mt-0.5 h-4 w-4 shrink-0 text-critical"
                />
                <div className="min-w-0">
                  <p className="text-body font-semibold text-ink">
                    {result.title}
                  </p>
                  <p className="text-caption text-ink-secondary">
                    {result.observed}
                  </p>
                  <p className="mt-1 text-caption text-ink-muted">
                    {result.remediation}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {other.length > 0 ? (
        <ul className="divide-y divide-border rounded-2xl border border-border">
          {other.map((result) => (
            <li
              key={result.rule_id}
              className="flex flex-wrap items-center gap-3 px-4 py-3"
            >
              <VerdictBadge verdict={result.verdict} />
              <div className="min-w-0 flex-1">
                <p className="truncate font-mono text-micro text-ink">
                  {result.rule_id}
                </p>
                <p className="text-caption text-ink-secondary">
                  {result.observed}
                </p>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
