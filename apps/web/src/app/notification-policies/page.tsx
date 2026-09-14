"use client";

/**
 * Notification policy management.
 *
 * Every input is a select or a checkbox over a vocabulary the API
 * published. There is no URL field, no header field, no JSON body and no
 * message template — a routing rule says *which incidents* and *to whom*,
 * and nothing else.
 */

import Link from "next/link";
import { useCallback, useEffect, useId, useState } from "react";
import {
  ArrowRight,
  BellRing,
  CheckCheck,
  CircleCheck,
  Layers,
  Pencil,
  Route,
  Send,
  Webhook,
} from "lucide-react";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";

import {
  FIELD_LABEL,
  IconBubble,
  KpiTile,
  PILL_BUTTON,
  PILL_FIELD,
  PILL_PRIMARY,
  ShareBar,
  StateCard,
} from "@/components/features/configure/kit";
import { DataState } from "@/components/state/DataState";
import { StatusBadge } from "@/components/state/StatusBadge";
import { Panel, PanelHeader } from "@/components/ui/Panel";
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

type Notice =
  | { kind: "none" }
  | { kind: "saved"; message: string }
  | { kind: "conflict"; message: string }
  | { kind: "error"; message: string };

function Step({
  number,
  title,
  children,
}: {
  number: number;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="relative pl-10">
      <span
        aria-hidden
        className="absolute top-0 left-0 flex h-7 w-7 items-center justify-center rounded-full bg-surface-3 text-micro font-semibold text-ink"
      >
        {number}
      </span>
      <p className="pt-1 text-body font-semibold text-ink">{title}</p>
      <div className="mt-3">{children}</div>
    </div>
  );
}

