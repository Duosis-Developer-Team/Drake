"use client";

/**
 * The application shell.
 *
 * Layout: the DOCUMENT scrolls, and the rail and top bar are sticky within
 * it. The obvious alternative — a viewport-height flex box with an
 * independently scrolling `<main>` — keeps the chrome in place too, but it
 * puts the page content in a nested scroller, and that breaks
 * find-in-page's scroll-into-view, full-page screenshots, and the mobile
 * address-bar collapse. Sticky positioning gets the same fixed chrome with
 * none of that.
 *
 * The mobile drawer is a real dialog — focus trapped, Escape closes, focus
 * returns to the trigger — because it is the only way to navigate below
 * 1024px and losing focus inside it strands a keyboard user.
 */

import { useCallback, useState } from "react";

import { Sidebar, useSidebarCollapse } from "@/components/shell/Sidebar";
import { SignedOut } from "@/components/shell/SignedOut";
import { TopBar } from "@/components/shell/TopBar";
import { LoadingSkeleton } from "@/components/ui/states";
import { useDismissable, useScrollLock } from "@/components/ui/overlay";
import { SessionProvider, useSession } from "@/lib/session";

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <SessionProvider>
      <SessionGate>{children}</SessionGate>
    </SessionProvider>
  );
}

function SessionGate({ children }: { children: React.ReactNode }) {
  const { state } = useSession();

  if (state.status === "loading") {
    return (
      <div
        data-testid="session-loading"
        aria-busy="true"
        className="flex min-h-screen items-center justify-center px-6"
      >
        <div className="w-72">
          <LoadingSkeleton rows={3} label="Checking session" />
        </div>
      </div>
    );
  }
  if (state.status === "signed-out") return <SignedOut variant="signed-out" />;
  if (state.status === "expired") return <SignedOut variant="expired" />;
  if (state.status === "unavailable") return <SignedOut variant="unavailable" />;

  return <AuthenticatedShell>{children}</AuthenticatedShell>;
}

function AuthenticatedShell({ children }: { children: React.ReactNode }) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [collapsed, toggleCollapse] = useSidebarCollapse();
  const openDrawer = useCallback(() => setDrawerOpen(true), []);
  const closeDrawer = useCallback(() => setDrawerOpen(false), []);
  const drawerRef = useDismissable<HTMLDivElement>({ open: drawerOpen, onClose: closeDrawer });
  useScrollLock(drawerOpen);

  return (
    <div className="flex min-h-screen">
      <a
        href="#main"
        className="sr-only rounded-control bg-brand px-3 py-2 text-body font-medium text-ink-inverse focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50"
      >
        Skip to content
      </a>

      <aside
        className={`sticky top-0 hidden h-screen shrink-0 self-start border-r border-border transition-[width] duration-[var(--duration-surface)] ease-[var(--ease-standard)] lg:block ${
          collapsed ? "w-14" : "w-60"
        }`}
      >
        <Sidebar
          collapsed={collapsed}
          onToggleCollapse={toggleCollapse}
          footer={collapsed ? null : <ShellFooter />}
        />
      </aside>

      {drawerOpen ? (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div aria-hidden className="absolute inset-0 bg-[var(--scrim)]" />
          <div
            ref={drawerRef}
            role="dialog"
            aria-modal="true"
            aria-label="Navigation"
            className="absolute inset-y-0 left-0 flex w-64 max-w-[85vw] flex-col border-r border-border shadow-overlay motion-safe:animate-[slide-in-left_240ms_var(--ease-entrance)]"
          >
            <Sidebar onNavigate={closeDrawer} footer={<ShellFooter />} />
          </div>
        </div>
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar onOpenSidebar={openDrawer} />
        {/* The sideways-scroll backstop lives on `html` in globals.css; this
            keeps a wide panel from stretching the column it sits in. */}
        <main id="main" tabIndex={-1} className="min-w-0 flex-1 bg-canvas">
          {children}
        </main>
      </div>
    </div>
  );
}

/**
 * What the rail says about the session.
 *
 * The theme control used to live here as well, duplicating the one in the
 * top bar. Two controls for one setting is worse than either alone: it
 * invites the question of whether they do the same thing.
 */
function ShellFooter() {
  const { state } = useSession();
  const scopeCount = state.status === "authenticated" ? Object.keys(state.me.scopes).length : 0;
  return (
    <div className="px-3 py-3">
      <p className="text-micro text-ink-muted">
        {scopeCount === 1 ? "1 authorized scope" : `${scopeCount} authorized scopes`}
      </p>
    </div>
  );
}

/**
 * The page frame.
 *
 * `width` is per-page rather than global: a dense table or a telemetry grid
 * uses the whole viewport, and a settings form does not — a 2560px-wide form
 * is unreadable. Everything else defaults to a measured column.
 */
export function PageFrame({
  children,
  width = "default",
}: {
  children: React.ReactNode;
  width?: "default" | "wide" | "narrow";
}) {
  const max =
    width === "wide" ? "max-w-none" : width === "narrow" ? "max-w-3xl" : "max-w-[110rem]";
  return <div className={`mx-auto w-full px-4 py-5 lg:px-6 ${max}`}>{children}</div>;
}

/**
 * A page's title block.
 *
 * The title is one line and stays out of the way — a monitoring page's job is
 * the data below it, not its own name. `status` is where the page's overall
 * state goes, so a reader sees "degraded" beside the title rather than having
 * to find it in the third panel down.
 */
export function PageHeader({
  title,
  description,
  status,
  meta,
  actions,
  tabs,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  status?: React.ReactNode;
  meta?: React.ReactNode;
  actions?: React.ReactNode;
  tabs?: React.ReactNode;
}) {
  return (
    <div className="mb-5">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="min-w-0 truncate text-title font-semibold text-ink">{title}</h1>
            {status}
          </div>
          {description ? (
            <p className="mt-1 max-w-3xl text-caption text-ink-secondary">{description}</p>
          ) : null}
          {meta ? (
            <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-micro text-ink-muted">
              {meta}
            </div>
          ) : null}
        </div>
        {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
      {tabs ? <div className="mt-4">{tabs}</div> : null}
    </div>
  );
}
