"use client";

/**
 * Generic dashboard renderer: fetches a registry-defined dashboard template
 * and drives one telemetry query per referenced query template (widgets
 * sharing a template share the envelope). Profile-gated widgets are hidden
 * when the scope's runtime profile does not provide them. Every widget
 * carries its own state machine — the dashboard never fakes a value.
 */

import { useCallback, useEffect, useState } from "react";

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
    let cancelled = false;
    const templateKeys = new Set<string>();
    for (const section of dashboard.sections) {
      for (const widget of section.widgets) {
        if (widget.requiredProfile && widget.requiredProfile !== profile) continue;
        templateKeys.add(widget.queryTemplateKey);
      }
    }
    setStates(
      Object.fromEntries([...templateKeys].map((key) => [key, { kind: "loading" as const }])),
    );
    for (const key of templateKeys) {
      queryTelemetry(me.csrf_token, { templateKey: key, scopeType, scopeId, preset })
        .then((envelope) => {
          if (!cancelled) {
            setStates((previous) => ({ ...previous, [key]: { kind: "ready", envelope } }));
          }
        })
        .catch((error: unknown) => {
          if (cancelled) return;
          let next: WidgetState = { kind: "error" };
          if (error instanceof ApiError) {
            if (error.status === 403 || error.status === 404) {
              next = { kind: "denied" };
            } else if (error.status === 502 || error.status === 503) {
              next = { kind: "unavailable", correlationId: error.correlationId };
            } else {
              next = { kind: "error", correlationId: error.correlationId };
            }
          }
          setStates((previous) => ({ ...previous, [key]: next }));
        });
    }
    return () => {
      cancelled = true;
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
                return <div key={widget.key} className="contents">{cell}</div>;
              })}
            </div>
          </section>
        );
      })}
    </div>
  );
}
