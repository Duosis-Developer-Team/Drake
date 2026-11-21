"use client";

/**
 * Shared pieces for the Respond lists (incidents, incoming alerts,
 * deployments).
 *
 * Three rules every list follows:
 *
 * 1. Counts come from the server. A tile reads the API's own `total` for its
 *    filter (one `limit=1` read each), never a tally of the 25 rows that
 *    happen to be loaded — that was wrong the moment a list passed one page.
 * 2. Every count is a door: clicking a tile applies its filter to the list.
 * 3. Filters live in the URL, so a filtered view can be linked and survives
 *    a reload; the list pages with the API's cursor ("Load more").
 */

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, apiGet } from "@/lib/api";
import { toneSpec, type StatusTone } from "@/lib/design/status";
import { useFormat } from "@/lib/i18n";

/** Read and write a fixed set of query parameters. Empty values are removed. */
export function useUrlFilters<K extends string>(
  keys: readonly K[],
): [Record<K, string>, (patch: Partial<Record<K, string>>) => void] {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname() || "/";
  const values = Object.fromEntries(keys.map((key) => [key, params.get(key) ?? ""])) as Record<K, string>;
  const update = useCallback(
    (patch: Partial<Record<K, string>>) => {
      const next = new URLSearchParams(params.toString());
      for (const [key, value] of Object.entries(patch) as [K, string | undefined][]) {
        if (value) next.set(key, value);
        else next.delete(key);
      }
      const query = next.toString();
      router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
    },
    [params, pathname, router],
  );
  return [values, update];
}

export interface CursorPage<T> {
  items: T[];
  next_cursor?: string | null;
  total?: number;
  /** Offset-paged APIs answer with the offset they served instead. */
  offset?: number;
}

export type CursorList<T> =
  | { state: "loading" }
  | { state: "error"; message: string; correlationId?: string; denied: boolean }
  | {
      state: "ready";
      items: T[];
      total: number | null;
      hasMore: boolean;
      loadingMore: boolean;
      moreError: boolean;
      loadMore: () => void;
    };

/**
 * One list, paged by the API's cursor. Changing `path` starts over; "load
 * more" appends the next page. A 403 or 404 on the list itself is reported
 * as `denied`, a different screen from "the request failed".
 */
export function useCursorList<T>(
  path: string,
  { paging = "cursor" }: { paging?: "cursor" | "offset" } = {},
): [CursorList<T>, () => void] {
  const [items, setItems] = useState<T[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [total, setTotal] = useState<number | null>(null);
  const [error, setError] = useState<{ message: string; correlationId?: string; denied: boolean } | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState(false);
  const [nonce, setNonce] = useState(0);
  const current = useRef(path);

  useEffect(() => {
    current.current = path;
    const controller = new AbortController();
    setItems(null);
    setError(null);
    setMoreError(false);
    apiGet<CursorPage<T>>(path, controller.signal)
      .then((page) => {
        if (controller.signal.aborted) return;
        setItems(page.items);
        setTotal(typeof page.total === "number" ? page.total : null);
        setCursor(
          paging === "offset"
            ? typeof page.total === "number" && page.items.length < page.total
              ? String(page.items.length)
              : null
            : (page.next_cursor ?? null),
        );
      })
      .catch((e: unknown) => {
        if (controller.signal.aborted) return;
        const status = e instanceof ApiError ? e.status : 0;
        setError({
          message: e instanceof Error ? e.message : String(e),
          correlationId: e instanceof ApiError ? e.correlationId : undefined,
          denied: status === 403 || status === 404,
        });
      });
    return () => controller.abort();
  }, [path, nonce, paging]);

  const loadMore = useCallback(() => {
    if (!cursor || loadingMore) return;
    const requested = path;
    const separator = path.includes("?") ? "&" : "?";
    setLoadingMore(true);
    setMoreError(false);
    const param = paging === "offset" ? "offset" : "cursor";
    apiGet<CursorPage<T>>(`${path}${separator}${param}=${encodeURIComponent(cursor)}`)
      .then((page) => {
        if (current.current !== requested) return;
        setItems((previous) => {
          const merged = [...(previous ?? []), ...page.items];
          if (paging === "offset") {
            const known = typeof page.total === "number" ? page.total : null;
            setCursor(known !== null && merged.length < known && page.items.length > 0 ? String(merged.length) : null);
          }
          return merged;
        });
        if (paging === "cursor") setCursor(page.next_cursor ?? null);
      })
      .catch(() => current.current === requested && setMoreError(true))
      .finally(() => setLoadingMore(false));
  }, [cursor, loadingMore, path, paging]);

  const retry = useCallback(() => setNonce((n) => n + 1), []);

  if (error) return [{ state: "error", ...error }, retry];
  if (items === null) return [{ state: "loading" }, retry];
  return [
    { state: "ready", items, total, hasMore: Boolean(cursor), loadingMore, moreError, loadMore },
    retry,
  ];
}

/** A server-side count for one filter: the API's `total` at `limit=1`. */
export function useTotal(path: string | null): number | null {
  const [total, setTotal] = useState<number | null>(null);
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    setTotal(null);
    apiGet<{ total?: number }>(path, controller.signal)
      .then((body) => !controller.signal.aborted && setTotal(typeof body.total === "number" ? body.total : null))
      .catch(() => !controller.signal.aborted && setTotal(null));
    return () => controller.abort();
  }, [path]);
  return total;
}

