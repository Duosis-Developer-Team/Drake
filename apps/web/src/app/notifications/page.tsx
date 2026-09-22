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
import type { LucideIcon } from "lucide-react";
import {
  ArrowRight,
  BellOff,
  BellRing,
  CheckCheck,
  CircleCheck,
  Inbox,
  MailOpen,
  Route,
  Siren,
} from "lucide-react";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";

import { StackedBar } from "@/components/charts/visuals";
import {
  IconBubble,
  KpiTile,
  PILL_BUTTON,
  ShareBar,
  StateCard,
} from "@/components/features/configure/kit";
import { DataState } from "@/components/state/DataState";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { SegmentedControl } from "@/components/ui/controls";
import { ApiError } from "@/lib/api";
import type { StatusTone } from "@/lib/design/status";
import { useFormat, useT } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import {
  EVENT_TYPES,
  EVENT_TYPE_LABELS,
  fetchInbox,
  markRead,
  type InboxItem,
  type InboxPage,
  type NotificationEventType,
} from "@/lib/notifications";

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string; denied: boolean }
  | { kind: "ready"; data: InboxPage };

const EVENT_VISUAL: Record<
  NotificationEventType,
  { icon: LucideIcon; tone: StatusTone }
> = {
  opened: { icon: Siren, tone: "critical" },
  acknowledged: { icon: BellRing, tone: "warning" },
  auto_resolved: { icon: CircleCheck, tone: "success" },
};

function NotificationRow({
  item,
  onRead,
  busy,
}: {
  item: InboxItem;
  onRead: (id: string) => void;
  busy: boolean;
}) {
  const t = useT("notifications");
  const fmt = useFormat();
  const visual = EVENT_VISUAL[item.event_type] ?? {
    icon: BellRing,
    tone: "unknown" as StatusTone,
  };
  return (
    <li
      className={`relative flex flex-wrap items-start gap-4 px-7 py-5 transition-colors hover:bg-surface-hover ${
        item.read_at ? "" : "bg-surface-2/60"
      }`}
      data-testid={`notification-${item.id}`}
    >
      {item.read_at ? null : (
        <span
          aria-hidden
          className="absolute top-1/2 left-2.5 h-2 w-2 -translate-y-1/2 rounded-full bg-info"
        />
      )}
      <IconBubble icon={visual.icon} tone={visual.tone} />
      <div className="min-w-0 flex-1">
        <p
          className={`text-body text-ink ${item.read_at ? "font-medium" : "font-semibold"}`}
        >
          {item.title}
        </p>
        <p className="mt-0.5 text-caption text-ink-secondary">{item.body}</p>
        <p className="mt-2 flex flex-wrap items-center gap-2 text-micro text-ink-muted">
          <span className="rounded-full bg-surface-3 px-2 py-0.5 font-medium text-ink-secondary">
            {t.dyn("eventType", item.event_type, EVENT_TYPE_LABELS[item.event_type])}
          </span>
          <time dateTime={item.created_at} title={fmt.utc(item.created_at)}>
            {fmt.relative(item.created_at)}
          </time>
          {item.read_at ? <span>· {t("inbox.row.read")}</span> : null}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2 self-center">
        {item.read_at ? null : (
          <button
            type="button"
            disabled={busy}
            onClick={() => onRead(item.id)}
            className={PILL_BUTTON}
          >
            <CheckCheck aria-hidden className="h-3.5 w-3.5" />
            {t("inbox.row.markRead")}
          </button>
        )}
        {/* Every listed row is one the reader may still open: the API
            filters out notifications whose incident has left their scope
            rather than returning a redacted placeholder. */}
        <Link
          href={item.target_path}
          className="inline-flex items-center gap-1.5 rounded-full bg-accent px-3.5 py-1.5 text-caption font-medium text-ink-inverse hover:opacity-90"
        >
          {t("inbox.row.openIncident")}
          <ArrowRight aria-hidden className="h-3.5 w-3.5" />
        </Link>
      </div>
    </li>
  );
}

