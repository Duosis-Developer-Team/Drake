"use client";

import Link from "next/link";
import { useCallback, useState } from "react";

import { LoadGate, MetaRow, useApi } from "@/components/catalog/primitives";
import {
  InstallationBadge,
  OnboardingBadge,
  VerdictBadge,
  formatUtc,
} from "@/components/github/primitives";
import { DataState } from "@/components/state/DataState";
import { StatusBadge } from "@/components/state/StatusBadge";
import { Card } from "@/components/ui/Card";
import { ApiError, apiGet, apiMutate } from "@/lib/api";
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

export default function GitHubIntegrationPage() {
  const { hasPermission } = useSession();
  const canManage = hasPermission("integration.manage");
  // Separate on purpose: managing the GitHub integration is not permission
  // to onboard a project into the catalog.
  const canOnboard = hasPermission("onboarding.manage");

  const [status, retryStatus] = useApi<GitHubStatus>("/v1/integrations/github/status");
  const [installations, retryInstallations] = useApi<{
    installations: GitHubInstallation[];
  }>("/v1/integrations/github/installations");
  const [repositories, retryRepositories] = useApi<{
    repositories: GitHubRepository[];
    next_cursor: string | null;
  }>("/v1/integrations/github/repositories");

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <div>
        <p className="text-xs text-ink-muted">
          <Link href="/integrations" className="hover:text-ink">
            Integrations
          </Link>{" "}
          / GitHub
        </p>
        <h1 className="mt-1 text-xl font-semibold tracking-tight text-ink">
          GitHub App integration
        </h1>
        <p className="mt-1 text-sm text-ink-secondary">
          Read-only repository governance. Drake evaluates branch protection, required
          checks and deployment gates — it never changes a repository setting.
        </p>
      </div>

      <LoadGate value={status} retry={retryStatus}>
        {(data) => (
          <ConfigurationCard status={data} />
        )}
      </LoadGate>

      <section aria-labelledby="installations-heading">
        <h2 id="installations-heading" className="mb-3 text-sm font-semibold text-ink">
          Installations
        </h2>
        <Card>
          <LoadGate value={installations} retry={retryInstallations}>
            {(data) =>
              data.installations.length === 0 ? (
                <DataState
                  kind="not-configured"
                  title="No installation yet"
                  description="Once the GitHub App is installed for the organization, its installation appears here."
                />
              ) : (
                <ul className="divide-y divide-border" data-testid="installation-list">
                  {data.installations.map((installation) => (
                    <li
                      key={installation.id}
                      className="flex flex-wrap items-center justify-between gap-3 px-1 py-3"
                    >
                      <span className="min-w-0">
                        <span className="block truncate text-sm font-medium text-ink">
                          {installation.account_login || "unknown account"}
                        </span>
                        <span className="block truncate font-mono text-xs text-ink-muted">
                          {installation.repository_selection} ·{" "}
                          {installation.subscribed_events.join(", ") || "no events"}
                        </span>
                      </span>
                      <span className="flex flex-wrap items-center gap-2">
                        {installation.last_error_code ? (
                          <StatusBadge status="warning" label={installation.last_error_code} />
                        ) : null}
                        <InstallationBadge state={installation.state} />
                      </span>
                    </li>
                  ))}
                </ul>
              )
            }
          </LoadGate>
        </Card>
      </section>

      <section aria-labelledby="repositories-heading">
        <h2 id="repositories-heading" className="mb-3 text-sm font-semibold text-ink">
          Repositories
        </h2>
        <Card>
          <LoadGate value={repositories} retry={retryRepositories}>
            {(data) =>
              data.repositories.length === 0 ? (
                <DataState
                  kind="empty"
                  title="No repositories in your scope"
                  description="Repositories the installation can see, and you are authorized to view, appear here."
                />
              ) : (
                <div className="space-y-4" data-testid="repository-list">
                  {data.repositories.map((repository) => (
                    <RepositoryCard
                      key={repository.id}
                      repository={repository}
                      canManage={canManage}
                      canOnboard={canOnboard}
                    />
                  ))}
                </div>
              )
            }
          </LoadGate>
        </Card>
      </section>
    </div>
  );
}

