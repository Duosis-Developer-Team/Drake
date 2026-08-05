"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { NAVIGATION } from "@/lib/navigation";

export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-16 items-center gap-3 border-b border-border px-5">
        <div
          aria-hidden
          className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent font-semibold text-white"
        >
          D
        </div>
        <div className="leading-tight">
          <div className="text-sm font-semibold tracking-wide text-ink">Drake</div>
          <div className="text-[11px] text-ink-muted">Operations control plane</div>
        </div>
      </div>

      <nav aria-label="Primary" className="flex-1 overflow-y-auto px-3 py-4">
        <ul className="space-y-1">
          {NAVIGATION.map((item) => {
            const active = pathname === item.href;
            const Icon = item.icon;
            return (
              <li key={item.href}>
                {item.enabled ? (
                  <Link
                    href={item.href}
                    onClick={onNavigate}
                    aria-current={active ? "page" : undefined}
                    className={`group flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors focus-visible:outline-2 focus-visible:outline-accent ${
                      active
                        ? "bg-accent-soft font-medium text-accent-ink"
                        : "text-ink-secondary hover:bg-surface-sunken hover:text-ink"
                    }`}
                  >
                    <Icon className="h-4 w-4 shrink-0" aria-hidden />
                    <span className="truncate">{item.label}</span>
                  </Link>
                ) : (
                  <span
                    aria-disabled
                    title="Available in a later sprint"
                    className="flex cursor-not-allowed items-center gap-3 rounded-lg px-3 py-2 text-sm text-ink-muted"
                  >
                    <Icon className="h-4 w-4 shrink-0" aria-hidden />
                    <span className="truncate">{item.label}</span>
                    <span className="ml-auto rounded border border-border px-1.5 py-0.5 text-[10px] uppercase tracking-wide">
                      soon
                    </span>
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      </nav>

      <div className="border-t border-border px-5 py-3 text-[11px] text-ink-muted">
        <div className="flex items-center justify-between">
          <span className="font-mono">v0.1.0</span>
          <span className="rounded border border-border px-1.5 py-0.5 uppercase tracking-wide">
            local
          </span>
        </div>
      </div>
    </div>
  );
}
