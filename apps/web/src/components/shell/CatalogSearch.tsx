"use client";

/**
 * Global catalog search: Cmd/Ctrl+K (or /) dialog, debounced authorized
 * search, keyboard navigation, screen-reader announcements. Results come
 * exclusively from the authorized /v1/catalog/search endpoint.
 */

import { Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, apiGet } from "@/lib/api";
import type { SearchResult } from "@/lib/catalog";

type SearchState =
  | { phase: "idle" }
  | { phase: "loading" }
  | { phase: "error"; message: string }
  | { phase: "ready"; results: SearchResult[] };

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

export function CatalogSearch() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [state, setState] = useState<SearchState>({ phase: "idle" });
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const openerRef = useRef<HTMLButtonElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const close = useCallback(() => {
    setOpen(false);
    setQuery("");
    setState({ phase: "idle" });
    setActiveIndex(0);
    openerRef.current?.focus();
  }, []);

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
            setActiveIndex(0);
          }
        })
        .catch((error: unknown) => {
          if (!controller.signal.aborted) {
            setState({
              phase: "error",
              message: error instanceof ApiError ? error.message : "search failed",
            });
          }
        });
    }, 250);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query]);

  const results = state.phase === "ready" ? state.results : [];

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
      router.push(resultHref(results[activeIndex]));
      close();
    }
  };

  return (
    <>
      <button
        ref={openerRef}
        type="button"
        onClick={() => setOpen(true)}
        className="hidden h-9 w-64 items-center gap-2 rounded-lg border border-border bg-surface-sunken px-3 text-sm text-ink-muted hover:text-ink md:flex"
        aria-label="Search catalog"
      >
        <Search className="h-4 w-4" aria-hidden />
        <span className="flex-1 text-left">Search catalog…</span>
        <kbd className="rounded border border-border px-1.5 text-[10px]">⌘K</kbd>
      </button>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label="Search catalog"
        className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-border text-ink-secondary hover:bg-surface-sunken md:hidden"
      >
        <Search className="h-4 w-4" aria-hidden />
      </button>

      {open ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Catalog search"
          className="fixed inset-0 z-50 flex items-start justify-center p-4 pt-[12vh]"
          onKeyDown={onDialogKey}
        >
          <button
            type="button"
            aria-label="Close search"
            onClick={close}
            className="absolute inset-0 bg-black/40"
            tabIndex={-1}
          />
          <div className="relative w-full max-w-lg rounded-xl border border-border bg-surface shadow-xl">
            <div className="flex items-center gap-2 border-b border-border px-4">
              <Search className="h-4 w-4 text-ink-muted" aria-hidden />
              <input
                ref={inputRef}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search projects, environments, services, clusters…"
                aria-label="Search query"
                className="h-12 flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-ink-muted"
              />
              <kbd className="rounded border border-border px-1.5 text-[10px] text-ink-muted">
                esc
              </kbd>
            </div>
            <div
              role="listbox"
              aria-label="Search results"
              className="max-h-80 overflow-y-auto p-2"
            >
              {state.phase === "idle" ? (
                <p className="px-3 py-6 text-center text-sm text-ink-muted">
                  Type at least two characters to search your authorized catalog.
                </p>
              ) : null}
              {state.phase === "loading" ? (
                <p role="status" className="px-3 py-6 text-center text-sm text-ink-muted">
                  Searching…
                </p>
              ) : null}
              {state.phase === "error" ? (
                <p role="alert" className="px-3 py-6 text-center text-sm text-critical">
                  {state.message}
                </p>
              ) : null}
              {state.phase === "ready" && results.length === 0 ? (
                <p role="status" className="px-3 py-6 text-center text-sm text-ink-muted">
                  No authorized results.
                </p>
              ) : null}
              {results.map((result, index) => (
                <button
                  key={`${result.kind}-${result.id}`}
                  type="button"
                  role="option"
                  aria-selected={index === activeIndex}
                  onMouseEnter={() => setActiveIndex(index)}
                  onClick={() => {
                    router.push(resultHref(result));
                    close();
                  }}
                  className={`flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm ${
                    index === activeIndex
                      ? "bg-accent-soft text-accent-ink"
                      : "text-ink hover:bg-surface-sunken"
                  }`}
                >
                  <span className="rounded border border-border px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-ink-muted">
                    {result.kind}
                  </span>
                  <span className="min-w-0 flex-1 truncate">
                    {result.display_name || result.key}
                  </span>
                  {result.project_key && result.kind !== "project" ? (
                    <span className="font-mono text-[11px] text-ink-muted">
                      {result.project_key}
                    </span>
                  ) : null}
                </button>
              ))}
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
