"use client";

/**
 * Generic dashboard renderer: fetches a registry-defined dashboard template
 * and drives one telemetry query per referenced query template (widgets
 * sharing a template share the envelope). Profile-gated widgets are hidden
 * when the scope's runtime profile does not provide them.
 *
 * Query execution is a bounded worker queue: at most MAX_CONCURRENT_QUERIES
 * run at once per dashboard instance, so a single screen can never exhaust
 * the backend's per-principal concurrency budget on its own. Every
 * generation (scope/range/profile/template change, retry, unmount) aborts
 * its in-flight requests via AbortController, queued work from a stale
 * generation never starts, and stale results can never write into newer
 * state. A real backend 429 renders as "throttled" — never misclassified
 * as provider unavailability.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { SectionHeader } from "@/components/ui/Panel";
import { ErrorState, LoadingSkeleton } from "@/components/ui/states";
import {
  KpiWidget,
  StatusWidget,
  TimeseriesWidget,
  type WidgetState,
} from "@/components/telemetry/widgets";
import { ApiError } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { DashboardDefinition, RangePreset } from "@/lib/telemetry";
import { fetchDashboard, queryTelemetry } from "@/lib/telemetry";

export const MAX_CONCURRENT_QUERIES = 3;
// A backend 429 is transient by construction (leases free within seconds):
// each widget retries ONCE, after a bounded delay, through the same bounded
// queue — never an uncontrolled fan-out, always abandoned with its
// generation.
export const THROTTLE_RETRY_DELAY_MS = 2500;

function classifyError(error: unknown): WidgetState {
  if (error instanceof ApiError) {
    if (error.status === 403 || error.status === 404) return { kind: "denied" };
    if (error.status === 429) {
      return { kind: "throttled", correlationId: error.correlationId };
    }
    if (error.status === 502 || error.status === 503) {
      return { kind: "unavailable", correlationId: error.correlationId };
    }
    return { kind: "error", correlationId: error.correlationId };
  }
  return { kind: "error" };
}

export function DashboardRenderer({
  templateKey,
  scopeType,
  scopeId,
  preset,
  profile,
  showSectionHeadings = true,
}: {
  templateKey: string;
  scopeType: "environment" | "service" | "cluster";
  scopeId: string;
  preset: RangePreset;
  profile?: string;
  /** Off when the page already titles the section — the template's section
   *  title and the page heading are otherwise the same words twice. */
  showSectionHeadings?: boolean;
}) {
  const { state: sessionState } = useSession();
  const me = sessionState.status === "authenticated" ? sessionState.me : null;
  const [dashboard, setDashboard] = useState<DashboardDefinition | null>(null);
  const [dashboardError, setDashboardError] = useState<string | null>(null);
  const [states, setStates] = useState<Record<string, WidgetState>>({});
  const [nonce, setNonce] = useState(0);
  const generationRef = useRef(0);

  useEffect(() => {
    let cancelled = false;
    fetchDashboard(templateKey)
      .then((definition) => {
        if (!cancelled) setDashboard(definition);
      })
      .catch(() => {
        if (!cancelled) setDashboardError("Could not load the dashboard definition.");
      });
    return () => {
      cancelled = true;
    };
  }, [templateKey]);

  useEffect(() => {
    if (!dashboard || !me) return;
    // New generation: anything from previous generations is dead on arrival.
    const generation = ++generationRef.current;
    const controller = new AbortController();

    const queue: string[] = [];
    for (const section of dashboard.sections) {
      for (const widget of section.widgets) {
        if (widget.requiredProfile && widget.requiredProfile !== profile) continue;
        if (!queue.includes(widget.queryTemplateKey)) queue.push(widget.queryTemplateKey);
      }
    }
    setStates(Object.fromEntries(queue.map((key) => [key, { kind: "loading" as const }])));

    const csrfToken = me.csrf_token;
    // TRUE capacity-accounted scheduler: `active` is the single source of
    // truth for in-flight queries. NOTHING — initial work, queued work, or
    // a throttle retry — may start a fetch unless active < the budget.
    // Retry timers only re-enqueue and wake the pump; they can never start
    // a fetch themselves.
    let next = 0;
    let active = 0;
    const retried = new Set<string>();
    const retryTimers: ReturnType<typeof setTimeout>[] = [];
    const pump = (): void => {
      // A stale generation must not start queued work.
      if (generation !== generationRef.current || controller.signal.aborted) return;
      while (active < MAX_CONCURRENT_QUERIES && next < queue.length) {
        const key = queue[next];
        next += 1;
        active += 1; // incremented exactly once per started query
        queryTelemetry(
          csrfToken,
          { templateKey: key, scopeType, scopeId, preset },
          controller.signal,
        )
          .then((envelope) => {
            if (generation === generationRef.current) {
              setStates((previous) => ({ ...previous, [key]: { kind: "ready", envelope } }));
            }
          })
          .catch((error: unknown) => {
            // Abort of an outdated generation is not an error state.
            if (generation !== generationRef.current || controller.signal.aborted) return;
            const state = classifyError(error);
            if (state.kind === "throttled" && !retried.has(key)) {
              retried.add(key); // at most ONE automatic retry per template
              retryTimers.push(
                setTimeout(() => {
                  if (generation !== generationRef.current || controller.signal.aborted) {
                    return;
                  }
                  // Re-enqueue only — the pump enforces capacity.
                  queue.push(key);
                  setStates((previous) => ({ ...previous, [key]: { kind: "loading" } }));
                  pump();
                }, THROTTLE_RETRY_DELAY_MS),
              );
            }
            setStates((previous) => ({ ...previous, [key]: state }));
          })
          .finally(() => {
            active -= 1; // decremented exactly once on every settle path
            pump();
          });
      }
    };
    pump();

    return () => {
      // In-flight requests are truly cancelled, not just ignored; pending
      // throttle retries die with their generation and queued retries can
      // never start (generation guard in pump).
      for (const timer of retryTimers) clearTimeout(timer);
      controller.abort();
    };
  }, [dashboard, me, scopeType, scopeId, preset, profile, nonce]);

  const retry = useCallback(() => setNonce((value) => value + 1), []);

  if (dashboardError) {
    return <ErrorState title="Dashboard unavailable" description={dashboardError} onRetry={retry} />;
  }
  if (!dashboard) {
    return <LoadingSkeleton variant="chart" label="Loading dashboard definition" />;
  }

  return (
    <div className="space-y-5" data-testid={`dashboard-${dashboard.key}`}>
      {dashboard.sections.map((section) => {
        const widgets = section.widgets.filter(
          (widget) => !widget.requiredProfile || widget.requiredProfile === profile,
        );
        if (widgets.length === 0) return null;
        return (
          <section key={section.key} aria-label={section.title}>
            {showSectionHeadings ? <SectionHeader title={section.title} /> : null}
            <div
              className={`grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4 ${
                showSectionHeadings ? "mt-3" : ""
              }`}
            >
              {widgets.map((widget) => {
                const state = states[widget.queryTemplateKey] ?? { kind: "loading" as const };
                const props = { widget, state, onRetry: retry };
                const cell =
                  widget.display === "timeseries" ? (
                    <div className="sm:col-span-2 xl:col-span-4">
                      <TimeseriesWidget {...props} />
                    </div>
                  ) : widget.display === "status" ? (
                    <StatusWidget {...props} />
                  ) : (
                    <KpiWidget {...props} />
                  );
                return (
                  <div key={widget.key} className="contents">
                    {cell}
                  </div>
                );
              })}
            </div>
          </section>
        );
      })}
    </div>
  );
}
