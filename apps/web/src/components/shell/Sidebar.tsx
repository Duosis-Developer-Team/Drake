"use client";

/**
 * The primary navigation rail.
 *
 * Collapsible, and the collapsed state persists — an operator who works at
 * 1280px keeps the extra width across reloads. Collapsed, the rail shows the
 * trace mark and icon-only entries whose accessible names and tooltips still
 * carry the full label.
 *
 * A flat rail beside the page, not a floating card: the rail is navigation,
 * and the page is where attention goes.
 */

import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { TracesisMark, TracesisWordmark } from "@/components/shell/Brand";
import { useT } from "@/lib/i18n";
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
  const t = useT("shell");

  return (
    <div
      className="flex h-full min-h-0 flex-col gap-2 border-r border-sidebar-border bg-sidebar px-2.5 pt-3 pb-2.5"
      data-testid="sidebar"
    >
      <div
        className={`flex h-10 shrink-0 items-center ${collapsed ? "justify-center" : "gap-2 pr-0.5 pl-2"}`}
      >
        <Link
          href="/"
          onClick={onNavigate}
          aria-label={t("sidebar.home")}
          className="flex min-w-0 items-center rounded-control"
        >
          {collapsed ? <TracesisMark height={13} /> : <TracesisWordmark size={19} />}
        </Link>
        {onToggleCollapse && !collapsed ? (
          <button
            type="button"
            onClick={onToggleCollapse}
            aria-label={t("sidebar.collapse")}
            title={t("sidebar.collapse")}
            aria-expanded
            className="ml-auto inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-control text-sidebar-ink-muted transition-colors hover:bg-sidebar-hover hover:text-sidebar-ink"
          >
            <PanelLeftClose className="h-4 w-4" aria-hidden />
          </button>
        ) : null}
      </div>

      {onToggleCollapse && collapsed ? (
        <button
          type="button"
          onClick={onToggleCollapse}
          aria-label={t("sidebar.expand")}
          title={t("sidebar.expand")}
          aria-expanded={false}
          className="mx-auto inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-control text-sidebar-ink-muted transition-colors hover:bg-sidebar-hover hover:text-sidebar-ink"
        >
          <PanelLeftOpen className="h-4 w-4" aria-hidden />
        </button>
      ) : null}

      <nav
        aria-label={t("sidebar.primary")}
        className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto [scrollbar-width:none]"
      >
        {NAVIGATION.map((group) => {
          const items = group.items.filter(
            (item) =>
              !item.anyPermission ||
              item.anyPermission.some((permission) => hasPermission(permission)),
          );
          if (items.length === 0) return null;
          const groupLabel = t.dyn("nav.group", group.key, group.label);
          return (
            <div key={group.key} className="pb-3">
              {group.untitled ? null : collapsed ? (
                <div aria-hidden className="mx-2 mt-1 mb-2 border-t border-sidebar-border" />
              ) : (
                <p
                  aria-hidden
                  className="px-2.5 pt-2 pb-1 font-mono text-[0.65625rem] font-medium tracking-[0.1em] text-sidebar-ink-muted uppercase"
                >
                  {groupLabel}
                </p>
              )}
              {/* The group name lives on the list, not on a heading: these sit
                  above the page's h1 and would put the outline out of order. */}
              <ul className="space-y-px" aria-label={groupLabel}>
                {items.map((item) => {
                  const isActive = active === item.href;
                  const Icon = item.icon;
                  const label = t.dyn("nav", item.key, item.label);
                  return (
                    <li key={item.href}>
                      <Link
                        href={item.href}
                        onClick={onNavigate}
                        aria-current={isActive ? "page" : undefined}
                        title={collapsed ? label : undefined}
                        className={`group relative flex h-9 items-center rounded-control text-[0.84375rem] transition-colors duration-[var(--duration-micro)] ${
                          collapsed ? "justify-center" : "gap-2.5 px-2.5"
                        } ${
                          isActive
                            ? "bg-sidebar-selected font-semibold text-sidebar-ink"
                            : "font-medium text-sidebar-ink-muted hover:bg-sidebar-hover hover:text-sidebar-ink"
                        }`}
                      >
                        {/* The active entry is marked three ways — surface,
                            weight and a cobalt icon — so it survives greyscale. */}
                        <Icon
                          className={`h-[1.0625rem] w-[1.0625rem] shrink-0 ${
                            isActive ? "text-sidebar-active-rail" : ""
                          }`}
                          strokeWidth={1.8}
                          aria-hidden
                        />
                        {collapsed ? (
                          <span className="sr-only">{label}</span>
                        ) : (
                          <span className="truncate">{label}</span>
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
