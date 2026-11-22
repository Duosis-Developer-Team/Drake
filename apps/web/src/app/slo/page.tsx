"use client";

/**
 * Service objectives, as a board.
 *
 * The posture band on top counts every objective by verdict (each verdict a
 * filter); below, the objectives are cards grouped by application, each
 * keeping four numbers deliberately apart: the objective, what compliance
 * actually was, how much error budget is left, and whether it is burning.
 *
 * `insufficient_data`, `stale`, `query_failed` and `not_configured` each
 * render as themselves. None of them renders as healthy, and none renders
 * as 0% — "we could not measure" and "we measured zero" are different
 * answers and this screen keeps them apart.
 */

import { Target } from "lucide-react";
import { Suspense, useEffect, useState } from "react";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";

import { ObjectiveGroups, PostureBand } from "@/components/alerting/SloBoard";
import {
  NewSloButton,
  SloComposer,
  SloSuggestions,
} from "@/components/alerting/SloComposer";
import { ListFooter, useCursorList, useTotal, useUrlFilters } from "@/components/ops/ListKit";
import { EmptyHero, FactPill, StatePad } from "@/components/incidents/OpsKit";
import { DataState } from "@/components/state/DataState";
import { Panel } from "@/components/ui/Panel";
import { useT } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import {
  SLO_EXPLANATIONS,
  SLO_LABELS,
  fetchSloOptions,
  sloListPath,
  type SloOptions,
  type Slo,
  type SloStatus,
} from "@/lib/alerting";

const STATES: SloStatus[] = [
  "healthy",
  "warning",
  "critical",
  "exhausted",
  "insufficient_data",
  "stale",
  "query_failed",
  "not_configured",
];

const FILTER_KEYS = ["status"] as const;

function withLimit(path: string): string {
  return `${path}${path.includes("?") ? "&" : "?"}limit=1`;
}

