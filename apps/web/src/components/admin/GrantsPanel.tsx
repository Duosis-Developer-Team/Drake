"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

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

interface GrantOptions {
  directory_scope: "organization" | "subtree";
  scopes: {
    id: string;
    scope_type: string;
    scope_ref: string;
    display_name: string;
    delegable_role_ids: string[];
  }[];
  roles: { id: string; name: string; permissions: string[] }[];
  identities: { id: string; display_name: string }[];
  group_mappings: { id: string; display_name: string }[];
}

type Loadable<T> =
  | { state: "loading" }
  | { state: "error"; message: string }
  | { state: "ready"; data: T };

export function GrantsPanel() {
  const { state: session } = useSession();
  const csrf = session.status === "authenticated" ? session.me.csrf_token : "";
  const [grants, setGrants] = useState<Loadable<Grant[]>>({ state: "loading" });
  const [options, setOptions] = useState<Loadable<GrantOptions>>({ state: "loading" });
  const [actionError, setActionError] = useState<string | null>(null);

  const load = useCallback(async () => {
    // Keep previously loaded data on refresh: the create form must not
    // unmount (and lose its success/error feedback) while lists reload.
    setGrants((current) => (current.state === "ready" ? current : { state: "loading" }));
    setOptions((current) => (current.state === "ready" ? current : { state: "loading" }));
    try {
      const [grantsBody, optionsBody] = await Promise.all([
        apiGet<{ grants: Grant[] }>("/v1/grants"),
        apiGet<GrantOptions>("/v1/grant-options"),
      ]);
      setGrants({ state: "ready", data: grantsBody.grants });
      setOptions({ state: "ready", data: optionsBody });
    } catch (error) {
      const message = error instanceof ApiError ? error.message : "request failed";
      setGrants({ state: "error", message });
      setOptions({ state: "error", message });
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
    <div className="space-y-4">
      {options.state === "ready" ? (
        <CreateGrantForm options={options.data} csrf={csrf} onCreated={load} />
      ) : null}

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
      </Card>
    </div>
  );
}

function CreateGrantForm({
  options,
  csrf,
  onCreated,
}: {
  options: GrantOptions;
  csrf: string;
  onCreated: () => Promise<void>;
}) {
  const [principalType, setPrincipalType] = useState<"identity" | "group">("identity");
  const [principalId, setPrincipalId] = useState("");
  const [scopeId, setScopeId] = useState(options.scopes[0]?.id ?? "");
  const [roleId, setRoleId] = useState("");
  const [validFrom, setValidFrom] = useState("");
  const [validTo, setValidTo] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const selectedScope = useMemo(
    () => options.scopes.find((scope) => scope.id === scopeId),
    [options.scopes, scopeId],
  );
  const delegableRoles = useMemo(
    () =>
      options.roles.filter((role) =>
        selectedScope ? selectedScope.delegable_role_ids.includes(role.id) : false,
      ),
    [options.roles, selectedScope],
  );
  const principals =
    principalType === "identity" ? options.identities : options.group_mappings;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    // Double-submit guard: one in-flight request at most.
    if (submitting) return;
    setFormError(null);
    setSuccess(null);

    if (!principalId || !roleId || !scopeId) {
      setFormError("Principal, role, and scope are required.");
      return;
    }
    if (validFrom && validTo && new Date(validTo) <= new Date(validFrom)) {
      setFormError("Valid-to must be after valid-from.");
      return;
    }

    setSubmitting(true);
    try {
      await apiMutate("/v1/grants", {
        csrfToken: csrf,
        body: {
          role_id: roleId,
          scope_id: scopeId,
          identity_id: principalType === "identity" ? principalId : null,
          group_mapping_id: principalType === "group" ? principalId : null,
          valid_from: validFrom ? new Date(validFrom).toISOString() : null,
          valid_to: validTo ? new Date(validTo).toISOString() : null,
        },
      });
      setSuccess("Grant created.");
      setPrincipalId("");
      setRoleId("");
      setValidFrom("");
      setValidTo("");
      await onCreated();
    } catch (error) {
      setFormError(error instanceof ApiError ? error.message : "create failed");
    } finally {
      setSubmitting(false);
    }
  };

  const selectClass =
    "h-9 w-full rounded-lg border border-border bg-surface px-2 text-sm text-ink focus-visible:outline-2 focus-visible:outline-accent";

  return (
    <Card title="Create grant">
      <form onSubmit={(event) => void submit(event)} data-testid="grant-create-form">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <fieldset>
            <legend className="mb-1 text-xs font-medium text-ink-secondary">
              Principal type
            </legend>
            <div className="flex gap-3">
              {(["identity", "group"] as const).map((type) => (
                <label key={type} className="flex items-center gap-1.5 text-sm text-ink">
                  <input
                    type="radio"
                    name="principal-type"
                    checked={principalType === type}
                    onChange={() => {
                      setPrincipalType(type);
                      setPrincipalId("");
                    }}
                    className="accent-[var(--accent)]"
                  />
                  {type === "identity" ? "Identity" : "Mapped group"}
                </label>
              ))}
            </div>
          </fieldset>

          <div>
            <label htmlFor="grant-principal" className="mb-1 block text-xs font-medium text-ink-secondary">
              {principalType === "identity" ? "Identity" : "Group mapping"}
            </label>
            <select
              id="grant-principal"
              value={principalId}
              onChange={(event) => setPrincipalId(event.target.value)}
              className={selectClass}
            >
              <option value="">Select…</option>
              {principals.map((principal) => (
                <option key={principal.id} value={principal.id}>
                  {principal.display_name}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label htmlFor="grant-scope" className="mb-1 block text-xs font-medium text-ink-secondary">
              Scope
            </label>
            <select
              id="grant-scope"
              value={scopeId}
              onChange={(event) => {
                setScopeId(event.target.value);
                setRoleId("");
              }}
              className={selectClass}
            >
              {options.scopes.map((scope) => (
                <option key={scope.id} value={scope.id}>
                  {scope.scope_type}/{scope.scope_ref}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label htmlFor="grant-role" className="mb-1 block text-xs font-medium text-ink-secondary">
              Role
            </label>
            <select
              id="grant-role"
              value={roleId}
              onChange={(event) => setRoleId(event.target.value)}
              className={selectClass}
            >
              <option value="">Select…</option>
              {delegableRoles.map((role) => (
                <option key={role.id} value={role.id}>
                  {role.name}
                </option>
              ))}
            </select>
            <p className="mt-1 text-[11px] text-ink-muted">
              Only roles you can delegate at the selected scope are listed.
            </p>
          </div>

          <div>
            <label htmlFor="grant-valid-from" className="mb-1 block text-xs font-medium text-ink-secondary">
              Valid from (optional)
            </label>
            <input
              id="grant-valid-from"
              type="datetime-local"
              value={validFrom}
              onChange={(event) => setValidFrom(event.target.value)}
              className={selectClass}
            />
          </div>

          <div>
            <label htmlFor="grant-valid-to" className="mb-1 block text-xs font-medium text-ink-secondary">
              Valid to (optional)
            </label>
            <input
              id="grant-valid-to"
              type="datetime-local"
              value={validTo}
              onChange={(event) => setValidTo(event.target.value)}
              className={selectClass}
            />
          </div>
        </div>

        {options.directory_scope === "subtree" ? (
          <p className="mt-3 text-xs text-ink-muted">
            You can select principals already present in your scope. Adding
            people beyond it arrives with the directory integration.
          </p>
        ) : null}

        {formError ? (
          <p role="alert" className="mt-3 text-sm text-critical">
            {formError}
          </p>
        ) : null}
        {success ? (
          <p role="status" className="mt-3 text-sm text-healthy">
            {success}
          </p>
        ) : null}

        <div className="mt-4 border-t border-border pt-3">
          <button
            type="submit"
            disabled={submitting}
            className="h-9 rounded-lg bg-accent px-5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            {submitting ? "Creating…" : "Create grant"}
          </button>
        </div>
      </form>
    </Card>
  );
}
