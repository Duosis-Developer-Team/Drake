"use client";

/**
 * The log panel's "Patterns" view: every line in the window grouped by its
 * shape, counted in the store (GET …/logs/patterns), most severe first.
 * The same range, severity, search and timeline slice as the lines view;
 * health checks stay hidden unless the reader asked to see them.
 */

import { ChevronDown, ChevronRight, EyeOff } from "lucide-react";
import { useEffect, useState } from "react";

import { ErrorState, EmptyState, LoadingSkeleton } from "@/components/ui/states";
import { ApiError, apiGet } from "@/lib/api";
import { toneSpec, type StatusTone } from "@/lib/design/status";
import { useT } from "@/lib/i18n";
import {
  isHealthCheck,
  severityBand,
  utcClock,
  utcDay,
  type LogRange,
  type LogSeverity,
  type SeverityBand,
} from "@/lib/logs";
import type { TimelineWindow } from "@/lib/timeline";

export interface LogPattern {
  pattern: string;
  lines: number;
  severity: string | null;
  severity_number: number;
  first_at: string;
  last_at: string;
  latest: string;
  earliest: string;
}

interface PatternPage {
  state: "ok" | "not_configured" | "unavailable";
  reason?: string;
  patterns: LogPattern[];
}

const BAND_TONE: Record<SeverityBand, StatusTone> = {
  fatal: "critical",
  error: "critical",
  warn: "warning",
  info: "info",
  debug: "neutral",
  none: "neutral",
};

export function patternsPath(
  bindingId: string,
  options: {
    range: LogRange;
    severity: LogSeverity;
    term: string;
    window?: { start: string; end: string } | null;
  },
): string {
  const params = new URLSearchParams({ range: options.range, severity: options.severity });
  const term = options.term.trim();
  if (term) params.set("q", term);
  if (options.window) {
    params.set("start", options.window.start);
    params.set("end", options.window.end);
  }
  return `/v1/service-health/bindings/${encodeURIComponent(bindingId)}/logs/patterns?${params.toString()}`;
}

type Load =
  | { kind: "loading" }
  | { kind: "failed"; message: string }
  | { kind: "page"; page: PatternPage };

export function LogPatterns({
  bindingId,
  range,
  severity,
  term,
  slice,
  hideHealth,
  onShowHealth,
}: {
  bindingId: string;
  range: LogRange;
  severity: LogSeverity;
  term: string;
  slice: TimelineWindow | null;
  hideHealth: boolean;
  onShowHealth: () => void;
}) {
  const t = useT("logs");
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoad({ kind: "loading" });
    apiGet<PatternPage>(patternsPath(bindingId, { range, severity, term, window: slice }), controller.signal)
      .then((page) => {
        if (!controller.signal.aborted) setLoad({ kind: "page", page });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        if (error instanceof DOMException && error.name === "AbortError") return;
        setLoad({ kind: "failed", message: error instanceof ApiError ? error.message : String(error) });
      });
    return () => controller.abort();
  }, [bindingId, range, severity, term, slice, nonce]);

  if (load.kind === "loading") return <LoadingSkeleton rows={5} />;
  if (load.kind === "failed") {
    return <ErrorState compact description={load.message} onRetry={() => setNonce((n) => n + 1)} />;
  }
  if (load.page.state !== "ok") {
    return (
      <ErrorState
        compact
        title={t("state.unavailable.title")}
        description={t("state.unavailable.description", { reason: load.page.reason ?? "—" })}
        onRetry={() => setNonce((n) => n + 1)}
      />
    );
  }

  const all = load.page.patterns;
  const health = all.filter((p) => isHealthCheck({ body: p.latest }));
  const shown = hideHealth ? all.filter((p) => !isHealthCheck({ body: p.latest })) : all;
  const hiddenLines = hideHealth ? health.reduce((sum, p) => sum + p.lines, 0) : 0;
  const total = shown.reduce((sum, p) => sum + p.lines, 0);
  const toggle = (key: string) =>
    setOpen((previous) => {
      const next = new Set(previous);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  return (
    <div className="space-y-3" data-testid="log-patterns">
      <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-caption text-ink-secondary">
        <span data-tabular className="font-medium text-ink">
          {t("patterns.summary", { lines: total, patterns: shown.length })}
        </span>
        {hiddenLines > 0 ? (
          <span className="inline-flex items-center gap-1.5 text-ink-muted" data-testid="log-patterns-health">
            <EyeOff className="h-3.5 w-3.5" aria-hidden />
            {t("healthChecks.hidden", { count: hiddenLines })}
            <button
              type="button"
              onClick={onShowHealth}
              className="font-medium text-ink-secondary underline-offset-2 hover:text-ink hover:underline"
            >
              {t("healthChecks.show")}
            </button>
          </span>
        ) : null}
      </p>

      {shown.length === 0 ? (
        <EmptyState
          compact
          title={health.length > 0 ? t("state.allHidden.title") : t("state.empty.title")}
        />
      ) : (
        <ol className="-mx-2 max-h-[40rem] divide-y divide-border overflow-y-auto overscroll-contain rounded-[1rem] border border-border">
          {shown.map((p) => {
            const key = p.pattern;
            const expanded = open.has(key);
            const band = severityBand({ severity_number: p.severity_number });
            const spec = toneSpec(BAND_TONE[band]);
            return (
              <li key={key}>
                <button
                  type="button"
                  onClick={() => toggle(key)}
                  aria-expanded={expanded}
                  className="grid w-full grid-cols-[auto_auto_minmax(0,1fr)_auto] items-center gap-x-3 px-4 py-2.5 text-left transition-colors hover:bg-surface-hover"
                >
                  {expanded ? (
                    <ChevronDown className="h-3.5 w-3.5 text-ink-muted" aria-hidden />
                  ) : (
                    <ChevronRight className="h-3.5 w-3.5 text-ink-muted" aria-hidden />
                  )}
                  <span className={`min-w-[3.5rem] rounded-md px-1.5 py-0.5 text-center text-micro font-semibold ${spec.chip}`}>
                    {p.severity ?? t(`band.${band}`)}
                  </span>
                  <span className="truncate font-mono text-caption text-ink" title={p.pattern}>
                    {p.pattern}
                  </span>
                  <span data-tabular className="text-caption font-semibold text-ink">
                    {t("patterns.times", { count: p.lines })}
                  </span>
                </button>
                {expanded ? (
                  <div className="space-y-2 border-t border-border bg-surface-2 px-4 py-3 sm:pl-10">
                    <p className="text-micro text-ink-muted">
                      {t("patterns.span", {
                        first: `${utcDay(p.first_at)} ${utcClock(p.first_at)}`,
                        last: `${utcDay(p.last_at)} ${utcClock(p.last_at)}`,
                      })}
                    </p>
                    <Sample label={t("patterns.latest")} text={p.latest} />
                    {p.earliest !== p.latest ? <Sample label={t("patterns.earliest")} text={p.earliest} /> : null}
                  </div>
                ) : null}
              </li>
            );
          })}
        </ol>
      )}
      <p className="text-micro text-ink-muted">{t("patterns.note")}</p>
    </div>
  );
}

function Sample({ label, text }: { label: string; text: string }) {
  return (
    <div>
      <p className="text-micro text-ink-muted">{label}</p>
      <pre className="mt-0.5 whitespace-pre-wrap break-all font-mono text-micro text-ink">{text}</pre>
    </div>
  );
}