function ConfigurationCard({ status }: { status: GitHubStatus }) {
  const configured = status.configuration_state === "configured";
  return (
    <Card title="Connection readiness" data-testid="github-status-card">
      {configured ? (
        <dl className="divide-y divide-border">
          <MetaRow label="Configuration">
            <StatusBadge status="healthy" label="configured" />
          </MetaRow>
          <MetaRow label="Installations">
            <span className="font-mono text-xs">{status.installations}</span>
          </MetaRow>
          <MetaRow label="Repositories">
            <span className="font-mono text-xs">{status.repositories}</span>
          </MetaRow>
          <MetaRow label="Blocked by a security gate">
            <span className="flex items-center gap-2">
              <span className="font-mono text-xs">{status.blocked_repositories}</span>
              {status.blocked_repositories > 0 ? (
                <StatusBadge status="critical" label="operator action required" />
              ) : null}
            </span>
          </MetaRow>
          <MetaRow label="Subscribed events">
            <span className="font-mono text-xs">{status.supported_events.join(", ")}</span>
          </MetaRow>
        </dl>
      ) : (
        <div className="space-y-3" data-testid="github-not-configured">
          <DataState
            kind="not-configured"
            title="GitHub App is not connected yet"
            description="Drake shows nothing here until an operator supplies the app identity and its secret references."
          />
          <div>
            <p className="text-xs font-medium text-ink-muted">Waiting on the operator:</p>
            <ul className="mt-1 space-y-1">
              {status.missing_operator_inputs.map((item) => (
                <li key={item} className="text-sm text-ink-secondary">
                  • {MISSING_INPUT_LABELS[item] ?? item}
                </li>
              ))}
            </ul>
            <p className="mt-2 text-xs text-ink-muted">
              Secrets are supplied out of band and referenced by name; they are never
              entered or displayed in this interface.
            </p>
          </div>
        </div>
      )}
    </Card>
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
      setActionError(error instanceof ApiError ? error.message : "request failed");
    }
  }, [repository.id]);

  const reconcile = useCallback(async () => {
    setBusy(true);
    setActionError(null);
    try {
      await apiMutate(`/v1/integrations/github/repositories/${repository.id}/reconcile`, {
        csrfToken: csrf,
        method: "POST",
      });
      await loadPolicy();
    } catch (error) {
      setActionError(error instanceof ApiError ? error.message : "request failed");
    } finally {
      setBusy(false);
    }
  }, [csrf, loadPolicy, repository.id]);

  return (
    <section
      className="rounded-xl border border-border p-4"
      aria-label={repository.full_name}
      data-testid="repository-card"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-ink">{repository.full_name}</p>
          <p className="truncate font-mono text-xs text-ink-muted">
            default branch: {repository.default_branch || "unknown"} ·{" "}
            {repository.private ? "private" : "public"}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {stale && !blocked ? <StatusBadge status="stale" label="stale" /> : null}
          <OnboardingBadge state={repository.onboarding_state} />
        </div>
      </div>

      {repository.pending_reconciliation && !blocked ? (
        <div className="mt-3" data-testid="reconciliation-required">
          <DataState
            kind="no-data"
            title="Reconciliation required"
            description="A recent change could not be recorded in full, so this installation is being re-read from the provider. What is shown here may be incomplete until that finishes."
          />
        </div>
      ) : null}

      {blocked ? (
        <div className="mt-3" data-testid="security-gate-warning">
          <DataState
            kind="permission-denied"
            title="Blocked by a manual security gate"
            description={
              repository.security_gate_reason ||
              "An operator must review and close this security gate before Drake may onboard this repository."
            }
          />
        </div>
      ) : null}

      <dl className="mt-3 divide-y divide-border">
        <MetaRow label="Last reconciliation">
          <span className="font-mono text-xs">{formatUtc(repository.last_reconciled_at)}</span>
        </MetaRow>
        <MetaRow label="Last policy evaluation">
          <span className="font-mono text-xs">
            {formatUtc(repository.last_policy_evaluated_at)}
          </span>
        </MetaRow>
        {repository.last_error_code ? (
          <MetaRow label="Last error">
            <span className="font-mono text-xs">{repository.last_error_code}</span>
          </MetaRow>
        ) : null}
      </dl>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={loadPolicy}
          className="rounded-lg border border-border px-2.5 py-1 text-xs font-medium text-ink-secondary hover:bg-surface-sunken"
        >
          Show last policy result
        </button>
        {canManage ? (
          <button
            type="button"
            onClick={reconcile}
            disabled={busy || blocked}
            className="rounded-lg border border-border px-2.5 py-1 text-xs font-medium text-ink-secondary hover:bg-surface-sunken disabled:opacity-60"
            data-testid="reconcile-button"
          >
            {busy ? "Evaluating…" : "Reconcile (dry run)"}
          </button>
        ) : null}
        {blocked ? (
          <span className="text-xs text-ink-muted">
            Reconciliation stays disabled while the gate is open.
          </span>
        ) : null}
      </div>

      {actionError ? (
        <div className="mt-3">
          <DataState kind="error" description={actionError} onRetry={loadPolicy} />
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
      <div className="mt-3">
        {canOnboard ? (
          <Link
            href={`/onboarding?repository_id=${repository.id}`}
            data-testid="onboarding-link"
            className="inline-block rounded-lg border border-border px-2.5 py-1 text-xs font-medium text-ink-secondary hover:bg-surface-sunken"
          >
            Onboard this repository →
          </Link>
        ) : (
          <p className="text-xs text-ink-muted" data-testid="onboarding-link-denied">
            Onboarding this repository needs the onboarding manage permission.
          </p>
        )}
      </div>
    </section>
  );
}

function PolicyResult({ snapshot }: { snapshot: PolicySnapshot }) {
  if (snapshot.state === "never_evaluated") {
    return (
      <div className="mt-3" data-testid="policy-never-evaluated">
        <DataState
          kind="no-data"
          title="No policy evaluation yet"
          description="Run a dry-run reconciliation to produce the first snapshot."
        />
      </div>
    );
  }
  const blocking = snapshot.results.filter((result) => result.blocking && result.verdict === "fail");
  const other = snapshot.results.filter(
    (result) => !(result.blocking && result.verdict === "fail") && result.verdict !== "pass",
  );
  return (
    <div className="mt-3 space-y-3" data-testid="policy-result">
      <div className="flex flex-wrap items-center gap-2 text-xs text-ink-muted">
        <span>Overall:</span>
        <VerdictBadge verdict={snapshot.overall} />
        <span>
          {snapshot.blocking_count ?? 0} blocking · {snapshot.unknown_count ?? 0} unknown ·
          evaluated <time className="font-mono">{formatUtc(snapshot.evaluated_at)}</time>
        </span>
        {snapshot.dry_run ? <StatusBadge status="maintenance" label="dry run" /> : null}
      </div>

      {blocking.length > 0 ? (
        <div data-testid="blocking-violations">
          <p className="text-xs font-medium text-critical">Blocking violations</p>
          <ul className="mt-1 space-y-2">
            {blocking.map((result) => (
              <li key={result.rule_id} className="rounded-lg bg-critical-soft p-2">
                <p className="text-sm font-medium text-ink">{result.title}</p>
                <p className="text-xs text-ink-secondary">{result.observed}</p>
                <p className="mt-1 text-xs text-ink-muted">{result.remediation}</p>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {other.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-border text-xs text-ink-muted">
                <th scope="col" className="py-2 pr-3 font-medium">
                  Rule
                </th>
                <th scope="col" className="py-2 pr-3 font-medium">
                  Verdict
                </th>
                <th scope="col" className="py-2 font-medium">
                  Observed
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {other.map((result) => (
                <tr key={result.rule_id}>
                  <td className="py-2 pr-3 font-mono text-xs">{result.rule_id}</td>
                  <td className="py-2 pr-3">
                    <VerdictBadge verdict={result.verdict} />
                  </td>
                  <td className="py-2 text-xs text-ink-secondary">{result.observed}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
