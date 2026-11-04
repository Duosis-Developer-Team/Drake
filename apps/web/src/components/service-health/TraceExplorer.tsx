"use client";

/**
 * Requests and traces for one bound service (ADR-0029).
 *
 * The question is "what is this service serving, and which requests hurt?"
 * — so the screen leads with three numbers (requests, error rate, p95),
 * then the endpoints that make them up, then the individual requests, and
 * only when one is opened, its trace. Nothing here is a query language.
 *
 * Absence stays honest (ADR-0011): tracing not enabled, the store not
 * answering, and a quiet window are three different screens, and a quiet
 * window is never presented as a healthy one. Access is the server's call:
 * a 404 on a binding the caller can already see means no `traces.view`.
 */

import { ChevronDown, ChevronRight, RefreshCw, Waypoints } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { IconBubble } from "@/components/service-health/primitives";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { Button, SegmentedControl } from "@/components/ui/controls";
import { CopyableIdentifier } from "@/components/ui/identifiers";
import {
  DeniedState,
  EmptyState,
  ErrorState,
  LoadingSkeleton,
  NotConfiguredState,
} from "@/components/ui/states";
import { ApiError, apiGet } from "@/lib/api";
import { toneSpec } from "@/lib/design/status";
import { useT } from "@/lib/i18n";
import { utcClock } from "@/lib/logs";
import {
  TRACE_RANGES,
  formatDuration,
  layoutWaterfall,
  traceDetailPath,
  tracesPath,
  type RequestSummary,
  type TraceDetail,
  type TracePage,
  type TraceRange,
  type TraceSort,
} from "@/lib/traces";

type Load =
  | { kind: "loading" }
  | { kind: "denied" }
  | { kind: "failed"; message: string; correlationId?: string }
  | { kind: "page"; page: TracePage };

/** How many requests a page shows at first, and how many each "show more"
 *  adds. The API returns up to 50; listing all of them at once turned the
 *  panel into a scroll that never ended. */
export const REQUESTS_STEP = 8;

function percent(ratio: number | null): string {
  if (ratio === null) return "—";
  const value = ratio * 100;
  return `${value < 10 && value > 0 ? value.toFixed(1) : Math.round(value)}%`;
}

