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

import { LanguageControl } from "@/components/shell/LanguageControl";
import { Sidebar, useSidebarCollapse } from "@/components/shell/Sidebar";
import { ThemeControl } from "@/components/shell/ThemeControl";
import { SignedOut } from "@/components/shell/SignedOut";
import { TopBar } from "@/components/shell/TopBar";
import { LoadingSkeleton } from "@/components/ui/states";
import { useDismissable, useScrollLock } from "@/components/ui/overlay";
import { LocaleProvider, useT } from "@/lib/i18n";
import { SessionProvider, useSession } from "@/lib/session";

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <LocaleProvider>
      <SessionProvider>
        <SessionGate>{children}</SessionGate>
      </SessionProvider>
    </LocaleProvider>
  );
}

function SessionGate({ children }: { children: React.ReactNode }) {
  const { state } = useSession();
  const t = useT("shell");

  if (state.status === "loading") {
    return (
      <div
        data-testid="session-loading"
        aria-busy="true"
        className="flex min-h-screen items-center justify-center px-6"
      >
        <div className="w-72">
          <LoadingSkeleton rows={3} label={t("session.checking")} />
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
  const t = useT("shell");
  const common = useT("common");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [collapsed, toggleCollapse] = useSidebarCollapse();
  const openDrawer = useCallback(() => setDrawerOpen(true), []);
  const closeDrawer = useCallback(() => setDrawerOpen(false), []);
  const drawerRef = useDismissable<HTMLDivElement>({ open: drawerOpen, onClose: closeDrawer });
  useScrollLock(drawerOpen);

  return (
    <div className="flex min-h-screen gap-3 bg-frame p-2 sm:p-3 lg:gap-4 lg:p-4">
      <a
        href="#main"
        className="sr-only rounded-control bg-brand px-3 py-2 text-body font-medium text-ink-inverse focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50"
      >
        {common("a11y.skipToContent")}
      </a>

      {/* PayFlow frame: the rail is its own rounded slab floating on the
          bezel, and the page is a second, larger slab beside it. */}
      <aside
        className={`sticky top-4 hidden h-[calc(100vh-2rem)] shrink-0 self-start transition-[width] duration-[var(--duration-surface)] ease-[var(--ease-standard)] lg:block ${
          collapsed ? "w-[var(--sidebar-width-collapsed)]" : "w-[var(--sidebar-width-expanded)]"
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
            aria-label={t("sidebar.navigation")}
            className="absolute inset-y-2 left-2 flex w-72 max-w-[85vw] flex-col shadow-overlay motion-safe:animate-[slide-in-left_240ms_var(--ease-entrance)]"
          >
            <Sidebar onNavigate={closeDrawer} footer={<ShellFooter />} />
          </div>
        </div>
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col rounded-[1.5rem] border border-border bg-canvas lg:rounded-[2rem] shadow-panel">
        <TopBar onOpenSidebar={openDrawer} />
        {/* The sideways-scroll backstop lives on `html` in globals.css; this
            keeps a wide panel from stretching the column it sits in. */}
        <main id="main" tabIndex={-1} className="min-w-0 flex-1 outline-none">
          {children}
        </main>
      </div>
    </div>
  );
}

/**
 * What the rail says about the session, plus theme on mobile only.
 *
 * The theme control appeared here AND in the top bar, so on a wide screen
 * one setting had two controls. It is now `md:hidden` here and `hidden
 * md:block` there: exactly one is reachable at any width.
 *
 * Deleting it outright was the obvious move and was wrong — the top bar
 * hides its copy below md, so narrow screens would have had no way to
 * change theme at all. An e2e case records that placement as deliberate:
 * on mobile the control belongs in the drawer with the other settings.
 *
 * The wrapping `dark` class is a local scope, not a real theme toggle: it
 * resolves every `dark:`-aware utility underneath (the segmented control's
 * own surface/border/text, `text-ink-muted`) to their dark values, so a
 * light-theme session does not float a white control on the obsidian rail.
 * `--sidebar-*` tokens are already theme-invariant and need no such scope.
 */
function ShellFooter() {
  const { state } = useSession();
  const t = useT("shell");
  if (state.status !== "authenticated") return null;
  const scopeCount = Object.keys(state.me.scopes).length;
  const { identity } = state.me;
  const initial = (identity.display_name || "?").charAt(0).toUpperCase();
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <div className="md:hidden">
          <ThemeControl />
        </div>
        <div className="lg:hidden">
          <LanguageControl />
        </div>
      </div>
      <div className="flex items-center gap-3 rounded-[1.25rem] border border-sidebar-border bg-sidebar-nav p-2.5">
        <span
          aria-hidden
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-sidebar-bubble-active text-body font-semibold text-sidebar-bubble-active-ink"
        >
          {initial}
        </span>
        <div className="min-w-0">
          <p className="truncate text-body font-medium text-sidebar-ink">{identity.display_name}</p>
          <p className="truncate text-micro text-sidebar-ink-muted">
            {t("sidebar.scopes", { count: scopeCount })}
          </p>
        </div>
      </div>
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
  // `@container/page` drives the shared column system in globals.css.
  return (
    <div className={`@container/page mx-auto w-full px-4 pt-4 pb-12 lg:px-10 ${max}`}>
      {children}
    </div>
  );
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
    <div className="mb-8">
      <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="min-w-0 truncate text-[1.75rem] leading-9 font-semibold tracking-[-0.02em] text-ink">{title}</h1>
            {status}
          </div>
          {description ? (
            <p className="mt-1 max-w-3xl text-body text-ink-muted">{description}</p>
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
