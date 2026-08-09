"use client";

/**
 * The operator's actions on one onboarding session.
 *
 * Every button here is a convenience, not a boundary. The API re-checks the
 * permission against the session's own scope and re-checks the state under
 * a row lock, so a disabled button is a courtesy and an enabled one is not
 * a promise. What this component owes the operator is that the courtesy is
 * accurate: a button that is live should work, and one that is not should
 * say why in a sentence rather than by being grey.
 *
 * Two things it deliberately does not do:
 *
 * - render the manifest. The draft downloads through a link so the bytes
 *   never enter the document. A manifest is a file to commit, not markup.
 * - hold the idempotency key anywhere but component memory. Not
 *   localStorage, not the URL, not a log — a key that outlives the tab is a
 *   key that replays an apply nobody asked for.
 */

import { useCallback, useState, type MutableRefObject } from "react";

import { DataState } from "@/components/state/DataState";
import { Card } from "@/components/ui/Card";
import { ApiError } from "@/lib/api";
import {
  ERROR_GUIDANCE,
  REFETCH_CODES,
  analyzeSession,
  applyPlan,
  approvePlan,
  cancelSession,
  counterLabel,
  manifestDraftPath,
  requestGitOps,
  shortSha,
  type ApplyResult,
  type OnboardingSession,
  type Plan,
} from "@/lib/onboarding";

type Pending = null | "analyze" | "approve" | "apply" | "cancel" | "gitops";
type Confirming = null | "approve" | "apply" | "cancel";

export interface SessionActionsProps {
  session: OnboardingSession;
  plan: Plan | null;
  csrfToken: string;
  gitopsEnabled: boolean;
  /** Refetch session, findings and plan together. */
  onChanged: () => void;
  /**
   * The apply result and the idempotency key live ABOVE this component.
   *
   * They have to. A refetch puts the session back into its loading state,
   * which unmounts this panel — so a result held here would vanish the
   * moment the apply that produced it refreshed the page, and the key held
   * here would be regenerated, making the next retry a NEW operation. Both
   * are owned by the screen, which outlives the reload.
   */
  result: ApplyResult | null;
  onResult: (result: ApplyResult) => void;
  applyKey: MutableRefObject<{ version: number; key: string } | null>;
}

/** States each action may run from. The server holds the same table. */
const ANALYZE_STATES = new Set([
  "draft",
  "discovery_pending",
  "needs_review",
  "ready",
  "provider_unavailable",
  "stale",
  "failed",
]);
const CANCEL_STATES = new Set([
  "draft",
  "discovery_pending",
  "needs_review",
  "ready",
  "approved",
  "provider_unavailable",
  "stale",
  "failed",
]);
const GITOPS_STATES = new Set(["needs_review", "ready", "approved"]);

function message(error: unknown): { text: string; correlationId?: string; code: string } {
  if (error instanceof ApiError) {
    return {
      // Drake's own words for a code Drake defined; the server's message is
      // also Drake's, so the fallback leaks nothing either.
      text: ERROR_GUIDANCE[error.code] ?? error.message,
      correlationId: error.correlationId,
      code: error.code,
    };
  }
  return { text: "The request could not be sent. Nothing was changed.", code: "network" };
}