export function TraceExplorer({ bindingId }: { bindingId: string }) {
  const t = useT("traces");
  const [range, setRange] = useState<TraceRange>("1h");
  const [sort, setSort] = useState<TraceSort>("recent");
  const [errorsOnly, setErrorsOnly] = useState(false);
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [refreshing, setRefreshing] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [shown, setShown] = useState(REQUESTS_STEP);
  const abortRef = useRef<AbortController | null>(null);

  const fetchPage = useCallback(
    (quiet: boolean) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      if (quiet) setRefreshing(true);
      else {
        setLoad({ kind: "loading" });
        setShown(REQUESTS_STEP);
      }
      apiGet<TracePage>(tracesPath(bindingId, { range, errorsOnly, sort }), controller.signal)
        .then((page) => {
          if (controller.signal.aborted) return;
          setLoad({ kind: "page", page });
        })
        .catch((error: unknown) => {
          if (controller.signal.aborted) return;
          if (error instanceof ApiError && (error.status === 404 || error.status === 403)) {
            setLoad({ kind: "denied" });
          } else if (error instanceof ApiError) {
            setLoad({ kind: "failed", message: error.message, correlationId: error.correlationId });
          } else if (!(error instanceof DOMException && error.name === "AbortError")) {
            setLoad({ kind: "failed", message: String(error) });
          }
        })
        .finally(() => {
          if (!controller.signal.aborted) setRefreshing(false);
        });
    },
    [bindingId, range, errorsOnly, sort],
  );

  useEffect(() => {
    fetchPage(false);
    return () => abortRef.current?.abort();
  }, [fetchPage]);

  const page = load.kind === "page" ? load.page : null;
  const slowest = useMemo(
    () => Math.max(1, ...(page?.requests ?? []).map((request) => request.duration_ms ?? 0)),
    [page],
  );

  return (
    <Panel data-testid="trace-explorer">
      <PanelHeader
        title={
          <span className="flex items-center gap-3">
            <IconBubble icon={Waypoints} />
            {t("title")}
          </span>
        }
        description={t("description")}
        actions={
          load.kind !== "denied" ? (
            <div className="flex flex-wrap items-center gap-2">
              <SegmentedControl
                label={t("range.label")}
                value={range}
                onChange={setRange}
                size="compact"
                options={TRACE_RANGES.map((value) => ({ value, label: value }))}
              />
              <Button
                size="compact"
                icon={RefreshCw}
                onClick={() => fetchPage(true)}
                disabled={load.kind === "loading" || refreshing}
                aria-busy={refreshing}
              >
                {t("refresh")}
              </Button>
            </div>
          ) : null
        }
      />

      {load.kind === "loading" ? <LoadingSkeleton rows={5} /> : null}
      {load.kind === "denied" ? (
        <DeniedState title={t("state.denied.title")} description={t("state.denied.description")} />
      ) : null}
      {load.kind === "failed" ? (
        <ErrorState description={load.message} correlationId={load.correlationId} onRetry={() => fetchPage(false)} />
      ) : null}
      {page?.state === "not_configured" ? (
        <NotConfiguredState title={t("state.notConfigured.title")} description={t("state.notConfigured.description")} />
      ) : null}
      {page?.state === "unavailable" ? (
        <ErrorState
          title={t("state.unavailable.title")}
          description={t("state.unavailable.description", { reason: page.reason ?? "—" })}
          onRetry={() => fetchPage(false)}
        />
      ) : null}

      {page?.state === "ok" && page.totals ? (
        page.totals.requests === 0 && !errorsOnly ? (
          <EmptyState title={t("state.empty.title")} description={t("state.empty.description")} />
        ) : (
          <>
            <dl className="grid grid-cols-1 gap-3 sm:grid-cols-3" data-testid="trace-totals">
              <Tile label={t("totals.requests")} value={String(page.totals.requests)} />
              <Tile
                label={t("totals.errorRate")}
                value={percent(page.totals.error_ratio)}
                critical={(page.totals.error_ratio ?? 0) > 0}
              />
              <Tile label={t("totals.p95")} value={formatDuration(page.totals.p95_ms)} />
            </dl>

            {page.routes.length > 0 ? (
              <div className="overflow-x-auto rounded-[1rem] border border-border">
                <table className="w-full text-caption" data-testid="trace-routes">
                  <caption className="sr-only">{t("routes.title")}</caption>
                  <thead className="bg-surface-2 text-micro text-ink-muted">
                    <tr>
                      <th scope="col" className="px-4 py-2 text-left font-medium">{t("routes.route")}</th>
                      <th scope="col" className="px-3 py-2 text-right font-medium">{t("routes.requests")}</th>
                      <th scope="col" className="px-3 py-2 text-right font-medium">{t("routes.errors")}</th>
                      <th scope="col" className="px-3 py-2 text-right font-medium">{t("routes.p50")}</th>
                      <th scope="col" className="px-4 py-2 text-right font-medium">{t("routes.p95")}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {page.routes.map((route) => (
                      <tr key={route.route}>
                        <td className="max-w-[24rem] truncate px-4 py-2 font-mono text-ink" title={route.route}>
                          {route.route}
                        </td>
                        <td data-tabular className="px-3 py-2 text-right text-ink">{route.requests}</td>
                        <td
                          data-tabular
                          className={`px-3 py-2 text-right ${route.errors > 0 ? "font-medium text-critical" : "text-ink-muted"}`}
                        >
                          {route.errors > 0 ? `${route.errors} · ${percent(route.error_ratio)}` : "0"}
                        </td>
                        <td data-tabular className="px-3 py-2 text-right text-ink-secondary">{formatDuration(route.p50_ms)}</td>
                        <td data-tabular className="px-4 py-2 text-right text-ink">{formatDuration(route.p95_ms)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}

            <div className="flex flex-wrap items-center gap-3">
              <h3 className="mr-auto text-body font-semibold text-ink">{t("requests.title")}</h3>
              <SegmentedControl
                label={t("requests.sort.label")}
                value={sort}
                onChange={setSort}
                size="compact"
                options={[
                  { value: "recent", label: t("requests.sort.recent") },
                  { value: "slowest", label: t("requests.sort.slowest") },
                ]}
              />
              <label className="inline-flex cursor-pointer items-center gap-2 text-caption text-ink-secondary">
                <input
                  type="checkbox"
                  checked={errorsOnly}
                  onChange={(event) => setErrorsOnly(event.target.checked)}
                  className="h-4 w-4 rounded border-border-strong accent-[var(--color-brand)]"
                />
                {t("requests.errorsOnly")}
              </label>
            </div>

            {page.requests.length === 0 ? (
              <EmptyState title={t("requests.none")} compact />
            ) : (
              <ol
                className="-mx-2 divide-y divide-border overflow-hidden rounded-[1rem] border border-border"
                data-testid="trace-requests"
                aria-busy={refreshing}
              >
                {page.requests.slice(0, shown).map((request) => (
                  <li key={request.trace_id}>
                    <RequestRow
                      request={request}
                      slowest={slowest}
                      open={open === request.trace_id}
                      onToggle={() => setOpen((current) => (current === request.trace_id ? null : request.trace_id))}
                    />
                    {open === request.trace_id ? (
                      <TraceWaterfall bindingId={bindingId} traceId={request.trace_id} />
                    ) : null}
                  </li>
                ))}
              </ol>
            )}

            {page.requests.length > 0 ? (
              <div className="flex flex-wrap items-center justify-between gap-3 text-micro text-ink-muted">
                <span data-testid="trace-requests-shown">
                  {t("requests.showing", {
                    shown: Math.min(shown, page.requests.length),
                    total: page.requests.length,
                  })}
                </span>
                {shown < page.requests.length ? (
                  <Button
                    size="compact"
                    onClick={() => setShown((current) => current + REQUESTS_STEP)}
                    data-testid="trace-requests-more"
                  >
                    {t("requests.more", {
                      count: Math.min(REQUESTS_STEP, page.requests.length - shown),
                    })}
                  </Button>
                ) : null}
              </div>
            ) : null}

            <p className="text-micro text-ink-muted">{t("note")}</p>
          </>
        )
      ) : null}
    </Panel>
  );
}

function Tile({ label, value, critical = false }: { label: string; value: string; critical?: boolean }) {
  return (
    <div className="rounded-[1rem] bg-surface-2 px-4 py-3">
      <dt className="text-micro text-ink-muted">{label}</dt>
      <dd data-tabular className={`mt-1 text-[1.5rem] leading-none font-semibold ${critical ? "text-critical" : "text-ink"}`}>
        {value}
      </dd>
    </div>
  );
}

function RequestRow({
  request,
  slowest,
  open,
  onToggle,
}: {
  request: RequestSummary;
  slowest: number;
  open: boolean;
  onToggle: () => void;
}) {
  const t = useT("traces");
  const fraction = Math.min(1, (request.duration_ms ?? 0) / slowest);
  const critical = toneSpec("critical");
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      className={`grid w-full grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-3 px-4 py-2 text-left transition-colors hover:bg-surface-hover sm:grid-cols-[auto_auto_minmax(0,1fr)_9rem_5rem] ${
        request.error ? "bg-critical-soft/40" : "" // i18n-ignore: CSS
      }`}
    >
      <span className="flex items-center gap-1.5">
        <span className="sr-only">{open ? t("requests.close") : t("requests.open")}:</span>
        {open ? (
          <ChevronDown className="h-3.5 w-3.5 text-ink-muted" aria-hidden />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 text-ink-muted" aria-hidden />
        )}
        <time data-tabular dateTime={request.timestamp} className="font-mono text-micro text-ink-muted">
          {utcClock(request.timestamp)}
        </time>
      </span>
      <span
        className={`hidden min-w-[3rem] justify-center rounded-full px-2 py-0.5 text-micro font-semibold sm:inline-flex ${
          request.error ? critical.chip : "bg-surface-2 text-ink-secondary"
        }`}
      >
        {request.http_status ?? (request.error ? "ERR" : "OK")}
      </span>
      <span className="truncate font-mono text-caption text-ink" title={request.path ?? request.route}>
        {request.path ?? request.route}
      </span>
      <span className="hidden h-1.5 overflow-hidden rounded-full bg-surface-2 sm:block" aria-hidden>
        <span
          className={`block h-full rounded-full ${request.error ? "bg-critical" : "bg-brand"}`}
          style={{ width: `${Math.max(2, fraction * 100)}%` }}
        />
      </span>
      <span data-tabular className="text-right font-mono text-micro text-ink">
        {formatDuration(request.duration_ms)}
      </span>
    </button>
  );
}

function TraceWaterfall({ bindingId, traceId }: { bindingId: string; traceId: string }) {
  const t = useT("traces");
  const [detail, setDetail] = useState<TraceDetail | null>(null);
  const [state, setState] = useState<"loading" | "gone" | "failed" | "ok">("loading");
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setState("loading");
    apiGet<TraceDetail>(traceDetailPath(bindingId, traceId), controller.signal)
      .then((body) => {
        if (controller.signal.aborted) return;
        setDetail(body);
        setState(body.state === "ok" ? "ok" : "failed");
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState(error instanceof ApiError && error.status === 404 ? "gone" : "failed");
      });
    return () => controller.abort();
  }, [bindingId, traceId]);

  const rows = useMemo(() => (detail ? layoutWaterfall(detail.spans) : []), [detail]);
  const total = rows.length > 0 ? Math.max(...rows.map((row) => row.span.duration_ms ?? 0)) : null;

  return (
    <div className="space-y-3 border-t border-border bg-surface-2 px-4 py-4" data-testid="trace-waterfall">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <span className="text-caption font-semibold text-ink">{t("trace.title")}</span>
        <CopyableIdentifier value={traceId} label={t("trace.title")} truncate={20} />
        {state === "ok" ? (
          <span className="text-micro text-ink-muted">
            {t("trace.spans", { count: rows.length })} · {t("trace.total", { duration: formatDuration(total) })}
          </span>
        ) : null}
      </div>
      {state === "loading" ? <p className="text-caption text-ink-muted">{t("trace.loading")}</p> : null}
      {state === "gone" ? <p className="text-caption text-ink-muted">{t("trace.gone")}</p> : null}
      {state === "failed" ? (
        <p className="text-caption text-critical">{t("state.unavailable.title")}</p>
      ) : null}
      {state === "ok" ? (
        <ol className="space-y-1" aria-label={t("trace.title")}>
          {rows.map((row) => {
            const isOpen = selected === row.span.span_id;
            const kind = t.dyn("trace.kind", row.span.kind, row.span.kind);
            return (
              <li key={row.span.span_id}>
                <button
                  type="button"
                  onClick={() => setSelected(isOpen ? null : row.span.span_id)}
                  aria-expanded={isOpen}
                  className="grid w-full grid-cols-[minmax(0,14rem)_minmax(0,1fr)_4.5rem] items-center gap-3 rounded-md px-2 py-1 text-left hover:bg-surface-hover"
                >
                  <span
                    className="truncate font-mono text-micro text-ink"
                    style={{ paddingLeft: `${Math.min(row.depth, 8) * 0.75}rem` }}
                    title={`${row.span.name} · ${kind}`}
                  >
                    {row.span.name}
                  </span>
                  <span className="relative h-3 rounded-full bg-surface" aria-hidden>
                    <span
                      className={`absolute top-0 h-full rounded-full ${row.span.error ? "bg-critical" : row.span.kind === "Client" ? "bg-info" : "bg-brand"}`}
                      style={{ left: `${row.offset * 100}%`, width: `${Math.max(0.6, row.width * 100)}%` }}
                    />
                  </span>
                  <span data-tabular className="text-right font-mono text-micro text-ink-secondary">
                    {formatDuration(row.span.duration_ms)}
                  </span>
                </button>
                {isOpen ? (
                  <dl className="mx-2 mt-1 mb-2 grid grid-cols-1 gap-x-6 gap-y-1 rounded-[0.75rem] border border-border bg-surface px-3 py-2 font-mono text-micro sm:grid-cols-[max-content_minmax(0,1fr)]">
                    <div className="contents">
                      <dt className="text-ink-muted">{t("trace.status")}</dt>
                      <dd className={row.span.error ? "text-critical" : "text-ink"}>
                        {kind}
                        {row.span.error ? ` · ${row.span.status_message ?? "error"}` : ""}
                      </dd>
                    </div>
                    {Object.entries(row.span.attributes).map(([key, value]) => (
                      <div key={key} className="contents">
                        <dt className="text-ink-muted">{key}</dt>
                        <dd className="break-all text-ink">{value}</dd>
                      </div>
                    ))}
                  </dl>
                ) : null}
              </li>
            );
          })}
        </ol>
      ) : null}
    </div>
  );
}
