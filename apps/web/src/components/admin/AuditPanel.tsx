"use client";

import { useCallback, useEffect, useState } from "react";

import { DataState } from "@/components/state/DataState";
import { StatusBadge } from "@/components/state/StatusBadge";
import { Card } from "@/components/ui/Card";
import { ApiError, apiGet } from "@/lib/api";

interface AuditEvent {
  id: string;
  occurred_at: string;
  actor_type: string;
  actor_id: string;
  action: string;
  scope_type: string | null;
  scope_ref: string | null;
  target_type: string | null;
  target_id: string | null;
  result: "success" | "failure" | "denied";
  correlation_id: string;
}

const RESULT_STATUS = {
  success: "healthy",
  failure: "critical",
  denied: "warning",
} as const;

export function AuditPanel() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [phase, setPhase] = useState<"loading" | "ready" | "error" | "loading-more">("loading");
  const [message, setMessage] = useState("");

  const loadPage = useCallback(async (nextCursor: string | null) => {
    setPhase(nextCursor ? "loading-more" : "loading");
    try {
      const query = nextCursor ? `&cursor=${encodeURIComponent(nextCursor)}` : "";
      const body = await apiGet<{ events: AuditEvent[]; next_cursor: string | null }>(
        `/v1/audit-events?limit=25${query}`,
      );
      setEvents((current) => (nextCursor ? [...current, ...body.events] : body.events));
      setCursor(body.next_cursor);
      setPhase("ready");
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : "request failed");
      setPhase("error");
    }
  }, []);

  useEffect(() => {
    void loadPage(null);
  }, [loadPage]);

  return (
    <Card title="Audit trail">
      {phase === "loading" ? <DataState kind="loading" /> : null}
      {phase === "error" ? (
        <DataState kind="error" description={message} onRetry={() => void loadPage(null)} />
      ) : null}
      {phase !== "loading" && phase !== "error" && events.length === 0 ? (
        <DataState kind="empty" title="No audit events in your scope" />
      ) : null}
      {events.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-sm" data-testid="audit-table">
            <thead>
              <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-ink-muted">
                <th className="px-2 py-2">Time (UTC)</th>
                <th className="px-2 py-2">Actor</th>
                <th className="px-2 py-2">Action</th>
                <th className="px-2 py-2">Scope</th>
                <th className="px-2 py-2">Result</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {events.map((event) => (
                <tr key={event.id}>
                  <td className="px-2 py-2 font-mono text-xs text-ink-secondary">
                    {event.occurred_at.replace("T", " ").slice(0, 19)}
                  </td>
                  <td className="px-2 py-2 font-mono text-xs text-ink-secondary">
                    {event.actor_type}
                  </td>
                  <td className="px-2 py-2 font-mono text-xs text-ink">{event.action}</td>
                  <td className="px-2 py-2 font-mono text-xs text-ink-secondary">
                    {event.scope_ref ?? "—"}
                  </td>
                  <td className="px-2 py-2">
                    <StatusBadge status={RESULT_STATUS[event.result]} label={event.result} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {cursor ? (
        <div className="mt-3 border-t border-border pt-3">
          <button
            type="button"
            onClick={() => void loadPage(cursor)}
            disabled={phase === "loading-more"}
            className="h-9 rounded-lg border border-border px-4 text-sm text-ink-secondary hover:bg-surface-sunken disabled:opacity-50"
          >
            {phase === "loading-more" ? "Loading…" : "Load more"}
          </button>
        </div>
      ) : null}
    </Card>
  );
}