export function SessionActions({
  session,
  plan,
  csrfToken,
  gitopsEnabled,
  onChanged,
  result,
  onResult,
  applyKey,
}: SessionActionsProps) {
  const [pending, setPending] = useState<Pending>(null);
  const [confirming, setConfirming] = useState<Confirming>(null);
  const [failure, setFailure] = useState<ReturnType<typeof message> | null>(null);

  /**
   * One idempotency key per (session, plan version).
   *
   * A retry after a timeout MUST reuse it: a new key describes a new
   * operation, so an apply that committed before the connection dropped
   * would be applied a second time. It is replaced when the plan version
   * changes, because a different plan is a different request and carrying
   * the key over would replay the old plan's recorded answer.
   */
  const keyFor = useCallback(
    (version: number) => {
      if (applyKey.current?.version !== version) {
        applyKey.current = { version, key: crypto.randomUUID() };
      }
      return applyKey.current.key;
    },
    [applyKey],
  );

  const run = async (action: Exclude<Pending, null>, work: () => Promise<void>) => {
    setPending(action);
    setFailure(null);
    try {
      await work();
      setConfirming(null);
    } catch (error) {
      const described = message(error);
      setFailure(described);
      // A stale client is refetched rather than left showing buttons for a
      // session that has moved on. The confirmation closes with it: it was
      // asking about a state that no longer exists.
      if (REFETCH_CODES.has(described.code)) {
        setConfirming(null);
        onChanged();
      }
    } finally {
      setPending(null);
    }
  };

  const busy = pending !== null;
  const planVersion = plan?.plan_version ?? null;
  const approvable =
    session.state === "ready" &&
    plan !== null &&
    plan.applicable &&
    plan.blocking_items === 0 &&
    session.can_manage;
  const appliable =
    session.state === "approved" &&
    session.can_apply &&
    session.approved_plan_version !== null;

  return (
    <Card title="Actions">
      <div className="space-y-3" data-testid="session-actions">
        {failure ? (
          <div data-testid="action-error">
            <DataState
              kind={failure.code === "network" ? "error" : "stale"}
              title="That did not happen"
              description={
                failure.correlationId
                  ? `${failure.text} (reference ${failure.correlationId})`
                  : failure.text
              }
            />
          </div>
        ) : null}

        <div className="flex flex-wrap gap-2">
          {ANALYZE_STATES.has(session.state) && session.can_manage ? (
            <button
              type="button"
              data-testid="action-analyze"
              disabled={busy}
              onClick={() =>
                run("analyze", async () => {
                  await analyzeSession(csrfToken, session.id);
                  onChanged();
                })
              }
              className="rounded-md border border-border px-3 py-1.5 text-xs font-medium text-ink hover:bg-surface-hover disabled:cursor-not-allowed disabled:opacity-50"
            >
              {pending === "analyze"
                ? "Analysing…"
                : session.plan
                  ? "Analyse again"
                  : "Analyse repository"}
            </button>
          ) : null}

          {session.state === "ready" && session.can_manage ? (
            <button
              type="button"
              data-testid="action-approve"
              disabled={busy || !approvable}
              onClick={() => setConfirming("approve")}
              className="rounded-md border border-border px-3 py-1.5 text-xs font-medium text-ink hover:bg-surface-hover disabled:cursor-not-allowed disabled:opacity-50"
            >
              Approve plan
            </button>
          ) : null}

          {session.state === "approved" && session.can_apply ? (
            <button
              type="button"
              data-testid="action-apply"
              disabled={busy || !appliable}
              onClick={() => setConfirming("apply")}
              className="rounded-md border border-accent bg-accent-soft px-3 py-1.5 text-xs font-medium text-ink hover:bg-surface-hover disabled:cursor-not-allowed disabled:opacity-50"
            >
              {pending === "apply" ? "Applying…" : "Apply approved plan"}
            </button>
          ) : null}

          {CANCEL_STATES.has(session.state) && session.can_manage ? (
            <button
              type="button"
              data-testid="action-cancel"
              disabled={busy}
              onClick={() => setConfirming("cancel")}
              className="rounded-md border border-border px-3 py-1.5 text-xs font-medium text-ink-secondary hover:bg-surface-hover disabled:cursor-not-allowed disabled:opacity-50"
            >
              Cancel session
            </button>
          ) : null}

          {GITOPS_STATES.has(session.state) && session.can_gitops ? (
            <button
              type="button"
              data-testid="action-gitops"
              disabled={busy || !gitopsEnabled}
              // The request is not sent while the flag is off. A disabled
              // button that still fires would make the flag a suggestion.
              onClick={() =>
                gitopsEnabled
                  ? run("gitops", async () => {
                      await requestGitOps(csrfToken, session.id);
                      onChanged();
                    })
                  : undefined
              }
              className="rounded-md border border-border px-3 py-1.5 text-xs font-medium text-ink hover:bg-surface-hover disabled:cursor-not-allowed disabled:opacity-50"
            >
              Propose manifest pull request
            </button>
          ) : null}

          {session.analyzed_commit_sha ? (
            <a
              data-testid="action-manifest-draft"
              href={manifestDraftPath(session.id)}
              download
              className="rounded-md border border-border px-3 py-1.5 text-xs font-medium text-ink hover:bg-surface-hover"
            >
              Download manifest draft
            </a>
          ) : null}
        </div>

        {GITOPS_STATES.has(session.state) && session.can_gitops && !gitopsEnabled ? (
          <p className="text-xs text-ink-muted" data-testid="gitops-disabled">
            Repository writes are disabled. No branch or pull request will be created.
          </p>
        ) : null}

        {session.state === "ready" && session.can_manage && !approvable ? (
          <p className="text-xs text-warning" data-testid="approve-blocked">
            {plan && plan.blocking_items > 0
              ? `Approval is blocked: ${plan.blocking_items} item(s) need a decision.`
              : "There is no applicable plan to approve yet."}
          </p>
        ) : null}

        {confirming === "approve" && plan && planVersion !== null ? (
          <Confirm
            testId="confirm-approve"
            title="Approve this plan?"
            busy={busy}
            confirmLabel="Approve"
            onCancel={() => setConfirming(null)}
            onConfirm={() =>
              run("approve", async () => {
                await approvePlan(csrfToken, session.id, planVersion, session.version);
                onChanged();
              })
            }
          >
            <dl className="space-y-1">
              <Fact label="Plan version">v{planVersion}</Fact>
              <Fact label="Commit">{shortSha(plan.commit_sha)}</Fact>
              <Fact label="Digest">{shortSha(plan.plan_digest)}</Fact>
              <Fact label="Items">{String(plan.total_items)}</Fact>
            </dl>
            <p className="mt-2 text-xs text-ink-secondary">
              Approving records that you accept these exact values. It changes nothing on its
              own.
            </p>
          </Confirm>
        ) : null}

        {confirming === "apply" && session.approved_plan_version !== null ? (
          <Confirm
            testId="confirm-apply"
            title="Apply the approved plan?"
            busy={busy}
            confirmLabel="Apply"
            onCancel={() => setConfirming(null)}
            onConfirm={() =>
              run("apply", async () => {
                const version = session.approved_plan_version as number;
                const applied = await applyPlan(
                  csrfToken,
                  session.id,
                  version,
                  keyFor(version),
                );
                onResult(applied);
                onChanged();
              })
            }
          >
            <dl className="space-y-1">
              <Fact label="Approved version">v{session.approved_plan_version}</Fact>
            </dl>
            <p className="mt-2 text-xs text-ink-secondary">
              This writes the approved plan to Drake&apos;s catalog. It does not write to the
              repository.
            </p>
          </Confirm>
        ) : null}

        {confirming === "cancel" ? (
          <Confirm
            testId="confirm-cancel"
            title="Cancel this session?"
            busy={busy}
            confirmLabel="Cancel session"
            onCancel={() => setConfirming(null)}
            onConfirm={() =>
              run("cancel", async () => {
                await cancelSession(csrfToken, session.id, session.version);
                onChanged();
              })
            }
          >
            <p className="text-xs text-ink-secondary">
              The session closes and its plan is no longer applicable. Nothing is removed from
              the catalog — cancelling a session does not undo anything already applied.
            </p>
          </Confirm>
        ) : null}

        {result ? <ApplyResultCard result={result} projectId={session.imported_project_id} /> : null}
      </div>
    </Card>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex min-w-0 justify-between gap-3 text-xs">
      <dt className="shrink-0 text-ink-muted">{label}</dt>
      {/* A digest or a UUID is one unbreakable word; let it break rather
          than push the page sideways. */}
      <dd className="font-mono break-all text-ink">{children}</dd>
    </div>
  );
}

