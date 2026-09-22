"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { CircleCheck, KeyRound, Plus, Users } from "lucide-react";

import {
  FIELD_LABEL,
  IconBubble,
  InitialsBubble,
  KpiTile,
  PILL_BUTTON,
  PILL_FIELD,
  PILL_PRIMARY,
  ShareBar,
  StateCard,
  TABLE_HEAD,
} from "@/components/features/configure/kit";
import { DataState } from "@/components/state/DataState";
import { StatusBadge } from "@/components/state/StatusBadge";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { ApiError, apiGet, apiMutate } from "@/lib/api";
import { useT } from "@/lib/i18n";
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
  const t = useT("admin");
  const common = useT("common");
  const { state: session } = useSession();
  const csrf = session.status === "authenticated" ? session.me.csrf_token : "";
  const [grants, setGrants] = useState<Loadable<Grant[]>>({ state: "loading" });
  const [options, setOptions] = useState<Loadable<GrantOptions>>({
    state: "loading",
  });
  const [actionError, setActionError] = useState<string | null>(null);

  const load = useCallback(async () => {
    // Keep previously loaded data on refresh: the create form must not
    // unmount (and lose its success/error feedback) while lists reload.
    setGrants((current) =>
      current.state === "ready" ? current : { state: "loading" },
    );
    setOptions((current) =>
      current.state === "ready" ? current : { state: "loading" },
    );
    try {
      const [grantsBody, optionsBody] = await Promise.all([
        apiGet<{ grants: Grant[] }>("/v1/grants"),
        apiGet<GrantOptions>("/v1/grant-options"),
      ]);
      setGrants({ state: "ready", data: grantsBody.grants });
      setOptions({ state: "ready", data: optionsBody });
    } catch (error) {
      const message =
        error instanceof ApiError ? error.message : t("error.request");
      setGrants({ state: "error", message });
      setOptions({ state: "error", message });
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  const revoke = async (grant: Grant) => {
    setActionError(null);
    try {
      await apiMutate(`/v1/grants/${grant.id}`, {
        csrfToken: csrf,
        method: "DELETE",
      });
      await load();
    } catch (error) {
      setActionError(
        error instanceof ApiError ? error.message : t("error.revoke"),
      );
    }
  };

  const grantList = grants.state === "ready" ? grants.data : [];
  const activeGrants = grantList.filter(
    (grant) => grant.revoked_at === null,
  ).length;
  const principals = new Set(
    grantList.map(
      (grant) => grant.identity_display ?? grant.group_display ?? grant.id,
    ),
  ).size;

  return (
    <div className="space-y-6">
      {grants.state === "ready" ? (
        <div className="page-grid" data-cols="3">
          <KpiTile icon={KeyRound} label={t("grants.kpi.grants")} value={grantList.length}>
            <p className="text-micro text-ink-muted">
              {t("grants.kpi.grantsCaption")}
            </p>
          </KpiTile>
          <KpiTile
            icon={CircleCheck}
            tone="success"
            label={t("grants.kpi.active")}
            value={activeGrants}
            suffix={t("grants.kpi.ofTotal", { total: grantList.length })}
          >
            <ShareBar
              value={activeGrants}
              total={grantList.length}
              tone="success"
              label={t("grants.kpi.inForce")}
            />
          </KpiTile>
          <KpiTile
            icon={Users}
            tone="info"
            label={t("grants.kpi.principals")}
            value={principals}
          >
            <p data-tabular className="text-micro text-ink-muted">
              {t("grants.kpi.revokedKept", { count: grantList.length - activeGrants })}
            </p>
          </KpiTile>
        </div>
      ) : null}

      <div className="page-split" data-cols="3">
        <div className="page-main">
          <Panel flush>
            <PanelHeader
              flush
              title={t("grants.list.title")}
              description={t("grants.list.description")}
            />
            {grants.state === "loading" ? (
              <div className="px-7 py-5">
                <DataState kind="loading" />
              </div>
            ) : null}
            {grants.state === "error" ? (
              <div className="px-7 py-5">
                <DataState
                  kind="error"
                  description={grants.message}
                  onRetry={() => void load()}
                />
              </div>
            ) : null}
            {grants.state === "ready" && grants.data.length === 0 ? (
              <StateCard
                kind="empty"
                icon={KeyRound}
                title={t("grants.list.emptyTitle")}
                description={t("grants.list.emptyDescription")}
              />
            ) : null}
            {actionError ? (
              <p
                role="alert"
                className="mx-7 mt-4 rounded-full bg-critical-soft px-4 py-2 text-caption text-critical"
              >
                {actionError}
              </p>
            ) : null}
            {grants.state === "ready" && grants.data.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-left" data-testid="grant-table">
                  <thead>
                    <tr className={`border-b border-border ${TABLE_HEAD}`}>
                      <th className="h-11 pr-3 pl-7 font-medium">{t("grants.list.principal")}</th>
                      <th className="h-11 px-3 font-medium">{t("grants.list.role")}</th>
                      <th className="h-11 px-3 font-medium">{t("grants.list.scope")}</th>
                      <th className="h-11 px-3 font-medium">{t("grants.list.status")}</th>
                      <th className="h-11 pr-7 pl-3" aria-label={common("field.actions")} />
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {grants.data.map((grant) => {
                      const active = grant.revoked_at === null;
                      const principal =
                        grant.identity_display ?? grant.group_display ?? "—";
                      return (
                        <tr
                          key={grant.id}
                          className="h-16 transition-colors hover:bg-surface-hover"
                        >
                          <td className="pr-3 pl-7">
                            <span className="flex items-center gap-3">
                              <InitialsBubble name={principal} size="sm" />
                              <span className="min-w-0">
                                <span className="block text-body font-semibold whitespace-nowrap text-ink">
                                  {principal}
                                </span>
                                {grant.group_display ? (
                                  <span className="block text-micro text-ink-muted">
                                    {t("grants.list.group")}
                                  </span>
                                ) : null}
                              </span>
                            </span>
                          </td>
                          <td className="px-3">
                            <span className="rounded-full bg-surface-2 px-2.5 py-1 text-caption font-medium whitespace-nowrap text-ink">
                              {grant.role_name}
                            </span>
                          </td>
                          <td className="px-3">
                            <span className="font-mono text-micro whitespace-nowrap text-ink-secondary">
                              {grant.scope_type}/{grant.scope_ref}
                            </span>
                          </td>
                          <td className="px-3">
                            <StatusBadge
                              status={active ? "healthy" : "unknown"}
                              label={active ? t("grants.list.active") : t("grants.list.revoked")}
                            />
                          </td>
                          <td className="pr-7 pl-3 text-right">
                            {active ? (
                              <button
                                type="button"
                                onClick={() => void revoke(grant)}
                                className={PILL_BUTTON}
                              >
                                {t("grants.list.revoke")}
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
          </Panel>
        </div>

        {options.state === "ready" ? (
          <div className="page-aside" data-sticky>
            <CreateGrantForm
              options={options.data}
              csrf={csrf}
              onCreated={load}
            />
          </div>
        ) : null}
      </div>
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
  const t = useT("admin");
  const [principalType, setPrincipalType] = useState<"identity" | "group">(
    "identity",
  );
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
        selectedScope
          ? selectedScope.delegable_role_ids.includes(role.id)
          : false,
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
      setFormError(t("grants.form.requiredError"));
      return;
    }
    if (validFrom && validTo && new Date(validTo) <= new Date(validFrom)) {
      setFormError(t("grants.form.intervalError"));
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
      setSuccess(t("grants.form.created"));
      setPrincipalId("");
      setRoleId("");
      setValidFrom("");
      setValidTo("");
      await onCreated();
    } catch (error) {
      setFormError(error instanceof ApiError ? error.message : t("error.create"));
    } finally {
      setSubmitting(false);
    }
  };

  const selectClass = PILL_FIELD;
  const labelClass = FIELD_LABEL;

  return (
    <Panel>
      <PanelHeader
        title={t("grants.form.title")}
        description={t("grants.form.description")}
        actions={<IconBubble icon={Plus} size="sm" />}
      />
      <form
        onSubmit={(event) => void submit(event)}
        data-testid="grant-create-form"
        className="grid grid-cols-1 gap-4 @min-[36rem]/page:grid-cols-2 @min-[60rem]/page:grid-cols-1"
      >
        <fieldset>
          <legend className={labelClass}>{t("grants.form.principalType")}</legend>
          <div className="grid grid-cols-2 gap-1 rounded-full border border-border bg-surface-2 p-1">
            {(["identity", "group"] as const).map((type) => (
              <label
                key={type}
                className={`flex cursor-pointer items-center justify-center gap-1.5 rounded-full px-3 py-2 text-caption font-medium transition-colors has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-accent ${
                  principalType === type
                    ? "bg-surface text-ink shadow-panel"
                    : "text-ink-secondary hover:text-ink"
                }`}
              >
                <input
                  type="radio"
                  name="principal-type"
                  checked={principalType === type}
                  onChange={() => {
                    setPrincipalType(type);
                    setPrincipalId("");
                  }}
                  className="sr-only"
                />
                {type === "identity" ? t("grants.form.identity") : t("grants.form.mappedGroup")}
              </label>
            ))}
          </div>
        </fieldset>

        <div>
          <label htmlFor="grant-principal" className={labelClass}>
            {principalType === "identity" ? t("grants.form.identity") : t("grants.form.groupMapping")}
          </label>
          <select
            id="grant-principal"
            value={principalId}
            onChange={(event) => setPrincipalId(event.target.value)}
            className={selectClass}
          >
            <option value="">{t("grants.form.select")}</option>
            {principals.map((principal) => (
              <option key={principal.id} value={principal.id}>
                {principal.display_name}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label htmlFor="grant-scope" className={labelClass}>
            {t("grants.form.scope")}
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
          <label htmlFor="grant-role" className={labelClass}>
            {t("grants.form.role")}
          </label>
          <select
            id="grant-role"
            value={roleId}
            onChange={(event) => setRoleId(event.target.value)}
            className={selectClass}
          >
            <option value="">{t("grants.form.select")}</option>
            {delegableRoles.map((role) => (
              <option key={role.id} value={role.id}>
                {role.name}
              </option>
            ))}
          </select>
          <p className="mt-1.5 px-4 text-micro text-ink-muted">
            {t("grants.form.roleHelp")}
          </p>
        </div>

        <div className="grid grid-cols-1 gap-3 @min-[36rem]/page:col-span-2 @min-[36rem]/page:grid-cols-2 @min-[60rem]/page:col-span-1 @min-[60rem]/page:grid-cols-1">
          <div>
            <label htmlFor="grant-valid-from" className={labelClass}>
              {t("grants.form.validFrom")}
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
            <label htmlFor="grant-valid-to" className={labelClass}>
              {t("grants.form.validTo")}
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
          <p className="rounded-[1.25rem] bg-surface-2 px-4 py-3 text-micro text-ink-secondary @min-[36rem]/page:col-span-2 @min-[60rem]/page:col-span-1">
            {t("grants.form.subtreeNote")}
          </p>
        ) : null}

        {formError ? (
          <p
            role="alert"
            className="rounded-full bg-critical-soft px-4 py-2 text-caption text-critical @min-[36rem]/page:col-span-2 @min-[60rem]/page:col-span-1"
          >
            {formError}
          </p>
        ) : null}
        {success ? (
          <p
            role="status"
            className="rounded-full bg-healthy-soft px-4 py-2 text-caption text-healthy @min-[36rem]/page:col-span-2 @min-[60rem]/page:col-span-1"
          >
            {success}
          </p>
        ) : null}

        <button
          type="submit"
          disabled={submitting}
          className={`${PILL_PRIMARY} w-full @min-[36rem]/page:col-span-2 @min-[60rem]/page:col-span-1`}
        >
          {submitting ? t("grants.form.submitting") : t("grants.form.submit")}
        </button>
      </form>
    </Panel>
  );
}
