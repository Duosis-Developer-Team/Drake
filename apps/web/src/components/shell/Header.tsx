"use client";

import { Menu } from "lucide-react";
import { usePathname } from "next/navigation";
import { Suspense } from "react";

import { CatalogSearch } from "@/components/shell/CatalogSearch";
import { IdentityMenu } from "@/components/shell/IdentityMenu";
import { NotificationBell } from "@/components/shell/NotificationBell";
import { ThemeToggle } from "@/components/shell/ThemeToggle";
import { TimeRangeControl } from "@/components/telemetry/TimeRangeControl";

/**
 * Top bar: scope context and search everywhere; the time-range control is
 * REAL and appears only on telemetry-capable screens (project overview and
 * service detail) — catalog/admin screens show no misleading control.
 */

function isTelemetryRoute(pathname: string): boolean {
  const segments = pathname.split("/").filter(Boolean);
  if (segments[0] !== "projects") return false;
  // /projects/{id} (overview with environment selector) or a service detail.
  return segments.length === 2 || segments.includes("services");
}

export function Header({ onOpenSidebar }: { onOpenSidebar: () => void }) {
  const pathname = usePathname() || "/";
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

      {/*
        `shrink-0` so the controls keep their size, and the breadcrumb
        beside them is allowed to shrink instead. At exactly 768px a
        two-segment route — the first of which is `/onboarding/{id}` — put
        the breadcrumb, its "detail" chip and this cluster 19px past the
        viewport, and the whole page scrolled sideways.
      */}
      <div className="ml-auto flex shrink-0 items-center gap-2">
        <CatalogSearch />
        {isTelemetryRoute(pathname) ? (
          <Suspense fallback={null}>
            <TimeRangeControl />
          </Suspense>
        ) : null}
        <NotificationBell />
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
    <div className="hidden min-w-0 items-center gap-2 text-sm text-ink-muted sm:flex">
      <span className="shrink-0 font-medium text-ink-secondary">Organization</span>
      <span aria-hidden>/</span>
      <span className="truncate text-ink">{label}</span>
      {depth > 1 ? (
        <span className="shrink-0 rounded border border-border px-1.5 py-0.5 text-[10px] uppercase tracking-wide">
          detail
        </span>
      ) : null}
    </div>
  );
}
