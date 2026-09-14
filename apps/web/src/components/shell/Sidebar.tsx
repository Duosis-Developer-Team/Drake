"use client";

/**
 * The primary navigation rail.
 *
 * Collapsible, and the collapsed state persists — an operator who works at
 * 1280px keeps the extra 168px across reloads. Collapsed, the rail shows the
 * D-and-serpent lockup and icon-only entries whose accessible names and
 * tooltips still carry the full label.
 *
 * The active entry is marked three ways — a rail, a surface and a weight
 * change — because on a dense screen an active state carried by colour alone
 * is easy to miss and impossible to see in greyscale.
 *
 * The rail is obsidian in both themes (`bg-sidebar` and its siblings, from
 * `--sidebar-*` in globals.css) — the one deliberate exception to "every
 * surface flips with the theme" (remake brief §6.2). It is not `bg-surface`.
 */

import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { DrakeMark, DrakeWordmark } from "@/components/shell/Brand";
import { NAVIGATION, activeNavHref } from "@/lib/navigation";
import { useSession } from "@/lib/session";

const COLLAPSE_KEY = "drake-sidebar-collapsed";

export function useSidebarCollapse(): [boolean, () => void] {
  const [collapsed, setCollapsed] = useState(false);

  // Read after mount: the server has no way to know this preference, so
  // rendering it during SSR would guarantee a hydration mismatch.
  useEffect(() => {
    try {
      setCollapsed(localStorage.getItem(COLLAPSE_KEY) === "1");
    } catch {
      // Storage unavailable; the rail simply starts expanded.
    }
  }, []);

  const toggle = useCallback(() => {
    setCollapsed((previous) => {
      const next = !previous;
      try {
        localStorage.setItem(COLLAPSE_KEY, next ? "1" : "0");
      } catch {
        // Best effort.
      }
      return next;
    });
  }, []);

  return [collapsed, toggle];
}

export function Sidebar({
  onNavigate,
  collapsed = false,
  onToggleCollapse,
  footer,
}: {
  onNavigate?: () => void;
  collapsed?: boolean;
  onToggleCollapse?: () => void;
  footer?: React.ReactNode;
}) {
  const pathname = usePathname() || "/";
  const { hasPermission } = useSession();
  const active = activeNavHref(pathname);

  return (
    <div
      className="flex h-full min-h-0 flex-col gap-3 rounded-[1.75rem] border border-sidebar-border bg-sidebar p-2.5"
      data-testid="sidebar"
    >
      <div
        className={`flex h-14 shrink-0 items-center ${collapsed ? "justify-center" : "gap-2 pr-1 pl-3"}`}
      >
        <Link
          href="/"
          onClick={onNavigate}
          aria-label="Drake home"
          className="flex min-w-0 items-center rounded"
        >
          {collapsed ? (
            <DrakeMark height={22} />
          ) : (
            <DrakeWordmark height={22} />
          )}
        </Link>
        {onToggleCollapse && !collapsed ? (
          <button
            type="button"
            onClick={onToggleCollapse}
            aria-label="Collapse navigation"
            title="Collapse navigation"
            aria-expanded
            className="ml-auto inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-sidebar-ink-muted transition-colors hover:bg-sidebar-hover hover:text-sidebar-ink"
          >
            <PanelLeftClose className="h-4 w-4" aria-hidden />
          </button>
        ) : null}
      </div>

      {onToggleCollapse && collapsed ? (
        <button
          type="button"
          onClick={onToggleCollapse}
          aria-label="Expand navigation"
          title="Expand navigation"
          aria-expanded={false}
          className="mx-auto inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-sidebar-ink-muted transition-colors hover:bg-sidebar-hover hover:text-sidebar-ink"
        >
          <PanelLeftOpen className="h-4 w-4" aria-hidden />
        </button>
      ) : null}

      {/* The nav sits on its own inset slab, the way the reference groups its
          menu — the rail reads as layered, not as a flat list on black. */}
      <nav
        aria-label="Primary"
        className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto rounded-[1.375rem] border border-sidebar-border bg-sidebar-nav p-2 [scrollbar-width:none]"
      >
        {NAVIGATION.map((group) => {
          const items = group.items.filter(
            (item) =>
              !item.anyPermission ||
              item.anyPermission.some((permission) => hasPermission(permission)),
          );
          if (items.length === 0) return null;
          return (
            <div key={group.key} className="mb-3 last:mb-0">
              {collapsed ? (
                <div aria-hidden className="mx-3 my-2 border-t border-sidebar-border" />
              ) : (
                <p
                  aria-hidden
                  className="px-3 pt-2 pb-1.5 text-[0.625rem] font-semibold tracking-[0.12em] text-sidebar-ink-muted uppercase"
                >
                  {group.label}
                </p>
              )}
              {/* The group name lives on the list, not on a heading: these sit
                  above the page's h1 and would put the outline out of order. */}
              <ul className="space-y-1" aria-label={group.label}>
                {items.map((item) => {
                  const isActive = active === item.href;
                  const Icon = item.icon;
                  return (
                    <li key={item.href}>
                      <Link
                        href={item.href}
                        onClick={onNavigate}
                        aria-current={isActive ? "page" : undefined}
                        title={collapsed ? item.label : undefined}
                        className={`group relative flex h-11 items-center rounded-full text-body transition-colors duration-[var(--duration-micro)] ${
                          collapsed ? "justify-center" : "gap-3 pr-3 pl-1.5"
                        } ${
                          isActive
                            ? "bg-sidebar-selected font-semibold text-sidebar-ink"
                            : "text-sidebar-ink-muted hover:bg-sidebar-hover hover:text-sidebar-ink"
                        }`}
                      >
                        <span
                          aria-hidden
                          className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full transition-colors ${
                            isActive
                              ? "bg-sidebar-bubble-active text-sidebar-bubble-active-ink"
                              : "bg-sidebar-bubble"
                          }`}
                        >
                          <Icon className="h-4 w-4" aria-hidden />
                        </span>
                        {collapsed ? (
                          <span className="sr-only">{item.label}</span>
                        ) : (
                          <span className="truncate">{item.label}</span>
                        )}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          );
        })}
      </nav>

      {footer ? <div className="shrink-0">{footer}</div> : null}
    </div>
  );
}
