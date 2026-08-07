"use client";

/** Catalog onboarding: scan, manifest, review and import.
 *
 * Three stages, shown honestly. The Import action is live only when the
 * server says the draft is importable — which means the manifest came from
 * the repository, validated, and the reviewed commit is still current.
 * A draft Drake generated is downloadable and explicitly not importable,
 * because ADR-0007 makes the repository the source of intent.
 */

import Link from "next/link";
import { useCallback, useState } from "react";

import { DataState } from "@/components/state/DataState";
import { StatusBadge, type HealthStatus } from "@/components/state/StatusBadge";
import { ApiError, apiGet, apiMutate } from "@/lib/api";
import {
  blockedReason,
  onboardingDownloadPath,
  onboardingImportPath,
  onboardingPath,
  onboardingScanPath,
  onboardingValidatePath,
  type GitHubRepository,
  type ImportResult,
  type ManifestValidation,
  type OnboardingDraft,
  type OnboardingDraftState,
} from "@/lib/github";

/** No onboarding state borrows the healthy colour except a real import. */
const DRAFT_BADGE: Record<OnboardingDraftState, { status: HealthStatus; label: string }> = {
  not_started: { status: "unknown", label: "not started" },
  scanning: { status: "maintenance", label: "scanning" },
  needs_input: { status: "warning", label: "needs input" },
  invalid: { status: "critical", label: "invalid manifest" },
  ready_to_import: { status: "maintenance", label: "ready to import" },
  imported: { status: "healthy", label: "imported" },
  failed: { status: "critical", label: "failed" },
};

function DraftBadge({ state }: { state: OnboardingDraftState }) {
  const spec = DRAFT_BADGE[state] ?? { status: "unknown" as HealthStatus, label: state };
  return <StatusBadge status={spec.status} label={spec.label} />;
}

function Stage({
  index,
  title,
  children,
  testId,
}: {
  index: number;
  title: string;
  children: React.ReactNode;
  testId: string;
}) {
  return (
    <section className="rounded-xl border border-border p-4" aria-label={title} data-testid={testId}>
      <h3 className="text-sm font-medium text-ink">
        <span className="mr-2 text-ink-muted">{index}.</span>
        {title}
      </h3>
      <div className="mt-3">{children}</div>
    </section>
  );
}

