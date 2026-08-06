"use client";

import { useCallback, useEffect, useState } from "react";

import { DataState } from "@/components/state/DataState";
import { StatusBadge } from "@/components/state/StatusBadge";
import { Card } from "@/components/ui/Card";
import { ApiError, apiGet, apiMutate } from "@/lib/api";
import { useSession } from "@/lib/session";

interface Role {
  id: string;
  name: string;
  description: string;
  is_system: boolean;
  status: "active" | "archived";
  version: number;
  permissions: string[];
  etag: string;
}

interface PermissionEntry {
  key: string;
  description: string;
}

type Loadable<T> = { state: "loading" } | { state: "error"; message: string } | { state: "ready"; data: T };

export function RolesPanel() {
  const { state: session } = useSession();
  const csrf = session.status === "authenticated" ? session.me.csrf_token : "";

  const [roles, setRoles] = useState<Loadable<Role[]>>({ state: "loading" });
  const [catalog, setCatalog] = useState<Loadable<PermissionEntry[]>>({ state: "loading" });
  const [selected, setSelected] = useState<Role | null>(null);
  const [draftPermissions, setDraftPermissions] = useState<string[]>([]);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [newRoleName, setNewRoleName] = useState("");

  const load = useCallback(async () => {
    setRoles({ state: "loading" });
    try {
      const [rolesBody, catalogBody] = await Promise.all([
        apiGet<{ roles: Role[] }>("/v1/roles"),
        apiGet<{ permissions: PermissionEntry[] }>("/v1/permissions"),
      ]);
      setRoles({ state: "ready", data: rolesBody.roles });
      setCatalog({ state: "ready", data: catalogBody.permissions });
    } catch (error) {
      const message = error instanceof ApiError ? error.message : "request failed";
      setRoles({ state: "error", message });
      setCatalog({ state: "error", message });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const selectRole = (role: Role) => {
    setSelected(role);
    setDraftPermissions(role.permissions);
    setSaveError(null);
  };

  const savePermissions = async () => {
    if (!selected) return;
    setSaveError(null);
    try {
      await apiMutate(`/v1/roles/${selected.id}/permissions`, {
        csrfToken: csrf,
        method: "PUT",
        body: { permissions: draftPermissions },
        ifMatch: selected.etag,
      });
      setSelected(null);
      await load();
    } catch (error) {
      setSaveError(error instanceof ApiError ? error.message : "save failed");
    }
  };

  const archiveRole = async (role: Role) => {
    setSaveError(null);
    try {
      await apiMutate(`/v1/roles/${role.id}/archive`, {
        csrfToken: csrf,
        ifMatch: role.etag,
      });
      setSelected(null);
      await load();
    } catch (error) {
      setSaveError(error instanceof ApiError ? error.message : "archive failed");
    }
  };

  const createRole = async () => {
    if (newRoleName.trim().length < 2) return;
    setSaveError(null);
    try {
      await apiMutate("/v1/roles", {
        csrfToken: csrf,
        body: { name: newRoleName.trim(), description: "" },
      });
      setNewRoleName("");
      await load();
    } catch (error) {
      setSaveError(error instanceof ApiError ? error.message : "create failed");
    }
  };

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <Card title="Roles">
        {roles.state === "loading" ? <DataState kind="loading" /> : null}
        {roles.state === "error" ? (
          <DataState kind="error" description={roles.message} onRetry={() => void load()} />
        ) : null}
        {roles.state === "ready" && roles.data.length === 0 ? <DataState kind="empty" /> : null}
        {roles.state === "ready" && roles.data.length > 0 ? (
          <ul className="divide-y divide-border" data-testid="role-list">
            {roles.data.map((role) => (
              <li key={role.id}>
                <button
                  type="button"
                  onClick={() => selectRole(role)}
                  className="flex w-full items-center justify-between gap-3 px-1 py-2.5 text-left hover:bg-surface-sunken"
                >
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-medium text-ink">
                      {role.name}
                    </span>
                    <span className="block text-xs text-ink-muted">
                      {role.permissions.length} permissions
                    </span>
                  </span>
                  <span className="flex shrink-0 items-center gap-1.5">
                    {role.is_system ? (
                      <span className="rounded border border-border px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-ink-muted">
                        template
                      </span>
                    ) : null}
                    <StatusBadge
                      status={role.status === "active" ? "healthy" : "unknown"}
                      label={role.status}
                    />
                  </span>
                </button>
              </li>
            ))}
          </ul>
        ) : null}

        <form
          className="mt-4 flex gap-2 border-t border-border pt-4"
          onSubmit={(event) => {
            event.preventDefault();
            void createRole();
          }}
        >
          <input
            value={newRoleName}
            onChange={(event) => setNewRoleName(event.target.value)}
            placeholder="New role name"
            aria-label="New role name"
            className="h-9 flex-1 rounded-lg border border-border bg-surface px-3 text-sm text-ink placeholder:text-ink-muted focus-visible:outline-2 focus-visible:outline-accent"
          />
          <button
            type="submit"
            className="h-9 rounded-lg bg-accent px-4 text-sm font-medium text-white hover:opacity-90"
          >
            Create
          </button>
        </form>
      </Card>

      <Card title={selected ? `Edit: ${selected.name}` : "Role details"}>
        {!selected ? (
          <DataState
            kind="empty"
            title="No role selected"
            description="Select a role to inspect or edit its permission set."
          />
        ) : (
          <div className="space-y-3">
            {selected.is_system ? (
              <p className="rounded-lg bg-unknown-soft px-3 py-2 text-xs text-ink-secondary">
                System templates are immutable. Create a custom role to tailor
                permissions.
              </p>
            ) : null}
            {catalog.state === "ready" ? (
              <fieldset
                disabled={selected.is_system || selected.status !== "active"}
                data-testid="permission-matrix"
                className="grid max-h-80 grid-cols-1 gap-1 overflow-y-auto sm:grid-cols-2"
              >
                <legend className="sr-only">Permissions</legend>
                {catalog.data.map((permission) => (
                  <label
                    key={permission.key}
                    className="flex items-start gap-2 rounded-lg px-2 py-1.5 text-sm hover:bg-surface-sunken"
                  >
                    <input
                      type="checkbox"
                      checked={draftPermissions.includes(permission.key)}
                      onChange={(event) =>
                        setDraftPermissions((current) =>
                          event.target.checked
                            ? [...current, permission.key]
                            : current.filter((key) => key !== permission.key),
                        )
                      }
                      className="mt-0.5 accent-[var(--accent)]"
                    />
                    <span>
                      <span className="block font-mono text-xs text-ink">{permission.key}</span>
                      <span className="block text-xs text-ink-muted">
                        {permission.description}
                      </span>
                    </span>
                  </label>
                ))}
              </fieldset>
            ) : (
              <DataState kind="loading" />
            )}
            {saveError ? (
              <p role="alert" className="text-sm text-critical">
                {saveError}
              </p>
            ) : null}
            {!selected.is_system && selected.status === "active" ? (
              <div className="flex gap-2 border-t border-border pt-3">
                <button
                  type="button"
                  onClick={() => void savePermissions()}
                  className="h-9 rounded-lg bg-accent px-4 text-sm font-medium text-white hover:opacity-90"
                >
                  Save permissions
                </button>
                <button
                  type="button"
                  onClick={() => void archiveRole(selected)}
                  className="h-9 rounded-lg border border-border px-4 text-sm text-ink-secondary hover:bg-surface-sunken"
                >
                  Archive role
                </button>
              </div>
            ) : null}
          </div>
        )}
      </Card>
    </div>
  );
}
