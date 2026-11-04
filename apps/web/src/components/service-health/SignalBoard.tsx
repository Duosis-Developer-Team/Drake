"use client";

/**
 * "Where is the problem?" — every bound service ranked by what its own
 * telemetry says is going wrong: failed requests first, then error log
 * lines, from one read for the whole list (lib/signals).
 *
 * Absence stays honest (ADR-0011): no store configured, a store that is
 * not answering, and a quiet hour are different messages, and the board
 * never says "nothing is wrong" without saying how many services it judged.
 */

import { CheckCircle2, ChevronRight, Siren } from "lucide-react";
import Link from "next/link";

import { IconBubble } from "@/components/service-health/primitives";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { ErrorState, LoadingSkeleton, NotConfiguredState } from "@/components/ui/states";
import { toneSpec } from "@/lib/design/status";
import { useT } from "@/lib/i18n";
import type { ServiceHealthRow } from "@/lib/serviceHealth";
import { anySignalConfigured, percent, rankSignals, type RankedSignal, type SignalPage } from "@/lib/signals";
import { formatDuration } from "@/lib/traces";
import type { Resource } from "@/lib/useResource";

function Count({ value, tone }: { value: number | null | undefined; tone?: "critical" | "warning" }) {
  if (value === null || value === undefined) {
    return <span className="text-ink-muted">—</span>;
  }
  const colour = value > 0 && tone ? (tone === "critical" ? "text-critical" : "text-warning") : "text-ink";
  return (
    <span data-tabular className={`font-semibold ${value === 0 ? "text-ink-muted" : colour}`}>
      {value}
    </span>
  );
}

const ROW_GRID =
  "grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 @min-[40rem]/signals:grid-cols-[minmax(10rem,1fr)_4.5rem_4.5rem_4.5rem_5rem_1.5rem]";