export function OnboardingPanel({
  repository,
  csrfToken,
  canManage,
}: {
  repository: GitHubRepository;
  csrfToken: string;
  canManage: boolean;
}) {
  const [draft, setDraft] = useState<OnboardingDraft | null>(null);
  const [imported, setImported] = useState<ImportResult | null>(null);
  const [validation, setValidation] = useState<ManifestValidation | null>(null);
  const [edited, setEdited] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const blocked = blockedReason(repository);

  const load = useCallback(async () => {
    setError(null);
    try {
      setDraft(await apiGet<OnboardingDraft>(onboardingPath(repository.id)));
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Could not load onboarding state.");
    }
  }, [repository.id]);

  const scan = useCallback(async () => {
    setBusy(true);
    setError(null);
    setValidation(null);
    try {
      const result = await apiMutate<OnboardingDraft>(onboardingScanPath(repository.id), {
        csrfToken,
      });
      setDraft(result);
      setEdited(result.draft_manifest ?? "");
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "The scan could not be started.");
    } finally {
      setBusy(false);
    }
  }, [csrfToken, repository.id]);

  const validate = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      setValidation(
        await apiMutate<ManifestValidation>(onboardingValidatePath(repository.id), {
          csrfToken,
          body: { content: edited },
        }),
      );
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "The manifest could not be checked.");
    } finally {
      setBusy(false);
    }
  }, [csrfToken, edited, repository.id]);

  const runImport = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      setImported(
        await apiMutate<ImportResult>(onboardingImportPath(repository.id), { csrfToken }),
      );
      await load();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "The import did not complete.");
    } finally {
      setBusy(false);
    }
  }, [csrfToken, load, repository.id]);

  if (blocked) {
    return (
      <div data-testid="onboarding-blocked">
        <DataState kind="permission-denied" title="Onboarding is not available" description={blocked} />
      </div>
    );
  }

  const discovery = draft?.discovery ?? {};
  const files = discovery.files ?? [];
  const detections = discovery.detections ?? [];

  return (
    <div className="mt-4 space-y-3" data-testid="onboarding-panel">
      {error ? (
        <div data-testid="onboarding-error">
          <DataState kind="error" description={error} onRetry={load} />
        </div>
      ) : null}

      <Stage index={1} title="Scan" testId="onboarding-stage-scan">
        <div className="flex flex-wrap items-center gap-2 text-xs text-ink-secondary">
          <span className="font-mono">{repository.full_name}</span>
          <span>·</span>
          <span>installation {repository.installation_external_id}</span>
          {draft ? <DraftBadge state={draft.state} /> : null}
        </div>
        {draft?.commit_sha ? (
          <p className="mt-2 text-xs text-ink-muted">
            Scanned at commit <span className="font-mono">{draft.commit_sha.slice(0, 12)}</span>.
            Every file was read at exactly this commit.
          </p>
        ) : (
          <p className="mt-2 text-xs text-ink-muted">
            Drake reads a short allowlist of metadata files. It never clones the repository, runs
            its code, or writes anything back.
          </p>
        )}
        {discovery.truncated ? (
          <p className="mt-2 text-xs text-warning" data-testid="onboarding-truncated">
            The scan stopped at one of its budgets, so this is not a complete picture.
          </p>
        ) : null}
        {files.length > 0 ? (
          <ul className="mt-3 space-y-1" data-testid="onboarding-files">
            {files.slice(0, 12).map((file) => (
              <li key={file.path} className="font-mono text-xs text-ink-secondary">
                {file.path}
              </li>
            ))}
          </ul>
        ) : null}
        {detections.length > 0 ? (
          <dl className="mt-3 space-y-1" data-testid="onboarding-detections">
            {detections.map((item) => (
              <div key={`${item.kind}-${item.value}`} className="text-xs">
                <dt className="inline text-ink">
                  {item.kind}: {item.value}
                </dt>
                <dd className="inline text-ink-muted">
                  {" "}
                  — {item.evidence} ({item.confidence} confidence)
                </dd>
              </div>
            ))}
          </dl>
        ) : null}
        {canManage ? (
          <button
            type="button"
            onClick={scan}
            disabled={busy}
            data-testid="onboarding-scan-button"
            className="mt-3 rounded-lg border border-border px-2.5 py-1 text-xs font-medium text-ink-secondary hover:bg-surface-sunken disabled:opacity-60"
          >
            {busy ? "Working…" : draft ? "Scan again" : "Start onboarding"}
          </button>
        ) : (
          <p className="mt-3 text-xs text-ink-muted">
            You can review onboarding here. Starting a scan needs integration management
            permission.
          </p>
        )}
      </Stage>

      {draft && draft.state !== "not_started" ? (
        <Stage index={2} title="Manifest" testId="onboarding-stage-manifest">
          {draft.manifest_source === "repository" ? (
            <p className="text-xs text-ink-secondary">
              This repository declares <span className="font-mono">.drake/project.yaml</span>.
            </p>
          ) : (
            <div data-testid="onboarding-generated">
              <DataState
                kind="no-data"
                title="No manifest in this repository"
                description="Drake generated a starting point from what it could observe. Commit this file to the repository as .drake/project.yaml, then rescan — Drake imports manifests from the repository, never a copy edited here."
              />
            </div>
          )}

          {draft.findings.length > 0 ? (
            <div className="mt-3" data-testid="onboarding-findings">
              <p className="text-xs font-medium text-critical">Validation findings</p>
              <ul className="mt-1 space-y-2">
                {draft.findings.map((finding, index) => (
                  <li
                    key={`${finding.rule}-${finding.path}-${index}`}
                    className="rounded-lg bg-critical-soft p-2"
                  >
                    <p className="font-mono text-xs text-ink">{finding.path}</p>
                    <p className="text-xs text-ink-secondary">{finding.message}</p>
                    <p className="font-mono text-xs text-ink-muted">{finding.rule}</p>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {draft.operator_inputs_required.length > 0 ? (
            <div className="mt-3" data-testid="onboarding-operator-inputs">
              <p className="text-xs font-medium text-ink">Decisions Drake will not guess</p>
              <dl className="mt-1 divide-y divide-border">
                {draft.operator_inputs_required.map((input) => (
                  <div key={input.field} className="py-1">
                    <dt className="font-mono text-xs text-ink">{input.field}</dt>
                    <dd className="text-xs text-ink-muted">{input.reason}</dd>
                  </div>
                ))}
              </dl>
            </div>
          ) : null}

          {draft.draft_manifest ? (
            <div className="mt-3 space-y-2">
              <label htmlFor="draft-manifest" className="block text-xs font-medium text-ink">
                Draft manifest
              </label>
              <textarea
                id="draft-manifest"
                data-testid="onboarding-draft-editor"
                value={edited || draft.draft_manifest}
                onChange={(event) => setEdited(event.target.value)}
                rows={12}
                spellCheck={false}
                className="w-full rounded-lg border border-border bg-surface-sunken p-2 font-mono text-xs text-ink"
              />
              <div className="flex flex-wrap items-center gap-2">
                <a
                  href={onboardingDownloadPath(repository.id)}
                  download="project.yaml"
                  data-testid="onboarding-download"
                  className="rounded-lg border border-border px-2.5 py-1 text-xs font-medium text-ink-secondary hover:bg-surface-sunken"
                >
                  Download project.yaml
                </a>
                {canManage ? (
                  <button
                    type="button"
                    onClick={validate}
                    disabled={busy}
                    data-testid="onboarding-validate-button"
                    className="rounded-lg border border-border px-2.5 py-1 text-xs font-medium text-ink-secondary hover:bg-surface-sunken disabled:opacity-60"
                  >
                    Check this draft
                  </button>
                ) : null}
              </div>
              {validation ? (
                <div data-testid="onboarding-validation">
                  <DataState
                    kind={validation.valid ? "no-data" : "error"}
                    title={validation.valid ? "This draft is valid" : "This draft is not valid yet"}
                    description={validation.next_step}
                  />
                </div>
              ) : null}
            </div>
          ) : null}
        </Stage>
      ) : null}

      {draft && draft.state !== "not_started" ? (
        <Stage index={3} title="Review and import" testId="onboarding-stage-import">
          {imported || draft.accepted_project_id ? (
            <div data-testid="onboarding-imported">
              <p className="text-xs text-ink-secondary">
                Imported into the Drake catalog. Runtime readiness is separate: nothing was
                deployed.
              </p>
              <Link
                href={`/projects/${imported?.project_id ?? draft.accepted_project_id}`}
                data-testid="onboarding-project-link"
                className="mt-2 inline-block rounded-lg border border-border px-2.5 py-1 text-xs font-medium text-ink-secondary hover:bg-surface-sunken"
              >
                Open the project
              </Link>
            </div>
          ) : (
            <>
              {!draft.importable ? (
                <p className="text-xs text-ink-muted" data-testid="onboarding-import-blocked">
                  {draft.manifest_source === "repository"
                    ? "This manifest cannot be imported yet. Resolve the findings above and scan again."
                    : "Commit the manifest to the repository and scan again before importing."}
                </p>
              ) : (
                <p className="text-xs text-ink-secondary">
                  Importing creates the project, its environments and services in one step.
                  Nothing is deployed and nothing is written back to GitHub.
                </p>
              )}
              {canManage ? (
                <button
                  type="button"
                  onClick={runImport}
                  disabled={busy || !draft.importable}
                  data-testid="onboarding-import-button"
                  className="mt-3 rounded-lg border border-border px-2.5 py-1 text-xs font-medium text-ink-secondary hover:bg-surface-sunken disabled:opacity-60"
                >
                  {busy ? "Importing…" : "Import into catalog"}
                </button>
              ) : null}
            </>
          )}
        </Stage>
      ) : null}
    </div>
  );
}
