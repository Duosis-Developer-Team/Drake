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
import { useCallback, useEffect, useMemo, useState } from "react";
import type { LucideIcon } from "lucide-react";
import {
  ChevronDown,
  CircleCheck,
  Clock,
  Hourglass,
  MailX,
  RotateCcw,
  Send,
  Webhook,
} from "lucide-react";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";

import { Donut } from "@/components/charts/visuals";
import {
  FilterChips,
  IconBubble,
  KpiTile,
  PILL_BUTTON,
  ShareBar,
  StateCard,
} from "@/components/features/configure/kit";
import { DataState } from "@/components/state/DataState";
import { StatusBadge, type HealthStatus } from "@/components/state/StatusBadge";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { ApiError } from "@/lib/api";
import { formatRelative, formatUtc } from "@/lib/design/format";
import type { StatusTone } from "@/lib/design/status";
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

const STATE_TONE: Record<DeliveryState, StatusTone> = {
  pending: "pending",
  processing: "pending",
  retrying: "warning",
  delivered: "success",
  dead_letter: "critical",
  suppressed: "neutral",
};

const STATE_ICON: Record<DeliveryState, LucideIcon> = {
  pending: Hourglass,
  processing: Send,
  retrying: RotateCcw,
  delivered: CircleCheck,
  dead_letter: MailX,
  suppressed: Clock,
};

const STATES: DeliveryState[] = [
  "pending",
  "retrying",
  "delivered",
  "dead_letter",
  "suppressed",
];

