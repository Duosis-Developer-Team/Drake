"use client";

import Link from "next/link";

import { LoadGate, useApi } from "@/components/catalog/primitives";
import { StatusMatrix } from "@/components/charts/visuals";
import { humanize, toneForHealth } from "@/lib/design/status";
import { DataState } from "@/components/state/DataState";
import { StatusBadge, type HealthStatus } from "@/components/state/StatusBadge";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import type { IntegrationHealth } from "@/lib/catalog";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";

const OBSERVED_STATUS: Record<string, HealthStatus> = {
  ok: "healthy",
  degraded: "critical",
  stale: "stale",
  unknown: "unknown",
  not_configured: "unknown",
};

export default function IntegrationsPage() {
  const [health, retry] = useApi<{ integrations: IntegrationHealth[]; next_cursor: string | null }>(
    "/v1/integrations/health",
  );

  return (
    <PageFrame>
      <PageHeader
        title="Integration Health"
        description="Connector configuration and observed state per scope. Providers are not yet connected in this phase."
        actions={
          <Link
            href="/integrations/github"
            className="rounded-control border border-border px-2.5 py-1 text-caption font-medium text-ink transition-colors hover:bg-surface-hover"
          >
            GitHub App integration
          </Link>
        }
      />
      <div className="space-y-5">
      <Panel
        className="motion-safe:animate-[fade-in_400ms_var(--ease-entrance)_backwards]"
      >
        <LoadGate value={health} retry={retry}>
          {(body) =>
            body.integrations.length === 0 ? (
              <DataState
                kind="empty"
                title="No integrations in your scope"
                description="Integrations registered on scopes you can read will appear here."
              />
            ) : (
              <>
              {/* Provider × scope is a matrix, and the gaps are the point: a
                  grid of tones shows which scope is missing which provider
                  from across the room, where a column of words makes the
                  reader compare strings. The list underneath keeps every
                  detail — this is the index, not a replacement. */}
              <div className="border-b border-border pb-4">
                <PanelHeader title="Provider readiness by scope" />
                <StatusMatrix
                  label="Provider readiness by scope"
                  rowLabel="Scope"
                  rows={[
                    ...new Map(
                      body.integrations.map((integration) => [
                        `${integration.scope.type}/${integration.scope.ref}`,
                        {
                          key: `${integration.scope.type}/${integration.scope.ref}`,
                          label: integration.scope.ref,
                          sub: integration.scope.type,
                        },
                      ]),
                    ).values(),
                  ]}
                  columns={[
                    ...new Map(
                      body.integrations.map((integration) => [
                        integration.integration_type,
                        { key: integration.integration_type, label: integration.integration_type },
                      ]),
                    ).values(),
                  ]}
                  cell={(scopeKey, type) => {
                    const match = body.integrations.find(
                      (integration) =>
                        `${integration.scope.type}/${integration.scope.ref}` === scopeKey &&
                        integration.integration_type === type,
                    );
                    if (!match) return null;
                    if (match.configuration_state !== "configured") {
                      return { tone: "not-applicable", label: "Not connected" };
                    }
                    return {
                      tone: toneForHealth(match.observed_state),
                      label: humanize(match.observed_state),
                    };
                  }}
                />
              </div>
              <ul className="divide-y divide-border" data-testid="integration-table">
                {body.integrations.map((integration) => (
                  <li
                    key={`${integration.integration_type}-${integration.scope.type}-${integration.scope.ref}`}
                    className="flex flex-wrap items-start justify-between gap-4 px-4 py-4 transition-colors hover:bg-surface-hover"
                  >
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-body font-semibold text-ink">
                          {integration.integration_type}
                        </span>
                        <StatusBadge
                          status={
                            integration.configuration_state === "configured"
                              ? "maintenance"
                              : "unknown"
                          }
                          label={integration.configuration_state}
                          size="compact"
                        />
                        <StatusBadge
                          status={OBSERVED_STATUS[integration.observed_state] ?? "unknown"}
                          label={integration.observed_state}
                          size="compact"
                        />
                      </div>
                      <span className="mt-1 block font-mono text-micro text-ink-muted">
                        {integration.scope.type}/{integration.scope.ref}
                      </span>
                    </div>
                    <div className="shrink-0 text-right text-micro text-ink-muted">
                      <div className="font-mono">
                        {integration.last_success_at
                          ? integration.last_success_at.slice(0, 19)
                          : "never"}
                      </div>
                      {integration.last_error_code ? (
                        <div className="font-mono text-critical">{integration.last_error_code}</div>
                      ) : null}
                    </div>
                  </li>
                ))}
              </ul>
              </>
            )
          }
        </LoadGate>
      </Panel>
      </div>
    </PageFrame>
  );
}
