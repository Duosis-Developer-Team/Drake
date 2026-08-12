"use client";

/**
 * Notification policy management.
 *
 * Every input is a select or a checkbox over a vocabulary the API
 * published. There is no URL field, no header field, no JSON body and no
 * message template — a routing rule says *which incidents* and *to whom*,
 * and nothing else.
 */

import { useCallback, useEffect, useState } from "react";

import { DataState } from "@/components/state/DataState";
import { StatusBadge } from "@/components/state/StatusBadge";
import { Card } from "@/components/ui/Card";
import { ApiError } from "@/lib/api";
import { useSession } from "@/lib/session";
import {
  EVENT_TYPES,
  EVENT_TYPE_LABELS,
  createPolicy,
  fetchDestinations,
  fetchPolicies,
  fetchPolicyOptions,
  updatePolicy,
  type NotificationDestination,
  type NotificationEventType,
  type NotificationPolicy,
  type PolicyOptions,
} from "@/lib/notifications";

interface ProjectSummary {
  id: string;
  project_key: string;
  display_name: string;
}

interface EnvironmentSummary {
  id: string;
  environment_key: string;
}

const SELECT_CLASS =
  "w-full rounded-lg border border-border bg-surface px-2.5 py-1.5 text-sm text-ink disabled:opacity-50";

type Notice =
  | { kind: "none" }
  | { kind: "saved"; message: string }
  | { kind: "conflict"; message: string }
  | { kind: "error"; message: string };

