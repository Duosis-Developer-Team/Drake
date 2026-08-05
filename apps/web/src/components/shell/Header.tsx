"use client";

import { Clock, Menu, Search } from "lucide-react";

import { ThemeToggle } from "@/components/shell/ThemeToggle";

/**
 * Top bar: scope context, search and time range are honest placeholders —
 * disabled and labeled as not configured until their sprints land. No fake
 * controls pretending to work.
 */
export function Header({ onOpenSidebar }: { onOpenSidebar: () => void }) {
  return (
    <header className="flex h-16 items-center gap-3 border-b border-border bg-surface px-4 lg:px-6">
      <button
        type="button"
        onClick={onOpenSidebar}
        aria-label="Open navigation"
        className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-border text-ink-secondary hover:bg-surface-sunken lg:hidden"
      >
        <Menu className="h-4 w-4" />
      </button>

      <div className="hidden items-center gap-2 text-sm text-ink-muted sm:flex">
        <span className="font-medium text-ink-secondary">Organization</span>
        <span aria-hidden>/</span>
        <span
          className="rounded border border-dashed border-border px-2 py-0.5 text-xs"
          title="Scope switching arrives with the catalog sprint"
        >
          scope not configured
        </span>
      </div>

      <div className="ml-auto flex items-center gap-2">
        <div
          className="hidden h-9 w-64 cursor-not-allowed items-center gap-2 rounded-lg border border-border bg-surface-sunken px-3 text-sm text-ink-muted md:flex"
          title="Search arrives with the catalog sprint"
        >
          <Search className="h-4 w-4" aria-hidden />
          <span>Search — not configured</span>
        </div>
        <button
          type="button"
          disabled
          title="Time range control arrives with the telemetry sprint"
          className="hidden h-9 cursor-not-allowed items-center gap-2 rounded-lg border border-border px-3 text-sm text-ink-muted sm:inline-flex"
        >
          <Clock className="h-4 w-4" aria-hidden />
          <span>Last 24h</span>
        </button>
        <ThemeToggle />
      </div>
    </header>
  );
}
