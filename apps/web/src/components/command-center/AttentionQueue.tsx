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
 */

import { ArrowRight, CheckCircle2 } from "lucide-react";
import Link from "next/link";

import { Panel, PanelHeader } from "@/components/ui/Panel";
import { RelativeTime } from "@/components/ui/identifiers";
import { LoadingSkeleton } from "@/components/ui/states";
import { toneSpec } from "@/lib/design/status";
import { useT, type Translator } from "@/lib/i18n";
import { groupByRootCause, rootCauseKeyOf, type AttentionItem } from "@/lib/overview";

function testIdOf(key: string): string {
  return `attention-group-${key.replace(/[^a-zA-Z0-9]+/g, "-")}`;
}

/** The three words of a row, translated from its `reason` where there is one. */
function describe(
  item: AttentionItem,
  t: Translator<"commandCenter">,
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
    case "alerts":
      return {
        state: t(`attention.state.alerts.${reason.priority}`, { count: reason.count }),
        subject: t(`attention.subject.${reason.priority}`),
        context: t("attention.context.alerting"),
      };
  }
}

function AttentionRow({ item }: { item: AttentionItem }) {
  const t = useT("commandCenter");
  const spec = toneSpec(item.tone);
  const Icon = spec.icon;
  const words = describe(item, t);
  return (
    <Link
      href={item.href}
      className="flex items-center gap-4 px-7 py-4 transition-colors hover:bg-surface-hover"
    >
      <span aria-hidden className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full ${spec.chip}`}>
        <Icon className="h-4 w-4" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-body font-semibold text-ink">{words.subject}</span>
        <span className="mt-0.5 block truncate text-micro text-ink-muted">{words.context}</span>
      </span>
      <span className={`hidden shrink-0 rounded-full px-2.5 py-1 text-micro font-medium sm:inline-flex ${spec.chip}`}>
        {words.state}
      </span>
      {item.asOf ? (
        <span className="w-14 shrink-0 text-right text-micro text-ink-muted">
          <RelativeTime value={item.asOf} />
        </span>
      ) : null}
    </Link>
  );
}

export function AttentionQueue({
  items,
  loading,
}: {
  items: AttentionItem[];
  loading: boolean;
}) {
  const t = useT("commandCenter");
  const common = useT("common");
  const groups = groupByRootCause(items);
  const tally = (["critical", "warning"] as const).map((tone) => ({
    tone,
    count: items.filter((item) => item.tone === tone).length,
  }));
  const otherCount = items.length - tally.reduce((sum, entry) => sum + entry.count, 0);

  return (
    <Panel flush data-testid="needs-attention">
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
        <div className="grid grid-cols-3 gap-3 border-b border-border px-7 py-5">
          {[
            ...tally.map((entry) => ({ key: entry.tone, label: t(`tone.${entry.tone}`), count: entry.count, spec: toneSpec(entry.tone) })),
            { key: "other", label: t("attention.cannotSee"), count: otherCount, spec: toneSpec("unknown") },
          ].map((entry) => (
            <div key={entry.key} className="rounded-[1rem] bg-surface-2 px-4 py-3">
              <span className="flex items-center gap-2 text-micro text-ink-muted">
                <span aria-hidden className={`h-2 w-2 rounded-full ${entry.spec.dot}`} />
                {entry.label}
              </span>
              <span
                data-tabular
                className={`mt-1 block text-[1.75rem] leading-none font-semibold tracking-[-0.03em] ${entry.count > 0 ? entry.spec.text : "text-ink-muted"}`}
              >
                {entry.count}
              </span>
            </div>
          ))}
        </div>
      ) : null}

      {loading ? (
        <div className="px-7 py-5">
          <LoadingSkeleton variant="table" rows={4} label={t("attention.loading")} />
        </div>
      ) : items.length === 0 ? (
        <div className="flex flex-col items-center px-7 py-10 text-center" data-testid="attention-empty">
          <span aria-hidden className="mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-healthy-soft">
            <CheckCircle2 className="h-7 w-7 text-healthy" />
          </span>
          <p className="text-body font-medium text-ink">{t("attention.emptyTitle")}</p>
          <p className="mt-1 max-w-prose text-caption text-ink-secondary">{t("attention.emptyBody")}</p>
        </div>
      ) : (
        <ul className="divide-y divide-border" data-testid="attention-list">
          {groups.map((group) => {
            const testId = testIdOf(rootCauseKeyOf(group[0]));
            if (group.length === 1) {
              return (
                <li key={testId} data-testid={testId}>
                  <AttentionRow item={group[0]} />
                </li>
              );
            }
            return (
              <li key={testId} data-testid={testId}>
                <details open className="group">
                  <summary className="cursor-pointer list-none px-7 py-2.5 text-micro text-ink-muted hover:bg-surface-hover">
                    {t("attention.group", { count: group.length, subject: describe(group[0], t).subject })}
                  </summary>
                  <ul className="divide-y divide-border border-t border-border">
                    {group.map((item) => (
                      <li key={item.key}>
                        <AttentionRow item={item} />
                      </li>
                    ))}
                  </ul>
                </details>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}
