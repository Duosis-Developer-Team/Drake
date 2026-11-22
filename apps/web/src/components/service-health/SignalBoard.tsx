"use client";

/**
 * "Where is the problem?" — every bound service judged by what its own
 * telemetry says is going wrong: failed requests first, then error log
 * lines, from one read for the whole list (lib/signals).
 *
 * Two layers, both visual. The map puts every judged service on screen at
 * once — one square each, grouped by app, coloured by its verdict — so a
 * failing service is found by eye before anything is read. The tiles below
 * take the worst few and draw their numbers against the thresholds that
 * judge them (lib/signals): a bar with the warn and critical marks, so
 * "how close" is visible, not only "over or not".
 *
 * Absence stays honest (ADR-0011): no store configured, a store that is
 * not answering, and a quiet hour are different messages, and the board
 * never says "nothing is wrong" without saying how many services it judged.
 */

import { ArrowRight, CheckCircle2, Siren } from "lucide-react";
import Link from "next/link";

import { IconBubble } from "@/components/service-health/primitives";
import { ProjectLogo } from "@/components/ui/ProjectLogo";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { ErrorState, LoadingSkeleton, NotConfiguredState } from "@/components/ui/states";
import { toneSpec, type StatusTone } from "@/lib/design/status";
import { useFormat, useT } from "@/lib/i18n";
import type { ServiceHealthRow } from "@/lib/serviceHealth";
import {
  ERROR_LINES_WARN,
  REQUEST_ERROR_CRITICAL,
  REQUEST_ERROR_WARN,
  anySignalConfigured,
  percent,
  rankSignals,
  type RankedSignal,
  type SignalPage,
} from "@/lib/signals";
import { formatDuration } from "@/lib/traces";
import type { Resource } from "@/lib/useResource";

/** A service with no requests and no error lines this hour reads as idle,
 *  not as healthy: nothing was measured to be fine. */
function isIdle(entry: RankedSignal): boolean {
  const traces = entry.item.traces;
  const logs = entry.item.logs;
  return (!traces || traces.requests === 0) && (!logs || logs.errors === 0);
}

function cellTone(entry: RankedSignal): StatusTone | "idle" {
  if (entry.verdict.cause) return entry.verdict.tone;
  return isIdle(entry) ? "idle" : "success";
}

const CELL_CLASS: Record<string, string> = {
  critical: "bg-critical",
  warning: "bg-warning",
  success: "bg-healthy/70",
  idle: "bg-surface-3",
};

/* ---------------- the map ---------------- */

interface AppGroup {
  projectId: string;
  projectKey: string;
  entries: RankedSignal[];
}

function groupByApp(ranked: RankedSignal[]): AppGroup[] {
  const groups = new Map<string, AppGroup>();
  for (const entry of ranked) {
    const key = entry.row.project_id;
    const group = groups.get(key) ?? { projectId: key, projectKey: entry.row.project_key, entries: [] };
    group.entries.push(entry);
    groups.set(key, group);
  }
  // An app with a failing service leads; otherwise by name.
  const worst = (g: AppGroup) =>
    Math.min(...g.entries.map((e) => (e.verdict.tone === "critical" ? 0 : e.verdict.cause ? 1 : 2)));
  return [...groups.values()].sort((a, b) => worst(a) - worst(b) || a.projectKey.localeCompare(b.projectKey));
}

