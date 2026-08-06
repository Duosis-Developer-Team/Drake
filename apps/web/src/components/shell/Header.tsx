"use client";

import { Clock, Menu } from "lucide-react";
import { usePathname } from "next/navigation";

import { CatalogSearch } from "@/components/shell/CatalogSearch";
import { IdentityMenu } from "@/components/shell/IdentityMenu";
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

      <RouteBreadcrumb />

      <div className="ml-auto flex items-center gap-2">
        <CatalogSearch />
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
        <IdentityMenu />
      </div>
    </header>
  );
}

const SEGMENT_LABELS: Record<string, string> = {
  projects: "Projects",
  clusters: "Clusters",
  integrations: "Integrations",
  environments: "Environments",
  services: "Services",
  admin: "Access Control",
};

/** Route-derived breadcrumb: honest scope context from the URL structure. */
function RouteBreadcrumb() {
  const pathname = usePathname();
  const segments = (pathname || "/").split("/").filter(Boolean);
  const label =
    segments.length === 0 ? "Command Center" : SEGMENT_LABELS[segments[0]] ?? segments[0];
  const depth = segments.length;
  return (
    <div className="hidden items-center gap-2 text-sm text-ink-muted sm:flex">
      <span className="font-medium text-ink-secondary">Organization</span>
      <span aria-hidden>/</span>
      <span className="text-ink">{label}</span>
      {depth > 1 ? (
        <span className="rounded border border-border px-1.5 py-0.5 text-[10px] uppercase tracking-wide">
          detail
        </span>
      ) : null}
    </div>
  );
}
