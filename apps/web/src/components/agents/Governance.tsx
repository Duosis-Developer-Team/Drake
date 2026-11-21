"use client";

/**
 * Governance: what the organisation decided about its agents.
 *
 * - `LifecycleBadge`: under review / approved / retired, and shadow AI;
 * - `GovernancePanel`: one agent — lifecycle, owner, purpose;
 * - `GovernanceView`: every agent — what waits for review (shadow AI first),
 *   what is approved, what was retired and runs again.
 */

import {
  CheckCircle2,
  CircleDashed,
  Archive,
  EyeOff,
  RotateCcw,
  ShieldQuestion,
  UserRound,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { AgentGlyph } from "@/components/agents/visuals";
import { KpiTile } from "@/components/features/configure/kit";
import { DataState } from "@/components/state/DataState";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import {
  callsOf,
  compact,
  fetchAgents,
  setGovernance,
  usd,
  type Agent,
  type AgentInventory,
  type Lifecycle,
  type Window,
} from "@/lib/agents";
import { useFormat, useLocale, useT } from "@/lib/i18n";
import { useSession } from "@/lib/session";

export function LifecycleBadge({
  agent,
  compactMode = false,
}: {
  agent: Pick<Agent, "lifecycle" | "shadow" | "seen_after_retirement">;
  compactMode?: boolean;
}) {
  const t = useT("agents");
  if (agent.shadow) {
    return (
      <span
        title={t("gov.shadowWhy")}
        className="inline-flex items-center gap-1 rounded-full bg-warning-soft px-2 py-0.5 text-micro font-semibold text-warning"
      >
        <EyeOff aria-hidden className="h-3 w-3" />
        {t("gov.shadow")}
      </span>
    );
  }
  if (agent.lifecycle === "approved") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-healthy-soft px-2 py-0.5 text-micro font-semibold text-healthy">
        <CheckCircle2 aria-hidden className="h-3 w-3" />
        {t("gov.lifecycle.approved")}
      </span>
    );
  }
  if (agent.lifecycle === "retired") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-surface-3 px-2 py-0.5 text-micro font-semibold text-ink-secondary">
        <Archive aria-hidden className="h-3 w-3" />
        {agent.seen_after_retirement
          ? t("gov.seenAgainBadge")
          : t("gov.lifecycle.retired")}
      </span>
    );
  }
  if (compactMode) return null;
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-surface-3 px-2 py-0.5 text-micro font-medium text-ink-secondary">
      <CircleDashed aria-hidden className="h-3 w-3" />
      {t("gov.lifecycle.observed")}
    </span>
  );
}

function useCsrf() {
  const { state } = useSession();
  return state.status === "authenticated" ? state.me.csrf_token : null;
}

