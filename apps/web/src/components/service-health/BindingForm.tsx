"use client";

/**
 * Bind a service to a workload.
 *
 * Every field is a select over rows the API already returned. There is no
 * text input for a namespace, a workload name, a label selector or a query
 * — not because free text would be rejected downstream (it would), but
 * because a form that cannot express an arbitrary selector cannot be
 * mistaken for one that can.
 *
 * The selects are dependent: choosing a cluster clears the namespace and
 * workload, choosing a namespace clears the workload. Leaving a stale
 * downstream value behind is how someone binds `prod/api` while looking at
 * a form that says `dev`.
 */

import { useCallback, useEffect, useState } from "react";

import { DataState } from "@/components/state/DataState";
import { Card } from "@/components/ui/Card";
import { ApiError } from "@/lib/api";
import { useSession } from "@/lib/session";
import {
  createBinding,
  fetchBindingOptions,
  resolveBinding,
  setBindingLifecycle,
  updateBinding,
  type BindingOptions,
  type BindingSummary,
} from "@/lib/serviceHealth";

interface Props {
  environmentServiceId: string;
  /** Present when editing rather than creating. */
  existing?: BindingSummary | null;
  onSaved?: (bindingId: string) => void;
}

type Notice =
  | { kind: "none" }
  | { kind: "saved"; message: string }
  | { kind: "conflict"; message: string }
  | { kind: "error"; message: string; correlationId?: string };

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block space-y-1">
      <span className="text-xs font-medium text-ink-secondary">{label}</span>
      {children}
      {hint ? <span className="block text-[11px] text-ink-muted">{hint}</span> : null}
    </label>
  );
}

const SELECT_CLASS =
  "w-full rounded-lg border border-border bg-surface px-2.5 py-1.5 text-sm text-ink disabled:opacity-50";

