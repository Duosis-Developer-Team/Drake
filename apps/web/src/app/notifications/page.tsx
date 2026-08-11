"use client";

/**
 * The in-app inbox.
 *
 * Every row is text the server composed, and every row is one the reader
 * may still open: the API omits notifications whose incident has left
 * their scope entirely. Rendering them as redacted placeholders would
 * still answer "something exists here you may not see", which is the
 * enumeration the scope filter exists to prevent.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { PageFrame } from "@/components/shell/AppShell";

import { DataState } from "@/components/state/DataState";
import { Card } from "@/components/ui/Card";
import { ApiError } from "@/lib/api";
import { useSession } from "@/lib/session";
import {
  EVENT_TYPE_LABELS,
  fetchInbox,
  markRead,
  type InboxItem,
  type InboxPage,
} from "@/lib/notifications";

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string; denied: boolean }
  | { kind: "ready"; data: InboxPage };

function NotificationRow({
  item,
  onRead,
  busy,
}: {
  item: InboxItem;
  onRead: (id: string) => void;
  busy: boolean;
}) {
  return (
    <li
      className={`flex flex-wrap items-start gap-3 border-t border-border py-3 ${
        item.read_at ? "" : "bg-surface-sunken/40"
      }`}
      data-testid={`notification-${item.id}`}
    >
      <span
        aria-hidden
        className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${
          item.read_at ? "bg-transparent" : "bg-accent"
        }`}
      />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-ink">{item.title}</p>
        <p className="mt-0.5 text-xs text-ink-secondary">{item.body}</p>
        <p className="mt-1 text-[11px] text-ink-muted">
          <span className="mr-2">{EVENT_TYPE_LABELS[item.event_type]}</span>
          <time className="font-mono">{item.created_at}</time>
          {item.read_at ? <span className="ml-2">read</span> : null}
        </p>
      </div>
      <div className="flex items-center gap-2">
        {/* Every listed row is one the reader may still open: the API
            filters out notifications whose incident has left their scope
            rather than returning a redacted placeholder. */}
        <Link
          href={item.target_path}
          className="text-xs font-medium text-ink-secondary underline hover:text-ink"
        >
          Open incident
        </Link>
        {item.read_at ? null : (
          <button
            type="button"
            disabled={busy}
            onClick={() => onRead(item.id)}
            className="rounded-lg border border-border px-2 py-1 text-[11px] font-medium text-ink-secondary hover:bg-surface-sunken disabled:opacity-50"
          >
            Mark read
          </button>
        )}
      </div>
    </li>
  );
}

export default function NotificationsPage() {
  const { state: session } = useSession();
  const csrfToken = session.status === "authenticated" ? session.me.csrf_token : null;
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [state, setState] = useState<State>({ kind: "loading" });
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    fetchInbox({ unreadOnly })
      .then((data) => {
        if (!cancelled) setState({ kind: "ready", data });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        const denied = error instanceof ApiError && error.status === 403;
        setState({
          kind: "error",
          message: error instanceof ApiError ? error.message : "request failed",
          denied,
        });
      });
    return () => {
      cancelled = true;
    };
  }, [unreadOnly]);

  useEffect(() => load(), [load]);

  const read = async (ids: string[]) => {
    if (!csrfToken || ids.length === 0) return;
    setBusy(true);
    try {
      await markRead(csrfToken, ids);
      load();
    } finally {
      setBusy(false);
    }
  };

  const unreadIds =
    state.kind === "ready"
      ? state.data.items.filter((item) => !item.read_at).map((item) => item.id)
      : [];

  return (
    <PageFrame>
      <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-title font-semibold text-ink">Notifications</h1>
          <p className="mt-1 max-w-3xl text-caption text-ink-secondary">
            Incidents you were routed by a notification policy. Drake writes these; nothing
            here was composed by another user.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div
            role="group"
            aria-label="View"
            className="inline-flex items-center gap-0.5 rounded-lg border border-border p-0.5"
          >
            {[
              { key: false, label: "All" },
              { key: true, label: "Unread" },
            ].map((option) => (
              <button
                key={option.label}
                type="button"
                aria-pressed={unreadOnly === option.key}
                onClick={() => setUnreadOnly(option.key)}
                className={`rounded-md px-2 py-1 text-xs font-medium transition-colors ${
                  unreadOnly === option.key
                    ? "bg-accent text-white"
                    : "text-ink-secondary hover:bg-surface-sunken"
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>
          {unreadIds.length > 0 ? (
            <button
              type="button"
              disabled={busy}
              onClick={() => read(unreadIds)}
              className="rounded-lg border border-border px-2.5 py-1.5 text-xs font-medium text-ink-secondary hover:bg-surface-sunken disabled:opacity-50"
            >
              Mark visible read
            </button>
          ) : null}
        </div>
      </div>

      {state.kind === "loading" ? <DataState kind="loading" /> : null}
      {state.kind === "error" ? (
        <Card>
          {state.denied ? (
            <DataState
              kind="permission-denied"
              description="Your session cannot read notifications."
            />
          ) : (
            <DataState kind="error" description={state.message} onRetry={load} />
          )}
        </Card>
      ) : null}
      {state.kind === "ready" && state.data.items.length === 0 ? (
        <Card>
          <DataState
            kind="empty"
            title={unreadOnly ? "Nothing unread" : "No notifications"}
            description="Drake sends these when an incident matches a notification policy you are a destination for."
          />
        </Card>
      ) : null}
      {state.kind === "ready" && state.data.items.length > 0 ? (
        <Card>
          <ul data-testid="inbox-list">
            {state.data.items.map((item) => (
              <NotificationRow
                key={item.id}
                item={item}
                busy={busy}
                onRead={(id) => read([id])}
              />
            ))}
          </ul>
        </Card>
      ) : null}
      </div>
    </PageFrame>
  );
}