export function GovernancePanel<T extends Agent>({
  agent,
  onChange,
}: {
  agent: T;
  onChange: (next: T) => void;
}) {
  const t = useT("agents");
  const fmt = useFormat();
  const csrf = useCsrf();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const [editing, setEditing] = useState(false);
  const [purpose, setPurpose] = useState(agent.purpose ?? "");
  const can = Boolean(agent.can_manage && csrf);

  const send = async (body: Parameters<typeof setGovernance>[2]) => {
    if (!csrf) return;
    setBusy(true);
    setFailed(false);
    try {
      const next = await setGovernance(csrf, agent.id, body);
      onChange({ ...agent, ...next });
      setEditing(false);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };

  const lifecycle = agent.lifecycle as Lifecycle;
  const changed = agent.lifecycle_changed_at
    ? agent.lifecycle_changed_by
      ? t("gov.changed", {
          state: t.dyn("gov.lifecycle", lifecycle, lifecycle),
          who: agent.lifecycle_changed_by,
          when: fmt.relative(agent.lifecycle_changed_at),
        })
      : t("gov.changedNoWho", {
          state: t.dyn("gov.lifecycle", lifecycle, lifecycle),
          when: fmt.relative(agent.lifecycle_changed_at),
        })
    : t("gov.firstSeen", { when: fmt.relative(agent.first_seen_at) });

  return (
    <Panel data-testid="agent-governance">
      <PanelHeader title={t("gov.title")} />
      <div className="space-y-4">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <LifecycleBadge agent={agent} />
            {agent.shadow ? (
              <LifecycleBadge agent={{ ...agent, shadow: false }} />
            ) : null}
          </div>
          <p className="mt-1 text-micro text-ink-muted">{changed}</p>
          {agent.shadow ? (
            <p className="mt-1 text-micro text-warning">{t("gov.shadowWhy")}</p>
          ) : null}
          {can ? (
            <div className="mt-2.5 flex flex-wrap gap-2">
              {lifecycle !== "approved" ? (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => send({ lifecycle: "approved" })}
                  data-testid="gov-approve"
                  className="inline-flex h-8 items-center gap-1.5 rounded-control bg-brand px-3 text-caption font-semibold text-white hover:opacity-90 disabled:opacity-50"
                >
                  <CheckCircle2 aria-hidden className="h-3.5 w-3.5" />
                  {t("gov.approve")}
                </button>
              ) : null}
              {lifecycle !== "observed" ? (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => send({ lifecycle: "observed" })}
                  className="inline-flex h-8 items-center gap-1.5 rounded-control border border-border px-3 text-caption font-medium text-ink-secondary hover:bg-surface-hover"
                >
                  <RotateCcw aria-hidden className="h-3.5 w-3.5" />
                  {t("gov.reopen")}
                </button>
              ) : null}
              {lifecycle !== "retired" ? (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    if (
                      window.confirm(
                        t("gov.confirmRetire", { name: agent.display_name }),
                      )
                    )
                      void send({ lifecycle: "retired" });
                  }}
                  data-testid="gov-retire"
                  className="inline-flex h-8 items-center gap-1.5 rounded-control border border-border px-3 text-caption font-medium text-ink-secondary hover:bg-surface-hover"
                >
                  <Archive aria-hidden className="h-3.5 w-3.5" />
                  {t("gov.retire")}
                </button>
              ) : null}
            </div>
          ) : null}
        </div>

        <div className="border-t border-border pt-3">
          <p className="text-micro text-ink-muted">{t("gov.owner")}</p>
          <div className="mt-0.5 flex flex-wrap items-center justify-between gap-2">
            <span
              className={`inline-flex items-center gap-1.5 text-caption font-medium ${agent.owner ? "text-ink" : "text-ink-muted italic"}`}
            >
              <UserRound aria-hidden className="h-3.5 w-3.5" />
              {agent.owner ? (agent.owner.name ?? "—") : t("gov.noOwner")}
            </span>
            {can ? (
              <button
                type="button"
                disabled={busy}
                onClick={() => send({ owner: agent.owner ? "none" : "me" })}
                data-testid="gov-owner"
                className="text-caption font-medium text-brand hover:underline"
              >
                {agent.owner ? t("gov.giveUp") : t("gov.takeOwnership")}
              </button>
            ) : null}
          </div>
        </div>

        <div className="border-t border-border pt-3">
          <div className="flex items-center justify-between">
            <p className="text-micro text-ink-muted">{t("gov.purpose")}</p>
            {can && !editing ? (
              <button
                type="button"
                onClick={() => {
                  setPurpose(agent.purpose ?? "");
                  setEditing(true);
                }}
                className="text-micro font-medium text-brand hover:underline"
              >
                {t("gov.edit")}
              </button>
            ) : null}
          </div>
          {editing ? (
            <div className="mt-1.5 space-y-2">
              <textarea
                value={purpose}
                maxLength={500}
                rows={3}
                onChange={(e) => setPurpose(e.target.value)}
                placeholder={t("gov.purposePlaceholder")}
                data-testid="gov-purpose"
                className="w-full rounded-control border border-border bg-surface px-2.5 py-2 text-caption text-ink"
              />
              <div className="flex items-center justify-between">
                <span className="text-micro tabular-nums text-ink-muted">
                  {purpose.length}/500
                </span>
                <span className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => setEditing(false)}
                    className="h-7 px-2 text-caption text-ink-secondary"
                  >
                    {t("gov.cancel")}
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => send({ purpose })}
                    data-testid="gov-purpose-save"
                    className="h-7 rounded-control bg-brand px-3 text-caption font-semibold text-white disabled:opacity-50"
                  >
                    {t("gov.save")}
                  </button>
                </span>
              </div>
            </div>
          ) : (
            <p
              className={`mt-0.5 text-caption ${agent.purpose ? "text-ink" : "text-ink-muted italic"}`}
            >
              {agent.purpose ?? t("gov.purposeEmpty")}
            </p>
          )}
        </div>
        {failed ? (
          <p className="text-micro text-critical">{t("gov.failed")}</p>
        ) : null}
        {!agent.can_manage ? (
          <p className="text-micro text-ink-muted">{t("gov.readOnly")}</p>
        ) : null}
      </div>
    </Panel>
  );
}