function SloInner({ onChanged }: { onChanged: () => void }) {
  const t = useT("alerting");
  const { state: session } = useSession();
  const csrfToken =
    session.status === "authenticated" ? session.me.csrf_token : null;
  const [options, setOptions] = useState<SloOptions | null>(null);
  const [composing, setComposing] = useState(false);
  useEffect(() => {
    let active = true;
    fetchSloOptions()
      .then((value) => active && setOptions(value))
      .catch(() => active && setOptions(null));
    return () => {
      active = false;
    };
  }, []);
  const canCompose = Boolean(
    options?.can_manage && csrfToken && options.services.length > 0,
  );
  const [filters, update] = useUrlFilters(FILTER_KEYS);
  const status = (STATES as string[]).includes(filters.status)
    ? filters.status
    : "";
  const [list, retry] = useCursorList<Slo>(sloListPath({ status }), {
    paging: "offset",
  });
  const items = list.state === "ready" ? list.items : [];

  // Server totals, not a tally of the loaded page. Breached spans three
  // states, so it is a sum; "never measured" also covers objectives with no
  // evaluation at all, so it is what remains of the whole.
  const all = useTotal(withLimit(sloListPath({})));
  const meeting = useTotal(withLimit(sloListPath({ status: "healthy" })));
  const burning = useTotal(withLimit(sloListPath({ status: "warning" })));
  const critical = useTotal(withLimit(sloListPath({ status: "critical" })));
  const exhausted = useTotal(withLimit(sloListPath({ status: "exhausted" })));
  const failed = useTotal(withLimit(sloListPath({ status: "query_failed" })));
  const stale = useTotal(withLimit(sloListPath({ status: "stale" })));
  const sum = (values: (number | null)[]) =>
    values.some((v) => v === null)
      ? null
      : values.reduce<number>((a, v) => a + (v ?? 0), 0);
  const breached = sum([critical, exhausted, failed]);
  const known = sum([meeting, burning, breached, stale]);
  const neverMeasured =
    all === null || known === null ? null : Math.max(0, all - known);
  const toggle = (value: string) =>
    update({ status: status === value ? "" : value });

  return (
    <PageFrame>
      <PageHeader
        title={t("sloList.title")}
        description={t("sloList.description")}
        actions={
          canCompose ? (
            <NewSloButton onClick={() => setComposing(true)} />
          ) : undefined
        }
      />
      {options && canCompose && csrfToken ? (
        <SloComposer
          open={composing}
          onClose={() => setComposing(false)}
          options={options}
          csrfToken={csrfToken}
          onCreated={onChanged}
        />
      ) : null}
      <div className="flex flex-col gap-6">
        <PostureBand
          total={all}
          active={status}
          onPick={toggle}
          slices={[
            {
              key: "meeting",
              label: t("sloList.kpi.meeting"),
              caption: t("sloList.kpi.meetingCaption"),
              value: meeting,
              tone: "success",
              status: "healthy",
              testId: "alerting-kpi-meeting",
            },
            {
              key: "burning",
              label: t("sloList.kpi.burning"),
              caption: t("sloList.kpi.burningCaption"),
              value: burning,
              tone: "warning",
              status: "warning",
              testId: "alerting-kpi-burning-fast",
            },
            {
              key: "breached",
              label: t("sloList.kpi.breached"),
              caption: t("sloList.kpi.breachedCaption"),
              value: breached,
              tone: "critical",
              testId: "alerting-kpi-breached",
            },
            {
              key: "never",
              label: t("sloList.kpi.neverMeasured"),
              caption: t("sloList.kpi.neverMeasuredCaption"),
              value: neverMeasured,
              tone: "unknown",
              testId: "alerting-kpi-never-measured",
            },
          ]}
        />

        {status ? (
          <div className="-mt-2 flex items-center gap-2 text-caption text-ink-secondary" data-testid="slo-filters">
            <span>
              {t("sloBoard.filtered", {
                state: t.dyn("slo", status, SLO_LABELS[status as SloStatus]),
              })}
            </span>
            <button
              type="button"
              onClick={() => update({ status: "" })}
              className="rounded-full border border-border px-2.5 py-0.5 font-medium text-ink hover:bg-surface-hover"
            >
              {t("sloBoard.clearFilter")}
            </button>
          </div>
        ) : null}

        {list.state === "loading" ? (
          <Panel>
            <StatePad>
              <DataState kind="loading" />
            </StatePad>
          </Panel>
        ) : list.state === "error" ? (
          <Panel>
            <StatePad>
              {list.denied ? (
                <DataState kind="permission-denied" />
              ) : (
                <DataState kind="error" description={list.message} onRetry={retry} />
              )}
            </StatePad>
          </Panel>
        ) : items.length === 0 ? (
          <Panel flush>
            <EmptyHero
              icon={Target}
              title={t("sloList.empty.title")}
              description={t("sloList.empty.description")}
            >
              <FactPill>
                {t("sloList.empty.state", {
                  value: status
                    ? t.dyn("slo", status, SLO_LABELS[status as SloStatus])
                    : t("sloList.empty.any"),
                })}
              </FactPill>
            </EmptyHero>
          </Panel>
        ) : (
          <>
            <ObjectiveGroups items={items} />
            {list.state === "ready" && list.hasMore ? (
              <Panel flush>
                <ListFooter
                  shown={items.length}
                  summary={t("sloList.shown", {
                    shown: items.length,
                    total: list.total ?? items.length,
                  })}
                  hasMore={list.hasMore}
                  loadingMore={list.loadingMore}
                  moreError={list.moreError}
                  onMore={() => list.loadMore()}
                  moreLabel={t("list.more")}
                  loadingLabel={t("list.loadingMore")}
                  errorLabel={t("list.moreError")}
                />
              </Panel>
            ) : null}

            {/* Anything that is not a measurement is explained in words, so
                nobody reads a dash as a zero. */}
            {items.some(
              (slo) =>
                slo.evaluation === null ||
                ["insufficient_data", "stale", "query_failed", "not_configured"].includes(slo.evaluation.status),
            ) ? (
              <div
                className="space-y-2 rounded-2xl border border-dashed border-border-strong bg-surface-2/40 px-6 py-5"
                data-testid="slo-caveats"
              >
                {items
                  .filter(
                    (slo) =>
                      slo.evaluation !== null &&
                      ["insufficient_data", "stale", "query_failed", "not_configured"].includes(
                        slo.evaluation.status,
                      ),
                  )
                  .map((slo) => (
                    <p key={slo.id} className="text-caption text-ink-secondary">
                      <span className="font-semibold text-ink">{slo.display_name}</span>
                      {" — "}
                      {t.dyn("sloExplanation", slo.evaluation!.status, SLO_EXPLANATIONS[slo.evaluation!.status])}
                    </p>
                  ))}
                {items.some((slo) => slo.evaluation === null) ? (
                  <p className="text-caption text-ink-secondary">{t("sloList.caveat.notEvaluated")}</p>
                ) : null}
              </div>
            ) : null}
          </>
        )}

        {options && csrfToken ? (
          <SloSuggestions options={options} csrfToken={csrfToken} onCreated={onChanged} />
        ) : null}
      </div>
    </PageFrame>
  );
}

export default function SloPage() {
  // A new objective changes the list, every count and the suggestions:
  // remounting reads them all again rather than patching each by hand.
  const [generation, setGeneration] = useState(0);
  return (
    <Suspense fallback={<DataState kind="loading" />}>
      <SloInner
        key={generation}
        onChanged={() => setGeneration((n) => n + 1)}
      />
    </Suspense>
  );
}