function SignalRow({ entry }: { entry: RankedSignal }) {
  const t = useT("signals");
  const { row, item, verdict } = entry;
  const spec = toneSpec(verdict.tone);
  const Icon = spec.icon;
  const name = row.display_name || row.service_key;
  const traces = item.traces;
  const logs = item.logs;
  return (
    <li data-testid={`signal-row-${row.service_key}`}>
      <Link
        href={`/service-health/${row.binding?.id ?? ""}`}
        className={`px-7 py-3.5 transition-colors hover:bg-surface-hover ${ROW_GRID}`}
      >
        <span className="flex min-w-0 items-center gap-3">
          <span
            aria-hidden
            className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${spec.chip}`}
          >
            <Icon className="h-3.5 w-3.5" />
          </span>
          <span className="min-w-0">
            <span className="block truncate text-body font-semibold text-ink">{name}</span>
            <span className="block truncate text-micro text-ink-muted">
              {verdict.cause ? t(`cause.${verdict.cause}`) : t("cause.none")}
              {" · "}
              <span className="font-mono">
                {row.project_key}/{row.environment_key}
              </span>
            </span>
          </span>
        </span>
        <span className="hidden text-caption @min-[40rem]/signals:block">
          {traces && traces.requests === 0 ? (
            <span className="text-ink-muted">{t("noRequests")}</span>
          ) : (
            <Count value={traces?.requests} />
          )}
        </span>
        <span className="hidden text-caption @min-[40rem]/signals:block">
          <Count value={traces?.errors} tone={verdict.cause === "requests" ? (verdict.tone === "critical" ? "critical" : "warning") : undefined} />
          {traces && traces.error_ratio !== null && traces.errors > 0 ? (
            <span className="ml-1 text-micro text-ink-muted">{percent(traces.error_ratio)}</span>
          ) : null}
        </span>
        <span data-tabular className="hidden text-caption text-ink @min-[40rem]/signals:block">
          {traces ? formatDuration(traces.p95_ms) : <span className="text-ink-muted">—</span>}
        </span>
        <span className="hidden text-caption @min-[40rem]/signals:block">
          <Count value={logs?.errors} tone={verdict.cause === "logs" ? "warning" : undefined} />
          {logs && logs.warnings > 0 ? (
            <span data-tabular className="ml-1 text-micro text-ink-muted">
              +{logs.warnings}
            </span>
          ) : null}
        </span>
        <span aria-hidden className="flex justify-end text-ink-muted">
          <ChevronRight className="h-4 w-4" />
        </span>
      </Link>
    </li>
  );
}

/**
 * The board. `rows` are the services already on the page; `resource` is the
 * signal read for their bindings. `limit` caps how many rows a summary
 * placement (Command Center) shows; the rest are counted, not hidden.
 */
export function SignalBoard({
  rows,
  resource,
  limit,
}: {
  rows: ServiceHealthRow[];
  resource: Resource<SignalPage>;
  limit?: number;
}) {
  const t = useT("signals");
  const page = resource.data;
  const ranked = page ? rankSignals(rows, page) : [];
  const flagged = ranked.filter((entry) => entry.verdict.cause !== null);
  const shown = limit ? ranked.slice(0, Math.max(limit, 0)) : ranked;
  const hidden = ranked.length - shown.length;

  let body: React.ReactNode;
  if (!page && resource.loading) {
    body = <LoadingSkeleton variant="table" rows={3} />;
  } else if (!page) {
    body = (
      <ErrorState
        compact
        description={resource.error ?? undefined}
        correlationId={resource.correlationId}
        onRetry={resource.reload}
      />
    );
  } else if (!anySignalConfigured(page)) {
    body = <NotConfiguredState compact description={t("notConfigured")} />;
  } else if (page.logs.state === "unavailable" && page.traces.state !== "ok") {
    body = <ErrorState compact description={t("partial.both")} onRetry={resource.reload} />;
  } else if (ranked.length === 0) {
    body = <p className="px-7 py-6 text-caption text-ink-muted">{t("noTelemetry")}</p>;
  } else {
    body = (
      <>
        {page.logs.state === "unavailable" || page.traces.state === "unavailable" ? (
          <p className="border-b border-border px-7 py-2.5 text-micro text-warning">
            {page.logs.state === "unavailable" ? t("partial.logs") : t("partial.traces")}
          </p>
        ) : null}
        {flagged.length === 0 ? (
          <div className="flex items-start gap-3 border-b border-border px-7 py-4">
            <CheckCircle2 aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-success" />
            <p className="text-caption text-ink-secondary">
              {t("clear")}{" "}
              <span className="text-ink-muted">{t("clearNote", { count: ranked.length })}</span>
            </p>
          </div>
        ) : null}
        <div
          aria-hidden
          className={`hidden border-b border-border px-7 py-2.5 text-micro font-medium tracking-[0.08em] text-ink-muted uppercase @min-[40rem]/signals:grid ${ROW_GRID}`}
        >
          <span>{t("column.service")}</span>
          <span>{t("column.requests")}</span>
          <span>{t("column.failed")}</span>
          <span>{t("column.p95")}</span>
          <span>{t("column.errorLines")}</span>
          <span />
        </div>
        <ul className="divide-y divide-border" data-testid="signal-board-rows">
          {shown.map((entry) => (
            <SignalRow key={entry.item.binding_id} entry={entry} />
          ))}
        </ul>
        {hidden > 0 ? (
          <Link
            href="/service-health"
            className="block border-t border-border px-7 py-3 text-micro text-ink-muted hover:text-brand"
          >
            {t("more", { count: hidden })}
          </Link>
        ) : null}
      </>
    );
  }

  return (
    <Panel flush className="@container/signals" data-testid="signal-board">
      <PanelHeader
        flush
        title={
          <span className="flex items-center gap-3">
            <IconBubble icon={Siren} tone={flagged.some((e) => e.verdict.tone === "critical") ? "critical" : flagged.length > 0 ? "warning" : undefined} />
            {t("title")}
          </span>
        }
        description={t("description")}
      />
      {body}
    </Panel>
  );
}