export default function NotificationsPage() {
  const t = useT("notifications");
  const c = useT("common");
  const { state: session } = useSession();
  const csrfToken =
    session.status === "authenticated" ? session.me.csrf_token : null;
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
          message: error instanceof ApiError ? error.message : c("state.error"),
          denied,
        });
      });
    return () => {
      cancelled = true;
    };
  }, [unreadOnly, c]);

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

  const items = state.kind === "ready" ? state.data.items : [];
  const unreadIds = items
    .filter((item) => !item.read_at)
    .map((item) => item.id);
  const unreadCount = unreadIds.length;
  const total = items.length;
  const byEvent = EVENT_TYPES.map((event) => ({
    event,
    count: items.filter((item) => item.event_type === event).length,
  }));

  return (
    <PageFrame>
      <PageHeader
        title={t("inbox.title")}
        description={t("inbox.description")}
        actions={
          <Link href="/notification-policies" className={PILL_BUTTON}>
            <Route aria-hidden className="h-3.5 w-3.5" />
            {t("inbox.routingPolicies")}
          </Link>
        }
      />

      <div className="space-y-6">
        {state.kind === "ready" ? (
          <div className="page-grid" data-cols="3">
            <KpiTile
              icon={Inbox}
              label={unreadOnly ? t("inbox.kpi.shownUnread") : t("inbox.kpi.shown")}
              value={total}
            >
              <p className="text-micro text-ink-muted">{t("inbox.kpi.shownCaption")}</p>
            </KpiTile>
            <KpiTile
              icon={BellRing}
              tone={unreadCount > 0 ? "info" : undefined}
              label={t("inbox.kpi.unread")}
              value={unreadCount}
              suffix={t("inbox.kpi.of", { total })}
            >
              <ShareBar
                value={unreadCount}
                total={total}
                tone="info"
                label={t("inbox.kpi.stillUnread")}
              />
            </KpiTile>
            <KpiTile
              icon={Siren}
              tone={byEvent[0].count > 0 ? "critical" : undefined}
              label={t("inbox.kpi.opened")}
              value={byEvent[0].count}
            >
              <p className="text-micro text-ink-muted">
                <span data-tabular className="font-medium text-ink-secondary">
                  {byEvent[2].count}
                </span>{" "}
                {t("inbox.kpi.resolved")} ·{" "}
                <span data-tabular className="font-medium text-ink-secondary">
                  {byEvent[1].count}
                </span>{" "}
                {t("inbox.kpi.acknowledged")}
              </p>
            </KpiTile>
          </div>
        ) : null}

        <div className="flex flex-wrap items-center justify-between gap-3">
          <SegmentedControl
            label={t("inbox.view.label")}
            value={unreadOnly ? "unread" : "all"}
            onChange={(value) => setUnreadOnly(value === "unread")}
            options={[
              { value: "all", label: t("inbox.view.all") },
              { value: "unread", label: t("inbox.view.unread") },
            ]}
          />
          {unreadIds.length > 0 ? (
            <button
              type="button"
              disabled={busy}
              onClick={() => read(unreadIds)}
              className={PILL_BUTTON}
            >
              <MailOpen aria-hidden className="h-3.5 w-3.5" />
              {t("inbox.markVisibleRead")}
            </button>
          ) : null}
        </div>

        {state.kind === "loading" ? (
          <Panel>
            <DataState kind="loading" />
          </Panel>
        ) : null}
        {state.kind === "error" ? (
          <Panel>
            {state.denied ? (
              <StateCard
                kind="permission-denied"
                title={t("inbox.forbiddenTitle")}
                description={t("inbox.forbiddenDescription")}
              />
            ) : (
              <StateCard
                kind="error"
                title={t("inbox.errorTitle")}
                description={state.message}
                onRetry={load}
              />
            )}
          </Panel>
        ) : null}
        {state.kind === "ready" && items.length === 0 ? (
          <Panel>
            <StateCard
              kind="empty"
              icon={BellOff}
              title={unreadOnly ? t("inbox.emptyUnread") : t("inbox.empty")}
              description={t("inbox.emptyDescription")}
              action={
                <Link href="/notification-policies" className={PILL_BUTTON}>
                  {t("inbox.reviewPolicies")}
                </Link>
              }
            />
          </Panel>
        ) : null}
        {state.kind === "ready" && items.length > 0 ? (
          <div className="page-split" data-cols="3">
            <div className="page-main">
              <Panel flush>
                <PanelHeader
                  flush
                  title={t("inbox.list.title")}
                  meta={<span>{t("inbox.list.meta", { total, unread: unreadCount })}</span>}
                />
                <ul className="divide-y divide-border" data-testid="inbox-list">
                  {items.map((item) => (
                    <NotificationRow
                      key={item.id}
                      item={item}
                      busy={busy}
                      onRead={(id) => read([id])}
                    />
                  ))}
                </ul>
              </Panel>
            </div>
            <div className="page-aside">
              <Panel>
                <PanelHeader
                  title={t("inbox.byEvent.title")}
                  description={t("inbox.byEvent.description")}
                />
                <StackedBar
                  label={t("inbox.byEvent.chart")}
                  segments={byEvent.map(({ event, count }) => ({
                    name: t.dyn("eventType", event, EVENT_TYPE_LABELS[event]),
                    value: count,
                    tone: EVENT_VISUAL[event].tone,
                  }))}
                />
              </Panel>
            </div>
          </div>
        ) : null}
      </div>
    </PageFrame>
  );
}
