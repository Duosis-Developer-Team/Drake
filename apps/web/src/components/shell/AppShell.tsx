"use client";

import { X } from "lucide-react";
import { useCallback, useState } from "react";

import { Header } from "@/components/shell/Header";
import { Sidebar } from "@/components/shell/Sidebar";
import { SignedOut } from "@/components/shell/SignedOut";
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
        className="flex min-h-screen items-center justify-center"
      >
        <div className="w-72 space-y-3">
          <div className="h-8 w-8 animate-pulse rounded-lg bg-surface-sunken" />
          <div className="h-4 w-2/3 animate-pulse rounded bg-surface-sunken" />
          <div className="h-4 w-1/2 animate-pulse rounded bg-surface-sunken" />
          <span className="sr-only">Checking session</span>
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
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const openSidebar = useCallback(() => setSidebarOpen(true), []);
  const closeSidebar = useCallback(() => setSidebarOpen(false), []);

  return (
    <div className="flex min-h-screen">
      {/* Static sidebar for large screens */}
      <aside className="hidden w-64 shrink-0 border-r border-border bg-surface lg:block">
        <Sidebar />
      </aside>

      {/* Slide-over sidebar for small screens */}
      {sidebarOpen ? (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true">
          <button
            type="button"
            aria-label="Close navigation"
            onClick={closeSidebar}
            className="absolute inset-0 bg-black/40"
          />
          <div className="absolute inset-y-0 left-0 flex w-72 max-w-[85vw] flex-col bg-surface shadow-xl">
            <button
              type="button"
              onClick={closeSidebar}
              aria-label="Close navigation"
              className="absolute top-4 right-4 z-10 inline-flex h-8 w-8 items-center justify-center rounded-lg border border-border text-ink-secondary"
            >
              <X className="h-4 w-4" />
            </button>
            <Sidebar onNavigate={closeSidebar} />
          </div>
        </div>
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col">
        <Header onOpenSidebar={openSidebar} />
        <main className="flex-1 px-4 py-6 lg:px-8">{children}</main>
      </div>
    </div>
  );
}