function Row({
  agent,
  onChange,
}: {
  agent: Agent;
  onChange: (next: Agent) => void;
}) {
  const t = useT("agents");
  const fmt = useFormat();
  const { locale } = useLocale();
  const csrf = useCsrf();
  const [busy, setBusy] = useState(false);
  const where = agent.project
    ? `${agent.project.key}${agent.environment ? `/${agent.environment.key}` : ""}`
    : agent.namespace || agent.service_name || t("card.outside");
  const act = async (lifecycle: Lifecycle) => {
    if (!csrf) return;
    setBusy(true);
    try {
      onChange({
        ...agent,
        ...(await setGovernance(csrf, agent.id, { lifecycle })),
      });
    } finally {
      setBusy(false);
    }
  };
  return (
    <li
      className="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-3 gap-y-2 py-3 md:grid-cols-[auto_minmax(0,1.6fr)_minmax(0,1fr)_auto]"
      data-testid={`gov-row-${agent.display_name}`}
    >
      <AgentGlyph
        name={agent.display_name}
        providers={agent.providers}
        size={36}
        observed={agent.confidence === "observed"}
      />
      <div className="min-w-0">
        <p className="flex flex-wrap items-center gap-2">
          <Link
            href={`/agents/${agent.id}`}
            className="truncate text-body font-semibold text-ink hover:underline"
          >
            {agent.display_name}
          </Link>
          <LifecycleBadge agent={agent} compactMode />
        </p>
        <p className="truncate text-micro text-ink-muted">
          <span className="font-mono">{where}</span>
          {" · "}
          {agent.owner?.name ?? t("gov.noOwner")}
          {agent.purpose ? ` · ${agent.purpose}` : ""}
        </p>
      </div>
      <p className="col-span-2 text-micro tabular-nums text-ink-secondary md:col-span-1">
        {compact(callsOf(agent), locale)} {t("card.calls")}
        {agent.cost_usd ? ` · ${usd(agent.cost_usd, locale)}` : ""}
        {" · "}
        {t("gov.firstSeen", { when: fmt.relative(agent.first_seen_at) })}
      </p>
      {agent.can_manage && csrf ? (
        <div className="col-span-2 flex gap-2 md:col-span-1 md:justify-end">
          {agent.lifecycle !== "approved" ? (
            <button
              type="button"
              disabled={busy}
              onClick={() => act("approved")}
              data-testid={`gov-approve-${agent.display_name}`}
              className="h-8 rounded-control bg-brand px-3 text-caption font-semibold text-white hover:opacity-90 disabled:opacity-50"
            >
              {t("gov.approve")}
            </button>
          ) : null}
          {agent.lifecycle !== "retired" ? (
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                if (
                  window.confirm(
                    t("gov.confirmRetire", { name: agent.display_name }),
                  )
                )
                  void act("retired");
              }}
              className="h-8 rounded-control border border-border px-3 text-caption font-medium text-ink-secondary hover:bg-surface-hover"
            >
              {t("gov.retire")}
            </button>
          ) : (
            <button
              type="button"
              disabled={busy}
              onClick={() => act("observed")}
              className="h-8 rounded-control border border-border px-3 text-caption font-medium text-ink-secondary hover:bg-surface-hover"
            >
              {t("gov.reopen")}
            </button>
          )}
        </div>
      ) : null}
    </li>
  );
}

