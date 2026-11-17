"use client";

/**
 * An environment's scheduled jobs: each CronJob with its health, its last
 * run and last success, how its recent runs ended, and its logs on demand.
 *
 * The health comes from the inventory's CronJob rule, so a job whose recent
 * runs failed reads "degraded" with the reason. A job that once succeeded
 * three months ago does not read healthy. Run outcomes are shown as
 * recorded. A run still in progress is "running", not a failure.
 */

import { CalendarClock, ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";

import { TileState } from "@/components/catalog/visuals";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { InlineCode, RelativeTime } from "@/components/ui/identifiers";
import { DeniedState, ErrorState, LoadingSkeleton } from "@/components/ui/states";
import { humanize, toneForHealth, type StatusTone } from "@/lib/design/status";
import { useT } from "@/lib/i18n";
import type { LogEntry } from "@/lib/logs";
import { useResource } from "@/lib/useResource";

export type RunOutcome = "succeeded" | "failed" | "running" | "unknown";

export interface ScheduledJobRun {
  name: string;
  scheduled_at: string | null;
  outcome: RunOutcome;
}

export interface ScheduledJob {
  name: string;
  schedule: string | null;
  suspended: boolean;
  health: string;
  reasons: string[];
  last_schedule_time: string | null;
  last_successful_time: string | null;
  recent_runs: ScheduledJobRun[];
}

export interface ScheduledJobsPage {
  environment_id: string;
  as_of: string;
  state: "ok" | "not_applicable";
  jobs: ScheduledJob[];
}

interface ScheduledJobLogs {
  state: "ok" | "not_configured" | "unavailable";
  reason?: string;
  entries: LogEntry[];
}

const OUTCOME_TONE: Record<RunOutcome, StatusTone> = {
  succeeded: "success",
  failed: "critical",
  running: "info",
  unknown: "unknown",
};

function JobLogs({ base, name }: { base: string; name: string }) {
  const t = useT("scheduledJobs");
  const logs = useResource<ScheduledJobLogs>(
    `${base}/${encodeURIComponent(name)}/logs?range=24h&limit=100`,
  );
  if (logs.loading && !logs.data) return <LoadingSkeleton rows={3} />;
  if (logs.notFound || logs.denied) {
    return <p className="text-caption text-ink-muted">{t("logs.denied")}</p>;
  }
  if (!logs.data) {
    return <ErrorState compact description={logs.error ?? undefined} onRetry={logs.reload} />;
  }
  if (logs.data.state === "not_configured") {
    return <p className="text-caption text-ink-muted">{t("logs.notConfigured")}</p>;
  }
  if (logs.data.state === "unavailable") {
    return (
      <p className="text-caption text-ink-muted">
        {t("logs.unavailable", { reason: logs.data.reason ?? "—" })}
      </p>
    );
  }
  if (logs.data.entries.length === 0) {
    return <p className="text-caption text-ink-muted">{t("logs.empty")}</p>;
  }
  return (
    <ol className="max-h-80 overflow-y-auto rounded-[0.75rem] border border-border bg-surface-2/40 font-mono text-micro" data-testid="scheduled-job-logs">
      {logs.data.entries.map((entry, index) => (
        <li key={`${entry.timestamp}-${index}`} className="flex gap-3 border-b border-border px-3 py-1.5 last:border-b-0">
          <span className="shrink-0 text-ink-muted">{entry.timestamp.slice(5, 19).replace("T", " ")}</span>
          <span className="shrink-0 text-ink-muted">{entry.pod ?? ""}</span>
          <span className="min-w-0 break-words whitespace-pre-wrap text-ink">{entry.body}</span>
        </li>
      ))}
    </ol>
  );
}

function JobRow({ job, base }: { job: ScheduledJob; base: string }) {
  const t = useT("scheduledJobs");
  const [open, setOpen] = useState(false);
  return (
    <li className="min-w-0 border-b border-border px-7 py-5 last:border-b-0" data-testid="scheduled-job">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-body font-semibold text-ink">{job.name}</p>
          <p className="mt-1 text-caption text-ink-muted">
            {t("schedule")}: <InlineCode>{job.schedule ?? "—"}</InlineCode>
            {job.suspended ? ` · ${t("suspended")}` : ""}
          </p>
        </div>
        <StatusBadge
          status={toneForHealth(job.health)}
          label={humanize(job.health)}
          size="compact"
        />
      </div>
      {job.reasons.length > 0 ? (
        <ul className="mt-2 text-caption text-ink" data-testid="scheduled-job-reasons">
          {job.reasons.map((reason) => (
            <li key={reason}>{t.dyn("reason", reason, humanize(reason))}</li>
          ))}
        </ul>
      ) : null}
      <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-1 text-caption sm:grid-cols-2">
        <div className="flex gap-2">
          <dt className="text-ink-muted">{t("lastRun")}</dt>
          <dd>{job.last_schedule_time ? <RelativeTime value={job.last_schedule_time} /> : t("never")}</dd>
        </div>
        <div className="flex gap-2">
          <dt className="text-ink-muted">{t("lastSuccess")}</dt>
          <dd>{job.last_successful_time ? <RelativeTime value={job.last_successful_time} /> : t("never")}</dd>
        </div>
      </dl>
      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-micro text-ink-muted">{t("recentRuns")}</span>
        {job.recent_runs.length === 0 ? (
          <span className="text-micro text-ink-muted">{t("noRuns")}</span>
        ) : (
          job.recent_runs.map((run) => (
            <span key={run.name} title={run.scheduled_at ?? run.name}>
              <StatusBadge
                status={OUTCOME_TONE[run.outcome] ?? "unknown"}
                label={t.dyn("outcome", run.outcome, humanize(run.outcome))}
                size="compact"
              />
            </span>
          ))
        )}
      </div>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="mt-3 inline-flex items-center gap-1 text-caption font-medium text-accent hover:underline"
      >
        {open ? <ChevronDown aria-hidden className="h-4 w-4" /> : <ChevronRight aria-hidden className="h-4 w-4" />}
        {open ? t("hideLogs") : t("showLogs")}
      </button>
      {open ? (
        <div className="mt-3">
          <p className="mb-2 text-micro text-ink-muted">{t("logs.title")}</p>
          <JobLogs base={base} name={job.name} />
        </div>
      ) : null}
    </li>
  );
}

export function ScheduledJobs({
  projectId,
  environmentId,
}: {
  projectId: string;
  environmentId: string;
}) {
  const t = useT("scheduledJobs");
  const base = `/v1/projects/${projectId}/environments/${environmentId}/scheduled-jobs`;
  const page = useResource<ScheduledJobsPage>(base);

  return (
    <Panel flush data-testid="scheduled-jobs">
      <PanelHeader flush title={t("title")} description={t("description")} />
      {page.loading && !page.data ? (
        <div className="px-7 py-6">
          <LoadingSkeleton variant="table" rows={2} />
        </div>
      ) : page.denied ? (
        <div className="px-7 py-6">
          <DeniedState compact />
        </div>
      ) : !page.data ? (
        <div className="px-7 py-6">
          <ErrorState
            compact
            description={page.error ?? t("loadError")}
            correlationId={page.correlationId}
            onRetry={page.reload}
          />
        </div>
      ) : page.data.state === "not_applicable" ? (
        <div className="px-7 py-6 text-caption text-ink-muted">{t("notApplicable")}</div>
      ) : page.data.jobs.length === 0 ? (
        <div className="px-7 py-8">
          <TileState icon={CalendarClock} testId="scheduled-jobs-empty" title={t("empty")} />
        </div>
      ) : (
        <ul>
          {page.data.jobs.map((job) => (
            <JobRow key={job.name} job={job} base={base} />
          ))}
        </ul>
      )}
    </Panel>
  );
}