function SignalMap({ ranked }: { ranked: RankedSignal[] }) {
  const t = useT("signals");
  const groups = groupByApp(ranked);
  const detail = (entry: RankedSignal): string => {
    const traces = entry.item.traces;
    const logs = entry.item.logs;
    const parts: string[] = [];
    if (traces) {
      parts.push(
        traces.requests === 0
          ? t("noRequests")
          : t("map.requests", { count: traces.requests, failed: percent(traces.error_ratio ?? 0) }),
      );
    }
    if (logs) parts.push(t("map.errorLines", { count: logs.errors }));
    return parts.join(" · ");
  };
  return (
    <div className="flex flex-col gap-3" data-testid="signal-map">
      {groups.map((group) => (
        <div key={group.projectId} className="flex min-w-0 items-center gap-3">
          <span className="flex w-28 shrink-0 items-center gap-2 truncate text-caption font-medium text-ink-secondary">
            <ProjectLogo projectId={group.projectId} name={group.projectKey} size="sm" variant="mark" />
            <span className="truncate">{group.projectKey}</span>
          </span>
          <ul className="flex min-w-0 flex-1 flex-wrap gap-1.5">
            {group.entries.map((entry) => {
              const tone = cellTone(entry);
              const name = entry.row.display_name || entry.row.service_key;
              const label = `${name} · ${entry.row.environment_key} — ${detail(entry)}`;
              return (
                <li key={entry.item.binding_id}>
                  <Link
                    href={`/service-health/${entry.item.binding_id}`}
                    aria-label={label}
                    title={label}
                    className={`group relative block h-7 min-w-7 rounded-[0.4rem] px-1.5 text-[10px] leading-7 font-semibold text-white/90 transition-transform outline-none hover:scale-110 focus-visible:ring-2 focus-visible:ring-focus ${CELL_CLASS[tone]} ${
                      tone === "idle" ? "text-ink-muted" : ""
                    }`}
                  >
                    <span aria-hidden className="font-mono uppercase">
                      {entry.row.environment_key.slice(0, 1)}
                    </span>
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
      <ul className="flex flex-wrap items-center gap-x-4 gap-y-1 pt-1 text-micro text-ink-muted" aria-label={t("map.legend")}>
        {(["critical", "warning", "success", "idle"] as const).map((tone) => (
          <li key={tone} className="inline-flex items-center gap-1.5">
            <span aria-hidden className={`h-2.5 w-2.5 rounded-[3px] ${CELL_CLASS[tone]}`} />
            {t(`map.key.${tone}`)}
          </li>
        ))}
        <li className="ml-auto font-mono">{t("map.envKey")}</li>
      </ul>
    </div>
  );
}

/* ---------------- the tiles ---------------- */

/** A value against its thresholds: the fill is the value, the ticks are
 *  where it starts to matter. `scale` is the bar's full width. */
function ThresholdBar({
  value,
  scale,
  marks,
  tone,
  label,
  text,
}: {
  value: number | null;
  scale: number;
  marks: number[];
  tone: StatusTone;
  label: string;
  text: string;
}) {
  const share = value === null ? 0 : Math.min(1, value / scale);
  const fill = tone === "critical" ? "bg-critical" : tone === "warning" ? "bg-warning" : "bg-brand-accent";
  return (
    <div className="min-w-0">
      <div className="flex items-baseline justify-between gap-2 text-[11px] text-ink-muted">
        <span>{label}</span>
        <span
          data-tabular
          className={`font-mono text-caption font-semibold ${
            value === null ? "text-ink-muted" : tone === "critical" || tone === "warning" ? toneSpec(tone).text : "text-ink"
          }`}
        >
          {text}
        </span>
      </div>
      <span className="relative mt-1.5 block h-1.5 rounded-full bg-surface-3" aria-hidden>
        <span className={`absolute inset-y-0 left-0 rounded-full ${fill}`} style={{ width: `${share * 100}%` }} />
        {marks.map((mark) => (
          <span
            key={mark}
            className="absolute -top-0.5 h-2.5 w-px bg-ink-muted/70"
            style={{ left: `${Math.min(1, mark / scale) * 100}%` }}
          />
        ))}
      </span>
    </div>
  );
}

function SignalTile({ entry }: { entry: RankedSignal }) {
  const t = useT("signals");
  const fmt = useFormat();
  const { row, item, verdict } = entry;
  const name = row.display_name || row.service_key;
  const traces = item.traces;
  const logs = item.logs;
  const idle = isIdle(entry);
  const tone: StatusTone = verdict.cause ? verdict.tone : "success";
  const spec = toneSpec(tone);
  const Icon = spec.icon;
  const ratio = traces && traces.requests > 0 ? (traces.error_ratio ?? 0) : null;
  const requestsTone: StatusTone =
    ratio === null ? "unknown" : ratio >= REQUEST_ERROR_CRITICAL ? "critical" : ratio >= REQUEST_ERROR_WARN ? "warning" : "success";
  const linesTone: StatusTone = logs && logs.errors >= ERROR_LINES_WARN ? "warning" : "success";
  const lineScale = Math.max(ERROR_LINES_WARN * 2, logs?.errors ?? 0);
  return (
    <li data-testid={`signal-row-${row.service_key}`} className="min-w-0">
      <Link
        href={`/service-health/${row.binding?.id ?? ""}`}
        className={`group relative flex h-full flex-col gap-3 overflow-hidden rounded-xl border bg-surface p-4 pl-5 transition-[border-color,box-shadow,transform] duration-200 hover:-translate-y-0.5 hover:border-border-strong hover:shadow-[0_14px_30px_-22px_rgb(0_0_0/0.5)] ${
          verdict.tone === "critical" && verdict.cause ? "border-critical/50" : "border-border"
        }`}
      >
        <span
          aria-hidden
          className={`absolute inset-y-0 left-0 w-[3px] ${verdict.cause ? spec.dot : idle ? "bg-surface-3" : "bg-healthy/60"}`}
        />
        <span className="flex min-w-0 items-start gap-3">
          <ProjectLogo projectId={row.project_id} name={row.project_key} size="md" variant="mark" />
          <span className="min-w-0 flex-1">
            <span className="block truncate text-body font-semibold text-ink">{name}</span>
            <span className="block truncate font-mono text-micro text-ink-muted">
              {row.project_key}/{row.environment_key}
            </span>
          </span>
          <span className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-micro font-semibold ${verdict.cause ? spec.chip : "bg-surface-2 text-ink-secondary"}`}>
            {verdict.cause ? <Icon aria-hidden className="h-3 w-3" /> : null}
            {verdict.cause ? t(`cause.${verdict.cause}`) : idle ? t("map.key.idle") : t("cause.none")}
          </span>
        </span>
        <span className="grid grid-cols-2 gap-4">
          <ThresholdBar
            label={t("column.failed")}
            value={ratio}
            scale={REQUEST_ERROR_CRITICAL * 1.4}
            marks={[REQUEST_ERROR_WARN, REQUEST_ERROR_CRITICAL]}
            tone={requestsTone}
            text={ratio === null ? "—" : percent(ratio)}
          />
          <ThresholdBar
            label={t("column.errorLines")}
            value={logs ? logs.errors : null}
            scale={lineScale}
            marks={[ERROR_LINES_WARN]}
            tone={linesTone}
            text={logs ? fmt.number(logs.errors) : "—"}
          />
        </span>
        <span className="flex items-center gap-4 border-t border-border pt-2.5 text-micro text-ink-muted">
          <span>
            {t("column.requests")}{" "}
            <span data-tabular className="font-mono font-semibold text-ink">
              {traces ? fmt.number(traces.requests) : "—"}
            </span>
          </span>
          <span>
            {t("column.p95")}{" "}
            <span data-tabular className="font-mono font-semibold text-ink">
              {traces ? formatDuration(traces.p95_ms) : "—"}
            </span>
          </span>
          <ArrowRight aria-hidden className="ml-auto h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" />
        </span>
      </Link>
    </li>
  );
}

/**
 * The board. `rows` are the services already on the page; `resource` is the
 * signal read for their bindings. `limit` caps how many tiles a summary
 * placement (Command Center) shows; the map still shows every service, and
 * the rest are counted, not hidden.
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
  const failing = flagged.filter((entry) => entry.verdict.cause === "requests").length;
  const noisy = flagged.length - failing;
  const shown = limit ? ranked.slice(0, Math.max(limit, 0)) : ranked;
  const hidden = ranked.length - shown.length;

  let body: React.ReactNode;
  if (!page && resource.loading) {
    body = (
      <div className="px-7 pb-6">
        <LoadingSkeleton variant="table" rows={3} />
      </div>
    );
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
          <p className="border-y border-border px-7 py-2.5 text-micro text-warning">
            {page.logs.state === "unavailable" ? t("partial.logs") : t("partial.traces")}
          </p>
        ) : null}
        <div className="border-b border-border px-7 pt-1 pb-5">
          <SignalMap ranked={ranked} />
        </div>
        {flagged.length === 0 ? (
          <div className="flex items-start gap-3 border-b border-border px-7 py-3.5">
            <CheckCircle2 aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-healthy" />
            <p className="text-caption text-ink-secondary">
              {t("clear")} <span className="text-ink-muted">{t("clearNote", { count: ranked.length })}</span>
            </p>
          </div>
        ) : null}
        <ul
          className="grid gap-3 p-5 @min-[36rem]/signals:grid-cols-2 @min-[64rem]/signals:grid-cols-3"
          data-testid="signal-board-rows"
        >
          {shown.map((entry) => (
            <SignalTile key={entry.item.binding_id} entry={entry} />
          ))}
        </ul>
        {hidden > 0 ? (
          <Link
            href="/service-health"
            className="mt-auto flex items-center justify-between border-t border-border px-7 py-3 text-micro text-ink-muted hover:text-brand"
          >
            {t("more", { count: hidden })}
            <ArrowRight aria-hidden className="h-3.5 w-3.5" />
          </Link>
        ) : null}
      </>
    );
  }

  const headerTone = flagged.some((e) => e.verdict.tone === "critical") ? "critical" : flagged.length > 0 ? "warning" : undefined;
  return (
    <Panel flush className="@container/signals flex flex-col" data-testid="signal-board">
      <PanelHeader
        flush
        title={
          <span className="flex items-center gap-3">
            <IconBubble icon={Siren} tone={headerTone} />
            {t("title")}
          </span>
        }
        description={t("description")}
        actions={
          page && ranked.length > 0 ? (
            <span className="flex flex-wrap items-center justify-end gap-1.5 text-micro">
              <span className="rounded-full bg-surface-2 px-2.5 py-1 font-medium text-ink-secondary">
                {t("summary.judged", { count: ranked.length })}
              </span>
              <span className={`rounded-full px-2.5 py-1 font-semibold ${failing ? toneSpec("critical").chip : "bg-surface-2 text-ink-muted"}`}>
                {t("summary.failing", { count: failing })}
              </span>
              <span className={`rounded-full px-2.5 py-1 font-semibold ${noisy ? toneSpec("warning").chip : "bg-surface-2 text-ink-muted"}`}>
                {t("summary.noisy", { count: noisy })}
              </span>
            </span>
          ) : undefined
        }
      />
      {body}
    </Panel>
  );
}