/**
 * A count that is also a filter. Pressed when its filter is the active one;
 * pressing it again clears the filter. "—" when the count could not be read.
 */
export function CountTile({
  label,
  value,
  tone,
  active,
  onClick,
  caption,
  "data-testid": testId,
}: {
  label: string;
  value: number | null;
  tone: StatusTone;
  active: boolean;
  onClick: () => void;
  caption?: string;
  "data-testid"?: string;
}) {
  const fmt = useFormat();
  const spec = toneSpec(tone);
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      data-testid={testId}
      className={`flex min-w-0 flex-col gap-1.5 rounded-panel border bg-surface px-4 py-3.5 text-left shadow-panel transition-colors ${
        active ? "border-brand ring-2 ring-brand-soft" : "border-border hover:border-border-strong"
      }`}
    >
      <span className="flex items-center gap-2 text-caption text-ink-muted">
        <span aria-hidden className={`h-2 w-2 rounded-full ${spec.dot}`} />
        {label}
      </span>
      <span
        data-tabular
        className={`font-[family-name:var(--font-display)] text-[1.75rem] leading-none font-bold tracking-[-0.03em] ${
          value && value > 0 && (tone === "critical" || tone === "warning") ? spec.text : "text-ink"
        }`}
      >
        {value === null ? "—" : fmt.number(value)}
      </span>
      {caption ? <span className="text-micro text-ink-muted">{caption}</span> : null}
    </button>
  );
}

/** The footer of a paged list: how much is shown, and the way to more. */
export function ListFooter({
  shown,
  summary,
  hasMore,
  loadingMore,
  moreError,
  onMore,
  moreLabel,
  loadingLabel,
  errorLabel,
}: {
  shown: number;
  summary: string;
  hasMore: boolean;
  loadingMore: boolean;
  moreError: boolean;
  onMore: () => void;
  moreLabel: string;
  loadingLabel: string;
  errorLabel: string;
}) {
  return (
    <div className="flex flex-wrap items-center gap-3 border-t border-border px-5 py-3 text-micro text-ink-muted" data-shown={shown}>
      <span>{summary}</span>
      {moreError ? <span className="text-critical">{errorLabel}</span> : null}
      {hasMore ? (
        <button
          type="button"
          onClick={onMore}
          disabled={loadingMore}
          className="ml-auto inline-flex h-8 items-center rounded-control border border-border bg-surface px-3 text-caption font-medium text-ink transition-colors hover:bg-surface-hover disabled:opacity-60"
        >
          {loadingMore ? loadingLabel : moreLabel}
        </button>
      ) : null}
    </div>
  );
}
