"use client";

/**
 * Onboarding session detail: discovery, plan, approval, result.
 *
 * The plan is the point of this screen. It says, item by item, what
 * applying the repository's stated intent would do to the catalog —
 * separated into what would be created, what would be linked to something
 * that already exists, what would change nothing, and what Drake cannot
 * resolve and refuses to guess at.
 *
 * Apply is live only when the SERVER says the plan is applicable and the
 * caller holds the apply right. Both are re-checked on the request; the
 * button state is a convenience, never a boundary.
 */

import Link from "next/link";
import { useParams } from "next/navigation";
import { useRef, useState } from "react";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";

import { LoadGate, MetaRow, useApi } from "@/components/catalog/primitives";
import { RichMessage } from "@/components/github/primitives";
import { ActionBadge, GitOpsBadge, SessionBadge } from "@/components/onboarding/primitives";
import { SessionActions } from "@/components/onboarding/SessionActions";
import { DataState } from "@/components/state/DataState";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { useFormat, useT, type Translator } from "@/lib/i18n";
import {
  shortSha,
  type Analysis,
  type ApplyResult,
  type Finding,
  type GitHubStatus,
  type OnboardingSession,
  type Plan,
  type PlanItem,
} from "@/lib/onboarding";
import { useSession } from "@/lib/session";

/**
 * The five outcomes a plan item can have, in the order an operator reads
 * them: what appears, what attaches to something that exists, what CHANGES
 * on a row that already exists, what stays as it is, and what Drake will not
 * decide.
 *
 * `update_metadata` used to sit under "No change", which was the one
 * grouping that could mislead: an item that rewrites a display name is not
 * a no-op, and filing it under one hides the only part of an apply that
 * edits an existing row.
 *
 * Each group is keyed by its first action; the title lives at
 * `detail.plan.group.<key>` and an optional note at `detail.plan.note.<key>`.
 */
const GROUPS: {
  key: "create" | "link" | "update_metadata" | "no_change" | "conflict";
  actions: string[];
  note?: "update_metadata" | "conflict";
}[] = [
  { key: "create", actions: ["create"] },
  { key: "link", actions: ["link"] },
  { key: "update_metadata", actions: ["update_metadata"], note: "update_metadata" },
  { key: "no_change", actions: ["no_change"] },
  { key: "conflict", actions: ["conflict", "unmapped", "unsupported"], note: "conflict" },
];

