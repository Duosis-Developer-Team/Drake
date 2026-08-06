"use client";

import { LoadGate, useApi } from "@/components/catalog/primitives";
import { DataState } from "@/components/state/DataState";
import { StatusBadge, type HealthStatus } from "@/components/state/StatusBadge";
import { Card } from "@/components/ui/Card";
import type { IntegrationHealth } from "@/lib/catalog";

const OBSERVED_STATUS: Record<string, HealthStatus> = {
  ok: "healthy",
  degraded: "critical",
  stale: "stale",
  unknown: "unknown",
  not_configured: "unknown",
};

export default function IntegrationsPage() {
  const [health, retry] = useApi<{ integrations: IntegrationHealth[] }>(
    "/v1/integrations/health",
  );

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">
          Integration Health
        </h1>
        <p className="mt-1 text-sm text-ink-secondary">
          Connector configuration and observed state per scope. Providers are
          not yet connected in this phase.
        </p>
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
              <div className="overflow-x-auto">
                <table className="w-full text-sm" data-testid="integration-table">
                  <thead>
                    <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-ink-muted">
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
            )
          }
        </LoadGate>
      </Card>
    </div>
  );
}
