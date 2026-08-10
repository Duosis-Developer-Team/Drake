"use client";

/**
 * Delivery audit.
 *
 * State, attempt count, safe error code and timing. There is no column for
 * a target URL, a response body or an exception, because the API has none
 * — a receiver's error page can contain its own secrets, and an audit
 * screen is a very convenient place for them to end up.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { DataState } from "@/components/state/DataState";
import { StatusBadge, type HealthStatus } from "@/components/state/StatusBadge";
import { Card } from "@/components/ui/Card";
import { ApiError } from "@/lib/api";
import {
  DELIVERY_ERROR_LABELS,
  DELIVERY_STATE_LABELS,
  EVENT_TYPE_LABELS,
  fetchDeliveries,
  fetchDeliveryAttempts,
  type DeliveryAttempt,
  type DeliveryRow,
  type DeliveryState,
} from "@/lib/notifications";

const STATE_BADGE: Record<DeliveryState, HealthStatus> = {
  pending: "unknown",
  processing: "unknown",
  retrying: "warning",
  delivered: "healthy",
  dead_letter: "critical",
  suppressed: "maintenance",
};

const STATES: DeliveryState[] = [
  "pending",
  "retrying",
  "delivered",
  "dead_letter",
  "suppressed",
];

function AttemptTimeline({ deliveryId }: { deliveryId: string }) {
  const [attempts, setAttempts] = useState<DeliveryAttempt[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchDeliveryAttempts(deliveryId)
      .then((rows) => {
        if (!cancelled) setAttempts(rows);
      })
      .catch((problem: unknown) => {
        if (!cancelled) {
          setError(problem instanceof ApiError ? problem.message : "request failed");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [deliveryId]);

  if (error) return <DataState kind="error" description={error} />;
  if (attempts === null) return <DataState kind="loading" />;
  if (attempts.length === 0) {
    return (
      <p className="text-xs text-ink-secondary">
        Not attempted yet — this delivery is still queued.
      </p>
    );
  }
  return (
    <ol className="space-y-1.5" data-testid="attempt-timeline">
      {attempts.map((attempt) => (
        <li key={attempt.attempt_number} className="text-[11px] text-ink-secondary">
          <span className="font-medium text-ink">Attempt {attempt.attempt_number}</span>{" "}
          — {attempt.outcome}
          {attempt.http_status ? ` · HTTP ${attempt.http_status}` : ""}
          {attempt.error_code
            ? ` · ${DELIVERY_ERROR_LABELS[attempt.error_code] ?? attempt.error_code}`
            : ""}
          {attempt.duration_ms !== null ? ` · ${attempt.duration_ms} ms` : ""}
          {attempt.retry_at ? (
            <>
              {" "}
              · retry at <time className="font-mono">{attempt.retry_at}</time>
            </>
          ) : null}
        </li>
      ))}
    </ol>
  );
}

export default function NotificationDeliveriesPage() {
  const [state, setState] = useState<DeliveryState | "">("");
  const [rows, setRows] = useState<DeliveryRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  const load = useCallback(() => {
    let cancelled = false;
    setRows(null);
    fetchDeliveries(state || undefined)
      .then((items) => {
        if (!cancelled) {
          setRows(items);
          setError(null);
        }
      })
      .catch((problem: unknown) => {
        if (!cancelled) {
          setError(problem instanceof ApiError ? problem.message : "request failed");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [state]);

  useEffect(() => load(), [load]);

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-ink">
            Notification deliveries
          </h1>
          <p className="mt-1 text-sm text-ink-secondary">
            Outbound webhook deliveries. Delivery is at-least-once: every request carries a
            stable idempotency key so a receiver can collapse repeats.
          </p>
        </div>
        <label className="flex items-center gap-1.5 text-xs text-ink-secondary">
          State
          <select
            className="rounded-lg border border-border bg-surface px-2.5 py-1.5 text-xs text-ink"
            value={state}
            onChange={(event) => setState(event.target.value as DeliveryState | "")}
          >
            <option value="">Any</option>
            {STATES.map((value) => (
              <option key={value} value={value}>
                {DELIVERY_STATE_LABELS[value]}
              </option>
            ))}
          </select>
        </label>
      </div>

      {error ? (
        <Card>
          <DataState kind="error" description={error} onRetry={load} />
        </Card>
      ) : null}
      {rows === null && !error ? <DataState kind="loading" /> : null}
      {rows !== null && rows.length === 0 ? (
        <Card>
          <DataState
            kind="empty"
            title="No deliveries"
            description="No webhook deliveries match this filter in your authorized scope."
          />
        </Card>
      ) : null}

      {rows !== null && rows.length > 0 ? (
        <Card>
          <ul className="divide-y divide-border" data-testid="delivery-list">
            {rows.map((row) => (
              <li key={row.id} className="py-3" data-testid={`delivery-${row.id}`}>
                <div className="flex flex-wrap items-center gap-3">
                  <StatusBadge
                    status={STATE_BADGE[row.state]}
                    label={DELIVERY_STATE_LABELS[row.state]}
                  />
                  <Link
                    href={`/incidents/${row.incident_id}`}
                    className="text-sm font-medium text-ink hover:underline"
                  >
                    {row.incident_title}
                  </Link>
                  <span className="text-[11px] text-ink-secondary">
                    {EVENT_TYPE_LABELS[row.event_type] ?? row.event_type}
                  </span>
                  <span className="text-[11px] text-ink-muted">
                    → {row.destination_display_name}
                  </span>
                  <span className="text-[11px] text-ink-muted">
                    {row.attempt_count} attempt{row.attempt_count === 1 ? "" : "s"}
                  </span>
                  <button
                    type="button"
                    onClick={() => setExpanded(expanded === row.id ? null : row.id)}
                    className="ml-auto text-xs font-medium text-ink-secondary underline hover:text-ink"
                  >
                    {expanded === row.id ? "Hide attempts" : "Attempts"}
                  </button>
                </div>
                <p className="mt-1 text-[11px] text-ink-muted">
                  {row.last_error_code ? (
                    <span className="mr-2">
                      {DELIVERY_ERROR_LABELS[row.last_error_code] ?? row.last_error_code}
                      {row.last_http_status ? ` (HTTP ${row.last_http_status})` : ""}
                    </span>
                  ) : null}
                  {row.delivered_at ? (
                    <>
                      delivered <time className="font-mono">{row.delivered_at}</time>
                    </>
                  ) : row.next_attempt_at ? (
                    <>
                      next attempt <time className="font-mono">{row.next_attempt_at}</time>
                    </>
                  ) : null}
                </p>
                {expanded === row.id ? (
                  <div className="mt-2 border-t border-border pt-2">
                    <AttemptTimeline deliveryId={row.id} />
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
    </div>
  );
}
