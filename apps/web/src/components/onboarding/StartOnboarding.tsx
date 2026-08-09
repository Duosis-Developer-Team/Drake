"use client";

/**
 * Choosing a repository and opening a session on it.
 *
 * The list comes from `/v1/onboarding/repositories`, which is scoped to
 * `onboarding.manage` — the permission the button here actually needs.
 * Pointing this at the integration repository list would have been easier
 * and would have shown repositories whose Start button then 404s, because
 * read access and act access are different sets.
 *
 * Nothing is created by arriving. A `?repository_id=` in the URL preselects
 * a repository and stops there; a link somebody was sent must not open a
 * session on their behalf.
 */

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useId, useState } from "react";

import { DataState } from "@/components/state/DataState";
import { Card } from "@/components/ui/Card";
import { ApiError } from "@/lib/api";
import {
  CANDIDATE_BLOCKERS,
  ERROR_GUIDANCE,
  createSession,
  fetchRepositoryCandidates,
  type RepositoryCandidate,
} from "@/lib/onboarding";

type Listing =
  | { state: "loading" }
  | { state: "error"; message: string; denied: boolean }
  | { state: "ready"; items: RepositoryCandidate[] };

export function StartOnboarding({
  csrfToken,
  canManage,
}: {
  csrfToken: string;
  canManage: boolean;
}) {
  const router = useRouter();
  const params = useSearchParams();
  const selectId = useId();
  const [listing, setListing] = useState<Listing>({ state: "loading" });
  const [selected, setSelected] = useState<string>(params.get("repository_id") ?? "");
  const [starting, setStarting] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const load = useCallback(async () => {
    setListing({ state: "loading" });
    try {
      // The API caps a page at 50 and refuses more; asking for 100 was a
      // 422 that rendered as "Query failed" with no way to tell that the
      // client had asked for something impossible.
      const page = await fetchRepositoryCandidates({ limit: 50 });
      setListing({ state: "ready", items: page.items });
    } catch (error) {
      const denied = error instanceof ApiError && (error.status === 404 || error.status === 403);
      setListing({
        state: "error",
        message: error instanceof ApiError ? error.message : "The list could not be loaded.",
        denied,
      });
    }
  }, []);

  useEffect(() => {
    if (canManage) void load();
    else setListing({ state: "ready", items: [] });
  }, [canManage, load]);

  if (!canManage) {
    return (
      <Card title="Start an onboarding">
        <div data-testid="start-permission-denied">
          <DataState
            kind="permission-denied"
            title="You cannot start an onboarding"
            description="Starting one needs the onboarding manage permission on the scope the repository belongs to. You can still review sessions you have access to."
          />
        </div>
      </Card>
    );
  }

  const chosen =
    listing.state === "ready"
      ? (listing.items.find((item) => item.id === selected) ?? null)
      : null;

  const start = async () => {
    if (!chosen) return;
    // A repository with a session already open sends the operator there
    // rather than opening a second one beside it.
    if (chosen.active_session_id) {
      router.push(`/onboarding/${chosen.active_session_id}`);
      return;
    }
    setStarting(true);
    setFailure(null);
    try {
      const created = await createSession(csrfToken, chosen.id);
      router.push(`/onboarding/${created.session_id}`);
    } catch (error) {
      setFailure(
        error instanceof ApiError
          ? (ERROR_GUIDANCE[error.code] ?? error.message)
          : "The session could not be started. Nothing was changed.",
      );
      setStarting(false);
    }
  };

  return (
    <Card title="Start an onboarding">
      <div className="space-y-3" data-testid="start-onboarding">
        {listing.state === "loading" ? (
          <DataState kind="loading" />
        ) : listing.state === "error" ? (
          listing.denied ? (
            <div data-testid="start-permission-denied">
              <DataState kind="permission-denied" />
            </div>
          ) : (
            <DataState kind="error" description={listing.message} onRetry={load} />
          )
        ) : listing.items.length === 0 ? (
          <div data-testid="start-empty">
            <DataState
              kind="empty"
              title="No repositories you can onboard"
              description="Drake projects no repository in a scope where you hold the onboarding manage permission. This is not a statement about which repositories exist."
            />
          </div>
        ) : (
          <>
            <div className="flex flex-wrap items-end gap-3">
              <div className="min-w-0 flex-1">
                <label htmlFor={selectId} className="block text-xs text-ink-muted">
                  Repository
                </label>
                <select
                  id={selectId}
                  data-testid="repository-select"
                  value={selected}
                  onChange={(event) => {
                    setSelected(event.target.value);
                    setFailure(null);
                  }}
                  className="mt-1 w-full rounded-md border border-border bg-surface px-2.5 py-1.5 text-sm text-ink"
                >
                  <option value="">Choose a repository…</option>
                  {listing.items.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.full_name}
                      {item.startable ? "" : " — unavailable"}
                    </option>
                  ))}
                </select>
              </div>
              <button
                type="button"
                data-testid="start-onboarding-button"
                disabled={!chosen || starting || (!chosen.startable && !chosen.active_session_id)}
                onClick={start}
                className="rounded-md border border-accent bg-accent-soft px-3 py-1.5 text-sm font-medium text-ink hover:bg-surface-hover disabled:cursor-not-allowed disabled:opacity-50"
              >
                {chosen?.active_session_id
                  ? "Open existing session"
                  : starting
                    ? "Starting…"
                    : "Start onboarding"}
              </button>
            </div>

            {chosen && !chosen.startable ? (
              <p className="text-xs text-warning" data-testid="repository-blocked">
                {/* Said in words, not only by the button being grey. */}
                {CANDIDATE_BLOCKERS[chosen.reason_code ?? ""] ??
                  "This repository cannot be onboarded right now."}
              </p>
            ) : null}

            {failure ? (
              <p className="text-xs text-critical" data-testid="start-error">
                {failure}
              </p>
            ) : null}

            <p className="text-xs text-ink-muted">
              Starting a session reads nothing yet. The analysis is a separate, explicit step.
            </p>
          </>
        )}
      </div>
    </Card>
  );
}