export function BindingForm({ environmentServiceId, existing, onSaved }: Props) {
  const { state, hasPermission } = useSession();
  const csrfToken = state.status === "authenticated" ? state.me.csrf_token : null;
  // UI gating is a convenience; the API remains the authority and answers
  // 404 either way.
  const canManage = hasPermission("integration.manage");

  const [options, setOptions] = useState<BindingOptions | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [clusterId, setClusterId] = useState(existing?.cluster_id ?? "");
  const [namespace, setNamespace] = useState(existing?.namespace ?? "");
  const [workload, setWorkload] = useState(
    existing ? `${existing.workload_kind}/${existing.workload_name}` : "",
  );
  const [presetKey, setPresetKey] = useState(existing?.preset_key ?? "kubernetes.baseline.v1");
  const [policyKey, setPolicyKey] = useState(existing?.health_policy_key ?? "default.v1");
  const [notice, setNotice] = useState<Notice>({ kind: "none" });
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    let cancelled = false;
    fetchBindingOptions({ environmentServiceId, clusterId, namespace })
      .then((data) => {
        if (!cancelled) {
          setOptions(data);
          setLoadError(null);
        }
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setLoadError(error instanceof ApiError ? error.message : "request failed");
      });
    return () => {
      cancelled = true;
    };
  }, [environmentServiceId, clusterId, namespace]);

  useEffect(() => load(), [load]);

  // Dependent resets. Downstream choices belong to the upstream one that
  // produced them; keeping them would let the form describe a workload the
  // user never picked.
  const chooseCluster = (next: string) => {
    setClusterId(next);
    setNamespace("");
    setWorkload("");
    setNotice({ kind: "none" });
  };
  const chooseNamespace = (next: string) => {
    setNamespace(next);
    setWorkload("");
    setNotice({ kind: "none" });
  };

  const failed = (error: unknown) => {
    if (error instanceof ApiError) {
      if (error.status === 409) {
        setNotice({
          kind: "conflict",
          message:
            "This binding changed since you opened it — someone else edited it. Reload to see the current values before saving again.",
        });
        return;
      }
      setNotice({
        kind: "error",
        message: error.message,
        correlationId: error.correlationId,
      });
      return;
    }
    setNotice({ kind: "error", message: "request failed" });
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!csrfToken) return;
    setBusy(true);
    setNotice({ kind: "none" });
    try {
      if (existing) {
        const result = await updateBinding(csrfToken, existing.id, {
          preset_key: presetKey,
          health_policy_key: policyKey,
          expected_revision: existing.revision,
        });
        setNotice({
          kind: "saved",
          message: result.changed
            ? `Saved. This binding is now revision ${result.revision}.`
            : "Nothing to save — the preset and policy are unchanged.",
        });
        onSaved?.(existing.id);
      } else {
        const [kind, name] = workload.split("/");
        const created = await createBinding(csrfToken, {
          environment_service_id: environmentServiceId,
          cluster_id: clusterId,
          namespace,
          workload_kind: kind,
          workload_name: name,
          preset_key: presetKey,
          health_policy_key: policyKey,
        });
        setNotice({
          kind: "saved",
          message: created.resolved
            ? "Bound. The workload was found in cluster inventory."
            : "Bound. The workload has not been reported by the cluster agent yet, so health is unresolved rather than unhealthy.",
        });
        onSaved?.(created.id);
      }
    } catch (error) {
      failed(error);
    } finally {
      setBusy(false);
    }
  };

  const act = async (run: () => Promise<unknown>, message: string) => {
    if (!csrfToken) return;
    setBusy(true);
    setNotice({ kind: "none" });
    try {
      await run();
      setNotice({ kind: "saved", message });
      if (existing) onSaved?.(existing.id);
    } catch (error) {
      failed(error);
    } finally {
      setBusy(false);
    }
  };

  if (loadError) {
    return <DataState kind="error" description={loadError} onRetry={load} />;
  }
  if (!options) return <DataState kind="loading" />;

  const complete = Boolean(clusterId && namespace && workload);

  return (
    <Card title={existing ? "Edit binding" : "Bind a workload"}>
      {!canManage ? (
        <div className="mb-3">
          <DataState
            kind="permission-denied"
            description="Binding a service to a workload needs integration.manage in this scope."
          />
        </div>
      ) : null}

      <form onSubmit={submit} className="space-y-4" aria-label="Workload binding">
        {existing ? (
          <p className="rounded-lg border border-border bg-surface-sunken px-3 py-2 text-xs text-ink-secondary">
            Bound to{" "}
            <span className="font-mono">
              {existing.cluster_ref}/{existing.namespace}/{existing.workload_kind}/
              {existing.workload_name}
            </span>
            . Pointing a service at a different workload is a new binding, so its health
            history always refers to one thing.
          </p>
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <Field label="Cluster">
              <select
                className={SELECT_CLASS}
                value={clusterId}
                disabled={!canManage || busy}
                onChange={(event) => chooseCluster(event.target.value)}
              >
                <option value="">Select a cluster…</option>
                {options.clusters.map((cluster) => (
                  <option key={cluster.id} value={cluster.id}>
                    {cluster.display_name || cluster.cluster_ref}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="Namespace">
              <select
                className={SELECT_CLASS}
                value={namespace}
                disabled={!canManage || busy || !clusterId}
                onChange={(event) => chooseNamespace(event.target.value)}
              >
                <option value="">
                  {clusterId ? "Select a namespace…" : "Choose a cluster first"}
                </option>
                {options.namespaces.map((entry) => (
                  <option key={entry} value={entry}>
                    {entry}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="Workload">
              <select
                className={SELECT_CLASS}
                value={workload}
                disabled={!canManage || busy || !namespace}
                onChange={(event) => setWorkload(event.target.value)}
              >
                <option value="">
                  {namespace ? "Select a workload…" : "Choose a namespace first"}
                </option>
                {options.workloads.map((entry) => (
                  <option key={`${entry.kind}/${entry.name}`} value={`${entry.kind}/${entry.name}`}>
                    {entry.kind} · {entry.name}
                  </option>
                ))}
              </select>
            </Field>
          </div>
        )}

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field
            label="Metric preset"
            hint={
              options.presets.find((preset) => preset.key === presetKey)?.description ??
              undefined
            }
          >
            <select
              className={SELECT_CLASS}
              value={presetKey}
              disabled={!canManage || busy}
              onChange={(event) => setPresetKey(event.target.value)}
            >
              {options.presets.map((preset) => (
                <option key={preset.key} value={preset.key}>
                  {preset.title}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Health policy">
            <select
              className={SELECT_CLASS}
              value={policyKey}
              disabled={!canManage || busy}
              onChange={(event) => setPolicyKey(event.target.value)}
            >
              {options.policies.map((policy) => (
                <option key={policy.key} value={policy.key}>
                  {policy.title}
                </option>
              ))}
            </select>
          </Field>
        </div>

        <div
          className="rounded-lg border border-border bg-surface-sunken px-3 py-2 text-xs text-ink-secondary"
          data-testid="datasource-state"
        >
          {/* State only. A datasource is configured by someone with
              integration access, never typed into this form. */}
          Telemetry datasource:{" "}
          <span className="font-medium text-ink">
            {options.datasource?.configured ? "configured" : "not configured"}
          </span>
          {options.datasource?.configured
            ? null
            : " — health will report as not configured until an integration is set up for this project."}
        </div>

        {notice.kind === "conflict" ? (
          <div role="alert" data-testid="version-conflict">
            <DataState kind="error" title="Version conflict" description={notice.message} />
          </div>
        ) : null}
        {notice.kind === "error" ? (
          <div role="alert">
            <DataState kind="error" description={notice.message} />
            {notice.correlationId ? (
              <p className="mt-1 text-xs text-ink-muted">
                Correlation ID: <span className="font-mono">{notice.correlationId}</span>
              </p>
            ) : null}
          </div>
        ) : null}
        {notice.kind === "saved" ? (
          <p role="status" className="text-xs text-ink-secondary" data-testid="save-notice">
            {notice.message}
          </p>
        ) : null}

        <div className="flex flex-wrap gap-2">
          <button
            type="submit"
            disabled={!canManage || busy || (!existing && !complete)}
            className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
          >
            {existing ? "Save changes" : "Create binding"}
          </button>

          {existing ? (
            <>
              <button
                type="button"
                disabled={!canManage || busy}
                onClick={() =>
                  act(
                    () => resolveBinding(csrfToken!, existing.id),
                    "Re-checked cluster inventory.",
                  )
                }
                className="rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-ink-secondary hover:bg-surface-sunken disabled:opacity-50"
              >
                Re-resolve
              </button>
              <button
                type="button"
                disabled={!canManage || busy}
                onClick={() =>
                  act(
                    () =>
                      setBindingLifecycle(
                        csrfToken!,
                        existing.id,
                        existing.lifecycle === "active" ? "disabled" : "active",
                        existing.revision,
                      ),
                    existing.lifecycle === "active"
                      ? "Binding disabled. It is kept, not deleted."
                      : "Binding re-enabled.",
                  )
                }
                className="rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-ink-secondary hover:bg-surface-sunken disabled:opacity-50"
              >
                {existing.lifecycle === "active" ? "Disable" : "Re-enable"}
              </button>
            </>
          ) : null}
        </div>
      </form>
    </Card>
  );
}
