"use client";

/**
 * Global command palette: Cmd/Ctrl+K (or /) dialog, keyboard navigation,
 * screen-reader announcements.
 *
 * Phase 1 of the 2026-09-13 remake brief's two-phase palette (§8.1): static
 * page/action search merges with the existing authorized catalog search.
 * Incident/alert/deployment/SLO indexing waits on a backend search contract
 * that does not exist yet — that is phase 2, not this component.
 *
 * Page results come from `NAV_ITEMS`, filtered through the same
 * `hasPermission` check `Sidebar` uses, so the palette never offers a
 * destination the rail itself would hide. Catalog results still come
 * exclusively from the authorized `/v1/catalog/search` endpoint.
 */

import { Clock, Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, apiGet } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { SearchResult } from "@/lib/catalog";
import { NAV_ITEMS, type NavItem } from "@/lib/navigation";
import { useSession } from "@/lib/session";

const RECENT_KEY = "drake-recent-pages";
const RECENT_LIMIT = 5;

interface RecentEntry {
  href: string;
  label: string;
}

type SearchState =
  | { phase: "idle" }
  | { phase: "loading" }
  | { phase: "error"; message: string }
  | { phase: "ready"; results: SearchResult[] };

type PaletteResult =
  | { source: "recent"; entry: RecentEntry }
  | { source: "page"; label: string; href: string; icon: NavItem["icon"] }
  | { source: "catalog"; result: SearchResult };

function resultHref(result: SearchResult): string {
  switch (result.kind) {
    case "project":
      return `/projects/${result.id}`;
    case "environment":
      return `/projects/${result.project_id}/environments/${result.id}`;
    case "service":
      return `/projects/${result.project_id}/environments/${result.parent_id}/services/${result.id}`;
    case "cluster":
      return `/clusters/${result.id}`;
  }
}

function paletteHref(result: PaletteResult): string {
  switch (result.source) {
    case "recent":
      return result.entry.href;
    case "page":
      return result.href;
    case "catalog":
      return resultHref(result.result);
  }
}

function paletteLabel(result: PaletteResult): string {
  switch (result.source) {
    case "recent":
      return result.entry.label;
    case "page":
      return result.label;
    case "catalog":
      return result.result.display_name || result.result.key;
  }
}

/**
 * Recently opened destinations, most recent first.
 *
 * `sessionStorage`, not the server: this is a per-tab convenience, not a
 * record of anything Drake reports on, so it never needs to survive a
 * browser restart or sync across devices.
 */
