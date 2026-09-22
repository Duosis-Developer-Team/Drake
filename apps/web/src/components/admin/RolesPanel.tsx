"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Archive,
  KeyRound,
  Lock,
  Plus,
  ShieldCheck,
  UserCog,
} from "lucide-react";

import {
  IconBubble,
  KpiTile,
  PILL_BUTTON,
  PILL_FIELD,
  PILL_PRIMARY,
  StateCard,
} from "@/components/features/configure/kit";
import { DataState } from "@/components/state/DataState";
import { StatusBadge } from "@/components/state/StatusBadge";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { ApiError, apiGet, apiMutate } from "@/lib/api";
import { useT } from "@/lib/i18n";
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

type Loadable<T> =
  | { state: "loading" }
  | { state: "error"; message: string }
  | { state: "ready"; data: T };

export function RolesPanel() {
  const t = useT("admin");
  const common = useT("common");
  const { state: session } = useSession();
  const csrf = session.status === "authenticated" ? session.me.csrf_token : "";

  const [roles, setRoles] = useState<Loadable<Role[]>>({ state: "loading" });
  const [catalog, setCatalog] = useState<Loadable<PermissionEntry[]>>({
    state: "loading",
  });
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
      const message =
        error instanceof ApiError ? error.message : t("error.request");
      setRoles({ state: "error", message });
      setCatalog({ state: "error", message });
    }
  }, [t]);

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
      setSaveError(error instanceof ApiError ? error.message : t("error.save"));
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
      setSaveError(
        error instanceof ApiError ? error.message : t("error.archive"),
      );
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
      setSaveError(error instanceof ApiError ? error.message : t("error.create"));
    }
  };

  const catalogSize = catalog.state === "ready" ? catalog.data.length : 0;
  const roleList = roles.state === "ready" ? roles.data : [];
  const templates = roleList.filter((role) => role.is_system).length;
  const activeCount = roleList.filter(
    (role) => role.status === "active",
  ).length;

  // Group the catalog by its dotted prefix ("rbac.manage" → "rbac") so the
  // matrix reads as sections rather than one long column.
  const groups = useMemo(() => {
    if (catalog.state !== "ready") return [];
    const map = new Map<string, PermissionEntry[]>();
    for (const permission of catalog.data) {
      const prefix = permission.key.split(".")[0] ?? permission.key;
      map.set(prefix, [...(map.get(prefix) ?? []), permission]);
    }
    return [...map.entries()];
  }, [catalog]);

  return (
    <div className="space-y-6">
      {roles.state === "ready" ? (
        <div className="page-grid" data-cols="3">
          <KpiTile icon={ShieldCheck} label={t("roles.kpi.roles")} value={roleList.length}>
            <p className="text-micro text-ink-muted">
              <span data-tabular className="font-medium text-ink-secondary">
                {activeCount}
              </span>{" "}
              {t("roles.kpi.inUse")} ·{" "}
              <span data-tabular className="font-medium text-ink-secondary">
                {roleList.length - activeCount}
              </span>{" "}
              {t("roles.kpi.archived")}
            </p>
          </KpiTile>
          <KpiTile
            icon={Lock}
            tone="info"
            label={t("roles.kpi.templates")}
            value={templates}
          >
            <p className="text-micro text-ink-muted">
              {t("roles.kpi.customRoles", { count: roleList.length - templates })}
            </p>
          </KpiTile>
          <KpiTile
            icon={KeyRound}
            label={t("roles.kpi.catalog")}
            value={catalogSize}
          >
            <p className="text-micro text-ink-muted">
              {t("roles.kpi.catalogCaption")}
            </p>
          </KpiTile>
        </div>
      ) : null}

      <div className="page-split" data-cols="3">
        <div className="page-aside">
          <Panel flush>
            <PanelHeader
              flush
              title={t("roles.list.title")}
              description={t("roles.list.description")}
            />
            {roles.state === "loading" ? (
              <div className="px-7 py-5">
                <DataState kind="loading" />
              </div>
            ) : null}
            {roles.state === "error" ? (
              <div className="px-7 py-5">
                <DataState
                  kind="error"
                  description={roles.message}
                  onRetry={() => void load()}
                />
              </div>
            ) : null}
            {roles.state === "ready" && roles.data.length === 0 ? (
              <StateCard
                kind="empty"
                icon={ShieldCheck}
                title={t("roles.list.emptyTitle")}
                description={t("roles.list.emptyDescription")}
              />
            ) : null}
            {roles.state === "ready" && roles.data.length > 0 ? (
              <ul className="divide-y divide-border" data-testid="role-list">
                {roles.data.map((role) => {
                  const isSelected = selected?.id === role.id;
                  const share =
                    catalogSize > 0
                      ? Math.round(
                          (role.permissions.length / catalogSize) * 100,
                        )
                      : 0;
                  return (
                    <li key={role.id}>
                      <button
                        type="button"
                        onClick={() => selectRole(role)}
                        aria-pressed={isSelected}
                        className={`flex w-full items-center gap-4 px-7 py-4 text-left transition-colors ${
                          isSelected
                            ? "bg-surface-selected"
                            : "hover:bg-surface-hover"
                        }`}
                      >
                        <IconBubble
                          icon={role.is_system ? Lock : UserCog}
                          tone={role.is_system ? undefined : "info"}
                        />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-body font-semibold text-ink">
                            {role.name}
                          </span>
                          <span className="mt-1.5 flex items-center gap-2">
                            <span
                              aria-hidden
                              className="h-1.5 w-20 overflow-hidden rounded-full bg-surface-3"
                            >
                              <span
                                className="block h-full rounded-full bg-info"
                                style={{ width: `${share}%` }}
                              />
                            </span>
                            <span className="text-micro whitespace-nowrap text-ink-muted">
                              {t("roles.list.permissions", { count: role.permissions.length })}
                            </span>
                          </span>
                        </span>
                        <span className="flex shrink-0 flex-col items-end gap-1.5">
                          <StatusBadge
                            status={
                              role.status === "active" ? "healthy" : "unknown"
                            }
                            label={t.dyn("roles.status", role.status, role.status)}
                            size="compact"
                          />
                          {role.is_system ? (
                            <span className="rounded-full bg-surface-3 px-2 py-0.5 text-micro font-medium text-ink-muted">
                              {t("roles.list.template")}
                            </span>
                          ) : null}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            ) : null}

            <form
              className="flex gap-2 border-t border-border px-7 py-5"
              onSubmit={(event) => {
                event.preventDefault();
                void createRole();
              }}
            >
              <input
                value={newRoleName}
                onChange={(event) => setNewRoleName(event.target.value)}
                placeholder={t("roles.list.newRoleName")}
                aria-label={t("roles.list.newRoleName")}
                className={`${PILL_FIELD} flex-1`}
              />
              <button type="submit" className={`${PILL_PRIMARY} shrink-0`}>
                <Plus aria-hidden className="h-4 w-4" />
                {common("action.create")}
              </button>
            </form>
          </Panel>
        </div>

        <div className="page-main">
          {/* The wrapper is the grown last child of page-main; the details
            card fills it so it always matches the roles list's height. */}
          <div className="flex flex-col">
            <Panel className="flex-1">
              {!selected ? (
                <>
                  <PanelHeader title={t("roles.detail.title")} />
                  <StateCard
                    kind="empty"
                    icon={KeyRound}
                    title={t("roles.detail.noneTitle")}
                    description={t("roles.detail.noneDescription")}
                  />
                  {saveError ? (
                    <p role="alert" className="text-caption text-critical">
                      {saveError}
                    </p>
                  ) : null}
                </>
              ) : (
                <>
                  <div className="flex flex-wrap items-center gap-4">
                    <IconBubble
                      icon={selected.is_system ? Lock : UserCog}
                      tone={selected.is_system ? undefined : "info"}
                      size="lg"
                    />
                    <div className="min-w-0 flex-1">
                      <h2 className="truncate text-[1.25rem] font-semibold tracking-[-0.01em] text-ink">
                        {t("roles.detail.edit", { name: selected.name })}
                      </h2>
                      <p data-tabular className="text-caption text-ink-muted">
                        {t("roles.detail.summary", {
                          selected: draftPermissions.length,
                          total: catalogSize,
                          version: selected.version,
                        })}
                      </p>
                    </div>
                    <div className="flex items-center gap-1.5">
                      {selected.is_system ? (
                        <span className="rounded-full bg-surface-3 px-2.5 py-1 text-micro font-medium text-ink-muted">
                          {t("roles.list.template")}
                        </span>
                      ) : null}
                      <StatusBadge
                        status={
                          selected.status === "active" ? "healthy" : "unknown"
                        }
                        label={t.dyn("roles.status", selected.status, selected.status)}
                      />
                    </div>
                  </div>

                  {selected.is_system ? (
                    <p className="flex items-center gap-3 rounded-[1.25rem] bg-surface-2 px-4 py-3 text-caption text-ink-secondary">
                      <Lock
                        aria-hidden
                        className="h-4 w-4 shrink-0 text-ink-muted"
                      />
                      {t("roles.detail.immutable")}
                    </p>
                  ) : null}

                  {catalog.state === "ready" ? (
                    <fieldset
                      disabled={
                        selected.is_system || selected.status !== "active"
                      }
                      data-testid="permission-matrix"
                      className="max-h-[32rem] space-y-5 overflow-y-auto pr-1"
                    >
                      <legend className="sr-only">{t("roles.detail.permissionsLegend")}</legend>
                      {groups.map(([prefix, permissions]) => (
                        <div key={prefix}>
                          <p className="mb-2 text-micro font-medium tracking-[0.08em] text-ink-muted uppercase">
                            {prefix}
                          </p>
                          <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                            {permissions.map((permission) => {
                              const checked = draftPermissions.includes(
                                permission.key,
                              );
                              return (
                                <label
                                  key={permission.key}
                                  className={`flex cursor-pointer items-start gap-3 rounded-2xl border px-4 py-3 transition-colors ${
                                    checked
                                      ? "border-info/40 bg-info-soft"
                                      : "border-border bg-surface hover:bg-surface-hover"
                                  }`}
                                >
                                  <input
                                    type="checkbox"
                                    checked={checked}
                                    onChange={(event) =>
                                      setDraftPermissions((current) =>
                                        event.target.checked
                                          ? [...current, permission.key]
                                          : current.filter(
                                              (key) => key !== permission.key,
                                            ),
                                      )
                                    }
                                    className="mt-0.5 h-4 w-4 accent-[var(--accent)]"
                                  />
                                  <span className="min-w-0">
                                    <span className="block truncate font-mono text-micro font-medium text-ink">
                                      {permission.key}
                                    </span>
                                    <span className="block text-micro text-ink-muted">
                                      {permission.description}
                                    </span>
                                  </span>
                                </label>
                              );
                            })}
                          </div>
                        </div>
                      ))}
                    </fieldset>
                  ) : (
                    <DataState kind="loading" />
                  )}
                  {saveError ? (
                    <p role="alert" className="text-caption text-critical">
                      {saveError}
                    </p>
                  ) : null}
                  {!selected.is_system && selected.status === "active" ? (
                    <div className="flex flex-wrap gap-2 border-t border-border pt-5">
                      <button
                        type="button"
                        onClick={() => void savePermissions()}
                        className={PILL_PRIMARY}
                      >
                        {t("roles.detail.save")}
                      </button>
                      <button
                        type="button"
                        onClick={() => void archiveRole(selected)}
                        className={PILL_BUTTON}
                      >
                        <Archive aria-hidden className="h-3.5 w-3.5" />
                        {t("roles.detail.archive")}
                      </button>
                    </div>
                  ) : null}
                </>
              )}
            </Panel>
          </div>
        </div>
      </div>
    </div>
  );
}