/** Before and after, side by side. Never a raw JSON blob. */
function Changes({ item }: { item: PlanItem }) {
  const t = useT("onboarding");
  const fields = Object.entries(item.changes ?? {});
  if (fields.length === 0) return null;
  return (
    <dl className="mt-1 ml-6 space-y-1" data-testid={`changes-${item.item_key}`}>
      {fields.map(([field, pair]) => (
        <div key={field} className="text-[11px]">
          <dt className="font-mono text-ink-secondary">{field}</dt>
          <dd className="ml-3 flex flex-wrap gap-x-3">
            <span className="text-ink-muted">
              {t("detail.plan.before")}{" "}
              <span className="font-mono text-ink-secondary">{renderValue(pair.before, t)}</span>
            </span>
            <span className="text-ink-muted">
              {t("detail.plan.after")}{" "}
              <span className="font-mono text-ink">{renderValue(pair.after, t)}</span>
            </span>
          </dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * An absent value is shown as absent.
 *
 * `null` here means the field had nothing recorded, which is not the same
 * as an empty string and definitely not the same as zero. Rendering it as
 * `""` would make "there was no display name" look like "the display name
 * was blank".
 */
function renderValue(value: unknown, t: Translator<"onboarding">): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value === "" ? "—" : value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  // Objects and arrays are summarised, never dumped: a plan review is not a
  // place to read serialized JSON.
  return Array.isArray(value)
    ? t("detail.plan.listValue", { count: value.length })
    : t("detail.plan.structuredValue");
}

export default function OnboardingSessionPage() {
  const t = useT("onboarding");
  const fmt = useFormat();
  const { sessionId } = useParams<{ sessionId: string }>();
  const { state: auth } = useSession();
  const csrfToken = auth.status === "authenticated" ? auth.me.csrf_token : "";
  const [session, reloadSession] = useApi<OnboardingSession>(
    `/v1/onboarding/sessions/${sessionId}`,
  );
  const [plan, reloadPlan] = useApi<{ plan: Plan | null; items: PlanItem[] }>(
    `/v1/onboarding/sessions/${sessionId}/plan`,
  );
  const [findings, reloadFindings] = useApi<{ analysis: Analysis | null; findings: Finding[] }>(
    `/v1/onboarding/sessions/${sessionId}/findings`,
  );
  const [status] = useApi<GitHubStatus>("/v1/onboarding/github/status");

  // Owned by the screen, not by the action panel. Reloading the session
  // unmounts that panel, so a result kept inside it would disappear the
  // instant the apply that produced it refreshed the page — and the
  // idempotency key would be regenerated, turning the next retry into a
  // second operation.
  const [applyResult, setApplyResult] = useState<ApplyResult | null>(null);
  const applyKey = useRef<{ version: number; key: string } | null>(null);

  // One refresh for all three. A mutation can change the session's state,
  // its plan and its findings together, and reloading only the one the
  // button belongs to leaves the other two describing a session that has
  // moved on.
  const reloadAll = () => {
    reloadSession();
    reloadPlan();
    reloadFindings();
  };

  return (
    <PageFrame>
      <LoadGate value={session} retry={reloadSession}>
        {(data) => (
          <>
            <PageHeader
              title={data.repository.full_name}
              description={
                <>
                  {data.repository.default_branch} · {shortSha(data.analyzed_commit_sha)}
                </>
              }
              status={<SessionBadge state={data.state} />}
            />

            <div className="space-y-6">
              {data.repository.security_gate ? (
                <Panel tone="critical">
                  <PanelHeader title={t("detail.gate.title")} />
                  <div data-testid="security-gate">
                    <DataState
                      kind="permission-denied"
                      title={t("detail.gate.cardTitle")}
                      description={t("detail.gate.description")}
                    />
                  </div>
                </Panel>
              ) : null}

              {data.state === "stale" ? (
                <Panel tone="warning">
                  <PanelHeader title={t("detail.stale.title")} />
                  <div data-testid="stale-notice">
                    <DataState
                      kind="stale"
                      title={t("detail.stale.cardTitle")}
                      description={t("detail.stale.description")}
                    />
                  </div>
                </Panel>
              ) : null}

              <SessionActions
                session={data}
                plan={plan.state === "ready" ? plan.data.plan : null}
                csrfToken={csrfToken}
                gitopsEnabled={status.state === "ready" ? status.data.gitops_pr_enabled : false}
                onChanged={reloadAll}
                result={applyResult}
                onResult={setApplyResult}
                applyKey={applyKey}
              />

              <div className="grid gap-6 md:grid-cols-2">
                <Panel>
                  <PanelHeader title={t("detail.discovery.title")} />
                  {findings.state === "loading" ? (
                    <DataState kind="loading" />
                  ) : findings.state === "error" ? (
                    <DataState kind="error" description={findings.message} />
                  ) : findings.data.analysis === null ? (
                    <DataState
                      kind="empty"
                      title={t("detail.discovery.notAnalysed.title")}
                      description={t("detail.discovery.notAnalysed.description")}
                    />
                  ) : (
                    <div data-testid="analysis">
                      <dl className="divide-y divide-border">
                        <MetaRow label={t("detail.discovery.commit")}>
                          {shortSha(findings.data.analysis.commit_sha)}
                        </MetaRow>
                        <MetaRow label={t("detail.discovery.filesRead")}>
                          {String(findings.data.analysis.files_read)}
                        </MetaRow>
                        <MetaRow label={t("detail.discovery.manifest")}>
                          {findings.data.analysis.manifest_found
                            ? t("detail.discovery.found")
                            : t("detail.discovery.absent")}
                        </MetaRow>
                        <MetaRow label={t("detail.discovery.analysed")}>
                          {fmt.relative(findings.data.analysis.analyzed_at)}
                        </MetaRow>
                      </dl>
                      {findings.data.analysis.truncated ? (
                        <div className="mt-2" data-testid="analysis-truncated">
                          <DataState
                            kind="partial"
                            title={t("detail.discovery.partial.title")}
                            description={t("detail.discovery.partial.description")}
                          />
                        </div>
                      ) : null}
                      <p className="mt-3 text-xs text-ink-muted">{t("detail.discovery.pathsOnly")}</p>
                    </div>
                  )}
                </Panel>

                <Panel>
                  <PanelHeader title={t("detail.session.title")} />
                  <dl className="divide-y divide-border">
                    <MetaRow label={t("detail.session.state")}>{data.state}</MetaRow>
                    <MetaRow label={t("detail.session.planVersion")}>
                      {data.plan ? `v${data.plan.plan_version}` : "—"}
                    </MetaRow>
                    <MetaRow label={t("detail.session.approved")}>
                      {data.approved_at
                        ? `v${data.approved_plan_version} · ${fmt.relative(data.approved_at)}`
                        : t("detail.session.notApproved")}
                    </MetaRow>
                    <MetaRow label={t("detail.session.imported")}>
                      {data.imported_project_key ? (
                        <Link
                          href={`/projects/${data.imported_project_id}`}
                          className="hover:underline"
                        >
                          {data.imported_project_key}
                        </Link>
                      ) : (
                        t("detail.session.notImported")
                      )}
                    </MetaRow>
                  </dl>
                  {data.reason ? (
                    <p className="mt-2 text-xs text-ink-secondary">{data.reason}</p>
                  ) : null}
                </Panel>
              </div>

              <Panel>
                <PanelHeader title={t("detail.plan.title")} />
                {plan.state === "loading" ? (
                  <DataState kind="loading" />
                ) : plan.state === "error" ? (
                  <DataState kind="error" description={plan.message} />
                ) : plan.data.plan === null ? (
                  <DataState
                    kind="empty"
                    title={t("detail.plan.empty.title")}
                    description={t("detail.plan.empty.description")}
                  />
                ) : (
                  <div className="space-y-5" data-testid="plan">
                    {/*
                      `min-w-0` and `break-all` on the digest: a 64-character
                      monospace token is one unbreakable word, so flex-wrap
                      cannot help it — it pushed the page 19px wider than the
                      viewport at 768px, which scrolls every row's right-hand
                      end (where the actions are) off screen.
                    */}
                    <div className="flex min-w-0 flex-wrap gap-4 rounded-2xl bg-surface-2 px-4 py-3 text-caption text-ink-secondary">
                      <span>
                        <RichMessage
                          template={t("detail.plan.version")}
                          parts={{
                            version: <span className="font-mono">v{plan.data.plan.plan_version}</span>,
                          }}
                        />
                      </span>
                      <span>
                        <RichMessage
                          template={t("detail.plan.commit")}
                          parts={{
                            sha: <span className="font-mono">{shortSha(plan.data.plan.commit_sha)}</span>,
                          }}
                        />
                      </span>
                      <span>
                        <RichMessage
                          template={t("detail.plan.digest")}
                          parts={{
                            digest: (
                              <span className="font-mono break-all">{plan.data.plan.plan_digest}</span>
                            ),
                          }}
                        />
                      </span>
                      <span>{t("detail.plan.items", { count: plan.data.plan.total_items })}</span>
                    </div>

                    {GROUPS.map((group) => {
                      const items = plan.data.items.filter((item) =>
                        group.actions.includes(item.action),
                      );
                      if (items.length === 0) return null;
                      return (
                        <div key={group.key} data-testid={`plan-group-${group.actions[0]}`}>
                          <p className="mb-2 text-caption font-medium text-ink">
                            {t(`detail.plan.group.${group.key}`)}
                          </p>
                          <ul className="space-y-1.5">
                            {items.map((item) => (
                              <li
                                key={item.item_key}
                                className="flex min-w-0 flex-wrap items-baseline gap-2 rounded-xl bg-surface-2 px-3 py-2 break-words"
                              >
                                <ActionBadge action={item.action} />
                                <span className="font-mono text-xs text-ink">
                                  {item.entity_kind}
                                </span>
                                <span className="text-xs text-ink-secondary">
                                  {item.proposed_name ?? item.existing_name ?? item.item_key}
                                </span>
                                {item.reason ? (
                                  <span className="text-[11px] text-ink-muted">
                                    {item.reason}
                                  </span>
                                ) : null}
                                {item.entity_kind === "deployment_source" &&
                                item.detail?.materialized === false ? (
                                  <span
                                    className="text-[11px] text-ink-muted"
                                    data-testid="deployment-source-note"
                                  >
                                    {t("detail.plan.evidenceOnly")}
                                  </span>
                                ) : null}
                                <Changes item={item} />
                              </li>
                            ))}
                          </ul>
                          {group.note ? (
                            <p className="mt-1.5 text-[11px] text-ink-muted">
                              {t(`detail.plan.note.${group.note}`)}
                            </p>
                          ) : null}
                        </div>
                      );
                    })}

                    <div className="border-t border-border pt-3">
                      {plan.data.plan.applicable && data.can_apply ? (
                        <p className="text-xs text-ink-secondary" data-testid="apply-available">
                          {t("detail.plan.applyAvailable")}
                        </p>
                      ) : (
                        <p className="text-xs text-warning" data-testid="apply-blocked">
                          {plan.data.plan.blocking_items > 0
                            ? t("detail.plan.applyBlocked", { count: plan.data.plan.blocking_items })
                            : data.can_apply === false
                              ? t("detail.plan.applyNoPermission")
                              : t("detail.plan.applyNotApplicable")}
                        </p>
                      )}
                    </div>
                  </div>
                )}
              </Panel>

              {data.gitops_requests && data.gitops_requests.length > 0 ? (
                <Panel>
                  <PanelHeader title={t("detail.gitopsRequests.title")} />
                  <ul className="space-y-2" data-testid="gitops-requests">
                    {data.gitops_requests.map((entry) => (
                      <li key={entry.id} className="flex min-w-0 flex-wrap items-baseline gap-2">
                        <GitOpsBadge state={entry.state} />
                        <span className="font-mono text-xs break-all text-ink-secondary">
                          {entry.branch_name} → {entry.file_path}
                        </span>
                        {entry.pull_request_url ? (
                          <a
                            href={entry.pull_request_url}
                            target="_blank"
                            // `noopener` so the opened tab cannot reach back
                            // through `window.opener`; `noreferrer` so Drake's
                            // URL — which contains a session id — is not sent
                            // to GitHub as a referrer.
                            rel="noopener noreferrer"
                            data-testid={`gitops-pr-link-${entry.id}`}
                            className="text-xs text-ink hover:underline"
                          >
                            {t("detail.gitopsRequests.openDraft", {
                              number: entry.provider_pr_number,
                            })}
                          </a>
                        ) : null}
                        {entry.error_code ? (
                          <span className="font-mono text-[11px] break-all text-critical">
                            {entry.error_code}
                          </span>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                  <p className="mt-3 text-xs text-ink-muted">
                    <RichMessage
                      template={t("detail.gitopsRequests.draftNote")}
                      parts={{
                        draft: <strong>{t("detail.gitopsRequests.draft")}</strong>,
                        // The placeholder token is what the file literally
                        // contains, so it is code, not copy.
                        token: <span className="font-mono">REPLACE_ME</span>,
                      }}
                    />
                  </p>
                  <p className="mt-1.5 text-xs text-ink-muted">{t("detail.gitopsRequests.mergeNote")}</p>
                </Panel>
              ) : null}
            </div>
          </>
        )}
      </LoadGate>
    </PageFrame>
  );
}
