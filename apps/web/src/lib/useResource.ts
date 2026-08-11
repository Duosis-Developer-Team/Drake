"use client";

/**
 * The read hook every redesigned screen uses.
 *
 * What it adds over a bare fetch, and why each one is here:
 *
 *   403 and 404 are their own states, not "error". A panel the caller is not
 *   authorized for must say "permission required" — telling them it is empty
 *   is both wrong and, in the other direction, a small disclosure.
 *
 *   A background refresh does not return to `loading`. Re-rendering a full
 *   skeleton every 30 seconds destroys the reader's place and makes a live
 *   screen unusable; `refreshing` is a separate flag the header shows.
 *
 *   The in-flight request is aborted when the path changes or the component
 *   unmounts, and a response that arrives after that is dropped. Without it,
 *   navigating between two projects can land the first project's data under
 *   the second one's heading — the wrong data under the right scope is the
 *   worst failure this screen has.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, apiGet } from "@/lib/api";

export interface Resource<T> {
  data: T | null;
  loading: boolean;
  /** A refresh is in flight over data already on screen. */
  refreshing: boolean;
  error: string | null;
  denied: boolean;
  notFound: boolean;
  correlationId?: string;
  /** When the data on screen was received. */
  fetchedAt: string | null;
  reload: () => void;
}

export function useResource<T>(
  path: string | null,
  options: { refreshMs?: number; enabled?: boolean } = {},
): Resource<T> {
  const { refreshMs, enabled = true } = options;
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [denied, setDenied] = useState(false);
  const [notFound, setNotFound] = useState(false);
  const [correlationId, setCorrelationId] = useState<string | undefined>();
  const [fetchedAt, setFetchedAt] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const hasData = useRef(false);
  const [nonce, setNonce] = useState(0);

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  useEffect(() => {
    if (!path || !enabled) {
      setLoading(false);
      return;
    }
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    if (hasData.current) setRefreshing(true);
    else setLoading(true);

    apiGet<T>(path, controller.signal)
      .then((body) => {
        if (controller.signal.aborted) return;
        hasData.current = true;
        setData(body);
        setError(null);
        setDenied(false);
        setNotFound(false);
        setCorrelationId(undefined);
        setFetchedAt(new Date().toISOString());
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
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
        if (controller.signal.aborted) return;
        setLoading(false);
        setRefreshing(false);
      });

    return () => controller.abort();
  }, [path, enabled, nonce]);

  // A new path is a new question: drop the previous answer so the old scope's
  // data cannot render for a frame under the new scope's heading.
  useEffect(() => {
    hasData.current = false;
    setData(null);
    setFetchedAt(null);
  }, [path]);

  useEffect(() => {
    if (!refreshMs || !path || !enabled) return;
    const timer = setInterval(reload, refreshMs);
    return () => clearInterval(timer);
  }, [refreshMs, path, enabled, reload]);

  return {
    data,
    loading,
    refreshing,
    error,
    denied,
    notFound,
    correlationId,
    fetchedAt,
    reload,
  };
}

/** The state a panel should render, from a resource. */
export function resourceStatus<T>(
  resource: Resource<T>,
): "loading" | "denied" | "not-found" | "error" | "ready" {
  if (resource.loading && !resource.data) return "loading";
  if (resource.denied) return "denied";
  if (resource.notFound) return "not-found";
  if (resource.error && !resource.data) return "error";
  return "ready";
}
