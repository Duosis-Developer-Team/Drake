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
 * It searches and pages, and both matter rather than being polish: an
 * installation with more repositories than one page would otherwise make
 * everything past the first page unreachable, and a `?repository_id=` link
 * would work or not depending on where the target happened to sort.
 *
 * Nothing is created by arriving. A `?repository_id=` preselects and stops;
 * a link somebody was sent must not open a session on their behalf.
 */

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useId, useRef, useState } from "react";

import { DataState } from "@/components/state/DataState";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { ApiError } from "@/lib/api";
import { useT } from "@/lib/i18n";
import {
  CANDIDATE_BLOCKERS,
  ERROR_GUIDANCE,
  createSession,
  fetchRepositoryCandidate,
  fetchRepositoryCandidates,
  type RepositoryCandidate,
} from "@/lib/onboarding";

const PAGE_SIZE = 25;

type Listing =
  | { state: "loading" }
  | { state: "error"; message: string; denied: boolean }
  | { state: "ready"; items: RepositoryCandidate[]; nextCursor: string | null };

export function StartOnboarding({
  csrfToken,
  canManage,
}: {
  csrfToken: string;
  canManage: boolean;
}) {
  const t = useT("onboarding");
  const router = useRouter();
  const params = useSearchParams();
  const requested = params.get("repository_id");
  const listId = useId();
  const inputId = useId();

  const [search, setSearch] = useState("");
  const [listing, setListing] = useState<Listing>({ state: "loading" });
  const [selected, setSelected] = useState<RepositoryCandidate | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [starting, setStarting] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [preselect, setPreselect] = useState<"idle" | "loading" | "denied">("idle");

  // The search term the in-flight request was issued for. A slower earlier
  // response must not overwrite a newer one — otherwise typing quickly
  // leaves the list showing results for a prefix the operator has moved on
  // from, which looks like the search is broken.
  const issued = useRef(0);

  const load = useCallback(async (term: string) => {
    const ticket = ++issued.current;
    setListing({ state: "loading" });
    try {
      const page = await fetchRepositoryCandidates({
        search: term || undefined,
        limit: PAGE_SIZE,
      });
      if (ticket !== issued.current) return;
      setListing({ state: "ready", items: page.items, nextCursor: page.next_cursor });
    } catch (error) {
      if (ticket !== issued.current) return;
      const denied = error instanceof ApiError && (error.status === 404 || error.status === 403);
      setListing({
        state: "error",
        message: error instanceof ApiError ? error.message : t("start.listFailed"),
        denied,
      });
    }
  }, [t]);

  const loadMore = useCallback(async () => {
    if (listing.state !== "ready" || !listing.nextCursor) return;
    setLoadingMore(true);
    try {
      const page = await fetchRepositoryCandidates({
        search: search || undefined,
        limit: PAGE_SIZE,
        cursor: listing.nextCursor,
      });
      setListing((current) =>
        current.state === "ready"
          ? { ...current, items: [...current.items, ...page.items], nextCursor: page.next_cursor }
          : current,
      );
    } catch (error) {
      setFailure(
        error instanceof ApiError ? error.message : t("start.nextPageFailed"),
      );
    } finally {
      setLoadingMore(false);
    }
  }, [listing, search, t]);

  useEffect(() => {
    if (!canManage) {
      setListing({ state: "ready", items: [], nextCursor: null });
      return;
    }
    const timer = setTimeout(() => void load(search), search ? 200 : 0);
    return () => clearTimeout(timer);
  }, [canManage, load, search]);

  // A `?repository_id=` target is fetched BY ID rather than looked for in
  // the first page. It may sort anywhere, and a preselection that works
  // only for repositories near the top of the alphabet is a coincidence,
  // not a feature.
  useEffect(() => {
    if (!canManage || !requested || selected?.id === requested) return;
    let cancelled = false;
    setPreselect("loading");
    fetchRepositoryCandidate(requested)
      .then((candidate) => {
        if (cancelled) return;
        setSelected(candidate);
        setPreselect("idle");
      })
      .catch(() => {
        if (cancelled) return;
        // 404 covers "no such repository" and "not in a scope you may act
        // in" alike, so this says neither. Naming which would answer the
        // question the scoping exists to refuse.
        setPreselect("denied");
      });
    return () => {
      cancelled = true;
    };
  }, [canManage, requested, selected?.id]);

  if (!canManage) {
    return (
      <Panel>
        <PanelHeader title={t("start.title")} />
        <div data-testid="start-permission-denied">
          <DataState
            kind="permission-denied"
            title={t("start.denied.title")}
            description={t("start.denied.description")}
          />
        </div>
      </Panel>
    );
  }

  const start = async () => {
    if (!selected) return;
    // A repository with a session already open sends the operator there
    // rather than opening a second one beside it.
    if (selected.active_session_id) {
      router.push(`/onboarding/${selected.active_session_id}`);
      return;
    }
    setStarting(true);
    setFailure(null);
    try {
      const created = await createSession(csrfToken, selected.id);
      router.push(`/onboarding/${created.session_id}`);
    } catch (error) {
      // Drake's own words for a code Drake defined, in the reader's language;
      // the server's message is also Drake's, so the fallback leaks nothing.
      setFailure(
        error instanceof ApiError
          ? t.has(`errorGuidance.${error.code}`)
            ? t.dyn("errorGuidance", error.code, ERROR_GUIDANCE[error.code])
            : t.has(`candidateBlocker.${error.code}`)
              ? t.dyn("candidateBlocker", error.code, CANDIDATE_BLOCKERS[error.code])
              : error.message
          : t("start.startFailed"),
      );
      setStarting(false);
    }
  };

  const items = listing.state === "ready" ? listing.items : [];
  const startDisabled =
    !selected || starting || (!selected.startable && !selected.active_session_id);

  return (
    <Panel>
      <PanelHeader title={t("start.title")} description={t("start.description")} />
      <div className="space-y-4" data-testid="start-onboarding">
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-0 flex-1">
            <label htmlFor={inputId} className="block text-caption text-ink-muted">
              {t("start.repository")}
            </label>
            {/*
              A native combobox: typing filters on the SERVER, and the
              datalist offers what came back. It is reachable, operable and
              announced without a custom widget reimplementing all three.
            */}
            <input
              id={inputId}
              list={listId}
              type="text"
              role="combobox"
              aria-expanded={items.length > 0}
              aria-controls={listId}
              autoComplete="off"
              data-testid="repository-search"
              placeholder={t("start.searchPlaceholder")}
              value={search}
              onChange={(event) => {
                const value = event.target.value;
                setSearch(value);
                setFailure(null);
                // A datalist reports a pick as an ordinary change whose
                // value is the option's value — the repository id.
                const picked = items.find((item) => item.id === value);
                if (picked) setSelected(picked);
              }}
              className="mt-1 w-full rounded-full border border-border bg-surface px-4 py-2 text-body text-ink placeholder:text-ink-muted"
            />
            <datalist id={listId} data-testid="repository-options">
              {items.map((item) => (
                <option key={item.id} value={item.id} label={item.full_name}>
                  {item.full_name}
                  {item.startable ? "" : t("start.unavailableSuffix")}
                </option>
              ))}
            </datalist>
          </div>
          <button
            type="button"
            data-testid="start-onboarding-button"
            disabled={startDisabled}
            onClick={start}
            className="rounded-full border border-accent bg-accent-soft px-4 py-2 text-body font-medium text-ink hover:bg-surface-hover disabled:cursor-not-allowed disabled:opacity-50"
          >
            {selected?.active_session_id
              ? t("start.openExisting")
              : starting
                ? t("start.starting")
                : t("start.start")}
          </button>
        </div>

        {/* An explicit list as well as the datalist: a datalist is a
            suggestion surface, not a control every browser exposes to the
            keyboard the same way. */}
        {listing.state === "loading" ? (
          <DataState kind="loading" />
        ) : listing.state === "error" ? (
          listing.denied ? (
            <div data-testid="start-permission-denied">
              <DataState kind="permission-denied" />
            </div>
          ) : (
            <DataState kind="error" description={listing.message} onRetry={() => void load(search)} />
          )
        ) : items.length === 0 ? (
          <div data-testid={search ? "start-no-matches" : "start-empty"}>
            <DataState
              kind="empty"
              title={search ? t("start.noMatches.title") : t("start.empty.title")}
              description={
                search ? t("start.noMatches.description") : t("start.empty.description")
              }
            />
          </div>
        ) : (
          <ul className="max-h-64 space-y-1.5 overflow-y-auto" data-testid="repository-list">
            {items.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  data-testid={`repository-option-${item.id}`}
                  aria-pressed={selected?.id === item.id}
                  onClick={() => {
                    setSelected(item);
                    setFailure(null);
                  }}
                  className={`flex w-full min-w-0 items-baseline justify-between gap-3 rounded-full border px-4 py-2 text-left text-caption ${
                    selected?.id === item.id
                      ? "border-accent bg-accent-soft"
                      : "border-border hover:bg-surface-hover"
                  }`}
                >
                  <span className="truncate text-ink">{item.full_name}</span>
                  {item.startable ? null : (
                    <span className="shrink-0 text-micro text-warning">
                      {t("start.unavailable")}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        )}

        {listing.state === "ready" && listing.nextCursor ? (
          <button
            type="button"
            data-testid="repository-load-more"
            disabled={loadingMore}
            onClick={() => void loadMore()}
            className="rounded-full border border-border px-3 py-1.5 text-caption text-ink-secondary hover:bg-surface-hover disabled:opacity-50"
          >
            {loadingMore ? t("start.loadingMore") : t("start.loadMore")}
          </button>
        ) : listing.state === "ready" && items.length > 0 ? (
          <p className="text-[11px] text-ink-muted" data-testid="repository-list-complete">
            {t("start.complete")}
          </p>
        ) : null}

        {preselect === "loading" ? (
          <p className="text-xs text-ink-muted" data-testid="preselect-loading">
            {t("start.preselectLoading")}
          </p>
        ) : null}
        {preselect === "denied" ? (
          <p className="text-xs text-warning" data-testid="preselect-denied">
            {t("start.preselectDenied")}
          </p>
        ) : null}

        {selected ? (
          <p className="text-xs text-ink-secondary" data-testid="repository-selected">
            {t("start.selected")} <span className="font-mono break-all">{selected.full_name}</span>
          </p>
        ) : null}

        {selected && !selected.startable ? (
          <p className="text-xs text-warning" data-testid="repository-blocked">
            {/* Said in words, not only by the button being grey. */}
            {t.dyn(
              "candidateBlocker",
              selected.reason_code,
              CANDIDATE_BLOCKERS[selected.reason_code ?? ""] ?? t("start.cannotStart"),
            )}
          </p>
        ) : null}

        {failure ? (
          <p className="text-xs text-critical" data-testid="start-error">
            {failure}
          </p>
        ) : null}

        <p className="text-xs text-ink-muted">
          {t("start.readsNothing")}
        </p>
      </div>
    </Panel>
  );
}
