"use client";

import { LogOut } from "lucide-react";
import { useState } from "react";

import { useSession } from "@/lib/session";

export function IdentityMenu() {
  const { state, signOut } = useSession();
  const [open, setOpen] = useState(false);

  if (state.status !== "authenticated") return null;
  const { identity } = state.me;
  const initial = (identity.display_name || "?").charAt(0).toUpperCase();

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Account menu"
        className="flex h-11 items-center gap-2 rounded-full border border-border bg-surface pr-1.5 pl-1.5 transition-colors hover:bg-surface-hover lg:pr-4"
      >
        <span
          aria-hidden
          className="flex h-8 w-8 items-center justify-center rounded-full bg-[linear-gradient(135deg,#f1f1f1,#9a9a9a)] text-xs font-semibold text-[#161616]"
        >
          {initial}
        </span>
        <span className="hidden max-w-32 truncate text-sm text-ink lg:block">
          {identity.display_name}
        </span>
      </button>
      {open ? (
        <div
          role="menu"
          className="absolute right-0 z-30 mt-2 w-64 rounded-[1.25rem] border border-border bg-surface-2 p-2 shadow-overlay"
        >
          <div className="border-b border-border px-3 py-2">
            <p className="truncate text-sm font-medium text-ink">{identity.display_name}</p>
            <p className="truncate text-xs text-ink-muted">{identity.email}</p>
          </div>
          {state.me.groups_overage ? (
            <p className="px-3 py-2 text-xs text-warning">
              Group memberships could not be evaluated; group-based access is
              disabled for this session.
            </p>
          ) : null}
          <button
            type="button"
            role="menuitem"
            onClick={() => void signOut()}
            className="mt-1 flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-ink-secondary hover:bg-surface-sunken hover:text-ink"
          >
            <LogOut className="h-4 w-4" aria-hidden />
            Sign out
          </button>
        </div>
      ) : null}
    </div>
  );
}
