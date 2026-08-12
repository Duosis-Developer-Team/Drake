"use client";

import Link from "next/link";

import { LoadGate, useApi } from "@/components/catalog/primitives";
import { StatusMatrix } from "@/components/charts/visuals";
import { humanize, toneForHealth } from "@/lib/design/status";
import { DataState } from "@/components/state/DataState";
import { StatusBadge, type HealthStatus } from "@/components/state/StatusBadge";
import { Card } from "@/components/ui/Card";
import type { IntegrationHealth } from "@/lib/catalog";
import { PageFrame } from "@/components/shell/AppShell";

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
      <div className="space-y-5">
      <div>
        <h1 className="text-title font-semibold text-ink">
          Integration Health
        </h1>
        <p className="mt-1 max-w-3xl text-caption text-ink-secondary">
          Connector configuration and observed state per scope. Providers are
          not yet connected in this phase.
        </p>
      </div>
      <div>
        <Link
          href="/integrations/github"
          className="inline-flex rounded-lg border border-border px-2.5 py-1 text-xs font-medium text-ink-secondary hover:bg-surface-sunken"
        >
          GitHub App integration
        </Link>
      </div>
      <Card>
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
                  reader compare strings. The table underneath keeps every
                  detail — this is the index, not a replacement. */}
              <div className="mb-4 border-b border-border pb-4">
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
              <div className="overflow-x-auto">
                <table className="w-full text-sm" data-testid="integration-table">
                  <thead>
                    <tr className="border-b border-border text-left text-caption text-ink-secondary">
                      <th className="px-2 py-2">Type</th>
                      <th className="px-2 py-2">Scope</th>
                      <th className="px-2 py-2">Configuration</th>
                      <th className="px-2 py-2">Observed</th>
                      <th className="px-2 py-2">Last success</th>
                      <th className="px-2 py-2">Error</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {body.integrations.map((integration) => (
                      <tr
                        key={`${integration.integration_type}-${integration.scope.type}-${integration.scope.ref}`}
                      >
                        <td className="px-2 py-2 font-mono text-xs text-ink">
                          {integration.integration_type}
                        </td>
                        <td className="px-2 py-2 font-mono text-xs text-ink-secondary">
                          {integration.scope.type}/{integration.scope.ref}
                        </td>
                        <td className="px-2 py-2">
                          <StatusBadge
                            status={
                              integration.configuration_state === "configured"
                                ? "maintenance"
                                : "unknown"
                            }
                            label={integration.configuration_state}
                          />
                        </td>
                        <td className="px-2 py-2">
                          <StatusBadge
                            status={
                              OBSERVED_STATUS[integration.observed_state] ?? "unknown"
                            }
                            label={integration.observed_state}
                          />
                        </td>
                        <td className="px-2 py-2 font-mono text-xs text-ink-secondary">
                          {integration.last_success_at
                            ? integration.last_success_at.slice(0, 19)
                            : "never"}
                        </td>
                        <td className="px-2 py-2 font-mono text-xs text-ink-secondary">
                          {integration.last_error_code ?? "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              </>
            )
          }
        </LoadGate>
      </Card>
      </div>
    </PageFrame>
  );
}
