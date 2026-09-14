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
 */

import { ArrowRight } from "lucide-react";
import Link from "next/link";

import { Panel, PanelHeader } from "@/components/ui/Panel";
import { RelativeTime } from "@/components/ui/identifiers";
import { LoadingSkeleton } from "@/components/ui/states";
import { toneSpec } from "@/lib/design/status";
import { groupByRootCause, rootCauseKeyOf, type AttentionItem } from "@/lib/overview";

function testIdOf(key: string): string {
  return `attention-group-${key.replace(/[^a-zA-Z0-9]+/g, "-")}`;
}

function AttentionRow({ item }: { item: AttentionItem }) {
  const spec = toneSpec(item.tone);
  const Icon = spec.icon;
  return (
    <Link
      href={item.href}
      className="flex items-start gap-3 px-6 py-3.5 transition-colors hover:bg-surface-hover"
    >
      <Icon aria-hidden className={`mt-0.5 h-4 w-4 shrink-0 ${spec.text}`} />
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-baseline gap-x-2">
          <span className="text-body font-medium text-ink">{item.subject}</span>
          <span className={`text-caption ${spec.text}`}>{item.state}</span>
        </span>
        <span className="mt-0.5 block truncate text-micro text-ink-muted">{item.context}</span>
      </span>
      {item.asOf ? (
        <span className="shrink-0 text-micro text-ink-muted">
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
  const groups = groupByRootCause(items);

  return (
    <Panel flush data-testid="needs-attention">
      <PanelHeader
        flush
        title="Needs attention"
        description="Critical first, then warnings, then anything Drake cannot currently see."
        meta={items.length > 0 ? <span>{items.length} items</span> : undefined}
        actions={
          items.length > 0 ? (
            <Link
              href="/incidents"
              className="inline-flex items-center gap-1 rounded text-caption font-medium text-brand hover:underline"
            >
              All incidents
              <ArrowRight className="h-3.5 w-3.5" aria-hidden />
            </Link>
          ) : undefined
        }
      />

      {loading ? (
        <div className="px-4 py-4">
          <LoadingSkeleton variant="table" rows={4} label="Loading attention list" />
        </div>
      ) : items.length === 0 ? (
        <div className="px-4 py-5" data-testid="attention-empty">
          <p className="text-body font-medium text-ink">Nothing is currently flagged.</p>
          <p className="mt-1 max-w-prose text-caption text-ink-secondary">
            This is not a statement that the platform is healthy — it is the result of the
            checks below. Anything Drake has no source for cannot appear here. See Evidence
            coverage for exactly what was checked.
          </p>
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
                  <summary className="cursor-pointer list-none px-4 py-2 text-micro text-ink-muted hover:bg-surface-hover">
                    {group.length} related signals about{" "}
                    <span className="font-medium text-ink-secondary">{group[0].subject}</span> —
                    shown together, not asserted as one cause
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
