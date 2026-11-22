"use client";

/**
 * Who is supposed to send backup evidence, and what each is still missing.
 *
 * Protection judges evidence a backup job sends after it runs. When nothing
 * has arrived, the honest screen is not an empty table but the list of
 * reporters the reviewed contract expects — per project, with the policies
 * each one will be judged on — and, for each, the one step it is waiting
 * for: registration in this deployment, its signing secret, or a first
 * report. The how-to below is the same for every team.
 */

import { CheckCircle2, KeyRound, Radio } from "lucide-react";
import { useState } from "react";

import { useApi } from "@/components/catalog/primitives";
import { StatusBadge } from "@/components/state/StatusBadge";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { ProjectLogo } from "@/components/ui/ProjectLogo";
import { useFormat, useT } from "@/lib/i18n";
import type { ConnectorStatus, ReporterStatus } from "@/lib/protection";

const STATE_TONE = {
  reporting: "success",
  waiting: "info",
  not_registered: "unknown",
} as const;

function Reporter({
  reporter,
  open,
  onToggle,
}: {
  reporter: ReporterStatus;
  open: boolean;
  onToggle: () => void;
}) {
  const t = useT("protection");
  const fmt = useFormat();
  const steps = [
    { done: reporter.registered, icon: Radio, label: t("setup.step.registered") },
    { done: reporter.secret_present, icon: KeyRound, label: t("setup.step.secret") },
    {
      done: reporter.last_seen_at !== null,
      icon: CheckCircle2,
      label: reporter.last_seen_at
        ? t("setup.step.seen", { when: fmt.relative(reporter.last_seen_at) })
        : t("setup.step.firstReport"),
    },
  ];
  const doneCount = steps.filter((s) => s.done).length;
  return (
    <li
      className="px-5 py-4 sm:px-7"
      data-testid={`reporter-${reporter.connector_key}`}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full flex-wrap items-center gap-x-4 gap-y-2 text-left"
      >
        <ProjectLogo
          projectId={reporter.project_id ?? ""}
          name={reporter.project_key}
          size="md"
          variant="mark"
        />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-body font-semibold text-ink">
            {reporter.display_name}
          </span>
          <span className="block truncate font-mono text-micro text-ink-muted">
            {reporter.connector_key} ·{" "}
            {t("setup.policies", { count: reporter.policies.length })}
          </span>
        </span>
        {/* Three steps to reporting, as a track that fills. */}
        <span aria-hidden className="hidden items-center gap-1 sm:flex">
          {steps.map((s, i) => (
            <span
              key={i}
              className={`h-1.5 w-8 rounded-full ${s.done ? "bg-[var(--status-success)]" : "bg-surface-3"}`}
            />
          ))}
          <span data-tabular className="ml-1.5 font-mono text-micro text-ink-muted">
            {doneCount}/3
          </span>
        </span>
        <StatusBadge
          status={STATE_TONE[reporter.state]}
          label={t.dyn("setup.state", reporter.state, reporter.state)}
          size="compact"
        />
      </button>
      {open ? (
        <div className="mt-4 space-y-4 sm:pl-11">
          <ol className="grid gap-2 sm:grid-cols-3">
            {steps.map(({ done, icon: Icon, label }, i) => (
              <li
                key={label}
                className={`flex items-center gap-2.5 rounded-xl border px-3 py-2.5 text-caption ${
                  done ? "border-[var(--status-success)]/40 bg-healthy-soft/50 text-ink" : "border-dashed border-border-strong text-ink-muted"
                }`}
              >
                <span
                  className={`grid h-6 w-6 shrink-0 place-items-center rounded-full font-mono text-[11px] font-semibold ${
                    done ? "bg-[var(--status-success)] text-ink-inverse" : "border border-border-strong"
                  }`}
                >
                  {done ? <Icon aria-hidden className="h-3.5 w-3.5" /> : i + 1}
                </span>
                <span className="min-w-0">{label}</span>
              </li>
            ))}
          </ol>
          <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-surface-2/40">
            {reporter.policies.map((policy) => (
              <li
                key={policy.policy_key}
                className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2 text-caption"
              >
                <span className="min-w-0 flex-1">
                  <span className="font-medium text-ink">
                    {policy.display_name}
                  </span>
                  <span className="ml-2 font-mono text-micro text-ink-muted">
                    {policy.policy_key}
                  </span>
                </span>
                <span className="text-ink-secondary">
                  {[
                    policy.environment_key,
                    policy.schedule,
                    policy.rpo_seconds
                      ? t("setup.rpo", {
                          value: fmt.duration(policy.rpo_seconds, {
                            compact: true,
                          }),
                        })
                      : null,
                    policy.requires_offsite ? t("setup.offsite") : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </li>
  );
}

function HowTo({ status }: { status: ConnectorStatus }) {
  const t = useT("protection");
  const example = status.reporters[0]?.connector_key ?? "<reporter>";
  const policy = status.reporters[0]?.policies[0]?.policy_key ?? "<policy>";
  const project = status.reporters[0]?.project_key ?? "<project>";
  return (
    <div
      className="space-y-3 border-t border-border bg-surface-2/40 px-5 py-5 sm:px-7"
      data-testid="reporter-howto"
    >
      <p className="text-caption font-semibold text-ink">
        {t("setup.howTo.title")}
      </p>
      <ol className="list-decimal space-y-3 pl-5 text-caption text-ink-secondary">
        <li>
          {t("setup.howTo.secret")}
          <pre className="mt-1.5 overflow-x-auto rounded-control bg-surface-2 px-3 py-2 font-mono text-micro text-ink">
            {`kubectl -n drake-prod create secret generic tracesis-protection-${example} \\\n  --from-literal=signing-secret="$(openssl rand -hex 32)"`}
          </pre>
        </li>
        <li>
          {t("setup.howTo.register")}
          <pre className="mt-1.5 overflow-x-auto rounded-control bg-surface-2 px-3 py-2 font-mono text-micro text-ink">
            {`protection:\n  connectors:\n    ${example}: {projectKey: ${project}, existingSecret: tracesis-protection-${example}}`}
          </pre>
        </li>
        <li>
          {t("setup.howTo.report")}
          <pre className="mt-1.5 overflow-x-auto rounded-control bg-surface-2 px-3 py-2 font-mono text-micro text-ink">
            {`# apps/api/src/drake_api/protection/report_cli.py — standard library only\npython3 report_cli.py --url <tracesis> --connector ${example} \\\n  --secret-file /run/secrets/signing-secret --policy ${policy} \\\n  --artifact <dump file> --started <ISO time> [--site <site> --offsite]`}
          </pre>
        </li>
      </ol>
      <p className="text-micro text-ink-muted">
        {t("setup.howTo.signing", {
          headers: [
            status.signing.connector_header,
            status.signing.timestamp_header,
            status.signing.signature_header,
          ].join(", "),
          material: status.signing.signed_material,
        })}
      </p>
      {!status.evaluating ? (
        <p className="text-micro text-warning">{t("setup.notEvaluating")}</p>
      ) : null}
    </div>
  );
}

export function ReporterSetup({ emphasis }: { emphasis: boolean }) {
  const t = useT("protection");
  const [status] = useApi<ConnectorStatus>("/v1/protection/connectors");
  const [openKey, setOpenKey] = useState<string | null>(null);
  if (status.state !== "ready" || status.data.reporters.length === 0)
    return null;
  const waiting = status.data.reporters.filter(
    (r) => r.state !== "reporting",
  ).length;
  return (
    <Panel flush data-testid="reporter-setup">
      <PanelHeader
        flush
        title={t("setup.title")}
        description={
          waiting > 0
            ? t("setup.descriptionWaiting", {
                waiting,
                total: status.data.reporters.length,
              })
            : t("setup.descriptionAll")
        }
      />
      <ul className="divide-y divide-border">
        {status.data.reporters.map((reporter) => (
          <Reporter
            key={reporter.connector_key}
            reporter={reporter}
            open={
              openKey === reporter.connector_key ||
              (emphasis &&
                openKey === null &&
                reporter === status.data.reporters[0])
            }
            onToggle={() =>
              setOpenKey(
                openKey === reporter.connector_key
                  ? ""
                  : reporter.connector_key,
              )
            }
          />
        ))}
      </ul>
      {waiting > 0 ? <HowTo status={status.data} /> : null}
    </Panel>
  );
}