const OUTCOME_TONE: Record<DeliveryAttempt["outcome"], StatusTone> = {
  delivered: "success",
  retryable: "warning",
  terminal: "critical",
  refused: "critical",
};

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
      <p className="text-caption text-ink-secondary">
        Not attempted yet — this delivery is still queued.
      </p>
    );
  }
  return (
    <ol className="relative space-y-4 pl-1" data-testid="attempt-timeline">
      {attempts.map((attempt, index) => {
        const tone = OUTCOME_TONE[attempt.outcome] ?? "unknown";
        return (
          <li key={attempt.attempt_number} className="relative flex gap-4">
            {index < attempts.length - 1 ? (
              <span aria-hidden className="absolute top-8 bottom-[-1rem] left-[0.9375rem] w-px bg-border" />
            ) : null}
            <span
              aria-hidden
              className={`relative flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-micro font-semibold ${
                tone === "success"
                  ? "bg-healthy-soft text-healthy"
                  : tone === "warning"
                    ? "bg-warning-soft text-warning"
                    : "bg-critical-soft text-critical"
              }`}
            >
              {attempt.attempt_number}
            </span>
            <div className="min-w-0 pt-1 text-micro text-ink-secondary">
              <p>
                <span className="text-caption font-semibold text-ink">Attempt {attempt.attempt_number}</span>{" "}
                — {attempt.outcome}
              </p>
              <p className="mt-1 flex flex-wrap gap-1.5">
                {attempt.http_status ? (
                  <span className="rounded-full bg-surface-3 px-2 py-0.5 font-mono">HTTP {attempt.http_status}</span>
                ) : null}
                {attempt.error_code ? (
                  <span className="rounded-full bg-surface-3 px-2 py-0.5">
                    {DELIVERY_ERROR_LABELS[attempt.error_code] ?? attempt.error_code}
                  </span>
                ) : null}
                {attempt.duration_ms !== null ? (
                  <span className="rounded-full bg-surface-3 px-2 py-0.5 font-mono">{attempt.duration_ms} ms</span>
                ) : null}
                {attempt.retry_at ? (
                  <span className="rounded-full bg-surface-3 px-2 py-0.5">
                    retry at <time className="font-mono">{attempt.retry_at}</time>
                  </span>
                ) : null}
              </p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function LifecycleCard() {
  return (
    <Panel>
      <PanelHeader title="Delivery lifecycle" description="At-least-once, with a stable idempotency key" />
      <ol className="space-y-3">
        {(["pending", "retrying", "delivered", "dead_letter", "suppressed"] as DeliveryState[]).map(
          (state) => (
            <li key={state} className="flex items-center gap-3">
              <IconBubble icon={STATE_ICON[state]} tone={STATE_TONE[state]} size="sm" />
              <span className="min-w-0 flex-1 text-caption font-medium text-ink">
                {DELIVERY_STATE_LABELS[state]}
              </span>
              <span className="text-right text-micro text-ink-muted">
                {
                  {
                    pending: "queued",
                    processing: "in flight",
                    retrying: "backing off",
                    delivered: "accepted",
                    dead_letter: "gave up",
                    suppressed: "not sent",
                  }[state]
                }
              </span>
            </li>
          ),
        )}
      </ol>
    </Panel>
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

  const byState = useMemo(() => {
    const counts: Record<DeliveryState, number> = {
      pending: 0,
      processing: 0,
      retrying: 0,
      delivered: 0,
      dead_letter: 0,
      suppressed: 0,
    };
    for (const row of rows ?? []) counts[row.state] += 1;
    return counts;
  }, [rows]);

  const shown = rows?.length ?? 0;
  const inFlight = byState.pending + byState.processing + byState.retrying;

  return (
    <PageFrame>
      <PageHeader
        title="Notification deliveries"
        description="Outbound webhook deliveries, their attempts and safe error codes."
        actions={
          <Link href="/notification-policies" className={PILL_BUTTON}>
            <Webhook aria-hidden className="h-3.5 w-3.5" />
            Routing policies
          </Link>
        }
      />

      <div className="space-y-6">
        {rows !== null ? (
          <div className="grid grid-cols-1 items-stretch gap-6 sm:grid-cols-2 xl:grid-cols-4">
            <KpiTile icon={Send} label="Deliveries shown" value={shown}>
              <p className="text-micro text-ink-muted">
                {state ? `Filtered to ${DELIVERY_STATE_LABELS[state]}` : "Every state, newest first"}
              </p>
            </KpiTile>
            <KpiTile icon={CircleCheck} tone="success" label="Delivered" value={byState.delivered} suffix={`of ${shown}`}>
              <ShareBar value={byState.delivered} total={shown} tone="success" label="accepted by the target" />
            </KpiTile>
            <KpiTile icon={RotateCcw} tone={inFlight > 0 ? "warning" : undefined} label="In flight" value={inFlight}>
              <p className="text-micro text-ink-muted">
                <span data-tabular className="font-medium text-ink-secondary">{byState.retrying}</span> retrying ·{" "}
                <span data-tabular className="font-medium text-ink-secondary">
                  {byState.pending + byState.processing}
                </span>{" "}
                queued
              </p>
            </KpiTile>
            <KpiTile icon={MailX} tone={byState.dead_letter > 0 ? "critical" : undefined} label="Dead letter" value={byState.dead_letter}>
              <p className="text-micro text-ink-muted">
                <span data-tabular className="font-medium text-ink-secondary">{byState.suppressed}</span> suppressed
              </p>
            </KpiTile>
          </div>
        ) : null}

        <FilterChips
          label="State"
          value={state}
          onChange={(value) => setState(value)}
          options={[
            { value: "" as DeliveryState | "", label: "Any state" },
            ...STATES.map((value) => ({ value, label: DELIVERY_STATE_LABELS[value] })),
          ]}
        />

        <div className="grid grid-cols-1 items-start gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,22rem)]">
          <div className="min-w-0 space-y-6">
            {error ? (
              <Panel>
                <StateCard kind="error" title="Could not load deliveries" description={error} onRetry={load} />
              </Panel>
            ) : null}
            {rows === null && !error ? (
              <Panel>
                <DataState kind="loading" />
              </Panel>
            ) : null}
            {rows !== null && rows.length === 0 ? (
              <Panel>
                <StateCard
                  kind="empty"
                  icon={Send}
                  title="No deliveries"
                  description="No webhook deliveries match this filter in your authorized scope."
                  action={
                    state ? (
                      <button type="button" onClick={() => setState("")} className={PILL_BUTTON}>
                        Show every state
                      </button>
                    ) : null
                  }
                />
              </Panel>
            ) : null}

            {rows !== null && rows.length > 0 ? (
              <Panel flush>
                <PanelHeader flush title="Deliveries" meta={<span>{rows.length} shown</span>} />
                <ul className="divide-y divide-border" data-testid="delivery-list">
                  {rows.map((row) => (
                    <li key={row.id} className="px-7 py-4 transition-colors hover:bg-surface-hover" data-testid={`delivery-${row.id}`}>
                      <div className="flex flex-wrap items-center gap-4">
                        <IconBubble icon={STATE_ICON[row.state]} tone={STATE_TONE[row.state]} />
                        <div className="min-w-0 flex-1">
                          <Link
                            href={`/incidents/${row.incident_id}`}
                            className="block truncate text-body font-semibold text-ink hover:underline"
                          >
                            {row.incident_title}
                          </Link>
                          <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-micro text-ink-muted">
                            <span className="text-ink-secondary">
                              {EVENT_TYPE_LABELS[row.event_type] ?? row.event_type}
                            </span>
                            <span>→ {row.destination_display_name}</span>
                            <span className="font-mono">{row.project_key}</span>
                          </p>
                        </div>
                        <div className="flex shrink-0 flex-wrap items-center gap-2">
                          <StatusBadge
                            status={STATE_BADGE[row.state]}
                            label={DELIVERY_STATE_LABELS[row.state]}
                          />
                          <span className="rounded-full bg-surface-2 px-2.5 py-1 text-micro font-medium text-ink-secondary">
                            {row.attempt_count} attempt{row.attempt_count === 1 ? "" : "s"}
                          </span>
                          <button
                            type="button"
                            aria-expanded={expanded === row.id}
                            onClick={() => setExpanded(expanded === row.id ? null : row.id)}
                            className={PILL_BUTTON}
                          >
                            {expanded === row.id ? "Hide attempts" : "Attempts"}
                            <ChevronDown
                              aria-hidden
                              className={`h-3.5 w-3.5 transition-transform ${expanded === row.id ? "rotate-180" : ""}`}
                            />
                          </button>
                        </div>
                      </div>
                      {row.last_error_code || row.delivered_at || row.next_attempt_at ? (
                        <p className="mt-2 flex flex-wrap items-center gap-2 pl-14 text-micro text-ink-muted">
                          {row.last_error_code ? (
                            <span className="rounded-full bg-critical-soft px-2 py-0.5 text-critical">
                              {DELIVERY_ERROR_LABELS[row.last_error_code] ?? row.last_error_code}
                              {row.last_http_status ? ` (HTTP ${row.last_http_status})` : ""}
                            </span>
                          ) : null}
                          {row.delivered_at ? (
                            <span>
                              delivered{" "}
                              <time dateTime={row.delivered_at} title={formatUtc(row.delivered_at)}>
                                {formatRelative(row.delivered_at)}
                              </time>
                            </span>
                          ) : row.next_attempt_at ? (
                            <span>
                              next attempt <time className="font-mono">{row.next_attempt_at}</time>
                            </span>
                          ) : null}
                        </p>
                      ) : null}
                      {expanded === row.id ? (
                        <div className="mt-4 ml-14 rounded-[1.25rem] bg-surface-2 p-5">
                          <AttemptTimeline deliveryId={row.id} />
                        </div>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </Panel>
            ) : null}
          </div>

          <div className="space-y-6">
            {rows !== null && rows.length > 0 ? (
              <Panel>
                <PanelHeader title="Outcome breakdown" description="Deliveries shown, by state" />
                <Donut
                  label="Delivery outcomes"
                  size={148}
                  thickness={16}
                  slices={(["delivered", "retrying", "pending", "processing", "dead_letter", "suppressed"] as DeliveryState[]).map(
                    (value) => ({
                      name: DELIVERY_STATE_LABELS[value],
                      value: byState[value],
                      tone: STATE_TONE[value],
                    }),
                  )}
                />
              </Panel>
            ) : null}
            <LifecycleCard />
          </div>
        </div>
      </div>
    </PageFrame>
  );
}