function PolicyForm({
  projects,
  options,
  destinations,
  existing,
  onSaved,
  onCancel,
}: {
  projects: ProjectSummary[];
  options: PolicyOptions;
  destinations: NotificationDestination[];
  existing: NotificationPolicy | null;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const { state: session, hasPermission } = useSession();
  const csrfToken = session.status === "authenticated" ? session.me.csrf_token : null;
  const canManage = hasPermission("notification.manage");
  const nameId = useId();
  const projectFieldId = useId();
  const envFieldId = useId();

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
    <Panel>
      <PanelHeader
        title={existing ? "Edit policy" : "New policy"}
        description="Applies from the moment it is saved"
        actions={
          existing ? (
            <button type="button" onClick={onCancel} className={PILL_BUTTON}>
              Cancel
            </button>
          ) : (
            <IconBubble icon={Route} size="sm" />
          )
        }
      />
      {!canManage ? (
        <div className="rounded-[1.25rem] bg-surface-2 p-4">
          <DataState
            kind="permission-denied"
            description="Managing notification policies needs notification.manage in this scope."
          />
        </div>
      ) : null}

      <form onSubmit={submit} className="space-y-6" aria-label="Notification policy">
        <Step number={1} title="Name this rule">
          <label htmlFor={nameId} className="sr-only">
            Name
          </label>
          <input
            id={nameId}
            type="text"
            value={name}
            maxLength={120}
            disabled={!canManage || busy}
            placeholder="e.g. Critical incidents"
            onChange={(event) => setName(event.target.value)}
            className={PILL_FIELD}
          />
        </Step>

        <Step number={2} title="Scope">
          <div className="space-y-3">
            <div>
              <label htmlFor={projectFieldId} className={FIELD_LABEL}>
                Project
              </label>
              <select
                id={projectFieldId}
                className={PILL_FIELD}
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
            </div>

            <div>
              <label htmlFor={envFieldId} className={FIELD_LABEL}>
                Environment (optional)
              </label>
              <select
                id={envFieldId}
                className={PILL_FIELD}
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
            </div>
          </div>
        </Step>

        <Step number={3} title="Notify on">
          <fieldset className="flex flex-wrap gap-2">
            <legend className="sr-only">Notify on</legend>
            {(options.event_types ?? EVENT_TYPES).map((event) => {
              const checked = events.includes(event);
              return (
                <label
                  key={event}
                  className={`inline-flex cursor-pointer items-center gap-1.5 rounded-full border px-3.5 py-2 text-caption font-medium transition-colors ${
                    checked
                      ? "border-transparent bg-accent text-ink-inverse"
                      : "border-border bg-surface text-ink-secondary hover:bg-surface-hover"
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={!canManage || busy}
                    onChange={() => toggleEvent(event)}
                    className="sr-only"
                  />
                  {checked ? <CheckCheck aria-hidden className="h-3.5 w-3.5" /> : null}
                  {EVENT_TYPE_LABELS[event]}
                </label>
              );
            })}
          </fieldset>
          <p className="mt-2 text-micro text-ink-muted">
            Severity is critical; recovery progress events are never notified.
          </p>
        </Step>

        {existing ? (
          <Step number={4} title="Status">
            <label className="inline-flex cursor-pointer items-center gap-2 text-body text-ink">
              <input
                type="checkbox"
                checked={enabled}
                disabled={!canManage || busy}
                onChange={(event) => setEnabled(event.target.checked)}
                className="h-4 w-4 rounded border-border-strong accent-[var(--accent)]"
              />
              Enabled
            </label>
          </Step>
        ) : null}

        <div className="flex items-center gap-3 rounded-[1.25rem] bg-surface-2 px-4 py-3">
          <IconBubble icon={Webhook} size="sm" />
          <p className="min-w-0 text-micro text-ink-secondary">
            <span data-tabular className="font-semibold text-ink">
              {destinations.length}
            </span>{" "}
            destinations in scope. Webhook targets are chosen by name — there is no address to
            enter here.
          </p>
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
          <p
            role="status"
            data-testid="policy-saved"
            className="flex items-center gap-2 rounded-full bg-healthy-soft px-4 py-2 text-caption text-healthy"
          >
            <CircleCheck aria-hidden className="h-4 w-4 shrink-0" />
            {notice.message}
          </p>
        ) : null}

        <button
          type="submit"
          disabled={!canManage || busy || !complete}
          className={`${PILL_PRIMARY} w-full`}
        >
          {existing ? "Save changes" : "Create policy"}
        </button>
      </form>
    </Panel>
  );
}

function RuleCard({
  policy,
  onEdit,
  editing,
}: {
  policy: NotificationPolicy;
  onEdit: () => void;
  editing: boolean;
}) {
  const scope = `${policy.project_key}${policy.environment_key ? `/${policy.environment_key}` : ""}${
    policy.service_key ? `/${policy.service_key}` : ""
  }`;
  return (
    <li
      data-testid={`policy-${policy.id}`}
      className={`rounded-[1.5rem] border bg-surface shadow-panel transition-colors ${
        editing ? "border-accent" : "border-border"
      }`}
    >
      <div className="flex flex-wrap items-center gap-4 px-6 pt-5 pb-4">
        <IconBubble icon={BellRing} tone={policy.enabled ? "info" : undefined} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-body font-semibold text-ink">{policy.display_name}</p>
          <p className="text-micro text-ink-muted">Revision {policy.version}</p>
        </div>
        <StatusBadge
          status={policy.enabled ? "healthy" : "unknown"}
          label={policy.enabled ? "Enabled" : "Disabled"}
        />
        <button type="button" onClick={onEdit} className={PILL_BUTTON}>
          <Pencil aria-hidden className="h-3.5 w-3.5" />
          Edit
        </button>
      </div>
      <div className="grid grid-cols-1 items-stretch gap-2 border-t border-border px-6 py-5 md:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)_auto_minmax(0,1fr)] md:items-center">
        <div className="h-full rounded-2xl bg-surface-2 px-4 py-3">
          <p className="flex items-center gap-1.5 text-micro font-medium tracking-[0.08em] text-ink-muted uppercase">
            <BellRing aria-hidden className="h-3 w-3" /> When
          </p>
          <p className="mt-1 text-caption font-medium text-ink">
            {policy.event_types.map((event) => EVENT_TYPE_LABELS[event] ?? event).join(", ")}
          </p>
        </div>
        <ArrowRight aria-hidden className="mx-auto hidden h-4 w-4 text-ink-muted md:block" />
        <div className="h-full rounded-2xl bg-surface-2 px-4 py-3">
          <p className="flex items-center gap-1.5 text-micro font-medium tracking-[0.08em] text-ink-muted uppercase">
            <Layers aria-hidden className="h-3 w-3" /> In
          </p>
          <p className="mt-1 truncate font-mono text-caption text-ink">{scope}</p>
        </div>
        <ArrowRight aria-hidden className="mx-auto hidden h-4 w-4 text-ink-muted md:block" />
        <div className="h-full rounded-2xl bg-surface-2 px-4 py-3">
          <p className="flex items-center gap-1.5 text-micro font-medium tracking-[0.08em] text-ink-muted uppercase">
            <Send aria-hidden className="h-3 w-3" /> Send to
          </p>
          <p className="mt-1 text-caption font-medium text-ink">
            {policy.destination_count} destination
            {policy.destination_count === 1 ? "" : "s"}
          </p>
        </div>
      </div>
    </li>
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

  const enabledCount = policies?.filter((policy) => policy.enabled).length ?? 0;
  const webhooks = destinations.filter((d) => d.destination_type === "webhook").length;

  return (
    <PageFrame>
      <PageHeader
        title="Notification policies"
        description="Which incidents are routed, and to whom. Policies never replay past incidents."
        actions={
          <Link href="/notification-deliveries" className={PILL_BUTTON}>
            <Send aria-hidden className="h-3.5 w-3.5" />
            Delivery audit
          </Link>
        }
      />
      <div className="space-y-6">
        {error ? (
          <Panel>
            <DataState kind="error" description={error} onRetry={load} />
          </Panel>
        ) : null}
        {policies === null && !error ? <DataState kind="loading" /> : null}

        {policies !== null ? (
          <div className="grid grid-cols-1 items-stretch gap-6 sm:grid-cols-3">
            <KpiTile icon={Route} label="Policies" value={policies.length}>
              <p className="text-micro text-ink-muted">Routing rules in your scope</p>
            </KpiTile>
            <KpiTile icon={CircleCheck} tone="success" label="Enabled" value={enabledCount} suffix={`of ${policies.length}`}>
              <ShareBar value={enabledCount} total={policies.length} tone="success" label="routing now" />
            </KpiTile>
            <KpiTile icon={Webhook} tone="info" label="Destinations" value={destinations.length}>
              <p className="text-micro text-ink-muted">
                <span data-tabular className="font-medium text-ink-secondary">{webhooks}</span> webhook ·{" "}
                <span data-tabular className="font-medium text-ink-secondary">
                  {destinations.length - webhooks}
                </span>{" "}
                in-app
              </p>
            </KpiTile>
          </div>
        ) : null}

        {policies !== null || options ? (
          <div className="grid grid-cols-1 items-start gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,24rem)]">
            <section aria-labelledby="policies-heading" className="min-w-0 space-y-4">
              <div className="flex items-baseline justify-between gap-3">
                <h2 id="policies-heading" className="text-[1.0625rem] font-semibold text-ink">
                  Rules
                </h2>
                {policies !== null ? (
                  <span className="text-micro text-ink-muted">{policies.length} configured</span>
                ) : null}
              </div>
              {policies !== null && policies.length === 0 ? (
                <Panel>
                  <StateCard
                    kind="empty"
                    icon={Route}
                    title="No policies"
                    description="Nothing is routed yet. Incidents still open and resolve; nobody is told about them."
                  />
                  <ol className="mx-auto grid w-full max-w-2xl grid-cols-1 gap-3 pb-2 sm:grid-cols-3" aria-label="How a rule reads">
                    {[
                      { icon: BellRing, title: "When", line: "an incident opens or resolves" },
                      { icon: Layers, title: "In", line: "a project or environment" },
                      { icon: Send, title: "Send to", line: "people or a named webhook" },
                    ].map((step) => (
                      <li key={step.title} className="flex items-center gap-3 rounded-2xl bg-surface-2 px-4 py-3">
                        <IconBubble icon={step.icon} size="sm" />
                        <span className="min-w-0">
                          <span className="block text-caption font-semibold text-ink">{step.title}</span>
                          <span className="block text-micro text-ink-muted">{step.line}</span>
                        </span>
                      </li>
                    ))}
                  </ol>
                </Panel>
              ) : null}
              {policies !== null && policies.length > 0 ? (
                <ul className="space-y-4" data-testid="policy-list">
                  {policies.map((policy) => (
                    <RuleCard
                      key={policy.id}
                      policy={policy}
                      editing={editing?.id === policy.id}
                      onEdit={() => setEditing(policy)}
                    />
                  ))}
                </ul>
              ) : null}
            </section>

            {options ? (
              <div className="xl:sticky xl:top-6">
                <PolicyForm
                  key={editing?.id ?? "new"}
                  projects={projects}
                  options={options}
                  destinations={destinations}
                  existing={editing}
                  onCancel={() => setEditing(null)}
                  onSaved={() => {
                    setEditing(null);
                    load();
                  }}
                />
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </PageFrame>
  );
}
