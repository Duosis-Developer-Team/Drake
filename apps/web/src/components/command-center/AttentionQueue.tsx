"use client";

/**
 * Needs attention — ranked, and grouped by root cause where more than one
 * row is about the same entity (brief §9.3, Task 2.3).
 *
 * Grouping is a display convenience, never a causality claim, and never a
 * way to hide a row: a lone row renders with no extra chrome at all, and a
 * multi-row group renders open by default with a `<details>` disclosure —
 * user-collapsible, but nothing starts collapsed. `groupByRootCause` is the
 * single source of truth for which rows belong together; see its own
 * comment in `lib/overview.ts` for why it keys off the row's own identity
 * rather than the free-text `context` label.
 *
 * A row's words come from its `reason` (data) through the catalogue; the
 * English `state`/`subject`/`context` strings on the item are the fallback
 * for an item built without one.
 *
 * Drawn as severity-led cards: the tone is the card's rail and its icon,
 * the app's logo says whose it is, and each card ends in what to do next.
 * The three counts at the top are filters, so a long queue can be read one
 * severity at a time.
 */

import { ArrowRight, Check, CheckCircle2, EyeOff } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Panel, PanelHeader } from "@/components/ui/Panel";
import { ProjectLogo } from "@/components/ui/ProjectLogo";
import { RelativeTime } from "@/components/ui/identifiers";
import { LoadingSkeleton } from "@/components/ui/states";
import { toneSpec, type StatusTone } from "@/lib/design/status";
import { useT, type Translator } from "@/lib/i18n";
import { groupByRootCause, rootCauseKeyOf, type AttentionItem } from "@/lib/overview";
import { percent } from "@/lib/signals";

function testIdOf(key: string): string {
  return `attention-group-${key.replace(/[^a-zA-Z0-9]+/g, "-")}`;
}

/** The three words of a row, translated from its `reason` where there is one. */
function describe(
  item: AttentionItem,
  t: Translator<"commandCenter">,
  ts: Translator<"signals">,
): { state: string; subject: string; context: string } {
  const reason = item.reason;
  if (!reason) return { state: item.state, subject: item.subject, context: item.context };
  switch (reason.kind) {
    case "incident":
      return {
        state: t(`attention.state.incident.${reason.state}`),
        subject: item.subject,
        context: item.context,
      };
    case "agent":
      return {
        state: t.dyn("attention.state.agent", reason.state, item.state),
        subject: item.subject,
        context: t("attention.context.clusterConnection"),
      };
    case "inventory":
      return {
        state: t.dyn("attention.state.inventory", reason.state, item.state),
        subject: item.subject,
        context: t("attention.context.clusterInventory"),
      };
    case "service":
      return {
        state: t.dyn("attention.state.service", reason.state, item.state),
        subject: item.subject,
        context: item.context,
      };
    case "integration": {
      // `t.dyn` takes no variables; the key is checked first so an observed
      // state the catalogue does not know still shows in the API's words.
      const key = `attention.state.integration.${reason.state}`;
      return {
        state: t.has(key)
          ? t(key as `attention.state.integration.${"degraded" | "stale" | "unknown"}`, { name: reason.name })
          : item.state,
        subject: item.subject,
        context: item.context,
      };
    }
    case "signal":
      return {
        state:
          reason.cause === "requests"
            ? ts("attention.requests", { ratio: percent(reason.ratio) })
            : ts("attention.logs", { count: reason.count }),
        subject: item.subject,
        context: item.context,
      };
    case "alerts":
      return {
        state: t(`attention.state.alerts.${reason.priority}`, { count: reason.count }),
        subject: t(`attention.subject.${reason.priority}`),
        context: t("attention.context.alerting"),
      };
  }
}