function readRecent(): RecentEntry[] {
  try {
    const raw = sessionStorage.getItem(RECENT_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(
        (entry): entry is RecentEntry =>
          typeof entry === "object" &&
          entry !== null &&
          typeof (entry as RecentEntry).href === "string" &&
          typeof (entry as RecentEntry).label === "string",
      )
      .slice(0, RECENT_LIMIT);
  } catch {
    return [];
  }
}

function pushRecent(entry: RecentEntry): void {
  try {
    const next = [entry, ...readRecent().filter((existing) => existing.href !== entry.href)].slice(
      0,
      RECENT_LIMIT,
    );
    sessionStorage.setItem(RECENT_KEY, JSON.stringify(next));
  } catch {
    // Best effort; a lost recents list breaks nothing else.
  }
}

export function CatalogSearch() {
  const router = useRouter();
  const { hasPermission } = useSession();
  const t = useT("shell");
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [state, setState] = useState<SearchState>({ phase: "idle" });
  const [activeIndex, setActiveIndex] = useState(0);
  const [recent, setRecent] = useState<RecentEntry[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const openerRef = useRef<HTMLButtonElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const pages = useMemo(
    () =>
      NAV_ITEMS.filter(
        (item) => !item.anyPermission || item.anyPermission.some((permission) => hasPermission(permission)),
      ),
    [hasPermission],
  );

  const close = useCallback(() => {
    setOpen(false);
    setQuery("");
    setState({ phase: "idle" });
    setActiveIndex(0);
    openerRef.current?.focus();
  }, []);

  const navigate = useCallback(
    (result: PaletteResult) => {
      const href = paletteHref(result);
      if (result.source !== "recent") pushRecent({ href, label: paletteLabel(result) });
      router.push(href);
      close();
    },
    [router, close],
  );

  useEffect(() => {
    if (open) setRecent(readRecent());
  }, [open]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const inField =
        target && ("value" in target || target.isContentEditable === true);
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen(true);
      } else if (event.key === "/" && !inField && !open) {
        event.preventDefault();
        setOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  useEffect(() => {
    setActiveIndex(0);
  }, [query]);

  useEffect(() => {
    abortRef.current?.abort();
    if (query.trim().length < 2) {
      setState({ phase: "idle" });
      return;
    }
    const controller = new AbortController();
    abortRef.current = controller;
    setState({ phase: "loading" });
    const timer = setTimeout(() => {
      apiGet<{ results: SearchResult[] }>(
        `/v1/catalog/search?q=${encodeURIComponent(query.trim())}`,
      )
        .then((body) => {
          if (!controller.signal.aborted) {
            setState({ phase: "ready", results: body.results });
          }
        })
        .catch((error: unknown) => {
          if (!controller.signal.aborted) {
            setState({
              phase: "error",
              message: error instanceof ApiError ? error.message : t("search.failed"),
            });
          }
        });
    }, 250);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
    // `t` changes only with the locale; a locale switch mid-search restarting
    // the request is the right behaviour.
  }, [query, t]);

  const trimmed = query.trim();
  const pageMatches: PaletteResult[] = trimmed
    ? pages
        .map((item) => ({ source: "page" as const, label: t.dyn("nav", item.key, item.label), href: item.href, icon: item.icon }))
        .filter((item) => item.label.toLowerCase().includes(trimmed.toLowerCase()))
    : [];
  const catalogMatches: PaletteResult[] =
    state.phase === "ready" ? state.results.map((result) => ({ source: "catalog" as const, result })) : [];
  const recentResults: PaletteResult[] = recent.map((entry) => ({ source: "recent" as const, entry }));

  const results: PaletteResult[] = trimmed ? [...pageMatches, ...catalogMatches] : recentResults;
  const catalogAttempted = trimmed.length >= 2;
  const showNoResults =
    trimmed.length > 0 &&
    pageMatches.length === 0 &&
    (catalogAttempted ? state.phase === "ready" && catalogMatches.length === 0 : true);

  const onDialogKey = (event: React.KeyboardEvent) => {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((index) => Math.min(index + 1, results.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((index) => Math.max(index - 1, 0));
    } else if (event.key === "Enter" && results[activeIndex]) {
      event.preventDefault();
      navigate(results[activeIndex]);
    }
  };

  let cursor = 0;
  const renderOption = (result: PaletteResult) => {
    const index = cursor;
    cursor += 1;
    const key =
      result.source === "catalog" ? `catalog-${result.result.kind}-${result.result.id}` : paletteHref(result);
    return (
      <button
        key={key}
        type="button"
        role="option"
        aria-selected={index === activeIndex}
        onMouseEnter={() => setActiveIndex(index)}
        onClick={() => navigate(result)}
        className={`flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm ${
          index === activeIndex ? "bg-accent-soft text-accent-ink" : "text-ink hover:bg-surface-sunken"
        }`}
      >
        {result.source === "recent" ? (
          <Clock className="h-3.5 w-3.5 shrink-0 text-ink-muted" aria-hidden />
        ) : (
          <span className="rounded border border-border px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-ink-muted">
            {result.source === "page" ? t("search.page") : t.dyn("search.kind", result.result.kind)}
          </span>
        )}
        <span className="min-w-0 flex-1 truncate">{paletteLabel(result)}</span>
        {result.source === "catalog" && result.result.project_key && result.result.kind !== "project" ? (
          <span className="font-mono text-[11px] text-ink-muted">{result.result.project_key}</span>
        ) : null}
      </button>
    );
  };

  return (
    <>
      <button
        ref={openerRef}
        type="button"
        onClick={() => setOpen(true)}
        className="hidden h-11 w-72 items-center gap-2.5 rounded-full border border-border bg-surface pr-1.5 pl-4 text-sm text-ink-muted transition-colors hover:text-ink md:flex"
        aria-label={t("search.open")}
      >
        <Search className="h-4 w-4" aria-hidden />
        <span className="flex-1 text-left">{t("search.placeholderShort")}</span>
        <kbd className="rounded-full bg-surface-3 px-2.5 py-1 text-[11px] font-medium text-ink-secondary">⌘K</kbd>
      </button>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={t("search.open")}
        className="inline-flex h-11 w-11 items-center justify-center rounded-full border border-border bg-surface text-ink-secondary hover:bg-surface-hover md:hidden"
      >
        <Search className="h-4 w-4" aria-hidden />
      </button>

      {open ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={t("search.open")}
          className="fixed inset-0 z-50 flex items-start justify-center p-4 pt-[12vh]"
          onKeyDown={onDialogKey}
        >
          <button
            type="button"
            aria-label={t("search.close")}
            onClick={close}
            className="absolute inset-0 bg-[var(--scrim)] motion-safe:animate-[fade-in_140ms_ease-out]"
            tabIndex={-1}
          />
          <div className="relative w-full max-w-lg rounded-overlay border border-border bg-surface shadow-overlay motion-safe:animate-[scale-in_180ms_var(--ease-entrance)]">
            <div className="flex items-center gap-2 border-b border-border px-4">
              <Search className="h-4 w-4 text-ink-muted" aria-hidden />
              <input
                ref={inputRef}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={t("search.placeholder")}
                aria-label={t("search.query")}
                className="h-12 flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-ink-muted"
              />
              <kbd className="rounded border border-border px-1.5 text-[10px] text-ink-muted">
                esc
              </kbd>
            </div>
            <div
              role="listbox"
              aria-label={t("search.results")}
              className="max-h-80 overflow-y-auto p-2"
            >
              {!trimmed && recentResults.length === 0 ? (
                <p className="px-3 py-6 text-center text-sm text-ink-muted">
                  {t("search.hint")}
                </p>
              ) : null}

              {!trimmed && recentResults.length > 0 ? (
                <div data-testid="palette-group-recent">
                  <p aria-hidden className="px-3 pt-1 pb-1 text-[11px] uppercase tracking-wide text-ink-muted">
                    {t("search.recent")}
                  </p>
                  {recentResults.map(renderOption)}
                </div>
              ) : null}

              {trimmed && pageMatches.length > 0 ? (
                <div data-testid="palette-group-pages">
                  <p aria-hidden className="px-3 pt-1 pb-1 text-[11px] uppercase tracking-wide text-ink-muted">
                    {t("search.pages")}
                  </p>
                  {pageMatches.map(renderOption)}
                </div>
              ) : null}

              {trimmed && catalogAttempted && state.phase === "loading" ? (
                <p role="status" className="px-3 py-3 text-center text-sm text-ink-muted">
                  {t("search.searching")}
                </p>
              ) : null}
              {trimmed && catalogAttempted && state.phase === "error" ? (
                <p role="alert" className="px-3 py-3 text-center text-sm text-critical">
                  {state.message}
                </p>
              ) : null}
              {trimmed && catalogMatches.length > 0 ? (
                <div data-testid="palette-group-catalog">
                  <p aria-hidden className="px-3 pt-1 pb-1 text-[11px] uppercase tracking-wide text-ink-muted">
                    {t("search.catalog")}
                  </p>
                  {catalogMatches.map(renderOption)}
                </div>
              ) : null}

              {showNoResults ? (
                <p role="status" className="px-3 py-6 text-center text-sm text-ink-muted">
                  {t("search.noResults")}
                </p>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