function Confirm({
  testId,
  title,
  children,
  busy,
  confirmLabel,
  onConfirm,
  onCancel,
}: {
  testId: string;
  title: string;
  children: React.ReactNode;
  busy: boolean;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div
      role="group"
      aria-label={title}
      data-testid={testId}
      className="rounded-md border border-border bg-surface-subtle p-3"
    >
      <p className="text-xs font-medium text-ink">{title}</p>
      <div className="mt-2">{children}</div>
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          data-testid={`${testId}-yes`}
          disabled={busy}
          onClick={onConfirm}
          className="rounded-md border border-accent px-3 py-1.5 text-xs font-medium text-ink disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? "Working…" : confirmLabel}
        </button>
        <button
          type="button"
          data-testid={`${testId}-no`}
          disabled={busy}
          onClick={onCancel}
          className="rounded-md border border-border px-3 py-1.5 text-xs text-ink-secondary disabled:cursor-not-allowed disabled:opacity-50"
        >
          Back
        </button>
      </div>
    </div>
  );
}

const COUNTERS: { key: keyof ApplyResult; label: string }[] = [
  { key: "created_entities", label: "Created" },
  { key: "linked_entities", label: "Linked" },
  { key: "unchanged_entities", label: "Unchanged" },
  { key: "metadata_updated", label: "Metadata updated" },
  { key: "slo_definitions_created", label: "SLOs created" },
  { key: "slo_definitions_updated", label: "SLOs updated" },
  { key: "bindings_created", label: "Bindings created" },
];

export function ApplyResultCard({
  result,
  projectId,
}: {
  result: ApplyResult;
  projectId: string | null;
}) {
  return (
    <div
      className="rounded-md border border-border bg-surface-subtle p-3"
      data-testid="apply-result"
    >
      <p className="text-xs font-medium text-ink">Applied to the catalog</p>
      <dl className="mt-2 grid gap-1 sm:grid-cols-2">
        {COUNTERS.map(({ key, label }) => (
          <Fact key={key} label={label}>
            {/* `null` is "the receipt never recorded this", not zero. */}
            {counterLabel(result[key] as number | null)}
          </Fact>
        ))}
      </dl>
      {result.project_id ?? projectId ? (
        <a
          href={`/projects/${result.project_id ?? projectId}`}
          data-testid="apply-result-project"
          className="mt-3 inline-block text-xs text-ink hover:underline"
        >
          Open the catalog project →
        </a>
      ) : null}
    </div>
  );
}
