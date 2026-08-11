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
    <div className="flex h-full min-h-0 flex-col bg-surface">
      <div
        className={`flex h-14 shrink-0 items-center border-b border-border ${
          collapsed ? "justify-center px-2" : "gap-2 px-4"
        }`}
      >
        <Link
          href="/"
          onClick={onNavigate}
          aria-label="Drake home"
          className="flex min-w-0 items-center rounded"
        >
          {collapsed ? <DrakeMark height={22} /> : <DrakeWordmark height={22} />}
        </Link>
        {onToggleCollapse && !collapsed ? (
          <button
            type="button"
            onClick={onToggleCollapse}
            aria-label="Collapse navigation"
            title="Collapse navigation"
            aria-expanded
            className="ml-auto inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-control text-ink-muted transition-colors hover:bg-surface-hover hover:text-ink"
          >
            <PanelLeftClose className="h-4 w-4" aria-hidden />
          </button>
        ) : null}
      </div>

      <nav
        aria-label="Primary"
        className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-2 py-3"
      >
        {NAVIGATION.map((group) => {
          const items = group.items.filter(
            (item) =>
              !item.anyPermission ||
              item.anyPermission.some((permission) => hasPermission(permission)),
          );
          if (items.length === 0) return null;
          return (
            <div key={group.key} className="mb-4 last:mb-0">
              {collapsed ? (
                <div aria-hidden className="mx-2 mb-2 border-t border-border first:border-0" />
              ) : (
                <h2 className="mb-1 px-2 text-micro font-semibold tracking-wide text-ink-muted uppercase">
                  {group.label}
                </h2>
              )}
              <ul className="space-y-0.5" aria-label={collapsed ? group.label : undefined}>
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
                        className={`group relative flex items-center rounded-control text-body transition-colors duration-[var(--duration-micro)] ${
                          collapsed ? "h-9 justify-center px-0" : "h-9 gap-2.5 px-2.5"
                        } ${
                          isActive
                            ? "bg-surface-selected font-semibold text-brand"
                            : "text-ink-secondary hover:bg-surface-hover hover:text-ink"
                        }`}
                      >
                        {isActive ? (
                          <span
                            aria-hidden
                            className="absolute inset-y-1.5 left-0 w-0.5 rounded-full bg-brand"
                          />
                        ) : null}
                        <Icon className="h-4 w-4 shrink-0" aria-hidden />
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

      <div className="shrink-0 border-t border-border">
        {onToggleCollapse && collapsed ? (
          <button
            type="button"
            onClick={onToggleCollapse}
            aria-label="Expand navigation"
            title="Expand navigation"
            aria-expanded={false}
            className="flex h-10 w-full items-center justify-center text-ink-muted transition-colors hover:bg-surface-hover hover:text-ink"
          >
            <PanelLeftOpen className="h-4 w-4" aria-hidden />
          </button>
        ) : null}
        {footer}
      </div>
    </div>
  );
}