function PolicyForm({
  projects,
  options,
  destinations,
  existing,
  onSaved,
}: {
  projects: ProjectSummary[];
  options: PolicyOptions;
  destinations: NotificationDestination[];
  existing: NotificationPolicy | null;
  onSaved: () => void;
}) {
  const { state: session, hasPermission } = useSession();
  const csrfToken = session.status === "authenticated" ? session.me.csrf_token : null;
  const canManage = hasPermission("notification.manage");

  const [name, setName] = useState(existing?.display_name ?? "");
  const [projectId, setProjectId] = useState(existing?.project_id ?? "");
  const [environmentId, setEnvironmentId] = useState(existing?.environment_id ?? "");
  const [events, setEvents] = useState<NotificationEventType[]>(
    existing?.event_types ?? ["opened", "auto_resolved"],
  );
  const [enabled, setEnabled] = useState(existing?.enabled ?? true);
  const [environments, setEnvironments] = useState<EnvironmentSummary[]>([]);
  const [notice, setNotice] = useState<Notice>({ kind: "none" });
  const [busy, setBusy] = useState(false);

  // Environments depend on the chosen project, and change with it. Leaving
  // a stale one selected is how a rule ends up scoped to the wrong place.
  useEffect(() => {
    if (!projectId) {
      setEnvironments([]);
      return;
    }
    let cancelled = false;
    fetch(`/v1/projects/${projectId}/environments`, { credentials: "include" })
      .then((response) => (response.ok ? response.json() : { environments: [] }))
      .then((body: { environments?: EnvironmentSummary[] }) => {
        if (!cancelled) setEnvironments(body.environments ?? []);
      })
      .catch(() => {
        if (!cancelled) setEnvironments([]);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const chooseProject = (next: string) => {
    setProjectId(next);
    setEnvironmentId("");
    setNotice({ kind: "none" });
  };

  const toggleEvent = (event: NotificationEventType) => {
    setEvents((current) =>
      current.includes(event)
        ? current.filter((entry) => entry !== event)
        : [...current, event],
    );
  };

  const submit = async (formEvent: React.FormEvent) => {
    formEvent.preventDefault();
    if (!csrfToken) return;
    setBusy(true);
    setNotice({ kind: "none" });
    try {
      if (existing) {
        await updatePolicy(csrfToken, existing.id, {
          display_name: name,
          environment_id: environmentId || null,
          event_types: events,
          enabled,
          expected_version: existing.version,
        });
        setNotice({ kind: "saved", message: "Saved. Only future incidents are affected." });
      } else {
        await createPolicy(csrfToken, {
          display_name: name,
          project_id: projectId,
          environment_id: environmentId || null,
          event_types: events,
        });
        setNotice({
          kind: "saved",
          message:
            "Created. It applies from now on — past incidents are not replayed.",
        });
      }
      onSaved();
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setNotice({
          kind: "conflict",
          message:
            "This policy changed while you were editing it — someone else saved first. Reload to see the current values before saving again.",
        });
      } else {
        setNotice({
          kind: "error",
          message: error instanceof ApiError ? error.message : "request failed",
        });
      }
    } finally {
      setBusy(false);
    }
  };

  const complete = Boolean(name && (existing || projectId) && events.length > 0);

  return (
    <Card title={existing ? "Edit policy" : "New policy"}>
      {!canManage ? (
        <div className="mb-3">
          <DataState
            kind="permission-denied"
            description="Managing notification policies needs notification.manage in this scope."
          />
        </div>
      ) : null}

      <form onSubmit={submit} className="space-y-4" aria-label="Notification policy">
        <label className="block space-y-1">
          <span className="text-xs font-medium text-ink-secondary">Name</span>
          <input
            type="text"
            value={name}
            maxLength={120}
            disabled={!canManage || busy}
            onChange={(event) => setName(event.target.value)}
            className={SELECT_CLASS}
          />
        </label>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="block space-y-1">
            <span className="text-xs font-medium text-ink-secondary">Project</span>
            <select
              className={SELECT_CLASS}
              value={projectId}
              disabled={!canManage || busy || Boolean(existing)}
              onChange={(event) => chooseProject(event.target.value)}
            >
              <option value="">Select a project…</option>
              {projects.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.display_name || project.project_key}
                </option>
              ))}
            </select>
          </label>

          <label className="block space-y-1">
            <span className="text-xs font-medium text-ink-secondary">
              Environment (optional)
            </span>
            <select
              className={SELECT_CLASS}
              value={environmentId}
              disabled={!canManage || busy || !projectId}
              onChange={(event) => setEnvironmentId(event.target.value)}
            >
              <option value="">
                {projectId ? "All environments" : "Choose a project first"}
              </option>
              {environments.map((environment) => (
                <option key={environment.id} value={environment.id}>
                  {environment.environment_key}
                </option>
              ))}
            </select>
          </label>
        </div>

        <fieldset className="space-y-1">
          <legend className="text-xs font-medium text-ink-secondary">Notify on</legend>
          <div className="flex flex-wrap gap-3">
            {(options.event_types ?? EVENT_TYPES).map((event) => (
              <label key={event} className="flex items-center gap-1.5 text-xs text-ink">
                <input
                  type="checkbox"
                  checked={events.includes(event)}
                  disabled={!canManage || busy}
                  onChange={() => toggleEvent(event)}
                />
                {EVENT_TYPE_LABELS[event]}
              </label>
            ))}
          </div>
          <p className="text-[11px] text-ink-muted">
            Severity is <span className="font-mono">critical</span>; recovery progress
            events are never notified.
          </p>
        </fieldset>

        {existing ? (
          <label className="flex items-center gap-2 text-xs text-ink">
            <input
              type="checkbox"
              checked={enabled}
              disabled={!canManage || busy}
              onChange={(event) => setEnabled(event.target.checked)}
            />
            Enabled
          </label>
        ) : null}

        <div className="rounded-lg border border-border bg-surface-sunken px-3 py-2 text-xs text-ink-secondary">
          Destinations available in this scope:{" "}
          <span className="font-medium text-ink">{destinations.length}</span>. Webhook
          targets are configured by an operator and chosen by name — there is no URL to
          enter here.
        </div>

        {notice.kind === "conflict" ? (
          <div role="alert" data-testid="policy-conflict">
            <DataState kind="error" title="Version conflict" description={notice.message} />
          </div>
        ) : null}
        {notice.kind === "error" ? (
          <div role="alert">
            <DataState kind="error" description={notice.message} />
          </div>
        ) : null}
        {notice.kind === "saved" ? (
          <p role="status" data-testid="policy-saved" className="text-xs text-ink-secondary">
            {notice.message}
          </p>
        ) : null}

        <button
          type="submit"
          disabled={!canManage || busy || !complete}
          className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
        >
          {existing ? "Save changes" : "Create policy"}
        </button>
      </form>
    </Card>
  );
}

