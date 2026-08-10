"use client";

/**
 * Unread badge in the top bar.
 *
 * The count comes from the API and is the caller's own — there is no
 * recipient parameter to point somewhere else. A failed poll shows no
 * badge rather than a stale one: a wrong number here is worse than none,
 * because people learn to trust it.
 */

import { Bell } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { fetchUnreadCount } from "@/lib/notifications";

const POLL_INTERVAL_MS = 60_000;

export function NotificationBell() {
  const [unread, setUnread] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      setUnread(await fetchUnreadCount());
    } catch {
      setUnread(null);
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [load]);

  const label =
    unread && unread > 0 ? `Notifications, ${unread} unread` : "Notifications";

  return (
    <Link
      href="/notifications"
      aria-label={label}
      data-testid="notification-bell"
      className="relative inline-flex h-9 w-9 items-center justify-center rounded-lg border border-border text-ink-secondary hover:bg-surface-sunken"
    >
      <Bell className="h-4 w-4" aria-hidden />
      {unread && unread > 0 ? (
        <span
          data-testid="unread-badge"
          className="absolute -right-1 -top-1 inline-flex min-w-4 items-center justify-center rounded-full bg-critical px-1 text-[10px] font-semibold text-white"
        >
          {unread > 99 ? "99+" : unread}
        </span>
      ) : null}
    </Link>
  );
}