export function GovernanceView({ span }: { span: Window }) {
  const t = useT("agents");
  const [data, setData] = useState<AgentInventory | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    setData(null);
    fetchAgents(span, "all")
      .then((body) => active && setData(body))
      .catch(() => active && setFailed(true));
    return () => {
      active = false;
    };
  }, [span]);

  const replace = (next: Agent) =>
    setData((prev) =>
      prev
        ? {
            ...prev,
            items: prev.items.map((a) =>
              a.id === next.id ? { ...a, ...next } : a,
            ),
          }
        : prev,
    );

  const groups = useMemo(() => {
    const items = data?.items ?? [];
    const review = items
      .filter((a) => a.lifecycle === "observed")
      .sort(
        (a, b) =>
          Number(Boolean(b.shadow)) - Number(Boolean(a.shadow)) ||
          callsOf(b) - callsOf(a),
      );
    return {
      review,
      approved: items.filter((a) => a.lifecycle === "approved"),
      retired: items
        .filter((a) => a.lifecycle === "retired")
        .sort(
          (a, b) =>
            Number(Boolean(b.seen_after_retirement)) -
            Number(Boolean(a.seen_after_retirement)),
        ),
      counts: {
        approved: items.filter((a) => a.lifecycle === "approved").length,
        review: review.length,
        shadow: items.filter((a) => a.shadow).length,
        noOwner: items.filter((a) => !a.owner && a.lifecycle !== "retired")
          .length,
        seenAgain: items.filter((a) => a.seen_after_retirement).length,
      },
    };
  }, [data]);

  if (failed)
    return (
      <Panel>
        <DataState kind="error" />
      </Panel>
    );
  if (!data)
    return (
      <Panel>
        <DataState kind="loading" />
      </Panel>
    );

  return (
    <div className="space-y-6" data-testid="agents-governance">
      <p className="max-w-4xl text-caption text-ink-secondary">
        {t("gov.hint")}
      </p>
      <div className="page-grid" data-cols="4">
        <KpiTile
          icon={CheckCircle2}
          label={t("gov.kpi.approved")}
          value={groups.counts.approved}
        />
        <KpiTile
          icon={ShieldQuestion}
          label={t("gov.kpi.review")}
          value={groups.counts.review}
        />
        <KpiTile
          icon={EyeOff}
          tone={groups.counts.shadow ? "warning" : undefined}
          label={t("gov.kpi.shadow")}
          value={groups.counts.shadow}
          data-testid="gov-kpi-shadow"
        >
          <p className="text-micro text-ink-muted">{t("gov.kpi.shadowHint")}</p>
        </KpiTile>
        <KpiTile
          icon={UserRound}
          label={t("gov.kpi.noOwner")}
          value={groups.counts.noOwner}
        />
      </div>
      {groups.counts.seenAgain ? (
        <p className="flex items-center gap-1.5 text-caption font-medium text-warning">
          <Archive aria-hidden className="h-4 w-4" />
          {t("gov.seenAgain", { count: groups.counts.seenAgain })}
        </p>
      ) : null}

      <Panel>
        <PanelHeader title={`${t("gov.review")} · ${groups.review.length}`} />
        {groups.review.length === 0 ? (
          <p className="py-4 text-center text-caption text-ink-muted">
            {t("gov.reviewEmpty")}
          </p>
        ) : (
          <ul className="divide-y divide-border">
            {groups.review.map((a) => (
              <Row key={a.id} agent={a} onChange={replace} />
            ))}
          </ul>
        )}
      </Panel>
      {groups.approved.length ? (
        <Panel>
          <PanelHeader
            title={`${t("gov.approvedList")} · ${groups.approved.length}`}
          />
          <ul className="divide-y divide-border">
            {groups.approved.map((a) => (
              <Row key={a.id} agent={a} onChange={replace} />
            ))}
          </ul>
        </Panel>
      ) : null}
      {groups.retired.length ? (
        <Panel>
          <PanelHeader
            title={`${t("gov.retiredList")} · ${groups.retired.length}`}
          />
          <ul className="divide-y divide-border">
            {groups.retired.map((a) => (
              <Row key={a.id} agent={a} onChange={replace} />
            ))}
          </ul>
        </Panel>
      ) : null}
    </div>
  );
}