export default function NotificationPoliciesPage() {
  const [policies, setPolicies] = useState<NotificationPolicy[] | null>(null);
  const [destinations, setDestinations] = useState<NotificationDestination[]>([]);
  const [options, setOptions] = useState<PolicyOptions | null>(null);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [editing, setEditing] = useState<NotificationPolicy | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    let cancelled = false;
    Promise.all([
      fetchPolicies(),
      fetchPolicyOptions(),
      fetchDestinations(),
      fetch("/v1/projects", { credentials: "include" })
        .then((response) => (response.ok ? response.json() : { projects: [] }))
        .then((body: { projects?: ProjectSummary[] }) => body.projects ?? []),
    ])
      .then(([loadedPolicies, loadedOptions, loadedDestinations, loadedProjects]) => {
        if (cancelled) return;
        setPolicies(loadedPolicies);
        setOptions(loadedOptions);
        setDestinations(loadedDestinations);
        setProjects(loadedProjects);
        setError(null);
      })
      .catch((problem: unknown) => {
        if (!cancelled) {
          setError(problem instanceof ApiError ? problem.message : "request failed");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => load(), [load]);

  return (
    <div className="mx-auto w-full max-w-[110rem] space-y-5 px-4 py-5 lg:px-6">
      <div>
        <h1 className="text-title font-semibold text-ink">
          Notification policies
        </h1>
        <p className="mt-1 max-w-3xl text-caption text-ink-secondary">
          Which incidents are routed, and to whom. A policy applies from the moment it is
          saved; it never replays incidents that already happened.
        </p>
      </div>

      {error ? (
        <Card>
          <DataState kind="error" description={error} onRetry={load} />
        </Card>
      ) : null}
      {policies === null && !error ? <DataState kind="loading" /> : null}

      {policies !== null ? (
        <Card title="Policies">
          {policies.length === 0 ? (
            <DataState
              kind="empty"
              title="No policies"
              description="Nothing is routed yet. Incidents still open and resolve; nobody is told about them."
            />
          ) : (
            <ul className="divide-y divide-border" data-testid="policy-list">
              {policies.map((policy) => (
                <li
                  key={policy.id}
                  className="flex flex-wrap items-center gap-3 py-2.5"
                  data-testid={`policy-${policy.id}`}
                >
                  <StatusBadge
                    status={policy.enabled ? "healthy" : "unknown"}
                    label={policy.enabled ? "Enabled" : "Disabled"}
                  />
                  <span className="text-sm font-medium text-ink">{policy.display_name}</span>
                  <span className="font-mono text-[11px] text-ink-muted">
                    {policy.project_key}
                    {policy.environment_key ? `/${policy.environment_key}` : ""}
                    {policy.service_key ? `/${policy.service_key}` : ""}
                  </span>
                  <span className="text-[11px] text-ink-secondary">
                    {policy.event_types
                      .map((event) => EVENT_TYPE_LABELS[event] ?? event)
                      .join(", ")}
                  </span>
                  <span className="text-[11px] text-ink-muted">
                    {policy.destination_count} destination
                    {policy.destination_count === 1 ? "" : "s"}
                  </span>
                  <button
                    type="button"
                    onClick={() => setEditing(policy)}
                    className="ml-auto text-xs font-medium text-ink-secondary underline hover:text-ink"
                  >
                    Edit
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      ) : null}

      {options ? (
        <PolicyForm
          key={editing?.id ?? "new"}
          projects={projects}
          options={options}
          destinations={destinations}
          existing={editing}
          onSaved={() => {
            setEditing(null);
            load();
          }}
        />
      ) : null}
    </div>
  );
}
