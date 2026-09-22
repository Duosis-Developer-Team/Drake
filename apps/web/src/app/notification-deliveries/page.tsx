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
import type { StatusTone } from "@/lib/design/status";
import { useFormat, useT } from "@/lib/i18n";
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
  const t = useT("notifications");
  const c = useT("common");
  const fmt = useFormat();
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
          setError(
            problem instanceof ApiError ? problem.message : c("state.error"),
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [deliveryId, c]);

  if (error) return <DataState kind="error" description={error} />;
  if (attempts === null) return <DataState kind="loading" />;
  if (attempts.length === 0) {
    return (
      <p className="text-caption text-ink-secondary">{t("deliveries.attempt.notAttempted")}</p>
    );
  }
  return (
    <ol className="relative space-y-4 pl-1" data-testid="attempt-timeline">
      {attempts.map((attempt, index) => {
        const tone = OUTCOME_TONE[attempt.outcome] ?? "unknown";
        return (
          <li key={attempt.attempt_number} className="relative flex gap-4">
            {index < attempts.length - 1 ? (
              <span
                aria-hidden
                className="absolute top-8 bottom-[-1rem] left-[0.9375rem] w-px bg-border"
              />
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
                <span className="text-caption font-semibold text-ink">
                  {t("deliveries.attempt.number", { number: attempt.attempt_number })}
                </span>{" "}
                — {t.dyn("outcome", attempt.outcome, attempt.outcome)}
              </p>
              <p className="mt-1 flex flex-wrap gap-1.5">
                {attempt.http_status ? (
                  <span className="rounded-full bg-surface-3 px-2 py-0.5 font-mono">
                    {t("deliveries.attempt.http", { status: attempt.http_status })}
                  </span>
                ) : null}
                {attempt.error_code ? (
                  <span className="rounded-full bg-surface-3 px-2 py-0.5">
                    {t.dyn(
                      "deliveryError",
                      attempt.error_code,
                      DELIVERY_ERROR_LABELS[attempt.error_code] ?? attempt.error_code,
                    )}
                  </span>
                ) : null}
                {attempt.duration_ms !== null ? (
                  <span className="rounded-full bg-surface-3 px-2 py-0.5 font-mono">
                    {c("time.millisecondsShort", { count: attempt.duration_ms })}
                  </span>
                ) : null}
                {attempt.retry_at ? (
                  <span className="rounded-full bg-surface-3 px-2 py-0.5">
                    {t("deliveries.attempt.retryAt", { when: fmt.utc(attempt.retry_at) })}
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
  const t = useT("notifications");
  return (
    <Panel>
      <PanelHeader
        title={t("deliveries.lifecycle.title")}
        description={t("deliveries.lifecycle.description")}
      />
      <ol className="space-y-3">
        {(
          [
            "pending",
            "retrying",
            "delivered",
            "dead_letter",
            "suppressed",
          ] as DeliveryState[]
        ).map((state) => (
          <li key={state} className="flex items-center gap-3">
            <IconBubble
              icon={STATE_ICON[state]}
              tone={STATE_TONE[state]}
              size="sm"
            />
            <span className="min-w-0 flex-1 text-caption font-medium text-ink">
              {t.dyn("deliveryState", state, DELIVERY_STATE_LABELS[state])}
            </span>
            <span className="text-right text-micro text-ink-muted">
              {t.dyn("deliveryStateHint", state, state)}
            </span>
          </li>
        ))}
      </ol>
    </Panel>
  );
}

export default function NotificationDeliveriesPage() {
  const t = useT("notifications");
  const c = useT("common");
  const fmt = useFormat();
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
          setError(
            problem instanceof ApiError ? problem.message : c("state.error"),
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [state, c]);

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
  const stateLabel = (value: DeliveryState) =>
    t.dyn("deliveryState", value, DELIVERY_STATE_LABELS[value]);

  return (
    <PageFrame>
      <PageHeader
        title={t("deliveries.title")}
        description={t("deliveries.description")}
        actions={
          <Link href="/notification-policies" className={PILL_BUTTON}>
            <Webhook aria-hidden className="h-3.5 w-3.5" />
            {t("deliveries.routingPolicies")}
          </Link>
        }
      />

      <div className="space-y-6">
        {rows !== null ? (
          <div className="page-grid">
            <KpiTile icon={Send} label={t("deliveries.kpi.shown")} value={shown}>
              <p className="text-micro text-ink-muted">
                {state
                  ? t("deliveries.kpi.filteredTo", { state: stateLabel(state) })
                  : t("deliveries.kpi.everyState")}
              </p>
            </KpiTile>
            <KpiTile
              icon={CircleCheck}
              tone="success"
              label={t("deliveries.kpi.delivered")}
              value={byState.delivered}
              suffix={t("deliveries.kpi.of", { total: shown })}
            >
              <ShareBar
                value={byState.delivered}
                total={shown}
                tone="success"
                label={t("deliveries.kpi.accepted")}
              />
            </KpiTile>
            <KpiTile
              icon={RotateCcw}
              tone={inFlight > 0 ? "warning" : undefined}
              label={t("deliveries.kpi.inFlight")}
              value={inFlight}
            >
              <p className="text-micro text-ink-muted">
                <span data-tabular className="font-medium text-ink-secondary">
                  {byState.retrying}
                </span>{" "}
                {t("deliveries.kpi.retrying")} ·{" "}
                <span data-tabular className="font-medium text-ink-secondary">
                  {byState.pending + byState.processing}
                </span>{" "}
                {t("deliveries.kpi.queued")}
              </p>
            </KpiTile>
            <KpiTile
              icon={MailX}
              tone={byState.dead_letter > 0 ? "critical" : undefined}
              label={t("deliveries.kpi.deadLetter")}
              value={byState.dead_letter}
            >
              <p className="text-micro text-ink-muted">
                <span data-tabular className="font-medium text-ink-secondary">
                  {byState.suppressed}
                </span>{" "}
                {t("deliveries.kpi.suppressed")}
              </p>
            </KpiTile>
          </div>
        ) : null}

        <FilterChips
          label={t("deliveries.filter.label")}
          value={state}
          onChange={(value) => setState(value)}
          options={[
            { value: "" as DeliveryState | "", label: t("deliveries.filter.any") },
            ...STATES.map((value) => ({
              value,
              label: stateLabel(value),
            })),
          ]}
        />

        <div className="page-split">
          <div className="page-main">
            {error ? (
              <Panel>
                <StateCard
                  kind="error"
                  title={t("deliveries.errorTitle")}
                  description={error}
                  onRetry={load}
                />
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
                  title={t("deliveries.emptyTitle")}
                  description={t("deliveries.emptyDescription")}
                  action={
                    state ? (
                      <button
                        type="button"
                        onClick={() => setState("")}
                        className={PILL_BUTTON}
                      >
                        {t("deliveries.showEveryState")}
                      </button>
                    ) : null
                  }
                />
              </Panel>
            ) : null}

            {rows !== null && rows.length > 0 ? (
              <Panel flush>
                <PanelHeader
                  flush
                  title={t("deliveries.list.title")}
                  meta={<span>{t("deliveries.list.shown", { count: rows.length })}</span>}
                />
                <ul
                  className="divide-y divide-border"
                  data-testid="delivery-list"
                >
                  {rows.map((row) => (
                    <li
                      key={row.id}
                      className="px-7 py-4 transition-colors hover:bg-surface-hover"
                      data-testid={`delivery-${row.id}`}
                    >
                      <div className="flex flex-wrap items-center gap-4">
                        <IconBubble
                          icon={STATE_ICON[row.state]}
                          tone={STATE_TONE[row.state]}
                        />
                        <div className="min-w-0 flex-1">
                          <Link
                            href={`/incidents/${row.incident_id}`}
                            className="block truncate text-body font-semibold text-ink hover:underline"
                          >
                            {row.incident_title}
                          </Link>
                          <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-micro text-ink-muted">
                            <span className="text-ink-secondary">
                              {t.dyn(
                                "eventType",
                                row.event_type,
                                EVENT_TYPE_LABELS[row.event_type] ?? row.event_type,
                              )}
                            </span>
                            <span>→ {row.destination_display_name}</span>
                            <span className="font-mono">{row.project_key}</span>
                          </p>
                        </div>
                        <div className="flex shrink-0 flex-wrap items-center gap-2">
                          <StatusBadge
                            status={STATE_BADGE[row.state]}
                            label={stateLabel(row.state)}
                          />
                          <span className="rounded-full bg-surface-2 px-2.5 py-1 text-micro font-medium text-ink-secondary">
                            {t("deliveries.row.attempts", { count: row.attempt_count })}
                          </span>
                          <button
                            type="button"
                            aria-expanded={expanded === row.id}
                            onClick={() =>
                              setExpanded(expanded === row.id ? null : row.id)
                            }
                            className={PILL_BUTTON}
                          >
                            {expanded === row.id
                              ? t("deliveries.row.hideAttempts")
                              : t("deliveries.row.showAttempts")}
                            <ChevronDown
                              aria-hidden
                              className={`h-3.5 w-3.5 transition-transform ${expanded === row.id ? "rotate-180" : ""}`}
                            />
                          </button>
                        </div>
                      </div>
                      {row.last_error_code ||
                      row.delivered_at ||
                      row.next_attempt_at ? (
                        <p className="mt-2 flex flex-wrap items-center gap-2 pl-14 text-micro text-ink-muted">
                          {row.last_error_code ? (
                            <span className="rounded-full bg-critical-soft px-2 py-0.5 text-critical">
                              {t.dyn(
                                "deliveryError",
                                row.last_error_code,
                                DELIVERY_ERROR_LABELS[row.last_error_code] ?? row.last_error_code,
                              )}
                              {row.last_http_status
                                ? ` ${t("deliveries.row.httpStatus", { status: row.last_http_status })}`
                                : ""}
                            </span>
                          ) : null}
                          {row.delivered_at ? (
                            <span title={fmt.utc(row.delivered_at)}>
                              {t("deliveries.row.deliveredAt", {
                                when: fmt.relative(row.delivered_at),
                              })}
                            </span>
                          ) : row.next_attempt_at ? (
                            <span>
                              {t("deliveries.row.nextAttempt", {
                                when: fmt.utc(row.next_attempt_at),
                              })}
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

          <div className="page-aside">
            {rows !== null && rows.length > 0 ? (
              <Panel>
                <PanelHeader
                  title={t("deliveries.breakdown.title")}
                  description={t("deliveries.breakdown.description")}
                />
                <Donut
                  label={t("deliveries.breakdown.chart")}
                  size={148}
                  thickness={16}
                  slices={(
                    [
                      "delivered",
                      "retrying",
                      "pending",
                      "processing",
                      "dead_letter",
                      "suppressed",
                    ] as DeliveryState[]
                  ).map((value) => ({
                    name: stateLabel(value),
                    value: byState[value],
                    tone: STATE_TONE[value],
                  }))}
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
