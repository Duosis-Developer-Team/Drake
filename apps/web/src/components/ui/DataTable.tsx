"use client";

/**
 * DataTable — the dense list.
 *
 * A small typed table rather than a table framework. Drake's lists are
 * server-paginated and server-filtered, so the parts a framework brings
 * (client-side sorting of a page that is not the whole set, virtual rows over
 * data the browser does not have) would be wrong here, and the parts that are
 * genuinely needed are alignment, priority columns and keyboard rows.
 *
 * The rules it enforces so no screen has to remember them:
 *
 *   Numbers are right-aligned and tabular; text is left-aligned. A column of
 *   proportional figures cannot be scanned.
 *
 *   Overflow is a visible scroller on the table, never a hidden clip. A cell
 *   that quietly cuts a namespace in half is worse than a scrollbar.
 *
 *   Narrow viewports drop columns marked `priority: "low"` rather than
 *   shrinking every column past readability. What is dropped stays reachable
 *   through the row's detail route or drawer.
 */

import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";

export type ColumnAlign = "left" | "right";

export interface Column<Row> {
  key: string;
  header: React.ReactNode;
  /** Cell content. Keep it presentational — no fetching from in here. */
  cell: (row: Row) => React.ReactNode;
  align?: ColumnAlign;
  /** `low` columns are hidden below `lg`. */
  priority?: "high" | "low";
  /** Sort key sent to the server. Omit for non-sortable columns. */
  sortKey?: string;
  width?: string;
  /** Screen-reader name when `header` is an icon or otherwise unreadable. */
  srHeader?: string;
}

export interface SortState {
  key: string;
  direction: "asc" | "desc";
}

export function DataTable<Row>({
  rows,
  columns,
  rowKey,
  caption,
  sort,
  onSortChange,
  density = "comfortable",
  onRowActivate,
  rowTone,
  emptyState,
  stickyHeader = false,
}: {
  rows: Row[];
  columns: Column<Row>[];
  rowKey: (row: Row) => string;
  /** Describes the table for screen readers. Always supplied. */
  caption: string;
  sort?: SortState | null;
  onSortChange?: (sort: SortState) => void;
  density?: "comfortable" | "compact";
  /** Makes rows keyboard-activatable. The row must also contain a real link
   *  for the primary target — this is the convenience, not the only path. */
  onRowActivate?: (row: Row) => void;
  /** A left rail colour per row, for triage lists. */
  rowTone?: (row: Row) => string | null;
  emptyState?: React.ReactNode;
  stickyHeader?: boolean;
}) {
  const pad = density === "compact" ? "px-3 py-1.5" : "px-3 py-2.5";

  if (rows.length === 0 && emptyState) {
    return <div className="px-4 py-2">{emptyState}</div>;
  }

  return (
    <div className="w-full overflow-x-auto" data-testid="data-table-scroller">
      <table className="w-full min-w-full border-collapse text-body" data-tabular>
        <caption className="sr-only">{caption}</caption>
        <thead
          className={`bg-surface-2 text-caption text-ink-secondary ${
            stickyHeader ? "sticky top-0 z-10" : ""
          }`}
        >
          <tr>
            {columns.map((column) => {
              const sortable = Boolean(column.sortKey && onSortChange);
              const active = sort && column.sortKey === sort.key;
              const Icon = !active ? ChevronsUpDown : sort.direction === "asc" ? ArrowUp : ArrowDown;
              return (
                <th
                  key={column.key}
                  scope="col"
                  style={column.width ? { width: column.width } : undefined}
                  aria-sort={
                    active ? (sort.direction === "asc" ? "ascending" : "descending") : undefined
                  }
                  className={`border-b border-border font-medium whitespace-nowrap ${pad} ${
                    column.align === "right" ? "text-right" : "text-left"
                  } ${column.priority === "low" ? "hidden lg:table-cell" : ""}`}
                >
                  {sortable ? (
                    <button
                      type="button"
                      onClick={() =>
                        onSortChange!({
                          key: column.sortKey!,
                          direction: active && sort.direction === "asc" ? "desc" : "asc",
                        })
                      }
                      className={`inline-flex items-center gap-1 rounded hover:text-ink ${
                        column.align === "right" ? "flex-row-reverse" : ""
                      }`}
                    >
                      {column.header}
                      <Icon aria-hidden className="h-3 w-3 shrink-0" />
                      {column.srHeader ? <span className="sr-only">{column.srHeader}</span> : null}
                    </button>
                  ) : (
                    <>
                      {column.header}
                      {column.srHeader ? <span className="sr-only">{column.srHeader}</span> : null}
                    </>
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const tone = rowTone?.(row);
            return (
              <tr
                key={rowKey(row)}
                tabIndex={onRowActivate ? 0 : undefined}
                onKeyDown={
                  onRowActivate
                    ? (event) => {
                        if (event.key === "Enter" && event.currentTarget === event.target) {
                          onRowActivate(row);
                        }
                      }
                    : undefined
                }
                className={`border-b border-border last:border-b-0 ${
                  onRowActivate ? "cursor-pointer" : ""
                } transition-colors hover:bg-surface-hover`}
              >
                {columns.map((column, index) => (
                  <td
                    key={column.key}
                    className={`align-top ${pad} ${
                      column.align === "right" ? "text-right" : "text-left"
                    } ${column.priority === "low" ? "hidden lg:table-cell" : ""} ${
                      index === 0 && tone ? `border-l-2 ${tone}` : ""
                    }`}
                  >
                    {column.cell(row)}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Server pagination controls.
 *
 * Shows the window and the total so a reader knows what is off-screen. When
 * the backend cannot give a total, `total` is omitted and the control says so
 * instead of inventing a page count.
 */
export function Pagination({
  offset,
  limit,
  count,
  total,
  onOffsetChange,
}: {
  offset: number;
  limit: number;
  /** Rows on this page. */
  count: number;
  total?: number | null;
  onOffsetChange: (offset: number) => void;
}) {
  const first = count === 0 ? 0 : offset + 1;
  const last = offset + count;
  const hasMore = total === null || total === undefined ? count === limit : last < total;
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-4 py-2">
      <p className="text-caption text-ink-muted" data-tabular>
        {total === null || total === undefined
          ? `Showing ${first}–${last}`
          : `Showing ${first}–${last} of ${total}`}
      </p>
      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={offset === 0}
          onClick={() => onOffsetChange(Math.max(0, offset - limit))}
          className="rounded-control border border-border px-2.5 py-1 text-caption font-medium text-ink transition-colors hover:bg-surface-hover disabled:opacity-40"
        >
          Previous
        </button>
        <button
          type="button"
          disabled={!hasMore}
          onClick={() => onOffsetChange(offset + limit)}
          className="rounded-control border border-border px-2.5 py-1 text-caption font-medium text-ink transition-colors hover:bg-surface-hover disabled:opacity-40"
        >
          Next
        </button>
      </div>
    </div>
  );
}
