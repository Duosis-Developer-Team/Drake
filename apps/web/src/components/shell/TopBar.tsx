"use client";

/**
 * The top bar.
 *
 * What it does NOT do is the important part: the time-range control and the
 * refresh control appear only on routes that actually query a time window.
 * A time picker above a catalog table implies the table is a point-in-time
 * view of something that changes, and it is not — it is a list of records.
 * `isTelemetryRoute` is the whole of that policy.
 */

import { Menu } from "lucide-react";
import { usePathname } from "next/navigation";
import { Suspense } from "react";

import { Breadcrumbs } from "@/components/shell/Breadcrumbs";
import { CatalogSearch } from "@/components/shell/CatalogSearch";
import { IdentityMenu } from "@/components/shell/IdentityMenu";
import { NotificationBell } from "@/components/shell/NotificationBell";
import { ThemeControl } from "@/components/shell/ThemeControl";
import { TimeRangeControl } from "@/components/telemetry/TimeRangeControl";

/**
 * Whether this route reads a bounded time window from the URL.
 *
 * Project overview and service detail do; the environment page does through
 * its dashboard; nothing else in the product does. Keep this list honest —
 * every route added here gets a control that must actually drive a query.
 */
export function isTelemetryRoute(pathname: string): boolean {
  const segments = pathname.split("/").filter(Boolean);
  if (segments[0] !== "projects") return false;
  return segments.length === 2 || segments.includes("environments") || segments.includes("services");
}

export function TopBar({ onOpenSidebar }: { onOpenSidebar: () => void }) {
  const pathname = usePathname() || "/";
  const telemetry = isTelemetryRoute(pathname);

  return (
    <header className="sticky top-0 z-30 flex h-14 shrink-0 items-center gap-3 border-b border-border bg-surface px-3 lg:px-5">
      <button
        type="button"
        onClick={onOpenSidebar}
        aria-label="Open navigation"
        className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-control border border-border text-ink-secondary transition-colors hover:bg-surface-hover lg:hidden"
      >
        <Menu className="h-4 w-4" aria-hidden />
      </button>

      <div className="hidden min-w-0 flex-1 sm:block">
        <Breadcrumbs />
      </div>

      {/*
        `shrink-0` on the control cluster, with the breadcrumb allowed to
        shrink instead. At 768px a deep route otherwise pushed this group past
        the viewport and the whole page scrolled sideways.
      */}
      <div className="ml-auto flex shrink-0 items-center gap-2">
        <CatalogSearch />
        {telemetry ? (
          <Suspense fallback={null}>
            <TimeRangeControl />
          </Suspense>
        ) : null}
        <NotificationBell />
        <div className="hidden md:block">
          <ThemeControl compact />
        </div>
        <IdentityMenu />
      </div>
    </header>
  );
}
