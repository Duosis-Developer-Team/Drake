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
}: {
  templateKey: string;
  scopeType: "environment" | "service" | "cluster";
  scopeId: string;
  preset: RangePreset;
  profile?: string;
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
    let next = 0;
    const runNext = (): void => {
      // A stale generation must not start queued work.
      if (generation !== generationRef.current || controller.signal.aborted) return;
      if (next >= queue.length) return;
      const key = queue[next];
      next += 1;
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
          setStates((previous) => ({ ...previous, [key]: classifyError(error) }));
        })
        .finally(() => {
          runNext();
        });
    };
    for (let worker = 0; worker < MAX_CONCURRENT_QUERIES; worker += 1) {
      runNext();
    }

    return () => {
      // In-flight requests are truly cancelled, not just ignored.
      controller.abort();
    };
  }, [dashboard, me, scopeType, scopeId, preset, profile, nonce]);

  const retry = useCallback(() => setNonce((value) => value + 1), []);

  if (dashboardError) {
    return <p className="text-sm text-ink-muted">{dashboardError}</p>;
  }
  if (!dashboard) {
    return (
      <div aria-hidden className="h-40 animate-pulse rounded-xl border border-border bg-surface" />
    );
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
            <h3 className="mb-2 text-sm font-semibold text-ink">{section.title}</h3>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
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
