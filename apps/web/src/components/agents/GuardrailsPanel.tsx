"use client";

/**
 * An agent's guardrails: four lines it should not cross, where it stands
 * against each, and — for whoever may set them — the controls to do so.
 *
 * Each row reads in one glance: the state chip, the rule in words, and a
 * gauge whose tick is the threshold, so "how close" is visible before it
 * fires.
 */

import { BellRing, Clock, Coins, Moon, ShieldAlert } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { DataState } from "@/components/state/DataState";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import {
  fetchGuardrails,
  fromInput,
  GUARD_KINDS,
  removeGuardrail,
  setGuardrail,
  toInput,
  usd,
  type GuardKind,
  type Guardrail,
  type Guardrails,
} from "@/lib/agents";
import { useFormat, useLocale, useT } from "@/lib/i18n";
import { useSession } from "@/lib/session";

const ICON: Record<GuardKind, typeof Clock> = {
  failure_ratio: ShieldAlert,
  avg_run_seconds: Clock,
  cost_day: Coins,
  silence_hours: Moon,
};

function useShow() {
  const fmt = useFormat();
  const { locale } = useLocale();
  return (kind: GuardKind, value: number | null): string => {
    if (value === null || value === undefined) return "—";
    if (kind === "failure_ratio")
      return `${fmt.number(value * 100, { maximumFractionDigits: 1 })}%`;
    if (kind === "avg_run_seconds") return fmt.latency(value * 1000);
    if (kind === "cost_day") return usd(value, locale);
    return `${fmt.number(value, { maximumFractionDigits: 1 })} h`;
  };
}

