"use client";

import { useCallback, useEffect, useState } from "react";

import { Fingerprint, ScrollText, ShieldAlert, UserRound } from "lucide-react";

import { StackedBar } from "@/components/charts/visuals";
import {
  IconBubble,
  KpiTile,
  PILL_BUTTON,
  StateCard,
  TABLE_HEAD,
} from "@/components/features/configure/kit";
import { DataState } from "@/components/state/DataState";
import { StatusBadge } from "@/components/state/StatusBadge";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { ApiError, apiGet } from "@/lib/api";
import { useT } from "@/lib/i18n";

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
  const t = useT("admin");
  const common = useT("common");
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
      setMessage(error instanceof ApiError ? error.message : t("error.request"));
      setPhase("error");
    }
  }, [t]);

  useEffect(() => {
    void loadPage(null);
  }, [loadPage]);

  const tally = {
    success: events.filter((event) => event.result === "success").length,
    denied: events.filter((event) => event.result === "denied").length,
    failure: events.filter((event) => event.result === "failure").length,
  };
  const actors = new Set(events.map((event) => `${event.actor_type}:${event.actor_id}`)).size;

  return (
    <div className="space-y-6">
      {events.length > 0 ? (
        <div className="grid grid-cols-1 items-stretch gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1.6fr)]">
          <KpiTile icon={ScrollText} label={t("audit.kpi.loaded")} value={events.length}>
            <p className="text-micro text-ink-muted">
              {cursor ? t("audit.kpi.morePages") : t("audit.kpi.allInScope")}
            </p>
          </KpiTile>
          <KpiTile icon={Fingerprint} tone="info" label={t("audit.kpi.actors")} value={actors}>
            <p className="text-micro text-ink-muted">{t("audit.kpi.actorsCaption")}</p>
          </KpiTile>
          <Panel className="h-full !gap-5">
            <div className="flex items-center gap-3">
              <IconBubble icon={ShieldAlert} tone={tally.failure + tally.denied > 0 ? "warning" : undefined} />
              <span className="text-caption font-medium text-ink-secondary">{t("audit.kpi.outcome")}</span>
            </div>
            <StackedBar
              label={t("audit.kpi.chart")}
              height={20}
              segments={[
                { name: t("audit.result.success"), value: tally.success, tone: "success" },
                { name: t("audit.result.denied"), value: tally.denied, tone: "warning" },
                { name: t("audit.result.failure"), value: tally.failure, tone: "critical" },
              ]}
            />
          </Panel>
        </div>
      ) : null}

      <Panel flush>
        <PanelHeader flush title={t("audit.list.title")} description={t("audit.list.description")} />
        {phase === "loading" ? (
          <div className="px-7 py-5">
            <DataState kind="loading" />
          </div>
        ) : null}
        {phase === "error" ? (
          <div className="px-7 py-5">
            <DataState kind="error" description={message} onRetry={() => void loadPage(null)} />
          </div>
        ) : null}
        {phase !== "loading" && phase !== "error" && events.length === 0 ? (
          <StateCard kind="empty" icon={ScrollText} title={t("audit.list.empty")} />
        ) : null}
        {events.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-left" data-testid="audit-table">
              <thead>
                <tr className={`border-b border-border ${TABLE_HEAD}`}>
                  <th className="h-11 px-7 font-medium">{t("audit.list.time")}</th>
                  <th className="h-11 px-3 font-medium">{t("audit.list.actor")}</th>
                  <th className="h-11 px-3 font-medium">{t("audit.list.action")}</th>
                  <th className="h-11 px-3 font-medium">{t("audit.list.scope")}</th>
                  <th className="h-11 px-7 font-medium">{t("audit.list.result")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {events.map((event) => (
                  <tr key={event.id} className="h-14 transition-colors hover:bg-surface-hover">
                    <td className="px-7 font-mono text-micro whitespace-nowrap text-ink-secondary">
                      {event.occurred_at.replace("T", " ").slice(0, 19)}
                    </td>
                    <td className="px-3">
                      <span className="inline-flex items-center gap-2 rounded-full bg-surface-2 py-1 pr-2.5 pl-1 text-micro text-ink-secondary">
                        <span aria-hidden className="flex h-5 w-5 items-center justify-center rounded-full bg-surface-3">
                          <UserRound className="h-3 w-3" />
                        </span>
                        <span className="font-mono">{event.actor_type}</span>
                      </span>
                    </td>
                    <td className="px-3 font-mono text-caption font-medium text-ink">{event.action}</td>
                    <td className="px-3 font-mono text-micro text-ink-secondary">
                      {event.scope_ref ?? "—"}
                    </td>
                    <td className="px-7">
                      <StatusBadge
                        status={RESULT_STATUS[event.result]}
                        label={t.dyn("audit.resultBadge", event.result, event.result)}
                        size="compact"
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {cursor ? (
          <div className="border-t border-border px-7 py-4">
            <button
              type="button"
              onClick={() => void loadPage(cursor)}
              disabled={phase === "loading-more"}
              className={PILL_BUTTON}
            >
              {phase === "loading-more" ? t("audit.list.loadingMore") : common("action.loadMore")}
            </button>
          </div>
        ) : null}
      </Panel>
    </div>
  );
}