function AttentionCard({ item, projectIds }: { item: AttentionItem; projectIds?: Map<string, string> }) {
  const t = useT("commandCenter");
  const ts = useT("signals");
  const spec = toneSpec(item.tone);
  const Icon = spec.icon;
  const words = describe(item, t, ts);
  const projectId = item.projectKey ? projectIds?.get(item.projectKey) : undefined;
  return (
    <Link
      href={item.href}
      className={`group relative flex items-center gap-4 overflow-hidden rounded-xl border bg-surface py-3.5 pr-4 pl-5 transition-[border-color,box-shadow,transform] duration-200 hover:-translate-y-0.5 hover:border-border-strong hover:shadow-[0_14px_30px_-22px_rgb(0_0_0/0.5)] ${
        item.tone === "critical" ? "border-critical/45" : "border-border"
      }`}
    >
      <span aria-hidden className={`absolute inset-y-0 left-0 w-1 ${spec.dot}`} />
      <span aria-hidden className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl ${spec.chip}`}>
        <Icon className="h-5 w-5" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex min-w-0 items-center gap-2">
          {projectId && item.projectKey ? (
            <ProjectLogo projectId={projectId} name={item.projectKey} size="sm" variant="mark" />
          ) : null}
          <span className="truncate text-body font-semibold text-ink">{words.subject}</span>
        </span>
        <span className="mt-1 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          <span className={`inline-flex shrink-0 rounded-full px-2 py-0.5 text-micro font-semibold ${spec.chip}`}>
            {words.state}
          </span>
          <span className="truncate font-mono text-micro text-ink-muted">{words.context}</span>
        </span>
      </span>
      <span className="hidden shrink-0 flex-col items-end gap-1 sm:flex">
        {item.asOf ? (
          <span className="text-micro text-ink-muted">
            <RelativeTime value={item.asOf} />
          </span>
        ) : null}
        <span className="inline-flex items-center gap-1 text-micro font-semibold text-brand-accent">
          {t(`attention.action.${item.origin}`)}
          <ArrowRight aria-hidden className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" />
        </span>
      </span>
    </Link>
  );
}

type Filter = "critical" | "warning" | "other" | null;

/** Cards shown before "show more": the rest are counted on the button,
 *  never dropped silently. */
const CAP = 6;

function matches(item: AttentionItem, filter: Filter): boolean {
  if (filter === null) return true;
  if (filter === "other") return item.tone !== "critical" && item.tone !== "warning";
  return item.tone === filter;
}

export interface CheckedSource {
  key: string;
  label: string;
  /** True when the source answered this time; false when it did not. */
  answered: boolean;
}

export function AttentionQueue({
  items,
  loading,
  projectIds,
  checked = [],
}: {
  items: AttentionItem[];
  loading: boolean;
  /** Project key → id, so a row can show its app's logo. */
  projectIds?: Map<string, string>;
  /** The sources behind an empty queue, named rather than implied. */
  checked?: CheckedSource[];
}) {
  const t = useT("commandCenter");
  const common = useT("common");
  const ts = useT("signals");
  const [filter, setFilter] = useState<Filter>(null);
  const [expanded, setExpanded] = useState(false);
  const counts: { key: Exclude<Filter, null>; label: string; count: number; tone: StatusTone }[] = [
    { key: "critical", label: t("tone.critical"), count: items.filter((i) => i.tone === "critical").length, tone: "critical" },
    { key: "warning", label: t("tone.warning"), count: items.filter((i) => i.tone === "warning").length, tone: "warning" },
    {
      key: "other",
      label: t("attention.cannotSee"),
      count: items.filter((i) => i.tone !== "critical" && i.tone !== "warning").length,
      tone: "unknown",
    },
  ];
  const visible = items.filter((item) => matches(item, filter));
  const groups = groupByRootCause(visible);

  return (
    <Panel flush data-testid="needs-attention" className="flex flex-col">
      <PanelHeader
        flush
        title={t("attention.title")}
        description={t("attention.description")}
        meta={items.length > 0 ? <span>{common("count.items", { count: items.length })}</span> : undefined}
        actions={
          items.length > 0 ? (
            <Link
              href="/incidents"
              className="inline-flex items-center gap-1 rounded text-caption font-medium text-brand hover:underline"
            >
              {t("attention.allIncidents")}
              <ArrowRight className="h-3.5 w-3.5" aria-hidden />
            </Link>
          ) : undefined
        }
      />

      {!loading ? (
        <div className="grid grid-cols-3 gap-2 px-5 pb-4" role="group" aria-label={t("attention.title")}>
          {counts.map((entry) => {
            const spec = toneSpec(entry.tone);
            const Icon = entry.key === "other" ? EyeOff : spec.icon;
            const active = filter === entry.key;
            return (
              <button
                key={entry.key}
                type="button"
                aria-pressed={active}
                disabled={entry.count === 0 && !active}
                onClick={() => setFilter(active ? null : entry.key)}
                title={active ? t("attention.showAll") : t("attention.filterBy", { label: entry.label })}
                className={`flex items-center gap-3 rounded-xl border px-3.5 py-2.5 text-left transition-colors disabled:cursor-default ${
                  active ? "border-brand-accent/60 bg-surface-selected" : "border-border bg-surface-2/60 enabled:hover:bg-surface-hover"
                }`}
              >
                <span aria-hidden className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${entry.count > 0 ? spec.chip : "bg-surface-3 text-ink-muted"}`}>
                  <Icon className="h-4 w-4" />
                </span>
                <span className="min-w-0">
                  <span
                    data-tabular
                    className={`block font-[family-name:var(--font-display)] text-[1.375rem] leading-none font-bold ${entry.count > 0 ? spec.text : "text-ink-muted"}`}
                  >
                    {entry.count}
                  </span>
                  <span className="mt-0.5 block truncate text-micro text-ink-muted">{entry.label}</span>
                </span>
              </button>
            );
          })}
        </div>
      ) : null}

      {loading ? (
        <div className="px-5 py-5">
          <LoadingSkeleton variant="table" rows={4} label={t("attention.loading")} />
        </div>
      ) : items.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center border-t border-border px-6 py-8 text-center" data-testid="attention-empty">
          <span aria-hidden className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-healthy-soft">
            <CheckCircle2 className="h-7 w-7 text-healthy" />
          </span>
          <p className="text-body font-semibold text-ink">{t("attention.emptyTitle")}</p>
          <p className="mt-1 max-w-prose text-caption text-ink-secondary">{t("attention.emptyBody")}</p>
          {checked.length > 0 ? (
            <ul className="mt-5 flex flex-wrap justify-center gap-1.5" aria-label={t("attention.checked")}>
              {checked.map((source) => (
                <li
                  key={source.key}
                  className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-micro font-medium ${
                    source.answered ? "border-healthy/35 bg-healthy-soft text-healthy" : "border-dashed border-border text-ink-muted"
                  }`}
                >
                  {source.answered ? <Check aria-hidden className="h-3 w-3" /> : <EyeOff aria-hidden className="h-3 w-3" />}
                  {source.label}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : (
        <div className="@container/attention border-t border-border bg-surface-2/40 p-4">
        <ul className="grid gap-2 @min-[52rem]/attention:grid-cols-2" data-testid="attention-list">
          {(expanded ? groups : groups.slice(0, CAP)).map((group) => {
            const testId = testIdOf(rootCauseKeyOf(group[0]));
            if (group.length === 1) {
              return (
                <li key={testId} data-testid={testId}>
                  <AttentionCard item={group[0]} projectIds={projectIds} />
                </li>
              );
            }
            return (
              <li key={testId} data-testid={testId} className="col-span-full">
                <details open className="group rounded-xl border border-dashed border-border-strong p-2">
                  <summary className="cursor-pointer list-none px-2 pt-0.5 pb-2 text-micro text-ink-muted hover:text-ink">
                    {t("attention.group", { count: group.length, subject: describe(group[0], t, ts).subject })}
                  </summary>
                  <ul className="flex flex-col gap-2">
                    {group.map((item) => (
                      <li key={item.key}>
                        <AttentionCard item={item} projectIds={projectIds} />
                      </li>
                    ))}
                  </ul>
                </details>
              </li>
            );
          })}
        </ul>
        {groups.length > CAP ? (
          <button
            type="button"
            onClick={() => setExpanded((value) => !value)}
            aria-expanded={expanded}
            className="mt-3 inline-flex w-full items-center justify-center gap-1.5 rounded-xl border border-dashed border-border-strong py-2 text-caption font-medium text-ink-secondary transition-colors hover:bg-surface-hover hover:text-ink"
          >
            {expanded ? t("attention.showFewer") : t("attention.showMore", { count: groups.length - CAP })}
          </button>
        ) : null}
        </div>
      )}
    </Panel>
  );
}
