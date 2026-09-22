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
import { LanguageControl } from "@/components/shell/LanguageControl";
import { NotificationBell } from "@/components/shell/NotificationBell";
import { ThemeControl } from "@/components/shell/ThemeControl";
import { TimeRangeControl } from "@/components/telemetry/TimeRangeControl";
import { useT } from "@/lib/i18n";

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
  const t = useT("shell");

  return (
    <header className="sticky top-0 z-30 flex h-[4.5rem] shrink-0 items-center gap-3 rounded-t-[1.5rem] bg-canvas/85 px-3 backdrop-blur-md lg:rounded-t-[2rem] lg:px-10">
      <button
        type="button"
        onClick={onOpenSidebar}
        aria-label={t("sidebar.openNavigation")}
        className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-full border border-border bg-surface text-ink-secondary transition-colors hover:bg-surface-hover lg:hidden"
      >
        <Menu className="h-4 w-4" aria-hidden />
      </button>

      <div className="flex shrink-0 items-center">
        <CatalogSearch />
      </div>

      <div className="hidden min-w-0 flex-1 px-2 sm:block">
        <Breadcrumbs />
      </div>

      {/*
        `shrink-0` on the control cluster, with the breadcrumb allowed to
        shrink instead. At 768px a deep route otherwise pushed this group past
        the viewport and the whole page scrolled sideways.
      */}
      <div className="ml-auto flex shrink-0 items-center gap-2">
        {telemetry ? (
          <Suspense fallback={null}>
            <TimeRangeControl />
          </Suspense>
        ) : null}
        <div className="hidden items-center gap-2 md:flex">
          <ThemeControl compact />
          <LanguageControl compact />
        </div>
        <NotificationBell />
        <IdentityMenu />
      </div>
    </header>
  );
}
