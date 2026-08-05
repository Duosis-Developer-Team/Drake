"use client";

import { useCallback, useEffect, useState } from "react";

import { DataState } from "@/components/state/DataState";
import { StatusBadge } from "@/components/state/StatusBadge";
import { Card } from "@/components/ui/Card";
import { ApiError, apiGet, apiMutate } from "@/lib/api";
import { useSession } from "@/lib/session";

interface Grant {
  id: string;
  identity_display: string | null;
  group_display: string | null;
  role_name: string;
  scope_type: string;
  scope_ref: string;
  valid_from: string;
  valid_to: string | null;
  revoked_at: string | null;
}

type Loadable<T> =
  | { state: "loading" }
  | { state: "error"; message: string }
  | { state: "ready"; data: T };

export function GrantsPanel() {
  const { state: session } = useSession();
  const csrf = session.status === "authenticated" ? session.me.csrf_token : "";
  const [grants, setGrants] = useState<Loadable<Grant[]>>({ state: "loading" });
  const [actionError, setActionError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setGrants({ state: "loading" });
    try {
      const body = await apiGet<{ grants: Grant[] }>("/v1/grants");
      setGrants({ state: "ready", data: body.grants });
    } catch (error) {
      setGrants({
        state: "error",
        message: error instanceof ApiError ? error.message : "request failed",
      });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const revoke = async (grant: Grant) => {
    setActionError(null);
    try {
      await apiMutate(`/v1/grants/${grant.id}`, { csrfToken: csrf, method: "DELETE" });
      await load();
    } catch (error) {
      setActionError(error instanceof ApiError ? error.message : "revoke failed");
    }
  };

  return (
    <Card title="Scoped grants">
      {grants.state === "loading" ? <DataState kind="loading" /> : null}
      {grants.state === "error" ? (
        <DataState kind="error" description={grants.message} onRetry={() => void load()} />
      ) : null}
      {grants.state === "ready" && grants.data.length === 0 ? (
        <DataState
          kind="empty"
          title="No grants in your scope"
          description="Grants you are allowed to manage will appear here."
        />
      ) : null}
      {actionError ? (
        <p role="alert" className="mb-2 text-sm text-critical">
          {actionError}
        </p>
      ) : null}
      {grants.state === "ready" && grants.data.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-sm" data-testid="grant-table">
            <thead>
              <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-ink-muted">
                <th className="px-2 py-2">Principal</th>
                <th className="px-2 py-2">Role</th>
                <th className="px-2 py-2">Scope</th>
                <th className="px-2 py-2">Status</th>
                <th className="px-2 py-2" aria-label="Actions" />
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {grants.data.map((grant) => {
                const active = grant.revoked_at === null;
                return (
                  <tr key={grant.id}>
                    <td className="px-2 py-2 text-ink">
                      {grant.identity_display ?? grant.group_display ?? "—"}
                      {grant.group_display ? (
                        <span className="ml-1 text-xs text-ink-muted">(group)</span>
                      ) : null}
                    </td>
                    <td className="px-2 py-2 text-ink-secondary">{grant.role_name}</td>
                    <td className="px-2 py-2">
                      <span className="font-mono text-xs text-ink-secondary">
                        {grant.scope_type}/{grant.scope_ref}
                      </span>
                    </td>
                    <td className="px-2 py-2">
                      <StatusBadge
                        status={active ? "healthy" : "unknown"}
                        label={active ? "active" : "revoked"}
                      />
                    </td>
                    <td className="px-2 py-2 text-right">
                      {active ? (
                        <button
                          type="button"
                          onClick={() => void revoke(grant)}
                          className="rounded-lg border border-border px-3 py-1 text-xs text-ink-secondary hover:bg-surface-sunken"
                        >
                          Revoke
                        </button>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
      <p className="mt-3 border-t border-border pt-3 text-xs text-ink-muted">
        Creating grants from this screen arrives with the catalog sprint, when
        scopes become selectable objects instead of raw identifiers.
      </p>
    </Card>
  );
}
