"use client";

/**
 * Bounded, user-driven collection pagination.
 *
 * Wave 3's portfolio and cluster views must not claim to cover an estate
 * they have not fully loaded, and must not crawl an unbounded collection on
 * their own. This hook loads exactly one page automatically (the scope's
 * first page) and loads every later page only through an explicit
 * `loadMore()` call, exposing `complete` so a caller can state — rather
 * than imply — whether its coverage is the whole authorized collection.
 *
 * The page adapter (`pageToSlice`) is held in a ref so passing a fresh
 * inline function every render cannot itself restart the request; only
 * `firstPath` changing is a new scope. A generation counter, paired with an
 * `AbortController` for the first-page fetch, drops any response that is no
 * longer for the current scope or reload.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, apiGet } from "@/lib/api";

export interface PageSlice<Item> {
  items: Item[];
  nextPath: string | null;
  total?: number | null;
}

export interface ProgressiveCollection<Item> {
  items: Item[];
  total: number | null;
  complete: boolean;
  loading: boolean;
  refreshing: boolean;
  loadingMore: boolean;
  denied: boolean;
  notFound: boolean;
  error: string | null;
  correlationId?: string;
  loadMore: () => void;
  reload: () => void;
}

export function useProgressiveCollection<Page, Item>(options: {
  firstPath: string;
  pageToSlice: (page: Page, loadedCount: number) => PageSlice<Item>;
  refreshMs?: number;
}): ProgressiveCollection<Item> {
  const { firstPath, refreshMs } = options;
  const pageToSliceRef = useRef(options.pageToSlice);
  pageToSliceRef.current = options.pageToSlice;

  const [items, setItems] = useState<Item[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [nextPath, setNextPath] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [denied, setDenied] = useState(false);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [correlationId, setCorrelationId] = useState<string | undefined>();

  const itemsRef = useRef<Item[]>([]);
  const nextPathRef = useRef<string | null>(null);
  const hasData = useRef(false);
  const generationRef = useRef(0);
  const loadingMoreRef = useRef(false);
  const [nonce, setNonce] = useState(0);

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  // A new scope starts from nothing — declared first so it runs before the
  // fetch effect below on the same render, and the fetch effect never reads
  // a stale `hasData`/`nextPath` left over from the previous scope.
  useEffect(() => {
    hasData.current = false;
    itemsRef.current = [];
    nextPathRef.current = null;
    setItems([]);
    setTotal(null);
    setNextPath(null);
  }, [firstPath]);

  useEffect(() => {
    const controller = new AbortController();
    generationRef.current += 1;
    const generation = generationRef.current;

    if (hasData.current) setRefreshing(true);
    else setLoading(true);

    apiGet<Page>(firstPath, controller.signal)
      .then((page) => {
        if (controller.signal.aborted || generationRef.current !== generation) return;
        const slice = pageToSliceRef.current(page, 0);
        hasData.current = true;
        itemsRef.current = slice.items;
        nextPathRef.current = slice.nextPath;
        setItems(slice.items);
        setTotal(slice.total ?? null);
        setNextPath(slice.nextPath);
        setError(null);
        setDenied(false);
        setNotFound(false);
        setCorrelationId(undefined);
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted || generationRef.current !== generation) return;
        if (cause instanceof ApiError) {
          setDenied(cause.status === 403 || cause.status === 401);
          setNotFound(cause.status === 404);
          setError(cause.message);
          setCorrelationId(cause.correlationId);
        } else {
          setDenied(false);
          setNotFound(false);
          setError("request failed");
          setCorrelationId(undefined);
        }
      })
      .finally(() => {
        if (controller.signal.aborted || generationRef.current !== generation) return;
        setLoading(false);
        setRefreshing(false);
      });

    return () => controller.abort();
  }, [firstPath, nonce]);

  useEffect(() => {
    if (!refreshMs) return;
    const timer = setInterval(reload, refreshMs);
    return () => clearInterval(timer);
  }, [refreshMs, reload]);

  const loadMore = useCallback(() => {
    const path = nextPathRef.current;
    if (!path || loadingMoreRef.current) return;
    loadingMoreRef.current = true;
    setLoadingMore(true);
    const generation = generationRef.current;

    apiGet<Page>(path)
      .then((page) => {
        if (generationRef.current !== generation) return;
        const slice = pageToSliceRef.current(page, itemsRef.current.length);
        const merged = [...itemsRef.current, ...slice.items];
        itemsRef.current = merged;
        nextPathRef.current = slice.nextPath;
        setItems(merged);
        setTotal(slice.total ?? null);
        setNextPath(slice.nextPath);
      })
      .catch(() => {
        // A later-page failure keeps the rows already loaded; completeness
        // stays false rather than the collection losing its progress.
      })
      .finally(() => {
        loadingMoreRef.current = false;
        if (generationRef.current === generation) setLoadingMore(false);
      });
  }, []);

  return {
    items,
    total,
    // A missing next page is only proof of completeness when the first page
    // actually succeeded — an errored fetch must never be read as "nothing
    // more exists".
    complete: nextPath === null && error === null,
    loading,
    refreshing,
    loadingMore,
    denied,
    notFound,
    error,
    correlationId,
    loadMore,
    reload,
  };
}
