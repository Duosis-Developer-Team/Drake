"use client";

/**
 * The log explorer for one bound service (ADR-0029).
 *
 * One question: what did this workload just say? The screen is built for
 * that and nothing else, which is the whole difference from a log product
 * where every line arrives under thirty facets.
 *
 * - Few controls with useful defaults: a window, a minimum severity, a
 *   plain-text search, and — only when there is more than one — a pod.
 * - Noise is hidden by default and accounted for. Kubernetes health checks
 *   are most of what a quiet service logs; they are folded away with a
 *   count and a one-click way back, never silently dropped.
 * - The lines that matter stand out: errors and warnings carry colour and
 *   are one click from being the only thing on screen.
 * - Absence is honest. "No lines matched", "no log store here" and "the
 *   store did not answer" are three different screens (ADR-0011), and none
 *   of them is phrased as "no errors".
 *
 * The server decides whose logs these are and enforces `logs.view`. This
 * component does not pre-judge access from the session: it asks, and a 404
 * on a binding the caller can already see means "no `logs.view` here",
 * which it says in those words. A UI check would be a second, weaker rule.
 */

import {
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  EyeOff,
  Radio,
  RefreshCw,
  ScrollText,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { IconBubble, PILL_SECONDARY } from "@/components/service-health/primitives";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { Button, SearchInput, SegmentedControl, Select } from "@/components/ui/controls";
import { CopyableIdentifier } from "@/components/ui/identifiers";
import {
  DeniedState,
  EmptyState,
  ErrorState,
  LoadingSkeleton,
  NotConfiguredState,
} from "@/components/ui/states";
import { ApiError, apiGet } from "@/lib/api";
import { toneSpec, type StatusTone } from "@/lib/design/status";
import { useT } from "@/lib/i18n";
import {
  LOG_RANGES,
  LOG_SEVERITIES,
  displayBand,
  isHealthCheck,
  logsPath,
  structuredBody,
  utcClock,
  utcDay,
  type LogEntry,
  type LogPage,
  type LogRange,
  type LogSeverity,
  type SeverityBand,
  type StructuredBody,
} from "@/lib/logs";

const LIVE_INTERVAL_MS = 10_000;
const SEARCH_DEBOUNCE_MS = 400;

const BAND_TONE: Record<SeverityBand, StatusTone> = {
  fatal: "critical",
  error: "critical",
  warn: "warning",
  info: "info",
  debug: "neutral",
  none: "neutral",
};

type Load =
  | { kind: "loading" }
  | { kind: "denied" }
  | { kind: "failed"; message: string; correlationId?: string }
  | { kind: "page"; page: LogPage; entries: LogEntry[] };

/** A line with what the view derives from it, computed once per page. */
interface Row {
  key: string;
  entry: LogEntry;
  structured: StructuredBody | null;
  band: SeverityBand;
  healthCheck: boolean;
}

function toRows(entries: LogEntry[]): Row[] {
  return entries.map((entry, index) => {
    const structured = structuredBody(entry.body);
    return {
      // Timestamps can repeat across a page boundary (the server repeats
      // rather than skips); the index keeps keys unique within the list.
      key: `${entry.timestamp}-${entry.pod ?? ""}-${index}`,
      entry,
      structured,
      band: displayBand(entry, structured),
      healthCheck: isHealthCheck(entry),
    };
  });
}

function useDebounced<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

export function LogExplorer({ bindingId }: { bindingId: string }) {
  const t = useT("logs");

  const [range, setRange] = useState<LogRange>("1h");
  const [severity, setSeverity] = useState<LogSeverity>("any");
  const [termInput, setTermInput] = useState("");
  const term = useDebounced(termInput, SEARCH_DEBOUNCE_MS);
  const [hideHealth, setHideHealth] = useState(true);
  const [pod, setPod] = useState("");
  const [bandFilter, setBandFilter] = useState<"" | "error" | "warn">("");
  const [live, setLive] = useState(false);

  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [refreshing, setRefreshing] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [olderFailed, setOlderFailed] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const abortRef = useRef<AbortController | null>(null);

  const fetchNewest = useCallback(
    (quiet: boolean) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      if (quiet) setRefreshing(true);
      else setLoad({ kind: "loading" });
      apiGet<LogPage>(logsPath(bindingId, { range, severity, term }), controller.signal)
        .then((page) => {
          if (controller.signal.aborted) return;
          setLoad({ kind: "page", page, entries: page.entries });
          setOlderFailed(false);
          if (!quiet) setExpanded(new Set());
        })
        .catch((error: unknown) => {
          if (controller.signal.aborted) return;
          if (error instanceof ApiError && (error.status === 404 || error.status === 403)) {
            // The binding itself is visible (this screen loaded), so a 404
            // here means the caller lacks `logs.view` at its scope.
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
    [bindingId, range, severity, term],
  );

  useEffect(() => {
    fetchNewest(false);
    return () => abortRef.current?.abort();
  }, [fetchNewest]);

  // Live: re-read the newest page on a timer. Paused while older lines are
  // loaded, because replacing the list would throw away what the reader
  // scrolled back for.
  const hasOlder = load.kind === "page" && load.entries.length > load.page.entries.length;
  useEffect(() => {
    if (!live || hasOlder || load.kind !== "page" || load.page.state !== "ok") return;
    const timer = setInterval(() => fetchNewest(true), LIVE_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [live, hasOlder, load, fetchNewest]);

  const loadOlder = useCallback(() => {
    if (load.kind !== "page" || !load.page.next_before) return;
    const current = load;
    setLoadingOlder(true);
    setOlderFailed(false);
    apiGet<LogPage>(
      logsPath(bindingId, { range, severity, term, before: current.page.next_before }),
    )
      .then((older) => {
        setLoad({
          kind: "page",
          // `page` keeps the newest page's metadata; the cursor moves on.
          page: { ...current.page, next_before: older.next_before },
          entries: [...current.entries, ...older.entries],
        });
      })
      .catch(() => setOlderFailed(true))
      .finally(() => setLoadingOlder(false));
  }, [load, bindingId, range, severity, term]);

  const rows = useMemo(() => (load.kind === "page" ? toRows(load.entries) : []), [load]);
  const pods = useMemo(
    () => [...new Set(rows.map((row) => row.entry.pod).filter((p): p is string => !!p))].sort(),
    [rows],
  );
  // A pod chosen earlier may not be on this page any more; drop the filter
  // rather than show an empty list for a pod that is not there.
  useEffect(() => {
    if (pod && !pods.includes(pod)) setPod("");
  }, [pod, pods]);

  const healthHidden = hideHealth ? rows.filter((row) => row.healthCheck).length : 0;
  const visible = rows.filter((row) => {
    if (hideHealth && row.healthCheck) return false;
    if (pod && row.entry.pod !== pod) return false;
    if (bandFilter === "error" && row.band !== "error" && row.band !== "fatal") return false;
    if (bandFilter === "warn" && row.band !== "warn") return false;
    return true;
  });
  const counted = rows.filter((row) => !(hideHealth && row.healthCheck));
  const errors = counted.filter((row) => row.band === "error" || row.band === "fatal").length;
  const warnings = counted.filter((row) => row.band === "warn").length;
  const filtersActive = severity !== "any" || term.trim() !== "" || pod !== "" || bandFilter !== "";

  const clearFilters = () => {
    setSeverity("any");
    setTermInput("");
    setPod("");
    setBandFilter("");
  };

  const toggle = (key: string) =>
    setExpanded((previous) => {
      const next = new Set(previous);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const isOk = load.kind === "page" && load.page.state === "ok";

  return (
    <Panel data-testid="log-explorer">
      <PanelHeader
        title={
          <span className="flex items-center gap-3">
            <IconBubble icon={ScrollText} />
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
                options={LOG_RANGES.map((value) => ({ value, label: value }))}
              />
              <button
                type="button"
                role="switch"
                aria-checked={live}
                onClick={() => setLive((value) => !value)}
                title={live ? t("live.on") : t("live.off")}
                className={`inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-caption font-medium transition-colors ${
                  live
                    ? "border-brand bg-surface-selected text-brand"
                    : "border-border text-ink-secondary hover:bg-surface-hover hover:text-ink"
                }`}
              >
                <Radio className={`h-3.5 w-3.5 ${live ? "motion-safe:animate-pulse" : ""}`} aria-hidden />
                {t("live.label")}
              </button>
              <Button
                size="compact"
                icon={RefreshCw}
                onClick={() => fetchNewest(true)}
                disabled={load.kind === "loading" || refreshing}
                aria-busy={refreshing}
              >
                {t("action.refresh")}
              </Button>
            </div>
          ) : null
        }
      />

      {load.kind !== "denied" ? (
        <div className="flex flex-wrap items-center gap-3" data-testid="log-filters">
          <SearchInput
            label={t("search.label")}
            placeholder={t("search.placeholder")}
            value={termInput}
            onChange={setTermInput}
            className="min-w-[14rem] flex-1"
          />
          <Select
            label={t("severity.label")}
            hideLabel
            value={severity}
            onChange={(value) => setSeverity((value || "any") as LogSeverity)}
            options={LOG_SEVERITIES.map((value) => ({ value, label: t(`severity.${value}`) }))}
            data-testid="log-severity"
          />
          {pods.length > 1 ? (
            <Select
              label={t("pod.label")}
              hideLabel
              value={pod}
              onChange={(value) => setPod(value)}
              placeholder={t("pod.all")}
              options={pods.map((value) => ({ value, label: value }))}
              data-testid="log-pod"
            />
          ) : null}
          <label className="inline-flex cursor-pointer items-center gap-2 text-caption text-ink-secondary">
            <input
              type="checkbox"
              checked={hideHealth}
              onChange={(event) => setHideHealth(event.target.checked)}
              className="h-4 w-4 rounded border-border-strong accent-[var(--color-brand)]"
            />
            {t("healthChecks.hide")}
          </label>
        </div>
      ) : null}

      {load.kind === "loading" ? <LoadingSkeleton rows={6} /> : null}

      {load.kind === "denied" ? (
        <DeniedState title={t("state.denied.title")} description={t("state.denied.description")} />
      ) : null}

      {load.kind === "failed" ? (
        <ErrorState
          description={load.message}
          correlationId={load.correlationId}
          onRetry={() => fetchNewest(false)}
        />
      ) : null}

      {load.kind === "page" && load.page.state === "not_configured" ? (
        <NotConfiguredState
          title={t("state.notConfigured.title")}
          description={t("state.notConfigured.description")}
        />
      ) : null}

      {load.kind === "page" && load.page.state === "unavailable" ? (
        <ErrorState
          title={t("state.unavailable.title")}
          description={t("state.unavailable.description", { reason: load.page.reason ?? "—" })}
          onRetry={() => fetchNewest(false)}
        />
      ) : null}

      {isOk && load.kind === "page" ? (
        <>
          <div
            className="flex flex-wrap items-center gap-2 text-caption text-ink-secondary"
            data-testid="log-summary"
          >
            <span data-tabular className="font-medium text-ink">
              {t("summary.lines", { count: counted.length })}
            </span>
            <SummaryChip
              active={bandFilter === "error"}
              tone="critical"
              count={errors}
              label={t("summary.errors", { count: errors })}
              title={t("summary.onlyErrors")}
              onClick={() => setBandFilter((value) => (value === "error" ? "" : "error"))}
            />
            <SummaryChip
              active={bandFilter === "warn"}
              tone="warning"
              count={warnings}
              label={t("summary.warnings", { count: warnings })}
              title={t("summary.onlyWarnings")}
              onClick={() => setBandFilter((value) => (value === "warn" ? "" : "warn"))}
            />
            {healthHidden > 0 ? (
              <span className="inline-flex items-center gap-1.5 text-ink-muted" data-testid="log-health-hidden">
                <EyeOff className="h-3.5 w-3.5" aria-hidden />
                {t("healthChecks.hidden", { count: healthHidden })}
                <button
                  type="button"
                  onClick={() => setHideHealth(false)}
                  className="font-medium text-ink-secondary underline-offset-2 hover:text-ink hover:underline"
                >
                  {t("healthChecks.show")}
                </button>
              </span>
            ) : null}
            {filtersActive ? (
              <button
                type="button"
                onClick={clearFilters}
                className="font-medium text-ink-secondary underline-offset-2 hover:text-ink hover:underline"
              >
                {t("action.clearFilters")}
              </button>
            ) : null}
            {load.page.window ? (
              <span data-tabular className="ml-auto font-mono text-micro text-ink-muted">
                {t("summary.window", {
                  start: utcClock(load.page.window.start).slice(0, 8),
                  end: utcClock(load.page.window.end).slice(0, 8),
                })}
              </span>
            ) : null}
          </div>

          {visible.length === 0 ? (
            rows.length > 0 && healthHidden === rows.length ? (
              <EmptyState
                title={t("state.allHidden.title")}
                description={t("state.allHidden.description")}
                action={
                  <button type="button" onClick={() => setHideHealth(false)} className={PILL_SECONDARY}>
                    {t("healthChecks.show")}
                  </button>
                }
              />
            ) : (
              <EmptyState
                title={t("state.empty.title")}
                description={t("state.empty.description")}
                action={
                  filtersActive ? (
                    <button type="button" onClick={clearFilters} className={PILL_SECONDARY}>
                      {t("action.clearFilters")}
                    </button>
                  ) : undefined
                }
              />
            )
          ) : (
            <ol
              className="-mx-2 divide-y divide-border overflow-hidden rounded-[1rem] border border-border"
              data-testid="log-lines"
              aria-busy={refreshing}
            >
              {visible.map((row, index) => {
                const day = utcDay(row.entry.timestamp);
                const previousDay = index > 0 ? utcDay(visible[index - 1].entry.timestamp) : null;
                return (
                  <li key={row.key}>
                    {previousDay !== null && day !== previousDay ? (
                      <div className="bg-surface-2 px-4 py-1 font-mono text-micro text-ink-muted">{day}</div>
                    ) : null}
                    <LogLine row={row} open={expanded.has(row.key)} onToggle={() => toggle(row.key)} />
                  </li>
                );
              })}
            </ol>
          )}

          <div className="flex flex-wrap items-center justify-between gap-3 text-micro text-ink-muted">
            <span>{t("redaction")}</span>
            {load.page.next_before ? (
              <Button
                size="compact"
                onClick={loadOlder}
                disabled={loadingOlder}
                data-testid="log-load-older"
              >
                {loadingOlder ? t("action.loadingOlder") : t("action.loadOlder")}
              </Button>
            ) : rows.length > 0 ? (
              <span>{t("summary.end")}</span>
            ) : null}
          </div>
          {olderFailed ? (
            <p role="alert" className="text-caption text-critical">
              {t("state.unavailable.title")} ·{" "}
              <button type="button" onClick={loadOlder} className="underline">
                {t("state.unavailableLink")}
              </button>
            </p>
          ) : null}
        </>
      ) : null}
    </Panel>
  );
}

function SummaryChip({
  active,
  tone,
  count,
  label,
  title,
  onClick,
}: {
  active: boolean;
  tone: StatusTone;
  count: number;
  label: string;
  title: string;
  onClick: () => void;
}) {
  const spec = toneSpec(tone);
  return (
    <button
      type="button"
      aria-pressed={active}
      title={title}
      onClick={onClick}
      disabled={count === 0 && !active}
      className={`inline-flex h-7 items-center gap-1.5 rounded-full px-2.5 font-medium transition-colors disabled:cursor-default disabled:opacity-60 ${
        count > 0 || active ? spec.chip : "bg-surface-2 text-ink-muted"
      } ${active ? "ring-2 ring-current ring-offset-1 ring-offset-surface" : ""}`}
    >
      <span data-tabular>{label}</span>
    </button>
  );
}

function LogLine({ row, open, onToggle }: { row: Row; open: boolean; onToggle: () => void }) {
  const t = useT("logs");
  const { entry, structured, band } = row;
  const tone = BAND_TONE[band];
  const spec = toneSpec(tone);
  const headline = structured?.message ?? entry.body;
  const emphasised = band === "error" || band === "fatal";

  return (
    <div
      className={emphasised ? "bg-critical-soft/40" : band === "warn" ? "bg-warning-soft/30" : ""} // i18n-ignore: CSS
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="grid w-full grid-cols-[auto_auto_minmax(0,1fr)] items-start gap-x-3 px-4 py-2 text-left transition-colors hover:bg-surface-hover sm:grid-cols-[auto_auto_10rem_minmax(0,1fr)]"
      >
        <span className="flex items-center gap-1.5 pt-px">
          <span className="sr-only">{open ? t("action.collapse") : t("action.expand")}:</span>
          {open ? (
            <ChevronDown className="h-3.5 w-3.5 text-ink-muted" aria-hidden />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 text-ink-muted" aria-hidden />
          )}
          <time data-tabular dateTime={entry.timestamp} className="font-mono text-micro text-ink-muted">
            {utcClock(entry.timestamp)}
          </time>
        </span>
        <span
          className={`inline-flex min-w-[3.5rem] justify-center rounded-full px-2 py-0.5 text-micro font-semibold ${
            band === "none" ? "bg-surface-2 text-ink-muted" : spec.chip
          }`}
        >
          {t(`band.${band}`)}
        </span>
        <span className="hidden truncate pt-px font-mono text-micro text-ink-muted sm:block" title={entry.pod ?? undefined}>
          {entry.pod ?? "—"}
        </span>
        <span
          className={`font-mono text-caption break-words whitespace-pre-wrap text-ink ${open ? "" : "line-clamp-2"}`}
        >
          {headline}
        </span>
      </button>
      {open ? <LogDetail row={row} /> : null}
    </div>
  );
}

function LogDetail({ row }: { row: Row }) {
  const t = useT("logs");
  const { entry, structured, band } = row;
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1600);
    return () => clearTimeout(timer);
  }, [copied]);

  const copy = () => {
    navigator.clipboard
      ?.writeText(entry.body)
      .then(() => setCopied(true))
      .catch(() => {
        // Clipboard access can be denied; the raw line below is selectable.
      });
  };

  const facts: [string, React.ReactNode][] = [
    [t("detail.time"), <span key="time" className="font-mono">{entry.timestamp}</span>],
    [t("detail.severity"), entry.severity ?? structured?.level ?? t(`band.${band}`)],
    [t("detail.pod"), <CopyableIdentifier key="pod" value={entry.pod} label={t("detail.pod")} />],
    [t("detail.container"), <span key="container" className="font-mono">{entry.container ?? "—"}</span>],
  ];
  if (entry.trace_id) {
    facts.push([t("detail.trace"), <CopyableIdentifier key="trace" value={entry.trace_id} label={t("detail.trace")} />]);
  }
  if (entry.span_id) {
    facts.push([t("detail.span"), <CopyableIdentifier key="span" value={entry.span_id} label={t("detail.span")} />]);
  }

  return (
    <div className="space-y-4 border-t border-border bg-surface-2 px-4 py-4 sm:pl-10" data-testid="log-detail">
      <dl className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2 xl:grid-cols-3">
        {facts.map(([label, value]) => (
          <div key={label} className="min-w-0">
            <dt className="text-micro text-ink-muted">{label}</dt>
            <dd className="truncate text-caption text-ink">{value}</dd>
          </div>
        ))}
      </dl>

      {structured && structured.fields.length > 0 ? (
        <div>
          <p className="mb-1.5 text-micro font-medium text-ink-secondary">{t("detail.fields")}</p>
          <dl className="grid grid-cols-1 gap-x-6 gap-y-1 rounded-[0.75rem] border border-border bg-surface px-3 py-2 font-mono text-micro sm:grid-cols-[max-content_minmax(0,1fr)]">
            {structured.fields.map(([key, value]) => (
              <div key={key} className="contents">
                <dt className="text-ink-muted">{key}</dt>
                <dd className="break-all text-ink">{value}</dd>
              </div>
            ))}
          </dl>
        </div>
      ) : null}

      <div>
        <div className="mb-1.5 flex items-center justify-between gap-2">
          <p className="text-micro font-medium text-ink-secondary">{t("detail.raw")}</p>
          <button
            type="button"
            onClick={copy}
            className={PILL_SECONDARY}
            aria-label={copied ? t("action.copied") : t("action.copyLine")}
          >
            {copied ? (
              <Check className="h-3.5 w-3.5 text-healthy" aria-hidden />
            ) : (
              <Copy className="h-3.5 w-3.5" aria-hidden />
            )}
            {copied ? t("action.copied") : t("action.copyLine")}
          </button>
        </div>
        <pre className="max-h-80 overflow-auto rounded-[0.75rem] border border-border bg-surface px-3 py-2 font-mono text-micro break-all whitespace-pre-wrap text-ink">
          {entry.body}
        </pre>
      </div>
    </div>
  );
}