function StateChip({ item }: { item: Guardrail | null }) {
  const t = useT("agents");
  const state = !item?.set
    ? "unset"
    : !item.enabled
      ? "off"
      : (item.state ?? "pending");
  const tone =
    state === "firing"
      ? "bg-critical-soft text-critical"
      : state === "ok"
        ? "bg-healthy-soft text-healthy"
        : "bg-surface-3 text-ink-secondary";
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-micro font-semibold ${tone}`}
    >
      {state === "firing" ? (
        <span className="relative flex h-2 w-2">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[var(--status-critical)] opacity-60 motion-reduce:hidden" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-[var(--status-critical)]" />
        </span>
      ) : null}
      {t.dyn("guard.state", state, state)}
    </span>
  );
}

/** value against threshold: the tick sits at 2/3, a breach runs past it. */
function Gauge({
  value,
  threshold,
  firing,
}: {
  value: number | null;
  threshold: number;
  firing: boolean;
}) {
  const share = value === null ? 0 : Math.min(1, value / threshold / 1.5);
  return (
    <span
      className="relative block h-1.5 w-full rounded-full bg-surface-3"
      aria-hidden
    >
      <span
        className="absolute inset-y-0 left-0 rounded-full"
        style={{
          width: `${share * 100}%`,
          background: firing
            ? "var(--status-critical)"
            : share > 0.55
              ? "var(--status-warning)"
              : "var(--status-success)",
        }}
      />
      <span
        className="absolute -top-1 h-3.5 w-0.5 rounded bg-ink"
        style={{ left: "66.6%" }}
      />
    </span>
  );
}

function Row({
  kind,
  item,
  data,
  onSaved,
}: {
  kind: GuardKind;
  item: Guardrail;
  data: Guardrails;
  onSaved: (next: Guardrails) => void;
}) {
  const t = useT("agents");
  const fmt = useFormat();
  const show = useShow();
  const session = useSession().state;
  const csrf =
    session.status === "authenticated" ? session.me.csrf_token : null;
  const Icon = ICON[kind];
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState<string>("");
  const [severity, setSeverity] = useState<"warning" | "critical">("warning");
  const [enabled, setEnabled] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);

  const open = () => {
    setValue(
      item.threshold !== null ? String(toInput(kind, item.threshold)) : "",
    );
    setSeverity(item.severity ?? "warning");
    setEnabled(item.set ? item.enabled : true);
    setError(false);
    setEditing(true);
  };
  const save = async () => {
    const number = Number(value);
    if (!csrf || !Number.isFinite(number) || number <= 0) return setError(true);
    setBusy(true);
    try {
      onSaved(
        await setGuardrail(csrf, data.agent_id, kind, {
          threshold: fromInput(kind, number),
          severity,
          enabled,
        }),
      );
      setEditing(false);
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    if (!csrf) return;
    setBusy(true);
    try {
      onSaved(await removeGuardrail(csrf, data.agent_id, kind));
      setEditing(false);
    } finally {
      setBusy(false);
    }
  };

  const firing = item.set && item.enabled && item.state === "firing";
  const limit = item.threshold !== null ? show(kind, item.threshold) : "…";

  return (
    <li
      className={`grid gap-3 rounded-control border px-3.5 py-3 md:grid-cols-[minmax(0,1fr)_14rem_auto] md:items-center ${
        firing
          ? "border-[var(--status-critical)] bg-critical-soft/40"
          : "border-border"
      }`}
      data-testid={`guard-${kind}`}
    >
      <div className="flex min-w-0 items-start gap-3">
        <span
          className={`mt-0.5 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-control ${
            firing
              ? "bg-critical-soft text-critical"
              : "bg-surface-2 text-ink-secondary"
          }`}
        >
          <Icon aria-hidden className="h-4 w-4" />
        </span>
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2">
            <span className="text-body font-semibold text-ink">
              {t.dyn("guard.kind", kind, kind)}
            </span>
            <StateChip item={item} />
            {item.set && item.severity === "critical" ? (
              <span className="inline-flex items-center gap-1 text-micro font-medium text-ink-secondary">
                <BellRing aria-hidden className="h-3 w-3" />
                {t("guard.severity.critical")}
              </span>
            ) : null}
          </p>
          <p className="mt-0.5 text-caption text-ink-secondary">
            {t
              .dyn("guard.rule", kind, kind)
              .replace("{limit}", limit)
              .replace("{min}", String(data.min_calls))}
          </p>
          {item.set && item.since && item.state !== "pending" ? (
            <p className="mt-0.5 text-micro text-ink-muted">
              {t("guard.since", { when: fmt.relative(item.since) })}
              {item.incident_id ? (
                <>
                  {" · "}
                  <Link
                    href={`/incidents/${item.incident_id}`}
                    className="font-medium text-critical hover:underline"
                  >
                    {t("guard.incident")}
                  </Link>
                </>
              ) : null}
            </p>
          ) : null}
        </div>
      </div>

      <div className="space-y-1.5">
        {item.set && item.threshold !== null ? (
          <>
            <Gauge
              value={item.value}
              threshold={item.threshold}
              firing={Boolean(firing)}
            />
            <p className="flex justify-between text-micro tabular-nums text-ink-muted">
              <span className={firing ? "font-semibold text-critical" : ""}>
                {t("guard.now", { value: show(kind, item.value) })}
              </span>
              <span>{limit}</span>
            </p>
          </>
        ) : null}
      </div>

      <div className="flex min-w-[4.75rem] items-center justify-end gap-2">
        {data.can_manage && !editing ? (
          <button
            type="button"
            onClick={open}
            className="h-8 rounded-control border border-border px-3 text-caption font-medium text-ink-secondary hover:bg-surface-hover hover:text-ink"
            data-testid={`guard-${kind}-edit`}
          >
            {item.set ? t("guard.edit") : t("guard.set")}
          </button>
        ) : null}
      </div>

      {editing ? (
        <div className="flex flex-wrap items-center gap-3 border-t border-border pt-3 md:col-span-3">
          <label className="inline-flex items-center gap-1.5 text-caption text-ink-secondary">
            <input
              type="number"
              inputMode="decimal"
              min={0}
              step="any"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              aria-label={t.dyn("guard.kind", kind, kind)}
              data-testid={`guard-${kind}-value`}
              className="h-8 w-24 rounded-control border border-border bg-surface px-2 text-right tabular-nums text-ink"
            />
            {t.dyn("guard.unit", kind, "")}
          </label>
          <div
            role="radiogroup"
            aria-label={t("guard.title")}
            className="inline-flex rounded-control border border-border p-0.5"
          >
            {(["warning", "critical"] as const).map((s) => (
              <button
                key={s}
                type="button"
                role="radio"
                aria-checked={severity === s}
                onClick={() => setSeverity(s)}
                className={`h-7 rounded-[6px] px-2.5 text-caption font-medium ${
                  severity === s
                    ? s === "critical"
                      ? "bg-critical-soft text-critical"
                      : "bg-surface-3 text-ink"
                    : "text-ink-secondary"
                }`}
              >
                {t.dyn("guard.severity", s, s)}
              </button>
            ))}
          </div>
          <label className="inline-flex items-center gap-1.5 text-caption text-ink-secondary">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
            />
            {t("guard.enabled")}
          </label>
          <div className="ml-auto flex items-center gap-2">
            {item.set ? (
              <button
                type="button"
                onClick={remove}
                disabled={busy}
                className="h-8 px-2 text-caption font-medium text-critical hover:underline"
              >
                {t("guard.remove")}
              </button>
            ) : null}
            <button
              type="button"
              onClick={() => setEditing(false)}
              className="h-8 px-2 text-caption text-ink-secondary"
            >
              {t("guard.cancel")}
            </button>
            <button
              type="button"
              onClick={save}
              disabled={busy}
              data-testid={`guard-${kind}-save`}
              className="h-8 rounded-control bg-brand px-3 text-caption font-semibold text-white hover:opacity-90 disabled:opacity-50"
            >
              {t("guard.save")}
            </button>
          </div>
          {error ? (
            <p className="w-full text-micro text-critical">
              {t("guard.failed")}
            </p>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

export function GuardrailsPanel({ agentId }: { agentId: string }) {
  const t = useT("agents");
  const [data, setData] = useState<Guardrails | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    fetchGuardrails(agentId)
      .then((body) => active && setData(body))
      .catch(() => active && setFailed(true));
    return () => {
      active = false;
    };
  }, [agentId]);

  return (
    <Panel data-testid="agent-guardrails">
      <PanelHeader title={t("guard.title")} description={t("guard.hint")} />
      {failed ? <DataState kind="error" /> : null}
      {!failed && !data ? <DataState kind="loading" /> : null}
      {data ? (
        <>
          {!data.pages ? (
            <p className="mb-3 text-micro text-warning">{t("guard.noPage")}</p>
          ) : null}
          {!data.evaluating ? (
            <p className="mb-3 text-micro text-ink-muted">{t("guard.off")}</p>
          ) : null}
          <ul className="space-y-2.5">
            {GUARD_KINDS.map((kind) => {
              const item = data.items.find((i) => i.kind === kind);
              return item ? (
                <Row
                  key={kind}
                  kind={kind}
                  item={item}
                  data={data}
                  onSaved={setData}
                />
              ) : null;
            })}
          </ul>
        </>
      ) : null}
    </Panel>
  );
}
